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
    / "9d1f3a5c7b09_add_provider_media_upload_receipts.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "provider_media_receipt_migration", MIGRATION_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_previous_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    workspaces = sa.Table("workspaces", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    sa.Table(
        "klm_omnichannel_channel_connections",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    metadata.create_all(connection)


def test_provider_media_receipt_migration_is_tenant_bound_and_immutable(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "9d1f3a5c7b09"
    assert migration.down_revision == "8c0e2f4a6b98"
    assert all(len(name) <= 63 for name in migration.INTENT_INDEX_NAMES.values())
    migration_source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "BEFORE TRUNCATE" in migration_source
    assert "immutable_truncate" in migration_source

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _create_previous_schema(connection)
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        workspace_a, workspace_b = uuid4(), uuid4()
        connection_a, connection_b = uuid4(), uuid4()
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:a), (:b)"),
            {"a": workspace_a.hex, "b": workspace_b.hex},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_channel_connections (id, workspace_id) "
                "VALUES (:ca, :wa), (:cb, :wb)"
            ),
            {
                "ca": connection_a.hex,
                "wa": workspace_a.hex,
                "cb": connection_b.hex,
                "wb": workspace_b.hex,
            },
        )

        migration.upgrade()
        assert migration.TABLE_NAME in sa.inspect(connection).get_table_names()
        assert migration.INTENT_TABLE_NAME in sa.inspect(connection).get_table_names()
        receipt_id = uuid4()
        insert = sa.text(
            f"INSERT INTO {migration.TABLE_NAME} "
            "(id, workspace_id, connection_id, provider, provider_media_id, media_type, "
            "source_sha256, source_size_bytes, source_content_type, "
            "provider_payload_sha256, uploaded_at) "
            "VALUES (:id, :workspace, :connection, 'meta_cloud', :media, 'image', "
            ":source_sha, 1024, 'image/jpeg', :payload_sha, CURRENT_TIMESTAMP)"
        )
        values = {
            "id": receipt_id.hex,
            "workspace": workspace_a.hex,
            "connection": connection_a.hex,
            "media": "meta-media-1",
            "source_sha": "a" * 64,
            "payload_sha": "b" * 64,
        }
        connection.execute(insert, values)
        intent_id = uuid4()
        connection.execute(
            sa.text(
                f"INSERT INTO {migration.INTENT_TABLE_NAME} "
                "(id, workspace_id, connection_id, provider, idempotency_key_hash, "
                "payload_sha256, media_type, source_sha256, source_size_bytes, "
                "source_content_type, source_extension, source_filename_sha256, "
                "state, receipt_id) "
                "VALUES (:id, :workspace, :connection, 'meta_cloud', :key_hash, "
                ":payload_sha, 'image', :source_sha, 1024, 'image/jpeg', '.jpg', "
                ":filename_sha, 'completed', :receipt_id)"
            ),
            {
                "id": intent_id.hex,
                "workspace": workspace_a.hex,
                "connection": connection_a.hex,
                "key_hash": "c" * 64,
                "payload_sha": "d" * 64,
                "source_sha": "a" * 64,
                "filename_sha": "e" * 64,
                "receipt_id": receipt_id.hex,
            },
        )

        with pytest.raises((DBAPIError, OperationalError)):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        f"UPDATE {migration.TABLE_NAME} SET provider_media_id = 'tampered' "
                        "WHERE id = :id"
                    ),
                    {"id": receipt_id.hex},
                )
        with pytest.raises((DBAPIError, OperationalError)):
            with connection.begin_nested():
                connection.execute(
                    sa.text(f"DELETE FROM {migration.TABLE_NAME} WHERE id = :id"),
                    {"id": receipt_id.hex},
                )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    insert,
                    {
                        **values,
                        "id": uuid4().hex,
                        "connection": connection_b.hex,
                        "media": "cross-workspace-media",
                    },
                )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        f"INSERT INTO {migration.INTENT_TABLE_NAME} "
                        "(id, workspace_id, connection_id, provider, "
                        "idempotency_key_hash, payload_sha256, media_type, "
                        "source_sha256, source_size_bytes, source_content_type, "
                        "source_extension, source_filename_sha256) "
                        "VALUES (:id, :workspace, :connection, 'meta_cloud', "
                        ":key_hash, :payload_sha, 'image', :source_sha, 1024, "
                        "'image/jpeg', '.jpg', :filename_sha)"
                    ),
                    {
                        "id": uuid4().hex,
                        "workspace": workspace_a.hex,
                        "connection": connection_b.hex,
                        "key_hash": "f" * 64,
                        "payload_sha": "1" * 64,
                        "source_sha": "2" * 64,
                        "filename_sha": "3" * 64,
                    },
                )
    engine.dispose()
