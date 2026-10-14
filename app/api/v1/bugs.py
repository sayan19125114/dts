"""Defect lifecycle endpoints."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.deps import AuthContext, get_auth_context, get_project_role, require_project_access
from app.core.permissions import (
    can_archive_bug,
    can_assign,
    can_create_bug,
    can_edit_bug,
    can_read_project,
    can_transition,
)
from app.db.models import Bug, BugHistory, CustomFieldDef, CustomFieldValue, Project, ProjectMember, User
from app.db.session import get_db, get_session_factory
from app.services.audit import log_audit
from app.services.workflow import validate_transition as wf_validate
from app.schemas.schemas import (
    ALLOWED_TRANSITIONS,
    SORT_ALLOWLIST_BUGS,
    BugAssignIn,
    BugCreateIn,
    BugListOut,
    BugOut,
    BugTransitionIn,
    BugUpdateIn,
    HistoryOut,
)

router = APIRouter()
log = logging.getLogger(__name__)


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _bug_out(b: Bug) -> BugOut:
    return BugOut.model_validate(b)


def _history(db: Session, bug_id: int, actor_id: int | None, event_type: str, changes: dict):
    # exclude secrets - only safe fields passed by callers
    h = BugHistory(bug_id=bug_id, actor_id=actor_id, event_type=event_type, changes_json=changes, created_at=_now())
    db.add(h)


def _save_custom_fields(db: Session, bug: Bug, custom: dict | None) -> None:
    if not custom:
        return
    try:
        from app.core.config import get_settings as _gs
        if not _gs().feature_custom_fields:
            return
    except Exception:
        pass
    defs = {d.key: d for d in db.execute(select(CustomFieldDef).where(CustomFieldDef.project_id == bug.project_id)).scalars().all()}
    for k, v in custom.items():
        d = defs.get(k)
        if d is None:
            raise HTTPException(status_code=400, detail=f"Unknown custom field: {k}")
        val = str(v)[:2000]
        if d.required and not val.strip():
            raise HTTPException(status_code=422, detail=f"Custom field {k} required")
        if d.field_type == "select" and d.options_json and val not in list(d.options_json):
            raise HTTPException(status_code=422, detail=f"Invalid option for {k}")
        existing = db.execute(
            select(CustomFieldValue).where(CustomFieldValue.bug_id == bug.id, CustomFieldValue.field_id == d.id)
        ).scalar_one_or_none()
        if existing:
            existing.value = val
        else:
            db.add(CustomFieldValue(bug_id=bug.id, field_id=d.id, value=val))


def _notify(db: Session, event_type: str, actor_id: int | None, bug: Bug, title: str, body: str = "", extra: str = "") -> None:
    try:
        from sqlalchemy.orm import sessionmaker as _sm

        from app.services.notifications import emit as _emit

        try:
            engine = db.get_bind()
        except Exception:
            engine = None
        factory = _sm(bind=engine) if engine is not None else get_session_factory()
        _emit(factory, event_type=event_type, actor_id=actor_id, bug_id=bug.id, project_id=bug.project_id, title=title, body=body, extra=extra or title)
    except Exception:
        log.exception("notify dispatch failed (non-fatal)")


def _ensure_archived_policy(project: Project, write: bool, ctx: AuthContext, role: str | None):
    if project.status == "archived" and write:
        # archived projects are read-only for ordinary users; admins/managers can view and restore, but defect edits blocked
        # Enforce: block all defect writes while archived
        raise HTTPException(status_code=409, detail="Project is archived; defect edits blocked until restored")


@router.get("/projects/{project_id}/bugs", response_model=BugListOut)
def list_bugs(
    project_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    severity: Optional[str] = None,
    priority: Optional[str] = None,
    type: Optional[str] = None,
    reporter_id: Optional[int] = None,
    assignee_id: Optional[int] = None,
    q: Optional[str] = None,
    sort: str = Query("-updated_at"),
    date_from: Optional[dt.date] = None,
    date_to: Optional[dt.date] = None,
    include_archived: bool = False,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    project, role = require_project_access(project_id, ctx, db)
    # sort allowlist
    direction = "desc" if sort.startswith("-") else "asc"
    field = sort.lstrip("+-")
    if field not in SORT_ALLOWLIST_BUGS:
        raise HTTPException(status_code=400, detail=f"Invalid sort field. Allowed: {sorted(SORT_ALLOWLIST_BUGS)}")
    col = getattr(Bug, field, None)
    if col is None:
        raise HTTPException(status_code=400, detail="Invalid sort field")
    order = col.desc() if direction == "desc" else col.asc()

    stmt = select(Bug).where(Bug.project_id == project_id)
    count_stmt = select(func.count()).select_from(Bug).where(Bug.project_id == project_id)
    if not include_archived:
        stmt = stmt.where(Bug.is_archived == False)  # noqa
        count_stmt = count_stmt.where(Bug.is_archived == False)  # noqa
    if status:
        stmt = stmt.where(Bug.status == status)
        count_stmt = count_stmt.where(Bug.status == status)
    if severity:
        stmt = stmt.where(Bug.severity == severity)
        count_stmt = count_stmt.where(Bug.severity == severity)
    if priority:
        stmt = stmt.where(Bug.priority == priority)
        count_stmt = count_stmt.where(Bug.priority == priority)
    if type:
        stmt = stmt.where(Bug.type == type)
        count_stmt = count_stmt.where(Bug.type == type)
    if reporter_id:
        stmt = stmt.where(Bug.reporter_id == reporter_id)
        count_stmt = count_stmt.where(Bug.reporter_id == reporter_id)
    if assignee_id:
        stmt = stmt.where(Bug.assignee_id == assignee_id)
        count_stmt = count_stmt.where(Bug.assignee_id == assignee_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Bug.title.ilike(like), Bug.description.ilike(like), Bug.public_key.ilike(like)))
        count_stmt = count_stmt.where(or_(Bug.title.ilike(like), Bug.description.ilike(like), Bug.public_key.ilike(like)))
    if date_from:
        dt_from = dt.datetime.combine(date_from, dt.time.min).replace(tzinfo=dt.timezone.utc)
        stmt = stmt.where(Bug.created_at >= dt_from)
        count_stmt = count_stmt.where(Bug.created_at >= dt_from)
    if date_to:
        dt_to = dt.datetime.combine(date_to, dt.time.max).replace(tzinfo=dt.timezone.utc)
        stmt = stmt.where(Bug.created_at <= dt_to)
        count_stmt = count_stmt.where(Bug.created_at <= dt_to)
    total = db.execute(count_stmt).scalar_one()
    stmt = stmt.order_by(order).offset((page - 1) * page_size).limit(page_size)
    items = db.execute(stmt).scalars().all()
    return BugListOut(items=[_bug_out(b) for b in items], total=total, page=page, page_size=page_size)


@router.post("/projects/{project_id}/bugs", response_model=BugOut, status_code=201)
def create_bug(project_id: int, payload: BugCreateIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    project, role = require_project_access(project_id, ctx, db)
    _ensure_archived_policy(project, True, ctx, role)
    if not can_create_bug(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Not permitted to create defects")
    # assignee must be active member
    if payload.assignee_id is not None:
        assignee = db.get(User, payload.assignee_id)
        if assignee is None or not assignee.is_active:
            raise HTTPException(status_code=400, detail="Assignee must be an active user")
        mem = db.execute(
            select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == payload.assignee_id)
        ).scalar_one_or_none()
        if mem is None:
            raise HTTPException(status_code=400, detail="Assignee must be a member of the same project")
    # safe sequence generation inside transaction: lock project row via update of counter
    # Use project.bug_counter increment
    try:
        # re-fetch with lock (sqlite: transaction serializes writes)
        proj = db.get(Project, project_id)
        assert proj is not None
        proj.bug_counter = (proj.bug_counter or 0) + 1
        seq = proj.bug_counter
        public_key = f"{proj.project_key}-{seq:04d}"
        db.flush()
        bug = Bug(
            project_id=project_id,
            sequence_number=seq,
            public_key=public_key,
            title=payload.title.strip(),
            description=payload.description,
            steps_to_reproduce=payload.steps_to_reproduce or "",
            expected_result=payload.expected_result or "",
            actual_result=payload.actual_result or "",
            environment=payload.environment or "",
            severity=payload.severity,
            priority=payload.priority,
            status="Open",
            type=payload.type,
            reporter_id=ctx.user.id,
            assignee_id=payload.assignee_id,
            resolution=payload.resolution or "",
            due_date=payload.due_date,
            version=1,
            component=payload.component,
            affected_version=payload.affected_version,
            fixed_version=payload.fixed_version,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(bug)
        db.flush()
        _history(db, bug.id, ctx.user.id, "created", {"public_key": public_key, "title": bug.title})
        _save_custom_fields(db, bug, payload.custom_fields)
        log_audit(db, actor_id=ctx.user.id, entity_type="bug", entity_id=bug.id, action="created", project_id=project_id, changes={"public_key": public_key})
        db.commit()
    except IntegrityError as e:
        db.rollback()
        log.warning("bug creation conflict: %s", e)
        raise HTTPException(status_code=409, detail="Duplicate defect key, retry")
    db.refresh(bug)
    _notify(db, "created", ctx.user.id, bug, f"{bug.public_key} created", bug.title)
    if bug.assignee_id:
        _notify(db, "assigned", ctx.user.id, bug, f"{bug.public_key} assigned", bug.title, extra=f"assign:{bug.assignee_id}")
    return _bug_out(bug)


def _load_bug_for_user(bug_id: int, ctx: AuthContext, db: Session) -> tuple[Bug, Project, str | None]:
    bug = db.get(Bug, bug_id)
    if bug is None:
        raise HTTPException(status_code=404, detail="Defect not found")
    project = db.get(Project, bug.project_id)
    assert project is not None
    if ctx.user.global_role == "admin":
        role = get_project_role(db, ctx.user.id, project.id) or "manager"
        return bug, project, role
    role = get_project_role(db, ctx.user.id, project.id)
    if role is None:
        raise HTTPException(status_code=404, detail="Defect not found")
    return bug, project, role


@router.get("/bugs/{bug_id}", response_model=BugOut)
def get_bug(bug_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    return _bug_out(bug)


@router.patch("/bugs/{bug_id}", response_model=BugOut)
def update_bug(bug_id: int, payload: BugUpdateIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    _ensure_archived_policy(project, True, ctx, role)
    if bug.is_archived:
        raise HTTPException(status_code=409, detail="Defect is archived")
    is_reporter = bug.reporter_id == ctx.user.id
    is_assignee = bug.assignee_id == ctx.user.id
    if not can_edit_bug(ctx.user.global_role, role, is_reporter, is_assignee, bug.status):
        raise HTTPException(status_code=403, detail="Not permitted to edit this defect")
    if payload.version != bug.version:
        raise HTTPException(status_code=409, detail="Version conflict; refresh and retry")
    changes: dict = {}
    for field in ("title", "description", "steps_to_reproduce", "expected_result", "actual_result", "environment", "severity", "priority", "type", "resolution", "due_date", "component", "affected_version", "fixed_version"):
        new_val = getattr(payload, field, None)
        if new_val is not None:
            old_val = getattr(bug, field)
            # normalize date comparison
            if old_val != new_val:
                changes[field] = {"old": str(old_val) if old_val is not None else None, "new": str(new_val)}
                setattr(bug, field, new_val.strip() if isinstance(new_val, str) and field == "title" else new_val)
    if payload.custom_fields:
        _save_custom_fields(db, bug, payload.custom_fields)
        changes["custom_fields"] = {"new": str(sorted(payload.custom_fields.keys()))}
    if not changes:
        return _bug_out(bug)
    bug.version += 1
    bug.updated_at = _now()
    _history(db, bug.id, ctx.user.id, "updated", changes)
    log_audit(db, actor_id=ctx.user.id, entity_type="bug", entity_id=bug.id, action="updated", project_id=bug.project_id, changes={k: str(v)[:200] for k, v in changes.items()})
    db.commit()
    db.refresh(bug)
    return _bug_out(bug)


@router.post("/bugs/{bug_id}/transition", response_model=BugOut)
def transition_bug(bug_id: int, payload: BugTransitionIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    _ensure_archived_policy(project, True, ctx, role)
    if bug.is_archived:
        raise HTTPException(status_code=409, detail="Defect is archived")
    ok, allowed = wf_validate(db, bug.project_id, bug.status, payload.to_status)
    if not ok:
        raise HTTPException(status_code=409, detail=f"Invalid transition from {bug.status}. Allowed: {allowed}")
    # reopen reason required when leaving a resolved state
    if bug.status in ("Fixed", "Ready for Test", "Closed", "Rejected") and payload.to_status in ("Reopened", "In Progress", "Open"):
        if not (payload.reason or "").strip():
            raise HTTPException(status_code=422, detail="A reopen reason is required for this transition")
    is_reporter = bug.reporter_id == ctx.user.id
    is_assignee = bug.assignee_id == ctx.user.id
    if not can_transition(ctx.user.global_role, role, is_reporter, is_assignee, bug.status, payload.to_status):
        raise HTTPException(status_code=403, detail="Not permitted for this transition")
    if payload.version is not None and payload.version != bug.version:
        raise HTTPException(status_code=409, detail="Version conflict")
    old = bug.status
    bug.status = payload.to_status
    bug.version += 1
    bug.updated_at = _now()
    hist_changes: dict = {"old": old, "new": payload.to_status}
    if payload.reason:
        hist_changes["reason"] = payload.reason.strip()[:500]
    _history(db, bug.id, ctx.user.id, "status_changed", hist_changes)
    log_audit(db, actor_id=ctx.user.id, entity_type="bug", entity_id=bug.id, action="status_changed", project_id=bug.project_id, changes=hist_changes)
    db.commit()
    db.refresh(bug)
    _notify(db, "status_changed", ctx.user.id, bug, f"{bug.public_key} → {bug.status}", (payload.reason or "")[:500], extra=f"{old}->{payload.to_status}")
    return _bug_out(bug)


@router.post("/bugs/{bug_id}/assign", response_model=BugOut)
def assign_bug(bug_id: int, payload: BugAssignIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    _ensure_archived_policy(project, True, ctx, role)
    if bug.is_archived:
        raise HTTPException(status_code=409, detail="Defect is archived")
    if not can_assign(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Only managers/admins can assign")
    if payload.version is not None and payload.version != bug.version:
        raise HTTPException(status_code=409, detail="Version conflict")
    if payload.assignee_id is not None:
        u = db.get(User, payload.assignee_id)
        if u is None or not u.is_active:
            raise HTTPException(status_code=400, detail="Assignee must be active")
        mem = db.execute(
            select(ProjectMember).where(ProjectMember.project_id == bug.project_id, ProjectMember.user_id == payload.assignee_id)
        ).scalar_one_or_none()
        if mem is None:
            raise HTTPException(status_code=400, detail="Assignee must be a member of the same project")
    old = bug.assignee_id
    bug.assignee_id = payload.assignee_id
    bug.version += 1
    bug.updated_at = _now()
    _history(db, bug.id, ctx.user.id, "assigned", {"old": old, "new": payload.assignee_id})
    log_audit(db, actor_id=ctx.user.id, entity_type="bug", entity_id=bug.id, action="assigned", project_id=bug.project_id, changes={"old": str(old), "new": str(payload.assignee_id)})
    db.commit()
    db.refresh(bug)
    _notify(db, "assigned", ctx.user.id, bug, f"{bug.public_key} assigned", "", extra=f"assign:{payload.assignee_id}")
    return _bug_out(bug)


@router.post("/bugs/{bug_id}/archive")
def archive_bug(bug_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    if not can_archive_bug(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Not permitted")
    bug.is_archived = True
    bug.archived_at = _now()
    bug.updated_at = _now()
    bug.version += 1
    _history(db, bug.id, ctx.user.id, "archived", {})
    log_audit(db, actor_id=ctx.user.id, entity_type="bug", entity_id=bug.id, action="archived", project_id=bug.project_id)
    db.commit()
    return {"ok": True}


@router.post("/bugs/{bug_id}/restore")
def restore_bug(bug_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    if not can_archive_bug(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Not permitted")
    bug.is_archived = False
    bug.archived_at = None
    bug.updated_at = _now()
    bug.version += 1
    _history(db, bug.id, ctx.user.id, "restored", {})
    log_audit(db, actor_id=ctx.user.id, entity_type="bug", entity_id=bug.id, action="restored", project_id=bug.project_id)
    db.commit()
    return {"ok": True}


@router.get("/bugs/{bug_id}/history", response_model=list[HistoryOut])
def get_history(
    bug_id: int,
    page: int = 1,
    page_size: int = Query(50, le=100),
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    stmt = (
        select(BugHistory).where(BugHistory.bug_id == bug_id).order_by(BugHistory.created_at.asc()).offset((page - 1) * page_size).limit(page_size)
    )
    rows = db.execute(stmt).scalars().all()
    return [HistoryOut(id=h.id, bug_id=h.bug_id, actor_id=h.actor_id, event_type=h.event_type, changes_json=h.changes_json or {}, created_at=h.created_at) for h in rows]
