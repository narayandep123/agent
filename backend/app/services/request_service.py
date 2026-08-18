from app.agents.proposal import propose
from app.autonomy.engine import decide
from app.models.domain import Decision, RequestStatus, ServiceRequest
from app.permissions.rbac import allowed
from app.policies.guardian import validate
from app.risk.engine import assess
from app.services.audit_service import record
from app.services.booking_service import allocate, available_labs, suggest_slot
from app.services.notification_service import notify
from datetime import date as current_date, datetime

REQUESTS: dict[str, ServiceRequest] = {}

DEFER_TERMS = (
    "any time", "anytime", "any slot", "any available", "next available", "whenever",
    "no preference", "don't know", "dont know", "not sure", "you decide", "you pick",
    "whatever works", "whatever is available", "asap", "as soon as possible", "earliest",
    "koi bhi", "kabhi bhi", "jab bhi",
)

def _notify_created(request):
    """Email the requester when a major action is taken on creation."""
    to = request.user_id
    rid = request.id
    entities = request.entities
    if request.intent == "MAINTENANCE" and request.status == RequestStatus.EXECUTED:
        notify(to, f"Maintenance ticket logged · {rid}",
               f"Hi,\n\nYour maintenance request has been logged and sent to the facilities team.\n\n"
               f"Ticket: {rid}\nIssue: {entities.get('issue', 'N/A')}\nLocation: {entities.get('location', 'N/A')}\n"
               f"Floor: {entities.get('floor', 'N/A')}\nStatus: {request.status.value}\n\n— CampusFlow AI")
    elif request.intent == "GRIEVANCE" and request.status == RequestStatus.ESCALATED:
        notify(to, f"Grievance escalated for review · {rid}",
               f"Hi,\n\nYour grievance has been recorded and escalated to a human officer.\n\n"
               f"Reference: {rid}\nStatus: {request.status.value}\nSummary: {entities.get('summary', request.text)}\n\n"
               f"You'll be notified once a decision is made.\n\n— CampusFlow AI")
    elif request.intent == "CERTIFICATE" and request.status == RequestStatus.PENDING_APPROVAL:
        notify(to, f"Certificate request received · {rid}",
               f"Hi,\n\nYour certificate request has been received and is awaiting administrator approval.\n\n"
               f"Reference: {rid}\nStatus: {request.status.value}\n\nYou'll be notified once it's reviewed.\n\n— CampusFlow AI")


def create(text: str, role, proposed_intent: str | None = None, proposed_entities: dict | None = None, user_id: str = "demo-student"):
    intent, entities, _, _ = propose(text)
    if proposed_intent is not None:
        intent = proposed_intent
    if proposed_entities is not None:
        entities = proposed_entities
    policy = validate(intent, text)
    permitted = allowed(role, intent)
    risk = assess(intent, policy.conflict, permitted)
    # If the user has no preference on day/time, propose the next open slot for them
    # instead of demanding details they may not know.
    if intent == "LAB_BOOKING" and any(term in text.lower() for term in DEFER_TERMS):
        if entities.get("date") in ("Not specified", "Sunday", "", None) or entities.get("time") in ("Not specified", "", None):
            slot_date, slot_time = suggest_slot(entities.get("date", ""))
            if entities.get("date") in ("Not specified", "Sunday", "", None):
                entities["date"] = slot_date
            if entities.get("time") in ("Not specified", "", None):
                entities["time"] = slot_time
    missing_core_booking = intent == "LAB_BOOKING" and any(entities.get(key) == "Not specified" for key in ("date", "time"))
    # Room and seat are optional: when unspecified we auto-assign rather than blocking.
    space_unspecified = intent == "LAB_BOOKING" and not missing_core_booking and entities.get("space") == "Not specified"
    booking_date = entities.get("date", "")
    sunday_booking = intent == "LAB_BOOKING" and (booking_date == "Sunday" or (booking_date not in ("", "Not specified") and current_date.fromisoformat(booking_date).weekday() == 6))
    missing_location = intent == "MAINTENANCE" and entities.get("location") == "Not specified"
    missing_floor = (
        intent == "MAINTENANCE"
        and entities.get("issue") == "Water cooler"
        and entities.get("location") != "Not specified"
        and entities.get("floor") == "Not specified"
    )
    decision, reason = decide(intent, policy.found, policy.conflict, permitted, risk, policy.uncertain)
    past_time = False
    if intent == "LAB_BOOKING" and booking_date == current_date.today().isoformat() and entities.get("time") != "Not specified":
        start_hour = int(entities["time"].split(":", 1)[0])
        past_time = start_hour <= datetime.now().hour
    outside_hours = False
    if intent == "LAB_BOOKING" and entities.get("time") not in ("", "Not specified"):
        try:
            start_part, end_part = entities["time"].split("-")
            open_hour = int(start_part.split(":")[0])
            close_hour = int(end_part.split(":")[0]) or 24
            outside_hours = open_hour < 8 or close_hour > 22
        except (ValueError, IndexError):
            outside_hours = False
    if sunday_booking:
        decision = Decision.STOP
        reason = "Library booking is unavailable on Sundays. Please choose Monday to Saturday."
    elif outside_hours:
        decision = Decision.STOP
        reason = "Libraries are open 08:00 to 22:00, Monday to Saturday. Please choose a time within these hours."
    elif past_time:
        decision = Decision.STOP
        reason = "That time slot has already started or passed according to the system clock. Please choose a future time."
    elif missing_core_booking:
        decision = Decision.ASK
        missing = [label for key, label in (("date", "day/date"), ("time", "time slot")) if entities.get(key) == "Not specified"]
        reason = (
            "Happy to help you book a library seat. To continue I still need the missing information: "
            f"{' and '.join(missing)}. Libraries are open Monday to Saturday, 08:00 to 22:00. "
            "The seat and room number are optional \u2014 I'll auto-assign the best available. "
            "If you have no preference, just say 'next available' and I'll pick a slot for you."
        )
    elif space_unspecified:
        decision = Decision.ASK
        choices = available_labs(entities["date"], entities["time"])
        pick = choices[0] if choices else "a free library"
        reason = (
            f"All set for {entities['date']} at {entities['time']}. You didn't request a specific room, "
            f"so I'll auto-assign an available one (next free: {pick}) and pick a seat for you. "
            "Reply 'confirm' to book, or tell me a library number (201\u2013205) if you'd like to choose."
        )
    elif missing_location:
        decision = Decision.ASK
        reason = "To create this maintenance request, please tell me the hostel/building and floor where the issue is located."
    elif missing_floor:
        decision = Decision.ASK
        reason = "I found the location, but need the floor number before I can create the maintenance request."
    status = RequestStatus.AWAITING_CONFIRMATION if (missing_core_booking or space_unspecified or missing_location or missing_floor) else {"ACT": RequestStatus.EXECUTED, "ASK": RequestStatus.AWAITING_CONFIRMATION, "APPROVE": RequestStatus.PENDING_APPROVAL, "ESCALATE": RequestStatus.ESCALATED, "STOP": RequestStatus.STOPPED}[decision.value]
    request = ServiceRequest(role=role, text=text, intent=intent, entities=entities, decision=decision, status=status, policy_id=policy.policy_id, policy_name=f"{policy.name} v{policy.version}", risk=risk, reason=reason)
    request.user_id = user_id
    REQUESTS[request.id] = request
    audit_id = record(request.id, request.user_id, decision.value, status.value, request.policy_name, risk)
    _notify_created(request)
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
        request.decision = Decision.ACT
        request.reason = f"Booking confirmed for {request.entities.get('space')} seat {request.entities.get('seat')} on {request.entities.get('date')} at {request.entities.get('time')}."
        result = "EXECUTED"
        entities = request.entities
        notify(request.user_id, f"Library seat booked · {request.id}",
               f"Hi,\n\nYour library seat booking is confirmed.\n\n"
               f"Reference: {request.id}\nLibrary: {entities.get('space')}\nSeat: {entities.get('seat')}\n"
               f"Date: {entities.get('date')}\nTime: {entities.get('time')}\nStatus: EXECUTED\n\n— CampusFlow AI")
    else:
        request.status = RequestStatus.STOPPED
        request.decision = Decision.STOP
        request.reason = "Booking cancelled by user."
        result = "CANCELLED"
    return request, record(request.id, request.user_id, "CONFIRMATION", result, request.policy_name, request.risk)

def review(request_id: str, approved: bool, reviewer: str = "approver", comment: str = ""):
    """Human-in-the-loop decision on a request awaiting approval or escalation."""
    request = REQUESTS.get(request_id)
    if not request:
        raise KeyError(request_id)
    if request.status not in (RequestStatus.PENDING_APPROVAL, RequestStatus.ESCALATED):
        raise ValueError("This request is not awaiting a human decision.")
    if approved:
        request.status = RequestStatus.EXECUTED
        request.decision = Decision.ACT
        if request.intent == "CERTIFICATE":
            request.reason = f"Approved by {reviewer}. {request.entities.get('certificate_type', 'Certificate')} has been issued and is ready for collection."
        elif request.intent == "GRIEVANCE":
            request.reason = f"Reviewed by {reviewer}. The grievance has been accepted and assigned to the relevant department for resolution."
        else:
            request.reason = f"Approved by {reviewer} and executed."
        result = "APPROVED"
    else:
        request.status = RequestStatus.STOPPED
        request.decision = Decision.STOP
        request.reason = f"Declined by {reviewer} after human review."
        result = "REJECTED"
    if comment:
        request.reason = f"{request.reason} Note: {comment}"
    audit_id = record(request.id, reviewer, "REVIEW", result, request.policy_name, request.risk)
    notify(request.user_id, f"Update on your request · {request.id}",
           f"Hi,\n\nThere's an update on your request {request.id}.\n\n"
           f"Decision: {'Approved' if approved else 'Declined'}\nStatus: {request.status.value}\n"
           f"Details: {request.reason}\n\n— CampusFlow AI")
    return request, audit_id

def list_requests(): return list(REQUESTS.values())
