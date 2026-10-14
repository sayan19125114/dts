"""Authorization matrix + IDOR."""
from __future__ import annotations

import datetime as dt

from tests.conftest import _register_and_login


def _mk_user(client, username, role="user", pw="UserPassword123!"):
    from app.core.security import hash_password
    from app.db.models import User

    SessionFactory = client._testing_session_factory
    db = SessionFactory()
    try:
        u = User(username=username.lower(), email=f"{username}@ex.com", display_name=username, password_hash=hash_password(pw), global_role=role, is_active=True, created_at=dt.datetime.now(dt.timezone.utc), updated_at=dt.datetime.now(dt.timezone.utc))
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _login(client, username, pw="UserPassword123!"):
    client.post("/api/v1/auth/logout")
    r = client.post("/api/v1/auth/login", json={"username_or_email": username, "password": pw})
    assert r.status_code == 200, r.text
    client.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})


def _admin_project(client, key="PAY", name="Pay"):
    r = client.post("/api/v1/projects", json={"project_key": key, "name": name, "description": "d"})
    assert r.status_code == 201, r.text
    return r.json()


def test_anonymous_blocked(client):
    assert client.get("/api/v1/projects").status_code == 401
    assert client.get("/api/v1/dashboard/summary").status_code == 401


def test_viewer_cannot_create(client):
    _register_and_login(client, username="admin", email="admin@ex.com", password="AdminPassword123!", global_role="admin")
    proj = _admin_project(client, key="VWR")
    _mk_user(client, "viewer1")
    client.post("/api/v1/projects/VWR/members".replace("VWR", str(proj["id"])), json={"username_or_email": "viewer1", "role": "viewer"})
    _login(client, "viewer1")
    r = client.post(f"/api/v1/projects/{proj['id']}/bugs", json={"title": "Valid title here", "description": "desc"})
    assert r.status_code == 403


def test_reporter_cannot_edit_others(client):
    _register_and_login(client, username="admin", email="admin@ex.com", password="AdminPassword123!", global_role="admin")
    proj = _admin_project(client, key="RPT")
    _mk_user(client, "rep1")
    _mk_user(client, "rep2")
    client.post(f"/api/v1/projects/{proj['id']}/members", json={"username_or_email": "rep1", "role": "reporter"})
    client.post(f"/api/v1/projects/{proj['id']}/members", json={"username_or_email": "rep2", "role": "reporter"})
    _login(client, "rep1")
    r = client.post(f"/api/v1/projects/{proj['id']}/bugs", json={"title": "Reporter bug title", "description": "d"})
    assert r.status_code == 201
    bug = r.json()
    _login(client, "rep2")
    r2 = client.patch(f"/api/v1/bugs/{bug['id']}", json={"title": "Hacked title here", "version": bug["version"]})
    assert r2.status_code == 403


def test_nonmember_cannot_read(client):
    _register_and_login(client, username="admin", email="admin@ex.com", password="AdminPassword123!", global_role="admin")
    proj = _admin_project(client, key="SEC")
    _mk_user(client, "outsider")
    _login(client, "outsider")
    r = client.get(f"/api/v1/projects/{proj['id']}")
    assert r.status_code == 404
    # try bug list too (need bug id? just project bugs)
    r2 = client.get(f"/api/v1/projects/{proj['id']}/bugs")
    assert r2.status_code == 404


def test_idor_bug_substitution(client):
    _register_and_login(client, username="admin", email="admin@ex.com", password="AdminPassword123!", global_role="admin")
    p1 = _admin_project(client, key="PAA")
    p2 = _admin_project(client, key="PBB")
    _mk_user(client, "member1")
    client.post(f"/api/v1/projects/{p1['id']}/members", json={"username_or_email": "member1", "role": "reporter"})
    # create bug in p2 as admin
    r = client.post(f"/api/v1/projects/{p2['id']}/bugs", json={"title": "Secret bug title", "description": "secret"})
    bug2 = r.json()
    _login(client, "member1")
    # member1 tries to read bug from p2
    r2 = client.get(f"/api/v1/bugs/{bug2['id']}")
    assert r2.status_code == 404


def test_nonadmin_cannot_use_admin(client):
    _register_and_login(client, username="admin", email="admin@ex.com", password="AdminPassword123!", global_role="admin")
    _mk_user(client, "plain")
    _login(client, "plain")
    assert client.get("/api/v1/admin/users").status_code == 403
    assert client.post("/api/v1/admin/users", json={"username": "x", "email": "x@ex.com", "display_name": "x", "password": "Password123456!", "global_role": "user"}).status_code == 403


def test_last_admin_protection(client):
    _register_and_login(client, username="admin", email="admin@ex.com", password="AdminPassword123!", global_role="admin")
    # find admin id
    SessionFactory = client._testing_session_factory
    from app.db.models import User

    db = SessionFactory()
    try:
        a = db.query(User).filter(User.username == "admin").first()
        aid = a.id
    finally:
        db.close()
    r = client.post(f"/api/v1/admin/users/{aid}/disable")
    assert r.status_code == 400
    r2 = client.patch(f"/api/v1/admin/users/{aid}", json={"global_role": "user"})
    assert r2.status_code == 400


def test_membership_removal_blocks_access(client):
    _register_and_login(client, username="admin", email="admin@ex.com", password="AdminPassword123!", global_role="admin")
    proj = _admin_project(client, key="RMV")
    _mk_user(client, "temp")
    client.post(f"/api/v1/projects/{proj['id']}/members", json={"username_or_email": "temp", "role": "viewer"})
    _login(client, "temp")
    assert client.get(f"/api/v1/projects/{proj['id']}").status_code == 200
    _login(client, "admin", pw="AdminPassword123!")
    db = client._testing_session_factory()
    try:
        from app.db.models import User

        t = db.query(User).filter(User.username == "temp").first()
        tid = t.id
    finally:
        db.close()
    r = client.delete(f"/api/v1/projects/{proj['id']}/members/{tid}")
    assert r.status_code == 200
    _login(client, "temp")
    assert client.get(f"/api/v1/projects/{proj['id']}").status_code == 404


def test_developer_cannot_change_unassigned(client):
    _register_and_login(client, username="admin", email="admin@ex.com", password="AdminPassword123!", global_role="admin")
    proj = _admin_project(client, key="DEV")
    _mk_user(client, "dev1")
    _mk_user(client, "rep1")
    client.post(f"/api/v1/projects/{proj['id']}/members", json={"username_or_email": "dev1", "role": "developer"})
    client.post(f"/api/v1/projects/{proj['id']}/members", json={"username_or_email": "rep1", "role": "reporter"})
    _login(client, "rep1")
    r = client.post(f"/api/v1/projects/{proj['id']}/bugs", json={"title": "Dev test bug title", "description": "d"})
    bug = r.json()
    _login(client, "dev1")
    r2 = client.patch(f"/api/v1/bugs/{bug['id']}", json={"title": "Dev tries edit unassigned bug!", "version": bug["version"]})
    assert r2.status_code == 403
