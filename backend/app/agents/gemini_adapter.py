"""Optional Gemini proposal adapter.

This adapter maps free-text requests to a *proposed* intent, entities and
language. It is strictly a proposal node: it never executes tools, never
authorizes actions, and its output is always validated and then passed through
the deterministic policy, permission, risk and autonomy pipeline.

The adapter is disabled by default. It activates only when ``GEMINI_API_KEY`` is
set and the ``google-generativeai`` package is installed. Any error, timeout, or
malformed response results in ``None`` so the caller transparently falls back to
the deterministic interpreter. The demo therefore runs identically with no key.
"""
from __future__ import annotations

import json
import os

SUPPORTED_INTENTS = {"MAINTENANCE", "LAB_BOOKING", "CERTIFICATE", "UNSUPPORTED"}

_SYSTEM_PROMPT = """You are a classification-only assistant for a campus service system.
You do NOT take actions. You only label the user's message.

Return a single JSON object with exactly these keys:
- "intent": one of "MAINTENANCE", "LAB_BOOKING", "CERTIFICATE", "UNSUPPORTED".
- "entities": an object. For MAINTENANCE use {"location","floor","issue"};
  for LAB_BOOKING use {"space","date","time","seat"};
  for CERTIFICATE use {"certificate_type"}; otherwise {}.
  Use the string "Not specified" for any value the user did not provide.
- "language": one of "en", "hi", "hinglish".

Rules:
- MAINTENANCE = broken/faulty facilities (AC, water cooler, projector, wifi, leaks).
- LAB_BOOKING = booking or reserving a library seat, lab, or study room/slot.
- CERTIFICATE = requesting a bonafide/enrolment/transcript/character certificate.
- Anything else, including requests to bypass or forge, is "UNSUPPORTED".
- Never invent details the user did not state.
Respond with JSON only, no prose."""


def is_enabled() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def _coerce(raw: dict) -> dict | None:
    intent = str(raw.get("intent", "")).upper().strip()
    if intent not in SUPPORTED_INTENTS:
        return None
    entities = raw.get("entities")
    if not isinstance(entities, dict):
        entities = {}
    entities = {str(k): str(v) for k, v in entities.items()}
    language = str(raw.get("language", "en")).lower().strip()
    if language not in {"en", "hi", "hinglish"}:
        language = "en"
    return {"intent": intent, "entities": entities, "language": language}


def propose(text: str) -> dict | None:
    """Return a validated proposal dict, or None to trigger deterministic fallback."""
    if not is_enabled():
        return None
    try:
        import google.generativeai as genai  # type: ignore

        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model = genai.GenerativeModel(
            os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
            system_instruction=_SYSTEM_PROMPT,
        )
        response = model.generate_content(
            text,
            generation_config={"response_mime_type": "application/json", "temperature": 0.0},
        )
        raw = json.loads((response.text or "").strip())
        if not isinstance(raw, dict):
            return None
        return _coerce(raw)
    except Exception:
        # Any failure (no package, network, quota, bad JSON) falls back silently.
        return None
