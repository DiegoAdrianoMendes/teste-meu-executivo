"""persist the resource type on bulk previews and jobs

Revision ID: eda8c2f6d111
Revises: eca8c2f6d110
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "eda8c2f6d111"
down_revision: str | Sequence[str] | None = "eca8c2f6d110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_bulk_previews",
        sa.Column("resource_type", sa.String(length=48), nullable=True),
    )
    op.add_column(
        "platform_bulk_jobs",
        sa.Column("resource_type", sa.String(length=48), nullable=True),
    )
    op.execute(
        """
        UPDATE platform_bulk_previews AS preview
           SET resource_type = selection.resource_type
          FROM platform_selection_sets AS selection
         WHERE selection.id = preview.selection_set_id
        """
    )
    op.execute(
        """
        UPDATE platform_bulk_jobs AS job
           SET resource_type = selection.resource_type
          FROM platform_selection_sets AS selection
         WHERE selection.id = job.selection_set_id
        """
    )
    op.alter_column("platform_bulk_previews", "resource_type", nullable=False)
    op.alter_column("platform_bulk_jobs", "resource_type", nullable=False)
    op.create_index(
        "ix_platform_bulk_previews_resource_created",
        "platform_bulk_previews",
        ["resource_type", "created_at"],
    )
    op.create_index(
        "ix_platform_bulk_jobs_resource_created",
        "platform_bulk_jobs",
        ["resource_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_platform_bulk_jobs_resource_created", table_name="platform_bulk_jobs")
    op.drop_index("ix_platform_bulk_previews_resource_created", table_name="platform_bulk_previews")
    op.drop_column("platform_bulk_jobs", "resource_type")
    op.drop_column("platform_bulk_previews", "resource_type")
