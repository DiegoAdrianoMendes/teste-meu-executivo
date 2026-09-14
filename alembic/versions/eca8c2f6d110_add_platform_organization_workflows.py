"""add platform organization and workflow foundations

Revision ID: eca8c2f6d110
Revises: eba8c2f6d109
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "eca8c2f6d110"
down_revision: str | Sequence[str] | None = "eba8c2f6d109"
branch_labels = None
depends_on = None


def _identity_columns():
    return (
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
    )


def upgrade() -> None:
    op.create_table(
        "platform_teams",
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("manager_user_id", sa.Uuid(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["manager_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_platform_team_slug"),
    )
    op.create_table(
        "platform_team_memberships",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["team_id"], ["platform_teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "user_id", name="uq_platform_team_membership"),
    )
    op.create_index(
        "ix_platform_team_membership_user_active",
        "platform_team_memberships",
        ["user_id", "active"],
    )
    op.create_table(
        "platform_workspace_assignments",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_type", sa.String(40), nullable=False),
        sa.Column("assigned_user_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_team_id", sa.Uuid(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_team_id"], ["platform_teams.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "assignment_type", name="uq_platform_workspace_assignment_type"
        ),
    )
    op.create_index(
        "ix_platform_workspace_assignment_user",
        "platform_workspace_assignments",
        ["assigned_user_id", "assignment_type"],
    )
    op.create_table(
        "platform_dashboard_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("template_key", sa.String(160), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("preference_delta", sa.JSON(), nullable=False),
        sa.Column("etag", sa.String(64), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_platform_dashboard_preference_user"),
    )
    op.create_table(
        "platform_staff_invitations",
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role_package", sa.String(80), nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("send_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_platform_staff_invitation_status_expires",
        "platform_staff_invitations",
        ["status", "expires_at"],
    )
    op.create_index("ix_platform_staff_invitation_email", "platform_staff_invitations", ["email"])
    op.create_table(
        "platform_onboarding_milestones",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "key", name="uq_platform_onboarding_milestone_key"),
    )
    op.create_index(
        "ix_platform_onboarding_milestone_due",
        "platform_onboarding_milestones",
        ["status", "due_at"],
    )
    op.create_table(
        "platform_operational_assignments",
        sa.Column("source_type", sa.String(48), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("next_action", sa.String(240), nullable=True),
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["team_id"], ["platform_teams.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type", "source_id", name="uq_platform_operational_assignment_source"
        ),
    )
    op.create_index(
        "ix_platform_operational_assignment_owner_due",
        "platform_operational_assignments",
        ["owner_user_id", "due_at"],
    )
    op.create_table(
        "platform_review_requests",
        sa.Column("source_type", sa.String(48), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewer_user_id", sa.Uuid(), nullable=True),
        sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewer_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_type", "source_id", name="uq_platform_review_request_source"),
    )
    op.create_index(
        "ix_platform_review_request_state_due",
        "platform_review_requests",
        ["state", "review_due_at"],
    )
    op.execute(
        sa.text(
            "UPDATE platform_work_items "
            "SET source_type = 'workspace_invitation' "
            "WHERE source_type = 'invitation'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE platform_work_items "
            "SET source_type = 'invitation' "
            "WHERE source_type = 'workspace_invitation'"
        )
    )
    for table in [
        "platform_review_requests",
        "platform_operational_assignments",
        "platform_onboarding_milestones",
        "platform_staff_invitations",
        "platform_dashboard_preferences",
        "platform_workspace_assignments",
        "platform_team_memberships",
        "platform_teams",
    ]:
        op.drop_table(table)
