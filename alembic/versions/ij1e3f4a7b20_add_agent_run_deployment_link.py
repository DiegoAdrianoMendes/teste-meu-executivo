"""Link AgentRun to the published deployment that authorized it."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "ij1e3f4a7b20"
down_revision: str | Sequence[str] | None = "hi0d2e3f6a19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("deployment_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_runs_deployment_id",
        "agent_runs",
        "agent_deployments",
        ["deployment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_agent_runs_deployment_id", "agent_runs", ["deployment_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_deployment_id", table_name="agent_runs")
    op.drop_constraint("fk_agent_runs_deployment_id", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "deployment_id")
