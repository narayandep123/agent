"""Proposal-only deterministic intent and entity extraction for the demo."""
import re
from datetime import date, timedelta

def booking_entities(text: str) -> dict:
    lower = text.lower()
    room = re.search(r"(?:library|lab|room)\s*(20[1-5])", lower)
    match = re.search(r"(\d{1,2})(?:\s*(am|pm))?\s*(?:to|se|-)\s*(\d{1,2})(?:\s*(am|pm))?", lower)
    time = "Not specified"
    if match:
        start, start_meridiem, end, end_meridiem = match.groups()
        start_hour, end_hour = int(start), int(end)
        effective_start = start_meridiem or end_meridiem
        if effective_start == "pm" and start_hour < 12: start_hour += 12
        if end_meridiem == "pm" and end_hour < 12: end_hour += 12
        if start_meridiem == "am" and start_hour == 12: start_hour = 0
        if end_meridiem == "am" and end_hour == 12: end_hour = 0
        time = f"{start_hour:02d}:00-{end_hour:02d}:00"
    tomorrow = bool(re.search(r"\btom+or+ow\b", lower)) or any(x in lower for x in ("tommorrow", "tomorow", "kal"))
    if "today" in lower or "aaj" in lower: requested_date = date.today().isoformat()
    elif tomorrow: requested_date = (date.today() + timedelta(days=1)).isoformat()
    elif "sunday" in lower: requested_date = "Sunday"
    else: requested_date = "Not specified"
    seat = re.search(r"\bseat\s*([a-z])\b", lower)
    return {"space": f"Library {room.group(1)}" if room else "Not specified", "time": time, "date": requested_date, "seat": seat.group(1).upper() if seat else "Auto assign"}

def interpret(text: str) -> tuple[str, dict, str]:
    lower = text.lower()
    if "cooler" in lower or any(w in lower for w in ("ac", "maintenance", "repair", "leak", "kaam nahi")):
        location = re.search(r"(?:classroom|room|lab|class|hostel|block)\s*([a-z]?\d{1,3})", lower)
        floor = re.search(r"(?:floor|fl|manzil)\s*(\d{1,2})", lower)
        return "MAINTENANCE", {"location": location.group(0).title() if location else "Not specified", "floor": floor.group(1) if floor else "Not specified", "issue": "Water cooler" if "cooler" in lower else "Facility maintenance"}, "hinglish" if "kaam nahi" in lower else "en"
    if any(w in lower for w in ("book", "booking", "reserve", "library", "lab", "slot")):
        return "LAB_BOOKING", booking_entities(text), "hinglish" if "kal" in lower else "en"
    if any(w in lower for w in ("bonafide", "certificate")):
        return "CERTIFICATE", {"certificate_type": "Bonafide certificate"}, "en"
    return "UNSUPPORTED", {}, "en"
