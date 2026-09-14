"""add tenant-native OSS state, commands, and audit trail

Revision ID: 95f6a7b8c9d0
Revises: 94e5f6a7b8c9
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "95f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "94e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_NAME = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"
TENANT_TABLES = (
    "oss_native_capability_states",
    "oss_native_commands",
    "oss_native_audit_events",
)

execution_mode_enum = sa.Enum(
    "disabled",
    "local_native",
    "external_equivalent",
    "enterprise_equivalent",
    name="oss_native_execution_mode_enum",
    native_enum=False,
    create_constraint=True,
    length=40,
)
command_execution_mode_enum = sa.Enum(
    "disabled",
    "local_native",
    "external_equivalent",
    "enterprise_equivalent",
    name="oss_native_command_execution_mode_enum",
    native_enum=False,
    create_constraint=True,
    length=40,
)
command_status_enum = sa.Enum(
    "received",
    "running",
    "succeeded",
    "failed",
    "blocked",
    "reconciliation_required",
    name="oss_native_command_status_enum",
    native_enum=False,
    create_constraint=True,
    length=40,
)
audit_action_enum = sa.Enum(
    "config_created",
    "config_updated",
    "command_received",
    "command_started",
    "command_succeeded",
    "command_failed",
    "command_blocked",
    "command_reconciliation_required",
    name="oss_native_audit_action_enum",
    native_enum=False,
    create_constraint=True,
    length=40,
)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def upgrade() -> None:
    op.create_table(
        "oss_native_capability_states",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=False),
        sa.Column("configuration", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "execution_mode",
            execution_mode_enum,
            nullable=False,
            server_default="disabled",
        ),
        sa.Column("updated_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "capability",
            name="uq_oss_native_state_workspace_provider_capability",
        ),
    )
    op.create_index(
        "ix_oss_native_state_workspace_provider",
        "oss_native_capability_states",
        ["workspace_id", "provider"],
    )

    op.create_table(
        "oss_native_commands",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("state_id", FlexibleUUID(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=False),
        sa.Column("command_type", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "command_payload",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "status",
            command_status_enum,
            nullable=False,
            server_default="received",
        ),
        sa.Column("execution_mode", command_execution_mode_enum, nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("command_result", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "dispatcher_invoked", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "external_network_allowed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["state_id"], ["oss_native_capability_states.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "capability",
            "idempotency_key",
            name="uq_oss_native_command_workspace_provider_capability_key",
        ),
    )
    op.create_index(
        "ix_oss_native_command_workspace_capability_created",
        "oss_native_commands",
        ["workspace_id", "provider", "capability", "created_at"],
    )
    op.create_index(
        "ix_oss_native_command_workspace_status",
        "oss_native_commands",
        ["workspace_id", "status"],
    )

    op.create_table(
        "oss_native_audit_events",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("state_id", FlexibleUUID(), nullable=True),
        sa.Column("command_id", FlexibleUUID(), nullable=True),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=False),
        sa.Column("action", audit_action_enum, nullable=False),
        sa.Column("summary", sa.String(length=240), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=True),
        sa.Column("metadata", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["state_id"], ["oss_native_capability_states.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["command_id"], ["oss_native_commands.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_oss_native_audit_workspace_capability_occurred",
        "oss_native_audit_events",
        ["workspace_id", "provider", "capability", "occurred_at"],
    )
    op.create_index(
        "ix_oss_native_audit_workspace_action",
        "oss_native_audit_events",
        ["workspace_id", "action"],
    )

    if op.get_bind().dialect.name == "postgresql":
        predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
        for table_name in TENANT_TABLES:
            op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f"""
                CREATE POLICY "{POLICY_NAME}" ON "{table_name}"
                AS PERMISSIVE FOR ALL TO PUBLIC
                USING ({predicate}) WITH CHECK ({predicate})
                """
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table_name in reversed(TENANT_TABLES):
            op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table_name}"')
            op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')

    op.drop_index("ix_oss_native_audit_workspace_action", table_name="oss_native_audit_events")
    op.drop_index(
        "ix_oss_native_audit_workspace_capability_occurred",
        table_name="oss_native_audit_events",
    )
    op.drop_table("oss_native_audit_events")
    op.drop_index("ix_oss_native_command_workspace_status", table_name="oss_native_commands")
    op.drop_index(
        "ix_oss_native_command_workspace_capability_created",
        table_name="oss_native_commands",
    )
    op.drop_table("oss_native_commands")
    op.drop_index(
        "ix_oss_native_state_workspace_provider",
        table_name="oss_native_capability_states",
    )
    op.drop_table("oss_native_capability_states")
