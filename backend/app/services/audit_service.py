"""Tamper-evident audit trail.

Every governance event is appended to a hash chain: each record stores the hash
of the previous record plus a SHA-256 over its own contents. Altering any past
record breaks the chain, which ``verify`` detects. This makes the action trail
cryptographically verifiable, not just a mutable list.
"""
import hashlib
import json
from datetime import datetime, timezone

AUDIT_LOG: list[dict] = []
GENESIS_HASH = "0" * 64
_last_hash = GENESIS_HASH

_HASHED_FIELDS = ("id", "timestamp", "user", "request_id", "action", "result", "policy", "risk")


def _digest(entry: dict, prev_hash: str) -> str:
    payload = {key: entry.get(key) for key in _HASHED_FIELDS}
    payload["prev_hash"] = prev_hash
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def record(request_id: str, user: str, action: str, result: str, policy: str, risk: str) -> str:
    global _last_hash
    audit_id = f"AUD-{len(AUDIT_LOG)+1:04d}"
    entry = {
        "id": audit_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user": user,
        "request_id": request_id,
        "action": action,
        "result": result,
        "policy": policy,
        "risk": risk,
        "prev_hash": _last_hash,
    }
    entry["hash"] = _digest(entry, _last_hash)
    _last_hash = entry["hash"]
    AUDIT_LOG.insert(0, entry)
    return audit_id


def all_events() -> list[dict]:
    return AUDIT_LOG


def verify() -> dict:
    """Re-walk the chain in chronological order and confirm it is intact."""
    prev = GENESIS_HASH
    for entry in reversed(AUDIT_LOG):
        if entry.get("prev_hash") != prev or entry.get("hash") != _digest(entry, prev):
            return {"valid": False, "broken_at": entry.get("id"), "count": len(AUDIT_LOG)}
        prev = entry["hash"]
    return {"valid": True, "count": len(AUDIT_LOG)}
