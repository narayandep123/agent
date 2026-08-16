from fastapi import APIRouter, HTTPException
from app.schemas.requests import ConfirmationInput, DecisionResponse, RequestInput
from app.agents.interpreter import booking_entities, interpret
from app.services import audit_service, request_service

router = APIRouter(prefix="/api/v1")
PENDING_ASSISTANT_TEXT: dict[str, str] = {}
LAST_BOOKING_ENTITIES: dict[str, dict] = {}

def serialize(request, policy=None, permitted=True, audit_id=""):
    needs_details = any(text in request.reason.lower() for text in ("missing information", "which library would you like")) or (request.intent == "MAINTENANCE" and ("please tell me" in request.reason.lower() or "need the floor" in request.reason.lower()))
    return DecisionResponse(request_id=request.id, intent=request.intent, entities=request.entities, decision=request.decision.value, status=request.status.value, policy={"id": request.policy_id, "name": request.policy_name, "confidence": policy.confidence if policy else .94}, permission="Allowed" if permitted else "Denied", risk=request.risk, evidence="Verified" if request.policy_id else "Insufficient", message=request.reason, audit_id=audit_id, requires_confirmation=request.status.value == "AWAITING_CONFIRMATION" and request.decision.value == "ASK" and not needs_details)

@router.post("/requests", response_model=DecisionResponse)
def submit(payload: RequestInput):
    request, policy, permitted, audit_id = request_service.create(payload.text, payload.role)
    return serialize(request, policy, permitted, audit_id)

@router.post("/assistant")
def assistant(payload: RequestInput):
    """Conversation gateway: only service intents become governed requests."""
    user_key = "demo-student"
    combined_text = f"{PENDING_ASSISTANT_TEXT.get(user_key, '')} {payload.text}".strip()
    intent, parsed_entities, _ = interpret(payload.text)
    if intent == "UNSUPPORTED" and user_key in LAST_BOOKING_ENTITIES and any(word in payload.text.lower() for word in ("tomorrow", "tommorrow", "tomorow", "kal", "same time", "library")):
        intent = "LAB_BOOKING"
    booking_context = None
    if intent == "LAB_BOOKING":
        current = booking_entities(payload.text)
        booking_context = dict(LAST_BOOKING_ENTITIES.get(user_key, {}))
        for key, value in current.items():
            if value != "Not specified" and not (key == "seat" and value == "Auto assign"):
                booking_context[key] = value
        booking_context.setdefault("space", "Not specified")
        booking_context.setdefault("date", "Not specified")
        booking_context.setdefault("time", "Not specified")
        booking_context.setdefault("seat", "Auto assign")
        LAST_BOOKING_ENTITIES[user_key] = booking_context
    greeting = payload.text.strip().lower() in {"hi", "hello", "hey", "hii", "help"}
    if intent == "UNSUPPORTED" and greeting:
        return {"type": "message", "message": "Hello! I can help you report maintenance issues, book a lab or room, request a certificate, or explain an official policy."}
    if intent == "UNSUPPORTED":
        return {"type": "message", "message": "I’m here for campus services. Try describing a maintenance issue, lab/room booking, certificate request, or ask about an official policy."}
    request, policy, permitted, audit_id = request_service.create(payload.text, payload.role, intent, booking_context)
    response = serialize(request, policy, permitted, audit_id)
    if request.status.value == "AWAITING_CONFIRMATION" and not response.requires_confirmation:
        PENDING_ASSISTANT_TEXT[user_key] = combined_text
    else:
        PENDING_ASSISTANT_TEXT.pop(user_key, None)
    return {"type": "decision", "decision": response.model_dump()}

@router.post("/requests/{request_id}/confirm", response_model=DecisionResponse)
def confirm(request_id: str, payload: ConfirmationInput):
    try:
        request, audit_id = request_service.confirm(request_id, payload.confirmed)
        return serialize(request, audit_id=audit_id)
    except KeyError: raise HTTPException(404, "Request not found")
    except ValueError as error: raise HTTPException(409, str(error))

@router.get("/requests")
def requests():
    return [{"id": r.id, "type": r.intent.replace("_", " ").title(), "status": r.status.value, "decision": r.decision.value, "created_at": r.created_at.isoformat()} for r in request_service.list_requests()]

@router.get("/audit")
def audit(): return audit_service.all_events()

@router.get("/policies")
def policies():
    return [{"id": "FAC-MNT-001", "name": "Facilities Maintenance Policy", "version": "1.4", "effective_date": "2026-01-01"}, {"id": "LIB-BOOK-002", "name": "Library Seat Booking Policy", "version": "2.1", "effective_date": "2026-01-01"}, {"id": "ACA-CERT-003", "name": "Academic Certificate Policy", "version": "3.0", "effective_date": "2026-01-01"}]
