"""Add omnichannel connections, social actions and automation traces.

Revision ID: f9d1e3a5c720
Revises: f8c0e2a4b619
Create Date: 2026-07-20 20:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "f9d1e3a5c720"
down_revision: str | Sequence[str] | None = "f8c0e2a4b619"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLES = (
    "klm_omnichannel_channel_connections",
    "klm_omnichannel_action_intents",
    "klm_omnichannel_automation_rules",
    "klm_omnichannel_automation_runs",
    "klm_omnichannel_automation_run_events",
)


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


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    for table_name in TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in TABLES:
        op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        "klm_omnichannel_channel_connections",
        _workspace(),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("connection_key", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=180), nullable=False),
        sa.Column("external_account_id", sa.String(length=180), nullable=True),
        sa.Column(
            "transport_mode", sa.String(length=40), nullable=False, server_default="official_api"
        ),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="configured_local"
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("capabilities", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("limits", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("routing_policy", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("cost_policy", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("health", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("configuration", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("credential_reference", sa.String(length=255), nullable=True),
        sa.Column(
            "webhook_state", sa.String(length=32), nullable=False, server_default="not_configured"
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_channel_connection_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            name="uq_klm_channel_connection_provider_key",
        ),
    )
    for column in ("workspace_id", "channel", "provider"):
        op.create_index(
            f"ix_klm_omnichannel_channel_connections_{column}",
            "klm_omnichannel_channel_connections",
            [column],
        )
    op.create_index(
        "ix_klm_channel_connection_workspace_channel_status",
        "klm_omnichannel_channel_connections",
        ["workspace_id", "channel", "status"],
    )

    op.create_table(
        "klm_omnichannel_action_intents",
        _workspace(),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("event_id", FlexibleUUID(), nullable=True),
        sa.Column("connection_id", FlexibleUUID(), nullable=True),
        sa.Column("requested_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("action_type", sa.String(length=60), nullable=False),
        sa.Column("target_external_id", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="local_preview"),
        sa.Column("required_capability", sa.String(length=80), nullable=False),
        sa.Column(
            "capability_state", sa.String(length=32), nullable=False, server_default="unknown"
        ),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action_payload", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "provider_receipt", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_action_intent_workspace_thread",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "event_id"],
            ["klm_omnichannel_channel_events.workspace_id", "klm_omnichannel_channel_events.id"],
            name="fk_klm_action_intent_workspace_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_action_intent_workspace_connection",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_klm_action_intent_key"),
    )
    for column in (
        "workspace_id",
        "thread_id",
        "event_id",
        "connection_id",
        "requested_by_user_id",
        "action_type",
        "state",
    ):
        op.create_index(
            f"ix_klm_omnichannel_action_intents_{column}",
            "klm_omnichannel_action_intents",
            [column],
        )
    op.create_index(
        "ix_klm_action_intent_workspace_state_due",
        "klm_omnichannel_action_intents",
        ["workspace_id", "state", "scheduled_at"],
    )

    op.create_table(
        "klm_omnichannel_automation_rules",
        _workspace(),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("channel", sa.String(length=40), nullable=True),
        sa.Column("trigger_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="draft"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("coexist_with_ai", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("stop_on_match", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trigger_config", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("conditions", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("actions", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("quiet_hours", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("rate_limits", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _created_at(),
        _updated_at(),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_automation_rule_workspace_id"),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_klm_automation_rule_slug"),
    )
    for column in ("workspace_id", "created_by_user_id", "channel", "trigger_type", "status"):
        op.create_index(
            f"ix_klm_omnichannel_automation_rules_{column}",
            "klm_omnichannel_automation_rules",
            [column],
        )
    op.create_index(
        "ix_klm_automation_rule_workspace_trigger_status",
        "klm_omnichannel_automation_rules",
        ["workspace_id", "trigger_type", "status"],
    )

    op.create_table(
        "klm_omnichannel_automation_runs",
        _workspace(),
        sa.Column("rule_id", FlexibleUUID(), nullable=False),
        sa.Column("event_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="simulated"),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("current_action", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("context", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("result", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "rule_id"],
            [
                "klm_omnichannel_automation_rules.workspace_id",
                "klm_omnichannel_automation_rules.id",
            ],
            name="fk_klm_automation_run_workspace_rule",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "event_id"],
            ["klm_omnichannel_channel_events.workspace_id", "klm_omnichannel_channel_events.id"],
            name="fk_klm_automation_run_workspace_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_automation_run_workspace_thread",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_klm_automation_run_key"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_automation_run_workspace_id"),
    )
    for column in ("workspace_id", "rule_id", "event_id", "thread_id", "state"):
        op.create_index(
            f"ix_klm_omnichannel_automation_runs_{column}",
            "klm_omnichannel_automation_runs",
            [column],
        )

    op.create_table(
        "klm_omnichannel_automation_run_events",
        _workspace(),
        sa.Column("run_id", FlexibleUUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("action_key", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_snapshot", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "output_snapshot", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _id(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["klm_omnichannel_automation_runs.workspace_id", "klm_omnichannel_automation_runs.id"],
            name="fk_klm_automation_run_event_workspace_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "run_id", "sequence", name="uq_klm_automation_run_event_sequence"
        ),
    )
    op.create_index(
        "ix_klm_omnichannel_automation_run_events_workspace_id",
        "klm_omnichannel_automation_run_events",
        ["workspace_id"],
    )
    op.create_index(
        "ix_klm_omnichannel_automation_run_events_run_id",
        "klm_omnichannel_automation_run_events",
        ["run_id"],
    )
    op.create_index(
        "ix_klm_omnichannel_automation_run_events_occurred_at",
        "klm_omnichannel_automation_run_events",
        ["occurred_at"],
    )
    op.create_index(
        "ix_klm_automation_run_event_run_time",
        "klm_omnichannel_automation_run_events",
        ["run_id", "occurred_at"],
    )

    _enable_rls()


def downgrade() -> None:
    _disable_rls()
    for table_name in reversed(TABLES):
        op.drop_table(table_name)
