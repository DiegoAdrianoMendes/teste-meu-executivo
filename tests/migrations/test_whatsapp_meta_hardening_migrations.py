from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path

CONNECTION_TIERS_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "a4c6e8f0b215_add_whatsapp_connection_tiers.py"
)
HARDENING_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "b5d7f9a1c326_add_whatsapp_meta_hardening_fields.py"
)


def _load_migration_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_connection_tiers_migration_revision_chain() -> None:
    migration = _load_migration_module(
        CONNECTION_TIERS_MIGRATION_PATH, "wa_connection_tiers_migration"
    )
    assert migration.revision == "a4c6e8f0b215"
    assert migration.down_revision == "0b7c9e1a2d34"


def test_hardening_migration_revision_chain() -> None:
    migration = _load_migration_module(
        HARDENING_MIGRATION_PATH, "wa_meta_hardening_migration"
    )
    assert migration.revision == "b5d7f9a1c326"
    assert migration.down_revision == "a4c6e8f0b215"


def test_connection_tiers_migration_upgrade_shape_defaults_and_downgrade(
    monkeypatch,
) -> None:
    migration = _load_migration_module(
        CONNECTION_TIERS_MIGRATION_PATH, "wa_connection_tiers_migration_run"
    )

    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "workspaces",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    sa.Table(
        "whatsapp_connections",
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
        columns = {
            column["name"]: column
            for column in inspector.get_columns("whatsapp_connection_tiers")
        }
        assert {
            "id",
            "workspace_id",
            "connection_id",
            "tier",
            "daily_unique_recipients",
            "window_started_at",
            "ramp_up_stage",
            "quality_rating",
            "last_synced_at",
            "created_at",
            "updated_at",
        } <= set(columns)

        indexes = {
            index["name"]: index
            for index in inspector.get_indexes("whatsapp_connection_tiers")
        }
        assert "ix_whatsapp_connection_tiers_workspace" in indexes
        assert "ix_whatsapp_connection_tiers_connection_id" in indexes

        workspace_id = str(uuid4())
        connection_id = str(uuid4())
        connection.execute(
            sa.text(
                "INSERT INTO workspaces (id) VALUES (:id)"
            ),
            {"id": workspace_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO whatsapp_connections (id) VALUES (:id)"
            ),
            {"id": connection_id},
        )

        # default tier / counters / window are applied without the caller
        # having to specify them explicitly.
        connection.execute(
            sa.text(
                "INSERT INTO whatsapp_connection_tiers "
                "(id, workspace_id, connection_id) VALUES (:id, :workspace_id, :connection_id)"
            ),
            {"id": str(uuid4()), "workspace_id": workspace_id, "connection_id": connection_id},
        )
        row = connection.execute(
            sa.text(
                "SELECT tier, daily_unique_recipients, ramp_up_stage, quality_rating "
                "FROM whatsapp_connection_tiers WHERE connection_id = :connection_id"
            ),
            {"connection_id": connection_id},
        ).one()
        assert row.tier == "tier_250"
        assert row.daily_unique_recipients == 0
        assert row.ramp_up_stage == 0
        assert row.quality_rating is None

        # uq_whatsapp_connection_tiers_connection: one tier row per connection.
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "INSERT INTO whatsapp_connection_tiers "
                        "(id, workspace_id, connection_id) "
                        "VALUES (:id, :workspace_id, :connection_id)"
                    ),
                    {
                        "id": str(uuid4()),
                        "workspace_id": workspace_id,
                        "connection_id": connection_id,
                    },
                )

        migration.downgrade()

        remaining_tables = sa.inspect(connection).get_table_names()
        assert "whatsapp_connection_tiers" not in remaining_tables


def test_hardening_migration_upgrade_columns_and_downgrade(monkeypatch) -> None:
    migration = _load_migration_module(
        HARDENING_MIGRATION_PATH, "wa_meta_hardening_migration_run"
    )

    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "whatsapp_campaigns",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    sa.Table(
        "whatsapp_connections",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    sa.Table(
        "whatsapp_campaign_recipients",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
    )
    sa.Table(
        "whatsapp_campaign_send_jobs",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
    )
    sa.Table(
        "whatsapp_campaign_delivery_events",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
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
        campaign_columns = {
            column["name"]: column
            for column in inspector.get_columns("whatsapp_campaigns")
        }
        assert "pause_reason" in campaign_columns
        assert campaign_columns["pause_reason"]["nullable"] is True
        assert "paused_by_policy" in campaign_columns
        assert campaign_columns["paused_by_policy"]["nullable"] is False

        connection_columns = {
            column["name"]
            for column in inspector.get_columns("whatsapp_connections")
        }
        assert {"app_secret_encrypted", "webhook_verify_token_encrypted"} <= connection_columns

        campaign_id = str(uuid4())
        connection.execute(
            sa.text("INSERT INTO whatsapp_campaigns (id) VALUES (:id)"),
            {"id": campaign_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO whatsapp_campaigns (id, pause_reason) "
                "VALUES (:id, :pause_reason)"
            ),
            {"id": str(uuid4()), "pause_reason": "quality_rating vermelho"},
        )
        row = connection.execute(
            sa.text(
                "SELECT paused_by_policy FROM whatsapp_campaigns WHERE id = :id"
            ),
            {"id": campaign_id},
        ).one()
        # server_default=false(): callers that omit the column still get a
        # deterministic, non-null default.
        assert row.paused_by_policy in (0, False)

        # delivery_uncertain is accepted end to end on every campaign status
        # column that shares the CampaignRecipientStatus / CampaignSendJobStatus
        # Python enum, mirroring a7c9e1f3b501_add_whatsapp_outbound_idempotency.
        for table_name in (
            "whatsapp_campaign_recipients",
            "whatsapp_campaign_send_jobs",
            "whatsapp_campaign_delivery_events",
        ):
            connection.execute(
                sa.text(
                    f"INSERT INTO {table_name} (id, status) VALUES (:id, :status)"
                ),
                {"id": str(uuid4()), "status": "delivery_uncertain"},
            )
            value = connection.execute(
                sa.text(
                    f"SELECT status FROM {table_name} WHERE status = 'delivery_uncertain'"
                )
            ).scalar_one()
            assert value == "delivery_uncertain"

        migration.downgrade()

        downgraded_campaign_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("whatsapp_campaigns")
        }
        assert "pause_reason" not in downgraded_campaign_columns
        assert "paused_by_policy" not in downgraded_campaign_columns

        downgraded_connection_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("whatsapp_connections")
        }
        assert "app_secret_encrypted" not in downgraded_connection_columns
        assert "webhook_verify_token_encrypted" not in downgraded_connection_columns
