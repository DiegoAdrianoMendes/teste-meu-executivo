"""allow workspace-scoped knowledge chunks without a matter

Revision ID: no6h9d2f1a75
Revises: mn5h8c1e0f64
Create Date: 2026-08-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "no6h9d2f1a75"
down_revision: str | None = "mn5h8c1e0f64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("knowledge_chunks", "matter_id", nullable=True)


def downgrade() -> None:
    missing = op.get_bind().exec_driver_sql(
        "SELECT COUNT(*) FROM knowledge_chunks WHERE matter_id IS NULL"
    ).scalar_one()
    if missing:
        raise RuntimeError(
            "cannot restore required knowledge_chunks.matter_id while "
            f"{missing} workspace-scoped chunk(s) exist"
        )
    op.alter_column("knowledge_chunks", "matter_id", nullable=False)
