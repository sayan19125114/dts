"""Dashboard aggregates - all from authorized queries."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.v1.deps import AuthContext, get_auth_context
from app.db.models import Bug, Project, ProjectMember
from app.db.session import get_db
from app.schemas.schemas import BugOut, DashboardSummary

router = APIRouter()


def _visible_project_ids(db: Session, ctx: AuthContext) -> list[int] | None:
    """None means all (admin)."""
    if ctx.user.global_role == "admin":
        return None
    rows = db.execute(select(ProjectMember.project_id).where(ProjectMember.user_id == ctx.user.id)).scalars().all()
    return list(rows)


@router.get("/summary", response_model=DashboardSummary)
def summary(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    pids = _visible_project_ids(db, ctx)
    base_filter = [Bug.is_archived == False]  # noqa
    if pids is not None:
        if not pids:
            return DashboardSummary(
                total_visible=0, by_status={}, by_priority={}, by_severity={},
                assigned_to_me=0, recently_updated=[], overdue=0, per_project=[],
            )
        base_filter.append(Bug.project_id.in_(pids))
    total = db.execute(select(func.count()).select_from(Bug).where(*base_filter)).scalar_one()
    # by status
    rows = db.execute(select(Bug.status, func.count()).where(*base_filter).group_by(Bug.status)).all()
    by_status = {r[0]: r[1] for r in rows}
    rows = db.execute(select(Bug.priority, func.count()).where(*base_filter).group_by(Bug.priority)).all()
    by_priority = {r[0]: r[1] for r in rows}
    rows = db.execute(select(Bug.severity, func.count()).where(*base_filter).group_by(Bug.severity)).all()
    by_severity = {r[0]: r[1] for r in rows}
    assigned = db.execute(
        select(func.count()).select_from(Bug).where(*base_filter, Bug.assignee_id == ctx.user.id)
    ).scalar_one()
    recent = db.execute(select(Bug).where(*base_filter).order_by(Bug.updated_at.desc()).limit(10)).scalars().all()
    today = dt.date.today()
    overdue = db.execute(
        select(func.count()).select_from(Bug).where(*base_filter, Bug.due_date != None, Bug.due_date < today, Bug.status.notin_(["Closed", "Rejected"]))  # noqa
    ).scalar_one()
    # per-project
    if pids is None:
        prows = db.execute(select(Project)).scalars().all()
    else:
        prows = db.execute(select(Project).where(Project.id.in_(pids))).scalars().all() if pids else []
    per_project = []
    for p in prows:
        c = db.execute(select(func.count()).select_from(Bug).where(Bug.project_id == p.id, Bug.is_archived == False)).scalar_one()  # noqa
        per_project.append({"project_id": p.id, "project_key": p.project_key, "name": p.name, "count": c})
    return DashboardSummary(
        total_visible=total,
        by_status=by_status,
        by_priority=by_priority,
        by_severity=by_severity,
        assigned_to_me=assigned,
        recently_updated=[BugOut.model_validate(b) for b in recent],
        overdue=overdue,
        per_project=per_project,
    )


@router.get("/assigned", response_model=list[BugOut])
def my_assigned(
    page: int = 1,
    page_size: int = Query(20, le=100),
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    pids = _visible_project_ids(db, ctx)
    stmt = select(Bug).where(Bug.assignee_id == ctx.user.id, Bug.is_archived == False)  # noqa
    if pids is not None:
        if not pids:
            return []
        stmt = stmt.where(Bug.project_id.in_(pids))
    stmt = stmt.order_by(Bug.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = db.execute(stmt).scalars().all()
    return [BugOut.model_validate(b) for b in rows]
