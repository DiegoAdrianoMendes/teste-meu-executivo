from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [
    entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT
]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path

MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "e2f4a6b8c0d3_add_native_support_center.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "native_support_center_migration",
        MIGRATION_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_parent_tables(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("error_logs", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "storage_objects",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    sa.Table(
        "klm_technical_requests",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("assigned_to_user_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("route", sa.String(300), nullable=True),
        sa.Column("severity", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(connection)


def test_native_support_migration_backfills_legacy_request_and_round_trips(
    monkeypatch,
) -> None:
    migration = _load_migration_module()
    assert migration.revision == "e2f4a6b8c0d3"
    assert migration.down_revision == "d1e3f5a7b9c2"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_parent_tables(connection)
        workspace_id = str(uuid4())
        user_id = str(uuid4())
        legacy_id = str(uuid4())
        created_at = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:id)"),
            {"id": workspace_id},
        )
        connection.execute(
            sa.text("INSERT INTO users (id) VALUES (:id)"),
            {"id": user_id},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO klm_technical_requests (
                    id, workspace_id, created_by_user_id, assigned_to_user_id,
                    title, description, route, severity, status, evidence,
                    resolution, created_at, updated_at
                ) VALUES (
                    :id, :workspace_id, :user_id, NULL,
                    :title, :description, :route, 'critical', 'resolved',
                    :evidence, :resolution, :created_at, :updated_at
                )
                """
            ),
            {
                "id": legacy_id,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "title": "Falha critica legada",
                "description": "Registro anterior ao nucleo nativo de suporte.",
                "route": "/app/legado",
                "evidence": json.dumps(
                    [{"requestId": "legacy-request-1", "status": 500}]
                ),
                "resolution": "Patch aplicado.",
                "created_at": created_at,
                "updated_at": created_at,
            },
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {
            "support_tickets",
            "support_ticket_messages",
            "support_ticket_events",
            "support_ticket_attachments",
            "support_ticket_collaborators",
            "support_notification_outbox",
            "support_saved_filters",
        } <= set(inspector.get_table_names())
        ticket = connection.execute(
            sa.text(
                """
                SELECT legacy_technical_request_id, workspace_id, reporter_user_id,
                       ticket_type, priority, severity, queue, status, subject,
                       description, route, technical_context, idempotency_key
                FROM support_tickets
                """
            )
        ).mappings().one()
        assert ticket["legacy_technical_request_id"] == legacy_id
        assert ticket["workspace_id"] == workspace_id
        assert ticket["reporter_user_id"] == user_id
        assert ticket["ticket_type"] == "problem"
        assert ticket["priority"] == "urgent"
        assert ticket["severity"] == "critical"
        assert ticket["queue"] == "technical"
        assert ticket["status"] == "resolved"
        assert ticket["subject"] == "Falha critica legada"
        assert ticket["description"] == "Registro anterior ao nucleo nativo de suporte."
        assert ticket["route"] == "/app/legado"
        assert ticket["idempotency_key"] == f"legacy:{legacy_id}"
        technical_context = (
            json.loads(ticket["technical_context"])
            if isinstance(ticket["technical_context"], str)
            else ticket["technical_context"]
        )
        assert technical_context == {
            "legacyEvidenceCount": 1,
            "migratedFrom": "klm_technical_requests",
        }
        assert "requestId" not in json.dumps(technical_context)

        event = connection.execute(
            sa.text(
                """
                SELECT id, event_type, before_data, after_data
                FROM support_ticket_events
                """
            )
        ).mappings().one()
        assert event["event_type"] == "LEGACY_TECHNICAL_REQUEST_MIGRATED"
        before_data = (
            json.loads(event["before_data"])
            if isinstance(event["before_data"], str)
            else event["before_data"]
        )
        assert before_data["legacyId"] == legacy_id
        assert before_data["resolution"] == "Patch aplicado."

        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                sa.text(
                    "UPDATE support_ticket_events SET event_type = 'changed' WHERE id = :id"
                ),
                {"id": event["id"]},
            )
        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                sa.text("DELETE FROM support_ticket_events WHERE id = :id"),
                {"id": event["id"]},
            )

        migration.downgrade()
        assert {
            "support_tickets",
            "support_ticket_messages",
            "support_ticket_events",
            "support_ticket_attachments",
            "support_ticket_collaborators",
            "support_notification_outbox",
            "support_saved_filters",
        }.isdisjoint(sa.inspect(connection).get_table_names())
        assert connection.execute(
            sa.text("SELECT COUNT(*) FROM klm_technical_requests")
        ).scalar_one() == 1
