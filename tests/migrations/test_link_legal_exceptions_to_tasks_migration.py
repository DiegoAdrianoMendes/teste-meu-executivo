from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path

MIGRATION_PATH = (
    PROJECT_ROOT / "alembic" / "versions" / "d7e9f1a3c5b6_link_legal_exceptions_to_tasks.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("link_exceptions_to_tasks_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "tasks",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("matter_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
    )
    sa.Table(
        "legal_exceptions",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
    )
    metadata.create_all(connection)


def _install(connection: sa.Connection):
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def test_link_legal_exceptions_to_tasks_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        assert migration.revision == "d7e9f1a3c5b6"
        assert migration.down_revision == "c67346a8bba6"

        migration.upgrade()

        inspector = sa.inspect(connection)
        task_columns = {column["name"] for column in inspector.get_columns("tasks")}
        exception_columns = {column["name"] for column in inspector.get_columns("legal_exceptions")}
        assert "source_exception_id" in task_columns
        assert "linked_task_id" in exception_columns

        task_indexes = {index["name"]: index for index in inspector.get_indexes("tasks")}
        exception_indexes = {
            index["name"]: index for index in inspector.get_indexes("legal_exceptions")
        }
        assert task_indexes["ux_tasks_source_exception_id"]["unique"] == 1
        assert exception_indexes["ux_legal_exceptions_linked_task_id"]["unique"] == 1

        connection.execute(
            sa.text("INSERT INTO legal_exceptions (id, workspace_id, title) VALUES ('exc-a', 'ws-a', 'Exceção A')")
        )
        connection.execute(
            sa.text("INSERT INTO legal_exceptions (id, workspace_id, title) VALUES ('exc-b', 'ws-a', 'Exceção B')")
        )
        connection.execute(
            sa.text(
                "INSERT INTO tasks (id, matter_id, title, source_exception_id) "
                "VALUES ('task-a', 'matter-a', 'Tarefa A', 'exc-a')"
            )
        )

        # Não pode existir uma segunda task para a MESMA exceção —
        # unicidade de banco, não só de aplicação.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO tasks (id, matter_id, title, source_exception_id) "
                    "VALUES ('task-b', 'matter-a', 'Tarefa B', 'exc-a')"
                )
            )

        # Mas duas tasks SEM exceção (NULL) são permitidas — índice é
        # parcial (WHERE source_exception_id IS NOT NULL).
        connection.execute(
            sa.text("INSERT INTO tasks (id, matter_id, title) VALUES ('task-c', 'matter-a', 'Tarefa C')")
        )
        connection.execute(
            sa.text("INSERT INTO tasks (id, matter_id, title) VALUES ('task-d', 'matter-a', 'Tarefa D')")
        )

        connection.execute(
            sa.text("UPDATE legal_exceptions SET linked_task_id = 'task-a' WHERE id = 'exc-a'")
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text("UPDATE legal_exceptions SET linked_task_id = 'task-a' WHERE id = 'exc-b'")
            )

        migration.downgrade()

        downgraded_task_columns = {
            column["name"] for column in sa.inspect(connection).get_columns("tasks")
        }
        downgraded_exception_columns = {
            column["name"] for column in sa.inspect(connection).get_columns("legal_exceptions")
        }
        assert "source_exception_id" not in downgraded_task_columns
        assert "linked_task_id" not in downgraded_exception_columns
    engine.dispose()
