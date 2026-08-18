from app.services import audit_service


def _grievance(client, headers, text):
    return client.post("/api/v1/assistant", json={"text": text}, headers=headers).json()["decision"]


def test_grievance_escalates_to_human(client, student_headers):
    body = _grievance(client, student_headers, "I want to file a grievance about unfair treatment by a staff member")
    assert body["intent"] == "GRIEVANCE"
    assert body["status"] == "ESCALATED"
    assert body["decision"] == "ESCALATE"


def test_admin_can_approve_escalated_request(client, student_headers, admin_headers):
    created = _grievance(client, student_headers, "I want to file a grievance about ragging in the hostel")
    reviewed = client.post(f"/api/v1/approvals/{created['request_id']}", json={"approved": True, "comment": "Assigned to warden."}, headers=admin_headers)
    assert reviewed.status_code == 200
    body = reviewed.json()
    assert body["status"] == "EXECUTED"
    assert body["decision"] == "ACT"


def test_admin_can_reject_request(client, student_headers, admin_headers):
    created = _grievance(client, student_headers, "I want to file a grievance about a billing dispute")
    reviewed = client.post(f"/api/v1/approvals/{created['request_id']}", json={"approved": False}, headers=admin_headers)
    assert reviewed.json()["status"] == "STOPPED"


def test_review_requires_admin(client, student_headers):
    created = _grievance(client, student_headers, "I want to file a grievance about a hostel issue")
    reviewed = client.post(f"/api/v1/approvals/{created['request_id']}", json={"approved": True}, headers=student_headers)
    assert reviewed.status_code == 403


def test_review_on_unknown_request_is_404(client, admin_headers):
    r = client.post("/api/v1/approvals/does-not-exist", json={"approved": True}, headers=admin_headers)
    assert r.status_code == 404


def test_audit_chain_is_verifiable(client, admin_headers):
    client.post("/api/v1/requests", json={"text": "Classroom 204 AC is not working"}, headers=admin_headers)
    assert client.get("/api/v1/audit/verify", headers=admin_headers).json()["valid"] is True


def test_audit_requires_admin(client, student_headers):
    assert client.get("/api/v1/audit/verify", headers=student_headers).status_code == 403


def test_audit_chain_detects_tampering(client, admin_headers):
    client.post("/api/v1/requests", json={"text": "Classroom 204 AC is not working"}, headers=admin_headers)
    assert audit_service.AUDIT_LOG, "expected at least one audit event"
    original = audit_service.AUDIT_LOG[-1]["result"]
    audit_service.AUDIT_LOG[-1]["result"] = "TAMPERED"
    try:
        assert client.get("/api/v1/audit/verify", headers=admin_headers).json()["valid"] is False
    finally:
        audit_service.AUDIT_LOG[-1]["result"] = original
