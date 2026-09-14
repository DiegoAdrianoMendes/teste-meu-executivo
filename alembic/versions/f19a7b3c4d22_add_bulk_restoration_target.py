"""add bulk restoration target snapshot

Revision ID: f19a7b3c4d22
Revises: eda8c2f6d111
Create Date: 2026-07-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON

revision: str = "f19a7b3c4d22"
down_revision: str | None = "eda8c2f6d111"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "platform_bulk_job_items",
        sa.Column("target", FlexibleJSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("platform_bulk_job_items", "target")
