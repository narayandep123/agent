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
