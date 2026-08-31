import uuid
import re


def _student(client):
    suffix = uuid.uuid4().hex[:8]
    email = f"memory-{suffix}@campus.edu"
    client.post("/api/v1/auth/signup", json={
        "name": "Memory Student", "roll_no": f"MEM-{suffix}",
        "email": email,
        "mobile": "9876543210", "role": "STUDENT", "password": "secret123",
    })
    from app.services.notification_service import OUTBOX
    body = next(item["body"] for item in OUTBOX if item["to"] == email and "verification code" in item["body"])
    code = re.search(r"\b\d{6}\b", body).group(0)
    response = client.post("/api/v1/auth/verify-email", json={"email": email, "code": code})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_conversation_messages_persist_and_title_is_generated(client, student_headers):
    conversation = client.post("/api/v1/conversations", json={}, headers=student_headers).json()
    cid = conversation["id"]
    saved = client.post(f"/api/v1/conversations/{cid}/messages", json={
        "role": "user", "text": "I need a bonafide certificate for a scholarship",
    }, headers=student_headers)
    assert saved.status_code == 200

    messages = client.get(f"/api/v1/conversations/{cid}/messages", headers=student_headers).json()
    assert messages[0]["text"] == "I need a bonafide certificate for a scholarship"
    threads = client.get("/api/v1/conversations", headers=student_headers).json()
    assert next(row for row in threads if row["id"] == cid)["title"].startswith("I need a bonafide")


def test_conversations_are_private_to_their_owner(client, student_headers):
    cid = client.post("/api/v1/conversations", json={}, headers=student_headers).json()["id"]
    other = _student(client)
    denied = client.get(f"/api/v1/conversations/{cid}/messages", headers=other)
    assert denied.status_code == 404
    assert "privacy boundary" in denied.json()["detail"]
    assert client.post(f"/api/v1/conversations/{cid}/messages", json={
        "role": "user", "text": "Trying to read another user's chat",
    }, headers=other).status_code == 404


def test_structured_decision_cards_round_trip(client, student_headers):
    cid = client.post("/api/v1/conversations", json={}, headers=student_headers).json()["id"]
    card = {"decision": "ACT", "request_id": "CF-TEST"}
    client.post(f"/api/v1/conversations/{cid}/messages", json={
        "role": "decision", "payload": card,
    }, headers=student_headers)
    message = client.get(f"/api/v1/conversations/{cid}/messages", headers=student_headers).json()[0]
    assert message["payload"] == card


def test_owner_can_delete_conversation_and_messages(client, student_headers):
    cid = client.post("/api/v1/conversations", json={}, headers=student_headers).json()["id"]
    client.post(f"/api/v1/conversations/{cid}/messages", json={
        "role": "user", "text": "This chat will be deleted",
    }, headers=student_headers)
    deleted = client.delete(f"/api/v1/conversations/{cid}", headers=student_headers)
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/conversations/{cid}/messages", headers=student_headers).status_code == 404
    assert all(row["id"] != cid for row in client.get("/api/v1/conversations", headers=student_headers).json())


def test_user_cannot_delete_another_users_conversation(client, student_headers):
    cid = client.post("/api/v1/conversations", json={}, headers=student_headers).json()["id"]
    other = _student(client)
    assert client.delete(f"/api/v1/conversations/{cid}", headers=other).status_code == 404
