"""add monotonic revision to blog posts

Revision ID: f2ab4c6d8e30
Revises: f19a7b3c4d22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2ab4c6d8e30"
down_revision: str | None = "f19a7b3c4d22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "blog_posts",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_check_constraint(
        "ck_blog_posts_revision_positive",
        "blog_posts",
        "revision > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_blog_posts_revision_positive", "blog_posts", type_="check"
    )
    op.drop_column("blog_posts", "revision")
