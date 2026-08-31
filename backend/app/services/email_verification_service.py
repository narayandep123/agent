"""One-time email verification codes for new accounts."""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db_models import EmailVerification, User
from app.services.notification_service import notify

TTL_MINUTES = int(os.getenv("EMAIL_VERIFICATION_TTL_MINUTES", "10"))
MAX_ATTEMPTS = int(os.getenv("EMAIL_VERIFICATION_MAX_ATTEMPTS", "5"))
SECRET = os.getenv("CAMPUSFLOW_SECRET", "dev-secret-change-me-in-production")


def _digest(user_id: int, code: str) -> str:
    return hmac.new(SECRET.encode(), f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def issue(db: Session, user: User) -> None:
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = db.query(EmailVerification).filter(EmailVerification.user_id == user.id).first()
    if not challenge:
        challenge = EmailVerification(user_id=user.id, code_hash="", expires_at=datetime.now(timezone.utc))
        db.add(challenge)
    challenge.code_hash = _digest(user.id, code)
    challenge.expires_at = datetime.now(timezone.utc) + timedelta(minutes=TTL_MINUTES)
    challenge.attempts = 0
    db.commit()
    notify(
        user.email,
        "Verify your CampusFlow email",
        f"Hi {user.name},\n\nYour CampusFlow verification code is: {code}\n\n"
        f"This code expires in {TTL_MINUTES} minutes and can be used only once. "
        "If you did not create this account, you can ignore this email.\n\n— CampusFlow AI",
    )


def verify(db: Session, user: User, code: str) -> str:
    challenge = db.query(EmailVerification).filter(EmailVerification.user_id == user.id).first()
    if not challenge:
        raise ValueError("No active verification code was found. Request a new code.")
    expires_at = challenge.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expires_at:
        raise ValueError("This verification code has expired. Request a new code.")
    if challenge.attempts >= MAX_ATTEMPTS:
        raise ValueError("Too many incorrect attempts. Request a new code.")
    if not hmac.compare_digest(challenge.code_hash, _digest(user.id, code)):
        challenge.attempts += 1
        db.commit()
        remaining = max(0, MAX_ATTEMPTS - challenge.attempts)
        raise ValueError(f"Invalid verification code. {remaining} attempt(s) remaining.")

    next_status = "ACTIVE" if user.role == "STUDENT" else "PENDING"
    user.status = next_status
    db.delete(challenge)
    db.commit()
    db.refresh(user)
    return next_status
