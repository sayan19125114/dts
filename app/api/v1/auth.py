"""Authentication endpoints."""
from __future__ import annotations

import datetime as dt
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.v1.deps import AuthContext, get_auth_context
from app.core.config import get_settings
from app.core.security import (
    clear_login_attempts,
    generate_csrf_token,
    generate_session_token,
    hash_password,
    hash_token,
    hash_user_agent,
    is_rate_limited,
    record_failed_login,
    verify_password,
)
from app.core.security import reset_throttle_store  # noqa
from app.db.models import PasswordResetToken, ProjectMember, Session as DBSession, User
from app.db.session import get_db
from app.schemas.schemas import ChangePasswordIn, LoginIn, LoginOut, MeOut, ResetPasswordIn, UserOut

router = APIRouter()
log = logging.getLogger(__name__)


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _set_session_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=s.session_cookie_name,
        value=token,
        httponly=True,
        secure=s.cookie_secure,
        samesite=s.cookie_samesite,  # type: ignore
        path="/",
        max_age=s.session_expire_seconds,
    )


def _clear_session_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(key=s.session_cookie_name, path="/")


def _create_session(db: Session, user: User, ua: str | None) -> tuple[str, DBSession]:
    s = get_settings()
    raw = generate_session_token()
    csrf = generate_csrf_token()
    now = _now_utc()
    sess = DBSession(
        user_id=user.id,
        token_hash=hash_token(raw),
        csrf_token=csrf,
        created_at=now,
        expires_at=now + dt.timedelta(seconds=s.session_expire_seconds),
        last_seen_at=now,
        user_agent_hash=hash_user_agent(ua),
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return raw, sess


@router.post("/login", response_model=LoginOut)
def login(payload: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    ident = payload.username_or_email.strip().lower()
    # rate limit per ident + IP
    client_ip = request.client.host if request.client else "unknown"
    rl_key = f"{ident}:{client_ip}"
    if is_rate_limited(rl_key, settings.login_max_attempts, settings.login_window_seconds):
        log.warning("login rate limited for %s", ident)
        raise HTTPException(status_code=429, detail="Too many attempts, try later")
    stmt = select(User).where(or_(User.username == ident, User.email == ident))
    user = db.execute(stmt).scalar_one_or_none()
    ok = False
    if user is not None and user.is_active:
        ok = verify_password(payload.password, user.password_hash)
    if not ok or user is None:
        record_failed_login(rl_key)
        log.info("failed login attempt")
        # generic error
        raise HTTPException(status_code=401, detail="Invalid credentials")
    # revoke old sessions? rotate: create new; keep others? Plan says rotate at login. We'll keep others but create new.
    # Actually rotate = new token; we create new session. Optionally keep old? We'll keep (multi-device) but rotate current.
    clear_login_attempts(rl_key)
    ua = request.headers.get("user-agent")
    raw, sess = _create_session(db, user, ua)
    user.last_login_at = _now_utc()
    db.commit()
    _set_session_cookie(response, raw)
    log.info("user %s logged in", user.id)
    return LoginOut(user=UserOut.model_validate(user), csrf_token=sess.csrf_token)


@router.post("/logout")
def logout(response: Response, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    ctx.session.revoked_at = _now_utc()
    db.commit()
    _clear_session_cookie(response)
    log.info("user %s logged out", ctx.user.id)
    return {"ok": True}


@router.get("/me", response_model=MeOut)
def me(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    members = db.execute(select(ProjectMember).where(ProjectMember.user_id == ctx.user.id)).scalars().all()
    roles = {str(m.project_id): m.role for m in members}
    return MeOut(user=UserOut.model_validate(ctx.user), csrf_token=ctx.session.csrf_token, project_roles=roles)


@router.get("/csrf")
def get_csrf(ctx: AuthContext = Depends(get_auth_context)):
    return {"csrf_token": ctx.session.csrf_token}


@router.post("/change-password")
def change_password(
    payload: ChangePasswordIn, response: Response, request: Request,
    ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db),
):
    user = db.get(User, ctx.user.id)
    assert user is not None
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password incorrect")
    try:
        user.password_hash = hash_password(payload.new_password)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    user.updated_at = _now_utc()
    # revoke all other sessions
    others = db.execute(select(DBSession).where(DBSession.user_id == user.id, DBSession.id != ctx.session.id)).scalars().all()
    now = _now_utc()
    for o in others:
        o.revoked_at = now
    # rotate current session: new token + new csrf
    raw = generate_session_token()
    ctx.session.token_hash = hash_token(raw)
    ctx.session.csrf_token = generate_csrf_token()
    ctx.session.expires_at = now + dt.timedelta(seconds=get_settings().session_expire_seconds)
    ctx.session.last_seen_at = now
    db.commit()
    _set_session_cookie(response, raw)
    log.info("user %s changed password, revoked %d sessions", user.id, len(others))
    # need fresh csrf to return
    db.refresh(ctx.session)
    return {"ok": True, "csrf_token": ctx.session.csrf_token}


@router.post("/reset-password")
def reset_password(payload: ResetPasswordIn, db: Session = Depends(get_db)):
    th = hash_token(payload.token)
    rec = db.execute(select(PasswordResetToken).where(PasswordResetToken.token_hash == th)).scalar_one_or_none()
    if rec is None or rec.used_at is not None:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    exp = rec.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=dt.timezone.utc)
    if exp < _now_utc():
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    user = db.get(User, rec.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    try:
        user.password_hash = hash_password(payload.new_password)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    rec.used_at = _now_utc()
    # revoke all sessions
    sessions = db.execute(select(DBSession).where(DBSession.user_id == user.id)).scalars().all()
    for se in sessions:
        se.revoked_at = _now_utc()
    db.commit()
    return {"ok": True}
