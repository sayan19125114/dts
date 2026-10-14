"""Organizations (Phase 2.1). Default org preserves single-org behavior."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import AuthContext, get_auth_context, require_admin
from app.db.models import Organization, Project
from app.db.session import get_db
from app.services.audit import log_audit

router = APIRouter()


def _now():
    return dt.datetime.now(dt.timezone.utc)


@router.get("", response_model=list[dict])
def list_orgs(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    rows = db.execute(select(Organization).order_by(Organization.id)).scalars().all()
    if not rows:
        # ensure default (for DBs created before migration)
        org = Organization(id=1, name="Default", slug="default", created_at=_now())
        db.add(org)
        db.commit()
        db.refresh(org)
        rows = [org]
    return [{"id": o.id, "name": o.name, "slug": o.slug} for o in rows]


@router.post("", response_model=dict, status_code=201)
def create_org(payload: dict, ctx: AuthContext = Depends(require_admin), db: Session = Depends(get_db)):
    name = str(payload.get("name", "")).strip()[:200]
    slug = str(payload.get("slug", "")).strip().lower()[:64]
    if not name or not slug:
        raise HTTPException(status_code=422, detail="name and slug required")
    import re

    if not re.match(r"^[a-z0-9-]{2,64}$", slug):
        raise HTTPException(status_code=422, detail="Invalid slug")
    if db.execute(select(Organization).where(Organization.slug == slug)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Slug exists")
    o = Organization(name=name, slug=slug, created_at=_now())
    db.add(o)
    db.flush()
    log_audit(db, actor_id=ctx.user.id, entity_type="organization", entity_id=o.id, action="created", changes={"slug": slug})
    db.commit()
    db.refresh(o)
    return {"id": o.id, "name": o.name, "slug": o.slug}
