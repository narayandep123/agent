"""Proposal orchestration: LLM-first with a deterministic guarantee.

``propose`` returns a proposed ``(intent, entities, language, source)``. It tries
the optional Gemini adapter and always falls back to the deterministic
interpreter. Booking entities are re-derived deterministically because the
downstream Sunday/past-time checks depend on exact ISO date and HH:MM formats
that only the regex extractor guarantees.
"""
from __future__ import annotations

from app.agents import gemini_adapter
from app.agents.interpreter import booking_entities, interpret, maintenance_entities

_DEFAULT_ENTITIES = {
    "MAINTENANCE": {"location": "Not specified", "floor": "Not specified", "issue": "Facility maintenance"},
    "CERTIFICATE": {"certificate_type": "Certificate"},
}


def _normalize(intent: str, entities: dict) -> dict:
    base = dict(_DEFAULT_ENTITIES.get(intent, {}))
    for key, value in entities.items():
        if value:
            base[key] = value
    return base


def propose(text: str) -> tuple[str, dict, str, str]:
    det_intent, det_entities, det_language = interpret(text)
    # Clear, deterministic intents do not need a network round trip. Gemini is
    # reserved for ambiguous text and explicit multi-task planning.
    if det_intent != "UNSUPPORTED":
        return det_intent, det_entities, det_language, "deterministic"
    lower = text.lower()
    clearly_external = any(term in lower for term in (
        "flight", "airline", "book a cab", "book cab", "taxi", "make a video",
        "create a video", "movie", "hotel booking",
    ))
    if clearly_external:
        return det_intent, det_entities, det_language, "deterministic"
    llm = gemini_adapter.propose(text)
    if not llm:
        return det_intent, det_entities, det_language, "deterministic"

    intent = llm["intent"]
    language = llm.get("language") or det_language

    if intent == "UNSUPPORTED":
        return "UNSUPPORTED", {}, language, "gemini"
    if intent == "LAB_BOOKING":
        lower = text.lower()
        campus_resource = any(term in lower for term in (
            "library", "lab", "study room", "reading room", "computer room", "classroom", "seat"
        )) or ("room" in lower and any(term in lower for term in ("book", "booking", "reserve")))
        if not campus_resource:
            return "UNSUPPORTED", {}, language, "guardrail"
        # Deterministic parsing keeps date/time formats the pipeline relies on.
        return intent, booking_entities(text), language, "gemini"
    if intent == det_intent:
        return intent, det_entities, language, "gemini"
    # Gemini may classify an ambiguous request, but it cannot supply facts. All
    # action entities are derived from the user's text or explicitly marked as
    # missing so the dialogue layer can ask one targeted question.
    if intent == "MAINTENANCE":
        return intent, maintenance_entities(text), language, "gemini"
    if intent == "GRIEVANCE":
        return intent, {"summary": text.strip()[:200]}, language, "gemini"
    if intent == "CERTIFICATE":
        certificate_type = "Bonafide certificate" if "bonaf" in text.lower() else "Certificate"
        return intent, {"certificate_type": certificate_type}, language, "gemini"
    if intent == "POLICY_QUESTION":
        return intent, {"policy_topic": text.strip()[:200]}, language, "gemini"
    return "UNSUPPORTED", {}, language, "guardrail"


def propose_plan(text: str, current_plan: list[dict] | None = None,
                 frustrated: bool = False) -> tuple[list[dict], str, str]:
    """Build or update a task DAG by meaning, never by connector tokens."""
    llm = gemini_adapter.plan(text, current_plan)
    if llm:
        return llm["tasks"], llm["urgency"], "gemini"

    # A terse follow-up updates the matching unresolved task in place.
    if current_plan:
        updated = [{**task, "known_params": dict(task.get("known_params", {}))} for task in current_plan]
        unresolved = [task for task in updated if task.get("status", "PENDING") not in {
            "COMPLETED", "FAILED", "ROLLED_BACK",
        }]
        supplied = _supplied_for_waiting(text, unresolved)
        if supplied:
            task, values = supplied
            task["known_params"].update(values)
            task["entities"] = dict(task["known_params"])
            task["missing_params"] = _missing_params(task["intent"], task["known_params"])
            task["summary"] = f"{task.get('summary', '')} {text}".strip()[:240]
            task["status"] = "READY" if not task["missing_params"] else "PENDING"
            return updated, "NORMAL", "deterministic-update"
        if frustrated and unresolved:
            for task in unresolved:
                task["defaults_applied"] = _safe_defaults(task["intent"], task.get("missing_params", []))
                task["requires_human_confirmation"] = True
                task["missing_params"] = []
                task["status"] = "READY"
            return updated, "NORMAL", "deterministic-update"

    # Punctuation only creates candidate linguistic spans. A span becomes a task
    # only after it independently classifies to a supported service meaning.
    import re
    candidates = [part.strip(" ,.") for part in re.split(r"(?<=[.!?])\s+|[\n;]+", text) if part.strip(" ,.")]
    if len(candidates) == 1:
        candidates = _category_segments(text)
    classified = []
    for candidate in candidates:
        intent, entities, _, _ = propose(candidate)
        if intent != "UNSUPPORTED" or len(candidates) == 1:
            classified.append((candidate, intent, entities))
    if not classified:
        intent, entities, _, _ = propose(text)
        classified = [(text.strip(), intent, entities)]

    tasks = [dict(task) for task in (current_plan or [])]
    starting_index = len(tasks) + 1
    for index, (part, intent, entities) in enumerate(classified[:max(0, 8 - len(tasks))], start=starting_index):
        tasks.append({
            "task_id": f"t{index}", "intent": intent, "summary": part[:240],
            "entities": entities, "known_params": _known_params(entities),
            "missing_params": _missing_params(intent, entities), "depends_on": [],
            "parallel_safe": True, "status": "PENDING", "requires_human_confirmation": False,
        })
    return tasks, "NORMAL", "deterministic"


def _category_segments(text: str) -> list[str]:
    """Extract different service meanings stated back-to-back in one sentence."""
    import re
    candidates = [part.strip(" ,.") for part in re.split(
        r"(?i)(?=\b(?:tell|explain|report|raise|file|book|reserve|request|show|need|want)\b)", text,
    ) if part.strip(" ,.")]
    classified: list[tuple[str, str]] = []
    for part in candidates:
        intent, _, _, _ = propose(part)
        if intent != "UNSUPPORTED":
            classified.append((intent, part))
        elif classified:
            intent, prior = classified[-1]
            classified[-1] = (intent, f"{prior} {part}"[:240])
    result: list[str] = []
    previous_intent = None
    for intent, part in classified:
        if intent == previous_intent and result:
            result[-1] = f"{result[-1]} {part}"[:240]
        else:
            result.append(part)
            previous_intent = intent
    return result or [text.strip()]


def _supplied_for_waiting(text: str, unresolved: list[dict]) -> tuple[dict, dict] | None:
    for task in unresolved:
        if task.get("intent") == "MAINTENANCE":
            values = _known_params(maintenance_entities(text))
        elif task.get("intent") == "LAB_BOOKING":
            values = _known_params(booking_entities(text))
            if values.get("seat") == "Auto assign":
                values.pop("seat", None)
        else:
            values = {}
        relevant = {key: value for key, value in values.items()
                    if key in task.get("missing_params", []) or key in task.get("known_params", {})}
        if relevant:
            return task, relevant
    return None


def _safe_defaults(intent: str, missing: list[str]) -> dict:
    defaults = {}
    for field in missing:
        if intent == "MAINTENANCE" and field in {"location", "floor"}:
            defaults[field] = "To be confirmed by facilities"
        elif intent == "MAINTENANCE" and field == "issue":
            defaults[field] = "General facility issue"
        elif intent == "LAB_BOOKING" and field == "space":
            defaults[field] = "Auto-assign available campus resource"
        elif intent == "LAB_BOOKING" and field in {"date", "time"}:
            defaults[field] = "Next policy-compliant available slot"
        else:
            defaults[field] = "To be confirmed by an authorized human reviewer"
    return defaults


def _known_params(entities: dict) -> dict:
    return {key: value for key, value in entities.items() if value not in (None, "", "Not specified")}


def _missing_params(intent: str, entities: dict) -> list[str]:
    required = {
        "MAINTENANCE": ("issue", "location", "floor"),
        "LAB_BOOKING": ("space", "date", "time"),
        "CERTIFICATE": ("certificate_type",),
        "GRIEVANCE": ("summary",),
        "POLICY_QUESTION": ("policy_topic",),
    }.get(intent, ())
    return [key for key in required if entities.get(key) in (None, "", "Not specified")]
