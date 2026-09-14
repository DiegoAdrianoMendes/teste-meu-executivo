"""Add durable social-action delivery protocol.

Revision ID: 0a8b0d2e4f97
Revises: ff7a9c1d3e86
Create Date: 2026-07-21 04:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "0a8b0d2e4f97"
down_revision: str | Sequence[str] | None = "ff7a9c1d3e86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INTENT_TABLE = "klm_omnichannel_action_intents"
EVENT_TABLE = "klm_omnichannel_action_intent_events"
WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    op.execute(f'ALTER TABLE "{EVENT_TABLE}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{EVENT_TABLE}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY "{RLS_POLICY}" ON "{EVENT_TABLE}"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({predicate}) WITH CHECK ({predicate})'''
    )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{EVENT_TABLE}"')
    op.execute(f'ALTER TABLE "{EVENT_TABLE}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{EVENT_TABLE}" DISABLE ROW LEVEL SECURITY')


def _enable_append_only_guard() -> None:
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
        op.execute(
            f'''CREATE TRIGGER "trg_{EVENT_TABLE}_immutable"
            BEFORE UPDATE OR DELETE ON "{EVENT_TABLE}"
            FOR EACH ROW EXECUTE FUNCTION klm_reject_append_only_mutation()'''
        )
    elif dialect == "sqlite":
        for operation in ("update", "delete"):
            op.execute(
                f'''
                CREATE TRIGGER IF NOT EXISTS "trg_{EVENT_TABLE}_immutable_{operation}"
                BEFORE {operation.upper()} ON "{EVENT_TABLE}"
                BEGIN
                    SELECT RAISE(ABORT, '{EVENT_TABLE} is immutable');
                END
                '''
            )


def _disable_append_only_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(f'DROP TRIGGER IF EXISTS "trg_{EVENT_TABLE}_immutable" ON "{EVENT_TABLE}"')
    elif dialect == "sqlite":
        for operation in ("update", "delete"):
            op.execute(f'DROP TRIGGER IF EXISTS "trg_{EVENT_TABLE}_immutable_{operation}"')


def upgrade() -> None:
    op.create_index(
        "uq_klm_action_intent_workspace_id",
        INTENT_TABLE,
        ["workspace_id", "id"],
        unique=True,
    )
    columns = (
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=180), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("delivery_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "provider_supports_idempotency",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("provider_action_id", sa.String(length=255), nullable=True),
        sa.Column("delivery_uncertain_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
    )
    for column in columns:
        op.add_column(INTENT_TABLE, column)
    op.create_index(
        "ix_klm_action_intent_workspace_delivery_due",
        INTENT_TABLE,
        ["workspace_id", "state", "scheduled_at", "next_attempt_at"],
    )

    op.create_table(
        EVENT_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("action_intent_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("action_type", sa.String(length=60), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("previous_state", sa.String(length=32), nullable=False),
        sa.Column("next_state", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("lease_owner", sa.String(length=180), nullable=True),
        sa.Column("lease_generation", sa.Integer(), nullable=True),
        sa.Column("external_action_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "event_metadata",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "action_intent_id"],
            [f"{INTENT_TABLE}.workspace_id", f"{INTENT_TABLE}.id"],
            name="fk_klm_action_event_workspace_intent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_action_event_workspace_thread",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_action_event_workspace_connection",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_klm_action_intent_event_workspace_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_klm_action_intent_event_idempotency",
        ),
    )
    for column in (
        "workspace_id",
        "action_intent_id",
        "thread_id",
        "connection_id",
        "provider",
        "action_type",
        "event_type",
        "occurred_at",
    ):
        op.create_index(f"ix_{EVENT_TABLE}_{column}", EVENT_TABLE, [column])
    op.create_index(
        "ix_klm_action_intent_event_intent_time",
        EVENT_TABLE,
        ["workspace_id", "action_intent_id", "occurred_at"],
    )
    _enable_rls()
    _enable_append_only_guard()


def downgrade() -> None:
    _disable_append_only_guard()
    _disable_rls()
    op.drop_index("ix_klm_action_intent_event_intent_time", table_name=EVENT_TABLE)
    for column in reversed(
        (
            "workspace_id",
            "action_intent_id",
            "thread_id",
            "connection_id",
            "provider",
            "action_type",
            "event_type",
            "occurred_at",
        )
    ):
        op.drop_index(f"ix_{EVENT_TABLE}_{column}", table_name=EVENT_TABLE)
    op.drop_table(EVENT_TABLE)
    op.drop_index("ix_klm_action_intent_workspace_delivery_due", table_name=INTENT_TABLE)
    for column in reversed(
        (
            "attempt_count",
            "max_attempts",
            "next_attempt_at",
            "lease_owner",
            "lease_expires_at",
            "lease_generation",
            "delivery_attempted_at",
            "provider_supports_idempotency",
            "provider_action_id",
            "delivery_uncertain_at",
            "completed_at",
            "last_error_code",
            "last_error_message",
        )
    ):
        op.drop_column(INTENT_TABLE, column)
    op.drop_index("uq_klm_action_intent_workspace_id", table_name=INTENT_TABLE)
