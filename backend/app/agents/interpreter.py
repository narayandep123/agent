"""Proposal-only deterministic intent and entity extraction for the demo."""
import re
from datetime import date, timedelta

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
DEFAULT_SLOT_HOURS = 1

def _to_24h(hour: int, meridiem: str | None) -> int:
    if meridiem == "pm":
        return hour if hour == 12 else hour + 12
    if meridiem == "am":
        return 0 if hour == 12 else hour
    # No meridiem: assume study hours, so 1-7 means afternoon/evening.
    return hour + 12 if 1 <= hour <= 7 else hour

def _fmt(hour: int, minute: int = 0) -> str:
    return f"{hour % 24:02d}:{minute:02d}"

def _parse_time(lower: str) -> str:
    rng = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:to|till|until|upto|se|[-\u2013\u2014])\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", lower)
    if rng:
        sh, sm, sap, eh, em, eap = rng.groups()
        # Share a single meridiem across both ends of the range.
        sap, eap = sap or eap, eap or sap
        start, end = _to_24h(int(sh), sap), _to_24h(int(eh), eap)
        if end <= start:
            end = _to_24h(int(eh), "pm")
            if end <= start:
                end = start + DEFAULT_SLOT_HOURS
        return f"{_fmt(start, int(sm or 0))}-{_fmt(end, int(em or 0))}"
    minute, meridiem = 0, None
    clock = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?", lower)
    ampm = re.search(r"(?:\bat\s*|\bfrom\s*|\bby\s*|@\s*)?(\d{1,2})\s*(am|pm)\b", lower)
    baje = re.search(r"(\d{1,2})\s*baje", lower)
    at = re.search(r"(?:\bat|\bfrom|@)\s*(\d{1,2})\b(?!\s*(?:st|nd|rd|th|:|/))", lower)
    if clock:
        hour, minute, meridiem = int(clock.group(1)), int(clock.group(2)), clock.group(3)
    elif ampm:
        hour, meridiem = int(ampm.group(1)), ampm.group(2)
    elif baje:
        hour = int(baje.group(1))
    elif at:
        hour = int(at.group(1))
    else:
        return "Not specified"
    duration = re.search(r"for\s+(\d{1,2})\s*(?:hours?|hrs?|h)\b", lower)
    start = _to_24h(hour, meridiem)
    end = start + (int(duration.group(1)) if duration else DEFAULT_SLOT_HOURS)
    return f"{_fmt(start, minute)}-{_fmt(end)}"

def _parse_date(lower: str) -> str:
    today = date.today()
    if any(x in lower for x in ("day after tomorrow", "parso", "parson")):
        return (today + timedelta(days=2)).isoformat()
    if "today" in lower or "aaj" in lower:
        return today.isoformat()
    if re.search(r"\btom+or+ow\b", lower) or any(x in lower for x in ("tommorrow", "tomorow", "tmrw", "kal")):
        return (today + timedelta(days=1)).isoformat()
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", lower)
    if iso:
        return iso.group(1)
    dmy = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", lower)
    if dmy:
        day, month = int(dmy.group(1)), int(dmy.group(2))
        year = int(dmy.group(3) or today.year) if not dmy.group(3) or len(dmy.group(3)) == 4 else 2000 + int(dmy.group(3))
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass
    named = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s*)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", lower) \
        or re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s*(\d{1,2})", lower)
    if named:
        groups = named.groups()
        day = int(groups[0]) if groups[0].isdigit() else int(groups[1])
        month = MONTHS[groups[1] if groups[0].isdigit() else groups[0]]
        try:
            candidate = date(today.year, month, day)
            if candidate < today:
                candidate = date(today.year + 1, month, day)
            return candidate.isoformat()
        except ValueError:
            pass
    weekday = re.search(r"\b(?:next|this|coming)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lower)
    if weekday:
        delta = (WEEKDAYS[weekday.group(1)] - today.weekday()) % 7 or 7
        return (today + timedelta(days=delta)).isoformat()
    return "Not specified"

def booking_entities(text: str) -> dict:
    lower = text.lower()
    room = re.search(r"(?:library|lab|room)\s*(20[1-5])", lower)
    seat = re.search(r"\bseat\s*([a-z])\b", lower)
    return {
        "space": f"Library {room.group(1)}" if room else "Not specified",
        "time": _parse_time(lower),
        "date": _parse_date(lower),
        "seat": seat.group(1).upper() if seat else "Auto assign",
    }


# Concrete facilities issues we can recognise from everyday language. Order matters:
# the first matching category wins so more specific items (water cooler) are tried
# before generic ones.
MAINTENANCE_ISSUES = (
    ("Water cooler", ("water cooler", "cooler", "drinking water", "dispenser")),
    ("Air conditioner", ("air conditioner", "air conditioning", " ac ", "hvac", "cooling")),
    ("Fan", ("fan",)),
    ("Lighting", ("light", "bulb", "tubelight", "tube light", "lamp")),
    ("Electrical", ("switch", "socket", "plug point", "power socket", "wiring", "electrical")),
    ("WiFi / Internet", ("wifi", "wi-fi", "internet")),
    ("Projector", ("projector",)),
    ("Plumbing", ("tap", "toilet", "washroom", "pipe", "flush", "drain", "leak")),
    ("Furniture", ("chair", "desk", "bench", "door", "window", "furniture", "cupboard")),
)
# Generic verbs that signal a maintenance complaint even without a named component.
MAINTENANCE_VERBS = (
    "broken", "not working", "isn't working", "doesn't work", "doesnt work", "faulty",
    "out of order", "repair", "damaged", "kaam nahi", "kharab",
)

# Spelled-out floors, so "second floor" / "ground floor" work as well as "2nd floor".
FLOOR_WORDS = {
    "ground": "0", "zeroth": "0", "first": "1", "second": "2", "third": "3",
    "fourth": "4", "fifth": "5", "sixth": "6", "seventh": "7", "eighth": "8",
    "ninth": "9", "tenth": "10",
}
# Tolerate common typos like "flooor"/"flr" via ``flo+rs?`` and a few synonyms.
_FLOOR_UNIT = r"(?:flo+rs?|flr|manzil|storey|story|level)"


def parse_floor(text: str, loose: bool = False) -> str | None:
    """Extract a floor number. With ``loose=True`` a bare answer ("5th", "second",
    "5") is accepted as the floor, for when the agent has just asked for it."""
    lower = f" {text.lower()} "
    match = (re.search(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s*{_FLOOR_UNIT}", lower)
             or re.search(rf"{_FLOOR_UNIT}\s*#?\s*(\d{{1,2}})", lower))
    if match:
        return match.group(1)
    for word, num in FLOOR_WORDS.items():
        if re.search(rf"\b{word}\b\s*{_FLOOR_UNIT}", lower) or re.search(rf"{_FLOOR_UNIT}\s*\b{word}\b", lower):
            return num
    if loose:
        bare = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", lower)
        if bare:
            return bare.group(1)
        for word, num in FLOOR_WORDS.items():
            if re.search(rf"\b{word}\b", lower):
                return num
    return None


def maintenance_entities(text: str) -> dict:
    lower = f" {text.lower()} "
    issue = "Not specified"
    for label, keywords in MAINTENANCE_ISSUES:
        # Match complete words/phrases: "flight" must never match "light" and
        # "fanatic" must never match "fan".
        if any(re.search(rf"(?<!\w){re.escape(k.strip())}(?!\w)", lower) for k in keywords):
            issue = label
            break
    if issue == "Not specified" and any(v in lower for v in MAINTENANCE_VERBS):
        issue = "Facility maintenance"
    parts = []
    # Words that look like an identifier but are really filler — never a block/building name.
    _loc_stop = {"and", "the", "is", "in", "on", "of", "at", "no", "my", "has", "have",
                 "floor", "room", "block", "building", "hostel", "issue", "problem",
                 "not", "with", "for", "near", "side"}
    # Numbered spaces: "room 204", "lab A-12", "hostel 5", "building 3".
    loc_num = re.search(r"(?:hostel|room|classroom|class|lab|hall|library|office|building|wing|tower)\s*(?:no\.?|number|#|:|-)?\s*([a-z]?-?\d{1,3}[a-z]?)\b", lower)
    if loc_num:
        parts.append(re.sub(r"\s*[-:#]\s*", " ", loc_num.group(0)).strip().title())
    # Named blocks/buildings/hostels without a number: "building - abc", "hostel xyz".
    named = re.search(r"\b(hostel|building|wing|tower)\s*(?:no\.?|number|#|:|-|named|name)?\s*([a-z]{1,4})\b", lower)
    if named and named.group(2) not in _loc_stop:
        label = f"{named.group(1).title()} {named.group(2).upper()}"
        if label.lower() not in " ".join(parts).lower():
            parts.append(label)
    loc_block = re.search(r"\b([a-z]{1,4})[-\s]?block\b", lower) or re.search(r"\bblock[-\s]?([a-z]{1,4})\b", lower)
    if loc_block and loc_block.group(1) not in _loc_stop:
        block_label = f"{loc_block.group(1).upper()} Block"
        if block_label.lower() not in " ".join(parts).lower():
            parts.append(block_label)
    location = ", ".join(parts) if parts else "Not specified"
    floor = parse_floor(text) or "Not specified"
    return {"location": location, "floor": floor, "issue": issue}


def interpret(text: str) -> tuple[str, dict, str]:
    lower = text.lower()
    maintenance = maintenance_entities(text)
    if maintenance["issue"] != "Not specified":
        return "MAINTENANCE", maintenance, "hinglish" if any(w in lower for w in ("kaam nahi", "kharab")) else "en"
    # A generic word such as "book" is not enough: flights, hotels and tickets
    # are outside CampusFlow. Require an actual campus space/resource.
    if any(w in lower for w in ("library", "lab", "study room", "reading room", "computer room", "classroom", "seat")) \
            or ("room" in lower and any(w in lower for w in ("book", "booking", "reserve"))):
        return "LAB_BOOKING", booking_entities(text), "hinglish" if "kal" in lower else "en"
    if "bonaf" in lower.replace(" ", "") or any(w in lower for w in ("certificate", "transcript", "praman patra", "pramaan patra")):
        return "CERTIFICATE", {"certificate_type": "Bonafide certificate"}, "en"
    if "grievance" in lower or "complain about" in lower or "complaint about" in lower or any(w in lower for w in ("harassment", "harassed", "abuse", "abused", "bully", "threat", "unsafe", "misbehav", "ragging", "discriminat", "unfair treatment", "teasing", "teased", "eve teasing", "molest", "catcall", "intimidat", "inappropriate touch")):
        return "GRIEVANCE", {"summary": text.strip()[:200]}, "en"
    # Policy lookup is a supported read-only task. Keep this in the deterministic
    # fallback so Gemini latency/quota cannot turn a valid campus-policy question
    # into an out-of-scope request.
    policy_terms = ("policy", "policies", "rule", "rules", "guideline", "curfew", "closing time", "opening time", "timing", "schedule", "departure")
    campus_topics = ("hostel", "campus", "college", "library", "lab", "student", "faculty", "certificate", "complaint", "grievance", "attendance", "exam", "scholarship", "maintenance", "wifi", "id card", "transcript")
    if any(term in lower for term in policy_terms) and any(topic in lower for topic in campus_topics):
        return "POLICY_QUESTION", {"policy_topic": text.strip()[:200]}, "en"
    return "UNSUPPORTED", {}, "en"
