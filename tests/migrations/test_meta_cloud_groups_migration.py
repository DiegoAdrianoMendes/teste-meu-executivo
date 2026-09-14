from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path

MIGRATION_PATH = (
    PROJECT_ROOT / "alembic" / "versions" / "d7f9b1c3e504_add_meta_cloud_groups_domain.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "meta_cloud_groups_migration",
        MIGRATION_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parents(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    workspaces = sa.Table(
        "workspaces",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    sa.Table(
        "klm_omnichannel_channel_connections",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey(workspaces.c.id),
            nullable=False,
        ),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    metadata.create_all(connection)


def test_meta_cloud_groups_migration_enforces_tenant_limits_and_append_only(
    monkeypatch,
) -> None:
    migration = _load_migration_module()
    assert migration.revision == "d7f9b1c3e504"
    assert migration.down_revision == "c6e8a0b2d403"
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "Create Date: 2026-07-21" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "BEFORE TRUNCATE" in source
    assert "external_dispatch_executed = false" in source

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _parents(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert set(migration.TENANT_TABLES) <= set(inspector.get_table_names())
        operation_columns = {
            column["name"]: column for column in inspector.get_columns("klm_meta_group_operations")
        }
        assert operation_columns["group_id"]["nullable"] is True
        assert {
            "ck_klm_meta_group_capability_official_limits",
            "ck_klm_meta_group_capability_external_evidence",
        } <= {
            check["name"]
            for check in inspector.get_check_constraints("klm_meta_group_capabilities")
        }
        assert {
            "ck_klm_meta_group_operation_no_dispatch",
            "ck_klm_meta_group_operation_type",
        } <= {
            check["name"] for check in inspector.get_check_constraints("klm_meta_group_operations")
        }
        assert "ck_klm_meta_group_official_provider_id" in {
            check["name"] for check in inspector.get_check_constraints("klm_meta_groups")
        }

        connection.execute(sa.text("INSERT INTO workspaces (id) VALUES ('workspace')"))
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_channel_connections (id, workspace_id) "
                "VALUES ('connection', 'workspace')"
            )
        )
        with pytest.raises(DBAPIError):
            connection.execute(
                sa.text(
                    """
                    INSERT INTO klm_meta_group_capabilities (
                        id, workspace_id, connection_id, phone_number_id,
                        developer_app_configured, webhook_callback_verified,
                        developer_app_state, required_webhook_fields,
                        subscribed_webhook_fields, oba_eligibility_state,
                        oba_evidence_source, product_mode, max_participants,
                        max_groups_per_business_number, invite_only,
                        evidence_metadata, version
                    ) VALUES (
                        'capability-invalid', 'workspace', 'connection', 'phone',
                        1, 1, 'ready', '[]', '[]', 'eligible', 'unverified',
                        'cloud_api', 9, 10000, 1, '{}', 1
                    )
                    """
                )
            )

        connection.execute(
            sa.text(
                """
                INSERT INTO klm_meta_groups (
                    id, workspace_id, connection_id, group_kind, subject,
                    join_approval_mode, state, participant_count,
                    create_idempotency_key, request_payload_sha256,
                    version, group_metadata
                ) VALUES (
                    'group', 'workspace', 'connection', 'local_internal',
                    'Local group', 'auto_approve', 'local_ready', 1,
                    'group-key', :payload_hash, 1, '{}'
                )
                """
            ),
            {"payload_hash": "a" * 64},
        )
        with pytest.raises(DBAPIError):
            connection.execute(
                sa.text(
                    """
                    INSERT INTO klm_meta_group_operations (
                        id, workspace_id, group_id, connection_id, operation_type,
                        state, idempotency_key, request_payload_sha256,
                        request_payload, capability_snapshot,
                        external_dispatch_executed
                    ) VALUES (
                        'operation-invalid', 'workspace', 'group', 'connection',
                        'prepare_group_message', 'local_ready', 'operation-invalid',
                        :payload_hash, '{}', '{}', 1
                    )
                    """
                ),
                {"payload_hash": "b" * 64},
            )
        connection.execute(
            sa.text(
                """
                INSERT INTO klm_meta_group_operations (
                    id, workspace_id, group_id, connection_id, operation_type,
                    state, idempotency_key, request_payload_sha256,
                    request_payload, capability_snapshot,
                    external_dispatch_executed
                ) VALUES (
                    'operation', 'workspace', 'group', 'connection',
                    'prepare_group_message', 'local_ready', 'operation-valid',
                    :payload_hash, '{}', '{}', 0
                )
                """
            ),
            {"payload_hash": "c" * 64},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO klm_meta_group_lifecycle_events (
                    id, workspace_id, group_id, operation_id, connection_id,
                    source, webhook_field, event_type, external_event_id,
                    idempotency_key, payload_sha256, event_payload,
                    state_snapshot
                ) VALUES (
                    'event', 'workspace', 'group', 'operation', 'connection',
                    'provider_webhook', 'group_status_update', 'group_active',
                    'provider-event', 'event-key', :payload_hash, '{}', '{}'
                )
                """
            ),
            {"payload_hash": "d" * 64},
        )
        with pytest.raises(DBAPIError):
            connection.execute(
                sa.text(
                    "UPDATE klm_meta_group_lifecycle_events "
                    "SET event_type = 'tampered' WHERE id = 'event'"
                )
            )
        with pytest.raises(DBAPIError):
            connection.execute(
                sa.text("DELETE FROM klm_meta_group_lifecycle_events WHERE id = 'event'")
            )

        migration.downgrade()
        assert not (set(migration.TENANT_TABLES) & set(sa.inspect(connection).get_table_names()))
