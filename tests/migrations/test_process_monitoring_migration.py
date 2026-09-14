from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.process_monitoring import MonitoredProcess, ProcessPublication

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path

MIGRATION_PATH = (
    PROJECT_ROOT / "alembic" / "versions" / "b6d8f0a2c415_add_process_monitoring_module.py"
)

LEGACY_ENTITLEMENTS = (
    "tokens",
    "ai_executions",
    "ai_conversations",
    "audio_minutes",
    "storage_bytes",
    "files",
    "users",
    "whatsapp_numbers",
    "agents",
    "automations",
    "digital_signature",
    "calendar",
    "email",
    "webhooks",
    "advanced_audit",
    "success_manager",
    "legal_areas",
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("process_monitoring_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_expression(values: tuple[str, ...]) -> str:
    return f"entitlement IN ({', '.join(repr(value) for value in values)})"


def _create_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "matters",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_matters_workspace_id"),
    )
    for table_name in (
        "plan_entitlements",
        "workspace_monthly_entitlement_usage",
        "entitlement_usage_reservations",
        "workspace_entitlement_overrides",
    ):
        sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("entitlement", sa.String(40), nullable=False),
            sa.CheckConstraint(
                _check_expression(LEGACY_ENTITLEMENTS),
                name="entitlement_key_enum",
            ),
        )
    metadata.create_all(connection)
    connection.execute(
        sa.text("INSERT INTO workspaces (id) VALUES ('workspace-a')")
    )


def _install(connection: sa.Connection):
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def _entitlement_constraint(connection: sa.Connection, table_name: str) -> str:
    return " ".join(
        item["sqltext"]
        for item in sa.inspect(connection).get_check_constraints(table_name)
        if item["name"] == "entitlement_key_enum"
    )


def test_process_monitoring_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        assert migration.revision == "b6d8f0a2c415"
        assert migration.down_revision == "a1f3b5c7d9e2"

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {"monitored_processes", "process_publications"} <= set(
            inspector.get_table_names()
        )
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("monitored_processes")
        } == {"uq_monitored_process_workspace_numero_cnj"}
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("process_publications")
        } == {"uq_process_publication_workspace_fonte_external_id"}
        for table_name in migration.ENTITLEMENT_TABLES:
            assert "monitored_processes" in _entitlement_constraint(connection, table_name)

        connection.execute(
            sa.text(
                "INSERT INTO monitored_processes "
                "(id, workspace_id, numero_cnj, status, created_by_user_id, "
                "created_at, updated_at) "
                "VALUES ('mp-a', 'workspace-a', '0001234-56.2026.8.07.0001', "
                "'ativo', 'user-a', '2026-07-26', '2026-07-26')"
            )
        )
        assert connection.execute(
            sa.text("SELECT numero_cnj FROM monitored_processes WHERE id = 'mp-a'")
        ).scalar_one() == "0001234-56.2026.8.07.0001"

        migration.downgrade()

        remaining = set(sa.inspect(connection).get_table_names())
        assert "monitored_processes" not in remaining
        assert "process_publications" not in remaining
        for table_name in migration.ENTITLEMENT_TABLES:
            assert "monitored_processes" not in _entitlement_constraint(connection, table_name)
    engine.dispose()


def test_process_monitoring_downgrade_fails_closed_with_persisted_quota() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        migration.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO plan_entitlements (id, entitlement) "
                "VALUES ('monitored-processes-a', 'monitored_processes')"
            )
        )

        with pytest.raises(RuntimeError, match="plan_entitlements=1"):
            migration.downgrade()

        assert "monitored_processes" in _entitlement_constraint(connection, "plan_entitlements")
    engine.dispose()


def test_process_monitoring_models_compile_for_postgresql() -> None:
    for model in (MonitoredProcess, ProcessPublication):
        ddl = str(
            sa.schema.CreateTable(model.__table__).compile(dialect=postgresql.dialect())
        )
        assert "UUID" in ddl

    monitored_ddl = str(
        sa.schema.CreateTable(MonitoredProcess.__table__).compile(dialect=postgresql.dialect())
    )
    assert "uq_monitored_process_workspace_numero_cnj" in monitored_ddl
    assert "monitored_process_status_enum" in monitored_ddl

    publication_ddl = str(
        sa.schema.CreateTable(ProcessPublication.__table__).compile(dialect=postgresql.dialect())
    )
    assert "uq_process_publication_workspace_fonte_external_id" in publication_ddl
    assert "ck_process_publication_prazo_requires_dias" in publication_ddl
