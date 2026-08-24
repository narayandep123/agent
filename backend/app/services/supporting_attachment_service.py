"""Private supporting documents retained for requester/admin review."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

UPLOAD_ROOT = Path(__file__).resolve().parents[2] / "uploads" / "supporting"
ALLOWED_TYPES = {
    "application/pdf": ".pdf", "image/jpeg": ".jpg",
    "image/png": ".png", "image/webp": ".webp",
}


def save(request, content: bytes, content_type: str, original_name: str, category: str) -> dict:
    if content_type not in ALLOWED_TYPES:
        raise ValueError("Unsupported supporting-document type.")
    attachment_id = f"DOC-{uuid4().hex[:10].upper()}"
    safe_name = Path(original_name or "supporting-document").name[:120]
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_ROOT / f"{request.id}-{attachment_id}{ALLOWED_TYPES[content_type]}"
    path.write_bytes(content)
    metadata = {
        "id": attachment_id, "filename": safe_name, "content_type": content_type,
        "size": len(content), "category": category,
    }
    request.entities.setdefault("supporting_documents", []).append(metadata)
    return metadata


def locate(request_id: str, attachment_id: str) -> Path | None:
    if not attachment_id.startswith("DOC-"):
        return None
    matches = list(UPLOAD_ROOT.glob(f"{request_id}-{attachment_id}.*")) if UPLOAD_ROOT.is_dir() else []
    return matches[0] if len(matches) == 1 and matches[0].is_file() else None


def delete_for_request(request_id: str) -> int:
    removed = 0
    if UPLOAD_ROOT.is_dir():
        for path in UPLOAD_ROOT.glob(f"{request_id}-DOC-*.*"):
            if path.is_file():
                path.unlink()
                removed += 1
    return removed
