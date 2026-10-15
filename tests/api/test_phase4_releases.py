"""Phase 4 releases + quality gates: readiness, blocking, waivers, force, notes."""
from __future__ import annotations

from tests.conftest import _register_and_login


def _proj(client, key="REL"):
    r = client.post("/api/v1/projects", json={"project_key": key, "name": key, "description": "d"})
    assert r.status_code == 201, r.text
    return r.json()


def _release(client, pid, version="1.0.0", **over):
    payload = {"name": f"Release {version}", "version": version, "gates_blocking": False}
    payload.update(over)
    r = client.post(f"/api/v1/projects/{pid}/releases", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _bug(client, pid, severity="Low", **over):
    payload = {"title": "Release linked defect title", "description": "d", "severity": severity, "priority": "P4"}
    payload.update(over)
    r = client.post(f"/api/v1/projects/{pid}/bugs", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_release_crud_and_unique_version(client):
    _register_and_login(client)
    pid = _proj(client)["id"]
    rel = _release(client, pid)
    assert rel["status"] == "planned"
    # duplicate version rejected
    dup = client.post(f"/api/v1/projects/{pid}/releases", json={"name": "dup", "version": "1.0.0"})
    assert dup.status_code == 409
    assert len(client.get(f"/api/v1/projects/{pid}/releases").json()) == 1
    # patch
    patch = client.patch(f"/api/v1/releases/{rel['id']}", json={"description": "hello"})
    assert patch.status_code == 200 and patch.json()["description"] == "hello"
    # archive
    assert client.delete(f"/api/v1/releases/{rel['id']}").status_code == 200
    assert client.get(f"/api/v1/releases/{rel['id']}").json()["status"] == "archived"


def test_link_bug_and_release_notes(client):
    _register_and_login(client)
    pid = _proj(client, key="REL2")["id"]
    rel = _release(client, pid, version="2.0.0")
    b = _bug(client, pid, severity="High")
    assert client.post(f"/api/v1/releases/{rel['id']}/bugs", json={"bug_id": b["id"], "relation": "fixed"}).status_code == 200
    assert client.get(f"/api/v1/bugs/{b['id']}").json()["fixed_release_id"] == rel["id"]
    notes = client.get(f"/api/v1/releases/{rel['id']}/notes").json()
    assert b["public_key"] in notes["markdown"] and notes["bug_count"] == 1
    # unlink
    assert client.delete(f"/api/v1/releases/{rel['id']}/bugs/{b['id']}").status_code == 200
    assert client.get(f"/api/v1/bugs/{b['id']}").json()["fixed_release_id"] is None


def test_blocking_gate_prevents_release_then_waiver_allows(client):
    _register_and_login(client)
    pid = _proj(client, key="REL3")["id"]
    rel = _release(client, pid, version="3.0.0", gates_blocking=True)
    gate = client.post(
        f"/api/v1/releases/{rel['id']}/gates",
        json={"name": "No criticals", "rule_type": "unresolved_critical", "config_json": {"scope": "project", "max": 0}, "blocking": True},
    )
    assert gate.status_code == 201, gate.text
    # passes with no criticals
    ready = client.get(f"/api/v1/releases/{rel['id']}/readiness").json()
    assert ready["ready"] is True and ready["would_block"] is False
    # introduce a critical defect -> gate fails -> release blocked
    _bug(client, pid, severity="Critical")
    client.post(f"/api/v1/releases/{rel['id']}/gates/{gate.json()['id']}/evaluate")
    ready = client.get(f"/api/v1/releases/{rel['id']}/readiness").json()
    assert ready["would_block"] is True and ready["ready"] is False
    blocked = client.post(f"/api/v1/releases/{rel['id']}/release")
    assert blocked.status_code == 409
    # waive the gate -> release succeeds
    w = client.post(f"/api/v1/releases/{rel['id']}/gates/{gate.json()['id']}/waive", json={"reason": "accepted risk for pilot"})
    assert w.status_code == 200 and w.json()["waived"] is True
    ok = client.post(f"/api/v1/releases/{rel['id']}/release")
    assert ok.status_code == 200 and ok.json()["status"] == "released"


def test_force_release_requires_reason_and_audits(client):
    _register_and_login(client)
    pid = _proj(client, key="REL4")["id"]
    rel = _release(client, pid, version="4.0.0", gates_blocking=True)
    gate = client.post(
        f"/api/v1/releases/{rel['id']}/gates",
        json={"name": "No criticals", "rule_type": "unresolved_critical", "config_json": {"scope": "project", "max": 0}, "blocking": True},
    ).json()
    _bug(client, pid, severity="Critical")
    client.post(f"/api/v1/releases/{rel['id']}/gates/{gate['id']}/evaluate")
    # force without reason rejected
    assert client.post(f"/api/v1/releases/{rel['id']}/release?force=true").status_code == 422
    forced = client.post(f"/api/v1/releases/{rel['id']}/release?force=true&reason=executive+override")
    assert forced.status_code == 200 and forced.json()["status"] == "released"
    audit = client.get("/api/v1/admin/audit").json()
    assert any(x["action"] == "force_released" for x in audit["items"])


def test_advisory_gate_does_not_block(client):
    _register_and_login(client)
    pid = _proj(client, key="REL5")["id"]
    rel = _release(client, pid, version="5.0.0", gates_blocking=True)
    gate = client.post(
        f"/api/v1/releases/{rel['id']}/gates",
        json={"name": "Advisory", "rule_type": "unresolved_critical", "config_json": {"scope": "project", "max": 0}, "blocking": False},
    ).json()
    _bug(client, pid, severity="Critical")
    client.post(f"/api/v1/releases/{rel['id']}/gates/{gate['id']}/evaluate")
    ready = client.get(f"/api/v1/releases/{rel['id']}/readiness").json()
    assert ready["would_block"] is False
    assert ready["advisory_failures"]
    assert client.post(f"/api/v1/releases/{rel['id']}/release").status_code == 200


def test_releases_flag_disabled_hides_endpoints(client, monkeypatch):
    _register_and_login(client)
    pid = _proj(client, key="REL6")["id"]
    from app.core import config as cfgmod

    s = cfgmod.get_settings()
    monkeypatch.setattr(s, "feature_releases", False)
    assert client.get(f"/api/v1/projects/{pid}/releases").status_code == 404
