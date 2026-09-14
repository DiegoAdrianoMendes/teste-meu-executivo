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
    / "f3e5a7c9d124_add_model_usage_accounting.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("model_usage_accounting_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_usage_accounting_migration_is_idempotent_and_append_only(monkeypatch) -> None:
    migration = _load_migration()
    assert migration.revision == "f3e5a7c9d124"
    assert migration.down_revision == "f2d4e6a8b013"
    engine = sa.create_engine("sqlite:///:memory:")

    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        metadata = sa.MetaData()
        sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
        metadata.create_all(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

        tables = set(sa.inspect(connection).get_table_names())
        assert {
            "platform_model_usage_ledger",
            "platform_token_usage_by_task",
        } <= tables
        workspace_id = str(uuid4())
        receipt_id = str(uuid4())
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:id)"),
            {"id": workspace_id},
        )
        receipt = {
            "id": receipt_id,
            "workspace_id": workspace_id,
            "request_id": "request-1",
            "task_class": "simple",
            "operation": "test",
            "model_used": "model",
            "provider": "fake",
            "provider_request_id": "provider-1",
            "input_tokens": 10,
            "output_tokens": 2,
            "cached_input_tokens": 0,
            "cost_usd": "0.001",
            "price_catalog_version": "prices-v1",
            "route_catalog_version": "routes-v1",
        }
        connection.execute(
            sa.text(
                "INSERT INTO platform_model_usage_ledger "
                "(id, workspace_id, request_id, task_class, operation, model_used, "
                "provider, provider_request_id, input_tokens, output_tokens, "
                "cached_input_tokens, cost_usd, price_catalog_version, "
                "route_catalog_version) VALUES "
                "(:id, :workspace_id, :request_id, :task_class, :operation, :model_used, "
                ":provider, :provider_request_id, :input_tokens, :output_tokens, "
                ":cached_input_tokens, :cost_usd, :price_catalog_version, "
                ":route_catalog_version)"
            ),
            receipt,
        )

        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO platform_model_usage_ledger "
                    "(id, workspace_id, request_id, task_class, operation, model_used, "
                    "provider, provider_request_id, input_tokens, output_tokens, "
                    "cached_input_tokens, cost_usd, price_catalog_version, "
                    "route_catalog_version) VALUES "
                    "(:duplicate_id, :workspace_id, :request_id, :task_class, :operation, "
                    ":model_used, :provider, :provider_request_id, :input_tokens, "
                    ":output_tokens, :cached_input_tokens, :cost_usd, "
                    ":price_catalog_version, :route_catalog_version)"
                ),
                {**receipt, "duplicate_id": str(uuid4())},
            )
        with pytest.raises(IntegrityError, match="immutable"):
            connection.execute(
                sa.text(
                    "UPDATE platform_model_usage_ledger SET operation='tampered' "
                    "WHERE id=:id"
                ),
                {"id": receipt_id},
            )
        with pytest.raises(IntegrityError, match="immutable"):
            connection.execute(
                sa.text("DELETE FROM platform_model_usage_ledger WHERE id=:id"),
                {"id": receipt_id},
            )

        migration.downgrade()
        tables = set(sa.inspect(connection).get_table_names())
        assert "platform_model_usage_ledger" not in tables
        assert "platform_token_usage_by_task" not in tables

    engine.dispose()
