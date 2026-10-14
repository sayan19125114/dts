"""Comments endpoints."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.bugs import _history, _load_bug_for_user
from app.api.v1.deps import AuthContext, get_auth_context
from app.core.permissions import can_comment
from app.db.models import Comment
from app.db.session import get_db, get_session_factory
from app.schemas.schemas import CommentCreateIn, CommentOut, CommentUpdateIn
from app.services.audit import log_audit

router = APIRouter()

COMMENT_EDIT_WINDOW_MIN = 30


def _now():
    return dt.datetime.now(dt.timezone.utc)


@router.get("/bugs/{bug_id}/comments", response_model=list[CommentOut])
def list_comments(
    bug_id: int,
    page: int = 1,
    page_size: int = Query(50, le=100),
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    stmt = (
        select(Comment)
        .where(Comment.bug_id == bug_id, Comment.is_deleted == False)  # noqa
        .order_by(Comment.created_at.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = db.execute(stmt).scalars().all()
    # internal comments visible only to manager/admin/developer; reporters/viewers see public only
    if not (ctx.user.global_role == "admin" or role in ("manager", "developer")):
        rows = [c for c in rows if not c.is_internal]
    return [CommentOut.model_validate(c) for c in rows]


@router.post("/bugs/{bug_id}/comments", response_model=CommentOut, status_code=201)
def create_comment(bug_id: int, payload: CommentCreateIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    if project.status == "archived":
        raise HTTPException(status_code=409, detail="Project is archived")
    if bug.is_archived:
        raise HTTPException(status_code=409, detail="Defect is archived")
    if not can_comment(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Not permitted to comment")
    internal = bool(payload.is_internal)
    if internal and not (ctx.user.global_role == "admin" or role in ("manager", "developer")):
        raise HTTPException(status_code=403, detail="Internal comments restricted")
    c = Comment(bug_id=bug_id, author_id=ctx.user.id, body=payload.body.strip(), is_internal=internal, created_at=_now(), updated_at=_now())
    db.add(c)
    db.flush()
    bug.updated_at = _now()
    bug.version += 1
    _history(db, bug.id, ctx.user.id, "commented", {"comment_id": c.id, "internal": internal})
    log_audit(db, actor_id=ctx.user.id, entity_type="comment", entity_id=c.id, action="created", project_id=bug.project_id, changes={"bug": str(bug_id)})
    db.commit()
    db.refresh(c)
    out = CommentOut.model_validate(c)
    # post-commit side effects (never fail the op) — use same engine so tests stay isolated
    try:
        from sqlalchemy.orm import sessionmaker as _sm

        from app.services.notifications import emit as _emit

        try:
            _engine = db.get_bind()
        except Exception:
            _engine = None
        _factory = _sm(bind=_engine) if _engine is not None else get_session_factory()
        _emit(_factory, event_type="commented", actor_id=ctx.user.id, bug_id=bug.id, project_id=bug.project_id, title=f"New comment on {bug.public_key}", body=c.body[:500], extra=f"comment:{c.id}")
        from app.api.v1.collab import notify_mentions as _nm

        _nm(_factory, author_id=ctx.user.id, bug_id=bug.id, project_id=bug.project_id, body=c.body)
    except Exception:
        pass
    return out


@router.patch("/comments/{comment_id}", response_model=CommentOut)
def update_comment(comment_id: int, payload: CommentUpdateIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    c = db.get(Comment, comment_id)
    if c is None or c.is_deleted:
        raise HTTPException(status_code=404, detail="Comment not found")
    bug, project, role = _load_bug_for_user(c.bug_id, ctx, db)
    # own comments within window, or manager/admin
    is_owner = c.author_id == ctx.user.id
    can_moderate = ctx.user.global_role == "admin" or role == "manager"
    if not (can_moderate or is_owner):
        raise HTTPException(status_code=403, detail="Not permitted")
    if is_owner and not can_moderate:
        created = c.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=dt.timezone.utc)
        if (_now() - created).total_seconds() > COMMENT_EDIT_WINDOW_MIN * 60:
            raise HTTPException(status_code=403, detail="Edit window expired")
    c.body = payload.body.strip()
    c.updated_at = _now()
    db.commit()
    db.refresh(c)
    return CommentOut.model_validate(c)


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    c = db.get(Comment, comment_id)
    if c is None or c.is_deleted:
        raise HTTPException(status_code=404, detail="Comment not found")
    bug, project, role = _load_bug_for_user(c.bug_id, ctx, db)
    is_owner = c.author_id == ctx.user.id
    can_moderate = ctx.user.global_role == "admin" or role == "manager"
    if not (can_moderate or is_owner):
        raise HTTPException(status_code=403, detail="Not permitted")
    c.is_deleted = True
    c.deleted_at = _now()
    _history(db, bug.id, ctx.user.id, "comment_deleted", {"comment_id": c.id})
    db.commit()
    return {"ok": True}
