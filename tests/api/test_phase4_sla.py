"""Phase 4 SLA: policy lifecycle, apply, pause/resume, breach, at-risk, reapply."""
from __future__ import annotations

import datetime as dt

from tests.conftest import _register_and_login

ALL_DAY = {str(d): [[0, 1440]] for d in range(7)}


def _proj(client, key="SLA"):
    r = client.post("/api/v1/projects", json={"project_key": key, "name": key, "description": "d"})
    assert r.status_code == 201, r.text
    return r.json()


def _policy(client, pid, **over):
    payload = {
        "name": "Test SLA",
        "timezone": "UTC",
        "business_hours": ALL_DAY,
        "holidays": [],
        "targets": {"default": {"first_response": 1, "triage": 1, "resolution": 1, "warning_pct": 80}},
        "pause_statuses": ["Fixed"],
        "escalation_role": "manager",
    }
    payload.update(over)
    return client.post(f"/api/v1/projects/{pid}/sla-policies", json=payload)


def _bug(client, pid, **over):
    payload = {"title": "SLA defect title here", "description": "d", "severity": "Low", "priority": "P4"}
    payload.update(over)
    r = client.post(f"/api/v1/projects/{pid}/bugs", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _age_bug(client, bug_id, minutes):
    """Backdate a defect so SLA clocks are overdue."""
    from app.db.models import Bug

    db = client._testing_session_factory()
    try:
        b = db.get(Bug, bug_id)
        b.created_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)
        db.commit()
    finally:
        db.close()


def test_policy_lifecycle_and_versioning(client):
    _register_and_login(client)
    pid = _proj(client)["id"]
    r = _policy(client, pid)
    assert r.status_code == 201, r.text
    assert r.json()["version"] == 1 and r.json()["is_active"] is True
    # second save creates v2 and deactivates v1
    r2 = _policy(client, pid, name="v2")
    assert r2.json()["version"] == 2
    active = client.get(f"/api/v1/projects/{pid}/sla-policies/active")
    assert active.status_code == 200 and active.json()["version"] == 2
    listed = client.get(f"/api/v1/projects/{pid}/sla-policies").json()
    assert [p["version"] for p in listed] == [2, 1]
    # invalid timezone rejected
    assert _policy(client, pid, timezone="Mars/Phobos").status_code == 422
    # invalid pause status rejected
    assert _policy(client, pid, pause_statuses=["Nope"]).status_code == 422


def test_policy_requires_manager(client):
    _register_and_login(client)
    pid = _proj(client, key="SLAR")["id"]
    # create a plain, non-member user -> no access at all
    from tests.api.test_phases_1_3 import _mk_user, _login

    _mk_user(client, "slaplain")
    _login(client, "slaplain")
    assert _policy(client, pid).status_code == 404


def test_sla_auto_applied_on_creation(client):
    _register_and_login(client)
    pid = _proj(client, key="SLA2")["id"]
    assert _policy(client, pid).status_code == 201
    b = _bug(client, pid)
    r = client.get(f"/api/v1/bugs/{b['id']}/sla")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sla"]["policy_version"] == 1
    assert body["sla"]["first_response_due"] is not None
    assert any(e["event_type"] == "applied" for e in body["events"])


def test_pause_and_resume_on_transition(client):
    _register_and_login(client)
    pid = _proj(client, key="SLA3")["id"]
    assert _policy(client, pid).status_code == 201
    b = _bug(client, pid)
    client.post(f"/api/v1/bugs/{b['id']}/transition", json={"to_status": "In Progress"})
    client.post(f"/api/v1/bugs/{b['id']}/transition", json={"to_status": "Fixed"})
    body = client.get(f"/api/v1/bugs/{b['id']}/sla").json()
    assert body["sla"]["is_paused"] is True
    assert body["sla"]["paused_at"] is not None
    # resume (Fixed -> Ready for Test)
    client.post(f"/api/v1/bugs/{b['id']}/transition", json={"to_status": "Ready for Test"})
    body = client.get(f"/api/v1/bugs/{b['id']}/sla").json()
    assert body["sla"]["is_paused"] is False
    assert any(e["event_type"] == "paused" for e in body["events"])
    assert any(e["event_type"] == "resumed" for e in body["events"])


def test_breach_detected_by_worker_and_escalated(client):
    _register_and_login(client)
    pid = _proj(client, key="SLA4")["id"]
    assert _policy(client, pid).status_code == 201
    b = _bug(client, pid)
    _age_bug(client, b["id"], 60)
    # re-apply now that the defect is old, so the deadline is in the past
    applied = client.post(f"/api/v1/bugs/{b['id']}/sla/apply")
    assert applied.status_code == 200, applied.text
    run = client.post("/api/v1/admin/sla/run")
    assert run.status_code == 200, run.text
    body = client.get(f"/api/v1/bugs/{b['id']}/sla").json()
    assert body["sla"]["state"] == "breached"
    assert any(e["event_type"] == "breach" for e in body["events"])
    # idempotent: a second run must not duplicate breach events
    n_before = sum(1 for e in body["events"] if e["event_type"] == "breach")
    client.post("/api/v1/admin/sla/run")
    body2 = client.get(f"/api/v1/bugs/{b['id']}/sla").json()
    n_after = sum(1 for e in body2["events"] if e["event_type"] == "breach")
    assert n_before == n_after
    # admin gets an escalation notification
    notifs = client.get("/api/v1/notifications").json()
    items = notifs["items"] if isinstance(notifs, dict) else notifs
    assert any("breach" in (n.get("type") or "") for n in items)


def test_at_risk_and_reapply_semantics(client):
    _register_and_login(client)
    pid = _proj(client, key="SLA5")["id"]
    assert _policy(client, pid).status_code == 201
    b = _bug(client, pid)
    _age_bug(client, b["id"], 60)
    client.post(f"/api/v1/bugs/{b['id']}/sla/apply")
    client.post("/api/v1/admin/sla/run")
    at_risk = client.get(f"/api/v1/projects/{pid}/sla/at-risk").json()
    assert at_risk["total"] >= 1
    assert at_risk["items"][0]["public_key"] == b["public_key"]
    # reapply without apply_to_existing is a no-op
    noop = client.post(f"/api/v1/projects/{pid}/sla/reapply", json={"apply_to_existing": False})
    assert noop.json()["applied"] == 0
    # explicit reapply applies to unresolved defects
    applied = client.post(f"/api/v1/projects/{pid}/sla/reapply", json={"apply_to_existing": True, "only_unresolved": True})
    assert applied.json()["applied"] >= 1


def test_no_policy_means_no_sla(client):
    _register_and_login(client)
    pid = _proj(client, key="SLA6")["id"]
    b = _bug(client, pid)
    assert client.get(f"/api/v1/bugs/{b['id']}/sla").status_code == 404
