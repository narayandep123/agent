def test_request_endpoint_returns_decision_card(client, student_headers):
    response = client.post("/api/v1/requests", json={"text": "Classroom 204 AC is not working"}, headers=student_headers)
    assert response.status_code == 200
    assert response.json()["decision"] == "ACT"


def test_request_endpoint_requires_authentication(client):
    response = client.post("/api/v1/requests", json={"text": "Classroom 204 AC is not working"})
    assert response.status_code == 401


def test_major_action_sends_notification(client, student_headers):
    client.post("/api/v1/requests", json={"text": "Classroom 204 AC is not working"}, headers=student_headers)
    inbox = client.get("/api/v1/notifications", headers=student_headers).json()
    assert any("Maintenance ticket logged" in n["subject"] for n in inbox)


def test_notifications_require_authentication(client):
    assert client.get("/api/v1/notifications").status_code == 401


def test_policy_details_are_returned_and_topic_persists(client, student_headers):
    # Asking for a specific policy's details returns the full sections...
    first = client.post("/api/v1/assistant", json={"text": "Can I know the policy details for bonafide certificate?"}, headers=student_headers).json()
    assert "Documents Required" in first["message"]
    # ...and a vague follow-up stays on the same policy instead of drifting to a generic one.
    second = client.post("/api/v1/assistant", json={"text": "details about policy?"}, headers=student_headers).json()
    assert "bonafide" in second["message"].lower()
    assert "Approval Flow" in second["message"]


def test_certificate_intake_waits_for_document_and_remembers_context(client, student_headers):
    first = client.post("/api/v1/assistant", json={"text": "I need a bonafide certificate"}, headers=student_headers).json()
    assert first["type"] == "message"
    assert first["action"]["type"] == "OPEN_DOCUMENT_VERIFIER"
    assert "Before I route anything" in first["message"]

    second = client.post("/api/v1/assistant", json={"text": "what documents are needed?"}, headers=student_headers).json()
    assert second["type"] == "message"
    assert "student ID or marksheet" in second["message"]
    assert second["action"]["type"] == "OPEN_DOCUMENT_VERIFIER"


def test_certificate_intake_releases_context_when_user_switches_policy(client, student_headers):
    client.post("/api/v1/assistant", json={"text": "I need a bonafide certificate"}, headers=student_headers)
    switched = client.post("/api/v1/assistant", json={"text": "What is the complaint policy?"}, headers=student_headers).json()
    assert switched["type"] == "message"
    assert "grievance" in switched["message"].lower() or "complaint" in switched["message"].lower()
    assert "bonafide" not in switched["message"].lower()


def test_policy_question_during_certificate_intake_is_answered_by_rag(client, admin_headers):
    client.post("/api/v1/assistant", json={"text": "I need a bonafide certificate"}, headers=admin_headers)
    response = client.post("/api/v1/assistant", json={"text": "What does the bonafide certificate policy say?"}, headers=admin_headers).json()
    assert response["type"] == "message"
    assert "valid student ID" in response["message"]
    assert "signed in as Admin" not in response["message"]


def test_unrelated_flight_request_releases_certificate_workflow(client, student_headers):
    client.post("/api/v1/assistant", json={"text": "I want a bonafide certificate"}, headers=student_headers)
    response = client.post("/api/v1/assistant", json={"text": "I want to book a flight?"}, headers=student_headers).json()
    assert response["type"] == "message"
    assert "outside CampusFlow's scope" in response["message"]
    assert "bonafide" not in response["message"].lower()
    assert "knowledge_gap" not in response
