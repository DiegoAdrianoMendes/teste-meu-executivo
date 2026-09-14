"""add canonical platform releases and support evidence foreign keys

Revision ID: e9a8c2f6d107
Revises: e8a8c2f6d106
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9a8c2f6d107"
down_revision: str | Sequence[str] | None = "e8a8c2f6d106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_releases",
        sa.Column("version", sa.String(length=120), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("previous_release_id", sa.Uuid(), nullable=True),
        sa.Column("rollback_target_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["previous_release_id"], ["platform_releases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["rollback_target_id"], ["platform_releases.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("environment", "version", name="uq_platform_release_environment_version"),
    )
    op.create_index("ix_platform_releases_environment_deployed", "platform_releases", ["environment", "deployed_at"])
    op.create_index("ix_platform_releases_status_deployed", "platform_releases", ["status", "deployed_at"])
    op.create_foreign_key(
        "fk_support_ticket_incident",
        "support_tickets",
        "platform_incidents",
        ["linked_incident_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_support_ticket_release",
        "support_tickets",
        "platform_releases",
        ["linked_release_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_support_ticket_release", "support_tickets", type_="foreignkey")
    op.drop_constraint("fk_support_ticket_incident", "support_tickets", type_="foreignkey")
    op.drop_table("platform_releases")
