"""Link matter tasks to flow runs with workspace idempotency.

Revision ID: xy6r9n2p3k75
Revises: wx5q8m1n2j64
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision = "xy6r9n2p3k75"
down_revision = "wx5q8m1n2j64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("flow_runs", sa.Column("task_id", FlexibleUUID(), nullable=True))
    op.add_column(
        "flow_runs",
        sa.Column("application_idempotency_key_hash", sa.String(64), nullable=True),
    )
    op.create_foreign_key(
        "fk_flow_runs_workspace_task",
        "flow_runs",
        "tasks",
        ["workspace_id", "task_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_flow_runs_workspace_application_idempotency",
        "flow_runs",
        ["workspace_id", "application_idempotency_key_hash"],
    )
    op.create_index(
        "ix_flow_runs_workspace_task_started",
        "flow_runs",
        ["workspace_id", "task_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_flow_runs_workspace_task_started", table_name="flow_runs")
    op.drop_constraint(
        "uq_flow_runs_workspace_application_idempotency", "flow_runs", type_="unique"
    )
    op.drop_constraint("fk_flow_runs_workspace_task", "flow_runs", type_="foreignkey")
    op.drop_column("flow_runs", "application_idempotency_key_hash")
    op.drop_column("flow_runs", "task_id")
