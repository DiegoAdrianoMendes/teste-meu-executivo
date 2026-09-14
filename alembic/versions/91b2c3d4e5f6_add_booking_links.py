"""add public booking links

Revision ID: 91b2c3d4e5f6
Revises: 90a1b2c3d4e5
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "91b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "90a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "booking_links",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("responsible_user_id", FlexibleUUID(), nullable=True),
        sa.Column("public_key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), server_default="45", nullable=False),
        sa.Column(
            "meeting_provider", sa.String(length=40), server_default="manual", nullable=False
        ),
        sa.Column("meeting_url", sa.String(length=500), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("use_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("page_overrides", FlexibleJSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["responsible_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "public_key",
            name="uq_booking_links_workspace_public_key",
        ),
    )
    op.create_index("ix_booking_links_workspace_id", "booking_links", ["workspace_id"])
    op.create_index("ix_booking_links_matter_id", "booking_links", ["matter_id"])
    op.create_index(
        "ix_booking_links_responsible_user_id", "booking_links", ["responsible_user_id"]
    )
    op.create_index("ix_booking_links_public_key", "booking_links", ["public_key"])
    op.create_index("ix_booking_links_expires_at", "booking_links", ["expires_at"])
    op.create_index("ix_booking_links_is_active", "booking_links", ["is_active"])

    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            "workspace_id::text = NULLIF(current_setting('app.current_workspace_id', true), '')"
        )
        op.execute('ALTER TABLE "booking_links" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "booking_links" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"""
            CREATE POLICY "workspace_current_id" ON "booking_links"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute('DROP POLICY IF EXISTS "workspace_current_id" ON "booking_links"')
    op.drop_index("ix_booking_links_is_active", table_name="booking_links")
    op.drop_index("ix_booking_links_expires_at", table_name="booking_links")
    op.drop_index("ix_booking_links_public_key", table_name="booking_links")
    op.drop_index("ix_booking_links_responsible_user_id", table_name="booking_links")
    op.drop_index("ix_booking_links_matter_id", table_name="booking_links")
    op.drop_index("ix_booking_links_workspace_id", table_name="booking_links")
    op.drop_table("booking_links")
