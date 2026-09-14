from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.entitlement import (
    EntitlementUsageReservation,
    PlanEntitlement,
    WorkspaceMonthlyUsage,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path

MIGRATION_PATH = (
    PROJECT_ROOT / "alembic" / "versions" / "c4e6f8a0d003_add_typed_entitlements.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("typed_entitlements_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_parent_tables(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("plans", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    metadata.create_all(connection)


def test_typed_entitlements_migration_round_trip(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "c4e6f8a0d003"
    assert migration.down_revision == "b3d5f7a9c002"
    assert migration.TENANT_TABLES == (
        "workspace_monthly_entitlement_usage",
        "entitlement_usage_reservations",
    )

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_parent_tables(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {
            "plan_entitlements",
            "workspace_monthly_entitlement_usage",
            "entitlement_usage_reservations",
        } <= set(inspector.get_table_names())
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("plan_entitlements")
        } == {"uq_plan_entitlement_key"}
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(
                "workspace_monthly_entitlement_usage"
            )
        } == {"uq_workspace_monthly_usage_key"}
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(
                "entitlement_usage_reservations"
            )
        } == {"uq_usage_reservation_idempotency"}

        migration.downgrade()
        remaining = set(sa.inspect(connection).get_table_names())
        assert "plan_entitlements" not in remaining
        assert "workspace_monthly_entitlement_usage" not in remaining
        assert "entitlement_usage_reservations" not in remaining


def test_entitlement_models_compile_for_postgresql() -> None:
    for model in (PlanEntitlement, WorkspaceMonthlyUsage, EntitlementUsageReservation):
        ddl = str(
            sa.schema.CreateTable(model.__table__).compile(dialect=postgresql.dialect())
        )
        assert "UUID" in ddl
        assert "entitlement_key_enum" in ddl

    reservation_ddl = str(
        sa.schema.CreateTable(EntitlementUsageReservation.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "uq_usage_reservation_idempotency" in reservation_ddl
    assert "usage_reservation_status_enum" in reservation_ddl
