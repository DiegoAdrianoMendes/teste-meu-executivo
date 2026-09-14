"""harden campaign monthly recipient quota

Revision ID: 0d2f4a6c8e10
Revises: f3a5c7d9e1b4
Create Date: 2026-07-24 17:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0d2f4a6c8e10"
down_revision: str | Sequence[str] | None = "f3a5c7d9e1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_campaign_recipients",
        sa.Column("quota_period_start", sa.Date(), nullable=True),
    )
    op.add_column(
        "whatsapp_campaign_recipients",
        sa.Column("quota_reserved_at", sa.DateTime(timezone=True), nullable=True),
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            UPDATE whatsapp_campaign_recipients
            SET quota_period_start = date_trunc('month', sent_at)::date,
                quota_reserved_at = sent_at
            WHERE sent_at IS NOT NULL
              AND quota_period_start IS NULL
            """
        )
        op.create_check_constraint(
            "ck_wa_campaign_recipients_quota_reservation_pair",
            "whatsapp_campaign_recipients",
            "(quota_period_start IS NULL) = (quota_reserved_at IS NULL)",
        )
    elif op.get_bind().dialect.name == "sqlite":
        op.execute(
            """
            UPDATE whatsapp_campaign_recipients
            SET quota_period_start = date(sent_at, 'start of month'),
                quota_reserved_at = sent_at
            WHERE sent_at IS NOT NULL
              AND quota_period_start IS NULL
            """
        )

    op.create_index(
        "ix_wa_campaign_recipients_workspace_quota_period",
        "whatsapp_campaign_recipients",
        ["workspace_id", "quota_period_start"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_wa_campaign_recipients_workspace_quota_period",
        table_name="whatsapp_campaign_recipients",
    )
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "ck_wa_campaign_recipients_quota_reservation_pair",
            "whatsapp_campaign_recipients",
            type_="check",
        )
    op.drop_column("whatsapp_campaign_recipients", "quota_reserved_at")
    op.drop_column("whatsapp_campaign_recipients", "quota_period_start")
