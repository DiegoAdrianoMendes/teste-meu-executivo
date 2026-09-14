"""add persisted platform bulk operations

Revision ID: e5a8c2f6d104
Revises: d4f7b1e5c093
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5a8c2f6d104"
down_revision: str | Sequence[str] | None = "d4f7b1e5c093"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "platform_selection_sets",
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("resource_type", sa.String(48), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("sort", sa.JSON(), nullable=False),
        sa.Column("excluded_ids", sa.JSON(), nullable=False),
        sa.Column("filter_hash", sa.String(64), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_platform_selection_sets_actor_expires",
        "platform_selection_sets",
        ["actor_user_id", "expires_at"],
    )
    op.create_index(
        "ix_platform_selection_sets_status_expires",
        "platform_selection_sets",
        ["status", "expires_at"],
    )

    op.create_table(
        "platform_selection_items",
        sa.Column("selection_set_id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("resource_version", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("initially_eligible", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["selection_set_id"], ["platform_selection_sets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "selection_set_id", "resource_id", name="uq_platform_selection_item_resource"
        ),
        sa.UniqueConstraint(
            "selection_set_id", "ordinal", name="uq_platform_selection_item_ordinal"
        ),
    )
    op.create_index(
        "ix_platform_selection_items_selection_ordinal",
        "platform_selection_items",
        ["selection_set_id", "ordinal"],
    )

    op.create_table(
        "platform_bulk_previews",
        sa.Column("selection_set_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("risk", sa.String(16), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("eligible_count", sa.Integer(), nullable=False),
        sa.Column("no_op_count", sa.Integer(), nullable=False),
        sa.Column("blocked_count", sa.Integer(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("impact_summary", sa.JSON(), nullable=False),
        sa.Column("sample", sa.JSON(), nullable=False),
        sa.Column("eligible_targets", sa.JSON(), nullable=False),
        sa.Column("confirmation_phrase", sa.String(160), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["selection_set_id"], ["platform_selection_sets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_platform_bulk_previews_actor_expires",
        "platform_bulk_previews",
        ["actor_user_id", "expires_at"],
    )
    op.create_index(
        "ix_platform_bulk_previews_selection", "platform_bulk_previews", ["selection_set_id"]
    )

    op.create_table(
        "platform_bulk_jobs",
        sa.Column("preview_id", sa.Uuid(), nullable=False),
        sa.Column("selection_set_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("risk", sa.String(16), nullable=False),
        sa.Column("requested_count", sa.Integer(), nullable=False),
        sa.Column("eligible_count", sa.Integer(), nullable=False),
        sa.Column("processed_count", sa.Integer(), nullable=False),
        sa.Column("succeeded_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("conflicted_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("cancelled_count", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("operation_id", sa.String(64), nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["preview_id"], ["platform_bulk_previews.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["selection_set_id"], ["platform_selection_sets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("correlation_id"),
        sa.UniqueConstraint("operation_id"),
    )
    op.create_index(
        "ix_platform_bulk_jobs_actor_created", "platform_bulk_jobs", ["actor_user_id", "created_at"]
    )
    op.create_index(
        "ix_platform_bulk_jobs_status_created", "platform_bulk_jobs", ["status", "created_at"]
    )

    op.create_table(
        "platform_bulk_job_items",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("resource_version", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["platform_bulk_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "resource_id", name="uq_platform_bulk_job_item_resource"),
        sa.UniqueConstraint("job_id", "ordinal", name="uq_platform_bulk_job_item_ordinal"),
    )
    op.create_index(
        "ix_platform_bulk_job_items_job_status",
        "platform_bulk_job_items",
        ["job_id", "status", "ordinal"],
    )


def downgrade() -> None:
    op.drop_table("platform_bulk_job_items")
    op.drop_table("platform_bulk_jobs")
    op.drop_table("platform_bulk_previews")
    op.drop_table("platform_selection_items")
    op.drop_table("platform_selection_sets")
