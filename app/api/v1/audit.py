"""Admin audit log read (Phase 1.4). Admin-only, paginated."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.deps import AuthContext, require_admin
from app.db.models import AuditLog
from app.db.session import get_db

router = APIRouter()


@router.get("", response_model=dict)
def list_audit(
    page: int = 1,
    page_size: int = Query(20, le=100),
    entity_type: str | None = None,
    project_id: int | None = None,
    ctx: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    cstmt = select(func.count()).select_from(AuditLog)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
        cstmt = cstmt.where(AuditLog.entity_type == entity_type)
    if project_id is not None:
        stmt = stmt.where(AuditLog.project_id == project_id)
        cstmt = cstmt.where(AuditLog.project_id == project_id)
    total = db.execute(cstmt).scalar_one()
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return {
        "items": [
            {
                "id": r.id, "actor_id": r.actor_id, "entity_type": r.entity_type, "entity_id": r.entity_id,
                "action": r.action, "project_id": r.project_id, "changes_json": r.changes_json or {},
                "request_id": r.request_id, "source": r.source, "created_at": r.created_at,
            }
            for r in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }
