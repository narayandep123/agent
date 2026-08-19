import json
import time
from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.schemas.requests import ChatMessageInput, ConfirmationInput, DecisionResponse, MaintenanceUpdateInput, RequestInput, ReviewInput
from app.agents.interpreter import booking_entities, maintenance_entities, parse_floor
from app.agents.proposal import propose_plan
from app.agents.router import route_turn
from app.agents import translator
from app.auth.deps import get_current_user, require_admin
from app.db_models import User
from app.db import get_db
from app.models.domain import Decision, RequestStatus
from app.rag.retriever import search, get_policy, is_grounded, list_policies
from app.services import audit_service, request_service
from app.services import notification_service
from app.services import conversation_service
from app.services import knowledge_gap_service
from app.services.document_verification_service import verify as verify_document
from app.services import maintenance_attachment_service
import re

router = APIRouter(prefix="/api/v1")
# Per-user conversation memory so the agent remembers an in-progress request and
# continues it across turns (collecting details, then confirming) like a real agent.
# A conversation entry carries a ``flow`` ("booking", "maintenance" or
# "offer_complaint") plus flow-specific fields (booking uses ``stage``).
CONVERSATION: dict[str, dict] = {}
LAST_BOOKING_ENTITIES: dict[str, dict] = {}
LAST_MAINTENANCE_ENTITIES: dict[str, dict] = {}
# Remembers the last policy a user was reading about, so a vague follow-up like
# "details about policy?" or "tell me more" continues on the same document.
LAST_POLICY: dict[str, str] = {}

AFFIRM_WORDS = {"yes", "y", "yeah", "yep", "yup", "confirm", "confirmed", "ok", "okay", "sure", "proceed", "book it", "go ahead", "haan", "done", "theek hai", "thik hai", "perfect", "great", "yes please", "please do", "haan ji"}
CANCEL_WORDS = {"no", "nope", "cancel", "stop", "abort", "nah", "nahi", "never mind", "nevermind", "forget it", "leave it", "not now", "no thanks", "rehne do"}
QUESTION_STARTERS = {"how", "what", "when", "where", "who", "whom", "why", "which", "whose", "is", "are", "do", "does", "can", "could", "should", "am", "will", "may"}
ACTION_TERMS = ("book ", "reserve", "cancel", "not working", "broken", "leaking", "i need", "i want", "raise a", "log a", "create a", "report a", "fix ", "repair", "issue a", "apply for")
CAMPUS_TERMS = (
    "campus", "college", "student", "faculty", "hostel", "library", "lab", "classroom",
    "certificate", "bonafide", "transcript", "marksheet", "enrollment", "enrolment",
    "scholarship", "maintenance", "grievance", "complaint", "policy", "wifi", "warden",
    "academic", "semester", "exam", "attendance", "room", "seat", "facility", "canteen",
)
CERTIFICATE_CONTEXT_TERMS = (
    "certificate", "bonafide", "document", "student id", "marksheet", "enrollment",
    "enrolment", "upload", "scan", "purpose", "scholarship", "copy",
)

def _clean(text: str) -> str:
    return text.strip().lower().strip("!.?, ")

def _is_question(text: str) -> bool:
    cleaned = _clean(text)
    if not cleaned:
        return False
    return text.strip().endswith("?") or cleaned.split(" ", 1)[0] in QUESTION_STARTERS or cleaned.startswith(("how do", "how to", "how can", "whom to", "who to", "tell me about", "explain"))

def _is_action(text: str) -> bool:
    return any(term in text.lower() for term in ACTION_TERMS)

def _is_campus_related(text: str) -> bool:
    low = text.lower()
    return any(term in low for term in CAMPUS_TERMS)

def _mentions_certificate_context(text: str) -> bool:
    low = text.lower()
    return any(term in low for term in CERTIFICATE_CONTEXT_TERMS)

# Words that signal the user wants the full policy text, not just a summary.
_DETAIL_HINTS = ("detail", "full", "complete", "entire", "elaborate", "more", "procedure",
                 "criteria", "eligib", "clause", "rule", "document", "requirement", "policy",
                 "guideline", "provision", "process", "step")
# Generic filler that does not identify a topic; used to detect a vague follow-up
# ("details about policy?") so we can continue on the last policy read.
_INFO_FILLER = {"detail", "details", "policy", "policies", "about", "know", "tell", "give", "show",
                "explain", "more", "info", "information", "the", "for", "of", "on", "please", "can",
                "could", "want", "need", "full", "complete", "its", "this", "that", "regarding",
                "related", "provide", "kindly", "all", "what", "whats", "would", "like", "get",
                "there", "any", "are", "does", "your", "you", "and", "with", "here", "them"}


def _wants_details(text: str) -> bool:
    low = text.lower()
    return any(hint in low for hint in _DETAIL_HINTS)


def _specific_terms(text: str) -> list[str]:
    """Topic words left after removing filler — empty means a vague follow-up."""
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _INFO_FILLER]


def _policy_answers_exact_fact(question: str, policy: dict) -> bool:
    """Reject a topically relevant document that lacks the requested fact.

    Retrieval answers "which document is related?"; this check answers "does
    that document actually contain the requested value?". This prevents a
    generic hostel/curfew paragraph from being presented as a closing time.
    """
    low = question.lower().replace("-", " ")
    asks_for_time = (
        any(phrase in low for phrase in ("closing time", "opening time", "curfew time", "what time"))
        or bool(re.search(r"\bwhen\b.{0,30}\b(?:close|open|curfew)", low))
    )
    asks_for_schedule = any(term in low for term in ("schedule", "departure", "arrival time"))
    if not (asks_for_time or asks_for_schedule):
        return True
    corpus_text = " ".join(
        [policy.get("answer", "")] + [body for _, body in policy.get("sections", [])]
    ).lower()
    subject_stop = {
        "what", "when", "tell", "about", "campus", "policy", "policies", "closing",
        "opening", "close", "open", "time", "timing", "schedule", "departure", "arrival",
    }
    subjects = {word for word in re.findall(r"[a-z]+", low) if len(word) > 3 and word not in subject_stop}
    subject_matches = not subjects or any(subject in corpus_text for subject in subjects)
    explicit_time = re.search(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b|\b\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?)\b|\b(?:noon|midnight)\b", corpus_text)
    return bool(explicit_time) and subject_matches


def _info_answer(user_key: str, text_en: str) -> str | None:
    """Answer an informational/policy question from the corpus, carrying the topic
    across follow-ups and returning the full policy sections when details are asked."""
    followup = not _specific_terms(text_en)
    policy_id = LAST_POLICY.get(user_key) if followup else None
    if not policy_id:
        matches = search(text_en, k=1)
        top = matches[0] if matches else None
        if not (top and top.answer and is_grounded(top)):
            return None
        policy_id = top.policy_id
    LAST_POLICY[user_key] = policy_id
    policy = get_policy(policy_id)
    if not policy:
        return None
    if not _policy_answers_exact_fact(text_en, policy):
        return None
    if _wants_details(text_en):
        segments = [
            f"{title}: {body}"
            for title, body in policy["sections"]
            if not (title.lower() == "overview" and len(policy["sections"]) > 1)
        ]
        if segments:
            return f"{policy['name']} (v{policy['version']}) \u2014 " + " ".join(segments)
    return policy["answer"] or None

def _policy_source(user_key: str) -> list[dict]:
    policy = get_policy(LAST_POLICY.get(user_key, ""))
    if not policy:
        return []
    return [{"policy_id": policy["id"], "name": policy["name"], "version": policy["version"],
             "section": "Official policy", "citation": f"{policy['name']} v{policy['version']}"}]

def _multi_policy_answer(text_en: str) -> tuple[str, list[dict]] | None:
    """Answer an explicit list of policy topics independently.

    A combined similarity search returns only one winner. Looking up each named
    topic avoids dropping scholarship/hostel when maintenance ranks highest.
    """
    low = text_en.lower()
    topic_aliases = {
        "scholarship": "scholarship eligibility policy",
        "hostel": "hostel accommodation policy",
        "maintenance": "facilities maintenance policy",
        "library": "library hours and seat booking policy",
        "certificate": "academic certificate policy",
        "grievance": "grievance escalation policy",
        "wifi": "campus wifi policy",
    }
    requested = [query for term, query in topic_aliases.items() if term in low]
    if len(requested) < 2:
        return None
    answers, sources, seen = [], [], set()
    for query in requested:
        matches = search(query, k=1)
        match = matches[0] if matches else None
        if not (match and match.answer and is_grounded(match)) or match.policy_id in seen:
            continue
        seen.add(match.policy_id)
        answers.append(f"{match.name} (v{match.version}) — {match.answer}")
        sources.append({"policy_id": match.policy_id, "name": match.name, "version": match.version,
                        "section": match.section, "citation": match.snippet})
    return ("\n\n".join(answers), sources) if answers else None

def _message(text: str, lang: str, *, sources: list[dict] | None = None, **extra) -> dict:
    return {"type": "message", "message": translator.localize(text, lang), "language": lang,
            "sources": sources or [], **extra}

def _is_affirmative(text: str) -> bool:
    cleaned = _clean(text)
    return cleaned in AFFIRM_WORDS or cleaned.startswith(("yes", "confirm", "sure", "go ahead", "book it"))

def _is_cancel(text: str) -> bool:
    cleaned = _clean(text)
    return cleaned in CANCEL_WORDS or cleaned.startswith(("cancel", "stop", "abort", "never mind", "nevermind"))

_COMPLAINT_RE = re.compile(r"\b(file|filing|lodge|make|raise|register|submit|put)\b[a-z ]{0,10}\bcomplaint", re.I)
_COMPLAIN_RE = re.compile(r"\b(want|like|need|wish|how|can|could|would|do|to)\b[a-z ]{0,14}\bcomplain\b", re.I)

def _wants_to_complain(text: str) -> bool:
    """A meta request to lodge a complaint (e.g. 'can I file a complaint?')."""
    return bool(_COMPLAINT_RE.search(text) or _COMPLAIN_RE.search(text))

def _has_booking_detail(text: str) -> bool:
    parsed = booking_entities(text)
    if any(parsed.get(key) != "Not specified" for key in ("date", "time", "space")):
        return True
    if parsed.get("seat") != "Auto assign":
        return True
    return any(term in text.lower() for term in request_service.DEFER_TERMS)

def _has_maintenance_detail(text: str) -> bool:
    parsed = maintenance_entities(text)
    # A location alone is not a facility fault. This prevents a safety report
    # "near hostel A" from being hijacked by the maintenance workflow.
    return parsed.get("issue", "Not specified") != "Not specified"

def _merge_maintenance(previous: dict, new: dict) -> dict:
    merged = dict(previous)
    for key in ("issue", "location", "floor"):
        value = new.get(key, "Not specified")
        if not value or value == "Not specified":
            continue
        if key == "location":
            existing = previous.get("location", "Not specified")
            if existing not in ("", "Not specified") and value.lower() not in existing.lower():
                merged["location"] = f"{existing}, {value}"
            else:
                merged["location"] = value
        else:
            merged[key] = value
    for key in ("issue", "location", "floor"):
        merged.setdefault(key, "Not specified")
    return merged

def _join_missing(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]

def _handle_maintenance(user_key: str, text_en: str, role, lang: str,
                        requester_id: str | None = None, db: Session | None = None) -> dict:
    """Maintenance flow with cross-turn memory: collect the issue, location and
    floor over as many turns as needed, then log the ticket."""
    previous = LAST_MAINTENANCE_ENTITIES.get(user_key, {})
    merged = _merge_maintenance(previous, maintenance_entities(text_en))
    # If a PREVIOUS turn already captured the issue and location and we asked for the
    # floor, accept a bare reply like "5th", "second" or "5" as the floor answer.
    # (Gating on the prior state avoids mistaking a room number like "room 12" for a
    # floor on the very first message.)
    if (previous.get("issue", "Not specified") != "Not specified"
            and previous.get("location", "Not specified") != "Not specified"
            and previous.get("floor", "Not specified") == "Not specified"
            and merged.get("floor", "Not specified") == "Not specified"):
        loose_floor = parse_floor(text_en, loose=True)
        if loose_floor:
            merged["floor"] = loose_floor
    LAST_MAINTENANCE_ENTITIES[user_key] = merged

    missing = []
    if merged.get("issue", "Not specified") == "Not specified":
        missing.append("what's wrong (for example a broken fan, AC or light)")
    if merged.get("location", "Not specified") == "Not specified":
        missing.append("the building/block and room")
    if merged.get("floor", "Not specified") == "Not specified":
        missing.append("the floor")
    if missing:
        CONVERSATION[user_key] = {"flow": "maintenance"}
        message = f"Sure \u2014 I can log that maintenance request. Could you tell me {_join_missing(missing)}?"
        return {"type": "message", "message": translator.localize(message, lang), "language": lang}

    detail_text = f"{merged['issue']} at {merged['location']}, floor {merged['floor']}"
    request, policy, permitted, audit_id = request_service.create(detail_text, role, "MAINTENANCE", dict(merged), user_id=requester_id or user_key)
    if request.decision.value == "ACT":
        request.reason = (
            f"Done \u2014 I've logged a maintenance ticket for the {merged['issue'].lower()} at "
            f"{merged['location']}, floor {merged['floor']}. The facilities team will look into it. "
            "Is there anything else I can help you with?"
        )
    remaining_plan = CONVERSATION.get(user_key, {}).get("remaining_plan", [])
    CONVERSATION.pop(user_key, None)
    LAST_MAINTENANCE_ENTITIES.pop(user_key, None)
    response = serialize(request, policy, permitted, audit_id)
    response.message = translator.localize(response.message, lang)
    result = {"type": "decision", "decision": response.model_dump(), "language": lang}
    if remaining_plan:
        next_task = remaining_plan[0]
        if next_task.get("intent") == "POLICY_QUESTION":
            query = next_task.get("summary", "")
            answer = _info_answer(user_key, query)
            if answer:
                result["follow_up"] = {
                    "message": translator.localize(answer, lang),
                    "sources": _policy_source(user_key),
                }
            elif db is not None:
                gap = knowledge_gap_service.raise_gap(db, query, requester_id or user_key)
                result["follow_up"] = {
                    "message": translator.localize(
                        "For task 2, I don't have a verified hostel closing time and I don't want to guess. "
                        f"I've raised knowledge request {gap.id} for an administrator to add or update that policy.", lang),
                    "sources": [],
                    "knowledge_gap": {"id": gap.id, "status": gap.status},
                }
    return result

def _handle_grievance(user_key: str, text_en: str, role, lang: str, requester_id: str | None = None) -> dict:
    """Log a non-facility complaint as a grievance and escalate it to a human officer."""
    low = text_en.lower()
    urgent_terms = ("unsafe", "danger", "threat", "harass", "ragging", "abus", "assault", "violence", "suicide", "self harm", "discriminat", "teas", "molest", "catcall", "intimidat", "inappropriate touch")
    priority = "HIGH" if any(term in low for term in urgent_terms) else "NORMAL"
    request, policy, permitted, audit_id = request_service.create(
        text_en, role, "GRIEVANCE", {"summary": text_en.strip()[:200], "priority": priority},
        user_id=requester_id or user_key,
    )
    if priority == "HIGH":
        request.reason = "I'm sorry you're dealing with this. I've marked the grievance HIGH priority and escalated it for prompt human review. If anyone is in immediate danger, contact campus security or local emergency services now."
    CONVERSATION.pop(user_key, None)
    response = serialize(request, policy, permitted, audit_id)
    response.message = translator.localize(response.message, lang)
    return {"type": "decision", "decision": response.model_dump(), "language": lang}

def _certificate_message(user: User, certificate_type: str, lang: str) -> dict:
    if user.role != "STUDENT":
        message = (
            f"I understood this as a {certificate_type} request. The document-verification workflow is currently "
            f"available only to student accounts, but you're signed in as {user.role.title()}. "
            "Please contact the academic office for the appropriate faculty/staff certificate process."
        )
        return {"type": "message", "message": translator.localize(message, lang), "language": lang}
    message = (
        f"I can help you request a {certificate_type}. Before I route anything to the academic office, "
        "I need to verify one clear scan of your student ID or marksheet. I'll check legibility, document format, "
        "and whether the name and roll number match your enrollment record. Open Verify document to upload it; "
        "the request will be created only after that check."
    )
    return {"type": "message", "message": translator.localize(message, lang), "language": lang,
            "action": {"type": "OPEN_DOCUMENT_VERIFIER", "label": "Upload and verify document"}}


def _execute_compound_task(task: dict, user_key: str, user: User, lang: str, db: Session) -> list[dict]:
    """Execute one planned task through the same governed handlers as a single turn."""
    intent = task["intent"]
    text = task.get("summary", "").strip()
    if intent == "POLICY_QUESTION":
        answer = _info_answer(user_key, text)
        if answer:
            return [_message(answer, lang, sources=_policy_source(user_key))]
        gap = knowledge_gap_service.raise_gap(db, text, user.email)
        return [_message(
            "I may not have verified information about that yet, so I don't want to guess. "
            f"I've raised knowledge request {gap.id} for an administrator to add or update the relevant policy.",
            lang, knowledge_gap={"id": gap.id, "status": gap.status},
        )]
    if intent == "MAINTENANCE":
        result = _handle_maintenance(user_key, text, user.role, lang, user.email, db)
    elif intent == "GRIEVANCE":
        result = _handle_grievance(user_key, text, user.role, lang, user.email)
    elif intent == "CERTIFICATE":
        result = _certificate_message(user, task.get("entities", {}).get("certificate_type", "bonafide certificate"), lang)
    elif intent == "LAB_BOOKING":
        entities = booking_entities(text)
        request, policy, permitted, audit_id = request_service.create(
            text, user.role, "LAB_BOOKING", entities, user_id=user.email,
        )
        response = serialize(request, policy, permitted, audit_id)
        response.message = translator.localize(response.message, lang)
        if response.requires_confirmation:
            CONVERSATION[user_key] = {"stage": "confirming", "request_id": request.id, "text": text}
        elif request.status.value == "AWAITING_CONFIRMATION" or request.decision.value == "STOP":
            CONVERSATION[user_key] = {"stage": "collecting", "request_id": None, "text": text}
        result = {"type": "decision", "decision": response.model_dump(), "language": lang}
    else:
        result = _message(
            "That part is outside the campus services currently available to me. I completed any other supported tasks in your request.",
            lang,
        )
    outputs = [result]
    if result.get("follow_up"):
        outputs.append({"type": "message", **result["follow_up"], "language": lang})
        result.pop("follow_up", None)
    return outputs

def serialize(request, policy=None, permitted=True, audit_id=""):
    needs_details = any(text in request.reason.lower() for text in ("missing information", "no preference")) or (request.intent == "MAINTENANCE" and ("please tell me" in request.reason.lower() or "need the floor" in request.reason.lower()))
    trace = [
        {"step": "Interpret request", "result": request.intent},
        {"step": "Retrieve policy", "result": request.policy_name or "No sufficient official policy"},
        {"step": "Check permission", "result": "Allowed" if permitted else "Denied"},
        {"step": "Assess risk", "result": request.risk},
        {"step": "Route safely", "result": f"{request.decision.value} / {request.status.value}"},
    ]
    return DecisionResponse(request_id=request.id, intent=request.intent, entities=request.entities, decision=request.decision.value, status=request.status.value, policy={"id": request.policy_id, "name": request.policy_name, "confidence": policy.confidence if policy else .94, "source_section": policy.source_section if policy else "", "citation": policy.citation if policy else "", "uncertain": policy.uncertain if policy else False}, permission="Allowed" if permitted else "Denied", risk=request.risk, evidence="Verified" if request.policy_id else "Insufficient", message=request.reason, audit_id=audit_id, requires_confirmation=request.status.value == "AWAITING_CONFIRMATION" and request.decision.value == "ASK" and not needs_details, trace=trace)

@router.post("/requests", response_model=DecisionResponse)
def submit(payload: RequestInput, user: User = Depends(get_current_user)):
    request, policy, permitted, audit_id = request_service.create(payload.text, user.role, user_id=user.email)
    return serialize(request, policy, permitted, audit_id)

def _conversation_out(row) -> dict:
    return {"id": row.id, "title": row.title, "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()}

@router.post("/conversations")
def create_conversation(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _conversation_out(conversation_service.create(db, user.id))

@router.get("/conversations")
def list_conversations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [_conversation_out(row) for row in conversation_service.list_for(db, user.id)]

@router.get("/conversations/{conversation_id}/messages")
def conversation_messages(conversation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conversation = conversation_service.owned(db, conversation_id, user.id)
    if not conversation:
        raise HTTPException(404, "Conversation not found.")
    return [conversation_service.serialize_message(row) for row in conversation_service.messages(db, conversation.id)]

@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conversation = conversation_service.owned(db, conversation_id, user.id)
    if not conversation:
        raise HTTPException(404, "Conversation not found.")
    conversation_service.delete(db, conversation)
    memory_key = f"{user.email}:{conversation_id}"
    CONVERSATION.pop(memory_key, None)
    LAST_BOOKING_ENTITIES.pop(memory_key, None)
    LAST_MAINTENANCE_ENTITIES.pop(memory_key, None)
    LAST_POLICY.pop(memory_key, None)

@router.post("/conversations/{conversation_id}/messages")
def add_conversation_message(conversation_id: str, payload: ChatMessageInput,
                             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conversation = conversation_service.owned(db, conversation_id, user.id)
    if not conversation:
        raise HTTPException(404, "Conversation not found.")
    try:
        row = conversation_service.add_message(db, conversation, payload.role, payload.text, payload.payload)
    except ValueError as error:
        raise HTTPException(400, str(error))
    return conversation_service.serialize_message(row)

@router.post("/certificate/verify")
async def verify_certificate_document(
    certificate_type: str = Form(...),
    document_type: str = Form(...),
    document: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Verify a student document before entering the human approval queue."""
    if user.role != "STUDENT":
        raise HTTPException(403, "Only students can submit certificate documents.")
    try:
        content = await document.read()
        verification = verify_document(content, document.content_type or "", document.filename or "document", document_type, user.name, user.roll_no)
    except ValueError as error:
        raise HTTPException(400, str(error))

    entities = {
        "certificate_type": certificate_type.strip() or "Bonafide certificate",
        "document_verification": verification.as_dict(),
    }
    # Verified documents enter the normal APPROVE path. Uncertain extraction is
    # escalated for human verification; known mismatches stay with the student.
    if verification.status in {"VERIFIED", "MANUAL_REVIEW"}:
        request, policy, permitted, audit_id = request_service.create(
            f"Request {entities['certificate_type']} with supporting {document_type}",
            user.role, "CERTIFICATE", entities, user_id=user.email,
        )
        if verification.status == "MANUAL_REVIEW":
            request.decision = Decision.ESCALATE
            request.status = RequestStatus.ESCALATED
            request.reason = "Automated document extraction was unavailable. Routed to an administrator for manual document verification."
            audit_service.record(request.id, user.email, "DOCUMENT_VERIFICATION", "MANUAL_REVIEW", request.policy_name, request.risk)
        response = serialize(request, policy, permitted, audit_id).model_dump()
        return {"verification": verification.as_dict(), "routed": True, "decision": response}
    return {"verification": verification.as_dict(), "routed": False, "decision": None}

@router.post("/assistant")
def assistant(payload: RequestInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Conversation gateway with memory: an in-progress booking continues across turns."""
    user_key = f"{user.email}:{payload.conversation_id}" if payload.conversation_id else user.email
    if payload.conversation_id:
        thread = conversation_service.owned(db, payload.conversation_id, user.id)
        if not thread:
            raise HTTPException(404, "Conversation not found.")
        # Restore workflow context after a process restart from the durable turns.
        if user_key not in CONVERSATION:
            prior = conversation_service.messages(db, thread.id)
            for saved in reversed(prior[:-1]):  # frontend stores the current user turn first
                if saved.role == "assistant":
                    try:
                        saved_payload = json.loads(saved.payload) or {}
                        action = saved_payload.get("action", {})
                    except (json.JSONDecodeError, AttributeError):
                        saved_payload = {}
                        action = {}
                    if action.get("type") == "OPEN_DOCUMENT_VERIFIER":
                        CONVERSATION[user_key] = {"flow": "certificate", "certificate_type": "Bonafide certificate"}
                        LAST_POLICY[user_key] = "ACA-BONAFIDE-011"
                    saved_plan = saved_payload.get("plan", {})
                    plan_tasks = saved_plan.get("tasks", []) if isinstance(saved_plan, dict) else []
                    if plan_tasks:
                        first = next((task for task in plan_tasks if task.get("intent") != "UNSUPPORTED"), None)
                        if first and first.get("intent") == "MAINTENANCE":
                            seeded = maintenance_entities(first.get("summary", ""))
                            for key, value in first.get("entities", {}).items():
                                if (key in {"issue", "location", "floor"}
                                        and seeded.get(key, "Not specified") == "Not specified"
                                        and value not in ("", "Not specified")):
                                    seeded[key] = value
                            LAST_MAINTENANCE_ENTITIES[user_key] = seeded
                            remaining = [task for task in plan_tasks if task is not first]
                            CONVERSATION[user_key] = {"flow": "maintenance", "remaining_plan": remaining}
                    # Only the latest assistant turn represents current state. Do
                    # not revive an older workflow after the topic has changed.
                    break
    role = user.role
    payload.role = role  # the authenticated account's role is authoritative, not the client's
    lang = translator.resolve_language(payload.language, payload.text)
    text_en = translator.to_english(payload.text, lang)
    turn = route_turn(text_en)

    # Compound requests are decomposed, then each task is actually run through
    # its normal policy, permission, risk and confirmation gates. A plan alone is
    # not a completed agent turn.
    if re.search(r"\b(?:and also|also|then)\b|[;\n]+", text_en, re.I):
        tasks, urgency, planner = propose_plan(text_en)
        actionable = [task for task in tasks if task["intent"] != "UNSUPPORTED"]
        if len(tasks) > 1 and actionable:
            CONVERSATION.pop(user_key, None)
            outputs = []
            for task in tasks:
                outputs.extend(_execute_compound_task(task, user_key, user, lang, db))
            return {"type": "compound", "message": translator.localize(
                "I completed each supported task in order. Any task that still needs information or confirmation is shown below.", lang),
                "language": lang, "planner": planner, "urgency": urgency, "outputs": outputs}
    state = CONVERSATION.get(user_key, {})
    flow = state.get("flow")
    stage = state.get("stage")
    pending_id = state.get("request_id")
    has_detail = _has_booking_detail(text_en) if stage else False

    # Keep certificate intake conversational until a document has been checked.
    if flow == "certificate":
        if _is_cancel(text_en):
            CONVERSATION.pop(user_key, None)
            return {"type": "message", "message": translator.localize("Okay, I've cancelled the certificate intake. No request was submitted.", lang), "language": lang}
        # Re-evaluate every turn. An explicit unrelated request must release stale
        # certificate state before any certificate response can be generated.
        fresh_intent = turn.intent
        switching_intent = fresh_intent in {"MAINTENANCE", "LAB_BOOKING", "GRIEVANCE"}
        switching_unknown_topic = fresh_intent == "UNSUPPORTED" and not _mentions_certificate_context(text_en) and (
            _is_action(text_en) or bool(_specific_terms(text_en))
        )
        if switching_intent or switching_unknown_topic:
            CONVERSATION.pop(user_key, None)
            LAST_POLICY.pop(user_key, None)
            state, flow = {}, None

        # Questions are informational even while an action workflow is active.
        # Answer them from RAG first; only imperative replies remain in intake.
        if flow == "certificate" and (_is_question(text_en) or _wants_details(text_en)):
            matches = search(text_en, k=1)
            new_policy = matches[0] if matches else None
            if new_policy and is_grounded(new_policy):
                if new_policy.policy_id != "ACA-BONAFIDE-011":
                    CONVERSATION.pop(user_key, None)
                    state, flow = {}, None
                answer = _info_answer(user_key, text_en)
                if answer:
                    return _message(answer, lang, sources=_policy_source(user_key))
        if flow == "certificate":
            LAST_POLICY[user_key] = "ACA-BONAFIDE-011"
            return _certificate_message(user, state.get("certificate_type", "bonafide certificate"), lang)

    # 0a) We offered to raise a complaint on the previous turn. A described facility
    # issue becomes a maintenance ticket; a plain "yes" opens a neutral intake; a
    # "no" drops it.
    if flow == "offer_complaint":
        if _is_cancel(text_en) and not _has_maintenance_detail(text_en):
            CONVERSATION.pop(user_key, None)
            LAST_MAINTENANCE_ENTITIES.pop(user_key, None)
            return {"type": "message", "message": translator.localize("Okay, I won't file a complaint. Is there anything else I can help you with?", lang), "language": lang}
        if turn.intent == "GRIEVANCE":
            return _handle_grievance(user_key, text_en, payload.role, lang, user.email)
        if turn.intent == "MAINTENANCE":
            return _handle_maintenance(user_key, text_en, payload.role, lang, user.email, db)
        if _is_affirmative(text_en):
            CONVERSATION[user_key] = {"flow": "complaint_intake"}
            return {"type": "message", "message": translator.localize("Sure \u2014 what would you like to report? Please describe the issue and, if it's about a facility, tell me the block/room and floor.", lang), "language": lang}
        CONVERSATION.pop(user_key, None)  # unrelated reply: drop the offer, continue normally
        state, flow = {}, None

    # 0b) Neutral complaint intake: route a facility issue to maintenance, anything
    # else to a grievance escalation for a human officer.
    if flow == "complaint_intake":
        if _is_cancel(text_en):
            CONVERSATION.pop(user_key, None)
            return {"type": "message", "message": translator.localize("Okay, I won't file a complaint. Is there anything else I can help you with?", lang), "language": lang}
        if turn.intent == "MAINTENANCE":
            return _handle_maintenance(user_key, text_en, payload.role, lang, user.email, db)
        prior_summary = state.get("summary", "")
        combined_grievance = f"{prior_summary} {text_en}".strip()
        placeholder_words = {"harassment", "complaint", "grievance", "issue", "report", "against", "raise", "file"}
        current_words = set(re.findall(r"[a-z]+", text_en.lower()))
        is_placeholder = bool(current_words) and current_words.issubset(placeholder_words)
        if len(text_en.split()) >= 3 and not is_placeholder:
            return _handle_grievance(user_key, combined_grievance, payload.role, lang, user.email)
        CONVERSATION[user_key] = {"flow": "complaint_intake", "summary": combined_grievance}
        return _message(
            "I'm sorry you're dealing with this. Please describe what happened and where or when it occurred. "
            "Share only what you're comfortable sharing. If anyone is in immediate danger, contact campus security or local emergency services now.",
            lang,
        )

    # 0c) Continue an in-progress maintenance request across turns (merging the
    # location/floor/issue) until we have enough to log the ticket.
    if flow == "maintenance":
        if _is_cancel(text_en) and not _has_maintenance_detail(text_en):
            CONVERSATION.pop(user_key, None)
            LAST_MAINTENANCE_ENTITIES.pop(user_key, None)
            return {"type": "message", "message": translator.localize("No problem, I've cancelled that maintenance request. Is there anything else I can help you with?", lang), "language": lang}
        switch_intent = turn.intent
        if switch_intent == "GRIEVANCE":
            CONVERSATION.pop(user_key, None)
            LAST_MAINTENANCE_ENTITIES.pop(user_key, None)
            return _handle_grievance(user_key, text_en, payload.role, lang, user.email)
        if switch_intent == "POLICY_QUESTION":
            # Let the user inspect another planned task without discarding the
            # partially collected maintenance details. A later floor/location
            # reply will still resume the ticket.
            answer = _info_answer(user_key, text_en)
            if answer:
                return _message(answer, lang, sources=_policy_source(user_key))
            gap = knowledge_gap_service.raise_gap(db, text_en, user.email)
            return _message(
                "I don't have sufficiently verified information for that policy question, so I don't want to guess. "
                f"I've raised knowledge request {gap.id} for an administrator to add or update that policy. "
                "Your maintenance report is still open in this conversation; send its missing details whenever you're ready to continue.",
                lang, knowledge_gap={"id": gap.id, "status": gap.status},
            )
        if switch_intent == "LAB_BOOKING" and _has_booking_detail(text_en):
            CONVERSATION.pop(user_key, None)  # user switched to a booking instead
            LAST_MAINTENANCE_ENTITIES.pop(user_key, None)
            state, flow, stage = {}, None, None
        else:
            return _handle_maintenance(user_key, text_en, payload.role, lang, user.email, db)

    # 0d) A meta request to lodge a complaint ("can I file a complaint?") with no
    # concrete facility issue yet opens a neutral intake, so the agent gathers the
    # specifics before deciding maintenance vs. grievance escalation.
    if not stage and not flow and _wants_to_complain(text_en) and not _has_maintenance_detail(text_en):
        initial_intent = turn.intent
        CONVERSATION[user_key] = {"flow": "complaint_intake", "summary": text_en}
        if initial_intent == "GRIEVANCE":
            return _message(
                "I'm sorry you're dealing with this. I can record it as a confidential grievance for human review. "
                "Please describe what happened and where or when it occurred. Share only what you're comfortable sharing. "
                "If anyone is in immediate danger, contact campus security or local emergency services now.",
                lang,
            )
        return _message("Sure — I can help you file a complaint. What would you like to report? Please describe what happened and where.", lang)

    # 1) Cancel an in-progress booking on a plain "no"/"cancel" (but not "no, at 1pm").
    if stage in ("collecting", "confirming") and _is_cancel(text_en) and not has_detail:
        if stage == "confirming" and pending_id:
            try: request_service.confirm(pending_id, False)
            except (KeyError, ValueError): pass
        CONVERSATION.pop(user_key, None)
        LAST_BOOKING_ENTITIES.pop(user_key, None)
        return {"type": "message", "message": translator.localize("No problem, I've cancelled that request. Is there anything else I can help you with?", lang), "language": lang}

    # 2) While awaiting confirmation, a plain "yes"/"confirm" books it immediately.
    if stage == "confirming" and _is_affirmative(text_en) and not has_detail:
        try:
            request, audit_id = request_service.confirm(pending_id, True)
            CONVERSATION.pop(user_key, None)
            LAST_BOOKING_ENTITIES.pop(user_key, None)
            response = serialize(request, audit_id=audit_id)
            response.message = translator.localize(response.message, lang)
            return {"type": "decision", "decision": response.model_dump(), "language": lang}
        except (KeyError, ValueError):
            CONVERSATION.pop(user_key, None)

    # Safety reports and policy questions always interrupt booking collection;
    # they can never be interpreted as another booking detail.
    if stage in ("collecting", "confirming") and turn.intent == "GRIEVANCE":
        CONVERSATION.pop(user_key, None)
        LAST_BOOKING_ENTITIES.pop(user_key, None)
        return _handle_grievance(user_key, text_en, payload.role, lang, user.email)
    if stage in ("collecting", "confirming") and turn.intent == "POLICY_QUESTION":
        answer = _info_answer(user_key, text_en)
        if answer:
            return _message(answer, lang, sources=_policy_source(user_key))
        gap = knowledge_gap_service.raise_gap(db, text_en, user.email)
        return _message(
            "I don't have sufficiently verified information for that policy question, so I don't want to guess. "
            f"I've raised knowledge request {gap.id}. Your unfinished booking is still saved.",
            lang, knowledge_gap={"id": gap.id, "status": gap.status},
        )

    # 3) Determine intent, continuing the booking when we are mid-conversation.
    if stage in ("collecting", "confirming"):
        if has_detail:
            intent = "LAB_BOOKING"  # any booking detail continues the current booking
        else:
            intent = turn.intent
            if intent in ("MAINTENANCE", "CERTIFICATE", "GRIEVANCE"):
                LAST_BOOKING_ENTITIES.pop(user_key, None)  # user switched to a new service
                CONVERSATION.pop(user_key, None)
                state = {}
            else:
                intent = "LAB_BOOKING"
    else:
        intent = turn.intent

    # An informational question about a service ("how do I get a certificate?",
    # "what documents are needed?", "details about policy?") is answered from the
    # policy corpus. It remembers the topic so a vague follow-up continues on the
    # same policy, and returns the full sections when the user asks for details.
    if not stage and not _is_action(text_en) and (_is_question(text_en) or _wants_details(text_en)):
        multi_policy = _multi_policy_answer(text_en)
        if multi_policy:
            answer, sources = multi_policy
            return _message(answer, lang, sources=sources)
        answer = _info_answer(user_key, text_en)
        if answer:
            # The grievance policy offers to raise a complaint; remember that so a
            # following "yes" starts a maintenance ticket instead of being lost.
            if LAST_POLICY.get(user_key) == "GRV-ESCAL-004":
                CONVERSATION[user_key] = {"flow": "offer_complaint"}
            return _message(answer, lang, sources=_policy_source(user_key))

    # A recognised policy question with no grounded answer is a knowledge gap,
    # never an executable service request and never a generic policy summary.
    if intent == "POLICY_QUESTION":
        gap = knowledge_gap_service.raise_gap(db, text_en, user.email)
        message = (
            "I may not have verified information about that yet, so I don't want to guess. "
            f"I've raised knowledge request {gap.id} for an administrator to add or update the relevant policy."
        )
        return _message(message, lang, knowledge_gap={"id": gap.id, "status": gap.status})

    greeting = _clean(payload.text) in {"hi", "hello", "hey", "hii", "help", "namaste"} or _clean(text_en) in {"hi", "hello", "hey", "help"}
    if intent == "UNSUPPORTED" and greeting:
        return {"type": "message", "message": translator.localize(translator.ASSISTANT_GREETING, lang), "language": lang}
    if intent == "UNSUPPORTED":
        # Informational fallback: answer FAQ questions in plain language from the
        # policy corpus. New topics only need a document added to app/rag/documents.
        matches = search(text_en, k=1)
        top = matches[0] if matches else None
        if top and top.answer and is_grounded(top):
            # The grievance answer offers to raise a complaint; remember that so a
            # following "yes" starts a maintenance ticket instead of being lost.
            LAST_POLICY[user_key] = top.policy_id
            if top.policy_id == "GRV-ESCAL-004":
                CONVERSATION[user_key] = {"flow": "offer_complaint"}
            return _message(top.answer, lang, sources=_policy_source(user_key))
        if _is_question(text_en) and _is_campus_related(text_en):
            gap = knowledge_gap_service.raise_gap(db, text_en, user.email)
            message = (
                "I may not have verified information about that yet, so I don't want to guess. "
                f"I've raised knowledge request {gap.id} for an administrator to add or update the relevant policy."
            )
            return {"type": "message", "message": translator.localize(message, lang), "language": lang,
                    "knowledge_gap": {"id": gap.id, "status": gap.status}}
        if not _is_campus_related(text_en):
            message = (
                "I’m sorry, but I’m not able to help with that request because it falls outside the campus services "
                "currently available to me. I can help with maintenance issues, campus room or library bookings, "
                "certificate requests, complaints, and verified institutional policies. If your request is related "
                "to campus, please share a little more context and I’ll do my best to guide you."
            )
            return {"type": "message", "message": translator.localize(message, lang), "language": lang}
        return {"type": "message", "message": translator.localize(translator.ASSISTANT_FALLBACK, lang), "language": lang}

    # A maintenance complaint ("the fan in hostel 102 isn't working") is logged as a
    # ticket, collecting the location/floor over multiple turns when needed.
    if intent == "MAINTENANCE":
        return _handle_maintenance(user_key, text_en, payload.role, lang, user.email, db)

    # A non-facility complaint is logged as a grievance and escalated to a human.
    if intent == "GRIEVANCE":
        return _handle_grievance(user_key, text_en, payload.role, lang, user.email)

    if intent == "CERTIFICATE":
        low = text_en.lower()
        certificate_type = next((label for term, label in (
            ("transcript", "Transcript / marksheet copy"),
            ("character", "Character certificate"),
            ("enrolment", "Enrollment certificate"),
            ("enrollment", "Enrollment certificate"),
        ) if term in low), "Bonafide certificate")
        LAST_POLICY[user_key] = "ACA-BONAFIDE-011"
        CONVERSATION[user_key] = {"flow": "certificate", "certificate_type": certificate_type}
        return _certificate_message(user, certificate_type, lang)

    combined_text = text_en
    booking_context = None
    if intent == "LAB_BOOKING":
        current = booking_entities(text_en)
        booking_context = dict(LAST_BOOKING_ENTITIES.get(user_key, {}))
        for key, value in current.items():
            if value != "Not specified" and not (key == "seat" and value == "Auto assign"):
                booking_context[key] = value
        booking_context.setdefault("space", "Not specified")
        booking_context.setdefault("date", "Not specified")
        booking_context.setdefault("time", "Not specified")
        booking_context.setdefault("seat", "Auto assign")
        LAST_BOOKING_ENTITIES[user_key] = booking_context
        combined_text = f"{state.get('text', '')} {text_en}".strip()

    request, policy, permitted, audit_id = request_service.create(combined_text, payload.role, intent, booking_context)
    response = serialize(request, policy, permitted, audit_id)
    response.message = translator.localize(response.message, lang)

    if intent == "LAB_BOOKING" and response.requires_confirmation:
        CONVERSATION[user_key] = {"stage": "confirming", "request_id": request.id, "text": combined_text}
    elif intent == "LAB_BOOKING" and (request.status.value == "AWAITING_CONFIRMATION" or request.decision.value == "STOP"):
        CONVERSATION[user_key] = {"stage": "collecting", "request_id": None, "text": combined_text}
    else:
        CONVERSATION.pop(user_key, None)
        LAST_BOOKING_ENTITIES.pop(user_key, None)
    return {"type": "decision", "decision": response.model_dump(), "language": lang}


@router.post("/assistant/stream")
def assistant_stream(payload: RequestInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """NDJSON progress stream ending with the normal, fully validated response.

    Only safe status text is streamed. Model output remains hidden until policy,
    permission, risk and schema validation have completed.
    """
    def events():
        yield json.dumps({"type": "status", "message": "Understanding your request…"}) + "\n"
        started = time.monotonic()
        result = assistant(payload, user, db)
        if time.monotonic() - started > 0.6:
            yield json.dumps({"type": "status", "message": "Checking policy and safety rules…"}) + "\n"
        yield json.dumps({"type": "result", "data": result}) + "\n"

    return StreamingResponse(
        events(), media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@router.post("/requests/{request_id}/confirm", response_model=DecisionResponse)
def confirm(request_id: str, payload: ConfirmationInput, user: User = Depends(get_current_user)):
    try:
        request, audit_id = request_service.confirm(request_id, payload.confirmed)
        CONVERSATION.pop(user.email, None)
        LAST_BOOKING_ENTITIES.pop(user.email, None)
        LAST_MAINTENANCE_ENTITIES.pop(user.email, None)
        return serialize(request, audit_id=audit_id)
    except KeyError: raise HTTPException(404, "Request not found")
    except ValueError as error: raise HTTPException(409, str(error))

@router.post("/approvals/{request_id}", response_model=DecisionResponse)
def review(request_id: str, payload: ReviewInput, admin: User = Depends(require_admin)):
    """Human-in-the-loop: an administrator approves or rejects a pending request."""
    reviewer = payload.reviewer if payload.reviewer and payload.reviewer != "approver" else admin.name
    try:
        request, audit_id = request_service.review(request_id, payload.approved, reviewer, payload.comment)
        return serialize(request, audit_id=audit_id)
    except KeyError: raise HTTPException(404, "Request not found")
    except ValueError as error: raise HTTPException(409, str(error))

@router.post("/maintenance/{request_id}/status", response_model=DecisionResponse)
def update_maintenance_status(request_id: str, payload: MaintenanceUpdateInput,
                              admin: User = Depends(require_admin)):
    try:
        request, audit_id = request_service.update_maintenance(
            request_id, payload.status, payload.assigned_to, payload.comment, admin.email,
        )
        return serialize(request, audit_id=audit_id)
    except KeyError:
        raise HTTPException(404, "Maintenance ticket not found")
    except ValueError as error:
        raise HTTPException(409, str(error))

@router.post("/maintenance/{request_id}/attachments")
async def add_maintenance_attachment(request_id: str, image: UploadFile = File(...),
                                     user: User = Depends(get_current_user)):
    request = request_service.REQUESTS.get(request_id)
    if not request:
        raise HTTPException(404, "Maintenance ticket not found")
    if user.role not in ("ADMIN", "APPROVER") and request.user_id != user.email:
        raise HTTPException(403, "You cannot add evidence to another user's ticket.")
    try:
        metadata = maintenance_attachment_service.save(
            request, await image.read(), image.content_type or "", image.filename or "maintenance-photo",
        )
    except ValueError as error:
        raise HTTPException(400, str(error))
    audit_service.record(request.id, user.email, "ATTACH_EVIDENCE", "UPLOADED", request.policy_name, request.risk)
    return {"attachment": metadata, "attachments": request.entities.get("attachments", [])}

@router.get("/maintenance/{request_id}/attachments/{attachment_id}")
def get_maintenance_attachment(request_id: str, attachment_id: str,
                               user: User = Depends(get_current_user)):
    request = request_service.REQUESTS.get(request_id)
    if not request:
        raise HTTPException(404, "Maintenance ticket not found")
    if user.role not in ("ADMIN", "APPROVER") and request.user_id != user.email:
        raise HTTPException(403, "You cannot view evidence for another user's ticket.")
    metadata = next((item for item in request.entities.get("attachments", []) if item["id"] == attachment_id), None)
    path = maintenance_attachment_service.locate(request_id, attachment_id)
    if not metadata or not path:
        raise HTTPException(404, "Attachment not found")
    return FileResponse(path, media_type=metadata["content_type"], filename=metadata["filename"])

@router.get("/requests")
def requests(user: User = Depends(get_current_user)):
    rows = request_service.list_requests()
    if user.role not in ("ADMIN", "APPROVER"):
        # Repair tickets created by the earlier conversation-scoped ownership bug
        # (email:conversation-id) so they immediately reappear for their owner.
        owned_rows = []
        for row in rows:
            if row.user_id == user.email or row.user_id.startswith(f"{user.email}:"):
                row.user_id = user.email
                owned_rows.append(row)
        rows = owned_rows
    return [{"id": r.id, "type": r.intent.replace("_", " ").title(), "status": r.status.value,
             "decision": r.decision.value, "created_at": r.created_at.isoformat(),
             "entities": r.entities, "message": r.reason} for r in rows]

@router.get("/audit")
def audit(admin: User = Depends(require_admin)): return audit_service.all_events()

@router.get("/audit/verify")
def audit_verify(admin: User = Depends(require_admin)):
    """Confirm the audit hash-chain is intact (tamper-evident action trail)."""
    return audit_service.verify()

@router.get("/notifications")
def notifications(user: User = Depends(get_current_user)):
    """The signed-in user's notification history (also delivered by email)."""
    return notification_service.inbox_for(user.email)

@router.post("/notifications/read")
def mark_notifications_read(user: User = Depends(get_current_user)):
    """Mark current notifications as seen while retaining the inbox history."""
    return notification_service.mark_all_read(user.email)

@router.get("/policies")
def policies(): return list_policies()

def _gap_out(gap) -> dict:
    return {"id": gap.id, "question": gap.question, "requested_by": gap.requested_by,
            "status": gap.status, "occurrences": gap.occurrences, "policy_id": gap.policy_id,
            "created_at": gap.created_at.isoformat(), "updated_at": gap.updated_at.isoformat()}

@router.get("/admin/knowledge-gaps")
def knowledge_gaps(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_gap_out(gap) for gap in knowledge_gap_service.list_gaps(db)]

@router.post("/admin/knowledge-gaps/{gap_id}/policy")
async def publish_gap_policy(gap_id: str, title: str = Form(...), version: str = Form("1.0"),
                             policy_file: UploadFile = File(...), admin: User = Depends(require_admin),
                             db: Session = Depends(get_db)):
    from app.db_models import KnowledgeGap
    gap = db.get(KnowledgeGap, gap_id)
    if not gap:
        raise HTTPException(404, "Knowledge request not found.")
    if policy_file.content_type not in {"text/plain", "text/markdown", "application/octet-stream"}:
        raise HTTPException(400, "Upload a plain-text or Markdown policy file.")
    raw = await policy_file.read()
    if not raw or len(raw) > 1024 * 1024:
        raise HTTPException(400, "Policy file must contain text and be 1 MB or smaller.")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "Policy file must be UTF-8 text.")
    if len(content.split()) < 5:
        raise HTTPException(400, "Policy content is too short to publish.")
    try:
        policy_id = knowledge_gap_service.resolve(db, gap, title, version, content, admin.email)
    except ValueError as error:
        raise HTTPException(409, str(error))
    return {"gap": _gap_out(gap), "policy_id": policy_id, "searchable": True}

@router.get("/policies/search")
def policy_search(q: str, k: int = 3):
    """Retrieval-grounded policy Q&A: returns cited policy sections for a query."""
    matches = search(q, k=max(1, min(k, 5)))
    grounded = [m for m in matches if m.score > 0]
    if not grounded:
        return {"query": q, "grounded": False, "message": "No sufficiently relevant policy was found. This should be escalated to a human.", "results": []}
    return {"query": q, "grounded": True, "results": [{"policy_id": m.policy_id, "name": m.name, "version": m.version, "source_section": m.section, "confidence": m.confidence, "citation": m.snippet} for m in grounded]}
