"""Track provider-declared Meta token expiration.

Revision ID: zc0v3s6t7p19
Revises: zb9u2r5s6n08
"""

import sqlalchemy as sa

from alembic import op

revision = "zc0v3s6t7p19"
down_revision = "zb9u2r5s6n08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_connections",
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "platform_official_whatsapp_connections",
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("platform_official_whatsapp_connections", "token_expires_at")
    op.drop_column("whatsapp_connections", "token_expires_at")
