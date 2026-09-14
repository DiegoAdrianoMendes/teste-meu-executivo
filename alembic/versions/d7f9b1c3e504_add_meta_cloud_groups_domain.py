# ruff: noqa: E501
"""Add the tenant-scoped, local-only Meta Cloud API Groups domain.

Revision ID: d7f9b1c3e504
Revises: c6e8a0b2d403
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "d7f9b1c3e504"
down_revision: str | Sequence[str] | None = "c6e8a0b2d403"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "klm_meta_group_capabilities",
    "klm_meta_groups",
    "klm_meta_group_participants",
    "klm_meta_group_operations",
    "klm_meta_group_lifecycle_events",
)
IMMUTABLE_TABLES = ("klm_meta_group_lifecycle_events",)
POLICY_NAME = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"


def _id_column() -> sa.Column:
    return sa.Column("id", FlexibleUUID(), nullable=False)


def _timestamp_columns() -> tuple[sa.Column, sa.Column]:
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


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    for table_name in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{POLICY_NAME}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in reversed(TENANT_TABLES):
        op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def _enable_append_only_guards() -> None:
    dialect = op.get_bind().dialect.name
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
        for table_name in IMMUTABLE_TABLES:
            op.execute(
                f'''CREATE TRIGGER "trg_{table_name}_immutable"
                BEFORE UPDATE OR DELETE ON "{table_name}"
                FOR EACH ROW EXECUTE FUNCTION klm_reject_append_only_mutation()'''
            )
            op.execute(
                f'''CREATE TRIGGER "trg_{table_name}_immutable_truncate"
                BEFORE TRUNCATE ON "{table_name}"
                FOR EACH STATEMENT EXECUTE FUNCTION klm_reject_append_only_mutation()'''
            )
    elif dialect == "sqlite":
        for table_name in IMMUTABLE_TABLES:
            for operation in ("update", "delete"):
                op.execute(
                    f'''CREATE TRIGGER IF NOT EXISTS "trg_{table_name}_immutable_{operation}"
                    BEFORE {operation.upper()} ON "{table_name}"
                    BEGIN
                        SELECT RAISE(ABORT, '{table_name} is immutable');
                    END'''
                )


def _disable_append_only_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table_name in reversed(IMMUTABLE_TABLES):
            op.execute(
                f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable_truncate" ON "{table_name}"'
            )
            op.execute(f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable" ON "{table_name}"')
    elif dialect == "sqlite":
        for table_name in reversed(IMMUTABLE_TABLES):
            for operation in ("update", "delete"):
                op.execute(f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable_{operation}"')


def _create_capability_table() -> None:
    op.create_table(
        "klm_meta_group_capabilities",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("phone_number_id", sa.String(length=180), nullable=False),
        sa.Column("waba_id", sa.String(length=180)),
        sa.Column(
            "developer_app_configured",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "webhook_callback_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "developer_app_state",
            sa.String(length=24),
            nullable=False,
            server_default="not_ready",
        ),
        sa.Column("required_webhook_fields", FlexibleJSON(), nullable=False),
        sa.Column("subscribed_webhook_fields", FlexibleJSON(), nullable=False),
        sa.Column(
            "oba_eligibility_state",
            sa.String(length=24),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "oba_evidence_source",
            sa.String(length=32),
            nullable=False,
            server_default="unverified",
        ),
        sa.Column("oba_evidence_reference", sa.String(length=500)),
        sa.Column("oba_evidence_recorded_at", sa.DateTime(timezone=True)),
        sa.Column(
            "product_mode",
            sa.String(length=32),
            nullable=False,
            server_default="unsupported",
        ),
        sa.Column("max_participants", sa.Integer(), nullable=False, server_default="8"),
        sa.Column(
            "max_groups_per_business_number",
            sa.Integer(),
            nullable=False,
            server_default="10000",
        ),
        sa.Column("invite_only", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("evidence_metadata", FlexibleJSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *_timestamp_columns(),
        _id_column(),
        sa.CheckConstraint(
            "developer_app_state IN ('not_ready', 'ready')",
            name="ck_klm_meta_group_capability_app_state",
        ),
        sa.CheckConstraint(
            "oba_eligibility_state IN ('unknown', 'eligible', 'ineligible')",
            name="ck_klm_meta_group_capability_oba_state",
        ),
        sa.CheckConstraint(
            "oba_evidence_source IN ('unverified', 'meta_api', 'meta_business_manager')",
            name="ck_klm_meta_group_capability_oba_source",
        ),
        sa.CheckConstraint(
            "product_mode IN ('official_api', 'cloud_api', 'business_app', 'coexistence', 'multi_solution', 'unsupported')",
            name="ck_klm_meta_group_capability_product_mode",
        ),
        sa.CheckConstraint(
            "max_participants = 8 AND max_groups_per_business_number = 10000 AND invite_only = true",
            name="ck_klm_meta_group_capability_official_limits",
        ),
        sa.CheckConstraint(
            "oba_evidence_source = 'unverified' OR (oba_evidence_reference IS NOT NULL AND oba_evidence_recorded_at IS NOT NULL)",
            name="ck_klm_meta_group_capability_external_evidence",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_meta_group_capability_connection",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_meta_group_capability_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "connection_id",
            name="uq_klm_meta_group_capability_connection",
        ),
    )


def _create_group_table() -> None:
    op.create_table(
        "klm_meta_groups",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("capability_id", FlexibleUUID()),
        sa.Column("created_by_user_id", FlexibleUUID()),
        sa.Column(
            "group_kind",
            sa.String(length=32),
            nullable=False,
            server_default="local_internal",
        ),
        sa.Column("provider_group_id", sa.String(length=512)),
        sa.Column("provider_request_id", sa.String(length=255)),
        sa.Column("subject", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=2048)),
        sa.Column(
            "join_approval_mode",
            sa.String(length=32),
            nullable=False,
            server_default="auto_approve",
        ),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="local_ready"),
        sa.Column("invite_link", sa.String(length=1000)),
        sa.Column("participant_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("create_idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("request_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("last_lifecycle_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("group_metadata", FlexibleJSON(), nullable=False),
        *_timestamp_columns(),
        _id_column(),
        sa.CheckConstraint(
            "group_kind IN ('local_internal', 'meta_cloud_group')",
            name="ck_klm_meta_group_kind",
        ),
        sa.CheckConstraint(
            "state IN ('local_ready', 'create_pending', 'active', 'delete_pending', 'deleted', 'failed')",
            name="ck_klm_meta_group_state",
        ),
        sa.CheckConstraint(
            "join_approval_mode IN ('auto_approve', 'approval_required')",
            name="ck_klm_meta_group_join_approval",
        ),
        sa.CheckConstraint(
            "participant_count >= 1 AND participant_count <= 8",
            name="ck_klm_meta_group_participant_count",
        ),
        sa.CheckConstraint(
            "length(request_payload_sha256) = 64",
            name="ck_klm_meta_group_payload_sha256",
        ),
        sa.CheckConstraint(
            "(group_kind = 'local_internal' AND provider_group_id IS NULL) OR (group_kind = 'meta_cloud_group' AND provider_group_id IS NOT NULL)",
            name="ck_klm_meta_group_official_provider_id",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_meta_group_connection",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "capability_id"],
            [
                "klm_meta_group_capabilities.workspace_id",
                "klm_meta_group_capabilities.id",
            ],
            name="fk_klm_meta_group_capability",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_meta_group_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "create_idempotency_key",
            name="uq_klm_meta_group_create_idempotency",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "connection_id",
            "provider_group_id",
            name="uq_klm_meta_group_provider_id",
        ),
    )


def _create_participant_table() -> None:
    op.create_table(
        "klm_meta_group_participants",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("group_id", FlexibleUUID(), nullable=False),
        sa.Column("provider_user_id", sa.String(length=255), nullable=False),
        sa.Column("wa_id", sa.String(length=80)),
        sa.Column("business_scoped_user_id", sa.String(length=255)),
        sa.Column("username", sa.String(length=255)),
        sa.Column("join_request_id", sa.String(length=255)),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="invited"),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False, server_default="member"),
        sa.Column("joined_at", sa.DateTime(timezone=True)),
        sa.Column("left_at", sa.DateTime(timezone=True)),
        sa.Column("participant_metadata", FlexibleJSON(), nullable=False),
        *_timestamp_columns(),
        _id_column(),
        sa.CheckConstraint(
            "state IN ('invited', 'join_requested', 'joined', 'left', 'removed', 'rejected')",
            name="ck_klm_meta_group_participant_state",
        ),
        sa.CheckConstraint(
            "source IN ('invite_link', 'join_request', 'provider_webhook')",
            name="ck_klm_meta_group_participant_source",
        ),
        sa.CheckConstraint(
            "role IN ('member', 'admin')", name="ck_klm_meta_group_participant_role"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "group_id"],
            ["klm_meta_groups.workspace_id", "klm_meta_groups.id"],
            name="fk_klm_meta_group_participant_group",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_klm_meta_group_participant_workspace_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "group_id",
            "provider_user_id",
            name="uq_klm_meta_group_participant_provider_user",
        ),
    )


def _create_operation_table() -> None:
    op.create_table(
        "klm_meta_group_operations",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("group_id", FlexibleUUID()),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("requested_by_user_id", FlexibleUUID()),
        sa.Column("operation_type", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="local_ready"),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("request_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_payload", FlexibleJSON(), nullable=False),
        sa.Column("capability_snapshot", FlexibleJSON(), nullable=False),
        sa.Column(
            "external_dispatch_executed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        *_timestamp_columns(),
        _id_column(),
        sa.CheckConstraint(
            "operation_type IN ('create_local_group', 'prepare_official_group_creation', 'prepare_group_message')",
            name="ck_klm_meta_group_operation_type",
        ),
        sa.CheckConstraint(
            "state IN ('local_ready', 'blocked_by_capability')",
            name="ck_klm_meta_group_operation_state",
        ),
        sa.CheckConstraint(
            "length(request_payload_sha256) = 64",
            name="ck_klm_meta_group_operation_payload_sha256",
        ),
        sa.CheckConstraint(
            "external_dispatch_executed = false",
            name="ck_klm_meta_group_operation_no_dispatch",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "group_id"],
            ["klm_meta_groups.workspace_id", "klm_meta_groups.id"],
            name="fk_klm_meta_group_operation_group",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_meta_group_operation_connection",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_meta_group_operation_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_klm_meta_group_operation_idempotency",
        ),
    )


def _create_lifecycle_table() -> None:
    op.create_table(
        "klm_meta_group_lifecycle_events",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("group_id", FlexibleUUID()),
        sa.Column("operation_id", FlexibleUUID()),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("actor_user_id", FlexibleUUID()),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("webhook_field", sa.String(length=64)),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("external_event_id", sa.String(length=255)),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("event_payload", FlexibleJSON(), nullable=False),
        sa.Column("state_snapshot", FlexibleJSON(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        _id_column(),
        sa.CheckConstraint(
            "source IN ('local_control', 'provider_webhook')",
            name="ck_klm_meta_group_event_source",
        ),
        sa.CheckConstraint(
            "(source = 'local_control' AND webhook_field IS NULL) OR (source = 'provider_webhook' AND webhook_field IN ('group_lifecycle_update', 'group_participants_update', 'group_settings_update', 'group_status_update'))",
            name="ck_klm_meta_group_event_webhook_field",
        ),
        sa.CheckConstraint(
            "length(payload_sha256) = 64", name="ck_klm_meta_group_event_payload_sha256"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "group_id"],
            ["klm_meta_groups.workspace_id", "klm_meta_groups.id"],
            name="fk_klm_meta_group_event_group",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["klm_meta_group_operations.workspace_id", "klm_meta_group_operations.id"],
            name="fk_klm_meta_group_event_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_meta_group_event_connection",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_meta_group_event_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_klm_meta_group_event_idempotency",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "connection_id",
            "external_event_id",
            name="uq_klm_meta_group_event_external",
        ),
    )


def _create_indexes() -> None:
    for table_name, column_name in (
        ("klm_meta_group_capabilities", "workspace_id"),
        ("klm_meta_group_capabilities", "connection_id"),
        ("klm_meta_group_capabilities", "phone_number_id"),
        ("klm_meta_groups", "workspace_id"),
        ("klm_meta_groups", "connection_id"),
        ("klm_meta_groups", "capability_id"),
        ("klm_meta_groups", "created_by_user_id"),
        ("klm_meta_groups", "state"),
        ("klm_meta_groups", "last_lifecycle_at"),
        ("klm_meta_group_participants", "workspace_id"),
        ("klm_meta_group_participants", "group_id"),
        ("klm_meta_group_participants", "state"),
        ("klm_meta_group_operations", "workspace_id"),
        ("klm_meta_group_operations", "group_id"),
        ("klm_meta_group_operations", "connection_id"),
        ("klm_meta_group_operations", "requested_by_user_id"),
        ("klm_meta_group_operations", "operation_type"),
        ("klm_meta_group_operations", "state"),
        ("klm_meta_group_lifecycle_events", "workspace_id"),
        ("klm_meta_group_lifecycle_events", "group_id"),
        ("klm_meta_group_lifecycle_events", "operation_id"),
        ("klm_meta_group_lifecycle_events", "connection_id"),
        ("klm_meta_group_lifecycle_events", "actor_user_id"),
        ("klm_meta_group_lifecycle_events", "webhook_field"),
        ("klm_meta_group_lifecycle_events", "event_type"),
        ("klm_meta_group_lifecycle_events", "occurred_at"),
    ):
        op.create_index(f"ix_{table_name}_{column_name}", table_name, [column_name], unique=False)
    for name, table_name, columns in (
        (
            "ix_klm_meta_group_workspace_connection_state",
            "klm_meta_groups",
            ["workspace_id", "connection_id", "state"],
        ),
        (
            "ix_klm_meta_group_participant_group_state",
            "klm_meta_group_participants",
            ["workspace_id", "group_id", "state"],
        ),
        (
            "ix_klm_meta_group_operation_workspace_state",
            "klm_meta_group_operations",
            ["workspace_id", "state", "created_at"],
        ),
        (
            "ix_klm_meta_group_event_group_time",
            "klm_meta_group_lifecycle_events",
            ["workspace_id", "group_id", "occurred_at"],
        ),
    ):
        op.create_index(name, table_name, columns, unique=False)


def upgrade() -> None:
    _create_capability_table()
    _create_group_table()
    _create_participant_table()
    _create_operation_table()
    _create_lifecycle_table()
    _create_indexes()
    _enable_rls()
    _enable_append_only_guards()


def downgrade() -> None:
    _disable_append_only_guards()
    _disable_rls()
    for table_name in reversed(TENANT_TABLES):
        op.drop_table(table_name)
