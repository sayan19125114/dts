"""Pydantic request/response schemas. Never return ORM blindly."""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

VALID_SEVERITIES = {"Critical", "High", "Medium", "Low", "Trivial"}
VALID_PRIORITIES = {"P0", "P1", "P2", "P3", "P4"}
VALID_STATUSES = {"Open", "In Progress", "Fixed", "Ready for Test", "Reopened", "Closed", "Rejected"}
VALID_TYPES = {"Bug", "Regression", "UI", "Performance", "Security", "Compatibility"}
VALID_PROJECT_ROLES = {"manager", "developer", "reporter", "viewer"}

ALLOWED_TRANSITIONS: Dict[str, List[str]] = {
    "Open": ["In Progress", "Rejected"],
    "In Progress": ["Fixed", "Open", "Rejected"],
    "Fixed": ["Ready for Test", "Reopened"],
    "Ready for Test": ["Closed", "Reopened"],
    "Reopened": ["In Progress", "Rejected"],
    "Closed": ["Reopened"],
    "Rejected": ["Reopened"],
}

SORT_ALLOWLIST_BUGS = {"created_at", "updated_at", "priority", "severity", "status", "title", "public_key"}


class ErrorResponse(BaseModel):
    detail: str
    field_errors: Optional[Dict[str, str]] = None


# ---- Auth ----
class LoginIn(BaseModel):
    username_or_email: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=128)


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=10, max_length=256)
    new_password: str = Field(min_length=12, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    email: str
    display_name: str
    global_role: str
    is_active: bool
    created_at: Optional[dt.datetime] = None
    last_login_at: Optional[dt.datetime] = None


class MeOut(BaseModel):
    user: UserOut
    csrf_token: str
    project_roles: Dict[str, str] = {}


class LoginOut(BaseModel):
    user: UserOut
    csrf_token: str


# ---- Admin users ----
class AdminCreateUserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: str = Field(min_length=3, max_length=255)
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=128)
    global_role: str = "user"

    @field_validator("username")
    @classmethod
    def norm_username(cls, v: str) -> str:
        v = v.strip().lower()
        if not v.replace("_", "").replace("-", "").replace(".", "").isalnum():
            raise ValueError("Invalid username")
        return v

    @field_validator("email")
    @classmethod
    def norm_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("global_role")
    @classmethod
    def check_role(cls, v: str) -> str:
        if v not in ("admin", "user"):
            raise ValueError("Invalid global role")
        return v


class AdminUpdateUserIn(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[str] = Field(default=None, max_length=255)
    global_role: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("global_role")
    @classmethod
    def check_role(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("admin", "user"):
            raise ValueError("Invalid global role")
        return v


# ---- Projects ----
class ProjectCreateIn(BaseModel):
    project_key: str = Field(min_length=2, max_length=16)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)

    @field_validator("project_key")
    @classmethod
    def norm_key(cls, v: str) -> str:
        v = v.strip().upper()
        import re

        if not re.match(r"^[A-Z][A-Z0-9]{1,15}$", v):
            raise ValueError("Project key must be 2-16 uppercase alphanumerics starting with letter")
        return v


class ProjectUpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_key: str
    name: str
    description: str
    status: str
    created_by: int
    created_at: Optional[dt.datetime] = None
    updated_at: Optional[dt.datetime] = None
    archived_at: Optional[dt.datetime] = None
    my_role: Optional[str] = None


class MemberAddIn(BaseModel):
    user_id: Optional[int] = None
    username_or_email: Optional[str] = None
    role: str

    @field_validator("role")
    @classmethod
    def check_role(cls, v: str) -> str:
        if v not in VALID_PROJECT_ROLES:
            raise ValueError("Invalid project role")
        return v


class MemberUpdateIn(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def check_role(cls, v: str) -> str:
        if v not in VALID_PROJECT_ROLES:
            raise ValueError("Invalid project role")
        return v


class MemberOut(BaseModel):
    user_id: int
    username: str
    display_name: str
    role: str


# ---- Bugs ----
class BugCreateIn(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    description: str = Field(min_length=1, max_length=20000)
    steps_to_reproduce: str = Field(default="", max_length=10000)
    expected_result: str = Field(default="", max_length=5000)
    actual_result: str = Field(default="", max_length=5000)
    environment: str = Field(default="", max_length=500)
    severity: str = "Medium"
    priority: str = "P2"
    type: str = "Bug"
    assignee_id: Optional[int] = None
    due_date: Optional[dt.date] = None
    resolution: str = Field(default="", max_length=500)
    component: Optional[str] = Field(default=None, max_length=120)
    affected_version: Optional[str] = Field(default=None, max_length=120)
    fixed_version: Optional[str] = Field(default=None, max_length=120)
    affected_release_id: Optional[int] = None
    fixed_release_id: Optional[int] = None
    custom_fields: Optional[Dict[str, str]] = None

    @field_validator("severity")
    @classmethod
    def check_sev(cls, v: str) -> str:
        if v not in VALID_SEVERITIES:
            raise ValueError(f"Invalid severity")
        return v

    @field_validator("priority")
    @classmethod
    def check_pri(cls, v: str) -> str:
        if v not in VALID_PRIORITIES:
            raise ValueError("Invalid priority")
        return v

    @field_validator("type")
    @classmethod
    def check_type(cls, v: str) -> str:
        if v not in VALID_TYPES:
            raise ValueError("Invalid type")
        return v


class BugUpdateIn(BaseModel):
    title: Optional[str] = Field(default=None, min_length=5, max_length=200)
    description: Optional[str] = Field(default=None, min_length=1, max_length=20000)
    steps_to_reproduce: Optional[str] = Field(default=None, max_length=10000)
    expected_result: Optional[str] = Field(default=None, max_length=5000)
    actual_result: Optional[str] = Field(default=None, max_length=5000)
    environment: Optional[str] = Field(default=None, max_length=500)
    severity: Optional[str] = None
    priority: Optional[str] = None
    type: Optional[str] = None
    resolution: Optional[str] = Field(default=None, max_length=500)
    due_date: Optional[dt.date] = None
    version: int
    component: Optional[str] = Field(default=None, max_length=120)
    affected_version: Optional[str] = Field(default=None, max_length=120)
    fixed_version: Optional[str] = Field(default=None, max_length=120)
    affected_release_id: Optional[int] = None
    fixed_release_id: Optional[int] = None
    custom_fields: Optional[Dict[str, str]] = None

    @field_validator("severity")
    @classmethod
    def check_sev(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_SEVERITIES:
            raise ValueError("Invalid severity")
        return v

    @field_validator("priority")
    @classmethod
    def check_pri(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_PRIORITIES:
            raise ValueError("Invalid priority")
        return v

    @field_validator("type")
    @classmethod
    def check_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_TYPES:
            raise ValueError("Invalid type")
        return v


class BugTransitionIn(BaseModel):
    to_status: str
    version: Optional[int] = None
    reason: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("to_status")
    @classmethod
    def check_status(cls, v: str) -> str:
        if v not in VALID_STATUSES:
            raise ValueError("Invalid status")
        return v


class BugAssignIn(BaseModel):
    assignee_id: Optional[int] = None
    version: Optional[int] = None


class BugOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    public_key: str
    sequence_number: int
    title: str
    description: str
    steps_to_reproduce: str = ""
    expected_result: str = ""
    actual_result: str = ""
    environment: str = ""
    severity: str
    priority: str
    status: str
    type: str
    reporter_id: int
    assignee_id: Optional[int] = None
    resolution: str = ""
    due_date: Optional[dt.date] = None
    version: int
    is_archived: bool = False
    component: Optional[str] = None
    affected_version: Optional[str] = None
    fixed_version: Optional[str] = None
    affected_release_id: Optional[int] = None
    fixed_release_id: Optional[int] = None
    resolved_at: Optional[dt.datetime] = None
    created_at: Optional[dt.datetime] = None
    updated_at: Optional[dt.datetime] = None


class BugListOut(BaseModel):
    items: List[BugOut]
    total: int
    page: int
    page_size: int


class HistoryOut(BaseModel):
    id: int
    bug_id: int
    actor_id: Optional[int] = None
    event_type: str
    changes_json: Dict[str, Any] = {}
    created_at: Optional[dt.datetime] = None


# ---- Comments ----
class CommentCreateIn(BaseModel):
    body: str = Field(min_length=1, max_length=10000)
    is_internal: Optional[bool] = False


class CommentUpdateIn(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    bug_id: int
    author_id: int
    body: str
    created_at: Optional[dt.datetime] = None
    updated_at: Optional[dt.datetime] = None
    is_deleted: bool = False
    is_internal: bool = False


# ---- Attachments ----
class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    bug_id: int
    uploaded_by: int
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: Optional[dt.datetime] = None


# ---- Dashboard ----
class DashboardSummary(BaseModel):
    total_visible: int
    by_status: Dict[str, int]
    by_priority: Dict[str, int]
    by_severity: Dict[str, int]
    assigned_to_me: int
    recently_updated: List[BugOut]
    overdue: int
    per_project: List[Dict[str, Any]]


# ---- Phase 4: SLA ----
class SlaPolicyIn(BaseModel):
    name: str = Field(default="Default SLA", min_length=1, max_length=120)
    timezone: str = Field(default="UTC", max_length=64)
    business_hours: Optional[Dict[str, Any]] = None
    holidays: Optional[List[str]] = None
    targets: Optional[Dict[str, Any]] = None
    pause_statuses: Optional[List[str]] = None
    escalation_role: str = Field(default="manager", max_length=16)
    escalation_user_id: Optional[int] = None
    notes: str = Field(default="", max_length=2000)

    @field_validator("pause_statuses")
    @classmethod
    def check_pause(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            for s in v:
                if s not in VALID_STATUSES:
                    raise ValueError(f"Invalid pause status: {s}")
        return v

    @field_validator("escalation_role")
    @classmethod
    def check_role(cls, v: str) -> str:
        if v not in ("manager", "lead", "admin"):
            raise ValueError("escalation_role must be manager, lead, or admin")
        return v


class SlaPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    name: str
    version: int
    is_active: bool
    timezone: str
    business_hours: Dict[str, Any] = {}
    holidays: List[Any] = []
    targets: Dict[str, Any] = {}
    pause_statuses: List[Any] = []
    escalation_role: str
    escalation_user_id: Optional[int] = None
    notes: str = ""
    effective_from: Optional[dt.datetime] = None
    created_at: Optional[dt.datetime] = None


class SlaReapplyIn(BaseModel):
    apply_to_existing: bool = False
    only_unresolved: bool = True
    project_id: Optional[int] = None


class SlaEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    clock: str
    event_type: str
    detail_json: Dict[str, Any] = {}
    created_at: Optional[dt.datetime] = None


class BugSlaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    bug_id: int
    project_id: int
    policy_id: Optional[int] = None
    policy_version: int
    timezone: str
    first_response_target: Optional[int] = None
    triage_target: Optional[int] = None
    resolution_target: Optional[int] = None
    warning_pct: int
    first_response_due: Optional[dt.datetime] = None
    triage_due: Optional[dt.datetime] = None
    resolution_due: Optional[dt.datetime] = None
    first_response_at: Optional[dt.datetime] = None
    triage_at: Optional[dt.datetime] = None
    resolved_at: Optional[dt.datetime] = None
    is_paused: bool
    paused_at: Optional[dt.datetime] = None
    state: str
    flags_json: Dict[str, Any] = {}
    started_at: Optional[dt.datetime] = None
    updated_at: Optional[dt.datetime] = None


class BugSlaDetailOut(BaseModel):
    sla: BugSlaOut
    events: List[SlaEventOut] = []
    compliance: Dict[str, Any] = {}


# ---- Phase 4: Releases ----
class ReleaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=5000)
    planned_date: Optional[dt.date] = None
    actual_date: Optional[dt.date] = None
    release_notes: str = Field(default="", max_length=20000)
    gates_blocking: bool = False


class ReleaseUpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=5000)
    planned_date: Optional[dt.date] = None
    actual_date: Optional[dt.date] = None
    release_notes: Optional[str] = Field(default=None, max_length=20000)
    gates_blocking: Optional[bool] = None


class ReleaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    name: str
    version: str
    description: str = ""
    status: str
    planned_date: Optional[dt.date] = None
    actual_date: Optional[dt.date] = None
    release_notes: str = ""
    gates_blocking: bool
    created_at: Optional[dt.datetime] = None
    updated_at: Optional[dt.datetime] = None
    released_at: Optional[dt.datetime] = None


class ReleaseGateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    rule_type: str
    config_json: Dict[str, Any] = {}
    blocking: bool = False

    @field_validator("rule_type")
    @classmethod
    def check_rule(cls, v: str) -> str:
        if v not in ("unresolved_critical", "unresolved_high", "max_open_by_severity", "sla_compliance"):
            raise ValueError("Invalid rule_type")
        return v


class ReleaseGateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    release_id: int
    name: str
    rule_type: str
    config_json: Dict[str, Any] = {}
    blocking: bool
    passed: Optional[bool] = None
    detail_json: Dict[str, Any] = {}
    evaluated_at: Optional[dt.datetime] = None
    waived: bool
    waived_by: Optional[int] = None
    waiver_reason: str = ""
    waived_at: Optional[dt.datetime] = None


class ReleaseReadinessOut(BaseModel):
    release: ReleaseOut
    gates: List[ReleaseGateOut]
    blocking_failures: List[int]
    advisory_failures: List[int]
    ready: bool
    would_block: bool
    unresolved_by_severity: Dict[str, int]


class ReleaseWaiveIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class ReleaseBugsIn(BaseModel):
    bug_id: int
    relation: str = "fixed"

    @field_validator("relation")
    @classmethod
    def check_rel(cls, v: str) -> str:
        if v not in ("affected", "fixed"):
            raise ValueError("relation must be affected or fixed")
        return v


# ---- Phase 4: exports ----
class ExportRequestIn(BaseModel):
    kind: str
    params: Dict[str, Any] = {}

    @field_validator("kind")
    @classmethod
    def check_kind(cls, v: str) -> str:
        if v not in ("bugs", "sla_compliance", "release_notes", "analytics"):
            raise ValueError("Invalid export kind")
        return v


class ExportJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    status: str
    filename: str
    row_count: Optional[int] = None
    error: str = ""
    expires_at: Optional[dt.datetime] = None
    created_at: Optional[dt.datetime] = None
    finished_at: Optional[dt.datetime] = None


class ExportJobCreatedOut(BaseModel):
    job: ExportJobOut
    download_url: str
    token: Optional[str] = None
