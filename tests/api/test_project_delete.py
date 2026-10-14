"""Admin-only hard project deletion + cascade."""
from __future__ import annotations

import datetime as dt
import os

from tests.conftest import _register_and_login


def _mk_user(client, username, role="user", pw="UserPassword123!"):
    from app.core.security import hash_password
    from app.db.models import User

    db = client._testing_session_factory()
    try:
        u = User(username=username.lower(), email=f"{username}@ex.com", display_name=username, password_hash=hash_password(pw), global_role=role, is_active=True, created_at=dt.datetime.now(dt.timezone.utc), updated_at=dt.datetime.now(dt.timezone.utc))
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def _login(client, username, pw="UserPassword123!"):
    client.post("/api/v1/auth/logout")
    r = client.post("/api/v1/auth/login", json={"username_or_email": username, "password": pw})
    assert r.status_code == 200, r.text
    client.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})


def _admin(client):
    return _register_and_login(client, username="admin", email="admin@ex.com", password="AdminPassword123!", global_role="admin")


def test_only_admin_can_delete_project(client):
    _admin(client)
    proj = client.post("/api/v1/projects", json={"project_key": "DEL", "name": "Del", "description": "d"}).json()
    _mk_user(client, "plain")
    _login(client, "plain")
    assert client.delete(f"/api/v1/projects/{proj['id']}").status_code == 403
    _login(client, "admin", pw="AdminPassword123!")
    assert client.delete(f"/api/v1/projects/{proj['id']}").status_code == 200
    assert client.get(f"/api/v1/projects/{proj['id']}").status_code == 404


def test_hard_delete_cascades_children_and_files(client):
    _admin(client)
    proj = client.post("/api/v1/projects", json={"project_key": "CAS", "name": "Cascade", "description": "d"}).json()
    bug = client.post(f"/api/v1/projects/{proj['id']}/bugs", json={"title": "Delete me bug title", "description": "d"}).json()
    assert client.post(f"/api/v1/bugs/{bug['id']}/comments", json={"body": "a comment"}).status_code == 201
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c626001000000ffff0300000600050b0a2d0000000049454e44ae426082")
    up = client.post(f"/api/v1/bugs/{bug['id']}/attachments", files={"file": ("shot.png", png, "image/png")})
    assert up.status_code == 201, up.text
    aid = up.json()["id"]

    from app.core.config import get_settings

    assert len(os.listdir(get_settings().upload_dir)) == 1

    r = client.delete(f"/api/v1/projects/{proj['id']}")
    assert r.status_code == 200, r.text

    assert client.get(f"/api/v1/projects/{proj['id']}").status_code == 404
    assert client.get(f"/api/v1/bugs/{bug['id']}").status_code == 404
    assert client.get(f"/api/v1/bugs/{bug['id']}/comments").status_code == 404
    assert client.get(f"/api/v1/attachments/{aid}/download").status_code == 404
    keys = [p["project_key"] for p in client.get("/api/v1/projects").json()]
    assert "CAS" not in keys
    assert os.listdir(get_settings().upload_dir) == []


def test_delete_missing_project_404(client):
    _admin(client)
    assert client.delete("/api/v1/projects/999999").status_code == 404
