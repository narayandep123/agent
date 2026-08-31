"""Validated, access-controlled image evidence for maintenance tickets."""
from __future__ import annotations

import io
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

UPLOAD_ROOT = Path(__file__).resolve().parents[2] / "uploads" / "maintenance"
ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENTS = 5
_FILES: dict[tuple[str, str], Path] = {}


def save(request, content: bytes, content_type: str, original_name: str) -> dict:
    if request.intent != "MAINTENANCE":
        raise ValueError("Photo evidence can only be added to maintenance tickets.")
    current = request.entities.setdefault("attachments", [])
    if len(current) >= MAX_ATTACHMENTS:
        raise ValueError(f"A maintenance ticket can have at most {MAX_ATTACHMENTS} photos.")
    if content_type not in ALLOWED_TYPES:
        raise ValueError("Upload a JPEG, PNG, or WebP image.")
    if not content:
        raise ValueError("The uploaded image is empty.")
    if len(content) > MAX_BYTES:
        raise ValueError("Each maintenance photo must be 8 MB or smaller.")
    try:
        image = Image.open(io.BytesIO(content))
        image.verify()
    except (UnidentifiedImageError, OSError):
        raise ValueError("The upload is not a readable image.")
    attachment_id = f"IMG-{uuid4().hex[:10].upper()}"
    safe_original = Path(original_name or "maintenance-photo").name[:120]
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_ROOT / f"{request.id}-{attachment_id}{ALLOWED_TYPES[content_type]}"
    path.write_bytes(content)
    _FILES[(request.id, attachment_id)] = path
    metadata = {
        "id": attachment_id,
        "filename": safe_original,
        "content_type": content_type,
        "size": len(content),
    }
    current.append(metadata)
    return metadata


def locate(request_id: str, attachment_id: str) -> Path | None:
    path = _FILES.get((request_id, attachment_id))
    return path if path and path.is_file() else None


def delete_for_request(request_id: str) -> int:
    """Remove all stored evidence belonging to one deleted request."""
    removed = 0
    for key, path in list(_FILES.items()):
        if key[0] != request_id:
            continue
        if path.is_file():
            path.unlink()
            removed += 1
        _FILES.pop(key, None)
    # Also cover files restored from disk after a process restart.
    if UPLOAD_ROOT.is_dir():
        for path in UPLOAD_ROOT.glob(f"{request_id}-IMG-*"):
            if path.is_file():
                path.unlink()
                removed += 1
    return removed
