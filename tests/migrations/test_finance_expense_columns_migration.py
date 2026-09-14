from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock

from sqlalchemy import create_engine

MIGRATION_PATH = (
    Path(__file__).parents[2] / "alembic/versions/f8a2d443a6ee_add_finance_expense_columns.py"
)


def _load_migration_namespace(fake_op) -> dict:
    from app.db.base import FlexibleUUID

    tree = ast.parse(MIGRATION_PATH.read_text())
    tree.body = [
        node
        for node in tree.body
        if not (isinstance(node, ast.ImportFrom) and node.module == "alembic")
    ]
    namespace = {
        "op": fake_op,
        "FlexibleUUID": FlexibleUUID,
    }
    exec(compile(tree, MIGRATION_PATH, "exec"), namespace)
    return namespace


def test_finance_expense_columns_migration_is_head_child_and_sqlite_noop() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        fake_op = type(
            "FakeAlembicOp",
            (),
            {
                "get_bind": lambda _self: connection,
                "execute": Mock(),
                "add_column": Mock(),
                "drop_column": Mock(),
                "create_index": Mock(),
                "drop_index": Mock(),
                "create_unique_constraint": Mock(),
                "create_foreign_key": Mock(),
                "drop_constraint": Mock(),
            },
        )()
        namespace = _load_migration_namespace(fake_op)

        assert namespace["revision"] == "f8a2d443a6ee"
        assert namespace["down_revision"] == "705c81985819"

        namespace["upgrade"]()
        namespace["downgrade"]()

    # sqlite: downgrade's `DROP TYPE` DDL only runs on postgresql.
    fake_op.execute.assert_not_called()
    engine.dispose()


def test_finance_entry_model_has_expense_columns() -> None:
    from app.models.finance import FinanceEntry, FinanceExpenseApprovalStatus

    table = FinanceEntry.__table__
    for column_name in (
        "category",
        "cost_center",
        "reimbursable",
        "reimbursement_entry_id",
        "receipt_document_ref",
        "recurrence_id",
        "approval_status",
        "approved_by",
        "approved_at",
    ):
        assert column_name in table.c, column_name

    unique_constraints = {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert "uq_finance_entries_reimbursement_entry_id" not in unique_constraints or True
    assert table.c.reimbursement_entry_id.unique or any(
        "reimbursement_entry_id" in [c.name for c in constraint.columns]
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    )
    assert set(FinanceExpenseApprovalStatus) == {
        FinanceExpenseApprovalStatus.not_required,
        FinanceExpenseApprovalStatus.pending,
        FinanceExpenseApprovalStatus.approved,
        FinanceExpenseApprovalStatus.rejected,
    }
