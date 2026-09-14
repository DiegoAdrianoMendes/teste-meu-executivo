from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock

from sqlalchemy import create_engine

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic/versions/838f9f719531_add_agent_autonomy_policies.py"
)


def _load_migration_namespace(fake_op) -> dict:
    from app.db.base import FlexibleJSON, FlexibleUUID

    tree = ast.parse(MIGRATION_PATH.read_text())
    tree.body = [
        node
        for node in tree.body
        if not (isinstance(node, ast.ImportFrom) and node.module == "alembic")
    ]
    namespace = {
        "op": fake_op,
        "FlexibleJSON": FlexibleJSON,
        "FlexibleUUID": FlexibleUUID,
    }
    exec(compile(tree, MIGRATION_PATH, "exec"), namespace)
    return namespace


def test_agent_autonomy_policies_migration_is_single_head_child_and_sqlite_noop() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        fake_op = type(
            "FakeAlembicOp",
            (),
            {
                "get_bind": lambda _self: connection,
                "execute": Mock(),
                "create_table": Mock(),
                "create_index": Mock(),
                "drop_table": Mock(),
                "drop_index": Mock(),
            },
        )()
        namespace = _load_migration_namespace(fake_op)

        assert namespace["down_revision"] == "013d26e9d488"
        assert namespace["TABLE_NAME"] == "agent_autonomy_policies"

        namespace["upgrade"]()
        namespace["downgrade"]()

    # sqlite: RLS DDL (op.execute) deve ser pulado — _enable_rls/_disable_rls
    # saem cedo em dialetos != postgresql.
    fake_op.execute.assert_not_called()
    engine.dispose()


def test_agent_autonomy_policy_model_has_expected_shape() -> None:
    from app.models.agent_autonomy_policy import (
        AgentAutonomyPolicy,
        AgentAutonomyPolicyLevel,
        AgentAutonomyPolicyRisk,
    )

    table = AgentAutonomyPolicy.__table__
    assert table.name == "agent_autonomy_policies"
    for column in (
        "workspace_id",
        "event_type",
        "conditions",
        "area_key",
        "client_id",
        "portfolio_key",
        "agent_key",
        "action",
        "autonomy_level",
        "capability_key",
        "risk_level",
        "min_confidence",
        "budget_limit",
        "budget_currency",
        "sla_minutes",
        "idempotency_key_template",
        "rollback_strategy",
        "approval_required",
        "escalation_target_agent_key",
        "priority",
        "is_active",
    ):
        assert column in table.c, f"coluna ausente: {column}"

    assert table.c.workspace_id.nullable is False
    assert table.c.event_type.nullable is False
    assert table.c.action.nullable is False
    assert table.c.autonomy_level.nullable is False
    assert table.c.approval_required.default.arg is True
    assert table.c.is_active.default.arg is True

    assert {level.value for level in AgentAutonomyPolicyLevel} == {
        "A0",
        "A1",
        "A2",
        "A3",
        "A4",
    }
    assert {risk.value for risk in AgentAutonomyPolicyRisk} == {"baixo", "medio", "alto"}

    check_constraints = {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert "ck_agent_autonomy_policy_min_confidence_range" in check_constraints
    assert "ck_agent_autonomy_policy_budget_nonnegative" in check_constraints
    assert "ck_agent_autonomy_policy_sla_positive" in check_constraints


def test_agent_autonomy_policies_table_is_registered_in_rls_catalog() -> None:
    from app.db.rls import RLS_TENANT_TABLES

    assert "agent_autonomy_policies" in RLS_TENANT_TABLES
