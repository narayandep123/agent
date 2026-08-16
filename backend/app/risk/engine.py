RISK_BY_INTENT = {"MAINTENANCE": "LOW", "LAB_BOOKING": "LOW", "CERTIFICATE": "MEDIUM", "UNSUPPORTED": "HIGH"}
def assess(intent: str, conflict: bool, authorized: bool) -> str:
    if conflict or not authorized: return "CRITICAL" if not authorized else "HIGH"
    return RISK_BY_INTENT.get(intent, "HIGH")
