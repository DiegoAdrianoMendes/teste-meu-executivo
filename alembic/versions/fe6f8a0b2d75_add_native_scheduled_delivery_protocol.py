"""Add durable native scheduled-message delivery protocol.

Revision ID: fe6f8a0b2d75
Revises: fd5e7f9a1c64
Create Date: 2026-07-21 02:25:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "fe6f8a0b2d75"
down_revision: str | Sequence[str] | None = "fd5e7f9a1c64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEDULE_TABLE = "klm_omnichannel_scheduled_messages"
EVENT_TABLE = "klm_omnichannel_scheduled_message_events"
WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
EVENT_INDEXES = {
    "workspace_id": "ix_klm_sched_evt_workspace",
    "scheduled_message_id": "ix_klm_sched_evt_schedule",
    "thread_id": "ix_klm_sched_evt_thread",
    "connection_id": "ix_klm_sched_evt_connection",
    "canonical_message_id": "ix_klm_sched_evt_message",
    "provider": "ix_klm_sched_evt_provider",
    "event_type": "ix_klm_sched_evt_type",
    "occurred_at": "ix_klm_sched_evt_occurred",
    "received_at": "ix_klm_sched_evt_received",
}


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


def _install_schedule_references() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.create_foreign_key(
            "fk_klm_schedule_created_by_user",
            SCHEDULE_TABLE,
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_foreign_key(
            "fk_klm_schedule_workspace_canonical_message",
            SCHEDULE_TABLE,
            "klm_omnichannel_messages",
            ["workspace_id", "canonical_message_id"],
            ["workspace_id", "id"],
            ondelete="RESTRICT",
        )
    elif dialect == "sqlite":
        for operation in ("insert", "update"):
            changed = " OF workspace_id, canonical_message_id" if operation == "update" else ""
            op.execute(
                f'''
                CREATE TRIGGER IF NOT EXISTS
                "trg_klm_schedule_tenant_message_{operation}"
                BEFORE {operation.upper()}{changed} ON "{SCHEDULE_TABLE}"
                WHEN NEW.canonical_message_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM klm_omnichannel_messages AS message
                     WHERE message.id = NEW.canonical_message_id
                       AND message.workspace_id = NEW.workspace_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'scheduled message requires a same-workspace canonical message');
                END
                '''
            )


def _remove_schedule_references() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.drop_constraint(
            "fk_klm_schedule_workspace_canonical_message",
            SCHEDULE_TABLE,
            type_="foreignkey",
        )
        op.drop_constraint(
            "fk_klm_schedule_created_by_user",
            SCHEDULE_TABLE,
            type_="foreignkey",
        )
    elif dialect == "sqlite":
        for operation in ("insert", "update"):
            op.execute(f'DROP TRIGGER IF EXISTS "trg_klm_schedule_tenant_message_{operation}"')


def upgrade() -> None:
    op.create_index(
        "uq_klm_schedule_workspace_id",
        SCHEDULE_TABLE,
        ["workspace_id", "id"],
        unique=True,
    )
    columns = (
        sa.Column("canonical_message_id", FlexibleUUID(), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("author_type", sa.String(length=24), server_default="system", nullable=False),
        sa.Column(
            "author_label",
            sa.String(length=180),
            server_default="Agendamento KLM",
            nullable=False,
        ),
        sa.Column("lease_generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("delivery_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "provider_supports_idempotency",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("delivery_uncertain_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
    )
    for column in columns:
        op.add_column(SCHEDULE_TABLE, column)
    for column in ("canonical_message_id", "created_by_user_id"):
        op.create_index(
            f"ix_{SCHEDULE_TABLE}_{column}",
            SCHEDULE_TABLE,
            [column],
        )
    op.create_index(
        "ix_klm_schedule_workspace_due",
        SCHEDULE_TABLE,
        ["workspace_id", "state", "scheduled_at", "next_attempt_at"],
    )
    _install_schedule_references()

    op.create_table(
        EVENT_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("scheduled_message_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=True),
        sa.Column("canonical_message_id", FlexibleUUID(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("previous_state", sa.String(length=32), nullable=False),
        sa.Column("next_state", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("lease_owner", sa.String(length=180), nullable=True),
        sa.Column("lease_generation", sa.Integer(), nullable=True),
        sa.Column("external_event_id", sa.String(length=255), nullable=True),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("event_metadata", FlexibleJSON(), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "scheduled_message_id"],
            [f"{SCHEDULE_TABLE}.workspace_id", f"{SCHEDULE_TABLE}.id"],
            name="fk_klm_schedule_event_workspace_schedule",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_schedule_event_workspace_thread",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_schedule_event_workspace_connection",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "canonical_message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_klm_schedule_event_workspace_canonical_message",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_schedule_event_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_klm_schedule_event_idempotency",
        ),
    )
    for column, index_name in EVENT_INDEXES.items():
        op.create_index(index_name, EVENT_TABLE, [column])
    op.create_index(
        "ix_klm_schedule_event_schedule_occurred",
        EVENT_TABLE,
        ["workspace_id", "scheduled_message_id", "occurred_at"],
    )
    _enable_rls()
    _enable_append_only_guard()


def downgrade() -> None:
    _disable_append_only_guard()
    _disable_rls()
    op.drop_index("ix_klm_schedule_event_schedule_occurred", table_name=EVENT_TABLE)
    for index_name in reversed(tuple(EVENT_INDEXES.values())):
        op.drop_index(index_name, table_name=EVENT_TABLE)
    op.drop_table(EVENT_TABLE)

    _remove_schedule_references()
    op.drop_index("ix_klm_schedule_workspace_due", table_name=SCHEDULE_TABLE)
    for column in reversed(("canonical_message_id", "created_by_user_id")):
        op.drop_index(f"ix_{SCHEDULE_TABLE}_{column}", table_name=SCHEDULE_TABLE)
    for column in reversed(
        (
            "canonical_message_id",
            "created_by_user_id",
            "author_type",
            "author_label",
            "lease_generation",
            "delivery_attempted_at",
            "provider_supports_idempotency",
            "provider_message_id",
            "delivery_uncertain_at",
            "completed_at",
            "last_error_code",
            "last_error_message",
        )
    ):
        op.drop_column(SCHEDULE_TABLE, column)
    op.drop_index("uq_klm_schedule_workspace_id", table_name=SCHEDULE_TABLE)
