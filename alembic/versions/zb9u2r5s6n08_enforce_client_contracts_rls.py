"""Enforce tenant RLS on canonical legal client contracts.

Revision ID: zb9u2r5s6n08
Revises: za8t1q4r5m97
"""

from alembic import op

revision = "zb9u2r5s6n08"
down_revision = "za8t1q4r5m97"
branch_labels = None
depends_on = None

_TABLE = "client_contracts"
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
