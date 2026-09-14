"""Add native WhatsApp Coexistence transport and structured payloads.

Revision ID: ff7a9c1d3e86
Revises: fe6f8a0b2d75
Create Date: 2026-07-21 03:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON

revision: str = "ff7a9c1d3e86"
down_revision: str | Sequence[str] | None = "fe6f8a0b2d75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_connections",
        sa.Column(
            "transport_mode",
            sa.String(length=32),
            server_default="official_api",
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE whatsapp_connections
               SET transport_mode = 'qr_gateway'
             WHERE connection_type = 'QR_CODE'
            """
        )
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("whatsapp_connections") as batch_op:
            batch_op.create_check_constraint(
                "ck_whatsapp_connection_transport_mode",
                "transport_mode IN ('official_api', 'coexistence', 'qr_gateway')",
            )
    else:
        op.create_check_constraint(
            "ck_whatsapp_connection_transport_mode",
            "whatsapp_connections",
            "transport_mode IN ('official_api', 'coexistence', 'qr_gateway')",
        )

    op.add_column(
        "whatsapp_messages",
        sa.Column(
            "message_metadata",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "klm_omnichannel_scheduled_messages",
        sa.Column(
            "message_payload",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("klm_omnichannel_scheduled_messages", "message_payload")
    op.drop_column("whatsapp_messages", "message_metadata")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("whatsapp_connections") as batch_op:
            batch_op.drop_constraint(
                "ck_whatsapp_connection_transport_mode",
                type_="check",
            )
    else:
        op.drop_constraint(
            "ck_whatsapp_connection_transport_mode",
            "whatsapp_connections",
            type_="check",
        )
    op.drop_column("whatsapp_connections", "transport_mode")
