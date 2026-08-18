from dataclasses import dataclass

from app.rag.retriever import retrieve_policy

# Below this retrieval confidence the evidence is treated as too weak to rely on
# and the request should be flagged for human review rather than acted on.
UNCERTAINTY_THRESHOLD = 0.75

CONFLICT_TERMS = ("bypass", "fake", "forged", "without permission", "without approval")

@dataclass(frozen=True)
class PolicyResult:
    found: bool
    policy_id: str
    name: str
    version: str
    confidence: float
    conflict: bool
    reason: str
    source_section: str = ""
    citation: str = ""
    uncertain: bool = False

def validate(intent: str, text: str) -> PolicyResult:
    if any(x in text.lower() for x in CONFLICT_TERMS):
        return PolicyResult(True, "INST-CONDUCT-001", "Institutional Conduct Policy", "3.2", .98, True, "Request conflicts with institutional policy.", source_section="Prohibited Conduct", citation="Requests to forge, fake, or bypass approvals are strictly prohibited and will be stopped.")
    match = retrieve_policy(intent, text)
    if not match:
        return PolicyResult(False, "", "No verified policy", "", 0, False, "No sufficient official policy evidence was found.")
    uncertain = match.confidence < UNCERTAINTY_THRESHOLD
    reason = (
        "Retrieved policy evidence is weak; human review is recommended."
        if uncertain else "Verified policy evidence is applicable."
    )
    return PolicyResult(True, match.policy_id, match.name, match.version, match.confidence, False, reason, source_section=match.section, citation=match.snippet, uncertain=uncertain)
