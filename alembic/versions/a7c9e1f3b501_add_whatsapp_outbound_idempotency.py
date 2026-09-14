"""add durable WhatsApp outbound idempotency

Revision ID: a7c9e1f3b501
Revises: f4a6c8e0b209
Create Date: 2026-07-20 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON

revision: str = "a7c9e1f3b501"
down_revision: str | Sequence[str] | None = "f4a6c8e0b209"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE wa_message_status_enum "
            "ADD VALUE IF NOT EXISTS 'delivery_uncertain'"
        )
        # WaMessageStatus is intentionally shared by the Meta-channel table.
        op.execute(
            "ALTER TYPE meta_message_status_enum "
            "ADD VALUE IF NOT EXISTS 'delivery_uncertain'"
        )

    op.add_column(
        "whatsapp_messages",
        sa.Column("outbound_idempotency_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "whatsapp_messages",
        sa.Column("outbound_payload_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "whatsapp_messages",
        sa.Column(
            "outbound_request_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "whatsapp_messages",
        sa.Column("delivery_uncertain_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "whatsapp_messages",
        sa.Column("outbound_error", FlexibleJSON(), nullable=True),
    )
    op.create_index(
        "ux_whatsapp_messages_outbound_idempotency_key",
        "whatsapp_messages",
        ["outbound_idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_whatsapp_messages_delivery_reconciliation",
        "whatsapp_messages",
        ["status", "delivery_uncertain_at"],
        unique=False,
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE meta_social_messages SET status = 'failed' "
            "WHERE status = 'delivery_uncertain'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE whatsapp_messages SET status = 'failed' "
            "WHERE status = 'delivery_uncertain'"
        )
    )
    op.drop_index(
        "ix_whatsapp_messages_delivery_reconciliation",
        table_name="whatsapp_messages",
    )
    op.drop_index(
        "ux_whatsapp_messages_outbound_idempotency_key",
        table_name="whatsapp_messages",
    )
    op.drop_column("whatsapp_messages", "outbound_error")
    op.drop_column("whatsapp_messages", "delivery_uncertain_at")
    op.drop_column("whatsapp_messages", "outbound_request_started_at")
    op.drop_column("whatsapp_messages", "outbound_payload_digest")
    op.drop_column("whatsapp_messages", "outbound_idempotency_key")
