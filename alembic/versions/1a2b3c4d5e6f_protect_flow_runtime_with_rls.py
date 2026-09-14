"""protect flow and agent runtime state with workspace RLS

Revision ID: 1a2b3c4d5e6f
Revises: 0f4e5d6c7b8a
Create Date: 2026-07-16 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "1a2b3c4d5e6f"
down_revision: str | Sequence[str] | None = "0f4e5d6c7b8a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
POLICY_NAME = "workspace_current_id"
TENANT_TABLES = (
    "agent_runs",
    "flow_runs",
    "human_overrides",
    "scheduled_messages",
)


def _workspace_predicate() -> str:
    return (
        "workspace_id::text = "
        f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    predicate = _workspace_predicate()
    for table_name in TENANT_TABLES:
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


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for table_name in reversed(TENANT_TABLES):
        op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')
