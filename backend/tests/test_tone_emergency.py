from app.services.tone_service import analyze


def test_fire_policy_question_is_not_misclassified_as_active_emergency():
    tone = analyze("What does the campus fire safety policy say?")
    assert tone.emergency is False


def test_active_fire_interrupts_maintenance_slot_collection(client, student_headers, admin_headers):
    waiting = client.post("/api/v1/assistant", json={
        "text": "The AC is broken in Lab 12",
    }, headers=student_headers).json()
    assert waiting["type"] == "message"

    emergency = client.post("/api/v1/assistant", json={
        "text": "The hostel is on fire right now and people are trapped",
    }, headers=student_headers).json()
    assert emergency["emergency_escalated"] is True
    decision = emergency["decision"]
    assert decision["intent"] == "GRIEVANCE"
    assert decision["decision"] == "ESCALATE"
    assert decision["status"] == "ESCALATED"
    assert decision["risk"] == "HIGH"
    assert decision["entities"]["emergency_type"] == "FIRE"
    assert "campus security" in decision["message"].lower()
    assert "floor" not in decision["message"].lower()
    admin_inbox = client.get("/api/v1/notifications", headers=admin_headers).json()
    assert any("URGENT campus safety escalation" in item["subject"] for item in admin_inbox)


def test_hindi_emergency_is_detected_and_answered_without_slot_filling(client, student_headers):
    response = client.post("/api/v1/assistant", json={
        "text": "हॉस्टल में आग लगी है, लोग अंदर फंसे हैं",
        "language": "hi",
    }, headers=student_headers).json()
    assert response["emergency_escalated"] is True
    assert response["decision"]["entities"]["emergency_type"] == "FIRE"
    assert "कैंपस सुरक्षा" in response["decision"]["message"]


def test_medical_emergency_interrupts_booking_confirmation(client, student_headers):
    booking = client.post("/api/v1/assistant", json={
        "text": "Book library 201 tomorrow at 3 PM",
    }, headers=student_headers).json()
    assert booking["decision"]["requires_confirmation"] is True

    emergency = client.post("/api/v1/assistant", json={
        "text": "My friend collapsed and is not breathing, medical emergency",
    }, headers=student_headers).json()
    assert emergency["emergency_escalated"] is True
    assert emergency["decision"]["entities"]["emergency_type"] == "MEDICAL"
    assert emergency["decision"]["requires_confirmation"] is False


def test_hinglish_repetition_annoyance_is_acknowledged_and_moved_forward(client, student_headers):
    client.post("/api/v1/assistant", json={
        "text": "Projector kharab hai",
    }, headers=student_headers)
    response = client.post("/api/v1/assistant", json={
        "text": "Kitni baar bataun, bas karo aur jo hai usse kar do",
        "language": "hinglish",
    }, headers=student_headers).json()
    assert response["type"] == "decision"
    assert response["decision"]["status"] == "OPEN"
    assert "repeat nahi karunga" in response["decision"]["message"] or "already shared" in response["decision"]["message"]
