"""Workflows, custom fields, links, bulk (Phase 2). All additive + permission-checked."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Optional

from app.api.v1.bugs import _history, _load_bug_for_user
from app.api.v1.deps import AuthContext, get_auth_context, require_project_access
from app.core.config import get_settings
from app.core.permissions import can_archive_bug, can_assign, can_edit_bug
from app.db.models import Bug, BugLink, CustomFieldDef, CustomFieldValue, ProjectWorkflow, utcnow
from app.db.session import get_db, get_session_factory
from app.schemas.schemas import ALLOWED_TRANSITIONS
from app.services import sla as _sla_service
from app.services.audit import log_audit

router = APIRouter()


def _now():
    return dt.datetime.now(dt.timezone.utc)


# ---- workflows ----
class WorkflowPutIn(BaseModel):
    workflow: dict


@router.get("/projects/{project_id}/workflow", response_model=dict)
def get_workflow(project_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    project, role = require_project_access(project_id, ctx, db)
    custom = db.execute(select(ProjectWorkflow).where(ProjectWorkflow.project_id == project_id)).scalar_one_or_none()
    if custom and get_settings().feature_custom_workflows:
        return {"project_id": project_id, "workflow": custom.workflow_json, "custom": True, "version": custom.version}
    return {"project_id": project_id, "workflow": dict(ALLOWED_TRANSITIONS), "custom": False, "version": 1}


@router.put("/projects/{project_id}/workflow", response_model=dict)
def put_workflow(project_id: int, payload: WorkflowPutIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    from app.core.permissions import can_manage_project

    project, role = require_project_access(project_id, ctx, db)
    if not can_manage_project(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Not permitted")
    if not get_settings().feature_custom_workflows:
        raise HTTPException(status_code=403, detail="Custom workflows disabled by feature flag")
    # validate: keys subset of known statuses, values subset of known statuses
    known = set(ALLOWED_TRANSITIONS.keys())
    for k, v in payload.workflow.items():
        if k not in known:
            raise HTTPException(status_code=422, detail=f"Unknown status: {k}")
        if not isinstance(v, list) or not all(x in known for x in v):
            raise HTTPException(status_code=422, detail=f"Invalid targets for {k}")
    rec = db.execute(select(ProjectWorkflow).where(ProjectWorkflow.project_id == project_id)).scalar_one_or_none()
    if rec is None:
        rec = ProjectWorkflow(project_id=project_id, workflow_json=payload.workflow, version=1, created_at=_now(), updated_at=_now())
        db.add(rec)
    else:
        rec.workflow_json = payload.workflow
        rec.version += 1
        rec.updated_at = _now()
    log_audit(db, actor_id=ctx.user.id, entity_type="project", entity_id=project_id, action="workflow_updated", project_id=project_id)
    db.commit()
    return {"ok": True}


# ---- custom fields ----
class FieldDefIn(BaseModel):
    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=120)
    field_type: str = "text"
    required: bool = False
    options: list[str] = []


@router.get("/projects/{project_id}/custom-fields", response_model=list[dict])
def list_fields(project_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    require_project_access(project_id, ctx, db)
    rows = db.execute(select(CustomFieldDef).where(CustomFieldDef.project_id == project_id)).scalars().all()
    return [{"id": r.id, "key": r.key, "label": r.label, "field_type": r.field_type, "required": r.required, "options": r.options_json or []} for r in rows]


@router.post("/projects/{project_id}/custom-fields", response_model=dict, status_code=201)
def create_field(project_id: int, payload: FieldDefIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    from app.core.permissions import can_manage_project

    project, role = require_project_access(project_id, ctx, db)
    if not can_manage_project(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Not permitted")
    if payload.field_type not in ("text", "select", "number", "date"):
        raise HTTPException(status_code=422, detail="Invalid field type")
    if db.execute(select(CustomFieldDef).where(CustomFieldDef.project_id == project_id, CustomFieldDef.key == payload.key)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Field key exists")
    r = CustomFieldDef(project_id=project_id, key=payload.key, label=payload.label, field_type=payload.field_type, required=payload.required, options_json=payload.options or [], created_at=_now())
    db.add(r)
    db.commit()
    return {"ok": True, "id": r.id}


@router.get("/bugs/{bug_id}/custom-fields", response_model=list[dict])
def get_bug_fields(bug_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    rows = db.execute(select(CustomFieldValue, CustomFieldDef).join(CustomFieldDef, CustomFieldDef.id == CustomFieldValue.field_id).where(CustomFieldValue.bug_id == bug_id)).all()
    return [{"key": d.key, "label": d.label, "value": v.value} for v, d in rows]


# ---- links ----
class LinkIn(BaseModel):
    to_bug_id: int
    link_type: str = Field(pattern=r"^(duplicates|blocks|relates|parent_child|regression)$")


@router.get("/bugs/{bug_id}/links", response_model=list[dict])
def list_links(bug_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    rows = db.execute(select(BugLink).where((BugLink.from_bug_id == bug_id) | (BugLink.to_bug_id == bug_id))).scalars().all()
    out = []
    for l in rows:
        # only include links where user can read the other side
        other = l.to_bug_id if l.from_bug_id == bug_id else l.from_bug_id
        try:
            _load_bug_for_user(other, ctx, db)
        except HTTPException:
            continue
        out.append({"id": l.id, "from_bug_id": l.from_bug_id, "to_bug_id": l.to_bug_id, "link_type": l.link_type})
    return out


@router.post("/bugs/{bug_id}/links", response_model=dict, status_code=201)
def create_link(bug_id: int, payload: LinkIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    if bug.is_archived or project.status == "archived":
        raise HTTPException(status_code=409, detail="Archived; links blocked")
    other, _, _ = _load_bug_for_user(payload.to_bug_id, ctx, db)
    if payload.to_bug_id == bug_id:
        raise HTTPException(status_code=422, detail="Cannot link defect to itself")
    # need edit perm on source
    is_rep = bug.reporter_id == ctx.user.id
    is_asg = bug.assignee_id == ctx.user.id
    if not can_edit_bug(ctx.user.global_role, role, is_rep, is_asg, bug.status) and ctx.user.global_role != "admin" and role != "manager":
        raise HTTPException(status_code=403, detail="Not permitted")
    if db.execute(select(BugLink).where(BugLink.from_bug_id == bug_id, BugLink.to_bug_id == payload.to_bug_id, BugLink.link_type == payload.link_type)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Link exists")
    l = BugLink(from_bug_id=bug_id, to_bug_id=payload.to_bug_id, link_type=payload.link_type, created_by=ctx.user.id, created_at=_now())
    db.add(l)
    db.flush()
    _history(db, bug.id, ctx.user.id, "link_added", {"to": payload.to_bug_id, "type": payload.link_type})
    _history(db, payload.to_bug_id, ctx.user.id, "link_added", {"from": bug_id, "type": payload.link_type})
    log_audit(db, actor_id=ctx.user.id, entity_type="bug", entity_id=bug_id, action="link_added", project_id=bug.project_id, changes={"to": str(payload.to_bug_id)})
    db.commit()
    return {"ok": True, "id": l.id}


@router.delete("/links/{link_id}")
def delete_link(link_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    l = db.get(BugLink, link_id)
    if l is None:
        raise HTTPException(status_code=404, detail="Not found")
    bug, project, role = _load_bug_for_user(l.from_bug_id, ctx, db)
    is_rep = bug.reporter_id == ctx.user.id
    is_asg = bug.assignee_id == ctx.user.id
    if not can_edit_bug(ctx.user.global_role, role, is_rep, is_asg, bug.status) and ctx.user.global_role != "admin" and role != "manager":
        raise HTTPException(status_code=403, detail="Not permitted")
    db.delete(l)
    db.commit()
    return {"ok": True}


@router.post("/bugs/{bug_id}/mark-duplicate", response_model=dict)
def mark_duplicate(bug_id: int, payload: dict, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    canonical = int(payload.get("canonical_bug_id", 0))
    if not canonical or canonical == bug_id:
        raise HTTPException(status_code=422, detail="canonical_bug_id required")
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    other, _, _ = _load_bug_for_user(canonical, ctx, db)
    is_rep = bug.reporter_id == ctx.user.id
    is_asg = bug.assignee_id == ctx.user.id
    if not can_edit_bug(ctx.user.global_role, role, is_rep, is_asg, bug.status) and ctx.user.global_role != "admin" and role != "manager":
        raise HTTPException(status_code=403, detail="Not permitted")
    if db.execute(select(BugLink).where(BugLink.from_bug_id == bug_id, BugLink.to_bug_id == canonical, BugLink.link_type == "duplicates")).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Already marked duplicate")
    db.add(BugLink(from_bug_id=bug_id, to_bug_id=canonical, link_type="duplicates", created_by=ctx.user.id, created_at=_now()))
    bug.status = "Rejected"
    bug.resolution = f"Duplicate of {other.public_key}"
    bug.version += 1
    bug.updated_at = _now()
    _history(db, bug.id, ctx.user.id, "status_changed", {"old": "Open", "new": "Rejected", "reason": f"duplicate of {other.public_key}"})
    log_audit(db, actor_id=ctx.user.id, entity_type="bug", entity_id=bug_id, action="marked_duplicate", project_id=bug.project_id, changes={"canonical": str(canonical)})
    db.commit()
    return {"ok": True}


# ---- bulk ----
class BulkIn(BaseModel):
    bug_ids: list[int] = Field(max_length=100)
    action: str
    assignee_id: Optional[int] = None
    priority: Optional[str] = None
    to_status: Optional[str] = None
    reason: Optional[str] = None


@router.post("/projects/{project_id}/bugs/bulk", response_model=dict)
def bulk(project_id: int, payload: BulkIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    from app.core.config import get_settings as _gs

    if not _gs().feature_bulk_ops:
        raise HTTPException(status_code=403, detail="Bulk disabled by flag")
    project, role = require_project_access(project_id, ctx, db)
    if project.status == "archived":
        raise HTTPException(status_code=409, detail="Project archived")
    if payload.action not in ("assign", "set_priority", "transition"):
        raise HTTPException(status_code=422, detail="Invalid bulk action")
    results: list[dict] = []
    for bid in payload.bug_ids[:100]:
        bug = db.get(Bug, bid)
        if bug is None or bug.project_id != project_id:
            results.append({"id": bid, "ok": False, "error": "Not found in project"})
            continue
        try:
            if payload.action == "assign":
                if not can_assign(ctx.user.global_role, role):
                    raise HTTPException(status_code=403, detail="Not permitted")
                bug.assignee_id = payload.assignee_id
                bug.version += 1
                bug.updated_at = _now()
                _history(db, bug.id, ctx.user.id, "assigned", {"bulk": True, "new": payload.assignee_id})
            elif payload.action == "set_priority":
                is_rep = bug.reporter_id == ctx.user.id
                is_asg = bug.assignee_id == ctx.user.id
                if not can_edit_bug(ctx.user.global_role, role, is_rep, is_asg, bug.status):
                    raise HTTPException(status_code=403, detail="Not permitted")
                from app.schemas.schemas import VALID_PRIORITIES

                if payload.priority not in VALID_PRIORITIES:
                    raise HTTPException(status_code=422, detail="Invalid priority")
                bug.priority = payload.priority
                bug.version += 1
                bug.updated_at = _now()
                _history(db, bug.id, ctx.user.id, "updated", {"priority": payload.priority, "bulk": True})
            else:
                from app.services.workflow import validate_transition as _vt

                ok, allowed = _vt(db, project_id, bug.status, payload.to_status or "")
                if not ok:
                    raise HTTPException(status_code=409, detail=f"Invalid transition; allowed {allowed}")
                from app.core.permissions import can_transition as _ct

                if not _ct(ctx.user.global_role, role, bug.reporter_id == ctx.user.id, bug.assignee_id == ctx.user.id, bug.status, payload.to_status or ""):
                    raise HTTPException(status_code=403, detail="Not permitted")
                old_status = bug.status
                bug.status = payload.to_status or bug.status
                bug.version += 1
                bug.updated_at = _now()
                if bug.status in _sla_service.RESOLVED_STATES and bug.resolved_at is None:
                    bug.resolved_at = bug.updated_at
                _history(db, bug.id, ctx.user.id, "status_changed", {"new": bug.status, "bulk": True, "reason": payload.reason or ""})
                if _gs().feature_sla:
                    try:
                        _sla_service.on_status_change(db, bug, old_status, bug.status, actor_id=ctx.user.id)
                    except Exception:
                        pass
            log_audit(db, actor_id=ctx.user.id, entity_type="bug", entity_id=bug.id, action=f"bulk_{payload.action}", project_id=project_id)
            results.append({"id": bid, "ok": True})
        except HTTPException as e:
            results.append({"id": bid, "ok": False, "error": e.detail})
    db.commit()
    return {"results": results}
