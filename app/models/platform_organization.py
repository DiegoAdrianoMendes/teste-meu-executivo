"""Organization, assignments and review workflow for Blackbox operations."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


class PlatformTeam(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_teams"
    __table_args__ = (UniqueConstraint("slug", name="uq_platform_team_slug"),)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    manager_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PlatformTeamMembership(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_team_memberships"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_platform_team_membership"),
        Index("ix_platform_team_membership_user_active", "user_id", "active"),
    )

    team_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_teams.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PlatformWorkspaceAssignment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_workspace_assignments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "assignment_type", name="uq_platform_workspace_assignment_type"),
        Index("ix_platform_workspace_assignment_user", "assigned_user_id", "assignment_type"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    assignment_type: Mapped[str] = mapped_column(String(40), nullable=False)
    assigned_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_team_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_teams.id", ondelete="SET NULL"), nullable=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PlatformDashboardPreference(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_dashboard_preferences"
    __table_args__ = (UniqueConstraint("user_id", name="uq_platform_dashboard_preference_user"),)

    user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    template_key: Mapped[str] = mapped_column(String(160), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    preference_delta: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    etag: Mapped[str] = mapped_column(String(64), nullable=False)


class PlatformStaffInvitation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_staff_invitations"
    __table_args__ = (
        Index("ix_platform_staff_invitation_status_expires", "status", "expires_at"),
        Index("ix_platform_staff_invitation_email", "email"),
    )

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role_package: Mapped[str] = mapped_column(String(80), nullable=False)
    invited_by_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    send_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PlatformOnboardingMilestone(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_onboarding_milestones"
    __table_args__ = (
        UniqueConstraint("workspace_id", "key", name="uq_platform_onboarding_milestone_key"),
        Index("ix_platform_onboarding_milestone_due", "status", "due_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    owner_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PlatformOperationalAssignment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_operational_assignments"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_platform_operational_assignment_source"),
        Index("ix_platform_operational_assignment_owner_due", "owner_user_id", "due_at"),
    )

    source_type: Mapped[str] = mapped_column(String(48), nullable=False)
    source_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    owner_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    team_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_teams.id", ondelete="SET NULL"), nullable=True
    )
    next_action: Mapped[str | None] = mapped_column(String(240), nullable=True)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PlatformReviewRequest(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_review_requests"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_platform_review_request_source"),
        Index("ix_platform_review_request_state_due", "state", "review_due_at"),
    )

    source_type: Mapped[str] = mapped_column(String(48), nullable=False)
    source_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    author_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    review_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
