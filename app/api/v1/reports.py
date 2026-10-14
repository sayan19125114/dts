"""CSV export with auth + formula-injection mitigation."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.v1.deps import AuthContext, get_auth_context
from app.db.models import Bug
from app.db.session import get_db
from app.db.models import ProjectMember

router = APIRouter()


def _csv_safe(v) -> str:
    if v is None:
        return ""
    s = str(v)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        s = "'" + s
    # also handle formula after whitespace? simple: prefix if starts with those after lstrip
    return s


@router.get("/bugs.csv", response_class=PlainTextResponse)
def export_csv(
    project_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    # determine visible projects
    if ctx.user.global_role == "admin":
        visible = None  # all
    else:
        visible = set(db.execute(select(ProjectMember.project_id).where(ProjectMember.user_id == ctx.user.id)).scalars().all())
        if not visible:
            # empty csv with header
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(["public_key", "project_id", "title", "status", "severity", "priority", "type", "assignee_id", "reporter_id", "created_at", "updated_at"])
            return PlainTextResponse(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="bugs.csv"', "X-Content-Type-Options": "nosniff"})
    stmt = select(Bug).where(Bug.is_archived == False)  # noqa
    if project_id is not None:
        # auth check: must have access
        if visible is not None and project_id not in visible:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Project not found")
        stmt = stmt.where(Bug.project_id == project_id)
    else:
        if visible is not None:
            stmt = stmt.where(Bug.project_id.in_(list(visible)))
    if status:
        stmt = stmt.where(Bug.status == status)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Bug.title.ilike(like), Bug.description.ilike(like), Bug.public_key.ilike(like)))
    stmt = stmt.order_by(Bug.public_key).limit(5000)
    rows = db.execute(stmt).scalars().all()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["public_key", "project_id", "title", "status", "severity", "priority", "type", "assignee_id", "reporter_id", "created_at", "updated_at"])
    for b in rows:
        w.writerow(
            [
                _csv_safe(b.public_key),
                _csv_safe(b.project_id),
                _csv_safe(b.title),
                _csv_safe(b.status),
                _csv_safe(b.severity),
                _csv_safe(b.priority),
                _csv_safe(b.type),
                _csv_safe(b.assignee_id),
                _csv_safe(b.reporter_id),
                _csv_safe(b.created_at),
                _csv_safe(b.updated_at),
            ]
        )
    return PlainTextResponse(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="bugs.csv"', "X-Content-Type-Options": "nosniff"})
