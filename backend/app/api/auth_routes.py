"""Authentication and administrator user-management endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_admin
from app.auth.security import create_token, hash_password, verify_password
from app.db import get_db
from app.db_models import ChatMessage, Conversation, EmailVerification, KnowledgeGap, User
from app.schemas.auth import (AccessInput, EmailResendInput, EmailVerificationInput, LoginInput,
                              SignupInput, TokenOut, UserDecisionInput, UserOut)
from app.services import audit_service
from app.services import email_verification_service
from app.services import maintenance_attachment_service, notification_service, request_service
from app.services.booking_service import BOOKINGS
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
    status = "EMAIL_PENDING"
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
    email_verification_service.issue(db, user)
    audit_service.record(f"USER-{user.id}", user.email, "SIGNUP", status, f"Role {role}; email verification required", "LOW")
    return {
        "pending": True, "verification_required": True, "email": user.email,
        "message": "We sent a six-digit verification code to your email address.",
    }


@router.post("/verify-email")
def verify_email(payload: EmailVerificationInput, db: Session = Depends(get_db)):
    email = str(payload.email).strip().lower()
    user = db.query(User).filter(func.lower(User.email) == email).first()
    if not user or user.status != "EMAIL_PENDING":
        raise HTTPException(400, "This email does not have a pending verification.")
    try:
        status = email_verification_service.verify(db, user, payload.code)
    except ValueError as error:
        raise HTTPException(400, str(error))
    audit_service.record(f"USER-{user.id}", user.email, "EMAIL_VERIFICATION", "VERIFIED", f"Role {user.role}", "LOW")
    if status == "ACTIVE":
        return {
            "verified": True, "pending_approval": False,
            "access_token": create_token(user), "token_type": "bearer",
            "user": UserOut.model_validate(user).model_dump(),
            "message": "Email verified. Your account is ready.",
        }
    notify(user.email, "CampusFlow enrolment request received",
           f"Hi {user.name},\n\nYour email is verified. Your {user.role.title()} account now requires "
           "administrator approval before sign-in.\n\n— CampusFlow AI")
    return {
        "verified": True, "pending_approval": True,
        "message": "Email verified. Your account is awaiting administrator approval.",
    }


@router.post("/resend-verification")
def resend_verification(payload: EmailResendInput, db: Session = Depends(get_db)):
    email = str(payload.email).strip().lower()
    user = db.query(User).filter(func.lower(User.email) == email).first()
    if user and user.status == "EMAIL_PENDING":
        email_verification_service.issue(db, user)
        audit_service.record(f"USER-{user.id}", user.email, "EMAIL_VERIFICATION", "RESENT", "New code issued", "LOW")
    # Deliberately generic so this endpoint cannot be used for account discovery.
    return {"message": "If this email has a pending verification, a new code has been sent."}

@router.post("/login", response_model=TokenOut)
def login(payload: LoginInput, db: Session = Depends(get_db)):
    user = db.query(User).filter(func.lower(User.email) == str(payload.email).strip().lower()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password.")
    if user.status != "ACTIVE":
        messages = {
            "EMAIL_PENDING": "Please verify your email address before signing in.",
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
    if user.status in {"EMAIL_PENDING", "PENDING"}:
        raise HTTPException(409, "This account is still awaiting verification or approval and cannot be changed here.")
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


@admin_router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Permanently remove an account and its user-owned operational data."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(400, "You cannot delete your own administrator account.")

    email = user.email
    conversations = db.query(Conversation).filter(Conversation.user_id == user.id).all()
    conversation_ids = [row.id for row in conversations]
    if conversation_ids:
        db.query(ChatMessage).filter(ChatMessage.conversation_id.in_(conversation_ids)).delete(synchronize_session=False)
        db.query(Conversation).filter(Conversation.id.in_(conversation_ids)).delete(synchronize_session=False)
    db.query(EmailVerification).filter(EmailVerification.user_id == user.id).delete(synchronize_session=False)
    db.query(KnowledgeGap).filter(KnowledgeGap.requested_by == email).update(
        {KnowledgeGap.requested_by: f"deleted-user-{user.id}"}, synchronize_session=False,
    )

    request_ids = [rid for rid, request in request_service.REQUESTS.items() if request.user_id == email]
    for request_id in request_ids:
        maintenance_attachment_service.delete_for_request(request_id)
        request_service.REQUESTS.pop(request_id, None)
    for key, owner in list(BOOKINGS.items()):
        if owner == email:
            BOOKINGS.pop(key, None)
    notification_service.OUTBOX[:] = [item for item in notification_service.OUTBOX if item.get("to") != email]

    db.delete(user)
    db.commit()
    audit_service.record(f"USER-{user_id}", admin.email, "USER_DELETE", "DELETED", "RBAC administrator action", "HIGH")
    return {
        "deleted": True, "user_id": user_id,
        "message": "User deleted and account access removed immediately.",
    }
