"""add private administrative export jobs

Revision ID: eaa8c2f6d108
Revises: e9a8c2f6d107
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "eaa8c2f6d108"
down_revision: str | Sequence[str] | None = "e9a8c2f6d107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_export_jobs",
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("dataset", sa.String(length=32), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("columns", sa.JSON(), nullable=False),
        sa.Column("selection_set_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("processed_rows", sa.Integer(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["selection_set_id"], ["platform_selection_sets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_user_id", "idempotency_key", name="uq_platform_export_actor_idempotency"),
        sa.UniqueConstraint("correlation_id"),
    )
    op.create_index("ix_platform_export_actor_created", "platform_export_jobs", ["actor_user_id", "created_at"])
    op.create_index("ix_platform_export_status_created", "platform_export_jobs", ["status", "created_at"])
    op.create_index("ix_platform_export_expires", "platform_export_jobs", ["expires_at"])


def downgrade() -> None:
    op.drop_table("platform_export_jobs")
