"""Phase 4 release/version management and configurable quality gates.

Gates are advisory by default; they only block a release when the release has
``gates_blocking`` enabled. A manager can override with an audited ``force``.
"""
from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import AuthContext, get_auth_context, require_project_access
from app.core.config import get_settings
from app.core.permissions import can_manage_project
from app.db.models import Bug, Release, ReleaseGate, utcnow
from app.db.session import get_db
from app.schemas.schemas import (
    ReleaseBugsIn,
    ReleaseGateIn,
    ReleaseGateOut,
    ReleaseIn,
    ReleaseOut,
    ReleaseReadinessOut,
    ReleaseUpdateIn,
    ReleaseWaiveIn,
)
from app.services import sla as sla_service

router = APIRouter()
log = logging.getLogger(__name__)

RESOLVED = set(sla_service.RESOLVED_STATES)


def _require_feature() -> None:
    if not get_settings().feature_releases:
        raise HTTPException(status_code=404, detail="Not found")


def _require_manager(project_id: int, ctx: AuthContext, db: Session):
    project, role = require_project_access(project_id, ctx, db)
    if not can_manage_project(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Manager role required")
    return project, role


def _load_release(release_id: int, ctx: AuthContext, db: Session, manager: bool = False):
    rel = db.get(Release, release_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="Release not found")
    project, role = require_project_access(rel.project_id, ctx, db)
    if manager and not can_manage_project(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Manager role required")
    return rel, project, role


def _scoped_bugs(db: Session, rel: Release, scope: str) -> list[Bug]:
    stmt = select(Bug).where(Bug.is_archived.is_(False))
    if scope == "project":
        stmt = stmt.where(Bug.project_id == rel.project_id)
    else:
        stmt = stmt.where((Bug.fixed_release_id == rel.id) | (Bug.affected_release_id == rel.id))
    return db.execute(stmt).scalars().all()


def _evaluate_gate(db: Session, rel: Release, gate: ReleaseGate) -> ReleaseGate:
    cfg = gate.config_json or {}
    scope = cfg.get("scope", "release")
    bugs = _scoped_bugs(db, rel, scope)
    open_bugs = [b for b in bugs if b.status not in RESOLVED]
    detail: dict = {"scope": scope, "evaluated_scope_size": len(bugs)}

    if gate.rule_type in ("unresolved_critical", "unresolved_high"):
        sev = "Critical" if gate.rule_type == "unresolved_critical" else "High"
        max_allowed = int(cfg.get("max", 0))
        count = len([b for b in open_bugs if b.severity == sev])
        detail.update({"severity": sev, "open_count": count, "max": max_allowed})
        gate.passed = count <= max_allowed
    elif gate.rule_type == "max_open_by_severity":
        sev = cfg.get("severity", "Medium")
        max_allowed = int(cfg.get("max", 10))
        count = len([b for b in open_bugs if b.severity == sev])
        detail.update({"severity": sev, "open_count": count, "max": max_allowed})
        gate.passed = count <= max_allowed
    elif gate.rule_type == "sla_compliance":
        min_pct = float(cfg.get("min_pct", 90))
        from app.db.models import BugSla

        bug_ids = [b.id for b in bugs]
        rows = db.execute(select(BugSla).where(BugSla.bug_id.in_(bug_ids))).scalars().all() if bug_ids else []
        met = missed = 0
        for row in rows:
            for info in sla_service.compliance(row).values():
                if info.get("met") is True:
                    met += 1
                elif info.get("met") is False:
                    missed += 1
        denom = met + missed
        pct = (100 * met / denom) if denom else None
        detail.update({"met": met, "missed": missed, "compliance_pct": round(pct, 2) if pct is not None else None, "min_pct": min_pct})
        gate.passed = None if pct is None else (pct >= min_pct)
    else:
        gate.passed = None
        detail["error"] = "unknown rule"

    gate.detail_json = detail
    gate.evaluated_at = utcnow()
    return gate


def _readiness(db: Session, rel: Release) -> dict:
    gates = db.execute(select(ReleaseGate).where(ReleaseGate.release_id == rel.id).order_by(ReleaseGate.id.asc())).scalars().all()
    bugs = _scoped_bugs(db, rel, "release")
    unresolved_by_severity: dict[str, int] = {}
    for b in bugs:
        if b.status not in RESOLVED:
            unresolved_by_severity[b.severity] = unresolved_by_severity.get(b.severity, 0) + 1
    blocking_failures = [g.id for g in gates if g.blocking and g.passed is False and not g.waived]
    advisory_failures = [g.id for g in gates if not g.blocking and g.passed is False and not g.waived]
    would_block = bool(rel.gates_blocking and blocking_failures)
    return {
        "release": rel,
        "gates": gates,
        "blocking_failures": blocking_failures,
        "advisory_failures": advisory_failures,
        "ready": not would_block,
        "would_block": would_block,
        "unresolved_by_severity": unresolved_by_severity,
    }


@router.get("/projects/{project_id}/releases", response_model=list[ReleaseOut])
def list_releases(project_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    require_project_access(project_id, ctx, db)
    return db.execute(select(Release).where(Release.project_id == project_id).order_by(Release.created_at.desc())).scalars().all()


@router.post("/projects/{project_id}/releases", response_model=ReleaseOut, status_code=201)
def create_release(project_id: int, payload: ReleaseIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    _require_manager(project_id, ctx, db)
    exists = db.execute(select(Release).where(Release.project_id == project_id, Release.version == payload.version)).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail="A release with this version already exists")
    rel = Release(
        project_id=project_id,
        name=payload.name,
        version=payload.version,
        description=payload.description,
        planned_date=payload.planned_date,
        actual_date=payload.actual_date,
        release_notes=payload.release_notes,
        gates_blocking=payload.gates_blocking,
        status="released" if payload.actual_date else "planned",
        released_at=utcnow() if payload.actual_date else None,
        created_by=ctx.user.id,
    )
    db.add(rel)
    db.commit()
    db.refresh(rel)
    return rel


@router.get("/releases/{release_id}", response_model=ReleaseOut)
def get_release(release_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    rel, _p, _r = _load_release(release_id, ctx, db)
    return rel


@router.patch("/releases/{release_id}", response_model=ReleaseOut)
def update_release(release_id: int, payload: ReleaseUpdateIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    rel, _p, _r = _load_release(release_id, ctx, db, manager=True)
    for field in ("name", "description", "planned_date", "actual_date", "release_notes", "gates_blocking"):
        val = getattr(payload, field, None)
        if val is not None:
            setattr(rel, field, val)
    if payload.actual_date is not None and rel.released_at is None:
        rel.released_at = utcnow()
    db.commit()
    db.refresh(rel)
    return rel


@router.delete("/releases/{release_id}")
def archive_release(release_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    rel, _p, _r = _load_release(release_id, ctx, db, manager=True)
    rel.status = "archived"
    db.commit()
    return {"ok": True}


@router.get("/releases/{release_id}/readiness", response_model=ReleaseReadinessOut)
def get_readiness(release_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    rel, _p, _r = _load_release(release_id, ctx, db)
    return _readiness(db, rel)


@router.post("/releases/{release_id}/gates", response_model=ReleaseGateOut, status_code=201)
def create_gate(release_id: int, payload: ReleaseGateIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    rel, _p, _r = _load_release(release_id, ctx, db, manager=True)
    gate = ReleaseGate(
        release_id=rel.id,
        name=payload.name,
        rule_type=payload.rule_type,
        config_json=payload.config_json,
        blocking=payload.blocking,
    )
    db.add(gate)
    db.flush()
    _evaluate_gate(db, rel, gate)
    db.commit()
    db.refresh(gate)
    return gate


@router.post("/releases/{release_id}/gates/{gate_id}/evaluate", response_model=ReleaseGateOut)
def evaluate_gate(release_id: int, gate_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    rel, _p, _r = _load_release(release_id, ctx, db, manager=True)
    gate = db.get(ReleaseGate, gate_id)
    if gate is None or gate.release_id != rel.id:
        raise HTTPException(status_code=404, detail="Gate not found")
    _evaluate_gate(db, rel, gate)
    db.commit()
    db.refresh(gate)
    return gate


@router.post("/releases/{release_id}/gates/{gate_id}/waive", response_model=ReleaseGateOut)
def waive_gate(release_id: int, gate_id: int, payload: ReleaseWaiveIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    rel, _p, _r = _load_release(release_id, ctx, db, manager=True)
    gate = db.get(ReleaseGate, gate_id)
    if gate is None or gate.release_id != rel.id:
        raise HTTPException(status_code=404, detail="Gate not found")
    gate.waived = True
    gate.waived_by = ctx.user.id
    gate.waiver_reason = payload.reason
    gate.waived_at = utcnow()
    db.commit()
    db.refresh(gate)
    return gate


@router.delete("/releases/{release_id}/gates/{gate_id}")
def delete_gate(release_id: int, gate_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    rel, _p, _r = _load_release(release_id, ctx, db, manager=True)
    gate = db.get(ReleaseGate, gate_id)
    if gate is None or gate.release_id != rel.id:
        raise HTTPException(status_code=404, detail="Gate not found")
    db.delete(gate)
    db.commit()
    return {"ok": True}


@router.post("/releases/{release_id}/bugs")
def link_bug(release_id: int, payload: ReleaseBugsIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    rel, _p, _r = _load_release(release_id, ctx, db, manager=True)
    bug = db.get(Bug, payload.bug_id)
    if bug is None or bug.project_id != rel.project_id:
        raise HTTPException(status_code=404, detail="Defect not found in this project")
    if payload.relation == "affected":
        bug.affected_release_id = rel.id
    else:
        bug.fixed_release_id = rel.id
    db.commit()
    return {"ok": True, "bug_id": bug.id, "relation": payload.relation}


@router.delete("/releases/{release_id}/bugs/{bug_id}")
def unlink_bug(release_id: int, bug_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    rel, _p, _r = _load_release(release_id, ctx, db, manager=True)
    bug = db.get(Bug, bug_id)
    if bug is None or bug.project_id != rel.project_id:
        raise HTTPException(status_code=404, detail="Defect not found in this project")
    if bug.affected_release_id == rel.id:
        bug.affected_release_id = None
    if bug.fixed_release_id == rel.id:
        bug.fixed_release_id = None
    db.commit()
    return {"ok": True}


@router.post("/releases/{release_id}/release", response_model=ReleaseOut)
def mark_released(
    release_id: int,
    force: bool = False,
    reason: str | None = None,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    _require_feature()
    rel, _p, _r = _load_release(release_id, ctx, db, manager=True)
    # refresh gate evaluations before enforcing
    for gate in db.execute(select(ReleaseGate).where(ReleaseGate.release_id == rel.id)).scalars().all():
        _evaluate_gate(db, rel, gate)
    readiness = _readiness(db, rel)
    if readiness["would_block"]:
        if not force:
            db.commit()  # persist fresh evaluations
            raise HTTPException(status_code=409, detail={"message": "Blocking quality gates failed", "gates": readiness["blocking_failures"]})
        if not (reason or "").strip():
            raise HTTPException(status_code=422, detail="A reason is required to force a release past blocking gates")
    rel.status = "released"
    if rel.actual_date is None:
        rel.actual_date = utcnow().date()
    rel.released_at = utcnow()
    db.commit()
    db.refresh(rel)
    if force:
        from app.services.audit import log_audit

        log_audit(db, actor_id=ctx.user.id, entity_type="release", entity_id=rel.id, action="force_released", project_id=rel.project_id, changes={"reason": (reason or "")[:500], "gates": readiness["blocking_failures"]})
        db.commit()
    return rel


@router.get("/releases/{release_id}/notes")
def release_notes(release_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    _require_feature()
    rel, _p, _r = _load_release(release_id, ctx, db)
    bugs = db.execute(select(Bug).where(Bug.fixed_release_id == rel.id).order_by(Bug.severity, Bug.public_key)).scalars().all()
    lines = [f"# {rel.name} ({rel.version})", "", f"Status: {rel.status}"]
    if rel.actual_date:
        lines.append(f"Released: {rel.actual_date}")
    lines.append("")
    lines.append("## Fixed defects")
    for b in bugs:
        lines.append(f"- {b.public_key} [{b.severity}/{b.priority}] {b.title}")
    if rel.release_notes:
        lines += ["", "## Notes", "", rel.release_notes]
    return {"markdown": "\n".join(lines), "bug_count": len(bugs)}
