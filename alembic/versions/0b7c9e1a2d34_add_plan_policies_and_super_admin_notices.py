"""Add versioned plan policies and local Super Admin notices.

Revision ID: 0b7c9e1a2d34
Revises: 04d6a8c2e140
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "0b7c9e1a2d34"
down_revision: str | Sequence[str] | None = "04d6a8c2e140"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLAN_TABLES = (
    "plan_policy_versions",
    "workspace_plan_policy_assignments",
    "plan_policy_audit_events",
    "plan_policy_idempotency_records",
)
NOTICE_TABLES = ("super_admin_notice_audit_events",)


def _enable_append_only_guards() -> None:
    dialect = op.get_bind().dialect.name
    tables = PLAN_TABLES + NOTICE_TABLES
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION klm_reject_append_only_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table_name in tables:
            op.execute(
                f'''CREATE TRIGGER "trg_{table_name}_immutable"
                BEFORE UPDATE OR DELETE ON "{table_name}"
                FOR EACH ROW EXECUTE FUNCTION klm_reject_append_only_mutation()'''
            )
    elif dialect == "sqlite":
        for table_name in tables:
            for operation in ("update", "delete"):
                op.execute(
                    f'''
                    CREATE TRIGGER IF NOT EXISTS "trg_{table_name}_immutable_{operation}"
                    BEFORE {operation.upper()} ON "{table_name}"
                    BEGIN
                        SELECT RAISE(ABORT, '{table_name} is immutable');
                    END
                    '''
                )


def _disable_append_only_guards() -> None:
    dialect = op.get_bind().dialect.name
    tables = PLAN_TABLES + NOTICE_TABLES
    if dialect == "postgresql":
        for table_name in tables:
            op.execute(f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable" ON "{table_name}"')
    elif dialect == "sqlite":
        for table_name in tables:
            for operation in ("update", "delete"):
                op.execute(f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable_{operation}"')


def upgrade() -> None:
    plan_rollout_mode = sa.Enum(
        "existing_and_new",
        "new_only",
        "scheduled",
        "rule",
        name="plan_policy_rollout_mode_enum",
        native_enum=False,
        create_constraint=True,
    )
    op.create_table(
        "plan_policy_drafts",
        sa.Column("plan_id", FlexibleUUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("etag", sa.String(length=64), nullable=False),
        sa.Column("snapshot", FlexibleJSON(), nullable=False),
        sa.Column("updated_by_user_id", FlexibleUUID(), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("revision > 0", name="ck_plan_policy_draft_revision_positive"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", name="uq_plan_policy_draft_plan"),
    )
    op.create_table(
        "plan_policy_versions",
        sa.Column("plan_id", FlexibleUUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=False),
        sa.Column("snapshot", FlexibleJSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("rollout_mode", plan_rollout_mode, nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rollout_rule", FlexibleJSON(), nullable=True),
        sa.Column("publication_reason", sa.Text(), nullable=False),
        sa.Column("published_by_user_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_plan_policy_version_positive"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "version", name="uq_plan_policy_version_number"),
        sa.UniqueConstraint("id", "plan_id", name="uq_plan_policy_version_plan_binding"),
    )
    op.create_index(
        "ix_plan_policy_version_effective",
        "plan_policy_versions",
        ["plan_id", "effective_at", "version"],
    )
    op.create_table(
        "workspace_plan_policy_assignments",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("plan_id", FlexibleUUID(), nullable=False),
        sa.Column("policy_version_id", FlexibleUUID(), nullable=False),
        sa.Column("assignment_reason", sa.String(length=40), nullable=False),
        sa.Column("assigned_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["policy_version_id", "plan_id"],
            ["plan_policy_versions.id", "plan_policy_versions.plan_id"],
            ondelete="RESTRICT",
            name="fk_workspace_plan_policy_version_plan",
        ),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "policy_version_id", name="uq_workspace_plan_policy_assignment"
        ),
    )
    op.create_index(
        "ix_workspace_plan_policy_current",
        "workspace_plan_policy_assignments",
        ["workspace_id", "plan_id", "assigned_at"],
    )
    op.create_table(
        "plan_policy_audit_events",
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("plan_id", FlexibleUUID(), nullable=False),
        sa.Column("policy_version_id", FlexibleUUID(), nullable=True),
        sa.Column("workspace_id", FlexibleUUID(), nullable=True),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("event_data", FlexibleJSON(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["policy_version_id"], ["plan_policy_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_plan_policy_audit_plan_time", "plan_policy_audit_events", ["plan_id", "occurred_at"]
    )
    op.create_index(
        "ix_plan_policy_audit_workspace_time",
        "plan_policy_audit_events",
        ["workspace_id", "occurred_at"],
    )
    op.create_table(
        "plan_policy_idempotency_records",
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("plan_id", FlexibleUUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version_id", FlexibleUUID(), nullable=False),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=False),
        sa.Column("response_payload", FlexibleJSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["policy_version_id"], ["plan_policy_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation", "plan_id", "idempotency_key", name="uq_plan_policy_idempotency_scope"
        ),
    )
    op.create_index(
        "ix_plan_policy_idempotency_created", "plan_policy_idempotency_records", ["created_at"]
    )

    notice_status = sa.Enum(
        "draft",
        "scheduled",
        "published",
        "cancelled",
        name="super_admin_notice_status_enum",
        native_enum=False,
        create_constraint=True,
    )
    notice_audience = sa.Enum(
        "global",
        "workspaces",
        "users",
        "rule",
        name="super_admin_notice_audience_kind_enum",
        native_enum=False,
        create_constraint=True,
    )
    notice_channel = sa.Enum(
        "in_app",
        "email",
        name="super_admin_notice_channel_enum",
        native_enum=False,
        create_constraint=True,
    )
    notice_state = sa.Enum(
        "in_app_available",
        "email_queued_local",
        "email_blocked_missing_address",
        "cancelled",
        name="super_admin_notice_receipt_state_enum",
        native_enum=False,
        create_constraint=True,
    )
    notice_action = sa.Enum(
        "created",
        "updated",
        "recipient_previewed",
        "scheduled",
        "published",
        "cancelled",
        "read",
        "acknowledged",
        name="super_admin_notice_audit_action_enum",
        native_enum=False,
        create_constraint=True,
    )
    op.create_table(
        "super_admin_notices",
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", notice_status, nullable=False),
        sa.Column("audience_kind", notice_audience, nullable=False),
        sa.Column("audience_definition", FlexibleJSON(), nullable=False),
        sa.Column("channels", FlexibleJSON(), nullable=False),
        sa.Column(
            "requires_acknowledgement",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("updated_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("published_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("cancelled_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("version >= 1", name="ck_super_admin_notices_version_positive"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["cancelled_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_super_admin_notices_status_schedule", "super_admin_notices", ["status", "scheduled_at"]
    )
    op.create_table(
        "super_admin_notice_receipts",
        sa.Column("notice_id", FlexibleUUID(), nullable=False),
        sa.Column("recipient_user_id", FlexibleUUID(), nullable=False),
        sa.Column("channel", notice_channel, nullable=False),
        sa.Column("state", notice_state, nullable=False),
        sa.Column("recipient_address_hint", sa.String(length=255), nullable=True),
        sa.Column("recipient_address_digest", sa.String(length=64), nullable=True),
        sa.Column("matched_by", sa.String(length=40), nullable=False),
        sa.Column(
            "scope_all_workspaces", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "external_delivery_attempted",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "external_delivery_attempted = false",
            name="ck_super_admin_notice_receipts_local_delivery_only",
        ),
        sa.ForeignKeyConstraint(["notice_id"], ["super_admin_notices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "notice_id",
            "recipient_user_id",
            "channel",
            name="uq_super_admin_notice_receipt_recipient_channel",
        ),
    )
    op.create_index(
        "ix_super_admin_notice_receipts_user_channel_created",
        "super_admin_notice_receipts",
        ["recipient_user_id", "channel", "created_at"],
    )
    op.create_index(
        "ix_super_admin_notice_receipts_notice_state",
        "super_admin_notice_receipts",
        ["notice_id", "state"],
    )
    op.create_table(
        "super_admin_notice_receipt_workspaces",
        sa.Column("receipt_id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.PrimaryKeyConstraint(
            "receipt_id", "workspace_id", name="pk_super_admin_notice_receipt_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"], ["super_admin_notice_receipts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_super_admin_notice_receipt_workspaces_scope",
        "super_admin_notice_receipt_workspaces",
        ["workspace_id", "receipt_id"],
    )
    op.create_table(
        "super_admin_notice_audit_events",
        sa.Column("notice_id", FlexibleUUID(), nullable=False),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
        sa.Column("action", notice_action, nullable=False),
        sa.Column("details", FlexibleJSON(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["notice_id"], ["super_admin_notices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_super_admin_notice_audit_notice_occurred",
        "super_admin_notice_audit_events",
        ["notice_id", "occurred_at"],
    )
    op.create_index(
        "ix_super_admin_notice_audit_actor_occurred",
        "super_admin_notice_audit_events",
        ["actor_user_id", "occurred_at"],
    )
    op.create_index(
        "ix_super_admin_notice_audit_occurred", "super_admin_notice_audit_events", ["occurred_at"]
    )
    op.create_table(
        "super_admin_notice_idempotency",
        sa.Column("actor_user_id", FlexibleUUID(), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("key_digest", sa.String(length=64), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("response_data", FlexibleJSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_user_id",
            "operation",
            "key_digest",
            name="uq_super_admin_notice_idempotency_actor_operation_key",
        ),
    )
    op.create_index(
        "ix_super_admin_notice_idempotency_expires",
        "super_admin_notice_idempotency",
        ["expires_at"],
    )
    _enable_append_only_guards()


def downgrade() -> None:
    _disable_append_only_guards()
    op.drop_index(
        "ix_super_admin_notice_idempotency_expires", table_name="super_admin_notice_idempotency"
    )
    op.drop_table("super_admin_notice_idempotency")
    op.drop_index(
        "ix_super_admin_notice_audit_occurred", table_name="super_admin_notice_audit_events"
    )
    op.drop_index(
        "ix_super_admin_notice_audit_actor_occurred", table_name="super_admin_notice_audit_events"
    )
    op.drop_index(
        "ix_super_admin_notice_audit_notice_occurred", table_name="super_admin_notice_audit_events"
    )
    op.drop_table("super_admin_notice_audit_events")
    op.drop_index(
        "ix_super_admin_notice_receipt_workspaces_scope",
        table_name="super_admin_notice_receipt_workspaces",
    )
    op.drop_table("super_admin_notice_receipt_workspaces")
    op.drop_index(
        "ix_super_admin_notice_receipts_notice_state", table_name="super_admin_notice_receipts"
    )
    op.drop_index(
        "ix_super_admin_notice_receipts_user_channel_created",
        table_name="super_admin_notice_receipts",
    )
    op.drop_table("super_admin_notice_receipts")
    op.drop_index("ix_super_admin_notices_status_schedule", table_name="super_admin_notices")
    op.drop_table("super_admin_notices")
    op.drop_index(
        "ix_plan_policy_idempotency_created", table_name="plan_policy_idempotency_records"
    )
    op.drop_table("plan_policy_idempotency_records")
    op.drop_index("ix_plan_policy_audit_workspace_time", table_name="plan_policy_audit_events")
    op.drop_index("ix_plan_policy_audit_plan_time", table_name="plan_policy_audit_events")
    op.drop_table("plan_policy_audit_events")
    op.drop_index(
        "ix_workspace_plan_policy_current", table_name="workspace_plan_policy_assignments"
    )
    op.drop_table("workspace_plan_policy_assignments")
    op.drop_index("ix_plan_policy_version_effective", table_name="plan_policy_versions")
    op.drop_table("plan_policy_versions")
    op.drop_table("plan_policy_drafts")
