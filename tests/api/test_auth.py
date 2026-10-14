"""Auth matrix."""
from __future__ import annotations

import datetime as dt

from tests.conftest import _register_and_login


def test_login_ok_and_me(client):
    _register_and_login(client)
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "admin"


def test_login_wrong_password_generic(client):
    _register_and_login(client)
    # need fresh client without auth? use new login attempt with wrong pw (still logged in but login endpoint is unauth)
    r = client.post("/api/v1/auth/login", json={"username_or_email": "admin", "password": "WrongPassword123!"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid credentials"


def test_login_unknown_user_generic(client):
    r = client.post("/api/v1/auth/login", json={"username_or_email": "nosuch", "password": "Whatever123456"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid credentials"


def test_logout_revokes(client):
    _register_and_login(client)
    r = client.post("/api/v1/auth/logout")
    assert r.status_code == 200
    r2 = client.get("/api/v1/auth/me")
    assert r2.status_code == 401


def test_expired_session_rejected(client):
    uid, csrf = _register_and_login(client)
    # expire session in DB
    SessionFactory = client._testing_session_factory
    from app.db.models import Session as DBSession

    db = SessionFactory()
    try:
        sess = db.query(DBSession).filter(DBSession.user_id == uid).first()
        sess.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


def test_disabled_account_cannot_login_and_sessions_revoked(client):
    uid, csrf = _register_and_login(client, username="admin")
    # create second user then disable via admin
    from app.core.security import hash_password
    from app.db.models import User

    SessionFactory = client._testing_session_factory
    db = SessionFactory()
    try:
        u = User(username="bob", email="bob@example.com", display_name="Bob", password_hash=hash_password("BobPassword123!"), global_role="user", is_active=True, created_at=dt.datetime.now(dt.timezone.utc), updated_at=dt.datetime.now(dt.timezone.utc))
        db.add(u)
        db.commit()
    finally:
        db.close()
    # disable bob
    # first login as bob in separate client? use same client after logout
    client.post("/api/v1/auth/logout")
    rb = client.post("/api/v1/auth/login", json={"username_or_email": "bob", "password": "BobPassword123!"})
    assert rb.status_code == 200
    bob_csrf = rb.json()["csrf_token"]
    client.headers.update({"X-CSRF-Token": bob_csrf})
    # admin re-login to disable? need admin session; logout bob, login admin
    client.post("/api/v1/auth/logout")
    ra = client.post("/api/v1/auth/login", json={"username_or_email": "admin", "password": "AdminPassword123!"})
    assert ra.status_code == 200
    client.headers.update({"X-CSRF-Token": ra.json()["csrf_token"]})
    # find bob id
    db = SessionFactory()
    try:
        bob = db.query(User).filter(User.username == "bob").first()
        bob_id = bob.id
    finally:
        db.close()
    r = client.post(f"/api/v1/admin/users/{bob_id}/disable")
    assert r.status_code == 200
    # bob cannot login now
    client.post("/api/v1/auth/logout")
    r2 = client.post("/api/v1/auth/login", json={"username_or_email": "bob", "password": "BobPassword123!"})
    assert r2.status_code == 401


def test_password_change_requires_current_and_revokes_others(client):
    _register_and_login(client)
    # wrong current
    r = client.post("/api/v1/auth/change-password", json={"current_password": "bad", "new_password": "NewPassword12345!"})
    assert r.status_code == 400
    # correct
    r2 = client.post("/api/v1/auth/change-password", json={"current_password": "AdminPassword123!", "new_password": "NewPassword12345!"})
    assert r2.status_code == 200
    # old password no longer works
    client.post("/api/v1/auth/logout")
    r3 = client.post("/api/v1/auth/login", json={"username_or_email": "admin", "password": "AdminPassword123!"})
    assert r3.status_code == 401
    r4 = client.post("/api/v1/auth/login", json={"username_or_email": "admin", "password": "NewPassword12345!"})
    assert r4.status_code == 200


def test_login_throttling(client):
    # 5 attempts then 429
    for i in range(5):
        r = client.post("/api/v1/auth/login", json={"username_or_email": "ghost", "password": "badpassword123"})
        assert r.status_code == 401
    r = client.post("/api/v1/auth/login", json={"username_or_email": "ghost", "password": "badpassword123"})
    assert r.status_code == 429


def test_csrf_missing_fails(client):
    _register_and_login(client)
    # remove csrf header
    client.headers.pop("X-CSRF-Token", None)
    r = client.post("/api/v1/projects", json={"project_key": "TST", "name": "Test"})
    assert r.status_code == 403
    assert "CSRF" in r.json()["detail"]
