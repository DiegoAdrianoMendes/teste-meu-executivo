from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

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
    / "b4d6f8a0c201_add_message_evidence_chain.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("message_evidence_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tenant_table(metadata: sa.MetaData, name: str, workspaces: sa.Table) -> sa.Table:
    return sa.Table(
        name,
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
        sa.UniqueConstraint("workspace_id", "id"),
    )


def _create_previous_schema(connection: sa.Connection) -> dict[str, sa.Table]:
    metadata = sa.MetaData()
    workspaces = sa.Table(
        "workspaces",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    tables = {"workspaces": workspaces}
    for table_name in (
        "klm_omnichannel_threads",
        "klm_omnichannel_channel_events",
        "klm_omnichannel_channel_connections",
        "storage_objects",
    ):
        tables[table_name] = _tenant_table(metadata, table_name, workspaces)
    tables["klm_omnichannel_messages"] = sa.Table(
        "klm_omnichannel_messages",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    tables["whatsapp_messages"] = sa.Table(
        "whatsapp_messages",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    metadata.create_all(connection)
    return tables


def _insert_tenant_record(
    connection: sa.Connection,
    table_name: str,
    *,
    record_id: str,
    workspace_id: str,
) -> None:
    connection.execute(
        sa.text(f"INSERT INTO {table_name} (id, workspace_id) VALUES (:id, :workspace)"),
        {"id": record_id, "workspace": workspace_id},
    )


def test_message_evidence_migration_is_immutable_and_tenant_bound(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "b4d6f8a0c201"
    assert migration.down_revision == "a2c4e6f8b10c"
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "BEFORE TRUNCATE" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "current_workspace_id" in source
    assert 'ondelete="RESTRICT"' in source

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _create_previous_schema(connection)
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))

        workspace_a, workspace_b = str(uuid4()), str(uuid4())
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:a), (:b)"),
            {"a": workspace_a, "b": workspace_b},
        )
        tenant_ids: dict[str, tuple[str, str]] = {}
        for table_name in (
            "klm_omnichannel_threads",
            "klm_omnichannel_messages",
            "klm_omnichannel_channel_events",
            "klm_omnichannel_channel_connections",
            "storage_objects",
        ):
            record_a, record_b = str(uuid4()), str(uuid4())
            _insert_tenant_record(
                connection,
                table_name,
                record_id=record_a,
                workspace_id=workspace_a,
            )
            _insert_tenant_record(
                connection,
                table_name,
                record_id=record_b,
                workspace_id=workspace_b,
            )
            tenant_ids[table_name] = (record_a, record_b)
        legacy_message_id = str(uuid4())
        connection.execute(
            sa.text("INSERT INTO whatsapp_messages (id) VALUES (:id)"),
            {"id": legacy_message_id},
        )

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert migration.ENVELOPE_TABLE in inspector.get_table_names()
        assert migration.EVIDENCE_TABLE in inspector.get_table_names()
        message_columns = {
            column["name"]
            for column in inspector.get_columns("klm_omnichannel_messages")
        }
        assert {
            "content_lifecycle_state",
            "evidence_event_count",
            "evidence_revision_count",
            "evidence_chain_sha256",
            "content_edited_at",
            "content_deleted_at",
        } <= message_columns

        envelope_id = str(uuid4())
        connection.execute(
            sa.text(
                f"INSERT INTO {migration.ENVELOPE_TABLE} "
                "(id, provider, payload_sha256, raw_payload, byte_size, signature_header, "
                "signature_sha256, signature_scheme, request_metadata) VALUES "
                "(:id, 'meta_cloud', :payload_hash, :payload, 2, 'sha256=test', "
                ":signature_hash, 'x-hub-signature-256', '{}')"
            ),
            {
                "id": envelope_id,
                "payload_hash": "a" * 64,
                "payload": b"{}",
                "signature_hash": "b" * 64,
            },
        )
        event_id = str(uuid4())
        chain_id = str(uuid4())
        connection.execute(
            sa.text(
                f"INSERT INTO {migration.EVIDENCE_TABLE} "
                "(id, workspace_id, chain_id, revision, content_revision, message_id, "
                "thread_id, channel_event_id, connection_id, legacy_whatsapp_message_id, "
                "envelope_id, storage_object_id, provider, connection_key, "
                "external_message_id, event_type, observation_state, structured_content, "
                "event_payload, event_payload_sha256, evidence_sha256, idempotency_key) "
                "VALUES (:id, :workspace, :chain, 1, 1, :message, :thread, :event, "
                ":connection, :legacy, :envelope, :storage, 'meta_cloud', 'wa:main', "
                "'wamid.1', 'captured', 'observed', '{}', '{}', :payload_hash, "
                ":evidence_hash, 'evidence-event-1')"
            ),
            {
                "id": event_id,
                "workspace": workspace_a,
                "chain": chain_id,
                "message": tenant_ids["klm_omnichannel_messages"][0],
                "thread": tenant_ids["klm_omnichannel_threads"][0],
                "event": tenant_ids["klm_omnichannel_channel_events"][0],
                "connection": tenant_ids["klm_omnichannel_channel_connections"][0],
                "legacy": legacy_message_id,
                "envelope": envelope_id,
                "storage": tenant_ids["storage_objects"][0],
                "payload_hash": "c" * 64,
                "evidence_hash": "d" * 64,
            },
        )

        for statement in (
            f"UPDATE {migration.EVIDENCE_TABLE} SET body_snapshot = 'tampered' WHERE id = :id",
            f"DELETE FROM {migration.EVIDENCE_TABLE} WHERE id = :id",
            f"UPDATE {migration.ENVELOPE_TABLE} SET byte_size = 3 WHERE id = :id",
            f"DELETE FROM {migration.ENVELOPE_TABLE} WHERE id = :id",
        ):
            target_id = event_id if "evidence" in statement else envelope_id
            with (
                pytest.raises((DBAPIError, OperationalError)),
                connection.begin_nested(),
            ):
                connection.execute(sa.text(statement), {"id": target_id})

        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                sa.text(
                    f"INSERT INTO {migration.EVIDENCE_TABLE} "
                    "(id, workspace_id, chain_id, revision, content_revision, message_id, "
                    "provider, connection_key, external_message_id, event_type, "
                    "observation_state, structured_content, event_payload, "
                    "event_payload_sha256, evidence_sha256, idempotency_key) VALUES "
                    "(:id, :workspace, :chain, 1, 1, :message, 'meta_cloud', "
                    "'wa:cross', 'wamid.cross', 'captured', 'observed', '{}', '{}', "
                    ":payload_hash, :evidence_hash, 'evidence-cross-tenant')"
                ),
                {
                    "id": str(uuid4()),
                    "workspace": workspace_a,
                    "chain": str(uuid4()),
                    "message": tenant_ids["klm_omnichannel_messages"][1],
                    "payload_hash": "e" * 64,
                    "evidence_hash": "f" * 64,
                },
            )

        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                sa.text("DELETE FROM workspaces WHERE id = :workspace"),
                {"workspace": workspace_a},
            )

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert migration.ENVELOPE_TABLE not in inspector.get_table_names()
        assert migration.EVIDENCE_TABLE not in inspector.get_table_names()
        assert "content_lifecycle_state" not in {
            column["name"]
            for column in inspector.get_columns("klm_omnichannel_messages")
        }
    engine.dispose()
