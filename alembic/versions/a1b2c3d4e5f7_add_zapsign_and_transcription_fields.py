"""add zapsign and transcription fields

Revision ID: a1b2c3d4e5f7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-05 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── matters: campos ZapSign ──
    op.add_column("matters", sa.Column("zapsign_doc_token", sa.String(255), nullable=True))
    op.add_column("matters", sa.Column("zapsign_sign_url", sa.Text(), nullable=True))
    op.add_column("matters", sa.Column("zapsign_status", sa.String(50), nullable=True))

    # ── whatsapp_messages: campos de transcrição ──
    op.add_column("whatsapp_messages", sa.Column("transcript", sa.Text(), nullable=True))
    op.add_column("whatsapp_messages", sa.Column("image_transcript", sa.Text(), nullable=True))

    # ── Adicionar novos valores aos enums ──
    op.execute("ALTER TYPE kanban_column_id_enum ADD VALUE IF NOT EXISTS 'assinado'")
    op.execute("ALTER TYPE micro_status_enum ADD VALUE IF NOT EXISTS 'contrato_assinado'")


def downgrade() -> None:
    # Remover colunas adicionadas
    op.drop_column("matters", "zapsign_doc_token")
    op.drop_column("matters", "zapsign_sign_url")
    op.drop_column("matters", "zapsign_status")
    op.drop_column("whatsapp_messages", "transcript")
    op.drop_column("whatsapp_messages", "image_transcript")

    # PostgreSQL enums não suportam DROP VALUE diretamente.
    # Downgrade dos valores de enum é intencionalmente um no-op.
