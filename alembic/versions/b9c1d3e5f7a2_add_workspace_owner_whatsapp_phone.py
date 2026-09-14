"""add workspace owner_whatsapp_phone (canal do dono — Sprint S1b)

Coluna em tabela existente (workspaces): RLS direto não se aplica —
workspaces é a raiz do tenant, não uma tabela com workspace_id.

Revision ID: b9c1d3e5f7a2
Revises: b7d1f3a9c520
Create Date: 2026-07-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "b9c1d3e5f7a2"
down_revision = "b7d1f3a9c520"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("owner_whatsapp_phone", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "owner_whatsapp_phone")
