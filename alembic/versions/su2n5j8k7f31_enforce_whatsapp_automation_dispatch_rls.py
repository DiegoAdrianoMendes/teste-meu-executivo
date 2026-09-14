"""Enforce tenant RLS on WhatsApp automation cooldown dispatches.

Revision ID: su2n5j8k7f31
Revises: rt1m4i7j6e20
"""

from alembic import op

revision = "su2n5j8k7f31"
down_revision = "rt1m4i7j6e20"
branch_labels = None
depends_on = None

_TABLE = "whatsapp_automation_rule_dispatches"
_POLICY = "workspace_current_id"
_PREDICATE = (
    "workspace_id::text = "
    "NULLIF(current_setting('app.current_workspace_id', true), '')"
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'ALTER TABLE "{_TABLE}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{_TABLE}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY "{_POLICY}" ON "{_TABLE}"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})'''
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'DROP POLICY IF EXISTS "{_POLICY}" ON "{_TABLE}"')
    op.execute(f'ALTER TABLE "{_TABLE}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{_TABLE}" DISABLE ROW LEVEL SECURITY')
