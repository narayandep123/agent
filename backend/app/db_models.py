"""Persistent database models."""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.db import Base


class User(Base):
    """An application account.

    ``status`` gates access: students self-enroll (``ACTIVE`` immediately) while
    faculty/staff/admin accounts start ``PENDING`` and must be approved by an
    administrator before they can sign in.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    roll_no = Column(String, default="")
    email = Column(String, unique=True, nullable=False, index=True)
    mobile = Column(String, default="")
    role = Column(String, default="STUDENT")
    status = Column(String, default="ACTIVE")  # ACTIVE | PENDING | REJECTED
    comment = Column(String, default="")  # admin note on an approval/rejection
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Conversation(Base):
    """A durable, user-owned assistant thread."""

    __tablename__ = "conversations"
    id = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, default="New conversation")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class ChatMessage(Base):
    """A rendered chat turn; structured cards are stored as JSON in ``payload``."""

    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    role = Column(String, nullable=False)  # user | assistant | decision
    text = Column(Text, default="")
    payload = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class KnowledgeGap(Base):
    """A deduplicated request for institutional knowledge missing from the corpus."""

    __tablename__ = "knowledge_gaps"
    id = Column(String, primary_key=True)
    normalized_key = Column(String, unique=True, nullable=False, index=True)
    question = Column(Text, nullable=False)
    requested_by = Column(String, nullable=False)
    status = Column(String, default="OPEN", index=True)  # OPEN | RESOLVED
    occurrences = Column(Integer, default=1)
    policy_id = Column(String, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
