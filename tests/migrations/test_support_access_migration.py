from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError

from app.models.support_access import SupportAccessEvent, SupportAccessGrant

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
    / "e7a9c1d3f205_add_local_support_break_glass.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("support_access_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_parent_tables(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "administrative_sessions",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    metadata.create_all(connection)


def test_support_access_migration_round_trip_and_immutable_events(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "e7a9c1d3f205"
    assert migration.down_revision == "d6f8a0c2e105"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_parent_tables(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {"support_access_grants", "support_access_events"} <= set(
            inspector.get_table_names()
        )
        assert {
            index["name"]
            for index in inspector.get_indexes("support_access_grants")
        } == {
            "ix_support_access_requester_state_requested",
            "ix_support_access_state_expires",
            "ix_support_access_workspace_state_requested",
        }

        workspace_id = str(uuid4())
        requester_id = str(uuid4())
        grant_id = str(uuid4())
        event_id = str(uuid4())
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:id)"),
            {"id": workspace_id},
        )
        connection.execute(
            sa.text("INSERT INTO users (id) VALUES (:id)"),
            {"id": requester_id},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO support_access_grants (
                    id, workspace_id, requester_user_id, reason, ticket_reference,
                    requested_scopes, requested_duration_minutes, state, version
                ) VALUES (
                    :id, :workspace_id, :requester_user_id, :reason, :ticket_reference,
                    :requested_scopes, 15, 'requested', 1
                )
                """
            ),
            {
                "id": grant_id,
                "workspace_id": workspace_id,
                "requester_user_id": requester_id,
                "reason": "Diagnóstico local explicitamente autorizado.",
                "ticket_reference": "LOCAL-123",
                "requested_scopes": '["workspace:read"]',
            },
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO support_access_events (
                    id, grant_id, workspace_id, actor_user_id, event_type,
                    from_state, to_state, details
                ) VALUES (
                    :id, :grant_id, :workspace_id, :actor_user_id,
                    'support_access_requested', NULL, 'requested', '{}'
                )
                """
            ),
            {
                "id": event_id,
                "grant_id": grant_id,
                "workspace_id": workspace_id,
                "actor_user_id": requester_id,
            },
        )

        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                sa.text(
                    "UPDATE support_access_events SET event_type = 'changed' WHERE id = :id"
                ),
                {"id": event_id},
            )
        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                sa.text("DELETE FROM support_access_events WHERE id = :id"),
                {"id": event_id},
            )

        migration.downgrade()
        assert {"support_access_grants", "support_access_events"}.isdisjoint(
            sa.inspect(connection).get_table_names()
        )


def test_support_access_models_compile_for_postgresql() -> None:
    grant_ddl = str(
        sa.schema.CreateTable(SupportAccessGrant.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    event_ddl = str(
        sa.schema.CreateTable(SupportAccessEvent.__table__).compile(
            dialect=postgresql.dialect()
        )
    )

    assert "UUID" in grant_ddl
    assert "support_access_state_enum" in grant_ddl
    assert "JSONB" in grant_ddl
    assert "JSONB" in event_ddl
