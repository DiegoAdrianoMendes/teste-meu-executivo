from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock

from sqlalchemy import create_engine

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic/versions/58f5e2e75303_add_knowledge_article_feedback.py"
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


def test_knowledge_article_feedback_migration_is_single_head_child_and_sqlite_noop() -> None:
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

        assert namespace["down_revision"] == "45913a19da62"
        assert namespace["TABLE_NAME"] == "klm_knowledge_article_feedback"

        namespace["upgrade"]()
        namespace["downgrade"]()

    # sqlite: RLS DDL (op.execute) deve ser pulado — _enable_rls/_disable_rls
    # saem cedo em dialetos != postgresql.
    fake_op.execute.assert_not_called()
    engine.dispose()


def test_knowledge_article_feedback_model_has_workspace_scoped_unique_vote() -> None:
    from app.models.knowledge_feedback import KnowledgeArticleFeedback

    table = KnowledgeArticleFeedback.__table__
    assert "workspace_id" in table.c
    assert table.c.workspace_id.nullable is False
    assert "user_id" in table.c
    assert table.c.user_id.nullable is False

    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert unique_constraints.get("uq_klm_knowledge_feedback_workspace_user_article") == (
        "workspace_id",
        "user_id",
        "article_id",
    )


def test_knowledge_article_feedback_table_is_registered_in_rls_catalog() -> None:
    from app.db.rls import RLS_TENANT_TABLES

    assert "klm_knowledge_article_feedback" in RLS_TENANT_TABLES


def test_follow_up_tasks_table_still_registered_in_rls_catalog() -> None:
    from app.db.rls import RLS_TENANT_TABLES

    assert "klm_follow_up_tasks" in RLS_TENANT_TABLES
