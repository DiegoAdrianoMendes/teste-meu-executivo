"""Typed contracts for Blackbox organization and operational workflows."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import CamelCaseSchema, WriteSchema


class PlatformTeamWrite(WriteSchema):
    name: str = Field(min_length=2, max_length=160)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,99}$")
    description: str | None = Field(default=None, max_length=1000)
    manager_user_id: UUID | None = None
    active: bool = True
    expected_version: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=8, max_length=500)


class PlatformTeamRead(CamelCaseSchema):
    id: UUID
    name: str
    slug: str
    description: str | None
    manager_user_id: UUID | None
    active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class PlatformTeamPageRead(CamelCaseSchema):
    items: list[PlatformTeamRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict] = Field(default_factory=list)


class PlatformTeamMembershipWrite(WriteSchema):
    user_id: UUID
    is_primary: bool = False
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    reason: str = Field(min_length=8, max_length=500)


class PlatformWorkspaceAssignmentWrite(WriteSchema):
    assignment_type: str = Field(pattern=r"^(onboarding|customer_success|billing|support)$")
    assigned_user_id: UUID | None = None
    assigned_team_id: UUID | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    expected_version: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def target_required(self):
        if self.assigned_user_id is None and self.assigned_team_id is None:
            raise ValueError("Informe a pessoa ou equipe responsável.")
        if self.assigned_user_id is not None and self.assigned_team_id is not None:
            raise ValueError("Informe uma pessoa ou uma equipe, não ambas.")
        return self


class PlatformWorkspaceAssignmentRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    assignment_type: str
    assigned_user_id: UUID | None
    assigned_team_id: UUID | None
    starts_at: datetime
    ends_at: datetime | None
    version: int


class PlatformWorkspaceAssignmentPageRead(CamelCaseSchema):
    items: list[PlatformWorkspaceAssignmentRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict] = Field(default_factory=list)


class PlatformDashboardPreferenceWrite(WriteSchema):
    template_key: str = Field(min_length=3, max_length=160)
    template_version: int = Field(ge=1)
    preference_delta: dict = Field(default_factory=dict)
    etag: str | None = Field(default=None, max_length=64)


class PlatformDashboardPreferenceRead(CamelCaseSchema):
    template_key: str
    template_version: int
    preference_delta: dict
    etag: str


class PlatformStaffInvitationWrite(WriteSchema):
    email: str = Field(min_length=3, max_length=255)
    name: str = Field(min_length=2, max_length=200)
    role_package: str = Field(min_length=2, max_length=80)
    expires_in_days: int = Field(default=7, ge=1, le=30)
    reason: str = Field(min_length=8, max_length=500)


class PlatformStaffInvitationRead(CamelCaseSchema):
    id: UUID
    email: str
    name: str
    role_package: str
    status: str
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    last_sent_at: datetime | None
    send_count: int
    version: int
    created_at: datetime


class PlatformStaffInvitationPageRead(CamelCaseSchema):
    items: list[PlatformStaffInvitationRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict] = Field(default_factory=list)


class PlatformReasonWrite(WriteSchema):
    reason: str = Field(min_length=8, max_length=500)


class PlatformOnboardingMilestoneWrite(WriteSchema):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,79}$")
    label: str = Field(min_length=2, max_length=200)
    status: str = Field(pattern=r"^(pending|in_progress|blocked|completed|cancelled)$")
    owner_user_id: UUID | None = None
    due_at: datetime | None = None
    expected_version: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=8, max_length=500)


class PlatformOnboardingMilestoneRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    key: str
    label: str
    status: str
    owner_user_id: UUID | None
    due_at: datetime | None
    completed_at: datetime | None
    version: int


class PlatformOnboardingMilestonePageRead(CamelCaseSchema):
    items: list[PlatformOnboardingMilestoneRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict] = Field(default_factory=list)


class PlatformOperationalAssignmentWrite(WriteSchema):
    source_type: str = Field(pattern=r"^(billing|lead|ticket|publication|configuration)$")
    source_id: UUID
    owner_user_id: UUID | None = None
    team_id: UUID | None = None
    next_action: str | None = Field(default=None, max_length=240)
    next_action_at: datetime | None = None
    due_at: datetime | None = None
    state: str = Field(
        default="open",
        pattern=r"^(open|in_progress|waiting_external|waiting_customer|blocked|done|cancelled)$",
    )
    expected_version: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def target_is_unambiguous(self):
        if self.owner_user_id is not None and self.team_id is not None:
            raise ValueError("Informe uma pessoa ou uma equipe, não ambas.")
        return self


class PlatformOperationalAssignmentRead(CamelCaseSchema):
    id: UUID
    source_type: str
    source_id: UUID
    owner_user_id: UUID | None
    team_id: UUID | None
    next_action: str | None
    next_action_at: datetime | None
    due_at: datetime | None
    state: str
    version: int


class PlatformOperationalAssignmentPageRead(CamelCaseSchema):
    items: list[PlatformOperationalAssignmentRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict] = Field(default_factory=list)


class PlatformReviewRequestWrite(WriteSchema):
    source_type: str = Field(pattern=r"^(publication|configuration)$")
    source_id: UUID
    state: str = Field(
        pattern=r"^(draft|in_review|changes_requested|approved|published|publish_failed|cancelled)$"
    )
    reviewer_user_id: UUID | None = None
    review_due_at: datetime | None = None
    expected_version: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=8, max_length=500)


class PlatformReviewRequestRead(CamelCaseSchema):
    id: UUID
    source_type: str
    source_id: UUID
    state: str
    author_user_id: UUID | None
    reviewer_user_id: UUID | None
    review_due_at: datetime | None
    reviewed_at: datetime | None
    reason: str | None
    version: int


class PlatformReviewRequestPageRead(CamelCaseSchema):
    items: list[PlatformReviewRequestRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict] = Field(default_factory=list)
