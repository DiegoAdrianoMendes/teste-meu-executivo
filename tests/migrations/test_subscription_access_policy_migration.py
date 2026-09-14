from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.models.subscription_access import (
    SubscriptionAccessState,
    SubscriptionAccessTransition,
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
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "b3d5f7a9c002_add_subscription_access_policy.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "subscription_access_policy_migration",
        MIGRATION_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_parent_tables(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("subscriptions", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    metadata.create_all(connection)


def test_subscription_access_migration_upgrade_downgrade_and_constraints(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "b3d5f7a9c002"
    assert migration.down_revision == "a2c4e6f8b001"
    assert migration.TENANT_TABLES == (
        "subscription_access_states",
        "subscription_access_transitions",
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
            "subscription_access_states",
            "subscription_access_transitions",
        } <= set(inspector.get_table_names())
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("subscription_access_states")
        } == {"uq_subscription_access_workspace"}
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(
                "subscription_access_transitions"
            )
        } == {"uq_subscription_access_transition_idempotency"}
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints("subscription_access_states")
        } == {
            "ck_subscription_access_grace_window",
            "ck_subscription_access_status",
            "ck_subscription_access_trial_window",
            "ck_subscription_access_version_positive",
        }
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "subscription_access_transitions"
            )
        } == {
            "ck_subscription_access_transition_from_status",
            "ck_subscription_access_transition_to_status",
            "ck_subscription_access_transition_version_step",
        }
        assert {
            index["name"] for index in inspector.get_indexes("subscription_access_states")
        } == {
            "ix_subscription_access_status",
            "ix_subscription_access_subscription_id",
        }
        assert {
            index["name"]
            for index in inspector.get_indexes("subscription_access_transitions")
        } == {
            "ix_subscription_access_transition_state_version",
            "ix_subscription_access_transition_workspace_occurred",
        }

        workspace_id = str(uuid4())
        subscription_id = str(uuid4())
        state_id = str(uuid4())
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:id)"),
            {"id": workspace_id},
        )
        connection.execute(
            sa.text("INSERT INTO subscriptions (id) VALUES (:id)"),
            {"id": subscription_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO subscription_access_states "
                "(id, workspace_id, subscription_id, status, state_version, status_changed_at) "
                "VALUES (:id, :workspace_id, :subscription_id, 'active', 1, CURRENT_TIMESTAMP)"
            ),
            {
                "id": state_id,
                "workspace_id": workspace_id,
                "subscription_id": subscription_id,
            },
        )
        transition_values = {
            "id": str(uuid4()),
            "workspace_id": workspace_id,
            "state_id": state_id,
            "subscription_id": subscription_id,
            "idempotency_key": "migration-idempotency-0001",
            "request_fingerprint": "a" * 64,
        }
        connection.execute(
            sa.text(
                "INSERT INTO subscription_access_transitions "
                "(id, workspace_id, access_state_id, subscription_id, idempotency_key, "
                "request_fingerprint, from_status, to_status, from_version, to_version, "
                "applied, source, reason, occurred_at) VALUES "
                "(:id, :workspace_id, :state_id, :subscription_id, :idempotency_key, "
                ":request_fingerprint, 'active', 'active', 1, 1, false, "
                "'migration.test', 'idempotency_probe', CURRENT_TIMESTAMP)"
            ),
            transition_values,
        )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "INSERT INTO subscription_access_transitions "
                        "(id, workspace_id, access_state_id, subscription_id, idempotency_key, "
                        "request_fingerprint, from_status, to_status, from_version, to_version, "
                        "applied, source, reason, occurred_at) VALUES "
                        "(:id, :workspace_id, :state_id, :subscription_id, :idempotency_key, "
                        ":request_fingerprint, 'active', 'active', 1, 1, false, "
                        "'migration.test', 'duplicate_probe', CURRENT_TIMESTAMP)"
                    ),
                    {**transition_values, "id": str(uuid4())},
                )

        migration.downgrade()
        downgraded = sa.inspect(connection)
        assert "subscription_access_states" not in downgraded.get_table_names()
        assert "subscription_access_transitions" not in downgraded.get_table_names()


def test_subscription_access_models_compile_for_postgresql() -> None:
    state_ddl = str(
        sa.schema.CreateTable(SubscriptionAccessState.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    transition_ddl = str(
        sa.schema.CreateTable(SubscriptionAccessTransition.__table__).compile(
            dialect=postgresql.dialect()
        )
    )

    assert "UUID" in state_ddl
    assert "ck_subscription_access_grace_window" in state_ddl
    assert "uq_subscription_access_workspace" in state_ddl
    assert "UUID" in transition_ddl
    assert "ck_subscription_access_transition_version_step" in transition_ddl
    assert "uq_subscription_access_transition_idempotency" in transition_ddl
