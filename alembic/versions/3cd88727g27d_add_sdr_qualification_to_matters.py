"""add sdr_qualification to matters

Revision ID: 3cd88727g27d
Revises: 2bc77616f16c
Create Date: 2026-04-01 00:00:00.000000

Adiciona a coluna sdr_qualification (JSONB) ao modelo Matter.
Armazena a qualificação estruturada do lead gerada pelo KLM SDR Agent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3cd88727g27d'
down_revision: Union[str, None] = '2bc77616f16c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adicionar coluna sdr_qualification (JSONB, nullable) à tabela matters
    op.execute("""
        ALTER TABLE matters
        ADD COLUMN IF NOT EXISTS sdr_qualification JSONB DEFAULT NULL;
    """)

    # Índice GIN para consultas eficientes no JSONB
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_matters_sdr_qualification
        ON matters USING GIN (sdr_qualification)
        WHERE sdr_qualification IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_matters_sdr_qualification;")
    op.execute("ALTER TABLE matters DROP COLUMN IF EXISTS sdr_qualification;")
