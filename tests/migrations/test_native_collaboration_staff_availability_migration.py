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
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "c6e8a0b2d403_add_native_collaboration_and_staff_availability.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "native_collaboration_staff_migration", MIGRATION_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parents(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    workspaces = sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    users = sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "workspace_memberships",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey(workspaces.c.id), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey(users.c.id), nullable=False),
        sa.UniqueConstraint("workspace_id", "user_id"),
    )
    for table_name in (
        "klm_omnichannel_threads",
        "klm_omnichannel_messages",
        "klm_workspace_teams",
    ):
        sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "workspace_id", sa.String(36), sa.ForeignKey(workspaces.c.id), nullable=False
            ),
            sa.UniqueConstraint("workspace_id", "id"),
        )
    metadata.create_all(connection)


def test_native_collaboration_staff_availability_migration(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "c6e8a0b2d403"
    assert migration.down_revision == "b4d6f8a0c201"
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "BEFORE TRUNCATE" in source
    assert "get_indexes" not in source

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _parents(connection)
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert set(migration.TENANT_TABLES) <= set(inspector.get_table_names())
        assert {
            "ck_conv_ref_distinct_messages",
            "ck_conv_ref_payload_hash",
        } <= {
            check["name"]
            for check in inspector.get_check_constraints("klm_conversation_message_references")
        }
        assert "uq_staff_availability_request_idempotency" in {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("staff_availability_change_requests")
        }

        connection.execute(sa.text("INSERT INTO workspaces (id) VALUES ('workspace')"))
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_threads (id, workspace_id) "
                "VALUES ('thread', 'workspace')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_conversation_groups "
                "(id, workspace_id, thread_id, name) "
                "VALUES ('group', 'workspace', 'thread', 'native')"
            )
        )
        with pytest.raises(DBAPIError):
            connection.execute(
                sa.text("UPDATE klm_conversation_groups SET name = 'changed' WHERE id = 'group'")
            )

        migration.downgrade()
        assert not (set(migration.TENANT_TABLES) & set(sa.inspect(connection).get_table_names()))
