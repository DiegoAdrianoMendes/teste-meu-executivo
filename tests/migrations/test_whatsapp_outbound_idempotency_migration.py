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

MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "a7c9e1f3b501_add_whatsapp_outbound_idempotency.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "whatsapp_outbound_idempotency_migration",
        MIGRATION_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_whatsapp_outbound_migration_upgrade_uniqueness_and_downgrade(
    monkeypatch,
) -> None:
    migration = _load_migration_module()
    assert migration.revision == "a7c9e1f3b501"
    assert migration.down_revision == "f4a6c8e0b209"

    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "whatsapp_messages",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
    )
    sa.Table(
        "meta_social_messages",
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
        columns = {column["name"] for column in inspector.get_columns("whatsapp_messages")}
        assert {
            "outbound_idempotency_key",
            "outbound_payload_digest",
            "outbound_request_started_at",
            "delivery_uncertain_at",
            "outbound_error",
        } <= columns
        indexes = {index["name"]: index for index in inspector.get_indexes("whatsapp_messages")}
        assert indexes["ux_whatsapp_messages_outbound_idempotency_key"]["unique"] == 1
        assert "ix_whatsapp_messages_delivery_reconciliation" in indexes

        key = "a" * 64
        insert = sa.text(
            "INSERT INTO whatsapp_messages "
            "(id, status, outbound_idempotency_key) "
            "VALUES (:id, :status, :key)"
        )
        connection.execute(
            insert,
            {
                "id": str(uuid4()),
                "status": "delivery_uncertain",
                "key": key,
            },
        )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    insert,
                    {"id": str(uuid4()), "status": "pending", "key": key},
                )

        migration.downgrade()

        assert connection.scalar(sa.text("SELECT status FROM whatsapp_messages")) == "failed"
        downgraded_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("whatsapp_messages")
        }
        assert downgraded_columns == {"id", "status"}
