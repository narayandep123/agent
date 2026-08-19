import io
import json
import uuid

from PIL import Image


def test_assistant_stream_sends_progress_before_validated_result(client, student_headers):
    with client.stream(
        "POST", "/api/v1/assistant/stream",
        json={"text": "What is the bonafide certificate policy?"},
        headers=student_headers,
    ) as response:
        events = [json.loads(line) for line in response.iter_lines() if line]
    assert response.status_code == 200
    assert events[0]["type"] == "status"
    assert events[-1]["type"] == "result"
    assert events[-1]["data"]["type"] == "message"


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


def test_maintenance_ticket_is_visible_to_student_and_tracks_admin_updates(client, student_headers, admin_headers):
    conversation = client.post("/api/v1/conversations", json={}, headers=student_headers).json()
    created = client.post("/api/v1/assistant", json={
        "text": "The AC is broken in classroom 204 on the second floor",
        "conversation_id": conversation["id"],
    }, headers=student_headers).json()
    assert created["type"] == "decision"
    ticket_id = created["decision"]["request_id"]
    assert created["decision"]["status"] == "OPEN"

    student_rows = client.get("/api/v1/requests", headers=student_headers).json()
    ticket = next(row for row in student_rows if row["id"] == ticket_id)
    assert ticket["status"] == "OPEN"

    assigned = client.post(f"/api/v1/maintenance/{ticket_id}/status", json={
        "status": "ASSIGNED", "assigned_to": "Facilities Team A", "comment": "Visit scheduled today.",
    }, headers=admin_headers)
    assert assigned.status_code == 200
    assert assigned.json()["status"] == "ASSIGNED"

    updated = next(row for row in client.get("/api/v1/requests", headers=student_headers).json() if row["id"] == ticket_id)
    assert updated["status"] == "ASSIGNED"
    assert updated["entities"]["assigned_to"] == "Facilities Team A"
    assert "Visit scheduled today" in updated["message"]
    inbox = client.get("/api/v1/notifications", headers=student_headers).json()
    assert any(ticket_id in item["subject"] and "Assigned" in item["subject"] for item in inbox)


def test_maintenance_photo_is_optional_validated_and_access_controlled(client, student_headers, admin_headers):
    created = client.post("/api/v1/requests", json={
        "text": "The AC is broken in classroom 204 on the second floor",
    }, headers=student_headers).json()
    ticket_id = created["request_id"]

    image_bytes = io.BytesIO()
    Image.new("RGB", (24, 24), "navy").save(image_bytes, format="PNG")
    uploaded = client.post(
        f"/api/v1/maintenance/{ticket_id}/attachments",
        files={"image": ("broken-ac.png", image_bytes.getvalue(), "image/png")},
        headers=student_headers,
    )
    assert uploaded.status_code == 200
    attachment = uploaded.json()["attachment"]
    assert attachment["filename"] == "broken-ac.png"

    owner_view = client.get(
        f"/api/v1/maintenance/{ticket_id}/attachments/{attachment['id']}",
        headers=student_headers,
    )
    assert owner_view.status_code == 200
    assert owner_view.headers["content-type"] == "image/png"
    assert client.get(
        f"/api/v1/maintenance/{ticket_id}/attachments/{attachment['id']}",
        headers=admin_headers,
    ).status_code == 200

    suffix = uuid.uuid4().hex[:8]
    other = client.post("/api/v1/auth/signup", json={
        "name": "Other Student", "roll_no": f"OT-{suffix}",
        "email": f"other-{suffix}@campus.edu", "mobile": "9876543210",
        "role": "STUDENT", "password": "secret123",
    }).json()
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    assert client.get(
        f"/api/v1/maintenance/{ticket_id}/attachments/{attachment['id']}",
        headers=other_headers,
    ).status_code == 403

    invalid = client.post(
        f"/api/v1/maintenance/{ticket_id}/attachments",
        files={"image": ("not-an-image.png", b"not really an image", "image/png")},
        headers=student_headers,
    )
    assert invalid.status_code == 400
    assert "readable image" in invalid.json()["detail"]


def test_notifications_require_authentication(client):
    assert client.get("/api/v1/notifications").status_code == 401


def test_notifications_remain_after_being_seen(client, student_headers):
    client.post("/api/v1/requests", json={
        "text": "Classroom 204 AC is not working",
    }, headers=student_headers)
    before = client.get("/api/v1/notifications", headers=student_headers).json()
    assert before and any(item["read_at"] is None for item in before)
    assert all(item.get("id") for item in before)

    marked = client.post("/api/v1/notifications/read", headers=student_headers)
    assert marked.status_code == 200
    assert len(marked.json()) == len(before)
    assert all(item["read_at"] for item in marked.json())

    after = client.get("/api/v1/notifications", headers=student_headers).json()
    assert {item["id"] for item in after} == {item["id"] for item in before}
    assert all(item["read_at"] for item in after)


def test_policy_details_are_returned_and_topic_persists(client, student_headers):
    # Asking for a specific policy's details returns the full sections...
    first = client.post("/api/v1/assistant", json={"text": "Can I know the policy details for bonafide certificate?"}, headers=student_headers).json()
    assert "Documents Required" in first["message"]
    # ...and a vague follow-up stays on the same policy instead of drifting to a generic one.
    second = client.post("/api/v1/assistant", json={"text": "details about policy?"}, headers=student_headers).json()
    assert "bonafide" in second["message"].lower()
    assert "Approval Flow" in second["message"]


def test_multiple_policy_topics_return_each_requested_document(client, student_headers):
    response = client.post("/api/v1/assistant", json={
        "text": "Tell me the scholarship, hostel, and maintenance policies?",
    }, headers=student_headers).json()
    assert response["type"] == "message"
    assert "Scholarship Eligibility Policy" in response["message"]
    assert "Hostel Accommodation Policy" in response["message"]
    assert "Facilities Maintenance Policy" in response["message"]
    assert len(response["sources"]) == 3


def test_compound_policy_and_complete_maintenance_request_executes_both(client, student_headers):
    response = client.post("/api/v1/assistant", json={
        "text": "Can you please tell me the hostel policy? and also I want to raise one complaint regarding the AC, room 123, building LH, ground floor.",
    }, headers=student_headers).json()
    assert response["type"] == "compound"
    assert len(response["outputs"]) == 2
    assert response["outputs"][0]["type"] == "message"
    assert response["outputs"][0]["sources"]
    assert "hostel" in response["outputs"][0]["message"].lower()
    assert response["outputs"][1]["type"] == "decision"
    decision = response["outputs"][1]["decision"]
    assert decision["intent"] == "MAINTENANCE"
    assert decision["status"] == "OPEN"
    assert decision["request_id"]
    assert decision["entities"]["floor"] == "0"


def test_harassment_near_hostel_stays_grievance_not_maintenance(client, student_headers):
    first = client.post("/api/v1/assistant", json={
        "text": "I want to raise a complaint against harassment.",
    }, headers=student_headers).json()
    assert first["type"] == "message"
    assert "confidential grievance" in first["message"]

    second = client.post("/api/v1/assistant", json={
        "text": "harassment complaint",
    }, headers=student_headers).json()
    assert second["type"] == "message"
    assert "describe what happened" in second["message"]

    third = client.post("/api/v1/assistant", json={
        "text": "I am being abused by some students near hostel-A.",
    }, headers=student_headers).json()
    assert third["type"] == "decision"
    assert third["decision"]["intent"] == "GRIEVANCE"
    assert third["decision"]["status"] == "ESCALATED"
    assert third["decision"]["entities"]["priority"] == "HIGH"


def test_teasing_report_after_hostel_policy_interrupts_rag_and_escalates(client, student_headers):
    policy = client.post("/api/v1/assistant", json={
        "text": "What is the hostel closing-time policy?",
    }, headers=student_headers).json()
    assert policy["type"] == "message"

    incident = client.post("/api/v1/assistant", json={
        "text": "boys are teasing me near hostel - B",
    }, headers=student_headers).json()
    assert incident["type"] == "decision"
    assert incident["decision"]["intent"] == "GRIEVANCE"
    assert incident["decision"]["status"] == "ESCALATED"
    assert incident["decision"]["entities"]["priority"] == "HIGH"
    assert "curfew" not in incident["decision"]["message"].lower()


def test_safety_grievance_interrupts_active_maintenance_flow(client, student_headers):
    waiting = client.post("/api/v1/assistant", json={
        "text": "The AC is broken in Lab 12",
    }, headers=student_headers).json()
    assert waiting["type"] == "message"
    interrupted = client.post("/api/v1/assistant", json={
        "text": "Some students are harassing me near hostel A",
    }, headers=student_headers).json()
    assert interrupted["type"] == "decision"
    assert interrupted["decision"]["intent"] == "GRIEVANCE"
    assert interrupted["decision"]["status"] == "ESCALATED"


def test_safety_grievance_interrupts_active_booking_flow(client, student_headers):
    client.post("/api/v1/assistant", json={
        "text": "Book library 201 tomorrow at 3 PM",
    }, headers=student_headers)
    interrupted = client.post("/api/v1/assistant", json={
        "text": "I am being threatened near room 201",
    }, headers=student_headers).json()
    assert interrupted["type"] == "decision"
    assert interrupted["decision"]["intent"] == "GRIEVANCE"


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
    assert "outside the campus services" in response["message"]
    assert "bonafide" not in response["message"].lower()
    assert "flight" not in response["message"].lower()
    assert "knowledge_gap" not in response


def test_out_of_scope_response_is_neutral_and_helpful(client, student_headers):
    response = client.post("/api/v1/assistant", json={"text": "Can you create a video for me?"}, headers=student_headers).json()
    assert response["type"] == "message"
    assert "outside the campus services" in response["message"]
    assert "flight" not in response["message"].lower()
    assert "share a little more context" in response["message"]
