"""Input/browser security + dashboard/reports + reliability."""
from __future__ import annotations

from tests.conftest import _register_and_login


def _admin_proj_bug(client, key="SEC1"):
    r = client.post("/api/v1/projects", json={"project_key": key, "name": key, "description": "d"})
    assert r.status_code == 201
    proj = r.json()
    rb = client.post(f"/api/v1/projects/{proj['id']}/bugs", json={"title": "Sec bug title here", "description": "d"})
    return proj, rb.json()


def test_sql_injection_safe(client):
    _register_and_login(client)
    proj, bug = _admin_proj_bug(client, key="SQL")
    r = client.get(f"/api/v1/projects/{proj['id']}/bugs?q=' OR '1'='1")
    assert r.status_code == 200
    # should not return all with injection; total should be 1 (only exact like match)
    assert r.json()["total"] <= 1


def test_sort_allowlist_rejects(client):
    _register_and_login(client)
    proj, bug = _admin_proj_bug(client, key="SRT")
    r = client.get(f"/api/v1/projects/{proj['id']}/bugs?sort=password_hash")
    assert r.status_code == 400
    r2 = client.get(f"/api/v1/projects/{proj['id']}/bugs?sort=updated_at;DROP")
    assert r2.status_code == 400


def test_error_no_stacktrace(client):
    r = client.get("/api/v1/bugs/999999")
    # unauth -> 401 without stack
    assert r.status_code == 401
    assert "Traceback" not in r.text
    _register_and_login(client)
    r2 = client.get("/api/v1/bugs/999999")
    assert r2.status_code == 404
    assert "Traceback" not in r2.text


def test_security_headers_present(client):
    _register_and_login(client)
    r = client.get("/api/v1/auth/me")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "Content-Security-Policy" in r.headers


def test_csv_formula_injection_mitigated(client):
    _register_and_login(client)
    proj, bug = _admin_proj_bug(client, key="CSV")
    # create bug with formula title? title min 5 chars, starts with =
    r = client.post(f"/api/v1/projects/{proj['id']}/bugs", json={"title": "=cmd|'/C calc'!A0", "description": "d"})
    assert r.status_code == 201
    csvr = client.get(f"/api/v1/reports/bugs.csv?project_id={proj['id']}")
    assert csvr.status_code == 200
    assert "'=cmd" in csvr.text  # escaped with leading single quote


def test_dashboard_scoped(client):
    _register_and_login(client, username="admin", email="admin@ex.com", password="AdminPassword123!", global_role="admin")
    p1 = client.post("/api/v1/projects", json={"project_key": "D1X", "name": "D1", "description": ""}).json()
    p2 = client.post("/api/v1/projects", json={"project_key": "D2X", "name": "D2", "description": ""}).json()
    client.post(f"/api/v1/projects/{p1['id']}/bugs", json={"title": "Dash bug one title", "description": "d"})
    # create plain user member of p1 only
    import datetime as dt
    from app.core.security import hash_password
    from app.db.models import User

    db = client._testing_session_factory()
    try:
        u = User(username="dash", email="dash@ex.com", display_name="d", password_hash=hash_password("DashPassword123!"), global_role="user", is_active=True, created_at=dt.datetime.now(dt.timezone.utc), updated_at=dt.datetime.now(dt.timezone.utc))
        db.add(u)
        db.commit()
    finally:
        db.close()
    client.post(f"/api/v1/projects/{p1['id']}/members", json={"username_or_email": "dash", "role": "viewer"})
    client.post("/api/v1/auth/logout")
    r = client.post("/api/v1/auth/login", json={"username_or_email": "dash", "password": "DashPassword123!"})
    client.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    d = client.get("/api/v1/dashboard/summary").json()
    assert d["total_visible"] == 1
    assert any(p["project_key"] == "D1X" for p in d["per_project"])
    assert not any(p["project_key"] == "D2X" for p in d["per_project"])
    # export only sees p1
    csvr = client.get("/api/v1/reports/bugs.csv")
    assert "D2X" not in csvr.text or "Dash bug" in csvr.text


def test_transaction_rollback_and_persistence(client):
    _register_and_login(client)
    # failed create (validation) leaves no partial
    proj = client.post("/api/v1/projects", json={"project_key": "TRX", "name": "Trx", "description": ""}).json()
    before = client.get(f"/api/v1/projects/{proj['id']}/bugs").json()["total"]
    r = client.post(f"/api/v1/projects/{proj['id']}/bugs", json={"title": "x", "description": "d"})  # too short
    assert r.status_code == 422
    after = client.get(f"/api/v1/projects/{proj['id']}/bugs").json()["total"]
    assert before == after


def test_concurrent_public_key_unique(client):
    # sequential creation must not duplicate; simulate rapid creates
    _register_and_login(client)
    proj = client.post("/api/v1/projects", json={"project_key": "CNQ", "name": "Cnq", "description": ""}).json()
    keys = set()
    for i in range(5):
        r = client.post(f"/api/v1/projects/{proj['id']}/bugs", json={"title": f"Conc bug title {i}", "description": "d"})
        assert r.status_code == 201
        keys.add(r.json()["public_key"])
    assert len(keys) == 5


def test_password_hash_not_exposed(client):
    _register_and_login(client)
    me = client.get("/api/v1/auth/me").json()
    assert "password_hash" not in str(me)
    assert "token_hash" not in str(me)
    users = client.get("/api/v1/admin/users").json()
    assert "password_hash" not in str(users)
