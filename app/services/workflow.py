"""Transition service (Phase 2.3). Default workflow preserved; custom per-project behind flag."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ProjectWorkflow
from app.schemas.schemas import ALLOWED_TRANSITIONS

DEFAULT_WORKFLOW = dict(ALLOWED_TRANSITIONS)


def get_allowed_transitions(db: Session, project_id: int, from_status: str) -> list[str]:
    try:
        if get_settings().feature_custom_workflows:
            wf = db.execute(select(ProjectWorkflow).where(ProjectWorkflow.project_id == project_id)).scalar_one_or_none()
            if wf and isinstance(wf.workflow_json, dict):
                allowed = wf.workflow_json.get(from_status)
                if isinstance(allowed, list):
                    return [str(x) for x in allowed]
    except Exception:
        pass
    return list(DEFAULT_WORKFLOW.get(from_status, []))


def validate_transition(db: Session, project_id: int, from_status: str, to_status: str) -> tuple[bool, list[str]]:
    allowed = get_allowed_transitions(db, project_id, from_status)
    return (to_status in allowed, allowed)
