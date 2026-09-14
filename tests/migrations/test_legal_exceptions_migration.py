from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.legal_exception import LegalException

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path

MIGRATION_PATH = (
    PROJECT_ROOT / "alembic" / "versions" / "3040c77e8efa_add_legal_exceptions_center.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("legal_exceptions_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "matters",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
    )
    metadata.create_all(connection)
    connection.execute(sa.text("INSERT INTO workspaces (id) VALUES ('workspace-a')"))
    connection.execute(sa.text("INSERT INTO users (id) VALUES ('user-a')"))
    connection.execute(
        sa.text(
            "INSERT INTO matters (id, workspace_id) VALUES ('matter-a', 'workspace-a')"
        )
    )


def _install(connection: sa.Connection):
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def test_legal_exceptions_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        assert migration.revision == "3040c77e8efa"
        assert migration.down_revision == "838f9f719531"

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert "legal_exceptions" in inspector.get_table_names()
        index_names = {index["name"] for index in inspector.get_indexes("legal_exceptions")}
        assert "uq_legal_exception_open_intent" in index_names
        assert "ix_legal_exception_workspace_id" in index_names
        assert "ix_legal_exception_matter_id" in index_names
        assert "ix_legal_exception_exception_kind" in index_names
        assert "ix_legal_exception_status" in index_names

        connection.execute(
            sa.text(
                "INSERT INTO legal_exceptions "
                "(id, workspace_id, matter_id, exception_kind, origin, dedupe_key, "
                "title, priority, status, created_at, updated_at) "
                "VALUES ('exc-a', 'workspace-a', 'matter-a', "
                "'publicacao_sem_vinculo_confiavel', 'process_monitoring.ingest_publication', "
                "'djen:ext-1', 'Publicacao sem processo vinculado', 'media', 'aberta', "
                "'2026-07-26', '2026-07-26')"
            )
        )
        assert connection.execute(
            sa.text("SELECT exception_kind FROM legal_exceptions WHERE id = 'exc-a'")
        ).scalar_one() == "publicacao_sem_vinculo_confiavel"

        # Idempotência: mesma origem+chave enquanto aberta não pode duplicar.
        with sa_raises_integrity_error():
            connection.execute(
                sa.text(
                    "INSERT INTO legal_exceptions "
                    "(id, workspace_id, matter_id, exception_kind, origin, dedupe_key, "
                    "title, priority, status, created_at, updated_at) "
                    "VALUES ('exc-b', 'workspace-a', 'matter-a', "
                    "'publicacao_sem_vinculo_confiavel', "
                    "'process_monitoring.ingest_publication', 'djen:ext-1', "
                    "'Duplicata', 'media', 'aberta', '2026-07-26', '2026-07-26')"
                )
            )

        migration.downgrade()

        remaining = set(sa.inspect(connection).get_table_names())
        assert "legal_exceptions" not in remaining
    engine.dispose()


def sa_raises_integrity_error():
    import pytest

    return pytest.raises(sa.exc.IntegrityError)


def test_legal_exceptions_model_compiles_for_postgresql() -> None:
    ddl = str(
        sa.schema.CreateTable(LegalException.__table__).compile(dialect=postgresql.dialect())
    )
    assert "UUID" in ddl
    assert "exception_kind_enum" in ddl
    assert "exception_priority_enum" in ddl
    assert "exception_center_status_enum" in ddl
