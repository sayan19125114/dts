"""Phase 4 report exports.

Small exports are processed inline (``settings.export_inline``); larger or
async-configured deployments leave jobs ``pending`` for the worker. Every job
carries a hashed time-limited download token; downloads are re-authorized on
every request (owner or admin) even when a valid token is presented.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import secrets
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_token
from app.db.models import Bug, BugSla, ExportJob, ProjectMember, Release, SlaEvent, User, utcnow
from app.services import sla as sla_service

log = logging.getLogger(__name__)


def _csv_safe(v) -> str:
    if v is None:
        return ""
    s = str(v)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def _export_root() -> Path:
    root = Path(get_settings().export_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _visible_project_ids(db: Session, user: User) -> set[int] | None:
    if user.global_role == "admin":
        return None
    return set(db.execute(select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)).scalars().all())


def create_job(db: Session, user: User, kind: str, params: dict) -> tuple[ExportJob, str]:
    raw_token = secrets.token_urlsafe(32)
    job = ExportJob(
        token_hash=hash_token(raw_token),
        created_by=user.id,
        kind=kind,
        params_json=params or {},
        status="pending",
        expires_at=utcnow() + dt.timedelta(seconds=get_settings().export_expire_seconds),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job, raw_token


def process_job(db: Session, job: ExportJob) -> ExportJob:
    if job.status not in ("pending", "failed"):
        return job
    job.status = "running"
    db.commit()
    try:
        user = db.get(User, job.created_by)
        if user is None:
            raise RuntimeError("owner no longer exists")
        content, filename, row_count = _render(db, user, job.kind, job.params_json or {})
        key = secrets.token_hex(16)
        path = _export_root() / key
        path.write_text(content, encoding="utf-8")
        job.storage_key = key
        job.filename = filename
        job.row_count = row_count
        job.status = "done"
        job.error = ""
        job.finished_at = utcnow()
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("export job %s failed", job.id)
        job.status = "failed"
        job.error = str(exc)[:500]
        job.finished_at = utcnow()
    db.commit()
    db.refresh(job)
    return job


def process_pending(db: Session, limit: int = 5) -> int:
    jobs = db.execute(select(ExportJob).where(ExportJob.status == "pending").order_by(ExportJob.id.asc()).limit(limit)).scalars().all()
    for job in jobs:
        process_job(db, job)
    return len(jobs)


def export_path(job: ExportJob) -> Path | None:
    if not job.storage_key:
        return None
    return _export_root() / job.storage_key


def is_expired(job: ExportJob, now: dt.datetime | None = None) -> bool:
    now = now or utcnow()
    exp = job.expires_at
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=dt.timezone.utc)
    return exp is not None and exp < now


def cleanup_expired(db: Session) -> int:
    """Delete stored files for expired jobs (keeps the audit row)."""
    removed = 0
    for job in db.execute(select(ExportJob).where(ExportJob.storage_key.isnot(None), ExportJob.expires_at < utcnow())).scalars().all():
        path = export_path(job)
        job.storage_key = None
        if path and path.exists():
            try:
                path.unlink()
                removed += 1
            except OSError:  # pragma: no cover
                log.warning("could not delete expired export file %s", path)
    if removed:
        db.commit()
    return removed


# ---- renderers ------------------------------------------------------------

def _render(db: Session, user: User, kind: str, params: dict):
    if kind == "bugs":
        return _render_bugs(db, user, params)
    if kind == "sla_compliance":
        return _render_sla(db, user, params)
    if kind == "release_notes":
        return _render_release_notes(db, user, params)
    if kind == "analytics":
        return _render_analytics(db, user, params)
    raise ValueError(f"unknown export kind: {kind}")


def _render_bugs(db: Session, user: User, params: dict):
    visible = _visible_project_ids(db, user)
    stmt = select(Bug)
    project_id = params.get("project_id")
    if project_id is not None:
        if visible is not None and project_id not in visible:
            raise PermissionError("Project not visible")
        stmt = stmt.where(Bug.project_id == project_id)
    elif visible is not None:
        if not visible:
            stmt = stmt.where(Bug.id == -1)
        else:
            stmt = stmt.where(Bug.project_id.in_(list(visible)))
    if params.get("status"):
        stmt = stmt.where(Bug.status == params["status"])
    if params.get("q"):
        like = f"%{str(params['q']).strip()}%"
        stmt = stmt.where(or_(Bug.title.ilike(like), Bug.public_key.ilike(like)))
    rows = db.execute(stmt.order_by(Bug.public_key).limit(20000)).scalars().all()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["public_key", "project_id", "title", "status", "severity", "priority", "type", "assignee_id", "reporter_id", "resolved_at", "created_at", "updated_at"])
    for b in rows:
        w.writerow([_csv_safe(x) for x in (b.public_key, b.project_id, b.title, b.status, b.severity, b.priority, b.type, b.assignee_id, b.reporter_id, b.resolved_at, b.created_at, b.updated_at)])
    return out.getvalue(), "bugs.csv", len(rows)


def _render_sla(db: Session, user: User, params: dict):
    visible = _visible_project_ids(db, user)
    stmt = select(BugSla)
    project_id = params.get("project_id")
    if project_id is not None:
        if visible is not None and project_id not in visible:
            raise PermissionError("Project not visible")
        stmt = stmt.where(BugSla.project_id == project_id)
    elif visible is not None:
        stmt = stmt.where(BugSla.project_id.in_(list(visible))) if visible else stmt.where(BugSla.id == -1)
    rows = db.execute(stmt.order_by(BugSla.project_id, BugSla.bug_id)).scalars().all()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
        "bug_id", "public_key", "project_id", "policy_version", "state",
        "first_response_due", "first_response_at", "first_response_met",
        "triage_due", "triage_at", "triage_met",
        "resolution_due", "resolved_at", "resolution_met",
    ])
    for row in rows:
        bug = db.get(Bug, row.bug_id)
        comp = sla_service.compliance(row)
        fr = comp.get("first_response", {})
        tr = comp.get("triage", {})
        rl = comp.get("resolution", {})
        w.writerow([_csv_safe(x) for x in (
            row.bug_id, bug.public_key if bug else "", row.project_id, row.policy_version, row.state,
            fr.get("due"), fr.get("observed"), fr.get("met"),
            tr.get("due"), tr.get("observed"), tr.get("met"),
            rl.get("due"), rl.get("observed"), rl.get("met"),
        )])
    return out.getvalue(), "sla-compliance.csv", len(rows)


def _render_release_notes(db: Session, user: User, params: dict):
    release_id = params.get("release_id")
    release = db.get(Release, release_id) if release_id else None
    if release is None:
        raise ValueError("release_id required")
    visible = _visible_project_ids(db, user)
    if visible is not None and release.project_id not in visible:
        raise PermissionError("Project not visible")
    bugs = db.execute(select(Bug).where(Bug.fixed_release_id == release.id).order_by(Bug.severity, Bug.public_key)).scalars().all()
    lines = [f"# {release.name} ({release.version})", ""]
    if release.planned_date:
        lines.append(f"- Planned: {release.planned_date}")
    if release.actual_date:
        lines.append(f"- Released: {release.actual_date}")
    lines.append(f"- Status: {release.status}")
    lines.append("")
    if release.description:
        lines += [release.description, ""]
    lines.append("## Fixed defects")
    lines.append("")
    for b in bugs:
        lines.append(f"- **{b.public_key}** [{b.severity}/{b.priority}] {b.title}")
    lines.append("")
    if release.release_notes:
        lines += ["## Notes", "", release.release_notes, ""]
    return "\n".join(lines), f"release-{release.version}-notes.md", len(bugs)


def _render_analytics(db: Session, user: User, params: dict):
    from app.services.analytics import compute_analytics

    data = compute_analytics(db, user, params)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["metric", "key", "value"])
    for group in ("totals", "distributions", "sla"):
        section = data.get(group, {})
        if isinstance(section, dict):
            for k, v in section.items():
                if isinstance(v, dict):
                    for kk, vv in v.items():
                        w.writerow([group, f"{k}.{kk}", vv])
                else:
                    w.writerow([group, k, v])
    for k, v in (data.get("throughput") or {}).items():
        w.writerow(["throughput", k, v])
    return out.getvalue(), "analytics.csv", sum(1 for _ in out.getvalue().splitlines()) - 1
