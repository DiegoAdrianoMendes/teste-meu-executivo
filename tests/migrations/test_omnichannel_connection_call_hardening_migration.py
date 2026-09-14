from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path

MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "fd5e7f9a1c64_harden_omnichannel_connections_and_calls.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("omnichannel_call_hardening", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_previous_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    workspaces = sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    threads = sa.Table(
        "klm_omnichannel_threads",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    sa.Table(
        "klm_omnichannel_channel_connections",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("connection_key", sa.String(120), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    sa.Table(
        "klm_omnichannel_endpoints",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("connection_key", sa.String(120), nullable=False),
        sa.Column("connection_id", sa.String(36), nullable=True),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    sa.Table(
        "klm_omnichannel_thread_bindings",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("endpoint_id", sa.String(36), nullable=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("connection_key", sa.String(120), nullable=False),
        sa.Column("connection_id", sa.String(36), nullable=True),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    sa.Table(
        "klm_omnichannel_channel_events",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("binding_id", sa.String(36), nullable=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("connection_key", sa.String(120), nullable=False),
    )
    sa.Table(
        "klm_lead_capture_receipts",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("connection_key", sa.String(120), nullable=False),
    )
    sa.Table(
        "klm_omnichannel_scheduled_messages",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("endpoint_id", sa.String(36), nullable=True),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("connection_key", sa.String(120), nullable=False),
    )
    sa.Table(
        "storage_objects",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
    )
    sa.Table(
        "klm_omnichannel_call_sessions",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("thread_id", sa.String(36), nullable=False),
        sa.Column("endpoint_id", sa.String(36), nullable=True),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("connection_key", sa.String(120), nullable=False),
        sa.Column("transcript_object_id", sa.String(36), nullable=True),
        sa.Column("recording_object_id", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(["thread_id"], [threads.c.id]),
    )
    metadata.create_all(connection)


def test_connection_and_call_hardening_round_trip(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "fd5e7f9a1c64"
    assert migration.down_revision == "fc4a6d8e0b53"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _create_previous_schema(connection)
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))

        workspace_a, workspace_b = str(uuid4()), str(uuid4())
        thread_a, thread_b = str(uuid4()), str(uuid4())
        connection_a, connection_b = str(uuid4()), str(uuid4())
        storage_a, storage_b = str(uuid4()), str(uuid4())
        endpoint_a, binding_a = str(uuid4()), str(uuid4())
        event_a, receipt_a, schedule_a, call_a = (str(uuid4()) for _ in range(4))
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:a), (:b)"),
            {"a": workspace_a, "b": workspace_b},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_threads (id, workspace_id) "
                "VALUES (:ta, :wa), (:tb, :wb)"
            ),
            {"ta": thread_a, "wa": workspace_a, "tb": thread_b, "wb": workspace_b},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_channel_connections "
                "(id, workspace_id, channel, provider, connection_key) VALUES "
                "(:ca, :wa, 'whatsapp', 'meta_cloud', 'principal'), "
                "(:cb, :wb, 'whatsapp', 'meta_cloud', 'principal')"
            ),
            {"ca": connection_a, "wa": workspace_a, "cb": connection_b, "wb": workspace_b},
        )
        connection.execute(
            sa.text(
                "INSERT INTO storage_objects (id, workspace_id) "
                "VALUES (:a, :wa), (:b, :wb)"
            ),
            {"a": storage_a, "wa": workspace_a, "b": storage_b, "wb": workspace_b},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_endpoints "
                "(id, workspace_id, provider, connection_key) "
                "VALUES (:id, :workspace, 'meta_cloud', 'principal')"
            ),
            {"id": endpoint_a, "workspace": workspace_a},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_thread_bindings "
                "(id, workspace_id, endpoint_id, provider, connection_key) "
                "VALUES (:id, :workspace, :endpoint, 'meta_cloud', 'principal')"
            ),
            {"id": binding_a, "workspace": workspace_a, "endpoint": endpoint_a},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_channel_events "
                "(id, workspace_id, binding_id, provider, connection_key) "
                "VALUES (:id, :workspace, :binding, 'meta_cloud', 'principal')"
            ),
            {"id": event_a, "workspace": workspace_a, "binding": binding_a},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_lead_capture_receipts "
                "(id, workspace_id, provider, connection_key) "
                "VALUES (:id, :workspace, 'meta_cloud', 'principal')"
            ),
            {"id": receipt_a, "workspace": workspace_a},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_scheduled_messages "
                "(id, workspace_id, endpoint_id, channel, connection_key) "
                "VALUES (:id, :workspace, :endpoint, 'whatsapp', 'principal')"
            ),
            {"id": schedule_a, "workspace": workspace_a, "endpoint": endpoint_a},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_call_sessions "
                "(id, workspace_id, thread_id, endpoint_id, channel, connection_key) "
                "VALUES (:id, :workspace, :thread, :endpoint, 'whatsapp', 'principal')"
            ),
            {
                "id": call_a,
                "workspace": workspace_a,
                "thread": thread_a,
                "endpoint": endpoint_a,
            },
        )

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert set(migration.EVIDENCE_TABLES) <= set(inspector.get_table_names())
        for table_name in (
            "klm_omnichannel_endpoints",
            "klm_omnichannel_thread_bindings",
            *migration.NEW_CONNECTION_COLUMNS,
        ):
            value = connection.execute(
                sa.text(f"SELECT connection_id FROM {table_name} WHERE workspace_id = :workspace"),
                {"workspace": workspace_a},
            ).scalar_one()
            assert value == connection_a

        with pytest.raises((IntegrityError, DBAPIError)):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "UPDATE klm_omnichannel_call_sessions SET connection_id = :foreign "
                        "WHERE id = :id"
                    ),
                    {"foreign": connection_b, "id": call_a},
                )
        connection.execute(
            sa.text(
                "UPDATE klm_omnichannel_call_sessions SET transcript_object_id = :storage "
                "WHERE id = :id"
            ),
            {"storage": storage_a, "id": call_a},
        )
        with pytest.raises((IntegrityError, DBAPIError)):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "UPDATE klm_omnichannel_call_sessions SET recording_object_id = :storage "
                        "WHERE id = :id"
                    ),
                    {"storage": storage_b, "id": call_a},
                )

        call_event_id = str(uuid4())
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_call_events "
                "(id, workspace_id, call_id, thread_id, connection_id, provider, "
                "idempotency_key, previous_state, next_state, payload_sha256, occurred_at, "
                "event_metadata) VALUES (:id, :workspace, :call, :thread, :connection, "
                "'meta_cloud', 'call-event-1', 'active', 'completed', :hash, CURRENT_TIMESTAMP, '{}')"
            ),
            {
                "id": call_event_id,
                "workspace": workspace_a,
                "call": call_a,
                "thread": thread_a,
                "connection": connection_a,
                "hash": "a" * 64,
            },
        )
        cost_id = str(uuid4())
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_call_cost_ledger "
                "(id, workspace_id, call_id, thread_id, call_event_id, provider, amount, "
                "currency, idempotency_key, occurred_at, entry_metadata) VALUES "
                "(:id, :workspace, :call, :thread, :event, 'meta_cloud', 1.25, 'BRL', "
                "'call-cost-1', CURRENT_TIMESTAMP, '{}')"
            ),
            {
                "id": cost_id,
                "workspace": workspace_a,
                "call": call_a,
                "thread": thread_a,
                "event": call_event_id,
            },
        )
        for table_name, record_id in (
            ("klm_omnichannel_call_events", call_event_id),
            ("klm_omnichannel_call_cost_ledger", cost_id),
        ):
            with pytest.raises((IntegrityError, DBAPIError)):
                with connection.begin_nested():
                    connection.execute(
                        sa.text(f"UPDATE {table_name} SET id = id WHERE id = :id"),
                        {"id": record_id},
                    )

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert not (set(migration.EVIDENCE_TABLES) & set(inspector.get_table_names()))
        for table_name in migration.NEW_CONNECTION_COLUMNS:
            assert "connection_id" not in {
                column["name"] for column in inspector.get_columns(table_name)
            }
