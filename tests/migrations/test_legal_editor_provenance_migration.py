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
    / "d0f2a4c6b813_add_legal_editor_provenance_columns.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("legal_editor_provenance_migration", MIGRATION_PATH)
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
        sa.Column("matter_id", sa.String(36), nullable=False),
        sa.Column("artifact_key", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_snapshot", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("evidence_schema_version", sa.Integer(), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(connection)
    connection.execute(sa.text("INSERT INTO workspaces (id) VALUES ('workspace-a')"))
    connection.execute(sa.text("INSERT INTO users (id) VALUES ('user-a')"))
    connection.execute(
        sa.text("INSERT INTO matters (id, workspace_id) VALUES ('matter-a', 'workspace-a')")
    )
    connection.execute(
        sa.text(
            "INSERT INTO legal_demands (id, workspace_id) VALUES ('demand-a', 'workspace-a')"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO klm_legal_artifact_versions "
            "(id, workspace_id, matter_id, artifact_key, version, kind, title, content, "
            "source_snapshot, created_by_user_id, evidence_schema_version, evidence_sha256, "
            "created_at) VALUES ('artifact-a', 'workspace-a', 'matter-a', 'artifact-key-a', 1, "
            "'peticao_inicial', 'Peça pré-existente', '{}', '{}', 'user-a', 1, :sha256, "
            "'2026-07-27')"
        ),
        {"sha256": "0" * 64},
    )


def _install(connection: sa.Connection):
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def test_legal_editor_provenance_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        assert migration.revision == "d0f2a4c6b813"
        assert migration.down_revision == "e7a9c1d3f5b7"

        migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {col["name"] for col in inspector.get_columns("klm_legal_artifact_versions")}
        assert {
            "legal_demand_id",
            "author_type",
            "author_agent_key",
            "office_template_key",
            "office_template_object_id",
            "office_template_object_version",
            "protocolability_state",
        } <= columns

        # server_default aplica-se à linha pré-existente.
        row = connection.execute(
            sa.text(
                "SELECT author_type, protocolability_state FROM klm_legal_artifact_versions "
                "WHERE id = 'artifact-a'"
            )
        ).one()
        assert row.author_type == "human"
        assert row.protocolability_state == "bloqueado"

        indexes = {ix["name"] for ix in inspector.get_indexes("klm_legal_artifact_versions")}
        assert "ix_klm_legal_artifact_version_legal_demand_id" in indexes
        assert "ix_klm_legal_artifact_version_protocolability_state" in indexes

        # CHECK fail-closed: author_type fora do enum.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO klm_legal_artifact_versions "
                    "(id, workspace_id, matter_id, artifact_key, version, kind, title, "
                    "content, source_snapshot, created_by_user_id, evidence_schema_version, "
                    "evidence_sha256, created_at, author_type) VALUES "
                    "('artifact-b', 'workspace-a', 'matter-a', 'artifact-key-b', 1, "
                    "'peticao_inicial', 'x', '{}', '{}', 'user-a', 1, :sha256, "
                    "'2026-07-27', 'robo')"
                ),
                {"sha256": "1" * 64},
            )

        # CHECK fail-closed: author_type='agent' exige author_agent_key.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO klm_legal_artifact_versions "
                    "(id, workspace_id, matter_id, artifact_key, version, kind, title, "
                    "content, source_snapshot, created_by_user_id, evidence_schema_version, "
                    "evidence_sha256, created_at, author_type, author_agent_key) VALUES "
                    "('artifact-c', 'workspace-a', 'matter-a', 'artifact-key-c', 1, "
                    "'peticao_inicial', 'x', '{}', '{}', 'user-a', 1, :sha256, "
                    "'2026-07-27', 'agent', NULL)"
                ),
                {"sha256": "2" * 64},
            )

        # CHECK fail-closed: pin do office_template precisa vir em par.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO klm_legal_artifact_versions "
                    "(id, workspace_id, matter_id, artifact_key, version, kind, title, "
                    "content, source_snapshot, created_by_user_id, evidence_schema_version, "
                    "evidence_sha256, created_at, office_template_object_id) VALUES "
                    "('artifact-d', 'workspace-a', 'matter-a', 'artifact-key-d', 1, "
                    "'peticao_inicial', 'x', '{}', '{}', 'user-a', 1, :sha256, "
                    "'2026-07-27', 'template-object-a')"
                ),
                {"sha256": "3" * 64},
            )

        migration.downgrade()

        remaining = {
            col["name"] for col in sa.inspect(connection).get_columns("klm_legal_artifact_versions")
        }
        assert "legal_demand_id" not in remaining
        assert "author_type" not in remaining
        assert "protocolability_state" not in remaining
    engine.dispose()
