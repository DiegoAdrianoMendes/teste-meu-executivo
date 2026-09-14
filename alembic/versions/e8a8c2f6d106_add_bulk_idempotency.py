"""add bulk job idempotency key

Revision ID: e8a8c2f6d106
Revises: e7a8c2f6d105
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8a8c2f6d106"
down_revision: str | Sequence[str] | None = "e7a8c2f6d105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_bulk_jobs",
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
    )
    op.execute(
        "UPDATE platform_bulk_jobs "
        "SET idempotency_key = 'legacy:' || CAST(id AS VARCHAR) "
        "WHERE idempotency_key IS NULL"
    )
    op.alter_column("platform_bulk_jobs", "idempotency_key", nullable=False)
    op.create_unique_constraint(
        "uq_platform_bulk_job_actor_idempotency",
        "platform_bulk_jobs",
        ["actor_user_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_platform_bulk_job_actor_idempotency",
        "platform_bulk_jobs",
        type_="unique",
    )
    op.drop_column("platform_bulk_jobs", "idempotency_key")
