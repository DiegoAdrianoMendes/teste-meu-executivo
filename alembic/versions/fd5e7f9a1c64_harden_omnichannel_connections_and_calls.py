"""Harden tenant-bound connections and immutable call evidence.

Revision ID: fd5e7f9a1c64
Revises: fc4a6d8e0b53
Create Date: 2026-07-21 01:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "fd5e7f9a1c64"
down_revision: str | Sequence[str] | None = "fc4a6d8e0b53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
EVIDENCE_TABLES = (
    "klm_omnichannel_call_events",
    "klm_omnichannel_call_cost_ledger",
)
CONNECTION_CHILDREN = (
    ("klm_omnichannel_endpoints", "fk_klm_endpoint_workspace_connection"),
    ("klm_omnichannel_thread_bindings", "fk_klm_binding_workspace_connection"),
    ("klm_omnichannel_channel_events", "fk_klm_channel_event_workspace_connection"),
    ("klm_lead_capture_receipts", "fk_klm_lead_capture_workspace_connection"),
    ("klm_omnichannel_scheduled_messages", "fk_klm_schedule_workspace_connection"),
    ("klm_omnichannel_call_sessions", "fk_klm_call_workspace_connection"),
)
NEW_CONNECTION_COLUMNS = (
    "klm_omnichannel_channel_events",
    "klm_lead_capture_receipts",
    "klm_omnichannel_scheduled_messages",
    "klm_omnichannel_call_sessions",
)
CALL_STORAGE_REFERENCES = (
    ("transcript_object_id", "fk_klm_call_workspace_transcript_object"),
    ("recording_object_id", "fk_klm_call_workspace_recording_object"),
)


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    for table_name in EVIDENCE_TABLES:
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
    for table_name in EVIDENCE_TABLES:
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
        for table_name in EVIDENCE_TABLES:
            op.execute(
                f'''CREATE TRIGGER "trg_{table_name}_immutable"
                BEFORE UPDATE OR DELETE ON "{table_name}"
                FOR EACH ROW EXECUTE FUNCTION klm_reject_append_only_mutation()'''
            )
    elif dialect == "sqlite":
        for table_name in EVIDENCE_TABLES:
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
        for table_name in EVIDENCE_TABLES:
            op.execute(f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable" ON "{table_name}"')
    elif dialect == "sqlite":
        for table_name in EVIDENCE_TABLES:
            for operation in ("update", "delete"):
                op.execute(
                    f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable_{operation}"'
                )


def _install_connection_constraints() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table_name, constraint_name in CONNECTION_CHILDREN:
            op.create_foreign_key(
                constraint_name,
                table_name,
                "klm_omnichannel_channel_connections",
                ["workspace_id", "connection_id"],
                ["workspace_id", "id"],
                ondelete="RESTRICT",
            )
        return

    if op.get_bind().dialect.name == "sqlite":
        for table_name, _ in CONNECTION_CHILDREN:
            for operation in ("insert", "update"):
                changed_columns = (
                    " OF workspace_id, connection_id" if operation == "update" else ""
                )
                op.execute(
                    f'''
                    CREATE TRIGGER IF NOT EXISTS
                    "trg_{table_name}_tenant_connection_{operation}"
                    BEFORE {operation.upper()}{changed_columns} ON "{table_name}"
                    WHEN NEW.connection_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM klm_omnichannel_channel_connections AS connection
                         WHERE connection.id = NEW.connection_id
                           AND connection.workspace_id = NEW.workspace_id
                    )
                    BEGIN
                        SELECT RAISE(ABORT, '{table_name} requires a same-workspace connection');
                    END
                    '''
                )


def _remove_connection_constraints() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table_name, constraint_name in reversed(CONNECTION_CHILDREN):
            op.drop_constraint(constraint_name, table_name, type_="foreignkey")
    elif op.get_bind().dialect.name == "sqlite":
        for table_name, _ in CONNECTION_CHILDREN:
            for operation in ("insert", "update"):
                op.execute(
                    f'DROP TRIGGER IF EXISTS "trg_{table_name}_tenant_connection_{operation}"'
                )


def _install_call_storage_constraints() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for column_name, constraint_name in CALL_STORAGE_REFERENCES:
            op.create_foreign_key(
                constraint_name,
                "klm_omnichannel_call_sessions",
                "storage_objects",
                ["workspace_id", column_name],
                ["workspace_id", "id"],
                ondelete="RESTRICT",
            )
        return

    if op.get_bind().dialect.name == "sqlite":
        for column_name, _ in CALL_STORAGE_REFERENCES:
            for operation in ("insert", "update"):
                changed_columns = (
                    f" OF workspace_id, {column_name}" if operation == "update" else ""
                )
                op.execute(
                    f'''
                    CREATE TRIGGER IF NOT EXISTS
                    "trg_klm_call_{column_name}_tenant_{operation}"
                    BEFORE {operation.upper()}{changed_columns}
                    ON klm_omnichannel_call_sessions
                    WHEN NEW.{column_name} IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM storage_objects AS storage
                         WHERE storage.id = NEW.{column_name}
                           AND storage.workspace_id = NEW.workspace_id
                    )
                    BEGIN
                        SELECT RAISE(ABORT, 'call artifact must belong to the same workspace');
                    END
                    '''
                )


def _remove_call_storage_constraints() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for _, constraint_name in reversed(CALL_STORAGE_REFERENCES):
            op.drop_constraint(
                constraint_name,
                "klm_omnichannel_call_sessions",
                type_="foreignkey",
            )
    elif op.get_bind().dialect.name == "sqlite":
        for column_name, _ in CALL_STORAGE_REFERENCES:
            for operation in ("insert", "update"):
                op.execute(
                    f'DROP TRIGGER IF EXISTS "trg_klm_call_{column_name}_tenant_{operation}"'
                )


def _backfill_connections() -> None:
    op.execute(
        """
        UPDATE klm_omnichannel_endpoints AS endpoint
           SET connection_id = (
               SELECT connection.id
                 FROM klm_omnichannel_channel_connections AS connection
                WHERE connection.workspace_id = endpoint.workspace_id
                  AND connection.provider = endpoint.provider
                  AND connection.connection_key = endpoint.connection_key
                LIMIT 1
           )
         WHERE endpoint.connection_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE klm_omnichannel_thread_bindings AS binding
           SET connection_id = COALESCE(
               (SELECT endpoint.connection_id
                  FROM klm_omnichannel_endpoints AS endpoint
                 WHERE endpoint.workspace_id = binding.workspace_id
                   AND endpoint.id = binding.endpoint_id),
               (SELECT connection.id
                  FROM klm_omnichannel_channel_connections AS connection
                 WHERE connection.workspace_id = binding.workspace_id
                   AND connection.provider = binding.provider
                   AND connection.connection_key = binding.connection_key
                 LIMIT 1)
           )
         WHERE binding.connection_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE klm_omnichannel_channel_events AS event
           SET connection_id = COALESCE(
               (SELECT binding.connection_id
                  FROM klm_omnichannel_thread_bindings AS binding
                 WHERE binding.workspace_id = event.workspace_id
                   AND binding.id = event.binding_id),
               (SELECT connection.id
                  FROM klm_omnichannel_channel_connections AS connection
                 WHERE connection.workspace_id = event.workspace_id
                   AND connection.provider = event.provider
                   AND connection.connection_key = event.connection_key
                 LIMIT 1)
           )
         WHERE event.connection_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE klm_lead_capture_receipts AS receipt
           SET connection_id = (
               SELECT connection.id
                 FROM klm_omnichannel_channel_connections AS connection
                WHERE connection.workspace_id = receipt.workspace_id
                  AND connection.provider = receipt.provider
                  AND connection.connection_key = receipt.connection_key
                LIMIT 1
           )
         WHERE receipt.connection_id IS NULL
        """
    )
    for table_name in (
        "klm_omnichannel_scheduled_messages",
        "klm_omnichannel_call_sessions",
    ):
        op.execute(
            f"""
            UPDATE {table_name} AS child
               SET connection_id = COALESCE(
                   (SELECT endpoint.connection_id
                      FROM klm_omnichannel_endpoints AS endpoint
                     WHERE endpoint.workspace_id = child.workspace_id
                       AND endpoint.id = child.endpoint_id),
                   (SELECT connection.id
                      FROM klm_omnichannel_channel_connections AS connection
                     WHERE connection.workspace_id = child.workspace_id
                       AND connection.channel = child.channel
                       AND connection.connection_key = child.connection_key
                       AND NOT EXISTS (
                           SELECT 1
                             FROM klm_omnichannel_channel_connections AS sibling
                            WHERE sibling.workspace_id = connection.workspace_id
                              AND sibling.channel = connection.channel
                              AND sibling.connection_key = connection.connection_key
                              AND sibling.id <> connection.id
                       )
                     LIMIT 1)
               )
             WHERE child.connection_id IS NULL
            """
        )


def upgrade() -> None:
    for table_name in NEW_CONNECTION_COLUMNS:
        op.add_column(table_name, sa.Column("connection_id", FlexibleUUID(), nullable=True))
        op.create_index(
            f"ix_{table_name}_connection_id",
            table_name,
            ["connection_id"],
        )

    _backfill_connections()
    op.create_index(
        "uq_klm_call_workspace_id",
        "klm_omnichannel_call_sessions",
        ["workspace_id", "id"],
        unique=True,
    )
    op.create_index(
        "uq_storage_object_workspace_id",
        "storage_objects",
        ["workspace_id", "id"],
        unique=True,
    )
    _install_connection_constraints()
    _install_call_storage_constraints()

    op.create_table(
        "klm_omnichannel_call_events",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("call_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=True),
        sa.Column("previous_state", sa.String(length=32), nullable=False),
        sa.Column("next_state", sa.String(length=32), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("provider_call_id", sa.String(length=255), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
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
            ["workspace_id", "call_id"],
            ["klm_omnichannel_call_sessions.workspace_id", "klm_omnichannel_call_sessions.id"],
            name="fk_klm_call_event_workspace_call",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_call_event_workspace_thread",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_call_event_workspace_connection",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_call_event_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_klm_call_event_idempotency"
        ),
    )
    for column in (
        "workspace_id",
        "call_id",
        "thread_id",
        "connection_id",
        "provider",
        "external_event_id",
        "occurred_at",
        "received_at",
    ):
        op.create_index(
            f"ix_klm_omnichannel_call_events_{column}",
            "klm_omnichannel_call_events",
            [column],
        )
    op.create_index(
        "ix_klm_call_event_call_occurred",
        "klm_omnichannel_call_events",
        ["workspace_id", "call_id", "occurred_at"],
    )

    op.create_table(
        "klm_omnichannel_call_cost_ledger",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("call_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("call_event_id", FlexibleUUID(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("amount", sa.Numeric(15, 6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("quantity", sa.Numeric(15, 6), server_default="1", nullable=False),
        sa.Column("unit", sa.String(length=40), server_default="call", nullable=False),
        sa.Column("pricing_category", sa.String(length=120), nullable=True),
        sa.Column("provider_invoice_reference", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("entry_metadata", FlexibleJSON(), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_klm_call_cost_amount_nonnegative"),
        sa.CheckConstraint("quantity > 0", name="ck_klm_call_cost_quantity_positive"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "call_id"],
            ["klm_omnichannel_call_sessions.workspace_id", "klm_omnichannel_call_sessions.id"],
            name="fk_klm_call_cost_workspace_call",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_call_cost_workspace_thread",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "call_event_id"],
            ["klm_omnichannel_call_events.workspace_id", "klm_omnichannel_call_events.id"],
            name="fk_klm_call_cost_workspace_event",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_klm_call_cost_idempotency"
        ),
    )
    for column in (
        "workspace_id",
        "call_id",
        "thread_id",
        "call_event_id",
        "provider",
        "occurred_at",
        "recorded_at",
    ):
        op.create_index(
            f"ix_klm_omnichannel_call_cost_ledger_{column}",
            "klm_omnichannel_call_cost_ledger",
            [column],
        )
    op.create_index(
        "ix_klm_call_cost_call_occurred",
        "klm_omnichannel_call_cost_ledger",
        ["workspace_id", "call_id", "occurred_at"],
    )
    _enable_rls()
    _enable_append_only_guards()


def downgrade() -> None:
    _disable_append_only_guards()
    _disable_rls()

    op.drop_index(
        "ix_klm_call_cost_call_occurred",
        table_name="klm_omnichannel_call_cost_ledger",
    )
    for column in reversed(
        (
            "workspace_id",
            "call_id",
            "thread_id",
            "call_event_id",
            "provider",
            "occurred_at",
            "recorded_at",
        )
    ):
        op.drop_index(
            f"ix_klm_omnichannel_call_cost_ledger_{column}",
            table_name="klm_omnichannel_call_cost_ledger",
        )
    op.drop_table("klm_omnichannel_call_cost_ledger")

    op.drop_index(
        "ix_klm_call_event_call_occurred",
        table_name="klm_omnichannel_call_events",
    )
    for column in reversed(
        (
            "workspace_id",
            "call_id",
            "thread_id",
            "connection_id",
            "provider",
            "external_event_id",
            "occurred_at",
            "received_at",
        )
    ):
        op.drop_index(
            f"ix_klm_omnichannel_call_events_{column}",
            table_name="klm_omnichannel_call_events",
        )
    op.drop_table("klm_omnichannel_call_events")

    _remove_call_storage_constraints()
    _remove_connection_constraints()
    op.drop_index("uq_storage_object_workspace_id", table_name="storage_objects")
    op.drop_index("uq_klm_call_workspace_id", table_name="klm_omnichannel_call_sessions")
    for table_name in reversed(NEW_CONNECTION_COLUMNS):
        op.drop_index(f"ix_{table_name}_connection_id", table_name=table_name)
        op.drop_column(table_name, "connection_id")
