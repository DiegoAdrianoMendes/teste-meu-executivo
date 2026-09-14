import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path


def _load_migration_module():
    migration_path = (
        PROJECT_ROOT / "alembic" / "versions" / "8af3d982fb78_add_growth_crm_prospects.py"
    )
    spec = importlib.util.spec_from_file_location("growth_crm_prospects_migration", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_legacy_schema(connection: sa.Connection) -> dict:
    metadata = sa.MetaData()
    workspaces = sa.Table(
        "workspaces",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
    )
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    workspace_subscriptions = sa.Table(
        "workspace_subscriptions",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("valor_mensal_cents", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(connection)

    now = datetime.now(UTC)
    ws_paying = str(uuid4())
    ws_trial = str(uuid4())

    connection.execute(workspaces.insert().values(id=ws_paying, name="Escritório Pagante"))
    connection.execute(workspaces.insert().values(id=ws_trial, name="Escritório Trial"))
    connection.execute(
        workspace_subscriptions.insert().values(
            id=str(uuid4()),
            workspace_id=ws_paying,
            status="ativa",
            valor_mensal_cents=49700,
            created_at=now,
        )
    )
    connection.execute(
        workspace_subscriptions.insert().values(
            id=str(uuid4()),
            workspace_id=ws_trial,
            status="trial",
            valor_mensal_cents=19700,
            created_at=now,
        )
    )
    return {"ws_paying": ws_paying, "ws_trial": ws_trial}


def test_growth_prospects_upgrade_backfills_and_downgrade_removes(monkeypatch):
    migration = _load_migration_module()
    assert migration.revision == "8af3d982fb78"
    assert migration.down_revision == "c2f8a1d4e6b9"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        ids = _create_legacy_schema(connection)
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert "growth_prospects" in inspector.get_table_names()
        assert "growth_activities" in inspector.get_table_names()
        assert "growth_stage_events" in inspector.get_table_names()

        rows = connection.execute(
            sa.text(
                "SELECT workspace_id, estagio, origem, valor_estimado_cents "
                "FROM growth_prospects"
            )
        ).mappings().all()
        by_workspace = {row["workspace_id"]: row for row in rows}

        paying = by_workspace[ids["ws_paying"]]
        assert paying["estagio"] == "pagante"
        assert paying["origem"] == "backfill"
        assert paying["valor_estimado_cents"] == 49700

        trial = by_workspace[ids["ws_trial"]]
        assert trial["estagio"] == "trial"

        event_count = connection.scalar(sa.text("SELECT COUNT(*) FROM growth_stage_events"))
        assert event_count == 2

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert "growth_prospects" not in inspector.get_table_names()
        assert "growth_activities" not in inspector.get_table_names()
        assert "growth_stage_events" not in inspector.get_table_names()

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert "growth_prospects" in inspector.get_table_names()
        count_after_reupgrade = connection.scalar(
            sa.text("SELECT COUNT(*) FROM growth_prospects")
        )
        assert count_after_reupgrade == 2
