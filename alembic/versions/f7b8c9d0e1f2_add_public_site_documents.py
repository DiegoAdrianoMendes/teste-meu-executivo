"""add public site documents

Revision ID: f7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-05 18:20:00.000000
"""
from alembic import op
import sqlalchemy as sa

from app.db.base import FlexibleUUID


# revision identifiers, used by Alembic.
revision = "f7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "public_site_documents",
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("draft_content", sa.JSON(), nullable=False),
        sa.Column("published_content", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("published_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_public_site_documents_key"), "public_site_documents", ["key"], unique=True)
    op.create_index(op.f("ix_public_site_documents_published_by_user_id"), "public_site_documents", ["published_by_user_id"], unique=False)
    op.create_index(op.f("ix_public_site_documents_updated_by_user_id"), "public_site_documents", ["updated_by_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_public_site_documents_updated_by_user_id"), table_name="public_site_documents")
    op.drop_index(op.f("ix_public_site_documents_published_by_user_id"), table_name="public_site_documents")
    op.drop_index(op.f("ix_public_site_documents_key"), table_name="public_site_documents")
    op.drop_table("public_site_documents")
