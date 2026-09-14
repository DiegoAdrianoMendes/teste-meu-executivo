from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.protocol_folder import ProtocolFolder

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path

MIGRATION_PATH = PROJECT_ROOT / "alembic" / "versions" / "b6b46ea00c28_add_protocol_folder.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("protocol_folder_migration", MIGRATION_PATH)
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
        "matters",
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
        sa.text(
            "INSERT INTO legal_demands (id, workspace_id) VALUES ('demand-a', 'workspace-a')"
        )
    )
    connection.execute(
        sa.text("INSERT INTO matters (id, workspace_id) VALUES ('matter-a', 'workspace-a')")
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


def test_protocol_folder_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        assert migration.revision == "b6b46ea00c28"
        assert migration.down_revision == "4ce67f6149cd"

        migration.upgrade()

        table_names = set(sa.inspect(connection).get_table_names())
        assert "protocol_folders" in table_names

        index_names = {
            index["name"] for index in sa.inspect(connection).get_indexes("protocol_folders")
        }
        assert "ix_protocol_folder_workspace_id" in index_names
        assert "ix_protocol_folder_state" in index_names

        connection.execute(
            sa.text(
                "INSERT INTO protocol_folders "
                "(id, workspace_id, legal_demand_id, matter_id, artifact_version_id, "
                "tribunal, act_type, state, manifest, files, signature_confirmed, "
                "created_at, updated_at) "
                "VALUES ('folder-a', 'workspace-a', 'demand-a', 'matter-a', 'artifact-a', "
                "'TJDFT', 'petition', 'bloqueado', '{}', '[]', 0, '2026-07-27', '2026-07-27')"
            )
        )
        # Uma segunda linha para a MESMA (workspace, demanda, artefato) é rejeitada —
        # upsert é feito pelo serviço, nunca por um segundo INSERT.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO protocol_folders "
                    "(id, workspace_id, legal_demand_id, matter_id, artifact_version_id, "
                    "tribunal, act_type, state, manifest, files, signature_confirmed, "
                    "created_at, updated_at) "
                    "VALUES ('folder-b', 'workspace-a', 'demand-a', 'matter-a', 'artifact-a', "
                    "'TJDFT', 'petition', 'bloqueado', '{}', '[]', 0, "
                    "'2026-07-27', '2026-07-27')"
                )
            )
        # `state` fora do vocabulário fechado é rejeitado pelo CHECK constraint.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO protocol_folders "
                    "(id, workspace_id, legal_demand_id, matter_id, artifact_version_id, "
                    "tribunal, act_type, state, manifest, files, signature_confirmed, "
                    "created_at, updated_at) "
                    "VALUES ('folder-c', 'workspace-a', 'demand-a', 'matter-a', 'artifact-a', "
                    "'TJDFT', 'petition', 'pronto', '{}', '[]', 0, "
                    "'2026-07-27', '2026-07-27')"
                )
            )
        # Recibo só com o par completo (registrado_em + registrado_por) — nunca um sem o outro.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO protocol_folders "
                    "(id, workspace_id, legal_demand_id, matter_id, artifact_version_id, "
                    "tribunal, act_type, state, manifest, files, signature_confirmed, "
                    "receipt_registered_at, receipt_registered_by_user_id, "
                    "created_at, updated_at) "
                    "VALUES ('folder-d', 'workspace-a', 'demand-a', 'matter-a', 'artifact-a', "
                    "'TJDFT', 'petition', 'bloqueado', '{}', '[]', 0, "
                    "'2026-07-27', NULL, '2026-07-27', '2026-07-27')"
                )
            )

        assert (
            connection.execute(sa.text("SELECT COUNT(*) FROM protocol_folders")).scalar_one() == 1
        )

        migration.downgrade()
        remaining = set(sa.inspect(connection).get_table_names())
        assert "protocol_folders" not in remaining
    engine.dispose()


def test_protocol_folder_migration_skips_postgres_only_statements_on_sqlite() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        migration.upgrade()
        migration.downgrade()
    engine.dispose()


def test_protocol_folder_model_compiles_for_postgresql() -> None:
    ddl = str(sa.schema.CreateTable(ProtocolFolder.__table__).compile(dialect=postgresql.dialect()))
    assert "UUID" in ddl
