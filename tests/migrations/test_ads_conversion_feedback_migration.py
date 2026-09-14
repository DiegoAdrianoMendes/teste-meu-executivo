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
    / "8c0e2f4a6b98_add_ads_conversion_feedback_evidence.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "ads_conversion_feedback_migration", MIGRATION_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_previous_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    workspaces = sa.Table(
        "workspaces", metadata, sa.Column("id", sa.Uuid(), primary_key=True)
    )
    sa.Table(
        "klm_external_action_intents",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
    )
    metadata.create_all(connection)


def test_ads_conversion_feedback_migration_is_tenant_safe_and_append_only(
    monkeypatch,
) -> None:
    migration = _load_migration_module()
    assert migration.revision == "8c0e2f4a6b98"
    assert migration.down_revision == "7b9d1f3a5c87"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _create_previous_schema(connection)
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )

        workspace_a, workspace_b = uuid4(), uuid4()
        intent_a, intent_b = uuid4(), uuid4()
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:a), (:b)"),
            {"a": workspace_a.hex, "b": workspace_b.hex},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_external_action_intents (id, workspace_id) "
                "VALUES (:ia, :wa), (:ib, :wb)"
            ),
            {
                "ia": intent_a.hex,
                "wa": workspace_a.hex,
                "ib": intent_b.hex,
                "wb": workspace_b.hex,
            },
        )

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert migration.TABLE_NAME in inspector.get_table_names()
        columns = {
            item["name"] for item in inspector.get_columns(migration.TABLE_NAME)
        }
        assert {
            "provider_event_id",
            "idempotency_key",
            "payload_sha256",
            "request_envelope",
            "provider_receipt",
            "external_actions_executed",
        } <= columns

        record_id = uuid4()
        insert_record = sa.text(
            f"INSERT INTO {migration.TABLE_NAME} "
            "(id, workspace_id, intent_id, provider, record_type, state, "
            "attempt_number, provider_event_id, idempotency_key, payload_sha256) "
            "VALUES (:id, :workspace, :intent, 'meta_ads', 'prepared', "
            "'prepared_local', 0, 'event-1', 'dedupe-1', :sha)"
        )
        connection.execute(
            insert_record,
            {
                "id": record_id.hex,
                "workspace": workspace_a.hex,
                "intent": intent_a.hex,
                "sha": "a" * 64,
            },
        )

        with pytest.raises((DBAPIError, OperationalError)):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        f"UPDATE {migration.TABLE_NAME} SET state = 'accepted' "
                        "WHERE id = :id"
                    ),
                    {"id": record_id.hex},
                )

        with pytest.raises((DBAPIError, OperationalError)):
            with connection.begin_nested():
                connection.execute(
                    sa.text(f"DELETE FROM {migration.TABLE_NAME} WHERE id = :id"),
                    {"id": record_id.hex},
                )

        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    insert_record,
                    {
                        "id": uuid4().hex,
                        "workspace": workspace_a.hex,
                        "intent": intent_b.hex,
                        "sha": "b" * 64,
                    },
                )

        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    insert_record,
                    {
                        "id": uuid4().hex,
                        "workspace": workspace_a.hex,
                        "intent": intent_a.hex,
                        "sha": "a" * 64,
                    },
                )

        migration.downgrade()
        assert migration.TABLE_NAME not in sa.inspect(connection).get_table_names()
