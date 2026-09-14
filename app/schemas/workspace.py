"""
Schemas do domínio de tenancy/workspaces.
"""
from datetime import datetime
from typing import Dict, List, Literal, Optional
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from app.core.password_policy import PasswordValue
from app.models.workspace import WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceRole
from app.schemas.agent_policy import AutomationPreset
from app.schemas.base import CamelCaseSchema, WriteSchema
from app.schemas.control_plane import (
    WorkspaceNotificationRecipientRead,
    WorkspaceNotificationRecipientUpdate,
)
from app.schemas.email import CanonicalEmail
from app.services.whatsapp.helpers import normalize_phone_number


class PermissionRead(CamelCaseSchema):
    module: str
    action: str
    scope: str


class PermissionWrite(WriteSchema):
    module: str = Field(min_length=1, max_length=80)
    action: str = Field(min_length=1, max_length=80)
    scope: Literal["workspace", "all"] = "workspace"


class PlatformPermissionContextRead(CamelCaseSchema):
    """Platform RBAC is module/action based and has no tenant scope field."""

    module: str
    action: str


class WorkspacePlanSummaryRead(CamelCaseSchema):
    tier: Optional[str] = None
    name: Optional[str] = None
    subscription_status: Optional[str] = None
    max_users: Optional[int] = None
    max_workspaces: Optional[int] = None
    max_connected_whatsapps: Optional[int] = None
    max_ai_agents: Optional[int] = None
    max_legal_areas: Optional[int] = None
    max_ai_conversations_per_month: Optional[int] = None
    max_audio_minutes_per_month: Optional[int] = None
    max_storage_bytes: Optional[int] = None
    max_files: Optional[int] = None
    max_files_per_conversation: Optional[int] = None
    automation_limit: Optional[int] = None
    max_operational_recipients: Optional[int] = None
    max_campaign_recipients_per_month: Optional[int] = None
    seats_used: int = 0
    seats_remaining: Optional[int] = None
    connected_whatsapps_used: int = 0
    connected_whatsapps_remaining: Optional[int] = None
    ai_agents_used: int = 0
    ai_agents_remaining: Optional[int] = None
    legal_areas_used: int = 0
    legal_areas_remaining: Optional[int] = None
    ai_conversations_used_this_month: int = 0
    ai_conversations_remaining_this_month: Optional[int] = None
    audio_minutes_used_this_month: int = 0
    audio_minutes_remaining_this_month: Optional[int] = None
    storage_bytes_used: int = 0
    storage_bytes_remaining: Optional[int] = None
    files_used: int = 0
    files_remaining: Optional[int] = None
    automations_used: int = 0
    automations_remaining: Optional[int] = None
    operational_recipients_used: int = 0
    operational_recipients_remaining: Optional[int] = None
    campaign_recipients_used_this_month: int = 0
    campaign_recipients_remaining_this_month: Optional[int] = None
    # Módulo processual contratável à parte (add-on, não core) — 0/None em
    # todo plano base; ligado por override de conta ou plano dedicado (ver
    # app/services/process_monitoring_service.py).
    max_monitored_processes: Optional[int] = None
    monitored_processes_used: int = 0
    monitored_processes_remaining: Optional[int] = None
    support_tier: Optional[str] = None
    has_digital_signature: bool = False
    has_calendar: bool = False
    has_email: bool = False
    has_webhooks: bool = False
    has_advanced_audit: bool = False
    has_success_manager: bool = False
    is_at_limit: bool = False


class WorkspaceRead(CamelCaseSchema):
    id: UUID
    name: str
    slug: str
    legal_name: Optional[str] = None
    description: Optional[str] = None
    status: WorkspaceStatus
    setup_mode: WorkspaceSetupMode
    is_active: bool
    settings: dict = Field(default_factory=dict)
    plan_summary: Optional[WorkspacePlanSummaryRead] = None


class WorkspaceSummary(CamelCaseSchema):
    id: UUID
    name: str
    slug: str
    status: WorkspaceStatus
    is_active: bool
    onboarding_completed: bool = False
    onboarding_progress: int = 0
    next_onboarding_step: Optional[str] = None
    plan_summary: Optional[WorkspacePlanSummaryRead] = None


class WorkspaceMembershipRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    user_id: UUID
    role: WorkspaceRole
    status: MembershipStatus
    is_default: bool
    access_package: Optional[str] = None
    workspace: WorkspaceSummary
    role_permissions: List[PermissionRead] = Field(default_factory=list)
    permission_grants: List[PermissionRead] = Field(default_factory=list)
    permission_denials: List[PermissionRead] = Field(default_factory=list)
    permissions: List[PermissionRead] = Field(default_factory=list)
    can_view_sensitive_lead_data: bool = False
    can_edit_sensitive_lead_data: bool = False


class SessionContextRead(CamelCaseSchema):
    surface: Literal["workspace", "platform", "support"]
    active_workspace: Optional[WorkspaceSummary] = None
    available_workspaces: List[WorkspaceMembershipRead] = Field(default_factory=list)
    permissions: List[PermissionRead] = Field(default_factory=list)
    is_internal_staff: bool = False
    is_platform_root: bool = False
    platform_role_package: Optional[str] = None
    platform_permissions: List[PlatformPermissionContextRead] = Field(default_factory=list)
    active_role: Optional[WorkspaceRole] = None


OnboardingStepId = Literal[
    "welcome",
    "whatsapp",
    "areas",
    "template",
    "follow_up",
    "playbook",
    "integrations",
    "review",
]

IntegrationSetupStatus = Literal["not_started", "connected", "skipped"]


class WorkspaceIntegrationSetupStatusRead(CamelCaseSchema):
    key: str
    label: str
    description: str
    required: bool = False
    status: IntegrationSetupStatus = "not_started"


class WorkspaceIntegrationSetupStatusUpdate(WriteSchema):
    key: str
    status: IntegrationSetupStatus


class OnboardingStepStatusRead(CamelCaseSchema):
    id: OnboardingStepId
    title: str
    description: str
    required: bool = True
    completed: bool = False
    skipped: bool = False
    blocked: bool = False
    status: str = "pending"


class WorkspaceOnboardingStateRead(CamelCaseSchema):
    current_step: OnboardingStepId
    onboarding_completed: bool = False
    onboarding_progress: int = 0
    next_onboarding_step: Optional[OnboardingStepId] = None
    pending_required_steps: List[OnboardingStepId] = Field(default_factory=list)
    completed_steps: List[OnboardingStepId] = Field(default_factory=list)
    skipped_steps: List[OnboardingStepId] = Field(default_factory=list)
    review_completed: bool = False
    playbook_id: Optional[UUID] = None
    integrations: List[WorkspaceIntegrationSetupStatusRead] = Field(default_factory=list)
    steps: List[OnboardingStepStatusRead] = Field(default_factory=list)
    area_activations: List[Dict[str, object]] = Field(default_factory=list)
    notification_recipients: List[WorkspaceNotificationRecipientRead] = Field(default_factory=list)


class WorkspaceOnboardingUpdate(WriteSchema):
    current_step: Optional[OnboardingStepId] = None
    mark_step_completed: Optional[OnboardingStepId] = None
    skip_step: Optional[OnboardingStepId] = None
    integrations: Optional[List[WorkspaceIntegrationSetupStatusUpdate]] = None
    review_completed: Optional[bool] = None
    playbook_id: Optional[UUID] = None
    area_activations: Optional[List[Dict[str, object]]] = None
    notification_recipients: Optional[List[WorkspaceNotificationRecipientUpdate]] = None


class WorkspaceOwnerChannelRead(CamelCaseSchema):
    """Configuração do canal do dono (notificações de rotina do Hermes)."""

    owner_whatsapp_phone: Optional[str] = None
    configured: bool = False


class WorkspaceOwnerChannelUpdate(WriteSchema):
    """Atualiza o WhatsApp do dono. Enviar null/vazio remove a configuração."""

    owner_whatsapp_phone: Optional[str] = Field(default=None, max_length=32)

    @field_validator("owner_whatsapp_phone")
    @classmethod
    def _normalize_owner_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not value.strip():
            return None
        normalized = normalize_phone_number(value)
        if normalized is None:
            raise ValueError(
                "Telefone do dono inválido: informe DDI, DDD e número. Ex.: 5561999999999."
            )
        return normalized


class WorkspaceSetupRequest(WriteSchema):
    workspace_name: str
    workspace_slug: Optional[str] = None
    legal_name: Optional[str] = None
    admin_name: str
    admin_email: str
    admin_password: PasswordValue
    setup_token: Optional[str] = None
    mode: WorkspaceSetupMode = WorkspaceSetupMode.self_service
    automation_preset: Optional[AutomationPreset] = None


class WorkspaceSetupResponse(CamelCaseSchema):
    workspace: WorkspaceRead
    membership: WorkspaceMembershipRead


class WorkspaceSelectRequest(WriteSchema):
    workspace_id: UUID


class WebSocketTicketRead(CamelCaseSchema):
    ticket: str
    expires_in: int


class WorkspaceCreate(WriteSchema):
    name: str
    slug: Optional[str] = None
    legal_name: Optional[str] = None
    description: Optional[str] = None
    setup_mode: WorkspaceSetupMode = WorkspaceSetupMode.assisted
    settings: dict = Field(default_factory=dict)


class WorkspaceUpdate(WriteSchema):
    name: Optional[str] = None
    slug: Optional[str] = None
    legal_name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[WorkspaceStatus] = None
    is_active: Optional[bool] = None
    settings: Optional[dict] = None


class MembershipCreate(WriteSchema):
    user_id: UUID
    role: WorkspaceRole
    status: MembershipStatus = MembershipStatus.active
    is_default: bool = False


class MembershipUpdate(WriteSchema):
    role: Optional[WorkspaceRole] = None
    status: Optional[MembershipStatus] = None
    is_default: Optional[bool] = None


class TeamMemberCreate(WriteSchema):
    model_config = ConfigDict(
        alias_generator=WriteSchema.model_config["alias_generator"],
        populate_by_name=True,
        protected_namespaces=(),
        extra="forbid",
    )

    name: str = Field(min_length=2, max_length=200)
    email: CanonicalEmail
    workspace_role: WorkspaceRole = WorkspaceRole.operator
    access_package: Optional[str] = Field(default=None, min_length=2, max_length=80)
    permission_grants: List[PermissionWrite] = Field(default_factory=list)
    permission_denials: List[PermissionWrite] = Field(default_factory=list)


class TeamMemberUpdate(WriteSchema):
    model_config = ConfigDict(
        alias_generator=WriteSchema.model_config["alias_generator"],
        populate_by_name=True,
        protected_namespaces=(),
        extra="forbid",
    )

    workspace_role: Optional[WorkspaceRole] = None
    access_package: Optional[str] = Field(default=None, min_length=2, max_length=80)
    status: Optional[MembershipStatus] = None
    permission_grants: Optional[List[PermissionWrite]] = None
    permission_denials: Optional[List[PermissionWrite]] = None


class TeamMemberRead(CamelCaseSchema):
    membership_id: UUID
    user_id: UUID
    name: str
    email: str
    phone: Optional[str] = None
    initials: str
    color: str
    avatar: Optional[str] = None
    workspace_role: WorkspaceRole
    access_package: Optional[str] = None
    status: MembershipStatus
    is_active: bool
    role_permissions: List[PermissionRead] = Field(default_factory=list)
    permission_grants: List[PermissionRead] = Field(default_factory=list)
    permission_denials: List[PermissionRead] = Field(default_factory=list)
    permissions: List[PermissionRead] = Field(default_factory=list)
    can_view_sensitive_lead_data: bool = False
    can_edit_sensitive_lead_data: bool = False
    created_at: datetime
    invitation_id: Optional[UUID] = None
    invitation_status: Optional[str] = None
    invitation_expires_at: Optional[datetime] = None
    invitation_delivery_id: Optional[UUID] = None
    invitation_delivery_status: str | None = None
    invitation_delivery_error_code: str | None = None


class RoleDefinitionRead(CamelCaseSchema):
    id: WorkspaceRole
    label: str
    description: str
    permissions: List[PermissionRead]


class WorkspaceAccessPackageRead(CamelCaseSchema):
    key: str
    label: str
    description: str
    workspace_role: WorkspaceRole
    permission_grants: List[PermissionRead] = Field(default_factory=list)
    permission_denials: List[PermissionRead] = Field(default_factory=list)
    permissions: List[PermissionRead] = Field(default_factory=list)
    sensitive_data_policy: str
