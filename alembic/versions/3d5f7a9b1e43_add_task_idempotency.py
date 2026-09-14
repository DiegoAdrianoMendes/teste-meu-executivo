"""Add idempotent task creation for one-click inbox actions.

Revision ID: 3d5f7a9b1e43
Revises: 2c4e6f8a0d32
Create Date: 2026-07-21 08:35:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3d5f7a9b1e43"
down_revision: str | Sequence[str] | None = "2c4e6f8a0d32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("idempotency_key", sa.String(length=180), nullable=True))
    op.add_column("tasks", sa.Column("request_sha256", sa.String(length=64), nullable=True))
    op.create_index(
        "ux_tasks_matter_idempotency",
        "tasks",
        ["matter_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_tasks_matter_idempotency", table_name="tasks")
    op.drop_column("tasks", "request_sha256")
    op.drop_column("tasks", "idempotency_key")
