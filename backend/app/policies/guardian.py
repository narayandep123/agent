from dataclasses import dataclass

@dataclass(frozen=True)
class PolicyResult:
    found: bool
    policy_id: str
    name: str
    version: str
    confidence: float
    conflict: bool
    reason: str

POLICIES = {
 "MAINTENANCE": ("FAC-MNT-001", "Facilities Maintenance Policy", "1.4"),
 "LAB_BOOKING": ("LIB-BOOK-002", "Library Seat Booking Policy", "2.1"),
 "CERTIFICATE": ("ACA-CERT-003", "Academic Certificate Policy", "3.0"),
}

def validate(intent: str, text: str) -> PolicyResult:
    if any(x in text.lower() for x in ("bypass", "fake", "forged", "without permission", "without approval")):
        return PolicyResult(True, "INST-CONDUCT-001", "Institutional Conduct Policy", "3.2", .98, True, "Request conflicts with institutional policy.")
    policy = POLICIES.get(intent)
    if not policy:
        return PolicyResult(False, "", "No verified policy", "", 0, False, "No sufficient official policy evidence was found.")
    return PolicyResult(True, policy[0], policy[1], policy[2], .94, False, "Verified policy evidence is applicable.")
