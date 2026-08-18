"""Sign-up, login and admin enrolment-approval flows."""
import uuid


def _signup(client, role, password="secret123"):
    suffix = uuid.uuid4().hex[:8]
    email = f"{role.lower()}-{suffix}@campus.edu"
    resp = client.post("/api/v1/auth/signup", json={
        "name": f"Test {role.title()}", "roll_no": f"R-{suffix}", "email": email,
        "mobile": "9876543210", "role": role, "password": password,
    })
    return email, resp


def test_student_self_enrols_and_is_logged_in(client):
    _, resp = _signup(client, "STUDENT")
    body = resp.json()
    assert resp.status_code == 200
    assert body["pending"] is False
    assert body["access_token"]
    assert body["user"]["status"] == "ACTIVE"


def test_faculty_signup_is_pending_and_cannot_login(client):
    email, resp = _signup(client, "FACULTY")
    assert resp.json()["pending"] is True
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "secret123"})
    assert login.status_code == 403


def test_admin_approves_faculty_then_they_can_login(client, admin_headers):
    email, _ = _signup(client, "STAFF")
    pending = client.get("/api/v1/admin/users?status=PENDING", headers=admin_headers).json()
    target = next(u for u in pending if u["email"] == email)
    decision = client.post(f"/api/v1/admin/users/{target['id']}/decision", json={"approved": True, "comment": "Verified."}, headers=admin_headers)
    assert decision.status_code == 200
    assert decision.json()["status"] == "ACTIVE"
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "secret123"})
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "STAFF"


def test_admin_rejects_faculty_and_login_stays_blocked(client, admin_headers):
    email, _ = _signup(client, "FACULTY")
    pending = client.get("/api/v1/admin/users?status=PENDING", headers=admin_headers).json()
    target = next(u for u in pending if u["email"] == email)
    client.post(f"/api/v1/admin/users/{target['id']}/decision", json={"approved": False, "comment": "Not recognised."}, headers=admin_headers)
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "secret123"})
    assert login.status_code == 403


def test_duplicate_email_is_rejected(client):
    email, _ = _signup(client, "STUDENT")
    dup = client.post("/api/v1/auth/signup", json={
        "name": "Dup", "roll_no": "R-2", "email": email,
        "mobile": "9876543210", "role": "STUDENT", "password": "secret123",
    })
    assert dup.status_code == 409


def test_duplicate_email_is_rejected_case_insensitively(client):
    email = f"Case-{uuid.uuid4().hex[:8]}@Campus.edu"
    payload = {"name": "Case User", "roll_no": f"CASE-{uuid.uuid4().hex[:6]}", "email": email,
               "mobile": "9876543210", "role": "STUDENT", "password": "secret123"}
    assert client.post("/api/v1/auth/signup", json=payload).status_code == 200
    payload["email"] = email.upper()
    payload["roll_no"] = f"OTHER-{uuid.uuid4().hex[:6]}"
    duplicate = client.post("/api/v1/auth/signup", json=payload)
    assert duplicate.status_code == 409
    assert "email" in duplicate.json()["detail"].lower()


def test_duplicate_roll_number_is_rejected_case_insensitively(client):
    roll = f"CS-{uuid.uuid4().hex[:8]}"
    first = {"name": "First Student", "roll_no": roll, "email": f"first-{uuid.uuid4().hex[:8]}@campus.edu",
             "mobile": "9876543210", "role": "STUDENT", "password": "secret123"}
    second = {**first, "name": "Duplicate Student", "roll_no": roll.lower(),
              "email": f"second-{uuid.uuid4().hex[:8]}@campus.edu"}
    assert client.post("/api/v1/auth/signup", json=first).status_code == 200
    duplicate = client.post("/api/v1/auth/signup", json=second)
    assert duplicate.status_code == 409
    assert "roll / employee number" in duplicate.json()["detail"]


def test_admin_user_list_requires_admin(client, student_headers):
    assert client.get("/api/v1/admin/users", headers=student_headers).status_code == 403


def test_admin_can_revoke_and_restore_access(client, admin_headers):
    email, resp = _signup(client, "STUDENT")
    uid = resp.json()["user"]["id"]
    revoked = client.post(f"/api/v1/admin/users/{uid}/access", json={"active": False, "comment": "Left campus."}, headers=admin_headers)
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
    assert client.post("/api/v1/auth/login", json={"email": email, "password": "secret123"}).status_code == 403
    restored = client.post(f"/api/v1/admin/users/{uid}/access", json={"active": True}, headers=admin_headers)
    assert restored.json()["status"] == "ACTIVE"
    assert client.post("/api/v1/auth/login", json={"email": email, "password": "secret123"}).status_code == 200


def test_admin_cannot_revoke_own_access(client, admin_headers):
    me = client.get("/api/v1/auth/me", headers=admin_headers).json()
    r = client.post(f"/api/v1/admin/users/{me['id']}/access", json={"active": False}, headers=admin_headers)
    assert r.status_code == 400
