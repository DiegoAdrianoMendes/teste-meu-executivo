from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

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
    / "a2c4e6f8b10c_add_provider_media_upload_reconciliations.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "provider_media_reconciliation_migration", MIGRATION_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_previous_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    workspaces = sa.Table("workspaces", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    connections = sa.Table(
        "klm_omnichannel_channel_connections",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    sa.Table(
        "klm_omnichannel_provider_media_upload_intents",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [connections.c.workspace_id, connections.c.id],
        ),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    metadata.create_all(connection)


def test_provider_media_reconciliation_is_tenant_bound_and_append_only(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "a2c4e6f8b10c"
    assert migration.down_revision == "9d1f3a5c7b09"
    assert all(len(name) <= 63 for name in migration.INDEX_NAMES.values())
    assert "BEFORE TRUNCATE" in MIGRATION_PATH.read_text(encoding="utf-8")

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _create_previous_schema(connection)
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        workspace_a, workspace_b = uuid4(), uuid4()
        connection_a, connection_b = uuid4(), uuid4()
        intent_a, intent_b = uuid4(), uuid4()
        actor_id = uuid4()
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:a), (:b)"),
            {"a": workspace_a.hex, "b": workspace_b.hex},
        )
        connection.execute(
            sa.text("INSERT INTO users (id) VALUES (:actor)"),
            {"actor": actor_id.hex},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_channel_connections (id, workspace_id) "
                "VALUES (:ca, :wa), (:cb, :wb)"
            ),
            {
                "ca": connection_a.hex,
                "wa": workspace_a.hex,
                "cb": connection_b.hex,
                "wb": workspace_b.hex,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_provider_media_upload_intents "
                "(id, workspace_id, connection_id) VALUES "
                "(:ia, :wa, :ca), (:ib, :wb, :cb)"
            ),
            {
                "ia": intent_a.hex,
                "wa": workspace_a.hex,
                "ca": connection_a.hex,
                "ib": intent_b.hex,
                "wb": workspace_b.hex,
                "cb": connection_b.hex,
            },
        )

        migration.upgrade()
        reconciliation_id = uuid4()
        insert = sa.text(
            f"INSERT INTO {migration.TABLE_NAME} "
            "(id, workspace_id, connection_id, upload_intent_id, actor_user_id, "
            "decision, reason, provider_media_id, provider_receipt) "
            "VALUES (:id, :workspace, :connection, :intent, :actor, "
            "'provider_accepted', :reason, :media, :receipt)"
        )
        values = {
            "id": reconciliation_id.hex,
            "workspace": workspace_a.hex,
            "connection": connection_a.hex,
            "intent": intent_a.hex,
            "actor": actor_id.hex,
            "reason": "Prova recuperada no provedor.",
            "media": "meta-media-1",
            "receipt": '{"id":"meta-media-1"}',
        }
        connection.execute(insert, values)

        for statement in (
            f"UPDATE {migration.TABLE_NAME} SET reason = 'alterado' WHERE id = :id",
            f"DELETE FROM {migration.TABLE_NAME} WHERE id = :id",
        ):
            with pytest.raises((DBAPIError, OperationalError)):
                with connection.begin_nested():
                    connection.execute(sa.text(statement), {"id": reconciliation_id.hex})

        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    insert,
                    {
                        **values,
                        "id": uuid4().hex,
                        "intent": intent_b.hex,
                    },
                )
        with pytest.raises((DBAPIError, IntegrityError)):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        f"INSERT INTO {migration.TABLE_NAME} "
                        "(id, workspace_id, connection_id, upload_intent_id, decision, reason) "
                        "VALUES (:id, :workspace, :connection, :intent, "
                        "'provider_accepted', 'Sem prova')"
                    ),
                    {
                        "id": uuid4().hex,
                        "workspace": workspace_a.hex,
                        "connection": connection_a.hex,
                        "intent": intent_a.hex,
                    },
                )
    engine.dispose()
