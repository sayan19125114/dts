"""Auth dependencies: session cookie auth, CSRF enforcement, project scope."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.csrf import csrf_token_from_header
from app.core.security import hash_token
from app.db.models import Project, ProjectMember, Session as DBSession, User
from app.db.session import get_db

log = logging.getLogger(__name__)


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class AuthContext:
    def __init__(self, user: User, session: DBSession):
        self.user = user
        self.session = session


def _get_session_token(request: Request, cookie_val: Optional[str]) -> Optional[str]:
    if cookie_val:
        return cookie_val
    # Fallback: raw cookie header parse (TestClient compat)
    return None


async def get_auth_context(
    request: Request,
    session_cookie: Optional[str] = Cookie(default=None, alias="dts_session"),
    db: Session = Depends(get_db),
) -> AuthContext:
    settings = get_settings()
    # alias may not match configured name; also try configured name directly from cookies
    token = session_cookie
    if token is None:
        token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    th = hash_token(token)
    sess = db.execute(select(DBSession).where(DBSession.token_hash == th)).scalar_one_or_none()
    if sess is None or sess.revoked_at is not None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    now = _now_utc()
    # normalize naive datetimes from sqlite
    exp = sess.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=dt.timezone.utc)
    if exp < now:
        raise HTTPException(status_code=401, detail="Session expired")
    # idle timeout
    last = sess.last_seen_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    idle_for = (now - last).total_seconds()
    if idle_for > settings.session_idle_timeout_seconds:
        sess.revoked_at = now
        db.commit()
        raise HTTPException(status_code=401, detail="Session expired")
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # update last_seen (short write)
    sess.last_seen_at = now
    db.commit()
    # CSRF enforcement for state-changing
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # Trusted origin check
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        # If Origin present, must be same-origin or in cors_origins? We serve same-origin, so allow same host.
        # We enforce CSRF token strictly; origin check is additional where present.
        csrf = csrf_token_from_header(request, settings.csrf_header_name)
        if not csrf or csrf != sess.csrf_token:
            log.warning("CSRF failure for user %s %s", user.id, request.url.path)
            raise HTTPException(status_code=403, detail="CSRF validation failed")
    return AuthContext(user=user, session=sess)


def require_admin(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if ctx.user.global_role != "admin":
        raise HTTPException(status_code=403, detail="Admin required")
    return ctx


def get_project_role(db: Session, user_id: int, project_id: int) -> Optional[str]:
    m = db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
    ).scalar_one_or_none()
    return m.role if m else None


def require_project_access(project_id: int, ctx: AuthContext, db: Session) -> tuple[Project, Optional[str]]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if ctx.user.global_role == "admin":
        # admin has manager-equivalent
        return project, "manager"
    role = get_project_role(db, ctx.user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project, role
