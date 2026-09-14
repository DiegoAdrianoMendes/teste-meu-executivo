from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import sqlalchemy as sa

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
    / "b8d1e3f5a702_add_dual_access_packages.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("dual_access_packages_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dual_access_packages_migration_round_trip(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "b8d1e3f5a702"
    assert migration.down_revision == "a7c9e1f3b501"

    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    sa.Table(
        "workspace_memberships",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
    )

    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(
            sa.text(
                "INSERT INTO workspace_memberships (id, workspace_id, role) VALUES "
                "('owner', 'workspace', 'workspace_owner'), "
                "('admin', 'workspace', 'workspace_admin'), "
                "('operator', 'workspace', 'operator')"
            )
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()

        inspector = sa.inspect(connection)
        user_columns = {column["name"] for column in inspector.get_columns("users")}
        assert {
            "platform_role_package",
            "platform_access_active",
            "platform_permission_grants",
            "platform_permission_denials",
        } <= user_columns
        membership_columns = {
            column["name"] for column in inspector.get_columns("workspace_memberships")
        }
        assert "access_package" in membership_columns
        assert {
            index["name"] for index in inspector.get_indexes("workspace_memberships")
        } == {"ix_workspace_memberships_access_package"}
        packages = dict(
            connection.execute(
                sa.text("SELECT id, access_package FROM workspace_memberships")
            ).all()
        )
        assert packages == {
            "owner": "office_owner",
            "admin": "office_admin",
            "operator": None,
        }

        migration.downgrade()

        assert {column["name"] for column in sa.inspect(connection).get_columns("users")} == {"id"}
        assert {
            column["name"]
            for column in sa.inspect(connection).get_columns("workspace_memberships")
        } == {"id", "workspace_id", "role"}
