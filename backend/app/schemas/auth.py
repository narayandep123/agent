"""Auth & user-management request/response schemas."""
from pydantic import BaseModel, EmailStr, Field


class SignupInput(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    roll_no: str = Field(default="", max_length=40)
    email: EmailStr
    mobile: str = Field(min_length=7, max_length=15)
    role: str = "STUDENT"
    password: str = Field(min_length=6, max_length=100)


class LoginInput(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    name: str
    roll_no: str
    email: EmailStr
    mobile: str
    role: str
    status: str
    comment: str = ""

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserDecisionInput(BaseModel):
    approved: bool
    comment: str = ""


class AccessInput(BaseModel):
    active: bool
    comment: str = ""
