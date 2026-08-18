"""Authentication and administrator user-management endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_admin
from app.auth.security import create_token, hash_password, verify_password
from app.db import get_db
from app.db_models import User
from app.schemas.auth import AccessInput, LoginInput, SignupInput, TokenOut, UserDecisionInput, UserOut
from app.services import audit_service
from app.services.notification_service import notify

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

VALID_ROLES = {"STUDENT", "FACULTY", "STAFF", "ADMIN"}
# Roles a person can enrol into without administrator sign-off.
SELF_ENROLL_ROLES = {"STUDENT"}


@router.post("/signup")
def signup(payload: SignupInput, db: Session = Depends(get_db)):
    role = payload.role.upper()
    email = str(payload.email).strip().lower()
    roll_no = payload.roll_no.strip().upper()
    if role not in VALID_ROLES:
        raise HTTPException(400, "Please choose a valid role.")
    if db.query(User).filter(func.lower(User.email) == email).first():
        raise HTTPException(409, "An account with this email already exists.")
    if roll_no and db.query(User).filter(func.upper(User.roll_no) == roll_no).first():
        raise HTTPException(409, "An account with this roll / employee number already exists.")
    status = "ACTIVE" if role in SELF_ENROLL_ROLES else "PENDING"
    user = User(
        name=payload.name,
        roll_no=roll_no,
        email=email,
        mobile=payload.mobile,
        role=role,
        status=status,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    audit_service.record(f"USER-{user.id}", user.email, "SIGNUP", status, f"Role {role}", "LOW")
    if status == "ACTIVE":
        return {
            "pending": False,
            "access_token": create_token(user),
            "token_type": "bearer",
            "user": UserOut.model_validate(user).model_dump(),
        }
    notify(user.email, "CampusFlow enrolment request received",
           f"Hi {user.name},\n\nWe've received your request to join CampusFlow as {role.title()}. "
           f"An administrator will review it shortly and you'll be notified once you're enrolled.\n\n— CampusFlow AI")
    return {
        "pending": True,
        "message": "Your account requires administrator approval. You'll be able to sign in once an admin enrols you.",
    }

@router.post("/login", response_model=TokenOut)
def login(payload: LoginInput, db: Session = Depends(get_db)):
    user = db.query(User).filter(func.lower(User.email) == str(payload.email).strip().lower()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password.")
    if user.status != "ACTIVE":
        messages = {
            "PENDING": "Your account is awaiting administrator approval.",
            "REJECTED": "Your enrolment request was declined. Please contact the administrator.",
            "REVOKED": "Your access has been revoked by an administrator. Please contact the administrator.",
        }
        raise HTTPException(403, messages.get(user.status, "Your account is not active."))
    return TokenOut(access_token=create_token(user), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)


@admin_router.get("/users")
def list_users(status: str | None = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    query = db.query(User)
    if status:
        query = query.filter(User.status == status.upper())
    users = query.order_by(User.created_at.desc()).all()
    return [UserOut.model_validate(u).model_dump() for u in users]


@admin_router.post("/users/{user_id}/decision", response_model=UserOut)
def decide_user(user_id: int, payload: UserDecisionInput, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.status != "PENDING":
        raise HTTPException(409, "This account is not awaiting approval.")
    user.status = "ACTIVE" if payload.approved else "REJECTED"
    user.comment = payload.comment
    db.commit()
    db.refresh(user)
    audit_service.record(
        f"USER-{user.id}", admin.email, "USER_REVIEW",
        "APPROVED" if payload.approved else "REJECTED", f"Enrol {user.role}", "LOW",
    )
    if payload.approved:
        notify(user.email, "Your CampusFlow account is approved",
               f"Hi {user.name},\n\nYour {user.role.title()} account has been approved by an administrator. "
               f"You can now sign in to CampusFlow.\n\n" + (f"Note: {payload.comment}\n\n" if payload.comment else "") + "— CampusFlow AI")
    else:
        notify(user.email, "Your CampusFlow enrolment was declined",
               f"Hi {user.name},\n\nYour enrolment request was not approved.\n\n" + (f"Reason: {payload.comment}\n\n" if payload.comment else "") + "Please contact the administrator for details.\n\n— CampusFlow AI")
    return UserOut.model_validate(user)


@admin_router.post("/users/{user_id}/access", response_model=UserOut)
def set_access(user_id: int, payload: AccessInput, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Revoke or restore an active account's access."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(400, "You cannot change your own access.")
    if user.status == "PENDING":
        raise HTTPException(409, "This account is still awaiting approval. Use approve or reject instead.")
    user.status = "ACTIVE" if payload.active else "REVOKED"
    user.comment = payload.comment
    db.commit()
    db.refresh(user)
    audit_service.record(
        f"USER-{user.id}", admin.email, "ACCESS_CHANGE",
        "RESTORED" if payload.active else "REVOKED", f"Role {user.role}", "MEDIUM",
    )
    if payload.active:
        notify(user.email, "Your CampusFlow access has been restored",
               f"Hi {user.name},\n\nYour access to CampusFlow has been restored. You can sign in again.\n\n" + (f"Note: {payload.comment}\n\n" if payload.comment else "") + "— CampusFlow AI")
    else:
        notify(user.email, "Your CampusFlow access has been revoked",
               f"Hi {user.name},\n\nYour access to CampusFlow has been revoked by an administrator.\n\n" + (f"Reason: {payload.comment}\n\n" if payload.comment else "") + "Please contact the administrator if you believe this is a mistake.\n\n— CampusFlow AI")
    return UserOut.model_validate(user)
