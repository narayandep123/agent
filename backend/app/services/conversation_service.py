"""Durable, strictly user-scoped assistant conversation storage."""
import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db_models import ChatMessage, Conversation


def create(db: Session, user_id: int) -> Conversation:
    row = Conversation(id=f"CHAT-{uuid4().hex[:12]}", user_id=user_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def owned(db: Session, conversation_id: str, user_id: int) -> Conversation | None:
    return db.query(Conversation).filter(Conversation.id == conversation_id, Conversation.user_id == user_id).first()


def list_for(db: Session, user_id: int) -> list[Conversation]:
    return db.query(Conversation).filter(Conversation.user_id == user_id).order_by(Conversation.updated_at.desc()).all()


def messages(db: Session, conversation_id: str) -> list[ChatMessage]:
    return db.query(ChatMessage).filter(ChatMessage.conversation_id == conversation_id).order_by(ChatMessage.id).all()


def add_message(db: Session, conversation: Conversation, role: str, text: str, payload: dict | None) -> ChatMessage:
    if role not in {"user", "assistant", "decision"}:
        raise ValueError("Invalid chat message role.")
    row = ChatMessage(conversation_id=conversation.id, role=role, text=text[:10000], payload=json.dumps(payload) if payload else "")
    db.add(row)
    if role == "user" and conversation.title == "New conversation":
        clean = " ".join(text.split())
        conversation.title = clean[:55] + ("…" if len(clean) > 55 else "")
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


def serialize_message(row: ChatMessage) -> dict:
    try:
        payload = json.loads(row.payload) if row.payload else None
    except json.JSONDecodeError:
        payload = None
    return {"id": row.id, "role": row.role, "text": row.text, "payload": payload, "created_at": row.created_at.isoformat()}
