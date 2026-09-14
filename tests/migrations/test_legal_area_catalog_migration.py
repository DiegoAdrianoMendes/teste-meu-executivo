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
    / "f2d4e6a8b013_add_legal_area_catalog_and_routing.py"
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
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("legal_area_catalog_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_expression(values: tuple[str, ...]) -> str:
    return f"entitlement IN ({', '.join(repr(value) for value in values)})"


def _create_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "matters",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("practice_area", sa.String(80), nullable=False),
    )
    for table_name in (
        "plan_entitlements",
        "workspace_monthly_entitlement_usage",
        "entitlement_usage_reservations",
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
        sa.text(
            "INSERT INTO matters (id, workspace_id, practice_area) "
            "VALUES ('matter-a', 'workspace-a', 'trabalhista')"
        )
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


def test_legal_area_catalog_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)

        migration.upgrade()

        inspector = sa.inspect(connection)
        column_names = {column["name"] for column in inspector.get_columns("matters")}
        assert {
            "legal_area_key",
            "legal_product_key",
            "legal_product_custom",
            "legal_object_name",
            "counterparty_name",
            "legal_issue_summary",
            "legal_context",
        } <= column_names
        assert connection.execute(
            sa.text("SELECT legal_area_key FROM matters WHERE id = 'matter-a'")
        ).scalar_one() == "trabalhista"
        for table_name in migration.ENTITLEMENT_TABLES:
            assert "legal_areas" in _entitlement_constraint(connection, table_name)

        migration.downgrade()

        column_names = {
            column["name"] for column in sa.inspect(connection).get_columns("matters")
        }
        assert "legal_area_key" not in column_names
        for table_name in migration.ENTITLEMENT_TABLES:
            assert "legal_areas" not in _entitlement_constraint(connection, table_name)
    engine.dispose()


def test_legal_area_catalog_downgrade_fails_closed_with_persisted_quota() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        migration.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO plan_entitlements (id, entitlement) "
                "VALUES ('legal-areas-a', 'legal_areas')"
            )
        )

        with pytest.raises(RuntimeError, match="plan_entitlements=1"):
            migration.downgrade()

        assert "legal_areas" in _entitlement_constraint(connection, "plan_entitlements")
    engine.dispose()
