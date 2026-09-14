"""Persist structured, versioned financial projection premises.

Revision ID: 97b8c9d0e1f2
Revises: 96a7b8c9d0e1
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "97b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "96a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "revenue_projections",
        sa.Column("projection_premises", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "revenue_projections",
        sa.Column("projection_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("revenue_projections", "projection_version")
    op.drop_column("revenue_projections", "projection_premises")
