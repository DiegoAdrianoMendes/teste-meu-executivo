"""Close RLS gaps for ordinary workspace-owned operational tables.

Revision ID: e5a7c9d1f306
Revises: d4f6a8c0e215
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e5a7c9d1f306"
down_revision: str | Sequence[str] | None = "d4f6a8c0e215"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_NAME = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"
DIRECT_WORKSPACE_TABLES = (
    "token_budgets",
)


def _predicate() -> str:
    return (
        "workspace_id::text = "
        f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    )


def _enable_policy(table_name: str) -> None:
    predicate = _predicate()
    op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY "{POLICY_NAME}" ON "{table_name}"
        AS PERMISSIVE
        FOR ALL
        TO PUBLIC
        USING ({predicate})
        WITH CHECK ({predicate})
        """
    )


def _disable_policy(table_name: str) -> None:
    op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table_name}"')
    op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in DIRECT_WORKSPACE_TABLES:
        _enable_policy(table_name)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in reversed(DIRECT_WORKSPACE_TABLES):
        _disable_policy(table_name)
