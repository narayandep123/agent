from typing import Any
from pydantic import BaseModel, Field
from app.models.domain import Role

class RequestInput(BaseModel):
    text: str = Field(min_length=3, max_length=1000)
    role: Role = Role.STUDENT
    language: str = "auto"
    conversation_id: str | None = None

class ChatMessageInput(BaseModel):
    role: str
    text: str = ""
    payload: dict[str, Any] | None = None

class ConfirmationInput(BaseModel):
    confirmed: bool

class ReviewInput(BaseModel):
    approved: bool
    reviewer: str = "approver"
    comment: str = ""

class MaintenanceUpdateInput(BaseModel):
    status: str
    assigned_to: str = ""
    comment: str = ""

class DecisionResponse(BaseModel):
    request_id: str
    intent: str
    entities: dict[str, Any]
    decision: str
    status: str
    policy: dict[str, Any]
    permission: str
    risk: str
    evidence: str
    message: str
    audit_id: str
    requires_confirmation: bool = False
    trace: list[dict[str, Any]] = Field(default_factory=list)
