"""Password hashing (bcrypt) and JWT issuing/verification."""
import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

SECRET_KEY = os.getenv("CAMPUSFLOW_SECRET", "dev-secret-change-me-in-production")
ALGORITHM = "HS256"
TOKEN_TTL_HOURS = 12


def hash_password(password: str) -> str:
    # bcrypt operates on at most 72 bytes; truncate defensively.
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], password_hash.encode())
    except (ValueError, TypeError):
        return False


def create_token(user) -> str:
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
