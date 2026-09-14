"""Allow AgentRun V2 contexts that are not tied to a matter."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "ij1e4f5a7b20"
down_revision: str | Sequence[str] | None = "hi0d2e3f6a19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("agent_runs", "matter_id", nullable=True)


def downgrade() -> None:
    # A downgrade cannot silently invent a legal matter for platform, finance,
    # workspace, client, contract, conversation or task scoped runs. Fail
    # explicitly when such V2 rows exist, then restore the legacy invariant.
    connection = op.get_bind()
    orphan_count = connection.exec_driver_sql(
        "SELECT COUNT(*) FROM agent_runs WHERE matter_id IS NULL"
    ).scalar_one()
    if orphan_count:
        raise RuntimeError(
            "Cannot downgrade AgentRun V2 while non-matter executions exist"
        )
    op.alter_column("agent_runs", "matter_id", nullable=False)
