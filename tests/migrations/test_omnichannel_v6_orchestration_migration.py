from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

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
    / "f9d1e3a5c720_add_omnichannel_connections_and_automation.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "omnichannel_v6_orchestration_migration", MIGRATION_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_parent_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    workspaces = sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "klm_omnichannel_threads",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    sa.Table(
        "klm_omnichannel_channel_events",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    metadata.create_all(connection)


def test_omnichannel_orchestration_migration_round_trip_and_tenant_constraints(
    monkeypatch,
) -> None:
    migration = _load_migration_module()
    assert migration.revision == "f9d1e3a5c720"
    assert migration.down_revision == "f8c0e2a4b619"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _create_parent_schema(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert set(migration.TABLES) <= set(inspector.get_table_names())
        assert {
            "transport_mode",
            "capabilities",
            "credential_reference",
            "version",
        } <= {
            column["name"]
            for column in inspector.get_columns("klm_omnichannel_channel_connections")
        }
        assert {"coexist_with_ai", "conditions", "actions", "version"} <= {
            column["name"] for column in inspector.get_columns("klm_omnichannel_automation_rules")
        }

        workspace_a = str(uuid4())
        workspace_b = str(uuid4())
        thread_id = str(uuid4())
        event_id = str(uuid4())
        connection_id = str(uuid4())
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:a), (:b)"),
            {"a": workspace_a, "b": workspace_b},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_threads (id, workspace_id) VALUES (:id, :workspace)"
            ),
            {"id": thread_id, "workspace": workspace_a},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_channel_events (id, workspace_id) "
                "VALUES (:id, :workspace)"
            ),
            {"id": event_id, "workspace": workspace_a},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_channel_connections "
                "(id, workspace_id, channel, provider, connection_key, display_name) "
                "VALUES (:id, :workspace, 'instagram', 'meta_instagram', "
                "'instagram:principal', 'Instagram KLM')"
            ),
            {"id": connection_id, "workspace": workspace_a},
        )

        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "INSERT INTO klm_omnichannel_action_intents "
                        "(id, workspace_id, thread_id, event_id, connection_id, action_type, "
                        "required_capability, idempotency_key, payload_sha256) VALUES "
                        "(:id, :workspace, :thread, :event, :connection, 'private_reply', "
                        "'private_reply', 'cross-tenant-action', :hash)"
                    ),
                    {
                        "id": str(uuid4()),
                        "workspace": workspace_b,
                        "thread": thread_id,
                        "event": event_id,
                        "connection": connection_id,
                        "hash": "a" * 64,
                    },
                )

        migration.downgrade()
        assert not (set(migration.TABLES) & set(sa.inspect(connection).get_table_names()))

        migration.upgrade()
        assert set(migration.TABLES) <= set(sa.inspect(connection).get_table_names())
