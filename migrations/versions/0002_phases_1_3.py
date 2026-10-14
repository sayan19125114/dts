"""Phase 1-3 additive schema. No drops, all nullable-safe.

Revision ID: 0002_phases_1_3
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_phases_1_3"
down_revision = "0001_initial"
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
    if not _has_table(bind, "organizations"):
        op.create_table(
            "organizations",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(200), nullable=False, server_default="Default"),
            sa.Column("slug", sa.String(64), nullable=False, unique=True, server_default="default"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    # ensure default org
    op.execute("INSERT OR IGNORE INTO organizations (id, name, slug, created_at) VALUES (1, 'Default', 'default', CURRENT_TIMESTAMP)")

    if not _has_column(bind, "projects", "org_id"):
        with op.batch_alter_table("projects") as batch:
            batch.add_column(sa.Column("org_id", sa.Integer, nullable=True))
    # FK enforced at app level for SQLite compat (SET NULL on org delete); backfill default
    op.execute("UPDATE projects SET org_id=1 WHERE org_id IS NULL")
    if not _has_column(bind, "bugs", "component"):
        op.add_column("bugs", sa.Column("component", sa.String(120), nullable=True))
    if not _has_column(bind, "bugs", "affected_version"):
        op.add_column("bugs", sa.Column("affected_version", sa.String(120), nullable=True))
    if not _has_column(bind, "bugs", "fixed_version"):
        op.add_column("bugs", sa.Column("fixed_version", sa.String(120), nullable=True))
    if not _has_column(bind, "comments", "is_internal"):
        op.add_column("comments", sa.Column("is_internal", sa.Boolean, nullable=False, server_default="0"))

    if not _has_table(bind, "audit_logs"):
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("actor_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("entity_type", sa.String(32), nullable=False),
            sa.Column("entity_id", sa.Integer, nullable=False),
            sa.Column("action", sa.String(64), nullable=False),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
            sa.Column("changes_json", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("request_id", sa.String(32), nullable=True),
            sa.Column("source", sa.String(16), nullable=False, server_default="api"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_audit_entity", "audit_logs", ["entity_type", "entity_id"])
        op.create_index("ix_audit_project", "audit_logs", ["project_id", "created_at"])
        op.create_index("ix_audit_created", "audit_logs", ["created_at"])

    if not _has_table(bind, "project_workflows"):
        op.create_table(
            "project_workflows",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("workflow_json", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("version", sa.Integer, nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if not _has_table(bind, "custom_field_defs"):
        op.create_table(
            "custom_field_defs",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("key", sa.String(64), nullable=False),
            sa.Column("label", sa.String(120), nullable=False),
            sa.Column("field_type", sa.String(16), nullable=False, server_default="text"),
            sa.Column("required", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("options_json", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "key", name="uq_custom_field"),
        )
        op.create_index("ix_cfd_project", "custom_field_defs", ["project_id"])
    if not _has_table(bind, "custom_field_values"):
        op.create_table(
            "custom_field_values",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("bug_id", sa.Integer, sa.ForeignKey("bugs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("field_id", sa.Integer, sa.ForeignKey("custom_field_defs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("value", sa.Text, nullable=False, server_default=""),
            sa.UniqueConstraint("bug_id", "field_id", name="uq_custom_value"),
        )
        op.create_index("ix_cfv_bug", "custom_field_values", ["bug_id"])
    if not _has_table(bind, "bug_links"):
        op.create_table(
            "bug_links",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("from_bug_id", sa.Integer, sa.ForeignKey("bugs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("to_bug_id", sa.Integer, sa.ForeignKey("bugs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("link_type", sa.String(32), nullable=False),
            sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("from_bug_id", "to_bug_id", "link_type", name="uq_bug_link"),
        )
        op.create_index("ix_links_from", "bug_links", ["from_bug_id"])
        op.create_index("ix_links_to", "bug_links", ["to_bug_id"])
    if not _has_table(bind, "bug_watchers"):
        op.create_table(
            "bug_watchers",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("bug_id", sa.Integer, sa.ForeignKey("bugs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("bug_id", "user_id", name="uq_bug_watcher"),
        )
        op.create_index("ix_watchers_bug", "bug_watchers", ["bug_id"])
        op.create_index("ix_watchers_user", "bug_watchers", ["user_id"])
    if not _has_table(bind, "notifications"):
        op.create_table(
            "notifications",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("type", sa.String(64), nullable=False),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("body", sa.Text, nullable=False, server_default=""),
            sa.Column("bug_id", sa.Integer, sa.ForeignKey("bugs.id", ondelete="SET NULL"), nullable=True),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
            sa.Column("is_read", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_notif_user", "notifications", ["user_id", "created_at"])
        op.create_index("ix_notif_unread", "notifications", ["user_id", "is_read"])
    if not _has_table(bind, "notification_prefs"):
        op.create_table(
            "notification_prefs",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
            sa.UniqueConstraint("user_id", "event_type", name="uq_notif_pref"),
        )
        op.create_index("ix_notif_pref_user", "notification_prefs", ["user_id"])
    if not _has_table(bind, "saved_views"):
        op.create_table(
            "saved_views",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("owner_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("filters_json", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_saved_owner", "saved_views", ["owner_id"])


def downgrade() -> None:
    for t in ["saved_views", "notification_prefs", "notifications", "bug_watchers", "bug_links", "custom_field_values", "custom_field_defs", "project_workflows", "audit_logs"]:
        op.drop_table(t)
    op.drop_column("comments", "is_internal")
    op.drop_column("bugs", "fixed_version")
    op.drop_column("bugs", "affected_version")
    op.drop_column("bugs", "component")
    op.drop_column("projects", "org_id")
    op.drop_table("organizations")
