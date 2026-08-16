from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
def test_request_endpoint_returns_decision_card():
    response = client.post("/api/v1/requests", json={"text": "Classroom 204 AC is not working"})
    assert response.status_code == 200
    assert response.json()["decision"] == "ACT"
