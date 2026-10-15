"""Phase 4 SLA endpoints: policy management, per-defect SLA, at-risk, worker trigger."""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import AuthContext, get_auth_context, require_admin, require_project_access
from app.core.config import get_settings
from app.core.permissions import can_manage_project
from app.db.models import Bug, BugSla, ProjectMember, SlaEvent, SlaPolicy
from app.db.session import get_db
from app.schemas.schemas import (
    BugSlaDetailOut,
    BugSlaOut,
    SlaEventOut,
    SlaPolicyIn,
    SlaPolicyOut,
    SlaReapplyIn,
)
from app.services import sla as sla_service

router = APIRouter()
log = logging.getLogger(__name__)


def _require_feature() -> None:
    if not get_settings().feature_sla:
        raise HTTPException(status_code=404, detail="Not found")


def _require_manager(project_id: int, ctx: AuthContext, db: Session):
    project, role = require_project_access(project_id, ctx, db)
    if not can_manage_project(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Manager role required")
    return project, role


def _validate_policy(payload: SlaPolicyIn) -> None:
    try:
        ZoneInfo(payload.timezone)
    except Exception:
        raise HTTPException(status_code=422, detail="Unknown timezone")
    if payload.business_hours is not None:
        if not isinstance(payload.business_hours, dict):
            raise HTTPException(status_code=422, detail="business_hours must be an object keyed by weekday 0-6")
        for dow, intervals in payload.business_hours.items():
            if str(dow) not in {str(i) for i in range(7)}:
                raise HTTPException(status_code=422, detail="business_hours keys must be 0-6")
            if not isinstance(intervals, list):
                raise HTTPException(status_code=422, detail="business_hours values must be lists of [start,end]")
            for iv in intervals:
                if (not isinstance(iv, (list, tuple)) or len(iv) != 2
                        or not all(isinstance(x, int) for x in iv) or not (0 <= iv[0] < iv[1] <= 1440)):
                    raise HTTPException(status_code=422, detail="business_hours interval must be [start,end] minutes within a day")
    if payload.targets is not None and not isinstance(payload.targets, dict):
        raise HTTPException(status_code=422, detail="targets must be an object")
    if payload.escalation_user_id is not None:
        from app.db.models import User

        u = db.get(User, payload.escalation_user_id)
        if u is None or not u.is_active:
            raise HTTPException(status_code=422, detail="escalation_user_id must be an active user")


@router.get("/projects/{project_id}/sla-policies", response_model=list[SlaPolicyOut])
def list_policies(project_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    require_project_access(project_id, ctx, db)
    rows = db.execute(select(SlaPolicy).where(SlaPolicy.project_id == project_id).order_by(SlaPolicy.version.desc())).scalars().all()
    return rows


@router.get("/projects/{project_id}/sla-policies/active", response_model=SlaPolicyOut)
def get_active_policy(project_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    require_project_access(project_id, ctx, db)
    policy = sla_service.get_active_policy(db, project_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="No active SLA policy")
    return policy


@router.post("/projects/{project_id}/sla-policies", response_model=SlaPolicyOut, status_code=201)
def create_policy(project_id: int, payload: SlaPolicyIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    _require_manager(project_id, ctx, db)
    _validate_policy(payload)
    latest = db.execute(select(SlaPolicy).where(SlaPolicy.project_id == project_id).order_by(SlaPolicy.version.desc())).scalars().first()
    version = (latest.version + 1) if latest else 1
    # deactivate previous versions
    for row in db.execute(select(SlaPolicy).where(SlaPolicy.project_id == project_id, SlaPolicy.is_active.is_(True))).scalars().all():
        row.is_active = False
    policy = SlaPolicy(
        project_id=project_id,
        name=payload.name,
        version=version,
        is_active=True,
        timezone=payload.timezone,
        business_hours=payload.business_hours if payload.business_hours is not None else sla_service.DEFAULT_BUSINESS_HOURS,
        holidays=payload.holidays if payload.holidays is not None else [],
        targets=payload.targets if payload.targets is not None else sla_service.DEFAULT_TARGETS,
        pause_statuses=payload.pause_statuses if payload.pause_statuses is not None else [],
        escalation_role=payload.escalation_role,
        escalation_user_id=payload.escalation_user_id,
        notes=payload.notes,
        created_by=ctx.user.id,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    log.info("sla policy v%s created for project %s by %s", version, project_id, ctx.user.id)
    return policy


@router.post("/projects/{project_id}/sla-policies/{policy_id}/activate", response_model=SlaPolicyOut)
def activate_policy(project_id: int, policy_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    _require_manager(project_id, ctx, db)
    policy = db.get(SlaPolicy, policy_id)
    if policy is None or policy.project_id != project_id:
        raise HTTPException(status_code=404, detail="Policy not found")
    for row in db.execute(select(SlaPolicy).where(SlaPolicy.project_id == project_id, SlaPolicy.is_active.is_(True))).scalars().all():
        row.is_active = False
    policy.is_active = True
    db.commit()
    db.refresh(policy)
    return policy


@router.post("/projects/{project_id}/sla/reapply")
def reapply_policy(project_id: int, payload: SlaReapplyIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    """Explicitly push the active policy onto existing defects (never automatic)."""
    _require_feature()
    _require_manager(project_id, ctx, db)
    policy = sla_service.get_active_policy(db, project_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="No active SLA policy")
    if not payload.apply_to_existing:
        return {"ok": True, "applied": 0, "note": "apply_to_existing=false; no defects changed"}
    stmt = select(Bug).where(Bug.project_id == project_id, Bug.is_archived.is_(False))
    if payload.only_unresolved:
        stmt = stmt.where(Bug.status.notin_(list(sla_service.RESOLVED_STATES)))
    bugs = db.execute(stmt).scalars().all()
    applied = 0
    for bug in bugs:
        sla_service.apply_policy(db, bug, policy, actor_id=ctx.user.id)
        applied += 1
    db.commit()
    return {"ok": True, "applied": applied, "policy_version": policy.version}


@router.get("/projects/{project_id}/sla/at-risk")
def at_risk(project_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    require_project_access(project_id, ctx, db)
    rows = db.execute(select(BugSla).where(BugSla.project_id == project_id)).scalars().all()
    out = []
    for row in rows:
        flags = row.flags_json or {}
        warned = any(v.get("warned") for v in flags.values())
        breached = any(v.get("breached") for v in flags.values()) or row.state == "breached"
        if not (warned or breached):
            continue
        bug = db.get(Bug, row.bug_id)
        item = BugSlaOut.model_validate(row).model_dump()
        item["public_key"] = bug.public_key if bug else None
        item["title"] = bug.title if bug else None
        item["status"] = bug.status if bug else None
        out.append(item)
    out.sort(key=lambda x: (x["state"] != "breached", x["public_key"] or ""))
    return {"items": out, "total": len(out)}


@router.get("/bugs/{bug_id}/sla", response_model=BugSlaDetailOut)
def get_bug_sla(bug_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    from app.api.v1.bugs import _load_bug_for_user

    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    row = db.execute(select(BugSla).where(BugSla.bug_id == bug.id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="No SLA applied to this defect")
    try:
        sla_service.evaluate_bug(db, bug)
        db.commit()
        db.refresh(row)
    except Exception:  # pragma: no cover - read must not fail on evaluation
        db.rollback()
        log.exception("sla evaluate failed on read for bug %s", bug.id)
    events = db.execute(
        select(SlaEvent).where(SlaEvent.bug_id == bug.id).order_by(SlaEvent.created_at.desc(), SlaEvent.id.desc()).limit(100)
    ).scalars().all()
    return BugSlaDetailOut(
        sla=BugSlaOut.model_validate(row),
        events=[SlaEventOut.model_validate(e) for e in events],
        compliance=sla_service.compliance(row),
    )


@router.post("/bugs/{bug_id}/sla/apply", response_model=BugSlaOut)
def apply_bug_sla(bug_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    from app.api.v1.bugs import _load_bug_for_user

    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    if not can_manage_project(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Manager role required")
    policy = sla_service.get_active_policy(db, bug.project_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="No active SLA policy")
    row = sla_service.apply_policy(db, bug, policy, actor_id=ctx.user.id)
    db.commit()
    db.refresh(row)
    return row


@router.post("/admin/sla/run")
def run_sla_worker(ctx: AuthContext = Depends(require_admin), db: Session = Depends(get_db)):
    """Manual, idempotent SLA evaluation pass (also invoked by the scheduler)."""
    _require_feature()
    from app.services.scheduler import run_sla_pass

    result = run_sla_pass(db)
    return {"ok": True, **result}
