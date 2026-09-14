from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.case_strategy import CaseFact, CasePleading, CaseProofLink

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
    / "cb28b2376b5d_add_case_strategy_fact_proof_pleading.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("case_strategy_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "legal_demands",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
    )
    sa.Table(
        "documents",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("matter_id", sa.String(36), nullable=False),
    )
    metadata.create_all(connection)
    connection.execute(sa.text("INSERT INTO workspaces (id) VALUES ('workspace-a')"))
    connection.execute(sa.text("INSERT INTO users (id) VALUES ('user-a')"))
    connection.execute(
        sa.text("INSERT INTO legal_demands (id, workspace_id) VALUES ('demand-a', 'workspace-a')")
    )
    connection.execute(
        sa.text("INSERT INTO documents (id, matter_id) VALUES ('doc-a', 'matter-a')")
    )


def _install(connection: sa.Connection):
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def test_case_strategy_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        assert migration.revision == "cb28b2376b5d"
        assert migration.down_revision == "c2d4f6a8e0b3"

        migration.upgrade()

        inspector = sa.inspect(connection)
        table_names = set(inspector.get_table_names())
        assert {"case_facts", "case_proof_links", "case_pleadings"} <= table_names

        fact_indexes = {index["name"] for index in inspector.get_indexes("case_facts")}
        assert "ix_case_fact_legal_demand_id" in fact_indexes
        assert "ix_case_fact_is_controverted" in fact_indexes

        proof_indexes = {index["name"] for index in inspector.get_indexes("case_proof_links")}
        assert "ix_case_proof_link_fact_id" in proof_indexes
        assert "ix_case_proof_link_status" in proof_indexes

        pleading_indexes = {index["name"] for index in inspector.get_indexes("case_pleadings")}
        assert "ix_case_pleading_legal_demand_id" in pleading_indexes

        connection.execute(
            sa.text(
                "INSERT INTO case_facts "
                "(id, workspace_id, legal_demand_id, description, is_controverted, "
                "created_at, updated_at) "
                "VALUES ('fact-a', 'workspace-a', 'demand-a', 'Réu recebeu a notificação', 1, "
                "'2026-07-27', '2026-07-27')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO case_proof_links "
                "(id, workspace_id, legal_demand_id, fact_id, document_id, force, status, "
                "created_at, updated_at) "
                "VALUES ('proof-a', 'workspace-a', 'demand-a', 'fact-a', 'doc-a', 'robusta', "
                "'tem', '2026-07-27', '2026-07-27')"
            )
        )
        assert connection.execute(
            sa.text("SELECT status FROM case_proof_links WHERE id = 'proof-a'")
        ).scalar_one() == "tem"

        connection.execute(
            sa.text(
                "INSERT INTO case_pleadings "
                "(id, workspace_id, legal_demand_id, request_text, supporting_fact_ids, "
                "created_at, updated_at) "
                "VALUES ('pleading-a', 'workspace-a', 'demand-a', 'Condenação ao pagamento', "
                "'[\"fact-a\"]', '2026-07-27', '2026-07-27')"
            )
        )
        assert connection.execute(
            sa.text("SELECT request_text FROM case_pleadings WHERE id = 'pleading-a'")
        ).scalar_one() == "Condenação ao pagamento"

        migration.downgrade()

        remaining = set(sa.inspect(connection).get_table_names())
        assert "case_facts" not in remaining
        assert "case_proof_links" not in remaining
        assert "case_pleadings" not in remaining
    engine.dispose()


def test_case_strategy_models_compile_for_postgresql() -> None:
    fact_ddl = str(sa.schema.CreateTable(CaseFact.__table__).compile(dialect=postgresql.dialect()))
    assert "UUID" in fact_ddl
    assert "is_controverted" in fact_ddl

    proof_ddl = str(
        sa.schema.CreateTable(CaseProofLink.__table__).compile(dialect=postgresql.dialect())
    )
    assert "case_proof_force_enum" in proof_ddl
    assert "case_proof_status_enum" in proof_ddl

    pleading_ddl = str(
        sa.schema.CreateTable(CasePleading.__table__).compile(dialect=postgresql.dialect())
    )
    assert "supporting_fact_ids" in pleading_ddl
