"""Phase 4 analytics + exports: metrics, scoping, token-limited downloads."""
from __future__ import annotations

import datetime as dt

from tests.conftest import _register_and_login


def _proj(client, key="AN"):
    r = client.post("/api/v1/projects", json={"project_key": key, "name": key, "description": "d"})
    assert r.status_code == 201, r.text
    return r.json()


def _bug(client, pid, **over):
    payload = {"title": "Analytics defect title here", "description": "d", "severity": "High", "priority": "P2"}
    payload.update(over)
    r = client.post(f"/api/v1/projects/{pid}/bugs", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_analytics_summary_and_totals(client):
    _register_and_login(client)
    pid = _proj(client)["id"]
    b = _bug(client, pid)
    client.post(f"/api/v1/bugs/{b['id']}/transition", json={"to_status": "In Progress"})
    client.post(f"/api/v1/bugs/{b['id']}/transition", json={"to_status": "Fixed"})
    r = client.get(f"/api/v1/analytics/summary?project_id={pid}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["totals"]["opened"] >= 1
    assert data["totals"]["resolved"] >= 1
    assert data["mttr_hours"]["count"] >= 1
    assert "by_severity" in data["distributions"]
    assert data["throughput"]  # at least one day bucket


def test_analytics_scope_isolates_projects(client):
    _register_and_login(client)
    pid = _proj(client, key="AN1")["id"]
    other = _proj(client, key="AN2")["id"]
    _bug(client, pid)
    data = client.get(f"/api/v1/analytics/summary?project_id={other}").json()
    assert data["totals"]["opened"] == 0


def test_analytics_sla_after_breach(client):
    _register_and_login(client)
    pid = _proj(client, key="AN3")["id"]
    all_day = {str(d): [[0, 1440]] for d in range(7)}
    client.post(f"/api/v1/projects/{pid}/sla-policies", json={
        "name": "S", "timezone": "UTC", "business_hours": all_day,
        "targets": {"default": {"first_response": 1, "triage": 1, "resolution": 1, "warning_pct": 80}},
    })
    b = _bug(client, pid)
    from app.db.models import Bug

    db = client._testing_session_factory()
    try:
        row = db.get(Bug, b["id"])
        row.created_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=30)
        db.commit()
    finally:
        db.close()
    client.post(f"/api/v1/bugs/{b['id']}/sla/apply")
    client.post("/api/v1/admin/sla/run")
    sla = client.get(f"/api/v1/analytics/sla?project_id={pid}").json()
    assert sla["sla"]["instances"] >= 1
    assert sla["sla"]["breached"] >= 1


def test_analytics_release_escape_rate(client):
    _register_and_login(client)
    pid = _proj(client, key="AN4")["id"]
    rel = client.post(f"/api/v1/projects/{pid}/releases", json={
        "name": "Shipped", "version": "9.9.9", "actual_date": (dt.date.today() - dt.timedelta(days=3)).isoformat(),
    }).json()
    b = _bug(client, pid)
    client.post(f"/api/v1/releases/{rel['id']}/bugs", json={"bug_id": b["id"], "relation": "affected"})
    data = client.get(f"/api/v1/analytics/releases?project_id={pid}").json()
    assert data["releases"]["escape_rate"] is not None
    assert data["releases"]["per_release"][0]["escaped"] >= 1


def test_export_inline_and_download(client, monkeypatch, tmp_path):
    _register_and_login(client)
    pid = _proj(client, key="EX1")["id"]
    _bug(client, pid)
    from app.core import config as cfgmod

    s = cfgmod.get_settings()
    monkeypatch.setattr(s, "export_dir", str(tmp_path / "exports"))
    r = client.post("/api/v1/reports/exports", json={"kind": "bugs", "params": {"project_id": pid}})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["job"]["status"] == "done"
    assert body["job"]["row_count"] >= 1
    # download with the returned token (no session needed)
    dl = client.get(body["download_url"])
    assert dl.status_code == 200
    assert "public_key" in dl.text
    # a wrong token cannot fetch it
    bad = client.get(f"/api/v1/reports/exports/{body['job']['id']}/download?token=bogus")
    assert bad.status_code == 404
    # listing shows the job
    jobs = client.get("/api/v1/reports/exports").json()
    assert any(j["id"] == body["job"]["id"] for j in jobs)


def test_export_kind_validation(client):
    _register_and_login(client)
    assert client.post("/api/v1/reports/exports", json={"kind": "nope", "params": {}}).status_code == 422


def test_export_expired_download_denied(client, monkeypatch, tmp_path):
    _register_and_login(client)
    pid = _proj(client, key="EX2")["id"]
    _bug(client, pid)
    from app.core import config as cfgmod

    monkeypatch.setattr(cfgmod.get_settings(), "export_dir", str(tmp_path / "exports"))
    body = client.post("/api/v1/reports/exports", json={"kind": "bugs", "params": {"project_id": pid}}).json()
    from app.db.models import ExportJob

    db = client._testing_session_factory()
    try:
        job = db.get(ExportJob, body["job"]["id"])
        job.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    assert client.get(body["download_url"]).status_code == 404


def test_export_sla_and_release_notes(client, monkeypatch, tmp_path):
    _register_and_login(client)
    pid = _proj(client, key="EX3")["id"]
    from app.core import config as cfgmod

    monkeypatch.setattr(cfgmod.get_settings(), "export_dir", str(tmp_path / "exports"))
    sla = client.post("/api/v1/reports/exports", json={"kind": "sla_compliance", "params": {"project_id": pid}})
    assert sla.status_code == 201 and sla.json()["job"]["status"] == "done"
    rel = client.post(f"/api/v1/projects/{pid}/releases", json={"name": "N", "version": "1.0.0"}).json()
    notes = client.post("/api/v1/reports/exports", json={"kind": "release_notes", "params": {"release_id": rel["id"]}})
    assert notes.status_code == 201 and notes.json()["job"]["status"] == "done"
