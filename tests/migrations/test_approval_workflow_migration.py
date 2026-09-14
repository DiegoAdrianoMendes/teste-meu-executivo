from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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
    / "f6b8d0e2a417_linearize_approval_workflow.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "approval_workflow_migration",
        MIGRATION_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_approval_table(connection: sa.Connection) -> sa.Table:
    metadata = sa.MetaData()
    table = sa.Table(
        "approval_queue_items",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("matter_id", sa.String(36), nullable=False),
        sa.Column("approval_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
    )
    metadata.create_all(connection)
    return table


def test_approval_pending_unique_index_round_trip(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "f6b8d0e2a417"
    assert migration.down_revision == "e5a7c9d1f306"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        approval = _create_approval_table(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

        approval = sa.Table(
            "approval_queue_items",
            sa.MetaData(),
            autoload_with=connection,
        )
        columns = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns("approval_queue_items")
        }
        assert columns[migration.INTENT_COLUMN]["nullable"] is False
        indexes = {
            index["name"]: index
            for index in sa.inspect(connection).get_indexes("approval_queue_items")
        }
        assert indexes[migration.INDEX_NAME]["unique"] == 1

        connection.execute(
            approval.insert(),
            [
                {
                    "id": "approved-1",
                    "matter_id": "matter-1",
                    "approval_type": "petition",
                    "intent_key": "historical:approved-1",
                    "status": "approved",
                },
                {
                    "id": "approved-2",
                    "matter_id": "matter-1",
                    "approval_type": "petition",
                    "intent_key": "historical:approved-2",
                    "status": "approved",
                },
                {
                    "id": "pending-1",
                    "matter_id": "matter-1",
                    "approval_type": "petition",
                    "intent_key": "petition:draft-v1",
                    "status": "pending",
                },
                {
                    "id": "pending-other-type",
                    "matter_id": "matter-1",
                    "approval_type": "contract",
                    "intent_key": "contract:draft-v1",
                    "status": "pending",
                },
                {
                    "id": "pending-other-intent",
                    "matter_id": "matter-1",
                    "approval_type": "petition",
                    "intent_key": "petition:draft-v2",
                    "status": "pending",
                },
            ],
        )
        savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError):
            connection.execute(
                approval.insert().values(
                    id="pending-duplicate",
                    matter_id="matter-1",
                    approval_type="petition",
                    intent_key="petition:draft-v1",
                    status="pending",
                )
            )
        savepoint.rollback()

        migration.downgrade()
        assert migration.INDEX_NAME not in {
            index["name"]
            for index in sa.inspect(connection).get_indexes("approval_queue_items")
        }
        assert migration.INTENT_COLUMN not in {
            column["name"]
            for column in sa.inspect(connection).get_columns("approval_queue_items")
        }


def test_migration_backfills_distinct_intents_for_historical_pending_rows(
    monkeypatch,
) -> None:
    migration = _load_migration_module()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        approval = _create_approval_table(connection)
        connection.execute(
            approval.insert(),
            [
                {
                    "id": "pending-1",
                    "matter_id": "matter-1",
                    "approval_type": "petition",
                    "status": "pending",
                },
                {
                    "id": "pending-2",
                    "matter_id": "matter-1",
                    "approval_type": "petition",
                    "status": "pending",
                },
            ],
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()

        migrated = sa.Table(
            "approval_queue_items",
            sa.MetaData(),
            autoload_with=connection,
        )
        rows = connection.execute(
            sa.select(migrated.c.id, migrated.c.intent_key).order_by(migrated.c.id)
        ).all()

        assert rows == [
            ("pending-1", "legacy:pending-1"),
            ("pending-2", "legacy:pending-2"),
        ]
        assert migration.INDEX_NAME in {
            index["name"]
            for index in sa.inspect(connection).get_indexes("approval_queue_items")
        }
