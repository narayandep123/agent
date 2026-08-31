"""Deterministic multilingual tone and active-emergency signals."""
from __future__ import annotations

from dataclasses import dataclass


_FRUSTRATION = (
    "already told", "already shared", "said already", "told you before", "how many times",
    "stop asking", "don't ask again", "dont ask again", "same question", "again and again",
    "just do it", "are you listening", "this is frustrating", "so frustrating", "seriously",
    "whatever", "get lost", "forget it", "leave me alone", "just skip it", "skip it",
    "don't care", "dont care", "idiot", "stupid", "pagal ho kya", "pagal hai kya",
    "great, asking", "very helpful", "obviously", "what part", "why do you keep",
    "pehle hi", "bata chuka", "bata chuki", "kitni baar", "bar bar", "baar baar",
    "phir se", "same cheez", "bas karo", "kar do", "maine diya", "maine bataya",
    "पहले ही", "कितनी बार", "बार बार", "फिर से", "बस करो", "बता चुका", "बता चुकी",
)

_EMERGENCY_GROUPS = {
    "MEDICAL": (
        "medical emergency", "not breathing", "can't breathe", "cannot breathe", "heart attack",
        "unconscious", "collapsed", "severe bleeding", "bleeding heavily", "overdose",
        "behosh", "saans nahi", "saans nahin", "khoon bah", "बेहोश", "सांस नहीं", "खून बह",
    ),
    "FIRE": (
        "building is on fire", "hostel is on fire", "room is on fire", "fire right now",
        "fire has started", "smoke everywhere", "trapped in fire", "aag lagi", "aag lag gayi",
        "jal raha hai", "jal rahi hai", "आग लगी", "आग लग गई", "जल रहा", "धुआं",
    ),
    "VIOLENCE": (
        "attacking me", "being attacked", "attack happening", "has a weapon", "with a weapon",
        "has a gun", "has a knife", "violence right now", "fight happening", "threatening me now",
        "maar rahe", "mar rahe", "pit rahe", "पीट रहे", "मार रहे", "हथियार", "चाकू",
    ),
    "SELF_HARM": (
        "kill myself", "end my life", "want to die", "suicide", "self harm", "hurt myself",
        "mar jana", "jaan de", "खुदकुशी", "आत्महत्या", "मर जाना", "जान दे",
    ),
    "HARASSMENT_IN_PROGRESS": (
        "harassing me right now", "harassment is happening", "following me right now",
        "stalking me now", "teasing me right now", "touching me right now", "won't leave me alone",
        "abhi pareshan", "peecha kar raha", "peecha kar rahe", "अभी परेशान", "पीछा कर रहा", "पीछा कर रहे",
    ),
}

_POLICY_LANGUAGE = ("policy", "rule", "guideline", "procedure", "what does", "tell me about")
_ACTIVE_LANGUAGE = ("now", "right now", "currently", "happening", "help", "urgent", "emergency", "abhi", "अभी")


@dataclass(frozen=True)
class Tone:
    frustrated: bool = False
    emergency: bool = False
    emergency_type: str = ""


def analyze(text: str) -> Tone:
    low = (text or "").lower().replace("’", "'")
    frustrated = any(term in low for term in _FRUSTRATION)
    # A policy-reading request is not itself an emergency unless it also uses
    # explicit active/urgent language.
    policy_only = any(term in low for term in _POLICY_LANGUAGE) and not any(term in low for term in _ACTIVE_LANGUAGE)
    if not policy_only:
        for category, terms in _EMERGENCY_GROUPS.items():
            if any(term in low for term in terms):
                return Tone(frustrated, True, category)
    return Tone(frustrated, False, "")
