from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

class Role(StrEnum):
    STUDENT = "STUDENT"
    FACULTY = "FACULTY"
    STAFF = "STAFF"
    APPROVER = "APPROVER"
    ADMIN = "ADMIN"

class Decision(StrEnum):
    ACT = "ACT"
    ASK = "ASK"
    APPROVE = "APPROVE"
    STOP = "STOP"

class RequestStatus(StrEnum):
    EXECUTED = "EXECUTED"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    STOPPED = "STOPPED"

@dataclass
class ServiceRequest:
    id: str = field(default_factory=lambda: f"CF-2026-{uuid4().hex[:6].upper()}")
    user_id: str = "demo-student"
    role: Role = Role.STUDENT
    text: str = ""
    intent: str = "UNKNOWN"
    entities: dict = field(default_factory=dict)
    decision: Decision = Decision.STOP
    status: RequestStatus = RequestStatus.STOPPED
    policy_id: str = ""
    policy_name: str = ""
    risk: str = "HIGH"
    reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
