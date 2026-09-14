"""add platform model credentials (chaves de provedores de IA geridas pelo Blackbox)

Segunda camada da precedência de chave por provider no gateway:
BYOK do workspace -> credencial da plataforma (esta tabela) -> variável de
ambiente. Tabela de plataforma, sem coluna de tenant e sem RLS, no mesmo
molde de platform_releases e platform_official_whatsapp_connections.

Revision ID: c9d0e1f2a3b4
Revises: b8d2f4a6c9e1
Create Date: 2026-09-06 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b8d2f4a6c9e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "platform_model_credentials"


def upgrade() -> None:
    op.create_table(
        "platform_model_credentials",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "api_key_hint",
            sa.String(length=8),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("updated_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "provider",
            name="uq_platform_model_credentials_provider",
        ),
        sa.CheckConstraint(
            "provider IN ('anthropic', 'openai', 'gemini', 'vertex', 'maritaca')",
            name="ck_platform_model_credentials_provider",
        ),
    )
    op.create_index(
        "ix_platform_model_credentials_updated_by_user_id",
        "platform_model_credentials",
        ["updated_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_model_credentials_updated_by_user_id", table_name=TABLE_NAME
    )
    op.drop_table(TABLE_NAME)
