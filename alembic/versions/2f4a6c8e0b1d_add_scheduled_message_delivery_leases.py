"""add scheduled message delivery leases

Revision ID: 2f4a6c8e0b1d
Revises: 1a2b3c4d5e6f
Create Date: 2026-07-16 18:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2f4a6c8e0b1d"
down_revision: str | Sequence[str] | None = "1a2b3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scheduled_messages",
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "scheduled_messages",
        sa.Column("lease_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "scheduled_messages",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "scheduled_messages",
        sa.Column("delivery_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "scheduled_messages",
        sa.Column(
            "delivery_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "scheduled_messages",
        sa.Column(
            "provider_supports_idempotency",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "scheduled_messages",
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "scheduled_messages",
        sa.Column("delivery_uncertain_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_scheduled_messages_processing_lease",
        "scheduled_messages",
        ["status", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ux_scheduled_messages_idempotency_key",
        "scheduled_messages",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ux_scheduled_messages_idempotency_key",
        table_name="scheduled_messages",
    )
    op.drop_index(
        "ix_scheduled_messages_processing_lease",
        table_name="scheduled_messages",
    )
    op.drop_column("scheduled_messages", "delivery_uncertain_at")
    op.drop_column("scheduled_messages", "provider_message_id")
    op.drop_column("scheduled_messages", "provider_supports_idempotency")
    op.drop_column("scheduled_messages", "delivery_attempt_count")
    op.drop_column("scheduled_messages", "delivery_attempted_at")
    op.drop_column("scheduled_messages", "lease_expires_at")
    op.drop_column("scheduled_messages", "lease_token")
    op.drop_column("scheduled_messages", "idempotency_key")
