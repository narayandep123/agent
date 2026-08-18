from app.autonomy.engine import decide
from app.models.domain import Decision
from app.policies.guardian import validate
from app.agents.interpreter import interpret
from app.agents.translator import to_english
from app.agents.interpreter import maintenance_entities
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


def test_hinglish_bonafide_is_certificate_not_booking():
    # "banwana" must not be read as "book"; a mis-spelled bonafide is still a certificate.
    english = to_english("mere ko bonafied banwana hai", "hinglish")
    intent, _, _ = interpret(english)
    assert intent == "CERTIFICATE"


def test_flight_booking_is_not_a_campus_lab_booking():
    from app.agents.interpreter import interpret
    intent, entities, _ = interpret("I want to book a flight")
    assert intent == "UNSUPPORTED"
    assert entities == {}


def test_named_building_location_is_captured():
    # An alphabetic block/building name (no digit) must be recognised as the location.
    entities = maintenance_entities("building - abc, floor 5th")
    assert entities["location"] == "Building ABC"
    assert entities["floor"] == "5"


def test_bonafide_query_retrieves_bonafide_policy():
    # A query naming "bonafide" must surface the specific bonafide procedure,
    # not the generic academic certificate policy.
    from app.rag.retriever import search
    top = search("what all documents are needed for a bonafide certificate", k=1)[0]
    assert top.policy_id == "ACA-BONAFIDE-011"


def test_offdomain_query_is_not_grounded():
    # An unrelated question that only shares a generic word must NOT be answered
    # from a tangential policy — the agent should recognise it lacks evidence.
    from app.rag.retriever import search, is_grounded
    assert not is_grounded(search("exam re-evaluation process", k=1)[0])
    assert not is_grounded(search("who is the college principal", k=1)[0])
    # ...but a genuinely on-topic question stays grounded.
    assert is_grounded(search("how do I get a bonafide certificate", k=1)[0])
    assert is_grounded(search("what are the library timings", k=1)[0])

def test_weak_evidence_escalates_instead_of_acting():
    decision, _ = decide("MAINTENANCE", True, False, True, "LOW", uncertain=True)
    assert decision is Decision.ESCALATE

def test_validate_grounds_request_in_retrieved_policy():
    policy = validate("CERTIFICATE", "I need a bonafide certificate for my scholarship")
    assert policy.found
    assert policy.policy_id
    assert policy.source_section
    assert policy.citation
    assert 0 < policy.confidence <= 0.99
