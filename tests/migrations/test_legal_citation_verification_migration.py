from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.legal_artifact_verification import LegalCitationVerification, LegalReviewFinding

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
    / "a3f9c1e6b204_add_legal_citation_verification_and_review.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "legal_citation_verification_migration", MIGRATION_PATH
    )
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
        "klm_legal_artifact_versions",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
    )
    metadata.create_all(connection)
    connection.execute(sa.text("INSERT INTO workspaces (id) VALUES ('workspace-a')"))
    connection.execute(sa.text("INSERT INTO users (id) VALUES ('user-a')"))
    connection.execute(
        sa.text("INSERT INTO legal_demands (id, workspace_id) VALUES ('demand-a', 'workspace-a')")
    )
    connection.execute(
        sa.text(
            "INSERT INTO klm_legal_artifact_versions (id, workspace_id) "
            "VALUES ('artifact-a', 'workspace-a')"
        )
    )


def _install(connection: sa.Connection):
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def test_legal_citation_verification_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        assert migration.revision == "a3f9c1e6b204"
        assert migration.down_revision == "d0f2a4c6b813"

        migration.upgrade()

        inspector = sa.inspect(connection)
        table_names = set(inspector.get_table_names())
        assert {"legal_citation_verifications", "legal_review_findings"} <= table_names

        citation_indexes = {
            index["name"] for index in inspector.get_indexes("legal_citation_verifications")
        }
        assert "ix_legal_citation_verification_artifact_version_id" in citation_indexes
        assert "ix_legal_citation_verification_status" in citation_indexes

        finding_indexes = {
            index["name"] for index in inspector.get_indexes("legal_review_findings")
        }
        assert "ix_legal_review_finding_severity" in finding_indexes

        connection.execute(
            sa.text(
                "INSERT INTO legal_citation_verifications "
                "(id, workspace_id, legal_demand_id, artifact_version_id, citation_text, "
                "citation_kind, status, trail, created_at, updated_at) "
                "VALUES ('citation-a', 'workspace-a', 'demand-a', 'artifact-a', "
                "'Súmula 7 do STJ', 'sumula', 'nao_verificada', '{}', "
                "'2026-07-27', '2026-07-27')"
            )
        )
        assert connection.execute(
            sa.text("SELECT status FROM legal_citation_verifications WHERE id = 'citation-a'")
        ).scalar_one() == "nao_verificada"

        # Idempotência declarativa: mesma citação para o mesmo artefato é
        # única (uq_legal_citation_verification_artifact_text).
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO legal_citation_verifications "
                    "(id, workspace_id, legal_demand_id, artifact_version_id, citation_text, "
                    "citation_kind, status, trail, created_at, updated_at) "
                    "VALUES ('citation-b', 'workspace-a', 'demand-a', 'artifact-a', "
                    "'Súmula 7 do STJ', 'sumula', 'nao_verificada', '{}', "
                    "'2026-07-27', '2026-07-27')"
                )
            )

    engine.dispose()


def test_legal_citation_verification_migration_downgrade() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        migration.upgrade()
        migration.downgrade()

        remaining = set(sa.inspect(connection).get_table_names())
        assert "legal_citation_verifications" not in remaining
        assert "legal_review_findings" not in remaining
    engine.dispose()


def test_legal_citation_verification_models_compile_for_postgresql() -> None:
    citation_ddl = str(
        sa.schema.CreateTable(LegalCitationVerification.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "UUID" in citation_ddl
    assert "citation_text" in citation_ddl

    finding_ddl = str(
        sa.schema.CreateTable(LegalReviewFinding.__table__).compile(dialect=postgresql.dialect())
    )
    assert "severity" in finding_ddl
    assert "JSONB" in finding_ddl
