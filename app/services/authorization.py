"""Central permission service with explicit actions (Phase 2.2).

Maps current roles to permission sets. Old can_* helpers in
app/core/permissions.py remain as thin wrappers — no contract break.
"""
from __future__ import annotations

ACTIONS = {
    "defect.read",
    "defect.create",
    "defect.edit",
    "defect.assign",
    "defect.transition",
    "defect.archive",
    "comment.create",
    "comment.moderate",
    "attachment.create",
    "project.read",
    "project.manage",
    "member.manage",
    "user.invite",
    "audit.read",
    "report.export",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "manager": {
        "defect.read", "defect.create", "defect.edit", "defect.assign",
        "defect.transition", "defect.archive", "comment.create", "comment.moderate",
        "attachment.create", "project.read", "project.manage", "member.manage",
        "report.export",
    },
    "developer": {
        "defect.read", "defect.create", "defect.edit", "defect.transition",
        "comment.create", "attachment.create", "project.read", "report.export",
    },
    "reporter": {
        "defect.read", "defect.create", "defect.edit", "defect.transition",
        "comment.create", "attachment.create", "project.read", "report.export",
    },
    "viewer": {"defect.read", "project.read", "report.export"},
}

PERMISSION_MATRIX_DOC = """
| action | manager | developer | reporter | viewer |
|---|---|---|---|---|
| defect.read | Y | Y | Y | Y |
| defect.create | Y | Y | Y | N |
| defect.edit | Y | assigned* | own+Open* | N |
| defect.assign | Y | N | N | N |
| defect.transition | Y | assigned* | own* | N |
| defect.archive | Y | N | N | N |
| comment.create | Y | Y | Y | N |
| attachment.create | Y | Y | Y | N |
| project.manage/member.manage | Y | N | N | N |
| report.export | Y | Y | Y | Y |
| audit.read | N(admin only) | N | N | N |
* contextual rules enforced in app/core/permissions.py (assignee/reporter/status).
"""


def role_permissions(role: str | None) -> set[str]:
    return set(ROLE_PERMISSIONS.get(role or "", set()))


def has_permission(global_role: str, project_role: str | None, action: str) -> bool:
    if global_role == "admin":
        return True
    return action in role_permissions(project_role)
