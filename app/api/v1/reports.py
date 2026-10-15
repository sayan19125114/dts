"""CSV export with auth + formula-injection mitigation."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.v1.deps import AuthContext, get_auth_context
from app.core.config import get_settings
from app.core.security import hash_token
from app.db.models import Bug, ExportJob
from app.db.session import get_db
from app.db.models import ProjectMember
from app.schemas.schemas import ExportJobCreatedOut, ExportJobOut, ExportRequestIn

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
        if visible is not None and project_id not in visible:
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


# ---- Phase 4: queued/async exports ----

@router.post("/exports", response_model=ExportJobCreatedOut, status_code=201)
def create_export(payload: ExportRequestIn, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.feature_async_exports:
        raise HTTPException(status_code=404, detail="Not found")
    from app.services import exports as exports_service

    job, token = exports_service.create_job(db, ctx.user, payload.kind, payload.params)
    if settings.export_inline:
        exports_service.process_job(db, job)
    url = f"/api/v1/reports/exports/{job.id}/download?token={token}"
    return ExportJobCreatedOut(job=ExportJobOut.model_validate(job), download_url=url, token=token)


@router.get("/exports", response_model=list[ExportJobOut])
def list_exports(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    if not get_settings().feature_async_exports:
        raise HTTPException(status_code=404, detail="Not found")
    stmt = select(ExportJob)
    if ctx.user.global_role != "admin":
        stmt = stmt.where(ExportJob.created_by == ctx.user.id)
    rows = db.execute(stmt.order_by(ExportJob.created_at.desc()).limit(100)).scalars().all()
    return rows


@router.get("/exports/{job_id}", response_model=ExportJobOut)
def get_export(job_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    if not get_settings().feature_async_exports:
        raise HTTPException(status_code=404, detail="Not found")
    job = db.get(ExportJob, job_id)
    if job is None or (ctx.user.global_role != "admin" and job.created_by != ctx.user.id):
        raise HTTPException(status_code=404, detail="Export not found")
    return job


@router.get("/exports/{job_id}/download")
async def download_export(job_id: int, request: Request, token: str | None = Query(default=None), db: Session = Depends(get_db)):
    if not get_settings().feature_async_exports:
        raise HTTPException(status_code=404, detail="Not found")
    from app.services import exports as exports_service

    job = db.get(ExportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Export not found")
    if token:
        if hash_token(token) != job.token_hash:
            raise HTTPException(status_code=404, detail="Export not found")
    else:
        settings = get_settings()
        ctx = await get_auth_context(request, request.cookies.get(settings.session_cookie_name), db)
        if ctx.user.global_role != "admin" and job.created_by != ctx.user.id:
            raise HTTPException(status_code=404, detail="Export not found")
    if job.status != "done" or exports_service.is_expired(job):
        raise HTTPException(status_code=404, detail="Export not available")
    path = exports_service.export_path(job)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Export not available")
    media = "text/markdown" if (job.filename or "").endswith(".md") else "text/csv"
    headers = {"X-Content-Type-Options": "nosniff", "Content-Disposition": f'attachment; filename="{job.filename or "export.csv"}"'}
    return FileResponse(str(path), media_type=media, headers=headers)
