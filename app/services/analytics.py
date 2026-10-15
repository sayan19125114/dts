"""Phase 4 quality analytics.

All formulas are documented here and mirrored in docs/ANALYTICS.md.

Definitions
-----------
* ``opened``       : defects created within the window.
* ``resolved``     : defects whose first resolution instant falls in the window.
* ``backlog``      : not-yet-resolved, non-archived defects (as of window end).
* ``mttr``         : mean/median ``resolved_at - created_at`` (wall hours) for
                     defects resolved in the window.
* ``triage``       : mean ``triage_at - created_at`` for defects that have a
                     triage signal.
* ``reopen_rate``  : distinct defects with a Reopened transition ÷ resolved defects.
* ``sla_compliance``: met clocks ÷ (met + breached clocks) per SLA instance.
* ``escape_rate``  : defects reported against a release *after* its actual ship
                     date ÷ all defects linked to that release.
* "team" is approximated by project (no team entity exists in this schema).

Everything is permission-scoped to the caller's visible projects; admins see all.
"""
from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Bug, BugHistory, BugSla, ProjectMember, Release, User, utcnow
from app.services import sla as sla_service

RESOLVED = set(sla_service.RESOLVED_STATES)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def _visible_project_ids(db: Session, user: User) -> set[int] | None:
    if user.global_role == "admin":
        return None
    return set(db.execute(select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)).scalars().all())


def _parse_date(value, default: dt.date) -> dt.date:
    if not value:
        return default
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return default


def compute_analytics(db: Session, user: User, params: dict) -> dict:
    params = params or {}
    now = utcnow()
    today = now.date()
    default_from = today - dt.timedelta(days=29)
    date_from = _parse_date(params.get("date_from"), default_from)
    date_to = _parse_date(params.get("date_to"), today)
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    start = dt.datetime.combine(date_from, dt.time.min, tzinfo=dt.timezone.utc)
    end = dt.datetime.combine(date_to, dt.time.max, tzinfo=dt.timezone.utc)
    group_by = params.get("group_by") or "day"

    visible = _visible_project_ids(db, user)
    project_id = params.get("project_id")
    if project_id is not None:
        if visible is not None and project_id not in visible:
            raise PermissionError("Project not visible")
        scope_ids: list[int] | None = [project_id]
    elif visible is not None:
        scope_ids = list(visible)
        if not scope_ids:
            scope_ids = [-1]
    else:
        scope_ids = None

    stmt = select(Bug)
    if scope_ids is not None:
        stmt = stmt.where(Bug.project_id.in_(scope_ids))
    if params.get("severity"):
        stmt = stmt.where(Bug.severity == params["severity"])
    if params.get("priority"):
        stmt = stmt.where(Bug.priority == params["priority"])
    if params.get("assignee_id"):
        stmt = stmt.where(Bug.assignee_id == params["assignee_id"])
    if params.get("component"):
        stmt = stmt.where(Bug.component == params["component"])
    bugs = db.execute(stmt).scalars().all()
    bug_ids = [b.id for b in bugs]

    history_by_bug: dict[int, list[BugHistory]] = defaultdict(list)
    if bug_ids:
        for h in db.execute(select(BugHistory).where(BugHistory.bug_id.in_(bug_ids)).order_by(BugHistory.created_at.asc(), BugHistory.id.asc())).scalars().all():
            history_by_bug[h.bug_id].append(h)

    reopened_ids: set[int] = set()
    triage_at_map: dict[int, dt.datetime] = {}
    first_resolution: dict[int, dt.datetime] = {}
    for b in bugs:
        for h in history_by_bug.get(b.id, []):
            new = (h.changes_json or {}).get("new")
            if h.event_type == "status_changed":
                if new == "Reopened":
                    reopened_ids.add(b.id)
                if new == "In Progress" and b.id not in triage_at_map:
                    triage_at_map[b.id] = _aware(h.created_at)
                if new in RESOLVED and b.id not in first_resolution:
                    first_resolution[b.id] = _aware(h.created_at)
            elif h.event_type == "assigned" and b.id not in triage_at_map and h.actor_id != b.reporter_id:
                triage_at_map[b.id] = _aware(h.created_at)

    def resolved_at(b: Bug) -> dt.datetime | None:
        return _aware(b.resolved_at) or first_resolution.get(b.id)

    opened = [b for b in bugs if start <= (_aware(b.created_at) or start) <= end]
    resolved = [b for b in bugs if (r := resolved_at(b)) is not None and start <= r <= end]
    backlog = [b for b in bugs if not b.is_archived and b.status not in RESOLVED]

    mttr_hours = None
    if resolved:
        deltas = [(_aware(b.resolved_at) or first_resolution.get(b.id) or _aware(b.created_at)) - _aware(b.created_at) for b in resolved]
        hours = [d.total_seconds() / 3600 for d in deltas if d is not None]
        if hours:
            mttr_hours = {"mean": round(statistics.fmean(hours), 2), "median": round(statistics.median(hours), 2), "count": len(hours)}

    triage_hours = None
    triage_vals = []
    for b, t in triage_at_map.items():
        created = _aware(next((x.created_at for x in bugs if x.id == b), None))
        if created is not None and t is not None and t >= created:
            triage_vals.append((t - created).total_seconds() / 3600)
    if triage_vals:
        triage_hours = {"mean": round(statistics.fmean(triage_vals), 2), "median": round(statistics.median(triage_vals), 2), "count": len(triage_vals)}

    reopen_denominator = len([b for b in bugs if resolved_at(b) is not None])
    reopen_rate = round(len(reopened_ids) / reopen_denominator, 4) if reopen_denominator else 0.0

    ages = [(now - (_aware(b.created_at) or now)).total_seconds() / 86400 for b in backlog]
    backlog_age = {"mean": round(statistics.fmean(ages), 2), "max": round(max(ages), 2), "count": len(ages)} if ages else {"mean": 0.0, "max": 0.0, "count": 0}

    throughput = _buckets(opened, resolved, group_by, start, end)

    distributions = {
        "by_status": _count(bugs, lambda b: b.status if not b.is_archived else "Archived"),
        "by_severity": _count(bugs, lambda b: b.severity),
        "by_priority": _count(bugs, lambda b: b.priority),
        "by_component": _count(bugs, lambda b: b.component or "Unspecified"),
        "by_assignee": _count(bugs, lambda b: str(b.assignee_id) if b.assignee_id else "Unassigned"),
        "by_affected_version": _count(bugs, lambda b: b.affected_version or "Unspecified"),
    }

    sla_stats = _sla_stats(db, scope_ids)

    releases = _release_stats(db, bugs)

    return {
        "scope": {"project_id": project_id, "project_ids": scope_ids, "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "group_by": group_by},
        "totals": {"opened": len(opened), "resolved": len(resolved), "backlog": len(backlog), "reopened": len(reopened_ids), "total_defects": len(bugs)},
        "mttr_hours": mttr_hours,
        "time_to_triage_hours": triage_hours,
        "reopen_rate": reopen_rate,
        "backlog_age_days": backlog_age,
        "throughput": throughput,
        "distributions": distributions,
        "sla": sla_stats,
        "releases": releases,
    }


def _count(items, keyfn) -> dict:
    out: dict[str, int] = {}
    for it in items:
        k = keyfn(it)
        out[str(k)] = out.get(str(k), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _buckets(opened, resolved, group_by, start, end) -> dict:
    def bucket_key(d: dt.date) -> str:
        if group_by == "month":
            return d.replace(day=1).isoformat()
        if group_by == "week":
            return (d - dt.timedelta(days=d.weekday())).isoformat()
        return d.isoformat()

    out: dict[str, dict[str, int]] = {}
    for b in opened:
        k = bucket_key((_aware(b.created_at) or start).date())
        out.setdefault(k, {"opened": 0, "resolved": 0})["opened"] += 1
    for b in resolved:
        r = _aware(b.resolved_at)
        if r is None:
            continue
        k = bucket_key(r.date())
        out.setdefault(k, {"opened": 0, "resolved": 0})["resolved"] += 1
    return dict(sorted(out.items()))


def _sla_stats(db: Session, scope_ids: list[int] | None) -> dict:
    stmt = select(BugSla)
    if scope_ids is not None:
        stmt = stmt.where(BugSla.project_id.in_(scope_ids))
    rows = db.execute(stmt).scalars().all()
    by_clock: dict[str, dict[str, int]] = {}
    breached = warned = 0
    for row in rows:
        comp = sla_service.compliance(row)
        for clock, info in comp.items():
            slot = by_clock.setdefault(clock, {"met": 0, "missed": 0, "pending": 0})
            if info.get("met") is True:
                slot["met"] += 1
            elif info.get("met") is False:
                slot["missed"] += 1
            else:
                slot["pending"] += 1
        flags = row.flags_json or {}
        if any(v.get("breached") for v in flags.values()) or row.state == "breached":
            breached += 1
        elif any(v.get("warned") for v in flags.values()):
            warned += 1
    total_met = sum(v["met"] for v in by_clock.values())
    total_missed = sum(v["missed"] for v in by_clock.values())
    denom = total_met + total_missed
    return {
        "instances": len(rows),
        "breached": breached,
        "at_risk": warned,
        "compliance_pct": round(100 * total_met / denom, 2) if denom else None,
        "by_clock": by_clock,
    }


def _release_stats(db: Session, bugs: list[Bug]) -> dict:
    releases = db.execute(select(Release)).scalars().all()
    if not releases:
        return {"escape_rate": None, "per_release": []}
    by_id = {r.id: r for r in releases}
    affected: dict[int, list[Bug]] = defaultdict(list)
    fixed: dict[int, list[Bug]] = defaultdict(list)
    for b in bugs:
        if b.affected_release_id in by_id:
            affected[b.affected_release_id].append(b)
        if b.fixed_release_id in by_id:
            fixed[b.fixed_release_id].append(b)
    per_release = []
    total_escaped = 0
    total_linked = 0
    for rid, rel in by_id.items():
        linked = affected.get(rid, []) + fixed.get(rid, [])
        escaped = 0
        if rel.actual_date is not None:
            escaped = len([b for b in affected.get(rid, []) if (_aware(b.created_at) or utcnow()).date() > rel.actual_date])
        total_escaped += escaped
        total_linked += len(linked)
        if linked or escaped:
            per_release.append({
                "release_id": rid,
                "version": rel.version,
                "status": rel.status,
                "linked": len(linked),
                "escaped": escaped,
                "escape_rate": round(escaped / len(linked), 4) if linked else None,
            })
    return {
        "escape_rate": round(total_escaped / total_linked, 4) if total_linked else None,
        "per_release": sorted(per_release, key=lambda x: x["version"]),
    }
