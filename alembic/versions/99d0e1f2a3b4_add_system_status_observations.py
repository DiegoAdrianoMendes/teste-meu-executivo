"""Persist real system status observations.

Revision ID: 99d0e1f2a3b4
Revises: 98c9d0e1f2a3
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "99d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "98c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_status_observations",
        sa.Column("overall_status", sa.String(length=32), nullable=False),
        sa.Column(
            "components", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_system_status_observed_at", "system_status_observations", ["observed_at"]
    )
    op.create_index(
        "ix_system_status_overall_observed",
        "system_status_observations",
        ["overall_status", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_system_status_overall_observed", table_name="system_status_observations"
    )
    op.drop_index("ix_system_status_observed_at", table_name="system_status_observations")
    op.drop_table("system_status_observations")
