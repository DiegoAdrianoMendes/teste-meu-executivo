"""add durable inbound WhatsApp pipeline checkpoint

Revision ID: 5e6f708192a3
Revises: 4d5e6f708192
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5e6f708192a3"
down_revision: str | None = "4d5e6f708192"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.add_column(
        "whatsapp_messages",
        sa.Column(
            "inbound_pipeline",
            json_type,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_messages", "inbound_pipeline")
