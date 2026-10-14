"""Admin user management."""
from __future__ import annotations

import datetime as dt
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.deps import AuthContext, get_auth_context, require_admin
from app.core.security import hash_password, hash_token
from app.db.models import PasswordResetToken, ProjectMember, Session as DBSession, User
from app.db.session import get_db
from app.schemas.schemas import AdminCreateUserIn, AdminUpdateUserIn, UserOut
from app.services.audit import log_audit

router = APIRouter()
log = logging.getLogger(__name__)


def _now():
    return dt.datetime.now(dt.timezone.utc)


@router.get("/users")
def list_users(
    page: int = 1,
    page_size: int = Query(20, le=100),
    q: str | None = None,
    ctx: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    stmt = select(User)
    cstmt = select(func.count()).select_from(User)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(User.username.ilike(like), User.email.ilike(like), User.display_name.ilike(like)))
        cstmt = cstmt.where(or_(User.username.ilike(like), User.email.ilike(like), User.display_name.ilike(like)))
    total = db.execute(cstmt).scalar_one()
    stmt = stmt.order_by(User.username).offset((page - 1) * page_size).limit(page_size)
    users = db.execute(stmt).scalars().all()
    return {"items": [UserOut.model_validate(u) for u in users], "total": total, "page": page, "page_size": page_size}


@router.post("/users", status_code=201)
def create_user(payload: AdminCreateUserIn, ctx: AuthContext = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        ph = hash_password(payload.password)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    u = User(
        username=payload.username,
        email=payload.email,
        display_name=payload.display_name.strip(),
        password_hash=ph,
        global_role=payload.global_role,
        is_active=True,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(u)
    db.flush()
    log_audit(db, actor_id=ctx.user.id, entity_type="user", entity_id=u.id, action="created", changes={"username": payload.username})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username or email already exists")
    db.refresh(u)
    log.info("admin %s created user %s", ctx.user.id, u.id)
    return UserOut.model_validate(u)


@router.get("/users/{user_id}")
def get_user(user_id: int, ctx: AuthContext = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    members = db.execute(select(ProjectMember).where(ProjectMember.user_id == user_id)).scalars().all()
    return {
        "user": UserOut.model_validate(u),
        "memberships": [{"project_id": m.project_id, "role": m.role} for m in members],
    }


@router.patch("/users/{user_id}")
def update_user(user_id: int, payload: AdminUpdateUserIn, ctx: AuthContext = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.display_name is not None:
        u.display_name = payload.display_name.strip()
    if payload.email is not None:
        u.email = payload.email.strip().lower()
    if payload.global_role is not None:
        # last-admin protection: cannot demote last active admin
        if u.global_role == "admin" and payload.global_role != "admin":
            count = db.execute(select(func.count()).select_from(User).where(User.global_role == "admin", User.is_active == True)).scalar_one()  # noqa
            if count <= 1:
                raise HTTPException(status_code=400, detail="Cannot demote the last active admin")
        # cannot demote self if last? also prevent self-demotion leaving no admin? covered above
        u.global_role = payload.global_role
    if payload.is_active is not None:
        if not payload.is_active and u.id == ctx.user.id:
            # allow? prevent disabling self if last admin
            count = db.execute(select(func.count()).select_from(User).where(User.global_role == "admin", User.is_active == True)).scalar_one()  # noqa
            if u.global_role == "admin" and count <= 1:
                raise HTTPException(status_code=400, detail="Cannot disable the last active admin")
        u.is_active = payload.is_active
        if not payload.is_active:
            # revoke sessions
            sess = db.execute(select(DBSession).where(DBSession.user_id == u.id)).scalars().all()
            for s in sess:
                s.revoked_at = _now()
    u.updated_at = _now()
    log_audit(db, actor_id=ctx.user.id, entity_type="user", entity_id=u.id, action="updated")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already exists")
    db.refresh(u)
    log.info("admin %s updated user %s", ctx.user.id, u.id)
    return UserOut.model_validate(u)


@router.post("/users/{user_id}/disable")
def disable_user(user_id: int, ctx: AuthContext = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    if u.global_role == "admin":
        count = db.execute(select(func.count()).select_from(User).where(User.global_role == "admin", User.is_active == True)).scalar_one()  # noqa
        if count <= 1:
            raise HTTPException(status_code=400, detail="Cannot disable the last active admin")
    u.is_active = False
    u.updated_at = _now()
    sess = db.execute(select(DBSession).where(DBSession.user_id == u.id)).scalars().all()
    for s in sess:
        s.revoked_at = _now()
    log_audit(db, actor_id=ctx.user.id, entity_type="user", entity_id=u.id, action="disabled")
    db.commit()
    log.info("admin %s disabled user %s", ctx.user.id, u.id)
    return {"ok": True}


@router.post("/users/{user_id}/enable")
def enable_user(user_id: int, ctx: AuthContext = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    u.is_active = True
    u.updated_at = _now()
    log_audit(db, actor_id=ctx.user.id, entity_type="user", entity_id=u.id, action="enabled")
    db.commit()
    return {"ok": True}


@router.post("/users/{user_id}/password-reset")
def admin_password_reset(user_id: int, ctx: AuthContext = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    raw = secrets.token_urlsafe(32)
    rec = PasswordResetToken(
        user_id=u.id,
        token_hash=hash_token(raw),
        expires_at=_now() + dt.timedelta(hours=1),
        created_at=_now(),
    )
    db.add(rec)
    log_audit(db, actor_id=ctx.user.id, entity_type="user", entity_id=u.id, action="password_reset_requested")
    db.commit()
    log.info("admin %s triggered password reset for %s", ctx.user.id, u.id)
    # If email not configured, return token once for admin-assisted reset (documented).
    # Never log raw token elsewhere; return once.
    return {"ok": True, "reset_token": raw, "expires_at": rec.expires_at.isoformat(), "note": "Deliver this token securely; it is one-time and expires in 1h."}
