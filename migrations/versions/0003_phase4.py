"""Phase 4 additive schema: SLA, releases, exports. No drops, nullable-safe.

Revision ID: 0003_phase4
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_phase4"
down_revision = "0002_phases_1_3"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    try:
        return bind.dialect.has_table(bind, name)
    except Exception:
        return False


def _has_column(bind, table: str, col: str) -> bool:
    try:
        from sqlalchemy import inspect as _inspect

        return any(c["name"] == col for c in _inspect(bind).get_columns(table))
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()

    # ---- add bug columns (nullable integers; FK enforced at app level for SQLite) ----
    if not _has_column(bind, "bugs", "resolved_at"):
        op.add_column("bugs", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    if not _has_column(bind, "bugs", "affected_release_id"):
        op.add_column("bugs", sa.Column("affected_release_id", sa.Integer, nullable=True))
    if not _has_column(bind, "bugs", "fixed_release_id"):
        op.add_column("bugs", sa.Column("fixed_release_id", sa.Integer, nullable=True))

    if not _has_table(bind, "sla_policies"):
        op.create_table(
            "sla_policies",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(120), nullable=False, server_default="Default SLA"),
            sa.Column("version", sa.Integer, nullable=False, server_default="1"),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="1"),
            sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
            sa.Column("business_hours", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("holidays", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("targets", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("pause_statuses", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
            sa.Column("escalation_role", sa.String(16), nullable=False, server_default="manager"),
            sa.Column("escalation_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("notes", sa.Text, nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "version", name="uq_sla_policy_version"),
        )
        op.create_index("ix_sla_policy_project", "sla_policies", ["project_id", "is_active"])

    if not _has_table(bind, "bug_sla"):
        op.create_table(
            "bug_sla",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("bug_id", sa.Integer, sa.ForeignKey("bugs.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("policy_id", sa.Integer, sa.ForeignKey("sla_policies.id", ondelete="SET NULL"), nullable=True),
            sa.Column("policy_version", sa.Integer, nullable=False, server_default="1"),
            sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
            sa.Column("business_hours", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("holidays", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("pause_statuses", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("escalation_role", sa.String(16), nullable=False, server_default="manager"),
            sa.Column("escalation_user_id", sa.Integer, nullable=True),
            sa.Column("first_response_target", sa.Integer, nullable=True),
            sa.Column("triage_target", sa.Integer, nullable=True),
            sa.Column("resolution_target", sa.Integer, nullable=True),
            sa.Column("warning_pct", sa.Integer, nullable=False, server_default="80"),
            sa.Column("first_response_due", sa.DateTime(timezone=True), nullable=True),
            sa.Column("triage_due", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolution_due", sa.DateTime(timezone=True), nullable=True),
            sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("triage_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_paused", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("state", sa.String(16), nullable=False, server_default="running"),
            sa.Column("flags_json", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_bug_sla_project", "bug_sla", ["project_id"])
        op.create_index("ix_bug_sla_state", "bug_sla", ["state"])

    if not _has_table(bind, "sla_events"):
        op.create_table(
            "sla_events",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("bug_id", sa.Integer, sa.ForeignKey("bugs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
            sa.Column("policy_id", sa.Integer, sa.ForeignKey("sla_policies.id", ondelete="SET NULL"), nullable=True),
            sa.Column("clock", sa.String(32), nullable=False, server_default=""),
            sa.Column("event_type", sa.String(32), nullable=False),
            sa.Column("detail_json", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("idempotency_key", sa.String(64), nullable=True, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_sla_event_bug", "sla_events", ["bug_id", "created_at"])
        op.create_index("ix_sla_event_type", "sla_events", ["event_type"])

    if not _has_table(bind, "releases"):
        op.create_table(
            "releases",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("version", sa.String(64), nullable=False),
            sa.Column("description", sa.Text, nullable=False, server_default=""),
            sa.Column("status", sa.String(16), nullable=False, server_default="planned"),
            sa.Column("planned_date", sa.Date, nullable=True),
            sa.Column("actual_date", sa.Date, nullable=True),
            sa.Column("release_notes", sa.Text, nullable=False, server_default=""),
            sa.Column("gates_blocking", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("project_id", "version", name="uq_release_version"),
        )
        op.create_index("ix_release_project", "releases", ["project_id"])

    if not _has_table(bind, "release_gates"):
        op.create_table(
            "release_gates",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("release_id", sa.Integer, sa.ForeignKey("releases.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("rule_type", sa.String(32), nullable=False),
            sa.Column("config_json", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("blocking", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("passed", sa.Boolean, nullable=True),
            sa.Column("detail_json", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("waived", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("waived_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("waiver_reason", sa.Text, nullable=False, server_default=""),
            sa.Column("waived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_release_gate_release", "release_gates", ["release_id"])

    if not _has_table(bind, "export_jobs"):
        op.create_table(
            "export_jobs",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("params_json", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("filename", sa.String(255), nullable=False, server_default=""),
            sa.Column("storage_key", sa.String(64), nullable=True),
            sa.Column("row_count", sa.Integer, nullable=True),
            sa.Column("error", sa.Text, nullable=False, server_default=""),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_export_owner", "export_jobs", ["created_by", "created_at"])
        op.create_index("ix_export_status", "export_jobs", ["status"])


def downgrade() -> None:
    for t in ["export_jobs", "release_gates", "releases", "sla_events", "bug_sla", "sla_policies"]:
        op.drop_table(t)
    op.drop_column("bugs", "fixed_release_id")
    op.drop_column("bugs", "affected_release_id")
    op.drop_column("bugs", "resolved_at")
