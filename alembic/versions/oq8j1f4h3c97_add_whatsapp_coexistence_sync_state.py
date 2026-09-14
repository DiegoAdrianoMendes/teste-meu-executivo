"""add WhatsApp coexistence synchronization state

Revision ID: oq8j1f4h3c97
Revises: op7i0e3g2b86
"""

import sqlalchemy as sa

from alembic import op

revision = "oq8j1f4h3c97"
down_revision = "op7i0e3g2b86"
branch_labels = None
depends_on = None


def _add_columns(table: str) -> None:
    if table == "platform_official_whatsapp_connections":
        op.add_column(
            table,
            sa.Column(
                "transport_mode",
                sa.String(length=32),
                nullable=False,
                server_default="official_api",
            ),
        )
    op.add_column(
        table,
        sa.Column(
            "coexistence_sync_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_applicable",
        ),
    )
    op.add_column(
        table,
        sa.Column("coexistence_contacts_sync_request_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        table,
        sa.Column("coexistence_history_sync_request_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        table,
        sa.Column("coexistence_sync_requested_at", sa.DateTime(timezone=True), nullable=True),
    )


def upgrade() -> None:
    _add_columns("whatsapp_connections")
    _add_columns("platform_official_whatsapp_connections")


def downgrade() -> None:
    for table in ("platform_official_whatsapp_connections", "whatsapp_connections"):
        op.drop_column(table, "coexistence_sync_requested_at")
        op.drop_column(table, "coexistence_history_sync_request_id")
        op.drop_column(table, "coexistence_contacts_sync_request_id")
        op.drop_column(table, "coexistence_sync_status")
    op.drop_column("platform_official_whatsapp_connections", "transport_mode")
