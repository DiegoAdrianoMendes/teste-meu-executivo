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
    PROJECT_ROOT / "alembic" / "versions" / "3d5f7a9b1e43_add_task_idempotency.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("task_idempotency_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_task_idempotency_migration_round_trip(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "3d5f7a9b1e43"
    assert migration.down_revision == "2c4e6f8a0d32"

    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "tasks",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("matter_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {column["name"] for column in inspector.get_columns("tasks")}
        assert {"idempotency_key", "request_sha256"} <= columns
        indexes = {index["name"]: index for index in inspector.get_indexes("tasks")}
        assert indexes["ux_tasks_matter_idempotency"]["unique"] == 1
        matter_id = str(uuid4())
        key = "copilot-task:thread-001:documentos"
        insert = sa.text(
            "INSERT INTO tasks (id, matter_id, title, idempotency_key) "
            "VALUES (:id, :matter_id, :title, :key)"
        )
        connection.execute(
            insert,
            {"id": str(uuid4()), "matter_id": matter_id, "title": "Primeira", "key": key},
        )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    insert,
                    {"id": str(uuid4()), "matter_id": matter_id, "title": "Segunda", "key": key},
                )
        connection.execute(
            insert,
            {"id": str(uuid4()), "matter_id": str(uuid4()), "title": "Outro caso", "key": key},
        )

        migration.downgrade()

        downgraded = {column["name"] for column in sa.inspect(connection).get_columns("tasks")}
        assert downgraded == {"id", "matter_id", "title"}
