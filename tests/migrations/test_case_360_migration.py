from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.case_party import CaseParty

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path

MIGRATION_PATH = (
    PROJECT_ROOT / "alembic" / "versions" / "e7a9c1d3f5b7_add_case_parties.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("case_360_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "legal_demands",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
    )
    metadata.create_all(connection)
    connection.execute(sa.text("INSERT INTO workspaces (id) VALUES ('workspace-a')"))
    connection.execute(
        sa.text("INSERT INTO legal_demands (id, workspace_id) VALUES ('demand-a', 'workspace-a')")
    )


def _install(connection: sa.Connection):
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def test_case_360_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        assert migration.revision == "e7a9c1d3f5b7"
        assert migration.down_revision == "cb28b2376b5d"

        migration.upgrade()

        inspector = sa.inspect(connection)
        table_names = set(inspector.get_table_names())
        assert "case_parties" in table_names

        indexes = {index["name"] for index in inspector.get_indexes("case_parties")}
        assert "ix_case_party_workspace_id" in indexes
        assert "ix_case_party_legal_demand_id" in indexes
        assert "ix_case_party_pole" in indexes

        connection.execute(
            sa.text(
                "INSERT INTO case_parties "
                "(id, workspace_id, legal_demand_id, name, pole, qualification, "
                "created_at, updated_at) "
                "VALUES ('party-a', 'workspace-a', 'demand-a', 'Contraparte Ltda', "
                "'passivo', '{}', '2026-07-27', '2026-07-27')"
            )
        )
        assert connection.execute(
            sa.text("SELECT name FROM case_parties WHERE id = 'party-a'")
        ).scalar_one() == "Contraparte Ltda"

        # CHECK constraint do enum local — polo fora do vocabulário fechado
        # (ativo/passivo/terceiro_interessado) é rejeitado.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO case_parties "
                    "(id, workspace_id, legal_demand_id, name, pole, qualification, "
                    "created_at, updated_at) "
                    "VALUES ('party-b', 'workspace-a', 'demand-a', 'Parte Inválida', "
                    "'invalido', '{}', '2026-07-27', '2026-07-27')"
                )
            )

        migration.downgrade()

        remaining = set(sa.inspect(connection).get_table_names())
        assert "case_parties" not in remaining
    engine.dispose()


def test_case_party_model_compiles_for_postgresql() -> None:
    ddl = str(sa.schema.CreateTable(CaseParty.__table__).compile(dialect=postgresql.dialect()))
    assert "UUID" in ddl
    assert "case_party_pole_enum" in ddl
    assert "legal_demands" in ddl
