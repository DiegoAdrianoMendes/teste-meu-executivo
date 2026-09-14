from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, OperationalError

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
    / "fb3f5d7b9c42_add_delivery_receipts_and_cost_ledger.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("delivery_receipt_migration", MIGRATION_PATH)
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
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    connections = sa.Table(
        "klm_omnichannel_channel_connections",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    endpoints = sa.Table(
        "klm_omnichannel_endpoints",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("connection_id", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [connections.c.workspace_id, connections.c.id],
        ),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    bindings = sa.Table(
        "klm_omnichannel_thread_bindings",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("connection_id", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [connections.c.workspace_id, connections.c.id],
        ),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    sa.Table(
        "klm_omnichannel_messages",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("thread_id", sa.String(36), nullable=False),
        sa.Column("endpoint_id", sa.String(36), nullable=True),
        sa.Column("binding_id", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            [threads.c.workspace_id, threads.c.id],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "endpoint_id"],
            [endpoints.c.workspace_id, endpoints.c.id],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "binding_id"],
            [bindings.c.workspace_id, bindings.c.id],
        ),
    )
    metadata.create_all(connection)


def test_delivery_receipt_migration_is_tenant_safe_and_append_only(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "fb3f5d7b9c42"
    assert migration.down_revision == "fa2e4c6a8b31"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _create_previous_schema(connection)
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))

        workspace_a, workspace_b = str(uuid4()), str(uuid4())
        thread_a, thread_b = str(uuid4()), str(uuid4())
        connection_a, connection_b = str(uuid4()), str(uuid4())
        endpoint_a, binding_a, message_a = (str(uuid4()) for _ in range(3))
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:a), (:b)"),
            {"a": workspace_a, "b": workspace_b},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_threads (id, workspace_id) "
                "VALUES (:a, :wa), (:b, :wb)"
            ),
            {"a": thread_a, "wa": workspace_a, "b": thread_b, "wb": workspace_b},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_channel_connections "
                "(id, workspace_id, provider) VALUES "
                "(:a, :wa, 'meta_cloud'), (:b, :wb, 'meta_cloud')"
            ),
            {"a": connection_a, "wa": workspace_a, "b": connection_b, "wb": workspace_b},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_endpoints (id, workspace_id, connection_id) "
                "VALUES (:id, :workspace, :connection)"
            ),
            {"id": endpoint_a, "workspace": workspace_a, "connection": connection_a},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_thread_bindings (id, workspace_id, connection_id) "
                "VALUES (:id, :workspace, :connection)"
            ),
            {"id": binding_a, "workspace": workspace_a, "connection": connection_a},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_messages "
                "(id, workspace_id, thread_id, endpoint_id, binding_id) "
                "VALUES (:id, :workspace, :thread, :endpoint, :binding)"
            ),
            {
                "id": message_a,
                "workspace": workspace_a,
                "thread": thread_a,
                "endpoint": endpoint_a,
                "binding": binding_a,
            },
        )

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert set(migration.TABLES) <= set(inspector.get_table_names())
        message_columns = {
            item["name"] for item in inspector.get_columns("klm_omnichannel_messages")
        }
        assert {"connection_id", "provider"} <= message_columns
        backfilled = connection.execute(
            sa.text(
                "SELECT connection_id, provider FROM klm_omnichannel_messages WHERE id = :id"
            ),
            {"id": message_a},
        ).one()
        assert backfilled == (connection_a, "meta_cloud")

        event_id = str(uuid4())
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_message_delivery_events "
                "(id, workspace_id, message_id, thread_id, connection_id, provider, "
                "connection_key, external_event_id, external_message_id, provider_status, "
                "projected_status_before, projected_status_after, payload_sha256, occurred_at) "
                "VALUES (:id, :workspace, :message, :thread, :connection, 'meta_cloud', "
                "'wa-main', 'event-1', 'wamid-1', 'delivered', 'sent', 'delivered', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                "CURRENT_TIMESTAMP)"
            ),
            {
                "id": event_id,
                "workspace": workspace_a,
                "message": message_a,
                "thread": thread_a,
                "connection": connection_a,
            },
        )
        with pytest.raises((IntegrityError, OperationalError)):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "UPDATE klm_omnichannel_message_delivery_events "
                        "SET provider_status = 'read' WHERE id = :id"
                    ),
                    {"id": event_id},
                )

        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "INSERT INTO klm_omnichannel_message_delivery_events "
                        "(id, workspace_id, message_id, thread_id, connection_id, provider, "
                        "connection_key, external_event_id, external_message_id, provider_status, "
                        "projected_status_before, projected_status_after, payload_sha256, "
                        "occurred_at) VALUES (:id, :workspace, :message, :thread, :connection, "
                        "'meta_cloud', 'wa-main', 'cross-tenant', 'wamid-x', 'sent', 'queued', "
                        "'sent', 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                        "CURRENT_TIMESTAMP)"
                    ),
                    {
                        "id": str(uuid4()),
                        "workspace": workspace_b,
                        "message": message_a,
                        "thread": thread_b,
                        "connection": connection_b,
                    },
                )

        migration.downgrade()
        downgraded = sa.inspect(connection)
        assert not (set(migration.TABLES) & set(downgraded.get_table_names()))
        assert "connection_id" not in {
            item["name"] for item in downgraded.get_columns("klm_omnichannel_messages")
        }
