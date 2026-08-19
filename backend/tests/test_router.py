import pytest

from app.agents.router import route_turn


@pytest.mark.parametrize(("text", "expected"), [
    ("I am being abused by students near hostel-A", "GRIEVANCE"),
    ("I was harassed beside the broken AC in room 201", "GRIEVANCE"),
    ("There is ragging near the library", "GRIEVANCE"),
    ("boys are teasing me near hostel - B", "GRIEVANCE"),
    ("Someone is catcalling and intimidating me outside the lab", "GRIEVANCE"),
    ("What does the harassment policy say?", "POLICY_QUESTION"),
    ("Tell me the anti-ragging rules", "POLICY_QUESTION"),
    ("The AC is broken in hostel A", "MAINTENANCE"),
    ("Book library 201 tomorrow at 3pm", "LAB_BOOKING"),
    ("I need a bonafide certificate", "CERTIFICATE"),
])
def test_authoritative_turn_router_precedence(text, expected):
    assert route_turn(text).intent == expected


def test_safety_route_is_explicitly_marked_critical():
    routed = route_turn("Some students threatened and abused me near the hostel")
    assert routed.intent == "GRIEVANCE"
    assert routed.safety_critical is True
    assert routed.entities["priority"] == "HIGH"
