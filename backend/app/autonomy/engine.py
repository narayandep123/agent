from app.models.domain import Decision
def decide(intent: str, policy_found: bool, conflict: bool, authorized: bool, risk: str, uncertain: bool = False) -> tuple[Decision, str]:
    if not policy_found: return Decision.STOP, "No verified institutional policy supports this action."
    if conflict: return Decision.STOP, "Policy conflict detected. No action was executed."
    if not authorized: return Decision.STOP, "Your role is not authorized for this action."
    if intent == "GRIEVANCE": return Decision.ESCALATE, "Your grievance has been logged and escalated to a human officer, who will review the details and follow up."
    if uncertain: return Decision.ESCALATE, "Policy evidence is weak or ambiguous. Escalating to a human reviewer instead of acting."
    if intent == "MAINTENANCE" and risk == "LOW": return Decision.ACT, "Low-risk maintenance request is authorized and was submitted."
    if intent == "LAB_BOOKING": return Decision.ASK, "Availability and permissions are verified; confirmation is required before booking."
    if intent == "CERTIFICATE": return Decision.APPROVE, "Certificate requests require authorized human approval."
    return Decision.STOP, "The request cannot be safely handled."
