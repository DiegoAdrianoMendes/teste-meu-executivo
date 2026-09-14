"""track templates removed from Meta and local history

Revision ID: qs0l3h6j5e19
Revises: pr9k2g5i4d08
"""

import sqlalchemy as sa

from alembic import op

revision = "qs0l3h6j5e19"
down_revision = "pr9k2g5i4d08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_meta_templates",
        sa.Column("removed_at_source_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_whatsapp_meta_templates_removed_at_source_at",
        "whatsapp_meta_templates",
        ["removed_at_source_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_whatsapp_meta_templates_removed_at_source_at",
        table_name="whatsapp_meta_templates",
    )
    op.drop_column("whatsapp_meta_templates", "removed_at_source_at")
