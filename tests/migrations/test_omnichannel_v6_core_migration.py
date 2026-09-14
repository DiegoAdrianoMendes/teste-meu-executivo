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

MIGRATION_PATH = PROJECT_ROOT / "alembic" / "versions" / "f8c0e2a4b619_add_omnichannel_v6_core.py"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("omnichannel_v6_core_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_parent_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    workspaces = sa.Table(
        "workspaces",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    clients = sa.Table(
        "clients",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    matters = sa.Table(
        "matters",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    memberships = sa.Table(
        "workspace_memberships",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("id", "workspace_id", "user_id"),
    )
    threads = sa.Table(
        "klm_omnichannel_threads",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("matter_id", sa.String(36), nullable=True),
        sa.Column("client_id", sa.String(36), nullable=True),
        sa.Column("assigned_human_id", sa.String(36), nullable=True),
        sa.Column("primary_channel", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("subject", sa.String(220), nullable=True),
        sa.Column("object_name", sa.String(300), nullable=True),
        sa.Column("owner_agent_key", sa.String(100), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_message_preview", sa.String(500), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("thread_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    messages = sa.Table(
        "klm_omnichannel_messages",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("thread_id", sa.String(36), nullable=False),
        sa.Column("matter_id", sa.String(36), nullable=True),
        sa.Column("author_user_id", sa.String(36), nullable=True),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("direction", sa.String(40), nullable=False),
        sa.Column("author_type", sa.String(40), nullable=False),
        sa.Column("author_label", sa.String(180), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("external_message_id", sa.String(255), nullable=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("delivery_status", sa.String(40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_metadata", sa.JSON(), nullable=False),
    )
    for table in (clients, matters, memberships, threads, messages):
        table.append_constraint(
            sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id], ondelete="CASCADE")
        )
    memberships.append_constraint(sa.ForeignKeyConstraint(["user_id"], [users.c.id]))
    metadata.create_all(connection)


def test_omnichannel_v6_migration_round_trip_and_tenant_constraints(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "f8c0e2a4b619"
    assert migration.down_revision == "f6b8d0e2a417"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _create_parent_schema(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert set(migration.NEW_TABLES) <= set(inspector.get_table_names())
        assert {
            "identity_id",
            "assigned_team_id",
            "queue",
            "version",
            "policy_state",
            "cost_state",
        } <= {column["name"] for column in inspector.get_columns("klm_omnichannel_threads")}
        assert {"endpoint_id", "binding_id", "transport", "template_id"} <= {
            column["name"] for column in inspector.get_columns("klm_omnichannel_messages")
        }

        workspace_a = str(uuid4())
        workspace_b = str(uuid4())
        identity_id = str(uuid4())
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:a), (:b)"),
            {"a": workspace_a, "b": workspace_b},
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_identities "
                "(id, workspace_id, display_name, identity_state, source, merge_version, "
                "identity_metadata, created_at, updated_at) VALUES "
                "(:id, :workspace, 'Lead A', 'verified', 'test', 1, '{}', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": identity_id, "workspace": workspace_a},
        )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "INSERT INTO klm_omnichannel_endpoints "
                        "(id, workspace_id, identity_id, channel, provider, connection_key, "
                        "external_user_id, consent_state, capabilities, endpoint_metadata, "
                        "created_at, updated_at) VALUES "
                        "(:id, :workspace, :identity, 'whatsapp', 'meta', 'official-1', "
                        "'external-1', 'granted', '{}', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {
                        "id": str(uuid4()),
                        "workspace": workspace_b,
                        "identity": identity_id,
                    },
                )

        migration.downgrade()
        downgraded = sa.inspect(connection)
        assert not (set(migration.NEW_TABLES) & set(downgraded.get_table_names()))
        assert "identity_id" not in {
            column["name"] for column in downgraded.get_columns("klm_omnichannel_threads")
        }
        assert "endpoint_id" not in {
            column["name"] for column in downgraded.get_columns("klm_omnichannel_messages")
        }

        migration.upgrade()
        assert set(migration.NEW_TABLES) <= set(sa.inspect(connection).get_table_names())
