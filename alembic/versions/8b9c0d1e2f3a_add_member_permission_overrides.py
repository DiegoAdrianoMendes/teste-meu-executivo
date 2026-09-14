"""add per-member permission overrides

Revision ID: 8b9c0d1e2f3a
Revises: 7a8b9c0d1e2f
Create Date: 2026-07-12 15:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.db.base import FlexibleJSON


revision: str = "8b9c0d1e2f3a"
down_revision: Union[str, Sequence[str], None] = "7a8b9c0d1e2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workspace_memberships",
        sa.Column("permission_grants", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column(
        "workspace_memberships",
        sa.Column("permission_denials", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("workspace_memberships", "permission_denials")
    op.drop_column("workspace_memberships", "permission_grants")
