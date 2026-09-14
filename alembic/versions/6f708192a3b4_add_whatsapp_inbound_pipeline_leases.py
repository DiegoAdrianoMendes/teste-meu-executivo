"""add fenced leases to the inbound WhatsApp pipeline

Revision ID: 6f708192a3b4
Revises: 5e6f708192a3
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6f708192a3b4"
down_revision: str | None = "5e6f708192a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_messages",
        sa.Column("inbound_lease_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "whatsapp_messages",
        sa.Column("inbound_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "whatsapp_messages",
        sa.Column(
            "inbound_lease_generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_index(
        "ix_whatsapp_messages_inbound_lease",
        "whatsapp_messages",
        ["inbound_lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_whatsapp_messages_inbound_lease", table_name="whatsapp_messages")
    op.drop_column("whatsapp_messages", "inbound_lease_generation")
    op.drop_column("whatsapp_messages", "inbound_lease_expires_at")
    op.drop_column("whatsapp_messages", "inbound_lease_token")
