"""Authentication dependencies for protected endpoints."""
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.auth.security import decode_token
from app.db import get_db
from app.db_models import User


def get_current_user(authorization: str = Header(None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Not authenticated")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except Exception:  # noqa: BLE001 - any decode failure means "unauthenticated"
        raise HTTPException(401, "Invalid or expired session. Please sign in again.")
    user = db.get(User, int(payload.get("sub", 0)))
    if not user:
        raise HTTPException(401, "Account no longer exists.")
    if user.status != "ACTIVE":
        raise HTTPException(403, "Your account is not active.")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("ADMIN", "APPROVER"):
        raise HTTPException(403, "Administrator privileges are required for this action.")
    return user
