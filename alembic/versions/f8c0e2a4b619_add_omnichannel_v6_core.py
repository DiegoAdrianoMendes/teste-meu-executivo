"""Add canonical omnichannel V6 identity, routing and event core.

Revision ID: f8c0e2a4b619
Revises: f6b8d0e2a417
Create Date: 2026-07-20 19:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "f8c0e2a4b619"
down_revision: str | Sequence[str] | None = "f6b8d0e2a417"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"


def _id() -> sa.Column:
    return sa.Column("id", FlexibleUUID(), nullable=False)


def _workspace() -> sa.Column:
    return sa.Column("workspace_id", FlexibleUUID(), nullable=False)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _enable_rls(table_names: tuple[str, ...]) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    for table_name in table_names:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def _disable_rls(table_names: tuple[str, ...]) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in table_names:
        op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


NEW_TABLES = (
    "klm_omnichannel_identities",
    "klm_workspace_teams",
    "klm_workspace_team_members",
    "klm_omnichannel_endpoints",
    "klm_omnichannel_thread_bindings",
    "klm_omnichannel_channel_events",
    "klm_omnichannel_routing_events",
    "klm_lead_capture_receipts",
    "klm_automation_trigger_receipts",
    "klm_omnichannel_scheduled_messages",
    "klm_omnichannel_call_sessions",
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for enum_name in (
            "klm_omnichannel_channel_enum",
            "klm_message_channel_enum",
            "klm_follow_up_channel_enum",
        ):
            for value in ("facebook", "messenger", "tiktok", "webchat"):
                op.execute(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'")

    op.create_table(
        "klm_omnichannel_identities",
        _workspace(),
        sa.Column("client_id", FlexibleUUID(), nullable=True),
        sa.Column("matter_id", FlexibleUUID(), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("canonical_phone", sa.String(length=32), nullable=True),
        sa.Column("canonical_email", sa.String(length=255), nullable=True),
        sa.Column(
            "identity_state", sa.String(length=32), nullable=False, server_default="provisional"
        ),
        sa.Column("source", sa.String(length=60), nullable=False, server_default="local"),
        sa.Column("merge_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "identity_metadata", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        _created_at(),
        _updated_at(),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_klm_identity_workspace_client",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_klm_identity_workspace_matter",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_identity_workspace_id"),
    )
    op.create_index(
        "uq_klm_identity_verified_phone",
        "klm_omnichannel_identities",
        ["workspace_id", "canonical_phone"],
        unique=True,
        postgresql_where=sa.text("canonical_phone IS NOT NULL"),
        sqlite_where=sa.text("canonical_phone IS NOT NULL"),
    )
    op.create_index(
        "uq_klm_identity_verified_email",
        "klm_omnichannel_identities",
        ["workspace_id", "canonical_email"],
        unique=True,
        postgresql_where=sa.text("canonical_email IS NOT NULL"),
        sqlite_where=sa.text("canonical_email IS NOT NULL"),
    )
    op.create_index(
        "ix_klm_omnichannel_identities_workspace_id", "klm_omnichannel_identities", ["workspace_id"]
    )
    op.create_index(
        "ix_klm_omnichannel_identities_client_id", "klm_omnichannel_identities", ["client_id"]
    )
    op.create_index(
        "ix_klm_omnichannel_identities_matter_id", "klm_omnichannel_identities", ["matter_id"]
    )
    op.create_index(
        "ix_klm_omnichannel_identities_identity_state",
        "klm_omnichannel_identities",
        ["identity_state"],
    )

    op.create_table(
        "klm_workspace_teams",
        _workspace(),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("team_type", sa.String(length=40), nullable=False, server_default="service"),
        sa.Column("color", sa.String(length=16), nullable=False, server_default="#1a9948"),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "routing_strategy", sa.String(length=40), nullable=False, server_default="manual"
        ),
        sa.Column("schedule", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _created_at(),
        _updated_at(),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_team_workspace_id"),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_klm_team_workspace_slug"),
    )
    op.create_index("ix_klm_workspace_teams_workspace_id", "klm_workspace_teams", ["workspace_id"])
    op.create_index("ix_klm_workspace_teams_is_active", "klm_workspace_teams", ["is_active"])

    op.create_table(
        "klm_workspace_team_members",
        _workspace(),
        sa.Column("team_id", FlexibleUUID(), nullable=False),
        sa.Column("membership_id", FlexibleUUID(), nullable=False),
        sa.Column("user_id", FlexibleUUID(), nullable=False),
        sa.Column("member_role", sa.String(length=40), nullable=False, server_default="member"),
        sa.Column("routing_weight", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("max_concurrent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default=sa.true()),
        _created_at(),
        _updated_at(),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "team_id"],
            ["klm_workspace_teams.workspace_id", "klm_workspace_teams.id"],
            name="fk_klm_team_member_workspace_team",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id", "workspace_id", "user_id"],
            [
                "workspace_memberships.id",
                "workspace_memberships.workspace_id",
                "workspace_memberships.user_id",
            ],
            name="fk_klm_team_member_membership_binding",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "team_id", "user_id", name="uq_klm_team_member_user"),
    )
    for column in ("workspace_id", "team_id", "membership_id", "user_id"):
        op.create_index(
            f"ix_klm_workspace_team_members_{column}", "klm_workspace_team_members", [column]
        )

    with op.batch_alter_table("klm_omnichannel_threads") as batch:
        batch.add_column(sa.Column("identity_id", FlexibleUUID(), nullable=True))
        batch.add_column(sa.Column("assigned_team_id", FlexibleUUID(), nullable=True))
        batch.add_column(
            sa.Column("queue", sa.String(length=60), nullable=False, server_default="new")
        )
        batch.add_column(sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(
            sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("preferred_channel", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_outbound_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("first_response_due_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("resolution_due_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("closed_reason", sa.String(length=180), nullable=True))
        batch.add_column(
            sa.Column(
                "policy_state", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")
            )
        )
        batch.add_column(
            sa.Column("cost_state", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch.create_foreign_key(
            "fk_klm_omnichannel_thread_workspace_identity",
            "klm_omnichannel_identities",
            ["workspace_id", "identity_id"],
            ["workspace_id", "id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_klm_omnichannel_thread_workspace_team",
            "klm_workspace_teams",
            ["workspace_id", "assigned_team_id"],
            ["workspace_id", "id"],
            ondelete="RESTRICT",
        )
    for column in ("identity_id", "assigned_team_id", "queue"):
        op.create_index(f"ix_klm_omnichannel_threads_{column}", "klm_omnichannel_threads", [column])
    op.create_index(
        "ix_klm_thread_workspace_queue_activity",
        "klm_omnichannel_threads",
        ["workspace_id", "queue", "status", "last_message_at"],
    )
    op.create_index(
        "uq_klm_thread_workspace_identity",
        "klm_omnichannel_threads",
        ["workspace_id", "identity_id"],
        unique=True,
        postgresql_where=sa.text("identity_id IS NOT NULL"),
        sqlite_where=sa.text("identity_id IS NOT NULL"),
    )

    op.create_table(
        "klm_omnichannel_endpoints",
        _workspace(),
        sa.Column("identity_id", FlexibleUUID(), nullable=False),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=True),
        sa.Column("connection_key", sa.String(length=120), nullable=False, server_default="local"),
        sa.Column("external_account_id", sa.String(length=180), nullable=True),
        sa.Column("external_user_id", sa.String(length=255), nullable=False),
        sa.Column("address", sa.String(length=255), nullable=True),
        sa.Column("normalized_address", sa.String(length=255), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_state", sa.String(length=40), nullable=False, server_default="unknown"),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capabilities", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "endpoint_metadata", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        _created_at(),
        _updated_at(),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "identity_id"],
            ["klm_omnichannel_identities.workspace_id", "klm_omnichannel_identities.id"],
            name="fk_klm_endpoint_workspace_identity",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_endpoint_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            "external_user_id",
            name="uq_klm_endpoint_provider_user",
        ),
    )
    for column in (
        "workspace_id",
        "identity_id",
        "channel",
        "provider",
        "connection_id",
        "consent_state",
    ):
        op.create_index(
            f"ix_klm_omnichannel_endpoints_{column}", "klm_omnichannel_endpoints", [column]
        )
    op.create_index(
        "ix_klm_endpoint_workspace_address",
        "klm_omnichannel_endpoints",
        ["workspace_id", "channel", "normalized_address"],
    )

    op.create_table(
        "klm_omnichannel_thread_bindings",
        _workspace(),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("endpoint_id", FlexibleUUID(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=True),
        sa.Column("connection_key", sa.String(length=120), nullable=False),
        sa.Column("external_thread_id", sa.String(length=255), nullable=False),
        sa.Column("binding_state", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("last_cursor", sa.String(length=500), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "binding_metadata", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        _created_at(),
        _updated_at(),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_binding_workspace_thread",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "endpoint_id"],
            ["klm_omnichannel_endpoints.workspace_id", "klm_omnichannel_endpoints.id"],
            name="fk_klm_binding_workspace_endpoint",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_binding_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            "external_thread_id",
            name="uq_klm_binding_provider_thread",
        ),
    )
    for column in ("workspace_id", "thread_id", "endpoint_id", "provider", "connection_id"):
        op.create_index(
            f"ix_klm_omnichannel_thread_bindings_{column}",
            "klm_omnichannel_thread_bindings",
            [column],
        )

    op.create_table(
        "klm_omnichannel_channel_events",
        _workspace(),
        sa.Column("identity_id", FlexibleUUID(), nullable=True),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("binding_id", FlexibleUUID(), nullable=True),
        sa.Column("parent_event_id", FlexibleUUID(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("connection_key", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("actor_external_id", sa.String(length=255), nullable=True),
        sa.Column(
            "visibility", sa.String(length=40), nullable=False, server_default="client_private"
        ),
        sa.Column("public_url", sa.String(length=1000), nullable=True),
        sa.Column(
            "actionability", sa.String(length=40), nullable=False, server_default="informational"
        ),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("correlation", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("event_payload", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("event_metadata", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "identity_id"],
            ["klm_omnichannel_identities.workspace_id", "klm_omnichannel_identities.id"],
            name="fk_klm_channel_event_workspace_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_channel_event_workspace_thread",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "binding_id"],
            ["klm_omnichannel_thread_bindings.workspace_id", "klm_omnichannel_thread_bindings.id"],
            name="fk_klm_channel_event_workspace_binding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "parent_event_id"],
            ["klm_omnichannel_channel_events.workspace_id", "klm_omnichannel_channel_events.id"],
            name="fk_klm_channel_event_workspace_parent",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_channel_event_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            "external_event_id",
            name="uq_klm_channel_event_external",
        ),
    )
    for column in (
        "workspace_id",
        "identity_id",
        "thread_id",
        "binding_id",
        "parent_event_id",
        "provider",
        "event_type",
        "occurred_at",
    ):
        op.create_index(
            f"ix_klm_omnichannel_channel_events_{column}",
            "klm_omnichannel_channel_events",
            [column],
        )
    op.create_index(
        "ix_klm_channel_event_workspace_thread_time",
        "klm_omnichannel_channel_events",
        ["workspace_id", "thread_id", "occurred_at"],
    )

    op.create_table(
        "klm_omnichannel_routing_events",
        _workspace(),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
        sa.Column("from_user_id", FlexibleUUID(), nullable=True),
        sa.Column("to_user_id", FlexibleUUID(), nullable=True),
        sa.Column("from_team_id", FlexibleUUID(), nullable=True),
        sa.Column("to_team_id", FlexibleUUID(), nullable=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="human"),
        sa.Column("expected_thread_version", sa.Integer(), nullable=False),
        sa.Column("result_thread_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("correlation_id", sa.String(length=180), nullable=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_routing_event_workspace_thread",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "from_team_id"],
            ["klm_workspace_teams.workspace_id", "klm_workspace_teams.id"],
            name="fk_klm_routing_event_workspace_from_team",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "to_team_id"],
            ["klm_workspace_teams.workspace_id", "klm_workspace_teams.id"],
            name="fk_klm_routing_event_workspace_to_team",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_klm_routing_event_idempotency"
        ),
    )
    for column in (
        "workspace_id",
        "thread_id",
        "actor_user_id",
        "action",
        "correlation_id",
        "occurred_at",
    ):
        op.create_index(
            f"ix_klm_omnichannel_routing_events_{column}",
            "klm_omnichannel_routing_events",
            [column],
        )

    op.create_table(
        "klm_lead_capture_receipts",
        _workspace(),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("connection_key", sa.String(length=120), nullable=False),
        sa.Column("external_lead_id", sa.String(length=255), nullable=False),
        sa.Column("external_form_id", sa.String(length=255), nullable=True),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="processing"),
        sa.Column("mapping_version", sa.String(length=40), nullable=False, server_default="v1"),
        sa.Column("identity_id", FlexibleUUID(), nullable=True),
        sa.Column("thread_id", FlexibleUUID(), nullable=True),
        sa.Column("matter_id", FlexibleUUID(), nullable=True),
        sa.Column("result", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "identity_id"],
            ["klm_omnichannel_identities.workspace_id", "klm_omnichannel_identities.id"],
            name="fk_klm_lead_capture_workspace_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_lead_capture_workspace_thread",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_klm_lead_capture_workspace_matter",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            "external_lead_id",
            name="uq_klm_lead_capture_external",
        ),
    )
    for column in (
        "workspace_id",
        "provider",
        "external_form_id",
        "state",
        "identity_id",
        "thread_id",
        "matter_id",
    ):
        op.create_index(
            f"ix_klm_lead_capture_receipts_{column}", "klm_lead_capture_receipts", [column]
        )

    op.create_table(
        "klm_automation_trigger_receipts",
        _workspace(),
        sa.Column("channel_event_id", FlexibleUUID(), nullable=False),
        sa.Column("flow_revision_id", FlexibleUUID(), nullable=False),
        sa.Column("action_key", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="claimed"),
        sa.Column("result", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        _created_at(),
        _updated_at(),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "channel_event_id"],
            ["klm_omnichannel_channel_events.workspace_id", "klm_omnichannel_channel_events.id"],
            name="fk_klm_automation_trigger_workspace_event",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "channel_event_id",
            "flow_revision_id",
            "action_key",
            name="uq_klm_automation_trigger_once",
        ),
    )
    for column in ("workspace_id", "channel_event_id", "flow_revision_id", "state"):
        op.create_index(
            f"ix_klm_automation_trigger_receipts_{column}",
            "klm_automation_trigger_receipts",
            [column],
        )

    op.create_table(
        "klm_omnichannel_scheduled_messages",
        _workspace(),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("endpoint_id", FlexibleUUID(), nullable=True),
        sa.Column("connection_key", sa.String(length=120), nullable=False),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("template_id", sa.String(length=180), nullable=True),
        sa.Column("attachments", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "timezone", sa.String(length=80), nullable=False, server_default="America/Sao_Paulo"
        ),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="scheduled"),
        sa.Column("lease_owner", sa.String(length=180), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column(
            "provider_receipt", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        _created_at(),
        _updated_at(),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_schedule_workspace_thread",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "endpoint_id"],
            ["klm_omnichannel_endpoints.workspace_id", "klm_omnichannel_endpoints.id"],
            name="fk_klm_schedule_workspace_endpoint",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_klm_schedule_idempotency"),
    )
    for column in ("workspace_id", "thread_id", "endpoint_id", "channel", "scheduled_at", "state"):
        op.create_index(
            f"ix_klm_omnichannel_scheduled_messages_{column}",
            "klm_omnichannel_scheduled_messages",
            [column],
        )
    op.create_index(
        "ix_klm_schedule_due",
        "klm_omnichannel_scheduled_messages",
        ["state", "scheduled_at", "next_attempt_at"],
    )

    op.create_table(
        "klm_omnichannel_call_sessions",
        _workspace(),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("endpoint_id", FlexibleUUID(), nullable=True),
        sa.Column("connection_key", sa.String(length=120), nullable=False),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="requested"),
        sa.Column(
            "permission_state", sa.String(length=32), nullable=False, server_default="unknown"
        ),
        sa.Column("provider_call_id", sa.String(length=255), nullable=True),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("transcript_object_id", FlexibleUUID(), nullable=True),
        sa.Column("recording_object_id", FlexibleUUID(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("cost_amount", sa.Numeric(15, 6), nullable=True),
        sa.Column("cost_currency", sa.String(length=3), nullable=True),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("call_metadata", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        _created_at(),
        _updated_at(),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_call_workspace_thread",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "endpoint_id"],
            ["klm_omnichannel_endpoints.workspace_id", "klm_omnichannel_endpoints.id"],
            name="fk_klm_call_workspace_endpoint",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_klm_call_idempotency"),
    )
    for column in ("workspace_id", "thread_id", "endpoint_id", "state"):
        op.create_index(
            f"ix_klm_omnichannel_call_sessions_{column}", "klm_omnichannel_call_sessions", [column]
        )

    with op.batch_alter_table("klm_omnichannel_messages") as batch:
        batch.add_column(sa.Column("endpoint_id", FlexibleUUID(), nullable=True))
        batch.add_column(sa.Column("binding_id", FlexibleUUID(), nullable=True))
        batch.add_column(
            sa.Column(
                "audience", sa.String(length=40), nullable=False, server_default="client_private"
            )
        )
        batch.add_column(
            sa.Column("content_type", sa.String(length=40), nullable=False, server_default="text")
        )
        batch.add_column(sa.Column("language", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("provider_status", sa.String(length=80), nullable=True))
        batch.add_column(
            sa.Column("transport", sa.String(length=60), nullable=False, server_default="local")
        )
        batch.add_column(sa.Column("cost_amount", sa.Numeric(15, 6), nullable=True))
        batch.add_column(sa.Column("cost_currency", sa.String(length=3), nullable=True))
        batch.add_column(sa.Column("window_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("template_id", sa.String(length=180), nullable=True))
        batch.create_foreign_key(
            "fk_klm_omnichannel_message_workspace_endpoint",
            "klm_omnichannel_endpoints",
            ["workspace_id", "endpoint_id"],
            ["workspace_id", "id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_klm_omnichannel_message_workspace_binding",
            "klm_omnichannel_thread_bindings",
            ["workspace_id", "binding_id"],
            ["workspace_id", "id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "ix_klm_omnichannel_messages_endpoint_id", "klm_omnichannel_messages", ["endpoint_id"]
    )
    op.create_index(
        "ix_klm_omnichannel_messages_binding_id", "klm_omnichannel_messages", ["binding_id"]
    )

    _enable_rls(NEW_TABLES)


def downgrade() -> None:
    _disable_rls(NEW_TABLES)

    op.drop_index("ix_klm_omnichannel_messages_binding_id", table_name="klm_omnichannel_messages")
    op.drop_index("ix_klm_omnichannel_messages_endpoint_id", table_name="klm_omnichannel_messages")
    with op.batch_alter_table("klm_omnichannel_messages") as batch:
        batch.drop_constraint("fk_klm_omnichannel_message_workspace_binding", type_="foreignkey")
        batch.drop_constraint("fk_klm_omnichannel_message_workspace_endpoint", type_="foreignkey")
        for column in (
            "template_id",
            "window_expires_at",
            "cost_currency",
            "cost_amount",
            "transport",
            "provider_status",
            "language",
            "content_type",
            "audience",
            "binding_id",
            "endpoint_id",
        ):
            batch.drop_column(column)

    # Drop dependent event/runtime tables first. Bindings and endpoints are
    # handled explicitly below because they reference the altered thread table.
    for table_name in reversed(NEW_TABLES[5:]):
        op.drop_table(table_name)

    op.drop_table("klm_omnichannel_thread_bindings")
    op.drop_table("klm_omnichannel_endpoints")

    op.drop_index("uq_klm_thread_workspace_identity", table_name="klm_omnichannel_threads")
    op.drop_index("ix_klm_thread_workspace_queue_activity", table_name="klm_omnichannel_threads")
    for column in ("queue", "assigned_team_id", "identity_id"):
        op.drop_index(f"ix_klm_omnichannel_threads_{column}", table_name="klm_omnichannel_threads")
    with op.batch_alter_table("klm_omnichannel_threads") as batch:
        batch.drop_constraint("fk_klm_omnichannel_thread_workspace_team", type_="foreignkey")
        batch.drop_constraint("fk_klm_omnichannel_thread_workspace_identity", type_="foreignkey")
        for column in (
            "cost_state",
            "policy_state",
            "closed_reason",
            "closed_at",
            "resolution_due_at",
            "first_response_due_at",
            "last_outbound_at",
            "last_inbound_at",
            "version",
            "preferred_channel",
            "is_favorite",
            "priority",
            "queue",
            "assigned_team_id",
            "identity_id",
        ):
            batch.drop_column(column)

    op.drop_table("klm_workspace_team_members")
    op.drop_table("klm_workspace_teams")
    op.drop_table("klm_omnichannel_identities")

    # PostgreSQL enum values are intentionally retained. Removing enum values is
    # unsafe when older rows or replicas may still reference them.
