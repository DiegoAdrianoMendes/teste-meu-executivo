# ruff: noqa: E501
"""Add native collaboration facts and staff availability persistence.

Revision ID: c6e8a0b2d403
Revises: b4d6f8a0c201
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "c6e8a0b2d403"
down_revision: str | Sequence[str] | None = "b4d6f8a0c201"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLLABORATION_TABLES = (
    "klm_conversation_groups",
    "klm_conversation_message_references",
    "klm_conversation_mention_operations",
    "klm_conversation_mentions",
    "klm_conversation_group_participants",
)
STAFF_TABLES = (
    "staff_availability_profiles",
    "staff_availability_change_requests",
    "staff_recurring_availability",
    "staff_availability_exceptions",
    "staff_availability_audit_events",
)
TENANT_TABLES = COLLABORATION_TABLES + STAFF_TABLES
IMMUTABLE_TABLES = COLLABORATION_TABLES + ("staff_availability_audit_events",)
POLICY_NAME = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"


def _timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
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


def _create_collaboration_tables() -> None:
    op.create_table(
        "klm_conversation_groups",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("name", sa.String(length=220), nullable=False),
        sa.Column("created_by_user_id", FlexibleUUID()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_conv_group_workspace_thread",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_conv_group_workspace_id"),
        sa.UniqueConstraint("workspace_id", "thread_id", name="uq_conv_group_thread"),
        sa.UniqueConstraint("workspace_id", "thread_id", "id", name="uq_conv_group_thread_id"),
    )
    op.create_table(
        "klm_conversation_message_references",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("source_message_id", FlexibleUUID(), nullable=False),
        sa.Column("target_message_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "reference_type",
            sa.Enum(
                "reply",
                "quote",
                "context",
                name="conversation_reference_type_enum",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", FlexibleUUID()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint(
            "source_message_id <> target_message_id", name="ck_conv_ref_distinct_messages"
        ),
        sa.CheckConstraint("length(payload_sha256) = 64", name="ck_conv_ref_payload_hash"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_conv_ref_workspace_thread",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_conv_ref_workspace_source_message",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "target_message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_conv_ref_workspace_target_message",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_conv_ref_workspace_id"),
        sa.UniqueConstraint("workspace_id", "thread_id", "id", name="uq_conv_ref_thread_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "thread_id",
            "source_message_id",
            "id",
            name="uq_conv_ref_thread_source_id",
        ),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_conv_ref_idempotency"),
    )
    op.create_table(
        "klm_conversation_mention_operations",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("source_message_id", FlexibleUUID(), nullable=False),
        sa.Column("reference_id", FlexibleUUID()),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", FlexibleUUID()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint("length(payload_sha256) = 64", name="ck_conv_mention_op_payload_hash"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_conv_mention_op_workspace_thread",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_conv_mention_op_workspace_message",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id", "source_message_id", "reference_id"],
            [
                "klm_conversation_message_references.workspace_id",
                "klm_conversation_message_references.thread_id",
                "klm_conversation_message_references.source_message_id",
                "klm_conversation_message_references.id",
            ],
            name="fk_conv_mention_op_workspace_reference",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_conv_mention_op_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "thread_id",
            "source_message_id",
            "id",
            name="uq_conv_mention_op_message_id",
        ),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_conv_mention_op_idempotency"
        ),
    )
    op.create_table(
        "klm_conversation_mentions",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("operation_id", FlexibleUUID(), nullable=False),
        sa.Column("reference_id", FlexibleUUID()),
        sa.Column("source_message_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "mention_type",
            sa.Enum(
                "user",
                "team",
                name="conversation_mention_type_enum",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("mentioned_user_id", FlexibleUUID()),
        sa.Column("mentioned_team_id", FlexibleUUID()),
        sa.Column("resolved_label", sa.String(length=200), nullable=False),
        sa.Column("resolution_source", sa.String(length=60), nullable=False),
        sa.Column(
            "resolved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by_user_id", FlexibleUUID()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint(
            "(mention_type = 'user' AND mentioned_user_id IS NOT NULL AND mentioned_team_id IS NULL) OR (mention_type = 'team' AND mentioned_team_id IS NOT NULL AND mentioned_user_id IS NULL)",
            name="ck_conv_mention_resolved_target",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_conv_mention_workspace_thread",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_conv_mention_workspace_message",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id", "source_message_id", "operation_id"],
            [
                "klm_conversation_mention_operations.workspace_id",
                "klm_conversation_mention_operations.thread_id",
                "klm_conversation_mention_operations.source_message_id",
                "klm_conversation_mention_operations.id",
            ],
            name="fk_conv_mention_workspace_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id", "source_message_id", "reference_id"],
            [
                "klm_conversation_message_references.workspace_id",
                "klm_conversation_message_references.thread_id",
                "klm_conversation_message_references.source_message_id",
                "klm_conversation_message_references.id",
            ],
            name="fk_conv_mention_workspace_reference",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "mentioned_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_conv_mention_workspace_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "mentioned_team_id"],
            ["klm_workspace_teams.workspace_id", "klm_workspace_teams.id"],
            name="fk_conv_mention_workspace_team",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_conv_mention_workspace_id"),
    )
    op.create_table(
        "klm_conversation_group_participants",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("group_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "participant_type",
            sa.Enum(
                "user",
                "team",
                name="conversation_participant_type_enum",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("user_id", FlexibleUUID()),
        sa.Column("team_id", FlexibleUUID()),
        sa.Column(
            "role",
            sa.Enum(
                "owner",
                "admin",
                "member",
                "observer",
                name="conversation_participant_role_enum",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("resolved_label", sa.String(length=200), nullable=False),
        sa.Column("resolution_source", sa.String(length=60), nullable=False),
        sa.Column(
            "resolved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", FlexibleUUID()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint(
            "(participant_type = 'user' AND user_id IS NOT NULL AND team_id IS NULL) OR (participant_type = 'team' AND team_id IS NOT NULL AND user_id IS NULL)",
            name="ck_conv_participant_resolved_target",
        ),
        sa.CheckConstraint("length(payload_sha256) = 64", name="ck_conv_participant_payload_hash"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id", "group_id"],
            [
                "klm_conversation_groups.workspace_id",
                "klm_conversation_groups.thread_id",
                "klm_conversation_groups.id",
            ],
            name="fk_conv_participant_workspace_group",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_conv_participant_workspace_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "team_id"],
            ["klm_workspace_teams.workspace_id", "klm_workspace_teams.id"],
            name="fk_conv_participant_workspace_team",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_conv_participant_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_conv_participant_idempotency"
        ),
    )


def _create_staff_tables() -> None:
    redirect_check = "(redirect_kind IS NULL AND redirect_user_id IS NULL AND redirect_team_id IS NULL) OR (redirect_kind = 'user' AND redirect_user_id IS NOT NULL AND redirect_team_id IS NULL) OR (redirect_kind = 'team' AND redirect_user_id IS NULL AND redirect_team_id IS NOT NULL)"
    op.create_table(
        "staff_availability_profiles",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("staff_user_id", FlexibleUUID(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column(
            "schedule_configured", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "availability_mode",
            sa.Enum(
                "available",
                "away",
                name="staff_availability_mode",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="available",
        ),
        sa.Column("availability_starts_at", sa.DateTime(timezone=True)),
        sa.Column("availability_ends_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("absence_message", sa.Text()),
        sa.Column("absence_message_starts_at", sa.DateTime(timezone=True)),
        sa.Column("absence_message_ends_at", sa.DateTime(timezone=True)),
        sa.Column(
            "redirect_kind",
            sa.Enum(
                "user",
                "team",
                name="staff_availability_redirect_kind",
                native_enum=False,
                create_constraint=True,
            ),
        ),
        sa.Column("redirect_user_id", FlexibleUUID()),
        sa.Column("redirect_team_id", FlexibleUUID()),
        sa.Column("redirect_starts_at", sa.DateTime(timezone=True)),
        sa.Column("redirect_ends_at", sa.DateTime(timezone=True)),
        sa.Column(
            "auto_reassign_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("updated_by_user_id", FlexibleUUID()),
        sa.Column("id", FlexibleUUID(), nullable=False),
        *_timestamp_columns(),
        sa.CheckConstraint("version >= 0", name="ck_staff_availability_profile_version"),
        sa.CheckConstraint(redirect_check, name="ck_staff_availability_profile_redirect_target"),
        sa.CheckConstraint(
            "absence_message_starts_at IS NULL OR absence_message_ends_at IS NULL OR absence_message_ends_at > absence_message_starts_at",
            name="ck_staff_availability_profile_message_window",
        ),
        sa.CheckConstraint(
            "redirect_starts_at IS NULL OR redirect_ends_at IS NULL OR redirect_ends_at > redirect_starts_at",
            name="ck_staff_availability_profile_redirect_window",
        ),
        sa.CheckConstraint(
            "availability_starts_at IS NULL OR availability_ends_at IS NULL OR availability_ends_at > availability_starts_at",
            name="ck_staff_availability_profile_state_window",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "staff_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_staff_availability_profile_member",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "redirect_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_staff_availability_profile_redirect_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "redirect_team_id"],
            ["klm_workspace_teams.workspace_id", "klm_workspace_teams.id"],
            name="fk_staff_availability_profile_redirect_team",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "staff_user_id", name="uq_staff_availability_profile_member"
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_staff_availability_profile_workspace_id"
        ),
    )
    op.create_table(
        "staff_availability_change_requests",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("target_user_id", FlexibleUUID()),
        sa.Column("requested_by_user_id", FlexibleUUID(), nullable=False),
        sa.Column("resolved_by_user_id", FlexibleUUID()),
        sa.Column(
            "change_type",
            sa.Enum(
                "replace_configuration",
                "replace_recurring_schedule",
                "create_exception",
                "update_exception",
                "delete_exception",
                "set_absence_routing",
                name="staff_availability_change_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                "cancelled",
                name="staff_availability_request_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("proposed_change", FlexibleJSON(), nullable=False),
        sa.Column("target_entity_id", FlexibleUUID()),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("applied_version", sa.Integer()),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("decision_note", sa.String(length=1000)),
        sa.Column("decision_policy", FlexibleJSON()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("id", FlexibleUUID(), nullable=False),
        *_timestamp_columns(),
        sa.CheckConstraint("base_version >= 0", name="ck_staff_availability_request_base_version"),
        sa.CheckConstraint(
            "(status = 'pending' AND resolved_by_user_id IS NULL AND resolved_at IS NULL) OR (status <> 'pending' AND resolved_by_user_id IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_staff_availability_request_resolution",
        ),
        sa.CheckConstraint(
            "(status = 'approved' AND applied_version IS NOT NULL) OR (status <> 'approved' AND applied_version IS NULL)",
            name="ck_staff_availability_request_applied_version",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "requested_by_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_staff_availability_request_requester",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "target_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_staff_availability_request_target",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "resolved_by_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_staff_availability_request_resolver",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_staff_availability_request_workspace_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "requested_by_user_id",
            "idempotency_key",
            name="uq_staff_availability_request_idempotency",
        ),
    )
    op.create_table(
        "staff_recurring_availability",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("profile_id", FlexibleUUID(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=False),
        sa.Column("ends_at", sa.Time(), nullable=False),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_until", sa.Date()),
        sa.Column("label", sa.String(length=120)),
        sa.Column("source_request_id", FlexibleUUID(), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "weekday >= 0 AND weekday <= 6", name="ck_staff_recurring_availability_weekday"
        ),
        sa.CheckConstraint(
            "ends_at > starts_at", name="ck_staff_recurring_availability_time_range"
        ),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_until IS NULL OR valid_until >= valid_from",
            name="ck_staff_recurring_availability_date_range",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "profile_id"],
            ["staff_availability_profiles.workspace_id", "staff_availability_profiles.id"],
            name="fk_staff_recurring_availability_profile",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_request_id"],
            [
                "staff_availability_change_requests.workspace_id",
                "staff_availability_change_requests.id",
            ],
            name="fk_staff_recurring_availability_request",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "profile_id",
            "weekday",
            "starts_at",
            "ends_at",
            "valid_from",
            "valid_until",
            name="uq_staff_recurring_availability_slot",
        ),
    )
    op.create_table(
        "staff_availability_exceptions",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "scope",
            sa.Enum(
                "staff",
                "workspace",
                name="staff_availability_exception_scope",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="staff",
        ),
        sa.Column("staff_user_id", FlexibleUUID()),
        sa.Column(
            "kind",
            sa.Enum(
                "unavailable",
                "available_override",
                "vacation",
                "holiday",
                name="staff_availability_exception_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("all_day", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("absence_message", sa.Text()),
        sa.Column(
            "redirect_kind",
            sa.Enum(
                "user",
                "team",
                name="staff_availability_exception_redirect_kind",
                native_enum=False,
                create_constraint=True,
            ),
        ),
        sa.Column("redirect_user_id", FlexibleUUID()),
        sa.Column("redirect_team_id", FlexibleUUID()),
        sa.Column("auto_reassign", sa.Boolean()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_request_id", FlexibleUUID(), nullable=False),
        sa.Column("updated_by_user_id", FlexibleUUID()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("id", FlexibleUUID(), nullable=False),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "(scope = 'staff' AND staff_user_id IS NOT NULL) OR (scope = 'workspace' AND staff_user_id IS NULL AND kind = 'holiday')",
            name="ck_staff_availability_exception_scope",
        ),
        sa.CheckConstraint(
            "ends_at > starts_at", name="ck_staff_availability_exception_time_range"
        ),
        sa.CheckConstraint("version >= 1", name="ck_staff_availability_exception_version"),
        sa.CheckConstraint(redirect_check, name="ck_staff_availability_exception_redirect_target"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "staff_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_staff_availability_exception_member",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "redirect_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_staff_availability_exception_redirect_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "redirect_team_id"],
            ["klm_workspace_teams.workspace_id", "klm_workspace_teams.id"],
            name="fk_staff_availability_exception_redirect_team",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_request_id"],
            [
                "staff_availability_change_requests.workspace_id",
                "staff_availability_change_requests.id",
            ],
            name="fk_staff_availability_exception_request",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_staff_availability_exception_workspace_id"
        ),
    )
    op.create_table(
        "staff_availability_audit_events",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("staff_user_id", FlexibleUUID()),
        sa.Column("request_id", FlexibleUUID()),
        sa.Column("actor_user_id", FlexibleUUID()),
        sa.Column(
            "action",
            sa.Enum(
                "request_submitted",
                "request_approved",
                "request_rejected",
                "request_cancelled",
                "configuration_replaced",
                "schedule_replaced",
                "absence_routing_changed",
                "exception_created",
                "exception_updated",
                "exception_deleted",
                name="staff_availability_audit_action",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", FlexibleUUID()),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("before_data", FlexibleJSON()),
        sa.Column("after_data", FlexibleJSON()),
        sa.Column("correlation_id", sa.String(length=120)),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_staff_availability_audit_workspace_id"),
    )


def _create_indexes() -> None:
    for table, columns in (
        ("klm_conversation_groups", ("workspace_id",)),
        ("klm_conversation_groups", ("thread_id",)),
        ("klm_conversation_message_references", ("workspace_id",)),
        ("klm_conversation_message_references", ("thread_id",)),
        ("klm_conversation_message_references", ("source_message_id",)),
        ("klm_conversation_message_references", ("target_message_id",)),
        ("klm_conversation_mention_operations", ("workspace_id",)),
        ("klm_conversation_mention_operations", ("thread_id",)),
        ("klm_conversation_mention_operations", ("source_message_id",)),
        ("klm_conversation_mention_operations", ("reference_id",)),
        ("klm_conversation_mentions", ("workspace_id",)),
        ("klm_conversation_mentions", ("thread_id",)),
        ("klm_conversation_mentions", ("operation_id",)),
        ("klm_conversation_mentions", ("reference_id",)),
        ("klm_conversation_mentions", ("source_message_id",)),
        ("klm_conversation_group_participants", ("workspace_id",)),
        ("klm_conversation_group_participants", ("thread_id",)),
        ("klm_conversation_group_participants", ("group_id",)),
    ):
        op.create_index(f"ix_{table}_{columns[0]}", table, list(columns))
    for name, table, columns in (
        (
            "ix_conv_ref_thread_created",
            "klm_conversation_message_references",
            ["workspace_id", "thread_id", "created_at"],
        ),
        (
            "ix_conv_mention_op_message_created",
            "klm_conversation_mention_operations",
            ["workspace_id", "thread_id", "source_message_id", "created_at"],
        ),
        (
            "ix_conv_mention_message_created",
            "klm_conversation_mentions",
            ["workspace_id", "thread_id", "source_message_id", "created_at"],
        ),
        (
            "ix_conv_participant_thread_created",
            "klm_conversation_group_participants",
            ["workspace_id", "thread_id", "created_at"],
        ),
    ):
        op.create_index(name, table, columns)
    for name, column, where in (
        ("uq_conv_mention_user", "mentioned_user_id", "mentioned_user_id IS NOT NULL"),
        ("uq_conv_mention_team", "mentioned_team_id", "mentioned_team_id IS NOT NULL"),
    ):
        op.create_index(
            name,
            "klm_conversation_mentions",
            ["workspace_id", "operation_id", column],
            unique=True,
            postgresql_where=sa.text(where),
            sqlite_where=sa.text(where),
        )
    for name, column, where in (
        ("uq_conv_participant_user", "user_id", "user_id IS NOT NULL"),
        ("uq_conv_participant_team", "team_id", "team_id IS NOT NULL"),
    ):
        op.create_index(
            name,
            "klm_conversation_group_participants",
            ["workspace_id", "group_id", column],
            unique=True,
            postgresql_where=sa.text(where),
            sqlite_where=sa.text(where),
        )
    for table, columns in (
        ("staff_availability_profiles", ("workspace_id",)),
        ("staff_availability_profiles", ("staff_user_id",)),
        ("staff_availability_profiles", ("redirect_user_id",)),
        ("staff_availability_profiles", ("redirect_team_id",)),
        ("staff_recurring_availability", ("workspace_id",)),
        ("staff_recurring_availability", ("profile_id",)),
        ("staff_availability_exceptions", ("workspace_id",)),
        ("staff_availability_exceptions", ("staff_user_id",)),
        ("staff_availability_exceptions", ("kind",)),
        ("staff_availability_exceptions", ("starts_at",)),
        ("staff_availability_exceptions", ("ends_at",)),
        ("staff_availability_exceptions", ("redirect_user_id",)),
        ("staff_availability_exceptions", ("redirect_team_id",)),
        ("staff_availability_exceptions", ("deleted_at",)),
        ("staff_availability_change_requests", ("workspace_id",)),
        ("staff_availability_change_requests", ("target_user_id",)),
        ("staff_availability_change_requests", ("requested_by_user_id",)),
        ("staff_availability_change_requests", ("resolved_by_user_id",)),
        ("staff_availability_change_requests", ("change_type",)),
        ("staff_availability_change_requests", ("status",)),
        ("staff_availability_change_requests", ("target_entity_id",)),
        ("staff_availability_audit_events", ("workspace_id",)),
        ("staff_availability_audit_events", ("staff_user_id",)),
        ("staff_availability_audit_events", ("request_id",)),
        ("staff_availability_audit_events", ("actor_user_id",)),
        ("staff_availability_audit_events", ("action",)),
        ("staff_availability_audit_events", ("entity_type",)),
        ("staff_availability_audit_events", ("entity_id",)),
        ("staff_availability_audit_events", ("correlation_id",)),
        ("staff_availability_audit_events", ("occurred_at",)),
    ):
        op.create_index(f"ix_{table}_{columns[0]}", table, list(columns))
    for name, table, columns in (
        (
            "ix_staff_availability_profile_workspace_staff",
            "staff_availability_profiles",
            ["workspace_id", "staff_user_id"],
        ),
        (
            "ix_staff_recurring_availability_lookup",
            "staff_recurring_availability",
            ["workspace_id", "profile_id", "weekday"],
        ),
        (
            "ix_staff_availability_exception_lookup",
            "staff_availability_exceptions",
            ["workspace_id", "staff_user_id", "starts_at", "ends_at"],
        ),
        (
            "ix_staff_availability_request_queue",
            "staff_availability_change_requests",
            ["workspace_id", "status", "created_at"],
        ),
        (
            "ix_staff_availability_request_target",
            "staff_availability_change_requests",
            ["workspace_id", "target_user_id", "created_at"],
        ),
        (
            "ix_staff_availability_audit_history",
            "staff_availability_audit_events",
            ["workspace_id", "staff_user_id", "occurred_at"],
        ),
        (
            "ix_staff_availability_audit_request",
            "staff_availability_audit_events",
            ["workspace_id", "request_id", "occurred_at"],
        ),
    ):
        op.create_index(name, table, columns)
    op.create_index(
        "ix_staff_availability_exception_workspace_holiday",
        "staff_availability_exceptions",
        ["workspace_id", "starts_at", "ends_at"],
        postgresql_where=sa.text("scope = 'workspace' AND deleted_at IS NULL"),
        sqlite_where=sa.text("scope = 'workspace' AND deleted_at IS NULL"),
    )


def upgrade() -> None:
    _create_collaboration_tables()
    _create_staff_tables()
    _create_indexes()
    _enable_rls()
    _enable_append_only_guards()


def downgrade() -> None:
    _disable_append_only_guards()
    _disable_rls()
    for table_name in reversed(STAFF_TABLES):
        op.drop_table(table_name)
    for table_name in reversed(COLLABORATION_TABLES):
        op.drop_table(table_name)
