import uuid


def _student(client):
    response = client.post("/api/v1/auth/signup", json={
        "name": "Memory Student", "roll_no": "MEM-1",
        "email": f"memory-{uuid.uuid4().hex[:8]}@campus.edu",
        "mobile": "9876543210", "role": "STUDENT", "password": "secret123",
    })
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
    assert client.get(f"/api/v1/conversations/{cid}/messages", headers=other).status_code == 404
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
