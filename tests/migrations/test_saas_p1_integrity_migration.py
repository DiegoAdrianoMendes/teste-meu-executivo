from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
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
    / "a9d2e4f6b508_enforce_saas_p1_integrity.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("saas_p1_integrity_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_entitlement_check() -> str:
    values = (
        "tokens",
        "ai_executions",
        "ai_conversations",
        "storage_bytes",
        "files",
        "users",
        "whatsapp_numbers",
        "agents",
        "automations",
    )
    return f"entitlement IN ({', '.join(repr(value) for value in values)})"


def _create_legacy_schema(connection: sa.Connection) -> None:
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
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("cpf", sa.String(14), nullable=True),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
    )
    sa.Table(
        "matters",
        metadata,
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="RESTRICT"),
    )
    sa.Table(
        "storage_objects",
        metadata,
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("matter_id", sa.String(36), nullable=True),
        sa.Column("client_id", sa.String(36), nullable=True),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="RESTRICT"),
    )
    plans = sa.Table(
        "plans",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    plan_entitlements = sa.Table(
        "plan_entitlements",
        metadata,
        sa.Column("plan_id", sa.String(36), nullable=False),
        sa.Column("entitlement", sa.String(32), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.CheckConstraint(_legacy_entitlement_check(), name="entitlement_key_enum"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
    )
    sa.Table(
        "workspace_monthly_entitlement_usage",
        metadata,
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("entitlement", sa.String(32), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.CheckConstraint(_legacy_entitlement_check(), name="entitlement_key_enum"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
    )
    sa.Table(
        "entitlement_usage_reservations",
        metadata,
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("usage_id", sa.String(36), nullable=False),
        sa.Column("entitlement", sa.String(32), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.CheckConstraint(_legacy_entitlement_check(), name="entitlement_key_enum"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["usage_id"],
            ["workspace_monthly_entitlement_usage.id"],
            ondelete="CASCADE",
        ),
    )
    metadata.create_all(connection)
    connection.execute(workspaces.insert(), ({"id": "workspace-a"}, {"id": "workspace-b"}))
    connection.execute(users.insert(), {"id": "user-a"})
    connection.execute(
        clients.insert(),
        (
            {"workspace_id": "workspace-a", "id": "client-a"},
            {"workspace_id": "workspace-b", "id": "client-b"},
        ),
    )
    connection.execute(plans.insert(), {"id": "plan-a"})
    connection.execute(
        plan_entitlements.insert(),
        {"plan_id": "plan-a", "entitlement": "tokens", "id": "entitlement-a"},
    )


def _install_migration(connection: sa.Connection):
    migration = _load_migration_module()
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def _constraint_names(inspector: sa.Inspector, table_name: str) -> set[str | None]:
    return {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(table_name)
    }


def _foreign_key_columns(
    inspector: sa.Inspector,
    table_name: str,
) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        (
            tuple(foreign_key["constrained_columns"]),
            foreign_key["referred_table"],
            tuple(foreign_key["referred_columns"]),
        )
        for foreign_key in inspector.get_foreign_keys(table_name)
    }


def test_saas_p1_integrity_migration_rejects_legacy_cross_tenant_rows() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_legacy_schema(connection)
        connection.execute(
            sa.text(
                "INSERT INTO matters (workspace_id, client_id, id) "
                "VALUES ('workspace-a', 'client-b', 'matter-cross')"
            )
        )
        migration = _install_migration(connection)

        with pytest.raises(RuntimeError, match="matters.client_id=1"):
            migration.upgrade()

        assert "uq_clients_workspace_id" not in _constraint_names(
            sa.inspect(connection),
            "clients",
        )
    engine.dispose()


def test_saas_p1_integrity_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_legacy_schema(connection)
        connection.execute(
            sa.text(
                "INSERT INTO matters (workspace_id, client_id, id) "
                "VALUES ('workspace-a', 'client-a', 'matter-a')"
            )
        )
        migration = _install_migration(connection)
        assert migration.revision == "a9d2e4f6b508"
        assert migration.down_revision == "f8c1d2e3a407"

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert "uq_clients_workspace_id" in _constraint_names(inspector, "clients")
        assert "uq_matters_workspace_id" in _constraint_names(inspector, "matters")
        assert (
            ("workspace_id", "client_id"),
            "clients",
            ("workspace_id", "id"),
        ) in _foreign_key_columns(inspector, "matters")
        storage_foreign_keys = _foreign_key_columns(inspector, "storage_objects")
        assert (
            ("workspace_id", "matter_id"),
            "matters",
            ("workspace_id", "id"),
        ) in storage_foreign_keys
        assert (
            ("workspace_id", "client_id"),
            "clients",
            ("workspace_id", "id"),
        ) in storage_foreign_keys
        for table_name in migration.ENTITLEMENT_TABLES:
            sqltext = " ".join(
                constraint["sqltext"]
                for constraint in inspector.get_check_constraints(table_name)
                if constraint["name"] == "entitlement_key_enum"
            )
            assert "audio_minutes" in sqltext
            assert "digital_signature" in sqltext
            assert "success_manager" in sqltext

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert "uq_clients_workspace_id" not in _constraint_names(inspector, "clients")
        assert "uq_matters_workspace_id" not in _constraint_names(inspector, "matters")
        assert (
            ("client_id",),
            "clients",
            ("id",),
        ) in _foreign_key_columns(inspector, "matters")
        for table_name in migration.ENTITLEMENT_TABLES:
            sqltext = " ".join(
                constraint["sqltext"]
                for constraint in inspector.get_check_constraints(table_name)
                if constraint["name"] == "entitlement_key_enum"
            )
            assert "audio_minutes" not in sqltext
            assert "tokens" in sqltext
    engine.dispose()


def test_saas_p1_integrity_downgrade_fails_closed_for_new_entitlement_values() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_legacy_schema(connection)
        migration = _install_migration(connection)
        migration.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO plan_entitlements (plan_id, entitlement, id) "
                "VALUES ('plan-a', 'calendar', 'entitlement-calendar')"
            )
        )

        with pytest.raises(RuntimeError, match="plan_entitlements=1"):
            migration.downgrade()

        sqltext = " ".join(
            constraint["sqltext"]
            for constraint in sa.inspect(connection).get_check_constraints(
                "plan_entitlements"
            )
            if constraint["name"] == "entitlement_key_enum"
        )
        assert "calendar" in sqltext
    engine.dispose()
