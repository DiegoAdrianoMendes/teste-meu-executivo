"""add Blackbox platform work-item projection

Revision ID: e6a8c2f4d105
Revises: d4f7b1e5c093
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6a8c2f4d105"
down_revision: str | Sequence[str] | None = "d4f7b1e5c093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_work_items",
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_version", sa.String(96), nullable=False),
        sa.Column("dedupe_key", sa.String(320), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(2), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_user_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_team_id", sa.Uuid(), nullable=True),
        sa.Column("requester_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewer_user_id", sa.Uuid(), nullable=True),
        sa.Column("safe_title", sa.String(240), nullable=False),
        sa.Column("safe_summary", sa.Text(), nullable=False),
        sa.Column("href", sa.String(500), nullable=False),
        sa.Column("required_module", sa.String(80), nullable=False),
        sa.Column("required_action", sa.String(80), nullable=False),
        sa.Column("allowed_commands", sa.JSON(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("projected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("projection_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("payload_safe", sa.JSON(), nullable=False),
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
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewer_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_platform_work_items_dedupe_key"),
    )
    op.create_index(
        "ix_platform_work_items_state_priority_due",
        "platform_work_items",
        ["state", "priority", "due_at"],
    )
    op.create_index(
        "ix_platform_work_items_assignee_state_due",
        "platform_work_items",
        ["assigned_user_id", "state", "due_at"],
    )
    op.create_index(
        "ix_platform_work_items_team_state_due",
        "platform_work_items",
        ["assigned_team_id", "state", "due_at"],
    )
    op.create_index(
        "ix_platform_work_items_source",
        "platform_work_items",
        ["source_type", "source_id"],
    )
    op.create_index(
        "ix_platform_work_items_workspace_state",
        "platform_work_items",
        ["workspace_id", "state"],
    )
    op.create_index(
        "ix_platform_work_items_cursor",
        "platform_work_items",
        ["source_updated_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("platform_work_items")
