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
        PROJECT_ROOT
        / "alembic"
        / "versions"
        / "b1479f28e98b_add_workspace_subscriptions_command_center.py"
    )
    spec = importlib.util.spec_from_file_location("workspace_subscriptions_migration", migration_path)
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
    plans = sa.Table(
        "plans",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tier", sa.String(30), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
    )
    subscriptions = sa.Table(
        "subscriptions",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("plan_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("interval", sa.String(20), nullable=False),
        sa.Column("trial_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
    )
    metadata.create_all(connection)

    now = datetime.now(UTC)

    ws_with_stripe = str(uuid4())
    ws_without_stripe = str(uuid4())
    plan_id = str(uuid4())
    sub_id = str(uuid4())

    connection.execute(workspaces.insert().values(id=ws_with_stripe, name="Escritório Mapeado"))
    connection.execute(workspaces.insert().values(id=ws_without_stripe, name="Escritório Novo"))
    connection.execute(plans.insert().values(id=plan_id, tier="pro", name="Pro"))
    connection.execute(
        subscriptions.insert().values(
            id=sub_id,
            workspace_id=ws_with_stripe,
            plan_id=plan_id,
            status="active",
            amount_cents=49700,
            interval="monthly",
            trial_end=None,
            current_period_start=now,
            current_period_end=now,
        )
    )
    return {"ws_with_stripe": ws_with_stripe, "ws_without_stripe": ws_without_stripe}


def test_workspace_subscriptions_upgrade_backfills_and_downgrade_removes(monkeypatch):
    migration = _load_migration_module()
    assert migration.revision == "b1479f28e98b"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        ids = _create_legacy_schema(connection)
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert "workspace_subscriptions" in inspector.get_table_names()
        assert "subscription_events" in inspector.get_table_names()

        rows = connection.execute(
            sa.text(
                "SELECT workspace_id, plano, status, valor_mensal_cents, origem "
                "FROM workspace_subscriptions"
            )
        ).mappings().all()
        by_workspace = {row["workspace_id"]: row for row in rows}

        mapped = by_workspace[ids["ws_with_stripe"]]
        assert mapped["plano"] == "escritorio"
        assert mapped["status"] == "ativa"
        assert mapped["valor_mensal_cents"] == 49700
        assert mapped["origem"] == "backfill_stripe_mirror"

        default = by_workspace[ids["ws_without_stripe"]]
        assert default["plano"] == "essencial"
        assert default["status"] == "trial"
        assert default["origem"] == "backfill_default_trial"

        event_count = connection.scalar(sa.text("SELECT COUNT(*) FROM subscription_events"))
        assert event_count == 2

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert "workspace_subscriptions" not in inspector.get_table_names()
        assert "subscription_events" not in inspector.get_table_names()

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert "workspace_subscriptions" in inspector.get_table_names()
        count_after_reupgrade = connection.scalar(
            sa.text("SELECT COUNT(*) FROM workspace_subscriptions")
        )
        assert count_after_reupgrade == 2
