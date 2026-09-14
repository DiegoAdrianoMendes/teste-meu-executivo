from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

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
    / "7b9d1f3a5c87_add_omnichannel_auditor.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("omnichannel_auditor", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_previous_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    workspaces = sa.Table("workspaces", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    sa.Table(
        "klm_omnichannel_threads",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
    )
    sa.Table(
        "klm_omnichannel_messages",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
    )
    sa.Table(
        "matters",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
    )
    metadata.create_all(connection)


def test_omnichannel_auditor_migration_round_trip(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "7b9d1f3a5c87"
    assert migration.down_revision == "6a8c0e2f4b76"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _create_previous_schema(connection)
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert migration.TABLE_NAME in inspector.get_table_names()
        columns = {item["name"] for item in inspector.get_columns(migration.TABLE_NAME)}
        assert {
            "dedupe_key",
            "evidence",
            "remediation",
            "internal_note_message_id",
            "retry_schedule_id",
            "version",
        } <= columns

        workspace_a, workspace_b = uuid4(), uuid4()
        thread_a, thread_b = uuid4(), uuid4()
        message_a, message_b = uuid4(), uuid4()
        matter_a, matter_b = uuid4(), uuid4()
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:a), (:b)"),
            {"a": workspace_a.hex, "b": workspace_b.hex},
        )
        for table_name, values in (
            ("klm_omnichannel_threads", (thread_a, thread_b)),
            ("klm_omnichannel_messages", (message_a, message_b)),
            ("matters", (matter_a, matter_b)),
        ):
            connection.execute(
                sa.text(
                    f"INSERT INTO {table_name} (id, workspace_id) "
                    "VALUES (:a, :wa), (:b, :wb)"
                ),
                {
                    "a": values[0].hex,
                    "wa": workspace_a.hex,
                    "b": values[1].hex,
                    "wb": workspace_b.hex,
                },
            )

        payload = {
            "id": uuid4().hex,
            "workspace": workspace_a.hex,
            "thread": thread_a.hex,
            "message": message_a.hex,
            "matter": matter_a.hex,
            "dedupe": "delivery-failed:message-a",
        }
        connection.execute(
            sa.text(
                f"INSERT INTO {migration.TABLE_NAME} "
                "(id, workspace_id, thread_id, message_id, matter_id, finding_type, severity, "
                "state, title, summary, dedupe_key, evidence, remediation) VALUES "
                "(:id, :workspace, :thread, :message, :matter, 'delivery_failed', 'critical', "
                "'open', 'Falha', 'Falha confirmada', :dedupe, '{}', '{}')"
            ),
            payload,
        )
        with pytest.raises((IntegrityError, DBAPIError)):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        f"INSERT INTO {migration.TABLE_NAME} "
                        "(id, workspace_id, thread_id, finding_type, severity, state, title, "
                        "summary, dedupe_key, evidence, remediation) VALUES "
                        "(:id, :workspace, :thread, 'unanswered_lead', 'warning', 'open', "
                        "'Sem resposta', 'Teste', 'cross-tenant', '{}', '{}')"
                    ),
                    {
                        "id": uuid4().hex,
                        "workspace": workspace_a.hex,
                        "thread": thread_b.hex,
                    },
                )
        with pytest.raises((IntegrityError, DBAPIError)):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        f"INSERT INTO {migration.TABLE_NAME} "
                        "(id, workspace_id, thread_id, finding_type, severity, state, title, "
                        "summary, dedupe_key, evidence, remediation) VALUES "
                        "(:id, :workspace, :thread, 'delivery_failed', 'critical', 'open', "
                        "'Falha', 'Duplicada', :dedupe, '{}', '{}')"
                    ),
                    {
                        "id": uuid4().hex,
                        "workspace": workspace_a.hex,
                        "thread": thread_a.hex,
                        "dedupe": payload["dedupe"],
                    },
                )

        migration.downgrade()
        assert migration.TABLE_NAME not in sa.inspect(connection).get_table_names()
