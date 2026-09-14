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
    PROJECT_ROOT / "alembic" / "versions" / "fe6f8a0b2d75_add_native_scheduled_delivery_protocol.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("native_schedule_protocol", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_previous_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    workspaces = sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "klm_omnichannel_threads",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    sa.Table(
        "klm_omnichannel_channel_connections",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    sa.Table(
        "klm_omnichannel_messages",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    sa.Table(
        "klm_omnichannel_scheduled_messages",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("thread_id", sa.String(36), nullable=False),
        sa.Column("connection_id", sa.String(36), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
    )
    metadata.create_all(connection)


def test_native_scheduled_delivery_protocol_round_trip(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "fe6f8a0b2d75"
    assert migration.down_revision == "fd5e7f9a1c64"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _create_previous_schema(connection)
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))

        workspace_a, workspace_b = str(uuid4()), str(uuid4())
        thread_a, thread_b = str(uuid4()), str(uuid4())
        schedule_a, schedule_b = str(uuid4()), str(uuid4())
        message_a, message_b = str(uuid4()), str(uuid4())
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:a), (:b)"),
            {"a": workspace_a, "b": workspace_b},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_threads (id, workspace_id) "
                "VALUES (:ta, :wa), (:tb, :wb)"
            ),
            {"ta": thread_a, "wa": workspace_a, "tb": thread_b, "wb": workspace_b},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_messages (id, workspace_id) "
                "VALUES (:ma, :wa), (:mb, :wb)"
            ),
            {"ma": message_a, "wa": workspace_a, "mb": message_b, "wb": workspace_b},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_scheduled_messages "
                "(id, workspace_id, thread_id, state, scheduled_at) VALUES "
                "(:sa, :wa, :ta, 'scheduled', CURRENT_TIMESTAMP), "
                "(:sb, :wb, :tb, 'scheduled', CURRENT_TIMESTAMP)"
            ),
            {
                "sa": schedule_a,
                "wa": workspace_a,
                "ta": thread_a,
                "sb": schedule_b,
                "wb": workspace_b,
                "tb": thread_b,
            },
        )

        migration.upgrade()
        columns = {
            item["name"]
            for item in sa.inspect(connection).get_columns("klm_omnichannel_scheduled_messages")
        }
        assert {
            "canonical_message_id",
            "author_type",
            "author_label",
            "lease_generation",
            "delivery_attempted_at",
            "delivery_uncertain_at",
        } <= columns
        projection = connection.execute(
            sa.text(
                "SELECT author_type, author_label, lease_generation "
                "FROM klm_omnichannel_scheduled_messages WHERE id = :id"
            ),
            {"id": schedule_a},
        ).one()
        assert tuple(projection) == ("system", "Agendamento KLM", 0)
        index_names = {
            item["name"]
            for table_name in (
                "klm_omnichannel_scheduled_messages",
                "klm_omnichannel_scheduled_message_events",
            )
            for item in sa.inspect(connection).get_indexes(table_name)
        }
        assert index_names
        assert max(map(len, index_names)) <= 63
        assert set(migration.EVENT_INDEXES.values()) <= index_names

        connection.execute(
            sa.text(
                "UPDATE klm_omnichannel_scheduled_messages "
                "SET canonical_message_id = :message WHERE id = :id"
            ),
            {"message": message_a, "id": schedule_a},
        )
        with pytest.raises((IntegrityError, DBAPIError)):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "UPDATE klm_omnichannel_scheduled_messages "
                        "SET canonical_message_id = :message WHERE id = :id"
                    ),
                    {"message": message_b, "id": schedule_a},
                )

        event_id = str(uuid4())
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_scheduled_message_events "
                "(id, workspace_id, scheduled_message_id, thread_id, canonical_message_id, "
                "provider, event_type, previous_state, next_state, idempotency_key, "
                "payload_sha256, occurred_at, event_metadata) VALUES "
                "(:id, :workspace, :schedule, :thread, :message, 'meta_cloud', 'sent', "
                "'processing', 'sent', 'event-1', :hash, CURRENT_TIMESTAMP, '{}')"
            ),
            {
                "id": event_id,
                "workspace": workspace_a,
                "schedule": schedule_a,
                "thread": thread_a,
                "message": message_a,
                "hash": "a" * 64,
            },
        )
        for statement in (
            "UPDATE klm_omnichannel_scheduled_message_events SET id = id WHERE id = :id",
            "DELETE FROM klm_omnichannel_scheduled_message_events WHERE id = :id",
        ):
            with pytest.raises((IntegrityError, DBAPIError)):
                with connection.begin_nested():
                    connection.execute(sa.text(statement), {"id": event_id})

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert "klm_omnichannel_scheduled_message_events" not in inspector.get_table_names()
        assert "canonical_message_id" not in {
            item["name"] for item in inspector.get_columns("klm_omnichannel_scheduled_messages")
        }
