"""Add additive AgentRun V2 context and wait states."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "fg8b0c1d4e97"
down_revision: str | Sequence[str] | None = "ff7a9c1d3e86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for value in ("waiting_data", "waiting_tool", "waiting_external", "paused_budget"):
            op.execute(
                sa.text(f"ALTER TYPE agent_run_status_enum ADD VALUE IF NOT EXISTS '{value}'")
            )
    op.add_column("agent_runs", sa.Column("context_type", sa.String(40), nullable=True))
    op.add_column("agent_runs", sa.Column("context_id", FlexibleUUID(), nullable=True))
    op.create_index("ix_agent_runs_context_type", "agent_runs", ["context_type"])
    op.create_index("ix_agent_runs_context_id", "agent_runs", ["context_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_context_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_context_type", table_name="agent_runs")
    op.drop_column("agent_runs", "context_id")
    op.drop_column("agent_runs", "context_type")
