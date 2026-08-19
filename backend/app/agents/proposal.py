"""Proposal orchestration: LLM-first with a deterministic guarantee.

``propose`` returns a proposed ``(intent, entities, language, source)``. It tries
the optional Gemini adapter and always falls back to the deterministic
interpreter. Booking entities are re-derived deterministically because the
downstream Sunday/past-time checks depend on exact ISO date and HH:MM formats
that only the regex extractor guarantees.
"""
from __future__ import annotations

from app.agents import gemini_adapter
from app.agents.interpreter import booking_entities, interpret

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
    return intent, _normalize(intent, llm.get("entities", {})), language, "gemini"


def propose_plan(text: str) -> tuple[list[dict], str, str]:
    """Plan multiple tasks without authorising or executing any of them."""
    llm = gemini_adapter.plan(text)
    if llm:
        return llm["tasks"], llm["urgency"], "gemini"

    # Conservative offline fallback: only explicit separators create tasks.
    import re
    parts = [part.strip(" ,.") for part in re.split(
        r"\b(?:and also|also|then)\b|[;\n]+", text, flags=re.I
    ) if part.strip(" ,.")]
    if len(parts) < 2:
        intent, entities, _, _ = propose(text)
        return [{"intent": intent, "summary": text.strip()[:240], "entities": entities}], "NORMAL", "deterministic"
    tasks = []
    for part in parts[:6]:
        intent, entities, _, _ = propose(part)
        tasks.append({"intent": intent, "summary": part[:240], "entities": entities})
    return tasks, "NORMAL", "deterministic"
