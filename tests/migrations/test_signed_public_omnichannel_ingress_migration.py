from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, OperationalError

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
    / "fc4a6d8e0b53_add_signed_public_omnichannel_ingress.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("signed_ingress_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_previous_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    workspaces = sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "klm_omnichannel_channel_connections",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    metadata.create_all(connection)


def test_signed_ingress_migration_is_tenant_safe_and_receipts_are_immutable(
    monkeypatch,
) -> None:
    migration = _load_migration_module()
    assert migration.revision == "fc4a6d8e0b53"
    assert migration.down_revision == "fb3f5d7b9c42"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _create_previous_schema(connection)
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))

        workspace_a, workspace_b = str(uuid4()), str(uuid4())
        connection_a, connection_b = str(uuid4()), str(uuid4())
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:a), (:b)"),
            {"a": workspace_a, "b": workspace_b},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_channel_connections (id, workspace_id) "
                "VALUES (:a, :wa), (:b, :wb)"
            ),
            {"a": connection_a, "wa": workspace_a, "b": connection_b, "wb": workspace_b},
        )

        migration.upgrade()
        assert set(migration.TABLES) <= set(sa.inspect(connection).get_table_names())

        credential_id = str(uuid4())
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_webhook_credentials "
                "(id, workspace_id, connection_id, signing_secret_encrypted) "
                "VALUES (:id, :workspace, :connection, 'ciphertext')"
            ),
            {
                "id": credential_id,
                "workspace": workspace_a,
                "connection": connection_a,
            },
        )
        connection.execute(
            sa.text(
                "UPDATE klm_omnichannel_webhook_credentials "
                "SET state = 'disabled', version = 2 WHERE id = :id"
            ),
            {"id": credential_id},
        )

        receipt_id = str(uuid4())
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_public_ingress_receipts "
                "(id, workspace_id, connection_id, provider, connection_key, "
                "external_event_id, payload_sha256, signature_sha256, signature_scheme, "
                "request_metadata) VALUES (:id, :workspace, :connection, 'meta_ads', "
                "'meta-main', 'event-1', :payload_hash, :signature_hash, "
                "'meta_x_hub_signature_256', '{}')"
            ),
            {
                "id": receipt_id,
                "workspace": workspace_a,
                "connection": connection_a,
                "payload_hash": "a" * 64,
                "signature_hash": "b" * 64,
            },
        )
        with pytest.raises((IntegrityError, OperationalError)):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "UPDATE klm_omnichannel_public_ingress_receipts "
                        "SET external_event_id = 'mutated' WHERE id = :id"
                    ),
                    {"id": receipt_id},
                )

        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "INSERT INTO klm_omnichannel_public_ingress_receipts "
                        "(id, workspace_id, connection_id, provider, connection_key, "
                        "external_event_id, payload_sha256, signature_sha256, "
                        "signature_scheme, request_metadata) VALUES "
                        "(:id, :workspace, :connection, 'meta_ads', 'cross', 'event-cross', "
                        ":payload_hash, :signature_hash, 'meta_x_hub_signature_256', '{}')"
                    ),
                    {
                        "id": str(uuid4()),
                        "workspace": workspace_b,
                        "connection": connection_a,
                        "payload_hash": "c" * 64,
                        "signature_hash": "d" * 64,
                    },
                )

        migration.downgrade()
        assert not (set(migration.TABLES) & set(sa.inspect(connection).get_table_names()))
