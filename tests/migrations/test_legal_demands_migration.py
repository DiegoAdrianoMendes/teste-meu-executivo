from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock

from sqlalchemy import create_engine

MIGRATION_PATH = (
    Path(__file__).parents[2] / "alembic/versions/013d26e9d488_add_legal_demands.py"
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


def test_legal_demands_migration_is_single_head_child_and_sqlite_noop() -> None:
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

        assert namespace["down_revision"] == "58f5e2e75303"
        assert namespace["TABLE_NAME"] == "legal_demands"

        namespace["upgrade"]()
        namespace["downgrade"]()

    # sqlite: RLS DDL (op.execute) deve ser pulado — _enable_rls/_disable_rls
    # saem cedo em dialetos != postgresql.
    fake_op.execute.assert_not_called()
    engine.dispose()


def test_legal_demand_model_unifies_seven_natures_without_duplicating_matter() -> None:
    from app.models.legal_demand import LegalDemand, LegalDemandNature

    table = LegalDemand.__table__
    assert "workspace_id" in table.c
    assert table.c.workspace_id.nullable is False
    assert "client_id" in table.c
    assert table.c.client_id.nullable is False

    # Referencia o que já existe — nunca duplica dado de Matter.
    for pointer_column in ("matter_id", "contract_ref", "finance_ref"):
        assert pointer_column in table.c
        assert table.c[pointer_column].nullable is True

    assert {nature.value for nature in LegalDemandNature} == {
        "judicial",
        "administrativo",
        "consultivo",
        "contrato",
        "acordo",
        "servico_recorrente",
        "massificado",
    }

    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert unique_constraints.get("uq_legal_demands_workspace_external_ref") == (
        "workspace_id",
        "external_ref",
    )


def test_legal_demands_table_is_registered_in_rls_catalog() -> None:
    from app.db.rls import RLS_TENANT_TABLES

    assert "legal_demands" in RLS_TENANT_TABLES
