from typing import Any
from pydantic import BaseModel, Field
from app.models.domain import Role

class RequestInput(BaseModel):
    text: str = Field(min_length=3, max_length=1000)
    role: Role = Role.STUDENT
    language: str = "auto"

class ConfirmationInput(BaseModel):
    confirmed: bool

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
