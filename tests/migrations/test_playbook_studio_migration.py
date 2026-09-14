from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.models.playbook import (
    PlaybookScopeBinding,
    PlaybookSimulationRun,
    PlaybookVersionGovernance,
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
    / "d9a7e5c3b102_add_playbook_studio.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("playbook_studio_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parents(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "config_documents",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
    )
    sa.Table(
        "config_document_versions",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), nullable=False),
    )
    metadata.create_all(connection)


def test_playbook_studio_migration_constraints_and_downgrade(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "d9a7e5c3b102"
    assert migration.down_revision == "c8f5d2a9b714"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _parents(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {
            "playbook_version_governance",
            "playbook_scope_bindings",
            "playbook_simulation_runs",
        } <= set(inspector.get_table_names())
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "playbook_simulation_runs"
            )
        } >= {
            "ck_playbook_simulation_local_only",
            "ck_playbook_simulation_nonnegative_usage",
        }

        workspace_id = str(uuid4())
        user_id = str(uuid4())
        document_id = str(uuid4())
        version_id = str(uuid4())
        connection.execute(sa.text("INSERT INTO workspaces (id) VALUES (:id)"), {"id": workspace_id})
        connection.execute(sa.text("INSERT INTO users (id) VALUES (:id)"), {"id": user_id})
        connection.execute(
            sa.text("INSERT INTO config_documents (id, workspace_id) VALUES (:id, :workspace_id)"),
            {"id": document_id, "workspace_id": workspace_id},
        )
        connection.execute(
            sa.text("INSERT INTO config_document_versions (id, document_id) VALUES (:id, :document_id)"),
            {"id": version_id, "document_id": document_id},
        )
        event_values = {
            "id": str(uuid4()),
            "workspace_id": workspace_id,
            "document_id": document_id,
            "document_version_id": version_id,
            "actor_user_id": user_id,
        }
        connection.execute(
            sa.text(
                "INSERT INTO playbook_version_governance "
                "(id, workspace_id, document_id, document_version_id, version, sequence, state, reason, actor_user_id) "
                "VALUES (:id, :workspace_id, :document_id, :document_version_id, 1, 1, 'draft', 'created', :actor_user_id)"
            ),
            event_values,
        )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "INSERT INTO playbook_version_governance "
                        "(id, workspace_id, document_id, document_version_id, version, sequence, state, reason) "
                        "VALUES (:id, :workspace_id, :document_id, :document_version_id, 1, 2, 'draft', 'duplicate')"
                    ),
                    {**event_values, "id": str(uuid4())},
                )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "INSERT INTO playbook_version_governance "
                        "(id, workspace_id, document_id, document_version_id, version, sequence, state, reason) "
                        "VALUES (:id, :workspace_id, :document_id, :document_version_id, 2, 2, 'approved', 'invalid sequence')"
                    ),
                    {**event_values, "id": str(uuid4())},
                )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "INSERT INTO playbook_simulation_runs "
                        "(id, workspace_id, document_id, document_version_id, version, content_digest, status, "
                        "scenario, customer_message, context, response, next_action, policy_trace, sources, "
                        "autonomy_mode, human_approval_required, external_actions_executed, prompt_tokens, "
                        "completion_tokens, estimated_cost_brl, model_key) VALUES "
                        "(:id, :workspace_id, :document_id, :document_version_id, 1, :digest, 'passed', "
                        "'local', 'message', '{}', 'response', 'next', '[]', '[]', "
                        "'human_only', true, 1, 1, 1, 0, 'local')"
                    ),
                    {**event_values, "id": str(uuid4()), "digest": "a" * 64},
                )

        migration.downgrade()
        tables = set(sa.inspect(connection).get_table_names())
        assert "playbook_version_governance" not in tables
        assert "playbook_scope_bindings" not in tables
        assert "playbook_simulation_runs" not in tables


def test_playbook_models_compile_for_postgresql() -> None:
    governance = str(
        sa.schema.CreateTable(PlaybookVersionGovernance.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    binding = str(
        sa.schema.CreateTable(PlaybookScopeBinding.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    simulation = str(
        sa.schema.CreateTable(PlaybookSimulationRun.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "UUID" in governance
    assert "ck_playbook_governance_state_sequence" in governance
    assert "uq_playbook_scope_binding" in binding
    assert "ck_playbook_simulation_local_only" in simulation
