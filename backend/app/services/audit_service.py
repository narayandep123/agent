from datetime import datetime, timezone
AUDIT_LOG: list[dict] = []
def record(request_id: str, user: str, action: str, result: str, policy: str, risk: str) -> str:
    audit_id = f"AUD-{len(AUDIT_LOG)+1:04d}"
    AUDIT_LOG.insert(0, {"id": audit_id, "timestamp": datetime.now(timezone.utc).isoformat(), "user": user, "request_id": request_id, "action": action, "result": result, "policy": policy, "risk": risk})
    return audit_id
def all_events(): return AUDIT_LOG
