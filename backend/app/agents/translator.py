"""Multilingual input/output layer.

Two responsibilities, both with a deterministic offline fallback:

* ``to_english`` normalizes a user's message to English so the deterministic
  interpreter can classify it.
* ``localize`` renders a system message in the user's language.

When the Gemini adapter is enabled (``GEMINI_API_KEY`` set and the SDK present),
translation is done by the model for full coverage. Otherwise a curated phrase
map and a small Hinglish glossary provide believable results for the demo, and
anything unmapped falls back to English so nothing ever breaks.
"""
from __future__ import annotations

import os
import re

SUPPORTED_LANGUAGES = ("en", "hi", "hinglish")

ASSISTANT_GREETING = "Hello! I can help you report maintenance issues, book a lab or room, request a certificate, or explain an official policy."
ASSISTANT_FALLBACK = "I’m here for campus services. Try describing a maintenance issue, lab/room booking, certificate request, or ask about an official policy."

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_HINGLISH_HINTS = ("kal", "aaj", "kaam nahi", "kharab", "chahiye", "karna", "banwana", "paani", "manzil", "kamra", "theek")

# Offline glossary to help the deterministic interpreter understand Hinglish.
_GLOSSARY = {
    "kaam nahi kar raha": "not working",
    "kaam nahi": "not working",
    "kharab": "broken",
    "chahiye": "need",
    "banwana hai": "i need",
    "banwana": "get made",
    "banwani hai": "i need",
    "book karna": "book",
    "book karni hai": "book",
    "bonafied": "bonafide",
    "praman patra": "certificate",
    "pramaan patra": "certificate",
    "paani": "water",
    "pani": "water",
    "manzil": "floor",
    "kamra": "room",
    "kal": "tomorrow",
    "aaj": "today",
}

_LANGUAGE_NAME = {"hi": "Hindi", "hinglish": "Hindi written in the Latin script (Hinglish)"}

# Curated translations for the finite set of static system messages.
_PHRASES: dict[str, dict[str, str]] = {
    ASSISTANT_GREETING: {
        "hi": "नमस्ते! मैं रखरखाव की समस्या दर्ज करने, लैब या कमरा बुक करने, प्रमाणपत्र का अनुरोध करने, या किसी आधिकारिक नीति को समझाने में आपकी मदद कर सकता हूँ।",
        "hinglish": "Namaste! Main maintenance issue report karne, lab ya room book karne, certificate request karne, ya kisi official policy ko samjhane me aapki help kar sakta hoon.",
    },
    ASSISTANT_FALLBACK: {
        "hi": "मैं कैंपस सेवाओं के लिए हूँ। कृपया रखरखाव की समस्या, लैब/कमरा बुकिंग, प्रमाणपत्र अनुरोध बताएं, या किसी आधिकारिक नीति के बारे में पूछें।",
        "hinglish": "Main campus services ke liye hoon. Maintenance issue, lab/room booking, certificate request bataiye, ya kisi official policy ke baare me poochhiye.",
    },
    "No problem, I've cancelled that request. Is there anything else I can help you with?": {
        "hi": "कोई बात नहीं, मैंने वह अनुरोध रद्द कर दिया है। क्या मैं आपकी और किसी चीज़ में मदद कर सकता हूँ?",
        "hinglish": "Koi baat nahi, maine wo request cancel kar di hai. Kya main aapki aur kisi cheez me help kar sakta hoon?",
    },
    "Low-risk maintenance request is authorized and was submitted.": {
        "hi": "कम-जोखिम वाला रखरखाव अनुरोध अधिकृत है और दर्ज कर दिया गया है।",
        "hinglish": "Low-risk maintenance request authorized hai aur submit kar di gayi hai.",
    },
    "Availability and permissions are verified; confirmation is required before booking.": {
        "hi": "उपलब्धता और अनुमतियाँ सत्यापित हैं; बुकिंग से पहले पुष्टि आवश्यक है।",
        "hinglish": "Availability aur permissions verified hain; booking se pehle confirmation zaroori hai.",
    },
    "Certificate requests require authorized human approval.": {
        "hi": "प्रमाणपत्र अनुरोधों के लिए अधिकृत मानव अनुमोदन आवश्यक है।",
        "hinglish": "Certificate requests ke liye authorized human approval zaroori hai.",
    },
    "Policy evidence is weak or ambiguous. Escalating to a human reviewer instead of acting.": {
        "hi": "नीति प्रमाण कमजोर या अस्पष्ट है। कार्रवाई करने के बजाय इसे मानव समीक्षक को भेजा जा रहा है।",
        "hinglish": "Policy evidence weak ya unclear hai. Action lene ke bajay ise human reviewer ko escalate kiya ja raha hai.",
    },
    "Request conflicts with institutional policy.": {
        "hi": "अनुरोध संस्थागत नीति से टकराता है।",
        "hinglish": "Request institutional policy se conflict karti hai.",
    },
    "Your role is not authorized for this action.": {
        "hi": "आपकी भूमिका इस कार्य के लिए अधिकृत नहीं है।",
        "hinglish": "Aapka role is action ke liye authorized nahi hai.",
    },
    "No verified institutional policy supports this action.": {
        "hi": "कोई सत्यापित संस्थागत नीति इस कार्य का समर्थन नहीं करती।",
        "hinglish": "Koi verified institutional policy is action ko support nahi karti.",
    },
    "Library booking is unavailable on Sundays. Please choose Monday to Saturday.": {
        "hi": "रविवार को पुस्तकालय बुकिंग उपलब्ध नहीं है। कृपया सोमवार से शनिवार चुनें।",
        "hinglish": "Sunday ko library booking available nahi hai. Kripya Monday se Saturday choose kijiye.",
    },
    "Libraries are open 08:00 to 22:00, Monday to Saturday. Please choose a time within these hours.": {
        "hi": "पुस्तकालय सोमवार से शनिवार, सुबह 08:00 से रात 22:00 तक खुले रहते हैं। कृपया इन्हीं घंटों में समय चुनें।",
        "hinglish": "Libraries Monday se Saturday, 08:00 se 22:00 tak open rehti hain. Kripya inhi hours me time choose kijiye.",
    },
    "To create this maintenance request, please tell me the hostel/building and floor where the issue is located.": {
        "hi": "यह रखरखाव अनुरोध बनाने के लिए, कृपया वह छात्रावास/भवन और मंज़िल बताएं जहाँ समस्या है।",
        "hinglish": "Ye maintenance request banane ke liye, kripya hostel/building aur floor bataiye jahan issue hai.",
    },
    "I found the location, but need the floor number before I can create the maintenance request.": {
        "hi": "मुझे स्थान मिल गया, लेकिन रखरखाव अनुरोध बनाने से पहले मंज़िल संख्या चाहिए।",
        "hinglish": "Mujhe location mil gaya, lekin maintenance request banane se pehle floor number chahiye.",
    },
}


def resolve_language(requested: str | None, text: str) -> str:
    if requested and requested.lower() in SUPPORTED_LANGUAGES:
        return requested.lower()
    if _DEVANAGARI.search(text):
        return "hi"
    lowered = text.lower()
    if any(hint in lowered for hint in _HINGLISH_HINTS):
        return "hinglish"
    return "en"


def _apply_glossary(text: str) -> str:
    result = text
    for term, english in _GLOSSARY.items():
        result = re.sub(rf"\b{re.escape(term)}\b", english, result, flags=re.IGNORECASE)
    return result


def _llm_translate(text: str, instruction: str) -> str | None:
    if not os.getenv("GEMINI_API_KEY"):
        return None
    try:
        import google.generativeai as genai  # type: ignore

        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model = genai.GenerativeModel(
            os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
            system_instruction=instruction,
        )
        response = model.generate_content(text, generation_config={"temperature": 0.0})
        translated = (response.text or "").strip()
        return translated or None
    except Exception:
        return None


def to_english(text: str, language: str) -> str:
    if language == "en":
        return text
    llm = _llm_translate(text, "Translate the user's message to English. Return only the translation, no notes.")
    if llm:
        return llm
    return _apply_glossary(text)


def localize(message: str, language: str) -> str:
    if language == "en" or not message:
        return message
    llm = _llm_translate(
        message,
        f"Translate the user's message to {_LANGUAGE_NAME[language]}. Keep IDs, numbers, times and dates unchanged. Return only the translation.",
    )
    if llm:
        return llm
    return _PHRASES.get(message, {}).get(language, message)
