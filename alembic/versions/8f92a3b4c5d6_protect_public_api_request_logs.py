"""protect Public API request logs with workspace RLS

Revision ID: 8f92a3b4c5d6
Revises: 7f8192a3b4c5
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "8f92a3b4c5d6"
down_revision: str | None = "7f8192a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = (
        "workspace_id::text = "
        "NULLIF(current_setting('app.current_workspace_id', true), '')"
    )
    op.execute('ALTER TABLE "public_api_request_logs" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "public_api_request_logs" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY "workspace_current_id" ON "public_api_request_logs"
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
    op.execute(
        'DROP POLICY IF EXISTS "workspace_current_id" ON "public_api_request_logs"'
    )
    op.execute('ALTER TABLE "public_api_request_logs" NO FORCE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "public_api_request_logs" DISABLE ROW LEVEL SECURITY')
