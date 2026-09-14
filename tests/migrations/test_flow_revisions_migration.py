import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path


def _load_migration_module():
    migration_path = PROJECT_ROOT / "alembic" / "versions" / "d1e2f3a4b5c6_add_flow_revisions.py"
    spec = importlib.util.spec_from_file_location(
        "flow_revisions_migration",
        migration_path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_legacy_flow_schema(connection: sa.Connection) -> tuple[str, str]:
    metadata = sa.MetaData()
    flows = sa.Table(
        "flows",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("practice_area", sa.String(100), nullable=False),
        sa.Column("is_template", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("trigger", sa.String(50), nullable=False),
        sa.Column("trigger_config", sa.JSON(), nullable=True),
        sa.Column("nodes", sa.JSON(), nullable=True),
        sa.Column("edges", sa.JSON(), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    flow_runs = sa.Table(
        "flow_runs",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("flow_id", sa.String(36), nullable=False),
    )
    metadata.create_all(connection)

    flow_id = str(uuid4())
    run_id = str(uuid4())
    now = datetime.now(UTC)
    connection.execute(
        flows.insert().values(
            id=flow_id,
            workspace_id=str(uuid4()),
            name="Flow anterior à migration",
            description="Preservar",
            practice_area="universal",
            is_template=False,
            is_active=True,
            trigger="manual",
            trigger_config={"maxSteps": 12},
            nodes=[{"id": "start", "type": "trigger", "config": {}}],
            edges=[],
            run_count=3,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
    )
    connection.execute(flow_runs.insert().values(id=run_id, flow_id=flow_id))
    return flow_id, run_id


def test_flow_revision_migration_upgrade_downgrade_and_checks(monkeypatch):
    migration = _load_migration_module()
    assert migration.revision == "d1e2f3a4b5c6"
    assert migration.down_revision == "9c0d1e2f3a4b"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        flow_id, run_id = _create_legacy_flow_schema(connection)
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert "flow_revisions" in inspector.get_table_names()
        flow_columns = {column["name"]: column for column in inspector.get_columns("flows")}
        run_columns = {column["name"]: column for column in inspector.get_columns("flow_runs")}
        assert {"current_revision", "current_revision_id"}.issubset(flow_columns)
        assert run_columns["revision_id"]["nullable"] is False
        assert {foreign_key["name"] for foreign_key in inspector.get_foreign_keys("flows")} == {
            "fk_flows_current_revision_id_flow_revisions"
        }
        assert {foreign_key["name"] for foreign_key in inspector.get_foreign_keys("flow_runs")} == {
            "fk_flow_runs_revision_id_flow_revisions"
        }
        assert {
            constraint["name"] for constraint in inspector.get_unique_constraints("flow_revisions")
        } == {"uq_flow_revisions_flow_revision_number"}

        revision = (
            connection.execute(
                sa.text(
                    "SELECT id, revision_number, snapshot, snapshot_hash, source "
                    "FROM flow_revisions WHERE flow_id = :flow_id"
                ),
                {"flow_id": flow_id},
            )
            .mappings()
            .one()
        )
        flow_pointer = (
            connection.execute(
                sa.text(
                    "SELECT current_revision, current_revision_id FROM flows WHERE id = :flow_id"
                ),
                {"flow_id": flow_id},
            )
            .mappings()
            .one()
        )
        run_pointer = connection.scalar(
            sa.text("SELECT revision_id FROM flow_runs WHERE id = :run_id"),
            {"run_id": run_id},
        )

        assert revision["revision_number"] == 1
        assert revision["source"] == "migration_backfill"
        assert flow_pointer == {
            "current_revision": 1,
            "current_revision_id": revision["id"],
        }
        assert run_pointer == revision["id"]
        snapshot = json.loads(revision["snapshot"])
        assert snapshot["flow_id"] == flow_id
        assert snapshot["name"] == "Flow anterior à migration"
        assert snapshot["trigger_config"] == {"maxSteps": 12}
        assert len(revision["snapshot_hash"]) == 64

        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                sa.text("UPDATE flow_revisions SET source = 'tampered' WHERE id = :revision_id"),
                {"revision_id": revision["id"]},
            )
        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                sa.text("DELETE FROM flow_revisions WHERE id = :revision_id"),
                {"revision_id": revision["id"]},
            )

        migration.downgrade()

        downgraded = sa.inspect(connection)
        assert "flow_revisions" not in downgraded.get_table_names()
        assert "current_revision" not in {
            column["name"] for column in downgraded.get_columns("flows")
        }
        assert "revision_id" not in {
            column["name"] for column in downgraded.get_columns("flow_runs")
        }


def test_flow_revision_schema_compiles_for_postgresql():
    from app.models.flow import FlowRevision

    ddl = str(sa.schema.CreateTable(FlowRevision.__table__).compile(dialect=postgresql.dialect()))

    assert "UUID" in ddl
    assert "JSONB" in ddl
    assert "uq_flow_revisions_flow_revision_number" in ddl
