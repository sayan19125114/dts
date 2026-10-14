"""Secure attachment upload/download."""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.bugs import _history, _load_bug_for_user
from app.api.v1.deps import AuthContext, get_auth_context
from app.core.config import get_settings
from app.core.permissions import can_attach
from app.db.models import Attachment
from app.db.session import get_db
from app.schemas.schemas import AttachmentOut

router = APIRouter()
log = logging.getLogger(__name__)


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _upload_base() -> Path:
    s = get_settings()
    p = Path(s.upload_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


# magic signatures
def _sniff_content(data_start: bytes, filename: str, claimed_mime: str) -> str | None:
    s = get_settings()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in s.allowed_extensions:
        return None
    # signature checks
    if data_start.startswith(b"\x89PNG"):
        return "image/png" if ext in ("png",) else None
    if data_start.startswith(b"\xff\xd8\xff"):
        return "image/jpeg" if ext in ("jpg", "jpeg") else None
    if data_start.startswith(b"RIFF") and b"WEBP" in data_start[:16]:
        return "image/webp" if ext in ("webp",) else None
    if data_start.startswith(b"%PDF"):
        return "application/pdf" if ext in ("pdf",) else None
    # txt/log: must be ascii-ish, no NUL, no executable signatures
    if ext in ("txt", "log"):
        if b"\x00" in data_start:
            return None
        # reject MZ/ELF
        if data_start.startswith(b"MZ") or data_start.startswith(b"\x7fELF"):
            return None
        return "text/plain"
    return None


@router.post("/bugs/{bug_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_attachment(bug_id: int, file: UploadFile, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    if project.status == "archived":
        raise HTTPException(status_code=409, detail="Project is archived")
    if bug.is_archived:
        raise HTTPException(status_code=409, detail="Defect is archived")
    if not can_attach(ctx.user.global_role, role):
        raise HTTPException(status_code=403, detail="Not permitted")
    s = get_settings()
    max_bytes = s.max_upload_mb * 1024 * 1024
    # sanitize display name (metadata only)
    orig = (file.filename or "file").strip()[:255]
    orig = orig.replace("/", "_").replace("\\", "_").replace("\x00", "")
    if not orig or orig in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    data = await file.read()
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail="File too large")
    ctype = _sniff_content(data[:32], orig, file.content_type or "")
    if ctype is None:
        raise HTTPException(status_code=400, detail="File type not allowed or signature mismatch")
    # executable rejection: double-check
    if data.startswith(b"MZ") or data.startswith(b"\x7fELF") or data.startswith(b"#!/"):
        # allow #!/ only for log/txt? reject to be safe if binary-ish
        if ctype != "text/plain":
            raise HTTPException(status_code=400, detail="Executable files rejected")
    sha = hashlib.sha256(data).hexdigest()
    storage_key = secrets.token_hex(24)
    dest = _upload_base() / storage_key
    # ensure no traversal: storage_key is hex only
    if not storage_key.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid storage key")
    dest.write_bytes(data)
    att = Attachment(
        bug_id=bug_id,
        uploaded_by=ctx.user.id,
        storage_key=storage_key,
        original_filename=orig[:255],
        content_type=ctype,
        size_bytes=len(data),
        sha256=sha,
        created_at=_now(),
    )
    db.add(att)
    db.flush()
    bug.updated_at = _now()
    bug.version += 1
    _history(db, bug.id, ctx.user.id, "attachment_added", {"attachment_id": att.id, "filename": orig})
    db.commit()
    db.refresh(att)
    return AttachmentOut.model_validate(att)


@router.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    att = db.get(Attachment, attachment_id)
    if att is None or att.is_deleted:
        raise HTTPException(status_code=404, detail="Attachment not found")
    bug, project, role = _load_bug_for_user(att.bug_id, ctx, db)
    # authorized via project access already
    path = _upload_base() / att.storage_key
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing")
    # safe headers
    safe_name = att.original_filename.replace('"', "_")
    return FileResponse(
        path,
        media_type=att.content_type,
        filename=safe_name,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/attachments/{attachment_id}")
def delete_attachment(attachment_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    att = db.get(Attachment, attachment_id)
    if att is None or att.is_deleted:
        raise HTTPException(status_code=404, detail="Attachment not found")
    bug, project, role = _load_bug_for_user(att.bug_id, ctx, db)
    can_moderate = ctx.user.global_role == "admin" or role == "manager"
    if not (can_moderate or att.uploaded_by == ctx.user.id):
        raise HTTPException(status_code=403, detail="Not permitted")
    att.is_deleted = True
    att.deleted_at = _now()
    _history(db, bug.id, ctx.user.id, "attachment_deleted", {"attachment_id": att.id})
    db.commit()
    # remove physical file safely
    try:
        p = _upload_base() / att.storage_key
        if p.exists():
            p.unlink()
    except Exception:
        log.exception("failed to remove attachment file")
    return {"ok": True}


@router.get("/bugs/{bug_id}/attachments", response_model=list[AttachmentOut])
def list_attachments(bug_id: int, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    bug, project, role = _load_bug_for_user(bug_id, ctx, db)
    rows = db.execute(select(Attachment).where(Attachment.bug_id == bug_id, Attachment.is_deleted == False)).scalars().all()  # noqa
    return [AttachmentOut.model_validate(a) for a in rows]
