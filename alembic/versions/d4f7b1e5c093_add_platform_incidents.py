"""add platform incidents

Revision ID: d4f7b1e5c093
Revises: c3e6a0d4b982
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4f7b1e5c093"
down_revision: str | Sequence[str] | None = "c3e6a0d4b982"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_incidents",
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), server_default="investigating", nullable=False),
        sa.Column("components", sa.JSON(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("updates", sa.JSON(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_platform_incident_status_severity",
        "platform_incidents",
        ["status", "severity"],
    )
    op.create_index("ix_platform_incident_started_at", "platform_incidents", ["started_at"])


def downgrade() -> None:
    op.drop_table("platform_incidents")
