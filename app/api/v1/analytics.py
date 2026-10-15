"""Phase 4 analytics endpoints (permission-scoped, read-only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.v1.deps import AuthContext, get_auth_context
from app.core.config import get_settings
from app.db.session import get_db
from app.services.analytics import compute_analytics

router = APIRouter()


def _require_feature() -> None:
    if not get_settings().feature_analytics:
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/summary")
def analytics_summary(
    project_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    group_by: str = Query(default="day", pattern="^(day|week|month)$"),
    severity: str | None = None,
    priority: str | None = None,
    assignee_id: int | None = None,
    component: str | None = None,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    _require_feature()
    params = {
        "project_id": project_id,
        "date_from": date_from,
        "date_to": date_to,
        "group_by": group_by,
        "severity": severity,
        "priority": priority,
        "assignee_id": assignee_id,
        "component": component,
    }
    try:
        return compute_analytics(db, ctx.user, params)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/sla")
def analytics_sla(
    project_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    _require_feature()
    try:
        data = compute_analytics(db, ctx.user, {"project_id": project_id, "date_from": date_from, "date_to": date_to})
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"scope": data["scope"], "sla": data["sla"]}


@router.get("/releases")
def analytics_releases(
    project_id: int | None = None,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    _require_feature()
    try:
        data = compute_analytics(db, ctx.user, {"project_id": project_id})
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"scope": data["scope"], "releases": data["releases"]}
