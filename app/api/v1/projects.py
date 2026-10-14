"""Project + membership endpoints."""
from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.deps import AuthContext, get_auth_context, get_project_role, require_project_access
from app.core.permissions import can_manage_members, can_manage_project, can_read_project
from app.db.models import BugHistory, Organization, Project, ProjectMember, User
from app.db.session import get_db
from app.services.audit import log_audit
from app.schemas.schemas import (
    MemberAddIn,
    MemberOut,
    MemberUpdateIn,
    ProjectCreateIn,
    ProjectOut,
    ProjectUpdateIn,
)

router = APIRouter()
log = logging.getLogger(__name__)


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _project_out(p: Project, my_role: str | None) -> ProjectOut:
    return ProjectOut(
        id=p.id,
        project_key=p.project_key,
        name=p.name,
        description=p.description,
        status=p.status,
        created_by=p.created_by,
        created_at=p.created_at,
        updated_at=p.updated_at,
        archived_at=p.archived_at,
        my_role=my_role,
    )


@router.get("", response_model=list[ProjectOut])
def list_projects(
    include_archived: bool = False,
    q: str | None = None,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    if ctx.user.global_role == "admin":
        stmt = select(Project)
        if not include_archived:
            stmt = stmt.where(Project.status == "active")
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(Project.name.ilike(like), Project.project_key.ilike(like)))
        stmt = stmt.order_by(Project.project_key)
        projects = db.execute(stmt).scalars().all()
        out = []
        for p in projects:
            role = get_project_role(db, ctx.user.id, p.id) or ("manager" if ctx.user.global_role == "admin" else None)
            out.append(_project_out(p, role))
        return out
    # regular users: only member projects
    stmt = select(Project, ProjectMember.role).join(ProjectMember, ProjectMember.project_id == Project.id).where(
        ProjectMember.user_id == ctx.user.id
    )
    if not include_archived:
        stmt = stmt.where(Project.status == "active")
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Project.name.ilike(like), Project.project_key.ilike(like)))
    stmt = stmt.order_by(Project.project_key)
    rows = db.execute(stmt).all()
    return [_project_out(p, role) for p, role in rows]


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreateIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    if ctx.user.global_role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can create projects")
    p = Project(
        project_key=payload.project_key,
        name=payload.name.strip(),
        description=payload.description or "",
        status="active",
        created_by=ctx.user.id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(p)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Project key already exists")
    db.refresh(p)
    # creator becomes manager
    db.add(ProjectMember(project_id=p.id, user_id=ctx.user.id, role="manager", created_at=_now()))
    # default org assignment (Phase 2.1 additive)
    try:
        org = db.execute(select(Organization)).scalars().first()
        if org is None:
            org = Organization(id=1, name="Default", slug="default", created_at=_now())
            db.add(org)
            db.flush()
        p.org_id = org.id
    except Exception:
        pass
    log_audit(db, actor_id=ctx.user.id, entity_type="project", entity_id=p.id, action="created", project_id=p.id, changes={"key": p.project_key})
    db.commit()
    log.info("project %s created by %s", p.project_key, ctx.user.id)
    return _project_out(p, "manager")


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    project, role = require_project_access(project_id, ctx, db)
    eff_role = role if ctx.user.global_role != "admin" else (get_project_role(db, ctx.user.id, project_id) or "manager")
    # admin without membership still manager-equivalent
    if ctx.user.global_role == "admin" and eff_role is None:
        eff_role = "manager"
    return _project_out(project, eff_role)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int, payload: ProjectUpdateIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
):
    project, role = require_project_access(project_id, ctx, db)
    if not can_manage_project(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Not permitted")
    if project.status == "archived":
        raise HTTPException(status_code=409, detail="Project is archived; restore first")
    if payload.name is not None:
        project.name = payload.name.strip()
    if payload.description is not None:
        project.description = payload.description
    project.updated_at = _now()
    log_audit(db, actor_id=ctx.user.id, entity_type="project", entity_id=project.id, action="updated", project_id=project.id)
    db.commit()
    db.refresh(project)
    return _project_out(project, role)


@router.post("/{project_id}/archive")
def archive_project(project_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    project, role = require_project_access(project_id, ctx, db)
    if not can_manage_project(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Not permitted")
    if project.status == "archived":
        return {"ok": True}
    project.status = "archived"
    project.archived_at = _now()
    project.updated_at = _now()
    log_audit(db, actor_id=ctx.user.id, entity_type="project", entity_id=project.id, action="archived", project_id=project.id)
    db.commit()
    log.info("project %s archived by %s", project.id, ctx.user.id)
    return {"ok": True}


@router.post("/{project_id}/restore")
def restore_project(project_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    project, role = require_project_access(project_id, ctx, db)
    if not can_manage_project(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Not permitted")
    project.status = "active"
    project.archived_at = None
    project.updated_at = _now()
    log_audit(db, actor_id=ctx.user.id, entity_type="project", entity_id=project.id, action="restored", project_id=project.id)
    db.commit()
    return {"ok": True}


# ---- members ----
@router.get("/{project_id}/members", response_model=list[MemberOut])
def list_members(project_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    project, role = require_project_access(project_id, ctx, db)
    rows = db.execute(
        select(ProjectMember, User).join(User, User.id == ProjectMember.user_id).where(ProjectMember.project_id == project_id).order_by(User.username)
    ).all()
    return [MemberOut(user_id=u.id, username=u.username, display_name=u.display_name, role=m.role) for m, u in rows]


@router.post("/{project_id}/members", response_model=MemberOut, status_code=201)
def add_member(project_id: int, payload: MemberAddIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    project, role = require_project_access(project_id, ctx, db)
    if not can_manage_members(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Not permitted")
    # resolve user
    target: User | None = None
    if payload.user_id is not None:
        target = db.get(User, payload.user_id)
    elif payload.username_or_email is not None:
        ident = payload.username_or_email.strip().lower()
        from sqlalchemy import or_

        target = db.execute(select(User).where(or_(User.username == ident, User.email == ident))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not target.is_active:
        raise HTTPException(status_code=400, detail="User is disabled")
    existing = db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == target.id)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Already a member")
    m = ProjectMember(project_id=project_id, user_id=target.id, role=payload.role, created_at=_now())
    db.add(m)
    log_audit(db, actor_id=ctx.user.id, entity_type="member", entity_id=target.id, action="added", project_id=project_id, changes={"role": payload.role})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Already a member")
    return MemberOut(user_id=target.id, username=target.username, display_name=target.display_name, role=payload.role)


@router.patch("/{project_id}/members/{user_id}", response_model=MemberOut)
def update_member(
    project_id: int, user_id: int, payload: MemberUpdateIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)
):
    project, role = require_project_access(project_id, ctx, db)
    if not can_manage_members(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Not permitted")
    m = db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="Membership not found")
    m.role = payload.role
    log_audit(db, actor_id=ctx.user.id, entity_type="member", entity_id=user_id, action="role_changed", project_id=project_id, changes={"role": payload.role})
    db.commit()
    u = db.get(User, user_id)
    assert u is not None
    return MemberOut(user_id=u.id, username=u.username, display_name=u.display_name, role=m.role)


@router.delete("/{project_id}/members/{user_id}")
def remove_member(project_id: int, user_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    project, role = require_project_access(project_id, ctx, db)
    if not can_manage_members(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Not permitted")
    m = db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="Membership not found")
    db.delete(m)
    log_audit(db, actor_id=ctx.user.id, entity_type="member", entity_id=user_id, action="removed", project_id=project_id)
    db.commit()
    log.info("removed member %s from project %s by %s", user_id, project_id, ctx.user.id)
    return {"ok": True}
