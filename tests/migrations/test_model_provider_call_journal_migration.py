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
    / "04d6a8c2e140_add_model_provider_call_journal.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "model_provider_call_journal_migration",
        MIGRATION_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _journal_values(*, workspace_id: str, request_id: str) -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "request_id": request_id,
        "request_fingerprint": "f" * 64,
        "state": "claimed",
        "task_class": "balanced",
        "operation": "migration-test",
        "provider": "anthropic",
        "model_used": "claude-test",
        "route_key": "anthropic:claude-test",
        "estimated_input_tokens": 10,
        "estimated_output_tokens": 5,
        "estimated_cost_usd": "0.001",
        "reserved_tokens": 15,
        "period_start": "2026-07-01",
        "entitlement_reservation_id": None,
        "provider_request_id": "",
        "actual_input_tokens": 0,
        "actual_output_tokens": 0,
        "cached_input_tokens": 0,
        "actual_cost_usd": None,
        "price_catalog_version": "test-v1",
        "route_catalog_version": "",
        "attempt_count": 0,
        "error_code": "",
        "provider_started_at": None,
        "resolved_at": None,
    }


def test_model_provider_call_journal_migration_round_trip(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "04d6a8c2e140"
    assert migration.down_revision == "f3e5a7c9d124"

    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "workspaces",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    sa.Table(
        "entitlement_usage_reservations",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
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
        assert migration.TABLE in inspector.get_table_names()
        indexes = {index["name"] for index in inspector.get_indexes(migration.TABLE)}
        assert {
            "ix_model_provider_call_workspace",
            "ix_model_provider_call_state",
            "ix_model_provider_call_period",
        } <= indexes

        workspace_id = str(uuid4())
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:id)"),
            {"id": workspace_id},
        )
        insert = sa.text(
            f"INSERT INTO {migration.TABLE} ("
            + ", ".join(_journal_values(workspace_id=workspace_id, request_id="req-1"))
            + ") VALUES ("
            + ", ".join(
                f":{name}"
                for name in _journal_values(workspace_id=workspace_id, request_id="req-1")
            )
            + ")"
        )
        connection.execute(
            insert,
            _journal_values(workspace_id=workspace_id, request_id="req-1"),
        )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    insert,
                    _journal_values(workspace_id=workspace_id, request_id="req-1"),
                )

        migration.downgrade()
        assert migration.TABLE not in sa.inspect(connection).get_table_names()
