from app.agents.interpreter import interpret
from app.autonomy.engine import decide
from app.models.domain import Decision, RequestStatus, ServiceRequest
from app.permissions.rbac import allowed
from app.policies.guardian import validate
from app.risk.engine import assess
from app.services.audit_service import record
from app.services.booking_service import allocate, available_labs
from datetime import date as current_date, datetime

REQUESTS: dict[str, ServiceRequest] = {}

def create(text: str, role, proposed_intent: str | None = None, proposed_entities: dict | None = None):
    intent, entities, _ = interpret(text)
    if proposed_intent is not None:
        intent = proposed_intent
    if proposed_entities is not None:
        entities = proposed_entities
    policy = validate(intent, text)
    permitted = allowed(role, intent)
    risk = assess(intent, policy.conflict, permitted)
    missing_core_booking = intent == "LAB_BOOKING" and any(entities.get(key) == "Not specified" for key in ("date", "time"))
    missing_library = intent == "LAB_BOOKING" and not missing_core_booking and entities.get("space") == "Not specified"
    booking_date = entities.get("date", "")
    sunday_booking = intent == "LAB_BOOKING" and (booking_date == "Sunday" or (booking_date not in ("", "Not specified") and current_date.fromisoformat(booking_date).weekday() == 6))
    missing_location = intent == "MAINTENANCE" and entities.get("location") == "Not specified"
    missing_floor = (
        intent == "MAINTENANCE"
        and entities.get("issue") == "Water cooler"
        and entities.get("location") != "Not specified"
        and entities.get("floor") == "Not specified"
    )
    decision, reason = decide(intent, policy.found, policy.conflict, permitted, risk)
    past_time = False
    if intent == "LAB_BOOKING" and booking_date == current_date.today().isoformat() and entities.get("time") != "Not specified":
        start_hour = int(entities["time"].split(":", 1)[0])
        past_time = start_hour <= datetime.now().hour
    if sunday_booking:
        decision = Decision.STOP
        reason = "Library booking is unavailable on Sundays. Please choose Monday to Saturday."
    elif past_time:
        decision = Decision.STOP
        reason = "That time slot has already started or passed according to the system clock. Please choose a future time."
    elif missing_core_booking:
        decision = Decision.ASK
        missing = [label for key, label in (("date", "day/date"), ("time", "time slot")) if entities.get(key) == "Not specified"]
        reason = f"Please provide only the missing information: {', '.join(missing)}."
    elif missing_library:
        decision = Decision.ASK
        choices = available_labs(entities["date"], entities["time"])
        reason = f"Multiple libraries are available for that slot: {', '.join(choices)}. Which library would you like?"
    elif missing_location:
        decision = Decision.ASK
        reason = "To create this maintenance request, please tell me the hostel/building and floor where the issue is located."
    elif missing_floor:
        decision = Decision.ASK
        reason = "I found the location, but need the floor number before I can create the maintenance request."
    status = RequestStatus.AWAITING_CONFIRMATION if (missing_core_booking or missing_library or missing_location or missing_floor) else {"ACT": RequestStatus.EXECUTED, "ASK": RequestStatus.AWAITING_CONFIRMATION, "APPROVE": RequestStatus.PENDING_APPROVAL, "STOP": RequestStatus.STOPPED}[decision.value]
    request = ServiceRequest(role=role, text=text, intent=intent, entities=entities, decision=decision, status=status, policy_id=policy.policy_id, policy_name=f"{policy.name} v{policy.version}", risk=risk, reason=reason)
    REQUESTS[request.id] = request
    audit_id = record(request.id, request.user_id, decision.value, status.value, request.policy_name, risk)
    return request, policy, permitted, audit_id

def confirm(request_id: str, confirmed: bool):
    request = REQUESTS.get(request_id)
    if not request: raise KeyError(request_id)
    if request.status == RequestStatus.EXECUTED:
        # Idempotent confirmation: repeated clicks must not create duplicate bookings.
        return request, record(request.id, request.user_id, "CONFIRMATION", "ALREADY_EXECUTED", request.policy_name, request.risk)
    if request.status != RequestStatus.AWAITING_CONFIRMATION:
        raise ValueError("This request is not awaiting confirmation.")
    if confirmed:
        if request.intent == "LAB_BOOKING":
            lab, seat = allocate(request.entities["date"], request.entities["time"], request.entities["space"], request.entities["seat"], request.user_id)
            request.entities["space"], request.entities["seat"] = lab, seat
        request.status = RequestStatus.EXECUTED
        request.reason = f"Booking confirmed for {request.entities.get('space')} seat {request.entities.get('seat')} on {request.entities.get('date')} at {request.entities.get('time')}."
        result = "EXECUTED"
    else:
        request.status = RequestStatus.STOPPED
        request.reason = "Booking cancelled by user."
        result = "CANCELLED"
    return request, record(request.id, request.user_id, "CONFIRMATION", result, request.policy_name, request.risk)

def list_requests(): return list(REQUESTS.values())
