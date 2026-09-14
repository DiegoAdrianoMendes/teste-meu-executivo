from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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
    / "c9e2f4a6b803_add_native_capability_records.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "native_capability_records_migration",
        MIGRATION_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_capability_records_and_outbox_round_trip(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "c9e2f4a6b803"
    assert migration.down_revision == "b8d1e3f5a702"

    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "oss_native_commands",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
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
        assert {"oss_native_records", "oss_native_outbox_events"} <= set(
            inspector.get_table_names()
        )
        record_columns = {
            column["name"] for column in inspector.get_columns("oss_native_records")
        }
        assert {
            "workspace_id",
            "scope_key",
            "resource_id",
            "last_operation",
            "row_version",
            "last_command_id",
        } <= record_columns
        outbox_columns = {
            column["name"] for column in inspector.get_columns("oss_native_outbox_events")
        }
        assert {
            "workspace_id",
            "unit_id",
            "binding_id",
            "dedupe_key",
            "status",
            "lease_expires_at",
        } <= outbox_columns

        migration.downgrade()

        assert "oss_native_records" not in sa.inspect(connection).get_table_names()
        assert "oss_native_outbox_events" not in sa.inspect(connection).get_table_names()
