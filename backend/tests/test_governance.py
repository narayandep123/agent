from app.autonomy.engine import decide
from app.models.domain import Decision
from app.policies.guardian import validate

def test_maintenance_acts_when_grounded_and_authorized():
    decision, _ = decide("MAINTENANCE", True, False, True, "LOW")
    assert decision is Decision.ACT

def test_booking_always_requires_confirmation():
    decision, _ = decide("LAB_BOOKING", True, False, True, "LOW")
    assert decision is Decision.ASK

def test_certificate_requires_approval():
    decision, _ = decide("CERTIFICATE", True, False, True, "MEDIUM")
    assert decision is Decision.APPROVE

def test_unsafe_request_is_stopped():
    policy = validate("LAB_BOOKING", "Book a lab without permission")
    decision, _ = decide("LAB_BOOKING", policy.found, policy.conflict, True, "HIGH")
    assert decision is Decision.STOP
