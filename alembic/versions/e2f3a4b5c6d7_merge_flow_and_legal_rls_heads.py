"""merge flow revision and legal CRM RLS heads

Revision ID: e2f3a4b5c6d7
Revises: c2d3e4f5a6b7, d1e2f3a4b5c6
Create Date: 2026-07-16 17:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "e2f3a4b5c6d7"
down_revision: str | Sequence[str] | None = (
    "c2d3e4f5a6b7",
    "d1e2f3a4b5c6",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
