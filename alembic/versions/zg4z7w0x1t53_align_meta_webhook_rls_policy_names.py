"""Align Meta webhook tenant policies with the canonical RLS contract.

Revision ID: zg4z7w0x1t53
Revises: zf3y6v9w0s42
"""

from alembic import op

revision = "zg4z7w0x1t53"
down_revision = "zf3y6v9w0s42"
branch_labels = None
depends_on = None

TABLES = (
    "meta_webhook_receipts",
    "meta_webhook_operational_states",
    "meta_webhook_subscription_states",
    "meta_webhook_automation_policies",
    "meta_webhook_subscription_jobs",
)


def _workspace_scope() -> str:
    return (
        "workspace_id::text = "
        "NULLIF(current_setting('app.current_workspace_id', true), '')"
    )


def upgrade() -> None:
    scope = _workspace_scope()
    for table in TABLES:
        op.execute(f'DROP POLICY IF EXISTS "{table}_workspace_isolation" ON "{table}"')
        op.execute(f'DROP POLICY IF EXISTS "workspace_current_id" ON "{table}"')
        op.execute(
            f'''CREATE POLICY "workspace_current_id" ON "{table}"
            USING ({scope})
            WITH CHECK ({scope})'''
        )


def downgrade() -> None:
    scope = _workspace_scope()
    for table in TABLES:
        op.execute(f'DROP POLICY IF EXISTS "workspace_current_id" ON "{table}"')
        op.execute(
            f'''CREATE POLICY "{table}_workspace_isolation" ON "{table}"
            USING ({scope})
            WITH CHECK ({scope})'''
        )
