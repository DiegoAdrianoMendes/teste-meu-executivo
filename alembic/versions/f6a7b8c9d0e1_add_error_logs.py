"""add error_logs table

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'error_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('error_code', sa.String(20), nullable=False),
        sa.Column('error_hash', sa.String(64), nullable=False),
        sa.Column('severity', sa.String(20), nullable=False, server_default='medium'),
        sa.Column('status', sa.String(20), nullable=False, server_default='open'),
        sa.Column('exception_type', sa.String(200), nullable=False),
        sa.Column('message', sa.Text, nullable=False),
        sa.Column('traceback', sa.Text, nullable=True),
        sa.Column('location', sa.String(500), nullable=True),
        sa.Column('endpoint', sa.String(500), nullable=True),
        sa.Column('method', sa.String(10), nullable=True),
        sa.Column('request_id', sa.String(100), nullable=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id', ondelete='SET NULL'), nullable=True),
        sa.Column('extra', sa.JSON, nullable=True),
        sa.Column('occurrences', sa.Integer, nullable=False, server_default='1'),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_by', sa.String(200), nullable=True),
        sa.Column('resolution_notes', sa.Text, nullable=True),
    )
    op.create_index('ix_error_logs_error_code', 'error_logs', ['error_code'])
    op.create_index('ix_error_logs_error_hash', 'error_logs', ['error_hash'])
    op.create_index('ix_error_logs_status', 'error_logs', ['status'])
    op.create_index('ix_error_logs_status_severity', 'error_logs', ['status', 'severity'])
    op.create_index('ix_error_logs_workspace_status', 'error_logs', ['workspace_id', 'status'])
    op.create_index('ix_error_logs_last_seen', 'error_logs', ['last_seen_at'])


def downgrade() -> None:
    op.drop_table('error_logs')
