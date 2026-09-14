from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock

from sqlalchemy import create_engine

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic/versions/705c81985819_add_finance_recurrences_and_runs.py"
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


def test_finance_recurrences_migration_is_single_head_child_and_sqlite_noop() -> None:
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

        assert namespace["down_revision"] == "e2c0c3a7aa8a"
        assert namespace["TABLES"] == ("finance_recurrences", "finance_recurrence_runs")

        namespace["upgrade"]()
        namespace["downgrade"]()

    # sqlite: RLS DDL (op.execute) deve ser pulado — _enable_rls/_disable_rls
    # saem cedo em dialetos != postgresql.
    fake_op.execute.assert_not_called()
    engine.dispose()


def test_finance_recurrence_model_has_workspace_and_unique_competence() -> None:
    from app.models.finance_recurrence import FinanceRecurrence, FinanceRecurrenceRun

    recurrence_table = FinanceRecurrence.__table__
    assert "workspace_id" in recurrence_table.c
    assert recurrence_table.c.workspace_id.nullable is False

    run_table = FinanceRecurrenceRun.__table__
    assert "workspace_id" in run_table.c
    assert run_table.c.workspace_id.nullable is False
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in run_table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert unique_constraints.get("uq_finance_recurrence_runs_recurrence_competence") == (
        "recurrence_id",
        "competence",
    )
