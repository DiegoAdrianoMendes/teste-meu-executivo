"""
Schemas do painel global da plataforma.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core.password_policy import PasswordValue
from app.models.workspace import WorkspaceSetupMode, WorkspaceStatus
from app.schemas.base import CamelCaseSchema, WriteSchema
from app.schemas.email import CanonicalEmail


class PlanLimitSetRead(CamelCaseSchema):
    max_tokens_per_month: int | None = None
    max_ai_executions_per_month: int | None = None
    max_users: int | None = None
    max_workspaces: int | None = None
    max_connected_whatsapps: int | None = None
    max_ai_agents: int | None = None
    max_ai_conversations_per_month: int | None = None
    max_audio_minutes_per_month: int | None = None
    max_storage_bytes: int | None = None
    max_files: int | None = None
    max_files_per_conversation: int | None = None
    automation_limit: int | None = None
    max_operational_recipients: int | None = None
    max_campaign_recipients_per_month: int | None = None


class PlanLimitSetUpdate(WriteSchema):
    max_tokens_per_month: int | None = Field(default=None, ge=0)
    max_ai_executions_per_month: int | None = Field(default=None, ge=0)
    max_users: int | None = None
    max_workspaces: int | None = None
    max_connected_whatsapps: int | None = None
    max_ai_agents: int | None = None
    max_ai_conversations_per_month: int | None = None
    max_audio_minutes_per_month: int | None = None
    max_storage_bytes: int | None = Field(default=None, ge=0)
    max_files: int | None = Field(default=None, ge=0)
    max_files_per_conversation: int | None = None
    automation_limit: int | None = None
    max_operational_recipients: int | None = Field(default=None, ge=0)
    max_campaign_recipients_per_month: int | None = Field(default=None, ge=0)


class PlanFeatureSetRead(CamelCaseSchema):
    digital_signature: bool = False
    calendar: bool = False
    email: bool = False
    webhooks: bool = False
    advanced_audit: bool = False
    success_manager: bool = False
    support_tier: str = "standard"
    integrations: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)


class PlanFeatureSetUpdate(WriteSchema):
    digital_signature: bool = False
    calendar: bool = False
    email: bool = False
    webhooks: bool = False
    advanced_audit: bool = False
    success_manager: bool = False
    support_tier: str = "standard"
    integrations: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)


class WorkspacePlanCatalogItemRead(CamelCaseSchema):
    key: str
    name: str
    description: str
    recommended: bool = False
    limits: PlanLimitSetRead
    features: PlanFeatureSetRead
    is_active: bool = True


class WorkspacePlanCatalogItemUpdate(WriteSchema):
    name: str
    description: str
    recommended: bool = False
    limits: PlanLimitSetUpdate
    features: PlanFeatureSetUpdate
    is_active: bool = True


class PlatformWorkspaceOwnerRead(CamelCaseSchema):
    name: str | None = None
    email: str | None = None


class PlatformWorkspacePlanRead(CamelCaseSchema):
    """Compact commercial identity for large platform account lists.

    Usage and quota values intentionally live in Account 360; omitting them
    here prevents an unavailable aggregate from being serialized as zero.
    """

    tier: str | None = None
    name: str | None = None
    subscription_status: str | None = None


class PlatformWorkspaceRead(CamelCaseSchema):
    id: UUID
    name: str
    slug: str
    legal_name: str | None = None
    description: str | None = None
    status: WorkspaceStatus
    setup_mode: WorkspaceSetupMode
    is_active: bool
    onboarding_completed: bool
    onboarding_progress: int = 0
    next_onboarding_step: str | None = None
    owner: PlatformWorkspaceOwnerRead
    plan_summary: PlatformWorkspacePlanRead | None = None
    member_count: int = 0
    # `to_camel` produziria matterCount30D/aiRuns30D; o frontend lê a forma com
    # dígito minúsculo (S20). Mesmo precedente de `usage_count30d` nas campanhas.
    matter_count_30d: int = Field(default=0, alias="matterCount30d")
    ai_runs_30d: int = Field(default=0, alias="aiRuns30d")
    health_score: int = 0
    health_status: str = "critical"
    launch_notes: str | None = None


class PlatformWorkspacePageRead(CamelCaseSchema):
    items: list[PlatformWorkspaceRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict] = Field(default_factory=list)


class PlatformWorkspaceCreate(WriteSchema):
    name: str
    slug: str | None = None
    legal_name: str | None = None
    description: str | None = None
    setup_mode: WorkspaceSetupMode = WorkspaceSetupMode.assisted
    plan_key: str = "pro"
    owner_name: str | None = None
    owner_email: str | None = None
    owner_password: str | None = None
    launch_notes: str | None = None


class PlatformWorkspaceUpdate(WriteSchema):
    reason: str = Field(min_length=8, max_length=500)
    name: str | None = None
    slug: str | None = None
    legal_name: str | None = None
    description: str | None = None
    status: WorkspaceStatus | None = None
    is_active: bool | None = None
    onboarding_completed: bool | None = None
    plan_key: str | None = None
    launch_notes: str | None = None


class PlatformWorkspaceBulkAction(WriteSchema):
    workspace_ids: list[UUID] = Field(min_length=1, max_length=100)
    action: Literal[
        "suspend",
        "reactivate",
        "complete_onboarding",
        "reopen_onboarding",
        "set_plan",
    ]
    reason: str = Field(min_length=8, max_length=500)
    plan_key: str | None = Field(default=None, min_length=2, max_length=40)


class PlatformWorkspaceBulkActionItemRead(CamelCaseSchema):
    workspace_id: UUID
    success: bool
    status: str | None = None
    error: str | None = None


class PlatformWorkspaceBulkActionRead(CamelCaseSchema):
    action: str
    requested: int
    succeeded: int
    failed: int
    reason: str
    items: list[PlatformWorkspaceBulkActionItemRead] = Field(default_factory=list)


class PlatformPermissionRead(CamelCaseSchema):
    module: str
    action: str


class PlatformRolePackageRead(CamelCaseSchema):
    key: str
    label: str
    description: str
    permissions: list[PlatformPermissionRead] = Field(default_factory=list)
    assignable: bool = True
    requires_mfa: bool = True


class PlatformStaffCreate(WriteSchema):
    name: str = Field(min_length=2, max_length=200)
    email: CanonicalEmail
    role_package: str = Field(min_length=2, max_length=80)
    permission_grants: list[PlatformPermissionRead] = Field(default_factory=list)
    permission_denials: list[PlatformPermissionRead] = Field(default_factory=list)
    temporary_password: PasswordValue | None = None


class PlatformStaffUpdate(WriteSchema):
    reason: str = Field(min_length=8, max_length=500)
    name: str | None = Field(default=None, min_length=2, max_length=200)
    role_package: str | None = Field(default=None, min_length=2, max_length=80)
    access_active: bool | None = None
    permission_grants: list[PlatformPermissionRead] | None = None
    permission_denials: list[PlatformPermissionRead] | None = None


class PlatformStaffSessionRevoke(WriteSchema):
    reason: str = Field(min_length=8, max_length=500)


class PlatformTemporaryPasswordSet(WriteSchema):
    temporary_password: str = Field(min_length=5, max_length=72)
    reason: str = Field(min_length=8, max_length=500)


class PlatformTemporaryPasswordRead(CamelCaseSchema):
    user_id: UUID
    requires_password_change: Literal[True] = True
    revoked_session_count: int


class PlatformStaffRead(CamelCaseSchema):
    id: UUID
    name: str
    email: str
    initials: str
    role_package: str
    role_label: str
    access_active: bool
    is_platform_root: bool = False
    permission_grants: list[PlatformPermissionRead] = Field(default_factory=list)
    permission_denials: list[PlatformPermissionRead] = Field(default_factory=list)
    permissions: list[PlatformPermissionRead] = Field(default_factory=list)
    requires_password_setup: bool = False
    requires_password_change: bool = False
    password_setup_delivery_id: UUID | None = None
    active_session_count: int = 0
    last_login_at: datetime | None = None
    created_at: datetime


class PlatformStaffPageRead(CamelCaseSchema):
    items: list[PlatformStaffRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict] = Field(default_factory=list)


class BlackboxSessionManifestRead(CamelCaseSchema):
    user_id: UUID
    is_platform_root: bool = False
    role_package: str | None = None
    permissions: list[PlatformPermissionRead] = Field(default_factory=list)
    available_modules: list[str] = Field(default_factory=list)
    masked_fields: list[str] = Field(default_factory=list)
    support_session: dict | None = None
    feature_flags: dict[str, bool] = Field(default_factory=dict)
    frontend_version: str
    backend_version: str
