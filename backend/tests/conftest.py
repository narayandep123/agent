"""Shared test fixtures.

Point the app at a throwaway SQLite database *before* it is imported, then expose
a TestClient and ready-made auth headers so tests can exercise the protected API.
"""
import os
import tempfile
import uuid
import re

# Must run before any `from app...` import so the DB engine binds to the temp file.
_TEST_DB = os.path.join(tempfile.gettempdir(), "campusflow_test.db")
os.environ["CAMPUSFLOW_DB"] = _TEST_DB
if os.path.exists(_TEST_DB):
    os.remove(_TEST_DB)

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    from app.main import app
    return TestClient(app)


def _signup(client, role):
    suffix = uuid.uuid4().hex[:8]
    email = f"{role.lower()}-{suffix}@campus.edu"
    resp = client.post("/api/v1/auth/signup", json={
        "name": f"Test {role.title()}", "roll_no": f"CS-{suffix}", "email": email,
        "mobile": "9876543210", "role": role, "password": "secret123",
    })
    from app.services.notification_service import OUTBOX
    body = next(item["body"] for item in OUTBOX if item["to"] == email and "verification code" in item["body"])
    code = re.search(r"\b\d{6}\b", body).group(0)
    verified = client.post("/api/v1/auth/verify-email", json={"email": email, "code": code})
    return email, verified


@pytest.fixture
def student_headers(client):
    _, resp = _signup(client, "STUDENT")
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def admin_headers(client):
    token = client.post("/api/v1/auth/login", json={
        "email": "admin@campusflow.edu", "password": "admin123",
    }).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
