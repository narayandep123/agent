"""Authoritative per-turn routing with explicit, testable precedence.

The router classifies a message once. Conversation workflows consume this result
instead of independently guessing intent from isolated entities. Gemini remains
an ambiguity helper and cannot override safety-critical deterministic matches.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agents.interpreter import interpret
from app.agents.proposal import propose


@dataclass(frozen=True)
class TurnRoute:
    intent: str
    entities: dict = field(default_factory=dict)
    language: str = "en"
    source: str = "deterministic"
    safety_critical: bool = False


_SAFETY_TERMS = (
    "harass", "abus", "ragging", "bully", "stalk", "assault", "violence",
    "threat", "unsafe", "discriminat", "retaliat", "coerc", "humiliat",
    "teas", "eve teas", "molest", "catcall", "intimidat", "inappropriate touch",
    "self harm", "suicide",
)
_POLICY_TERMS = ("policy", "policies", "rule", "rules", "guideline", "procedure")
_QUESTION_PREFIXES = ("what", "when", "where", "who", "why", "how", "tell", "explain", "show", "give")


def _explicit_policy_question(text: str) -> bool:
    low = text.lower().strip()
    return any(term in low for term in _POLICY_TERMS) and (
        low.endswith("?") or low.startswith(_QUESTION_PREFIXES) or "tell me" in low
    )


def _safety_grievance(text: str) -> bool:
    low = text.lower()
    return any(term in low for term in _SAFETY_TERMS)


def route_turn(text: str) -> TurnRoute:
    """Classify one turn using a fixed precedence that cannot vary by workflow."""
    # Reading a safety policy is not the same as reporting an incident.
    if _explicit_policy_question(text):
        return TurnRoute("POLICY_QUESTION", {"policy_topic": text.strip()[:200]})

    # Actual safety language always outranks locations and facility nouns.
    if _safety_grievance(text):
        return TurnRoute(
            "GRIEVANCE", {"summary": text.strip()[:200], "priority": "HIGH"},
            safety_critical=True,
        )

    intent, entities, language = interpret(text)
    if intent != "UNSUPPORTED":
        return TurnRoute(intent, entities, language)

    # Common non-safety grievance language receives deterministic routing too.
    low = text.lower()
    if re.search(r"\b(?:complaint|grievance|unfair treatment|billing dispute)\b", low):
        return TurnRoute("GRIEVANCE", {"summary": text.strip()[:200]}, language)

    intent, entities, language, source = propose(text)
    return TurnRoute(intent, entities, language, source)
