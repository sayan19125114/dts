"""Notifications, watchers, mentions, saved views (Phase 3)."""
from __future__ import annotations

import datetime as dt
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.bugs import _load_bug_for_user
from app.api.v1.deps import AuthContext, get_auth_context, get_project_role
from app.core.config import get_settings
from app.db.models import Bug, BugWatcher, Notification, NotificationPref, ProjectMember, SavedView, User, utcnow
from app.db.session import get_db
from app.services.audit import log_audit

router = APIRouter()
MENTION_RE = re.compile(r"@([A-Za-z0-9_.-]{3,64})")


def _now():
    return dt.datetime.now(dt.timezone.utc)


def extract_mentions(body: str) -> list[str]:
    return sorted({m.group(1).lower() for m in MENTION_RE.finditer(body or "")})


def notify_mentions(db_factory, *, author_id: int, bug_id: int, project_id: int, body: str) -> None:
    try:
        if not get_settings().feature_mentions:
            return
    except Exception:
        pass
    import hashlib

    from sqlalchemy.exc import IntegrityError

    candidate = db_factory() if callable(db_factory) else db_factory
    db = candidate() if not hasattr(candidate, "get") else candidate
    try:
        bug = db.get(Bug, bug_id)
        if bug is None:
            return
        for uname in extract_mentions(body):
            u = db.execute(select(User).where(User.username == uname)).scalar_one_or_none()
            if u is None or not u.is_active or u.id == author_id:
                continue
            # must have access
            mem = db.execute(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == u.id)).scalar_one_or_none()
            if mem is None and u.global_role != "admin":
                continue
            # pref check
            pref = db.execute(select(NotificationPref).where(NotificationPref.user_id == u.id, NotificationPref.event_type == "mentioned")).scalar_one_or_none()
            if pref is not None and not pref.enabled:
                continue
            key = hashlib.sha256(f"mentioned:{u.id}:{bug_id}:mention:{u.id}:{bug_id}".encode()).hexdigest()[:32]
            if db.execute(select(Notification).where(Notification.idempotency_key == key)).scalar_one_or_none():
                continue
            db.add(Notification(user_id=u.id, type="mentioned", title=f"Mentioned in {bug.public_key}", body=body[:500], bug_id=bug_id, project_id=project_id, is_read=False, idempotency_key=key, created_at=_now()))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.get("/notifications", response_model=dict)
def list_notifications(
    unread_only: bool = False,
    page: int = 1,
    page_size: int = Query(20, le=100),
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    stmt = select(Notification).where(Notification.user_id == ctx.user.id).order_by(Notification.created_at.desc())
    cstmt = select(func.count()).select_from(Notification).where(Notification.user_id == ctx.user.id)
    if unread_only:
        stmt = stmt.where(Notification.is_read == False)  # noqa
        cstmt = cstmt.where(Notification.is_read == False)  # noqa
    total = db.execute(cstmt).scalar_one()
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return {
        "items": [{"id": r.id, "type": r.type, "title": r.title, "body": r.body, "bug_id": r.bug_id, "project_id": r.project_id, "is_read": r.is_read, "created_at": r.created_at} for r in rows],
        "total": total, "page": page, "page_size": page_size,
    }


@router.post("/notifications/{nid}/read")
def mark_read(nid: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    n = db.get(Notification, nid)
    if n is None or n.user_id != ctx.user.id:
        raise HTTPException(status_code=404, detail="Not found")
    n.is_read = True
    db.commit()
    return {"ok": True}


@router.post("/notifications/mark-all-read")
def mark_all(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    rows = db.execute(select(Notification).where(Notification.user_id == ctx.user.id, Notification.is_read == False)).scalars().all()  # noqa
    for r in rows:
        r.is_read = True
    db.commit()
    return {"ok": True, "marked": len(rows)}


@router.get("/notification-prefs", response_model=dict)
def get_prefs(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    from app.services.notifications import DEFAULT_PREFS

    rows = db.execute(select(NotificationPref).where(NotificationPref.user_id == ctx.user.id)).scalars().all()
    out = dict(DEFAULT_PREFS)
    for r in rows:
        out[r.event_type] = bool(r.enabled)
    return out


@router.put("/notification-prefs", response_model=dict)
def put_prefs(payload: dict, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    allowed = {"assigned", "mentioned", "commented", "status_changed"}
    for k, v in payload.items():
        if k not in allowed or not isinstance(v, bool):
            raise HTTPException(status_code=422, detail=f"Invalid pref: {k}")
        rec = db.execute(select(NotificationPref).where(NotificationPref.user_id == ctx.user.id, NotificationPref.event_type == k)).scalar_one_or_none()
        if rec is None:
            db.add(NotificationPref(user_id=ctx.user.id, event_type=k, enabled=v))
        else:
            rec.enabled = v
    db.commit()
    return {"ok": True}


# ---- watchers ----
@router.get("/bugs/{bug_id}/watchers", response_model=list[dict])
def list_watchers(bug_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    rows = db.execute(select(BugWatcher, User).join(User, User.id == BugWatcher.user_id).where(BugWatcher.bug_id == bug_id)).all()
    return [{"user_id": u.id, "username": u.username} for w, u in rows]


@router.post("/bugs/{bug_id}/watch")
def watch(bug_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    if db.execute(select(BugWatcher).where(BugWatcher.bug_id == bug_id, BugWatcher.user_id == ctx.user.id)).scalar_one_or_none() is None:
        db.add(BugWatcher(bug_id=bug_id, user_id=ctx.user.id, created_at=_now()))
        db.commit()
    return {"ok": True, "watching": True}


@router.post("/bugs/{bug_id}/unwatch")
def unwatch(bug_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    w = db.execute(select(BugWatcher).where(BugWatcher.bug_id == bug_id, BugWatcher.user_id == ctx.user.id)).scalar_one_or_none()
    if w:
        db.delete(w)
        db.commit()
    return {"ok": True, "watching": False}


# ---- saved views ----
ALLOWED_FILTER_KEYS = {"status", "severity", "priority", "type", "assignee_id", "reporter_id", "q", "sort", "date_from", "date_to"}


@router.get("/saved-views", response_model=list[dict])
def list_views(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    if not get_settings().feature_saved_views:
        raise HTTPException(status_code=403, detail="Saved views disabled")
    rows = db.execute(select(SavedView).where(SavedView.owner_id == ctx.user.id).order_by(SavedView.created_at.desc())).scalars().all()
    return [{"id": r.id, "project_id": r.project_id, "name": r.name, "filters": r.filters_json or {}} for r in rows]


@router.post("/saved-views", response_model=dict, status_code=201)
def create_view(payload: dict, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    if not get_settings().feature_saved_views:
        raise HTTPException(status_code=403, detail="Saved views disabled")
    name = str(payload.get("name", "")).strip()[:120]
    filters = payload.get("filters", {})
    project_id = payload.get("project_id")
    if not name or not isinstance(filters, dict):
        raise HTTPException(status_code=422, detail="name and filters required")
    for k in filters.keys():
        if k not in ALLOWED_FILTER_KEYS:
            raise HTTPException(status_code=422, detail=f"Invalid filter: {k}")
    if project_id is not None:
        # must have access to that project (no privilege grant via saved view)
        from app.api.v1.deps import require_project_access

        require_project_access(int(project_id), ctx, db)
    v = SavedView(owner_id=ctx.user.id, project_id=project_id, name=name, filters_json=filters, created_at=_now(), updated_at=_now())
    db.add(v)
    db.commit()
    db.refresh(v)
    return {"ok": True, "id": v.id}


@router.delete("/saved-views/{vid}")
def delete_view(vid: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    v = db.get(SavedView, vid)
    if v is None or v.owner_id != ctx.user.id:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(v)
    db.commit()
    return {"ok": True}
