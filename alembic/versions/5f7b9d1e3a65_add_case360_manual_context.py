"""Add editable Case 360 metadata and manual-context revision.

Revision ID: 5f7b9d1e3a65
Revises: 4e6a8c0d2f54
Create Date: 2026-07-21 11:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON

revision: str = "5f7b9d1e3a65"
down_revision: str | Sequence[str] | None = "4e6a8c0d2f54"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("matters") as batch:
        batch.add_column(
            sa.Column("tags", FlexibleJSON(), server_default=sa.text("'[]'"), nullable=False)
        )
        batch.add_column(sa.Column("health_subtheme_custom", sa.String(length=160), nullable=True))
        batch.add_column(
            sa.Column("manual_context_revision", sa.Integer(), server_default="0", nullable=False)
        )
        batch.add_column(sa.Column("manual_context_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_matters_workspace_health_subtheme_custom",
        "matters",
        ["workspace_id", "health_subtheme_custom"],
    )


def downgrade() -> None:
    op.drop_index("ix_matters_workspace_health_subtheme_custom", table_name="matters")
    with op.batch_alter_table("matters") as batch:
        batch.drop_column("manual_context_updated_at")
        batch.drop_column("manual_context_revision")
        batch.drop_column("health_subtheme_custom")
        batch.drop_column("tags")
