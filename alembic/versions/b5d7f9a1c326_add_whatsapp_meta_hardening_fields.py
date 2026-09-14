"""add WhatsApp campaign pause policy, BYOA connection secrets and
delivery_uncertain campaign statuses

Revision ID: b5d7f9a1c326
Revises: a4c6e8f0b215
Create Date: 2026-07-23 00:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b5d7f9a1c326"
down_revision: str | Sequence[str] | None = "a4c6e8f0b215"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # Compartilhado pelas colunas status de whatsapp_campaign_recipients e
        # whatsapp_campaign_send_jobs / whatsapp_campaign_delivery_events: cada
        # coluna native-enum criada a partir de CampaignRecipientStatus /
        # CampaignSendJobStatus ganha um tipo Postgres próprio, mesmo
        # compartilhando o enum Python (mesmo padrão de
        # a7c9e1f3b501_add_whatsapp_outbound_idempotency.py).
        op.execute(
            "ALTER TYPE wa_campaign_recipient_status_enum "
            "ADD VALUE IF NOT EXISTS 'delivery_uncertain'"
        )
        op.execute(
            "ALTER TYPE wa_campaign_delivery_status_enum "
            "ADD VALUE IF NOT EXISTS 'delivery_uncertain'"
        )
        op.execute(
            "ALTER TYPE wa_campaign_send_job_status_enum "
            "ADD VALUE IF NOT EXISTS 'delivery_uncertain'"
        )

    op.add_column(
        "whatsapp_campaigns",
        sa.Column("pause_reason", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "whatsapp_campaigns",
        sa.Column(
            "paused_by_policy",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.add_column(
        "whatsapp_connections",
        sa.Column("app_secret_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "whatsapp_connections",
        sa.Column("webhook_verify_token_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_connections", "webhook_verify_token_encrypted")
    op.drop_column("whatsapp_connections", "app_secret_encrypted")

    op.drop_column("whatsapp_campaigns", "paused_by_policy")
    op.drop_column("whatsapp_campaigns", "pause_reason")

    # Postgres native enums não suportam DROP VALUE: revertemos qualquer
    # linha que já tenha adotado o valor novo, no mesmo espírito de
    # a7c9e1f3b501_add_whatsapp_outbound_idempotency.py, mantendo o tipo em
    # si (remover o valor exigiria recriar o tipo e é destrutivo demais para
    # um downgrade rotineiro).
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "UPDATE whatsapp_campaign_send_jobs SET status = 'failed' "
                "WHERE status = 'delivery_uncertain'"
            )
        )
        op.execute(
            sa.text(
                "UPDATE whatsapp_campaign_delivery_events SET status = 'failed' "
                "WHERE status = 'delivery_uncertain'"
            )
        )
        op.execute(
            sa.text(
                "UPDATE whatsapp_campaign_recipients SET status = 'failed' "
                "WHERE status = 'delivery_uncertain'"
            )
        )
