"""Permission matrix in code. All authorization uses DB membership + global role."""
from __future__ import annotations

from typing import Literal

ProjectRole = Literal["manager", "developer", "reporter", "viewer"]
GlobalRole = Literal["admin", "user"]

VALID_PROJECT_ROLES = {"manager", "developer", "reporter", "viewer"}
VALID_GLOBAL_ROLES = {"admin", "user"}

# Documented default matrix
# admin: all
# manager: project settings, members, all defects, comments, status, assign, reports
# developer: read, comment, update assigned defects, attach
# reporter: create, read, comment, edit own while Open
# viewer: read-only


def is_admin(global_role: str) -> bool:
    return global_role == "admin"


def can_manage_project(global_role: str, project_role: str | None) -> bool:
    if is_admin(global_role):
        return True
    return project_role == "manager"


def can_manage_members(global_role: str, project_role: str | None) -> bool:
    return can_manage_project(global_role, project_role)


def can_read_project(global_role: str, project_role: str | None) -> bool:
    if is_admin(global_role):
        return True
    return project_role is not None


def can_create_bug(global_role: str, project_role: str | None) -> bool:
    if is_admin(global_role):
        return True
    return project_role in ("manager", "developer", "reporter")


def can_comment(global_role: str, project_role: str | None) -> bool:
    if is_admin(global_role):
        return True
    return project_role in ("manager", "developer", "reporter")


def can_attach(global_role: str, project_role: str | None) -> bool:
    if is_admin(global_role):
        return True
    return project_role in ("manager", "developer", "reporter")


def can_edit_bug(
    global_role: str,
    project_role: str | None,
    is_reporter: bool,
    is_assignee: bool,
    bug_status: str,
) -> bool:
    """General field edit (not status transition alone)."""
    if is_admin(global_role):
        return True
    if project_role == "manager":
        return True
    if project_role == "developer":
        # developers may update assigned defects; allow additional updates only if assigned
        return is_assignee
    if project_role == "reporter":
        return is_reporter and bug_status == "Open"
    return False


def can_transition(
    global_role: str,
    project_role: str | None,
    is_reporter: bool,
    is_assignee: bool,
    from_status: str,
    to_status: str,
) -> bool:
    # Closed/Rejected -> Reopened restricted to manager/admin
    if to_status == "Reopened" and from_status in ("Closed", "Rejected"):
        if is_admin(global_role):
            return True
        return project_role == "manager"
    if is_admin(global_role):
        return True
    if project_role == "manager":
        return True
    if project_role == "developer":
        return is_assignee
    if project_role == "reporter":
        # reporters may reopen their own? keep strict: only if they are reporter and transition valid
        # Allow Reopened from Fixed/ReadyForTest? No - keep to own open? Simplify: reporter cannot transition except Open->? No.
        # To keep usable: reporter can move own bug Open->In Progress? No. Disallow reporter transitions except Reopened when they reported?
        # Policy: reporter may request reopen of own Fixed/ReadyForTest? Allow if is_reporter.
        return is_reporter
    return False


def can_assign(global_role: str, project_role: str | None) -> bool:
    if is_admin(global_role):
        return True
    return project_role == "manager"


def can_archive_bug(global_role: str, project_role: str | None) -> bool:
    if is_admin(global_role):
        return True
    return project_role == "manager"


PERMISSION_MATRIX_DOC = """
| Action | admin | manager | developer | reporter | viewer | non-member |
|---|---|---|---|---|---|---|
| manage project/members | Y | Y | N | N | N | N |
| create defect | Y | Y | Y | Y | N | N |
| read defect | Y | Y | Y | Y | Y | N |
| edit defect fields | Y | Y | assigned only | own+Open only | N | N |
| transition status | Y | Y | assigned only | own (except Closed/Rejected→Reopened) | N | N |
| assign | Y | Y | N | N | N | N |
| comment | Y | Y | Y | Y | N | N |
| attach | Y | Y | Y | Y | N | N |
| archive/restore bug | Y | Y | N | N | N | N |
| reports/export | Y | Y | Y | Y | Y | N |
"""
