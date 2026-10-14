"""Notification event interface (Phase 3.1). Called AFTER commit; failures never fail the op."""
from __future__ import annotations

import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Bug, Notification, NotificationPref, ProjectMember, utcnow

log = logging.getLogger(__name__)

DEFAULT_PREFS = {
    "assigned": True,
    "mentioned": True,
    "commented": True,
    "status_changed": True,
}


def _pref_enabled(db: Session, user_id: int, event_type: str) -> bool:
    pref = db.execute(
        select(NotificationPref).where(NotificationPref.user_id == user_id, NotificationPref.event_type == event_type)
    ).scalar_one_or_none()
    if pref is None:
        return DEFAULT_PREFS.get(event_type, True)
    return bool(pref.enabled)


def _idem(event_type: str, user_id: int, bug_id: int | None, extra: str = "") -> str:
    raw = f"{event_type}:{user_id}:{bug_id}:{extra}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _project_members(db: Session, project_id: int) -> list[int]:
    return list(db.execute(select(ProjectMember.user_id).where(ProjectMember.project_id == project_id)).scalars().all())


def emit(db_factory, *, event_type: str, actor_id: int | None, bug_id: int | None = None, project_id: int | None = None, title: str, body: str = "", extra: str = "") -> None:
    """Fire-and-forget in-process delivery (stub for queue). Idempotent, retry-safe."""
    try:
        if not get_settings().feature_notifications:
            return
    except Exception:
        pass
    try:
        candidate = db_factory() if callable(db_factory) else db_factory
        # candidate may be a sessionmaker (needs one more call) or a Session
        db = candidate() if not hasattr(candidate, "get") else candidate
    except Exception:
        log.exception("notify: cannot open db")
        return
    try:
        recipients: set[int] = set()
        bug = None
        if bug_id is not None:
            bug = db.get(Bug, bug_id)
            if bug is not None and project_id is None:
                project_id = bug.project_id
        if event_type == "assigned" and bug is not None and bug.assignee_id:
            recipients.add(bug.assignee_id)
        elif project_id is not None:
            # status/comment eventsNotify watchers + members involved; keep conservative: watchers + assignee + reporter
            from app.db.models import BugWatcher

            watchers = db.execute(select(BugWatcher.user_id).where(BugWatcher.bug_id == bug_id)).scalars().all() if bug_id else []
            recipients.update(watchers)
            if bug is not None:
                if bug.assignee_id:
                    recipients.add(bug.assignee_id)
                recipients.add(bug.reporter_id)
        if actor_id in recipients:
            recipients.discard(actor_id)  # avoid self-notify loops
        for uid in list(recipients):
            # privacy: must still have project access
            if project_id is not None:
                mem = db.execute(
                    select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == uid)
                ).scalar_one_or_none()
                # allow admins without membership? check global role
                from app.db.models import User

                u = db.get(User, uid)
                if u is None or not u.is_active:
                    continue
                if mem is None and u.global_role != "admin":
                    continue
            else:
                from app.db.models import User

                u = db.get(User, uid)
                if u is None or not u.is_active:
                    continue
            if not _pref_enabled(db, uid, event_type):
                continue
            key = _idem(event_type, uid, bug_id, extra or title)
            if db.execute(select(Notification).where(Notification.idempotency_key == key)).scalar_one_or_none():
                continue
            db.add(
                Notification(
                    user_id=uid,
                    type=event_type[:64],
                    title=title[:255],
                    body=(body or "")[:2000],
                    bug_id=bug_id,
                    project_id=project_id,
                    is_read=False,
                    idempotency_key=key,
                    created_at=utcnow(),
                )
            )
        db.commit()
    except IntegrityError:
        try:
            db.rollback()
        except Exception:
            pass
    except Exception:
        log.exception("notify failed (non-fatal)")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        try:
            db.close()
        except Exception:
            pass
