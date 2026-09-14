"""add contextual restoration previews and jobs

Revision ID: eba8c2f6d109
Revises: eaa8c2f6d108
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "eba8c2f6d109"
down_revision: str | Sequence[str] | None = "eaa8c2f6d108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_restoration_previews",
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("source_audit_event_id", sa.Uuid(), nullable=True),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=160), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("current_hash", sa.String(length=64), nullable=False),
        sa.Column("target_version", sa.Integer(), nullable=True),
        sa.Column("target_hash", sa.String(length=64), nullable=False),
        sa.Column("target_snapshot", sa.JSON(), nullable=False),
        sa.Column("diff", sa.JSON(), nullable=False),
        sa.Column("impact", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("ineligible_reasons", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_audit_event_id"], ["platform_audit_events.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_platform_restore_preview_actor_expires", "platform_restoration_previews", ["actor_user_id", "expires_at"])
    op.create_index("ix_platform_restore_preview_resource", "platform_restoration_previews", ["resource_type", "resource_id"])
    op.create_table(
        "platform_restoration_jobs",
        sa.Column("preview_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["preview_id"], ["platform_restoration_previews.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_user_id", "idempotency_key", name="uq_platform_restore_actor_idempotency"),
        sa.UniqueConstraint("correlation_id"),
    )
    op.create_index("ix_platform_restore_job_status_created", "platform_restoration_jobs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("platform_restoration_jobs")
    op.drop_table("platform_restoration_previews")
