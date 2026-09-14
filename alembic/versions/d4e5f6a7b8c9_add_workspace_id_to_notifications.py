"""add workspace_id to notifications

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-05

Isolamento multi-tenant: notificações agora são scoped por workspace.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'notifications',
        sa.Column('workspace_id', sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        'fk_notifications_workspace_id',
        'notifications', 'workspaces',
        ['workspace_id'], ['id'],
        ondelete='CASCADE',
    )
    op.create_index('ix_notifications_workspace_id', 'notifications', ['workspace_id'])


def downgrade() -> None:
    op.drop_index('ix_notifications_workspace_id', table_name='notifications')
    op.drop_constraint('fk_notifications_workspace_id', 'notifications', type_='foreignkey')
    op.drop_column('notifications', 'workspace_id')
