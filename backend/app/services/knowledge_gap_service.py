"""Knowledge-gap intake and administrator policy publication."""
from datetime import datetime, timezone
from pathlib import Path
import re
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db_models import KnowledgeGap
from app.rag import retriever
from app.services.audit_service import record


def _key(question: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", question.lower())
    return " ".join(sorted(set(tokens)))[:500]


def raise_gap(db: Session, question: str, requested_by: str) -> KnowledgeGap:
    key = _key(question)
    existing = db.query(KnowledgeGap).filter(KnowledgeGap.normalized_key == key, KnowledgeGap.status == "OPEN").first()
    if existing:
        existing.occurrences += 1
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing
    gap = KnowledgeGap(id=f"KG-{uuid4().hex[:8].upper()}", normalized_key=key,
                       question=question.strip(), requested_by=requested_by)
    db.add(gap)
    db.commit()
    db.refresh(gap)
    record(gap.id, requested_by, "KNOWLEDGE_GAP", "OPENED", "No grounded policy", "LOW")
    return gap


def list_gaps(db: Session) -> list[KnowledgeGap]:
    return db.query(KnowledgeGap).order_by(KnowledgeGap.status.asc(), KnowledgeGap.updated_at.desc()).all()


def resolve(db: Session, gap: KnowledgeGap, title: str, version: str, content: str, admin_email: str) -> str:
    if gap.status != "OPEN":
        raise ValueError("This knowledge gap has already been resolved.")
    policy_id = f"KB-{gap.id[3:]}"
    safe_content = content.strip()
    document = (
        f"id: {policy_id}\nname: {title.strip()}\nversion: {version.strip()}\n"
        f"effective_date: {datetime.now(timezone.utc).date().isoformat()}\nintents: GENERAL\n"
        f"keywords: {gap.question}\nanswer: {safe_content[:800].replace(chr(10), ' ')}\n\n"
        f"## Policy Information\n{safe_content}\n"
    )
    path: Path = retriever.DOCUMENTS_DIR / f"managed_{gap.id.lower()}.md"
    path.write_text(document, encoding="utf-8")
    retriever.reload_corpus()
    gap.status = "RESOLVED"
    gap.policy_id = policy_id
    gap.updated_at = datetime.now(timezone.utc)
    db.commit()
    record(gap.id, admin_email, "POLICY_PUBLISHED", "RESOLVED", f"{title} v{version}", "LOW")
    return policy_id
