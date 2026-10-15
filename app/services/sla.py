"""Phase 4 SLA engine.

Business-calendar aware clocks for first response, triage, and resolution.
Everything here is additive: bugs without an applicable policy are untouched.

Design notes / documented definitions
-------------------------------------
* Times are *business minutes* inside the policy's calendar (timezone +
  weekday windows + holidays). A 2880-minute resolution target is 6 business
  days at 8h/day, not 48 wall-clock hours.
* ``first_response`` = first human action on the defect by someone other than
  the reporter (status change, assignment, comment, or attachment), recorded in
  ``bug_history``.
* ``triage`` = first assignment or first move into ``In Progress``.
* ``resolution`` = first move into a resolved state (Fixed / Ready for Test /
  Closed). Reopening clears the SLA's live resolution clock (the bug's
  ``resolved_at`` column keeps the historical first-resolution instant for
  analytics).
* Pausing: while a defect sits in a configured pause status the clocks stop.
  Deadlines are shifted forward on resume by the business minutes spent paused.
* Warning/breach/escalation events are idempotent (``sla_events.idempotency_key``
  and ``notifications.idempotency_key``), so the worker can run repeatedly.
* Policies are snapshotted onto the defect at apply time. Editing or replacing a
  policy never changes clocks for existing defects unless an admin explicitly
  re-applies it.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Bug,
    BugHistory,
    BugSla,
    Notification,
    NotificationPref,
    ProjectMember,
    SlaEvent,
    SlaPolicy,
    User,
    utcnow,
)

# 0=Monday .. 6=Sunday, minutes since local midnight.
DEFAULT_BUSINESS_HOURS: dict[str, list[list[int]]] = {
    "0": [[540, 1020]],
    "1": [[540, 1020]],
    "2": [[540, 1020]],
    "3": [[540, 1020]],
    "4": [[540, 1020]],
}

DEFAULT_TARGETS: dict = {
    "default": {"first_response": 240, "triage": 480, "resolution": 2880, "warning_pct": 80},
    "by_severity": {
        "Critical": {"first_response": 60, "triage": 120, "resolution": 480, "warning_pct": 80},
        "High": {"first_response": 120, "triage": 240, "resolution": 960, "warning_pct": 80},
    },
}

RESOLVED_STATES = ("Fixed", "Ready for Test", "Closed")
REOPEN_TARGETS = ("Open", "In Progress", "Reopened")

_MAX_DAYS = 3660  # hard cap to keep calendar maths bounded


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    """SQLite hands back naive datetimes; treat them as UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


class BusinessCalendar:
    """Business-minute arithmetic over a weekly calendar with holidays."""

    def __init__(self, tz: str, business_hours: dict | None, holidays: list | None):
        try:
            self.tz = ZoneInfo(tz or "UTC")
        except Exception:
            self.tz = ZoneInfo("UTC")
        hours = business_hours or DEFAULT_BUSINESS_HOURS
        self.hours: dict[int, list[tuple[int, int]]] = {}
        for dow in range(7):
            raw = hours.get(str(dow)) or hours.get(dow) or []
            intervals: list[tuple[int, int]] = []
            for item in raw:
                try:
                    start, end = int(item[0]), int(item[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if end > start:
                    intervals.append((start, end))
            self.hours[dow] = sorted(intervals)
        self.holidays: set[dt.date] = set()
        for h in holidays or []:
            try:
                self.holidays.add(dt.date.fromisoformat(str(h)[:10]))
            except ValueError:
                continue

    def _local(self, when: dt.datetime) -> dt.datetime:
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        return when.astimezone(self.tz)

    def _minutes(self, local: dt.datetime) -> int:
        return local.hour * 60 + local.minute

    def is_working_day(self, day: dt.date) -> bool:
        return day not in self.holidays and bool(self.hours[day.weekday()])

    def business_minutes_between(self, start: dt.datetime, end: dt.datetime) -> int:
        """Business minutes in [start, end). Returns 0 if end <= start."""
        start = _aware(start)
        end = _aware(end)
        if end <= start:
            return 0
        s = self._local(start)
        e = self._local(end)
        total = 0
        day = s.date()
        last = e.date()
        guard = 0
        while day <= last and guard < _MAX_DAYS:
            guard += 1
            if day not in self.holidays:
                for a, b in self.hours[day.weekday()]:
                    lo = max(s, dt.datetime.combine(day, dt.time(0), tzinfo=self.tz) + dt.timedelta(minutes=a))
                    hi = min(e, dt.datetime.combine(day, dt.time(0), tzinfo=self.tz) + dt.timedelta(minutes=b))
                    if hi > lo:
                        total += int((hi - lo).total_seconds() // 60)
            day += dt.timedelta(days=1)
        return total

    def add_business_minutes(self, start: dt.datetime, minutes: int) -> dt.datetime:
        """Add business minutes to ``start`` and return an aware UTC datetime."""
        start = _aware(start)
        if minutes <= 0:
            return start
        cursor = self._local(start)
        remaining = minutes
        guard = 0
        while guard < _MAX_DAYS:
            guard += 1
            day = cursor.date()
            for a, b in (self.hours[day.weekday()] if day not in self.holidays else []):
                win_start = dt.datetime.combine(day, dt.time(0), tzinfo=self.tz) + dt.timedelta(minutes=a)
                win_end = dt.datetime.combine(day, dt.time(0), tzinfo=self.tz) + dt.timedelta(minutes=b)
                eff = max(cursor, win_start)
                if eff >= win_end:
                    continue
                take = int((win_end - eff).total_seconds() // 60)
                if take <= 0:
                    continue
                if take >= remaining:
                    result = eff + dt.timedelta(minutes=remaining)
                    return result.astimezone(dt.timezone.utc)
                remaining -= take
            cursor = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), tzinfo=self.tz)
        return cursor.astimezone(dt.timezone.utc)


# ---- policy helpers -------------------------------------------------------

def _norm_target(raw: dict, fallback_warning: int) -> dict:
    def _int(key):
        v = raw.get(key)
        try:
            v = int(v)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    warn = raw.get("warning_pct", fallback_warning)
    try:
        warn = max(1, min(99, int(warn)))
    except (TypeError, ValueError):
        warn = fallback_warning
    return {
        "first_response": _int("first_response"),
        "triage": _int("triage"),
        "resolution": _int("resolution"),
        "warning_pct": warn,
    }


def resolve_targets(targets: dict | None, severity: str | None, priority: str | None) -> dict:
    t = targets or {}
    fallback_warning = 80
    try:
        fallback_warning = int((t.get("default") or {}).get("warning_pct", 80))
    except (TypeError, ValueError):
        fallback_warning = 80
    for key, val in (("by_severity", severity), ("by_priority", priority)):
        mapping = t.get(key) or {}
        if val and val in mapping:
            return _norm_target(mapping[val], fallback_warning)
    return _norm_target(t.get("default") or {}, fallback_warning)


def get_active_policy(db: Session, project_id: int) -> SlaPolicy | None:
    return db.execute(
        select(SlaPolicy)
        .where(SlaPolicy.project_id == project_id, SlaPolicy.is_active.is_(True))
        .order_by(SlaPolicy.version.desc())
    ).scalars().first()


# ---- signal derivation ----------------------------------------------------

def _parse_changes(row: BugHistory) -> dict:
    return row.changes_json or {}


def compute_signals(db: Session, bug: Bug) -> dict:
    """Derive first_response / triage / resolved instants from immutable history."""
    rows = db.execute(
        select(BugHistory).where(BugHistory.bug_id == bug.id).order_by(BugHistory.created_at.asc(), BugHistory.id.asc())
    ).scalars().all()

    first_response = None
    triage = None
    resolved = None
    for row in rows:
        ch = _parse_changes(row)
        new = ch.get("new")
        if first_response is None and row.actor_id is not None and row.actor_id != bug.reporter_id:
            if row.event_type in ("status_changed", "assigned", "commented", "attachment_added"):
                first_response = row.created_at
        if triage is None:
            if row.event_type == "assigned" and row.actor_id is not None and row.actor_id != bug.reporter_id:
                triage = row.created_at
            elif row.event_type == "status_changed" and new == "In Progress":
                triage = row.created_at
        if resolved is None:
            if row.event_type == "status_changed" and new in RESOLVED_STATES:
                resolved = row.created_at
    return {
        "first_response_at": _aware(first_response),
        "triage_at": _aware(triage),
        "resolved_at": _aware(resolved),
    }


# ---- apply / lifecycle ----------------------------------------------------

def _snapshot_kwargs(policy: SlaPolicy, targets: dict, warning_pct: int) -> dict:
    return {
        "policy_id": policy.id,
        "policy_version": policy.version,
        "timezone": policy.timezone or "UTC",
        "business_hours": policy.business_hours or DEFAULT_BUSINESS_HOURS,
        "holidays": policy.holidays or [],
        "pause_statuses": list(policy.pause_statuses or []),
        "escalation_role": policy.escalation_role or "manager",
        "escalation_user_id": policy.escalation_user_id,
        "first_response_target": targets["first_response"],
        "triage_target": targets["triage"],
        "resolution_target": targets["resolution"],
        "warning_pct": warning_pct,
    }


def apply_policy(db: Session, bug: Bug, policy: SlaPolicy, actor_id: int | None = None) -> BugSla:
    """Create or replace the SLA instance for a defect from ``policy``."""
    existing = db.execute(select(BugSla).where(BugSla.bug_id == bug.id)).scalar_one_or_none()
    if existing is not None:
        db.delete(existing)
        db.flush()

    targets = resolve_targets(policy.targets, bug.severity, bug.priority)
    cal = BusinessCalendar(policy.timezone, policy.business_hours, policy.holidays)
    start = bug.created_at or utcnow()
    row = BugSla(
        bug_id=bug.id,
        project_id=bug.project_id,
        state="running",
        started_at=start,
        **_snapshot_kwargs(policy, targets, targets["warning_pct"]),
    )
    if targets["first_response"]:
        row.first_response_due = cal.add_business_minutes(start, targets["first_response"])
    if targets["triage"]:
        row.triage_due = cal.add_business_minutes(start, targets["triage"])
    if targets["resolution"]:
        row.resolution_due = cal.add_business_minutes(start, targets["resolution"])
    db.add(row)
    db.flush()
    _event(db, bug.id, bug.project_id, row.policy_id, "clock", "applied", {"policy_version": row.policy_version})
    log_sla_audit(db, actor_id, bug, "sla_applied", {"policy_id": policy.id, "policy_version": policy.version})
    return row


def apply_active_policy_if_any(db: Session, bug: Bug, actor_id: int | None = None) -> BugSla | None:
    policy = get_active_policy(db, bug.project_id)
    if policy is None:
        return None
    # Never apply a policy that only becomes effective later; and never apply to
    # defects that already carry an SLA instance (explicit reapply is separate).
    existing = db.execute(select(BugSla).where(BugSla.bug_id == bug.id)).scalar_one_or_none()
    if existing is not None:
        return existing
    return apply_policy(db, bug, policy, actor_id=actor_id)


def _calendar_for(row: BugSla) -> BusinessCalendar:
    return BusinessCalendar(row.timezone, row.business_hours, row.holidays)


def on_status_change(db: Session, bug: Bug, old_status: str, new_status: str, actor_id: int | None = None) -> None:
    """Pause/resume clocks and drive the resolution clock on a status change."""
    row = db.execute(select(BugSla).where(BugSla.bug_id == bug.id)).scalar_one_or_none()
    if row is None:
        return
    now = utcnow()
    cal = _calendar_for(row)
    pause = set(row.pause_statuses or [])

    if new_status in pause and not row.is_paused:
        row.is_paused = True
        row.paused_at = now
        row.state = "paused"
        _event(db, bug.id, bug.project_id, row.policy_id, "clock", "paused", {"status": new_status})
    elif row.is_paused and new_status not in pause:
        paused_at = row.paused_at or now
        shift = cal.business_minutes_between(paused_at, now)
        for attr in ("first_response_due", "triage_due", "resolution_due"):
            val = getattr(row, attr)
            if val is not None:
                setattr(row, attr, val + dt.timedelta(minutes=shift))
        row.is_paused = False
        row.paused_at = None
        row.state = "running"
        _event(db, bug.id, bug.project_id, row.policy_id, "clock", "resumed", {"status": new_status, "shifted_minutes": shift})

    if new_status in RESOLVED_STATES and row.resolved_at is None:
        row.resolved_at = now
        _event(db, bug.id, bug.project_id, row.policy_id, "resolution", "resolved", {"status": new_status})
    elif new_status in REOPEN_TARGETS and row.resolved_at is not None:
        row.resolved_at = None
        row.state = "running"
        _event(db, bug.id, bug.project_id, row.policy_id, "resolution", "reopened", {"status": new_status})
    row.updated_at = now


def shift_deadlines(row: BugSla, now: dt.datetime | None = None) -> None:
    """Move all open deadlines forward by business time elapsed while paused."""
    now = now or utcnow()
    if not row.is_paused or row.paused_at is None:
        return
    cal = _calendar_for(row)
    shift = cal.business_minutes_between(row.paused_at, now)
    for attr in ("first_response_due", "triage_due", "resolution_due"):
        val = getattr(row, attr)
        if val is not None:
            setattr(row, attr, val + dt.timedelta(minutes=shift))


# ---- evaluation -----------------------------------------------------------

def _already(db: Session, key: str) -> bool:
    return db.execute(select(SlaEvent.id).where(SlaEvent.idempotency_key == key)).first() is not None


def _event(db: Session, bug_id: int, project_id: int | None, policy_id: int | None, clock: str, event_type: str, detail: dict, key: str | None = None) -> SlaEvent | None:
    if key and _already(db, key):
        return None
    ev = SlaEvent(
        bug_id=bug_id,
        project_id=project_id,
        policy_id=policy_id,
        clock=clock,
        event_type=event_type,
        detail_json=detail,
        idempotency_key=key,
    )
    db.add(ev)
    db.flush()
    return ev


def _notify(db: Session, event_type: str, bug: Bug, policy_row: BugSla, title: str, body: str, key: str) -> None:
    recipient_ids: set[int] = set()
    if policy_row.escalation_user_id:
        recipient_ids.add(policy_row.escalation_user_id)
    elif policy_row.escalation_role == "lead":
        # approximation: project managers act as leads (no separate lead entity)
        rows = db.execute(select(ProjectMember.user_id).where(ProjectMember.project_id == bug.project_id, ProjectMember.role == "manager")).all()
        recipient_ids.update(r[0] for r in rows)
    elif policy_row.escalation_role == "manager":
        rows = db.execute(select(ProjectMember.user_id).where(ProjectMember.project_id == bug.project_id, ProjectMember.role == "manager")).all()
        recipient_ids.update(r[0] for r in rows)
    else:
        admins = db.execute(select(User.id).where(User.global_role == "admin", User.is_active.is_(True))).all()
        recipient_ids.update(r[0] for r in admins)
    if policy_row.escalation_user_id is None:
        admins = db.execute(select(User.id).where(User.global_role == "admin", User.is_active.is_(True))).all()
        recipient_ids.update(r[0] for r in admins)
    if bug.assignee_id:
        recipient_ids.add(bug.assignee_id)
    recipients = [uid for uid in recipient_ids if uid]
    if not recipients:
        return
    disabled = {
        r[0]
        for r in db.execute(
            select(NotificationPref.user_id).where(
                NotificationPref.event_type == event_type, NotificationPref.enabled.is_(False)
            )
        ).all()
    }
    for uid in recipients:
        if uid in disabled:
            continue
        nkey = f"{key}:u{uid}"[:64]
        if db.execute(select(Notification.id).where(Notification.idempotency_key == nkey)).first() is not None:
            continue
        db.add(
            Notification(
                user_id=uid,
                type=event_type,
                title=title,
                body=body,
                bug_id=bug.id,
                project_id=bug.project_id,
                idempotency_key=nkey,
            )
        )


def _clock_observed(row: BugSla, clock: str) -> dt.datetime | None:
    return _aware({"first_response": row.first_response_at, "triage": row.triage_at, "resolution": row.resolved_at}[clock])


def _clock_due(row: BugSla, clock: str) -> dt.datetime | None:
    return _aware({"first_response": row.first_response_due, "triage": row.triage_due, "resolution": row.resolution_due}[clock])


def _clock_target(row: BugSla, clock: str) -> int | None:
    return {"first_response": row.first_response_target, "triage": row.triage_target, "resolution": row.resolution_target}[clock]


def evaluate_bug(db: Session, bug: Bug, now: dt.datetime | None = None) -> dict:
    """Sync observed signals and evaluate clocks. Idempotent. Returns flags."""
    row = db.execute(select(BugSla).where(BugSla.bug_id == bug.id)).scalar_one_or_none()
    if row is None:
        return {}
    now = now or utcnow()
    signals = compute_signals(db, bug)

    # observed instants are monotonic: never move a satisfied clock backwards on reopen
    for clock in ("first_response", "triage", "resolution"):
        if clock == "resolution":
            # live resolution clock uses the SLA row (cleared on reopen), not first-ever history
            continue
        obs = signals[f"{clock}_at"]
        cur = _clock_observed(row, clock)
        if obs is not None and (cur is None or obs < cur):
            setattr(row, f"{clock}_at", obs)

    cal = _calendar_for(row)
    effective_now = _aware(row.paused_at) if (row.is_paused and row.paused_at) else now
    flags = dict(row.flags_json or {})
    emitted: list[dict] = []

    for clock in ("first_response", "triage", "resolution"):
        due = _clock_due(row, clock)
        target = _clock_target(row, clock)
        if due is None or not target:
            continue
        observed = _clock_observed(row, clock)
        cflags = flags.setdefault(clock, {"warned": False, "breached": False, "escalated": False})
        if observed is not None:
            if observed <= due and not cflags.get("breached"):
                cflags["met"] = True
            elif observed > due:
                cflags["breached"] = True
            continue
        remaining = cal.business_minutes_between(effective_now, due)
        warn_at = target * (100 - row.warning_pct) / 100.0
        if effective_now >= due:
            if not cflags.get("breached"):
                cflags["breached"] = True
                _event(db, bug.id, bug.project_id, row.policy_id, clock, "breach", {"due": due.isoformat()}, key=f"sla:{bug.id}:{clock}:breach")
                emitted.append({"clock": clock, "type": "breach"})
            if not cflags.get("escalated"):
                cflags["escalated"] = True
                _event(db, bug.id, bug.project_id, row.policy_id, clock, "escalated", {"due": due.isoformat()}, key=f"sla:{bug.id}:{clock}:escalated")
                _notify(db, "sla_breached", bug, row, f"SLA breached: {bug.public_key}", f"{clock} clock breached", key=f"sla:{bug.id}:{clock}:breach")
                emitted.append({"clock": clock, "type": "escalated"})
        elif remaining <= warn_at and not cflags.get("warned"):
            cflags["warned"] = True
            _event(db, bug.id, bug.project_id, row.policy_id, clock, "warning", {"remaining_minutes": remaining}, key=f"sla:{bug.id}:{clock}:warning")
            _notify(db, "sla_warning", bug, row, f"SLA warning: {bug.public_key}", f"{clock} clock at {row.warning_pct}%", key=f"sla:{bug.id}:{clock}:warning")
            emitted.append({"clock": clock, "type": "warning"})

    row.flags_json = flags
    row.updated_at = now
    if row.is_paused:
        row.state = "paused"
    elif any(v.get("breached") for v in flags.values()):
        row.state = "breached"
    else:
        pending = [c for c in ("first_response", "triage", "resolution") if _clock_due(row, c) and _clock_observed(row, c) is None]
        row.state = "met" if not pending else "running"
    return flags


def compliance(row: BugSla) -> dict:
    """Explainable per-clock compliance summary for reporting."""
    out: dict = {}
    for clock in ("first_response", "triage", "resolution"):
        due = _clock_due(row, clock)
        target = _clock_target(row, clock)
        observed = _clock_observed(row, clock)
        if due is None or not target:
            continue
        info = {"target_minutes": target, "due": due.isoformat(), "observed": observed.isoformat() if observed else None}
        if observed is not None:
            info["met"] = observed <= due
            info["actual_business_minutes"] = _calendar_for(row).business_minutes_between(row.started_at, observed)
        else:
            info["met"] = None
        out[clock] = info
    return out


def log_sla_audit(db: Session, actor_id: int | None, bug: Bug, action: str, changes: dict) -> None:
    try:
        from app.services.audit import log_audit

        log_audit(db, actor_id=actor_id, entity_type="sla", entity_id=bug.id, action=action, project_id=bug.project_id, changes=changes)
    except Exception:  # pragma: no cover - audit must never block SLA
        pass
