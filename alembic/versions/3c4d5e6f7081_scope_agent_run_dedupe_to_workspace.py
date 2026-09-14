"""scope AgentRun idempotency keys to a workspace

Revision ID: 3c4d5e6f7081
Revises: 2b3c4d5e6f70
Create Date: 2026-07-16 18:28:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "3c4d5e6f7081"
down_revision: str | Sequence[str] | None = "2b3c4d5e6f70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_constraint("uq_agent_runs_dedupe_key", type_="unique")
        batch_op.create_unique_constraint(
            "uq_agent_runs_workspace_dedupe_key",
            ["workspace_id", "dedupe_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_constraint(
            "uq_agent_runs_workspace_dedupe_key",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_agent_runs_dedupe_key",
            ["dedupe_key"],
        )
