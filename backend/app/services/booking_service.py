"""Deterministic in-memory demo inventory for five labs and 26 seats each."""
from datetime import date as _date, datetime, timedelta
from string import ascii_uppercase

LABS = tuple(f"Library {number}" for number in range(201, 206))
SEATS = tuple(ascii_uppercase)
OPEN_HOUR, CLOSE_HOUR = 8, 22
BOOKINGS: dict[tuple[str, str, str, str], str] = {}

def allocate(date: str, time: str, preferred_lab: str, preferred_seat: str, user_id: str) -> tuple[str, str]:
    # Explicit choices are promises: never silently replace a requested room or
    # seat. Unspecified values may be assigned after the user confirms.
    if preferred_lab in LABS and preferred_seat in SEATS:
        key = (date, time, preferred_lab, preferred_seat)
        if key in BOOKINGS:
            raise ValueError("The selected room and seat are no longer available. Please choose an alternative.")
        BOOKINGS[key] = user_id
        return preferred_lab, preferred_seat
    lab_order = ([preferred_lab] if preferred_lab in LABS else []) + [lab for lab in LABS if lab != preferred_lab]
    seat_order = ([preferred_seat] if preferred_seat in SEATS else []) + [seat for seat in SEATS if seat != preferred_seat]
    for lab in lab_order:
        for seat in seat_order:
            key = (date, time, lab, seat)
            if key not in BOOKINGS:
                BOOKINGS[key] = user_id
                return lab, seat
    raise ValueError("No seat is available for that lab and time slot.")

def is_taken(date: str, time: str, lab: str, seat: str) -> bool:
    return (date, time, lab, seat) in BOOKINGS

def available_labs(date: str, time: str) -> list[str]:
    return [lab for lab in LABS if any((date, time, lab, seat) not in BOOKINGS for seat in SEATS)]

def alternatives(date: str, time: str, preferred_lab: str = "", preferred_seat: str = "", limit: int = 3) -> list[dict]:
    """Return transparent alternatives without reserving or silently choosing one."""
    result = []
    lab_order = ([preferred_lab] if preferred_lab in LABS else []) + [lab for lab in LABS if lab != preferred_lab]
    seat_order = ([preferred_seat] if preferred_seat in SEATS else []) + [seat for seat in SEATS if seat != preferred_seat]
    for lab in lab_order:
        for seat in seat_order:
            if not is_taken(date, time, lab, seat):
                result.append({"space": lab, "seat": seat, "date": date, "time": time})
                if len(result) == limit:
                    return result
    return result

def suggest_slot(preferred_date: str = "") -> tuple[str, str]:
    """Pick the next open one-hour slot within library hours (Mon-Sat, 08:00-22:00).

    Used when the user has no preference so the agent can propose a concrete slot
    instead of forcing them to guess a time.
    """
    now = datetime.now()
    try:
        base = _date.fromisoformat(preferred_date) if preferred_date not in ("", "Not specified", "Sunday") else now.date()
    except ValueError:
        base = now.date()
    for _ in range(8):
        if base.weekday() == 6:  # Sunday: library closed, roll to Monday.
            base += timedelta(days=1)
            continue
        start = max(OPEN_HOUR, now.hour + 1) if base == now.date() else OPEN_HOUR + 2
        if start + 1 <= CLOSE_HOUR:
            return base.isoformat(), f"{start:02d}:00-{start + 1:02d}:00"
        base += timedelta(days=1)
    return base.isoformat(), f"{OPEN_HOUR:02d}:00-{OPEN_HOUR + 1:02d}:00"
