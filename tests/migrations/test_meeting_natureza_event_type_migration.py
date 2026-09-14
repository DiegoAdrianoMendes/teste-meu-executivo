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
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "f3a7c92e5d81_add_meeting_natureza_event_type.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("meeting_natureza_event_type_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "meetings",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
    )
    metadata.create_all(connection)
    connection.execute(sa.text("INSERT INTO workspaces (id) VALUES ('workspace-a')"))
    connection.execute(
        sa.text(
            "INSERT INTO meetings (id, workspace_id, title, starts_at, status) "
            "VALUES ('meeting-a', 'workspace-a', 'Reunião pré-existente', "
            "'2026-07-27 12:00:00', 'scheduled')"
        )
    )


def _install(connection: sa.Connection):
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def test_meeting_natureza_event_type_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        assert migration.revision == "f3a7c92e5d81"
        assert migration.down_revision == "d7e9f1a3c5b6"

        migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {col["name"] for col in inspector.get_columns("meetings")}
        assert {"natureza", "event_type"} <= columns

        # server_default aplica-se às linhas pré-existentes — natureza
        # comercial/reunião, exatamente o significado histórico da tabela.
        row = connection.execute(
            sa.text("SELECT natureza, event_type FROM meetings WHERE id = 'meeting-a'")
        ).one()
        assert row.natureza == "comercial"
        assert row.event_type == "reuniao"

        indexes = {ix["name"] for ix in inspector.get_indexes("meetings")}
        assert "ix_meetings_natureza" in indexes

        # CHECK constraint fail-closed: valor fora do enum é rejeitado.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO meetings "
                    "(id, workspace_id, title, starts_at, status, natureza, event_type) "
                    "VALUES ('meeting-b', 'workspace-a', 'Reunião inválida', "
                    "'2026-07-27 12:00:00', 'scheduled', 'inexistente', 'reuniao')"
                )
            )

        migration.downgrade()

        remaining = {col["name"] for col in sa.inspect(connection).get_columns("meetings")}
        assert "natureza" not in remaining
        assert "event_type" not in remaining
    engine.dispose()


def test_meeting_model_compiles_for_postgresql_with_natureza_and_event_type() -> None:
    from sqlalchemy.dialects import postgresql

    from app.models.meeting import Meeting

    ddl = str(sa.schema.CreateTable(Meeting.__table__).compile(dialect=postgresql.dialect()))
    assert "natureza" in ddl
    assert "meeting_natureza_enum" in ddl
    assert "event_type" in ddl
    assert "meeting_event_type_enum" in ddl
