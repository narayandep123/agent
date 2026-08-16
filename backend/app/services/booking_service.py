"""Deterministic in-memory demo inventory for five labs and 26 seats each."""
from string import ascii_uppercase

LABS = tuple(f"Library {number}" for number in range(201, 206))
SEATS = tuple(ascii_uppercase)
BOOKINGS: dict[tuple[str, str, str, str], str] = {}

def allocate(date: str, time: str, preferred_lab: str, preferred_seat: str, user_id: str) -> tuple[str, str]:
    labs = (preferred_lab,) if preferred_lab in LABS else LABS
    seats = (preferred_seat,) if preferred_seat in SEATS else SEATS
    for lab in labs:
        for seat in seats:
            key = (date, time, lab, seat)
            if key not in BOOKINGS:
                BOOKINGS[key] = user_id
                return lab, seat
    raise ValueError("No seat is available for that lab and time slot.")

def is_taken(date: str, time: str, lab: str, seat: str) -> bool:
    return (date, time, lab, seat) in BOOKINGS

def available_labs(date: str, time: str) -> list[str]:
    return [lab for lab in LABS if any((date, time, lab, seat) not in BOOKINGS for seat in SEATS)]
