"""Append-only audit service (Phase 1.4). Never stores secrets."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging_config import request_id_var
from app.db.models import AuditLog, utcnow

SAFE_KEYS = {"title", "status", "severity", "priority", "type", "assignee_id", "public_key", "name", "role", "project_key"}


def _safe_changes(changes: dict | None) -> dict:
    if not changes:
        return {}
    out = {}
    for k, v in changes.items():
        if k.lower() in ("password", "password_hash", "token", "token_hash", "secret", "csrf"):
            continue
        # keep small safe payloads
        try:
            s = str(v)[:500]
        except Exception:
            continue
        out[k] = s
    return out


def log_audit(
    db: Session,
    *,
    actor_id: int | None,
    entity_type: str,
    entity_id: int,
    action: str,
    project_id: int | None = None,
    changes: dict | None = None,
    source: str = "api",
    request_id: str | None = None,
) -> AuditLog:
    rid = request_id
    if rid is None:
        try:
            rid = request_id_var.get()
            if rid == "-":
                rid = None
        except Exception:
            rid = None
    rec = AuditLog(
        actor_id=actor_id,
        entity_type=entity_type[:32],
        entity_id=entity_id,
        action=action[:64],
        project_id=project_id,
        changes_json=_safe_changes(changes),
        request_id=(rid[:32] if rid else None),
        source=source[:16],
        created_at=utcnow(),
    )
    db.add(rec)
    return rec
