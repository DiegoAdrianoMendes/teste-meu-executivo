"""Add immutable delivery receipts and provider cost ledger.

Revision ID: fb3f5d7b9c42
Revises: fa2e4c6a8b31
Create Date: 2026-07-20 23:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "fb3f5d7b9c42"
down_revision: str | Sequence[str] | None = "fa2e4c6a8b31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLES = (
    "klm_omnichannel_message_delivery_events",
    "klm_omnichannel_message_cost_ledger",
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
        for table_name in TABLES:
            op.execute(
                f'''CREATE TRIGGER "trg_{table_name}_immutable"
                BEFORE UPDATE OR DELETE ON "{table_name}"
                FOR EACH ROW EXECUTE FUNCTION klm_reject_append_only_mutation()'''
            )
    elif dialect == "sqlite":
        for table_name in TABLES:
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
    if dialect == "postgresql":
        for table_name in TABLES:
            op.execute(f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable" ON "{table_name}"')
    elif dialect == "sqlite":
        for table_name in TABLES:
            for operation in ("update", "delete"):
                op.execute(
                    f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable_{operation}"'
                )


def upgrade() -> None:
    op.add_column(
        "klm_omnichannel_messages",
        sa.Column("connection_id", FlexibleUUID(), nullable=True),
    )
    op.add_column(
        "klm_omnichannel_messages",
        sa.Column("provider", sa.String(length=40), nullable=True),
    )
    op.create_index(
        "ix_klm_omnichannel_messages_connection_id",
        "klm_omnichannel_messages",
        ["connection_id"],
    )
    op.create_index(
        "ix_klm_omnichannel_messages_provider",
        "klm_omnichannel_messages",
        ["provider"],
    )
    op.create_index(
        "uq_klm_message_workspace_id",
        "klm_omnichannel_messages",
        ["workspace_id", "id"],
        unique=True,
    )

    op.execute(
        sa.text(
            """
            UPDATE klm_omnichannel_messages
               SET connection_id = COALESCE(
                   (SELECT binding.connection_id
                      FROM klm_omnichannel_thread_bindings AS binding
                     WHERE binding.workspace_id = klm_omnichannel_messages.workspace_id
                       AND binding.id = klm_omnichannel_messages.binding_id),
                   (SELECT endpoint.connection_id
                      FROM klm_omnichannel_endpoints AS endpoint
                     WHERE endpoint.workspace_id = klm_omnichannel_messages.workspace_id
                       AND endpoint.id = klm_omnichannel_messages.endpoint_id)
               )
             WHERE connection_id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE klm_omnichannel_messages
               SET provider = (
                   SELECT connection.provider
                     FROM klm_omnichannel_channel_connections AS connection
                    WHERE connection.workspace_id = klm_omnichannel_messages.workspace_id
                      AND connection.id = klm_omnichannel_messages.connection_id
               )
             WHERE provider IS NULL
               AND connection_id IS NOT NULL
            """
        )
    )
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_klm_omnichannel_message_workspace_connection",
            "klm_omnichannel_messages",
            "klm_omnichannel_channel_connections",
            ["workspace_id", "connection_id"],
            ["workspace_id", "id"],
            ondelete="RESTRICT",
        )

    op.create_table(
        "klm_omnichannel_message_delivery_events",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("message_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("connection_key", sa.String(length=120), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("external_message_id", sa.String(length=255), nullable=False),
        sa.Column("provider_status", sa.String(length=80), nullable=False),
        sa.Column("projected_status_before", sa.String(length=32), nullable=False),
        sa.Column("projected_status_after", sa.String(length=32), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ignored_reason", sa.String(length=120), nullable=True),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("event_payload", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("event_metadata", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_klm_delivery_event_workspace_message",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_delivery_event_workspace_thread",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_delivery_event_workspace_connection",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_klm_delivery_event_workspace_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            "external_event_id",
            name="uq_klm_delivery_event_provider_event",
        ),
    )
    for column in (
        "workspace_id",
        "message_id",
        "thread_id",
        "connection_id",
        "provider",
        "external_message_id",
        "occurred_at",
        "received_at",
    ):
        op.create_index(
            f"ix_klm_omnichannel_message_delivery_events_{column}",
            "klm_omnichannel_message_delivery_events",
            [column],
        )
    op.create_index(
        "ix_klm_delivery_event_message_occurred",
        "klm_omnichannel_message_delivery_events",
        ["workspace_id", "message_id", "occurred_at"],
    )

    op.create_table(
        "klm_omnichannel_message_cost_ledger",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("message_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("delivery_event_id", FlexibleUUID(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("connection_key", sa.String(length=120), nullable=False),
        sa.Column(
            "entry_type", sa.String(length=40), nullable=False, server_default="provider_charge"
        ),
        sa.Column("amount", sa.Numeric(15, 6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("quantity", sa.Numeric(15, 6), nullable=False, server_default="1"),
        sa.Column("unit", sa.String(length=40), nullable=False, server_default="message"),
        sa.Column("pricing_category", sa.String(length=120), nullable=True),
        sa.Column("provider_invoice_reference", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("entry_metadata", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_klm_message_cost_amount_nonnegative"),
        sa.CheckConstraint("quantity > 0", name="ck_klm_message_cost_quantity_positive"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_klm_message_cost_workspace_message",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_message_cost_workspace_thread",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "delivery_event_id"],
            [
                "klm_omnichannel_message_delivery_events.workspace_id",
                "klm_omnichannel_message_delivery_events.id",
            ],
            name="fk_klm_message_cost_workspace_delivery_event",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_klm_message_cost_idempotency"
        ),
    )
    for column in (
        "workspace_id",
        "message_id",
        "thread_id",
        "delivery_event_id",
        "provider",
        "occurred_at",
        "recorded_at",
    ):
        op.create_index(
            f"ix_klm_omnichannel_message_cost_ledger_{column}",
            "klm_omnichannel_message_cost_ledger",
            [column],
        )
    op.create_index(
        "ix_klm_message_cost_thread_occurred",
        "klm_omnichannel_message_cost_ledger",
        ["workspace_id", "thread_id", "occurred_at"],
    )
    _enable_rls()
    _enable_append_only_guards()


def downgrade() -> None:
    _disable_append_only_guards()
    _disable_rls()

    op.drop_index(
        "ix_klm_message_cost_thread_occurred",
        table_name="klm_omnichannel_message_cost_ledger",
    )
    for column in reversed(
        (
            "workspace_id",
            "message_id",
            "thread_id",
            "delivery_event_id",
            "provider",
            "occurred_at",
            "recorded_at",
        )
    ):
        op.drop_index(
            f"ix_klm_omnichannel_message_cost_ledger_{column}",
            table_name="klm_omnichannel_message_cost_ledger",
        )
    op.drop_table("klm_omnichannel_message_cost_ledger")

    op.drop_index(
        "ix_klm_delivery_event_message_occurred",
        table_name="klm_omnichannel_message_delivery_events",
    )
    for column in reversed(
        (
            "workspace_id",
            "message_id",
            "thread_id",
            "connection_id",
            "provider",
            "external_message_id",
            "occurred_at",
            "received_at",
        )
    ):
        op.drop_index(
            f"ix_klm_omnichannel_message_delivery_events_{column}",
            table_name="klm_omnichannel_message_delivery_events",
        )
    op.drop_table("klm_omnichannel_message_delivery_events")

    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "fk_klm_omnichannel_message_workspace_connection",
            "klm_omnichannel_messages",
            type_="foreignkey",
        )
    op.drop_index("uq_klm_message_workspace_id", table_name="klm_omnichannel_messages")
    op.drop_index(
        "ix_klm_omnichannel_messages_provider", table_name="klm_omnichannel_messages"
    )
    op.drop_index(
        "ix_klm_omnichannel_messages_connection_id", table_name="klm_omnichannel_messages"
    )
    op.drop_column("klm_omnichannel_messages", "provider")
    op.drop_column("klm_omnichannel_messages", "connection_id")
