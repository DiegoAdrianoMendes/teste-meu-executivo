from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock

from sqlalchemy import create_engine

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic/versions/e2c0c3a7aa8a_add_finance_charges_and_asaas_webhook_receipts.py"
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


def test_finance_charges_migration_is_single_head_child_and_sqlite_noop() -> None:
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

        assert namespace["revision"] == "e2c0c3a7aa8a"
        assert namespace["down_revision"] == "2f4a6c8e0b31"
        assert namespace["TABLES"] == ("finance_charges", "asaas_webhook_receipts")

        namespace["upgrade"]()
        namespace["downgrade"]()

    # sqlite: RLS DDL (op.execute) must be skipped, since _enable_rls/_disable_rls
    # short-circuit on non-postgresql dialects.
    fake_op.execute.assert_not_called()
    engine.dispose()


def test_finance_charge_model_has_unique_entry_id_and_workspace_predicate() -> None:
    from app.models.finance_charge import FinanceCharge

    table = FinanceCharge.__table__
    assert "workspace_id" in table.c
    assert table.c.workspace_id.nullable is False
    unique_constraints = {
        constraint.name for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert "uq_finance_charges_entry_id" in unique_constraints


def test_asaas_webhook_receipt_model_has_unique_workspace_event_id() -> None:
    from app.models.asaas_webhook_receipt import AsaasWebhookReceipt

    table = AsaasWebhookReceipt.__table__
    assert "workspace_id" in table.c
    assert table.c.workspace_id.nullable is False
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert unique_constraints.get("uq_asaas_webhook_receipts_workspace_event_id") == (
        "workspace_id",
        "event_id",
    )
