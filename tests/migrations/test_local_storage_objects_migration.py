from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import ADDITIONAL_DIRECT_TENANT_TABLES, RLS_POLICY_PREDICATES
from app.models.storage_object import StorageObject

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
    / "d5f7a9c1e004_add_local_storage_objects.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("local_storage_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_parent_tables(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("clients", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("matters", metadata, sa.Column("id", sa.String(36), primary_key=True))
    metadata.create_all(connection)


def test_local_storage_migration_round_trip(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "d5f7a9c1e004"
    assert migration.down_revision == "c4e6f8a0d003"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_parent_tables(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert "storage_objects" in inspector.get_table_names()
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("storage_objects")
        } == {
            "uq_storage_object_storage_key",
            "uq_storage_object_workspace_object_version",
        }
        assert {
            index["name"] for index in inspector.get_indexes("storage_objects")
        } == {
            "ix_storage_object_workspace_object_version",
            "ix_storage_object_workspace_state",
            "ix_storage_objects_client_id",
            "ix_storage_objects_matter_id",
        }

        columns = {column["name"] for column in inspector.get_columns("storage_objects")}
        assert {
            "matter_id",
            "client_id",
            "scan_method",
            "ticket_epoch",
            "reconciliation_attempts",
            "last_reconciled_at",
        } <= columns
        foreign_keys = {
            (
                tuple(item["constrained_columns"]),
                item["referred_table"],
                item["options"].get("ondelete"),
            )
            for item in inspector.get_foreign_keys("storage_objects")
        }
        assert (("matter_id",), "matters", "RESTRICT") in foreign_keys
        assert (("client_id",), "clients", "RESTRICT") in foreign_keys

        migration.downgrade()
        assert "storage_objects" not in sa.inspect(connection).get_table_names()


def test_storage_object_model_and_rls_compile_for_postgresql() -> None:
    ddl = str(
        sa.schema.CreateTable(StorageObject.__table__).compile(
            dialect=postgresql.dialect()
        )
    )

    assert "UUID" in ddl
    assert "storage_object_state_enum" in ddl
    assert "storage_av_status_enum" in ddl
    assert "uq_storage_object_object_version" in ddl
    assert "uq_storage_object_workspace_object_version" in ddl
    assert "storage_objects" in ADDITIONAL_DIRECT_TENANT_TABLES
    assert RLS_POLICY_PREDICATES["storage_objects"].startswith("workspace_id::text")


def test_canonical_models_register_storage_object_in_isolated_metadata() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.db.base import Base; import app.models; "
                "assert 'storage_objects' in Base.metadata.tables"
            ),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
