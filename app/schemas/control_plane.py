"""
Schemas do control plane administrativo e da operação simplificada do workspace.
"""
from datetime import UTC, datetime
from enum import Enum
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.meeting import (
    MeetingEventType,
    MeetingNature,
    MeetingNotificationStatus,
    MeetingParticipantRole,
    MeetingParticipantRsvp,
    MeetingStatus,
)
from app.schemas.agent_policy import AutomationStageMode
from app.schemas.base import CamelCaseSchema, WriteSchema


class ModelTier(str, Enum):
    simple = "simple"
    medium = "medium"
    complex = "complex"
    premium = "premium"


class FollowUpProfile(str, Enum):
    gentle = "gentle"
    balanced = "balanced"
    assertive = "assertive"


class FlowTemplateReferenceRead(CamelCaseSchema):
    id: str
    name: str
    description: Optional[str] = None
    practice_area: str
    trigger: str
    is_active: bool
    node_count: int
    prompt_count: int


class PracticeAreaStagePromptRead(CamelCaseSchema):
    stage_key: str
    title: str
    prompt: str


class PracticeAreaStagePromptUpdate(WriteSchema):
    stage_key: str
    title: str
    prompt: str


class PracticeAreaTemplateRead(CamelCaseSchema):
    model_config = ConfigDict(protected_namespaces=())

    key: str
    label: str
    description: str
    flow_practice_area: str
    trigger_summary: str
    base_prompt: str
    default_follow_up_profile: FollowUpProfile
    human_intervention_mode: AutomationStageMode
    default_agent_key: str
    model_tier: ModelTier
    checklist: List[str] = Field(default_factory=list)
    allowed_flow_template_ids: List[str] = Field(default_factory=list)
    stage_prompts: List[PracticeAreaStagePromptRead] = Field(default_factory=list)
    available_flows: List[FlowTemplateReferenceRead] = Field(default_factory=list)


class PracticeAreaTemplateUpdate(WriteSchema):
    model_config = ConfigDict(protected_namespaces=())

    key: str
    label: str
    description: str
    flow_practice_area: str
    trigger_summary: str
    base_prompt: str
    default_follow_up_profile: FollowUpProfile
    human_intervention_mode: AutomationStageMode
    default_agent_key: str
    model_tier: ModelTier
    checklist: List[str] = Field(default_factory=list)
    allowed_flow_template_ids: List[str] = Field(default_factory=list)
    stage_prompts: List[PracticeAreaStagePromptUpdate] = Field(default_factory=list)


class ModelCatalogItemRead(CamelCaseSchema):
    key: str
    label: str
    provider: str
    tier: ModelTier
    description: str
    experimental: bool = False


class AgentPromptPolicyRead(CamelCaseSchema):
    agent_key: str
    label: str
    prompt: str


class AgentPromptPolicyUpdate(WriteSchema):
    agent_key: str
    label: str
    prompt: str


class AiGovernanceRead(CamelCaseSchema):
    gemma_enabled: bool = True
    simple_model: str
    medium_model: str
    complex_model: str
    premium_model: str
    available_models: List[ModelCatalogItemRead] = Field(default_factory=list)
    agent_prompts: List[AgentPromptPolicyRead] = Field(default_factory=list)


class AiGovernanceUpdate(WriteSchema):
    gemma_enabled: bool = True
    simple_model: str
    medium_model: str
    complex_model: str
    premium_model: str
    agent_prompts: List[AgentPromptPolicyUpdate] = Field(default_factory=list)


class WorkspaceNotificationRecipientRead(CamelCaseSchema):
    id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    role: str = "custom"
    user_id: Optional[UUID] = None
    locked: bool = False


class WorkspaceNotificationRecipientUpdate(WriteSchema):
    id: Optional[str] = None
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    role: str = "custom"
    user_id: Optional[UUID] = None


class WorkspaceNotificationRuleRead(CamelCaseSchema):
    key: str
    label: str
    description: str
    enabled: bool = True
    email_enabled: bool = True
    whatsapp_enabled: bool = True
    reminder_minutes_before: Optional[int] = None


class WorkspaceNotificationRuleUpdate(WriteSchema):
    key: str
    enabled: bool = True
    email_enabled: bool = True
    whatsapp_enabled: bool = True
    reminder_minutes_before: Optional[int] = None


class NotificationSettingsRead(CamelCaseSchema):
    whatsapp_enabled: bool = True
    email_enabled: bool = True
    daily_digest_hour: int = 7
    recipients: List[WorkspaceNotificationRecipientRead] = Field(default_factory=list)
    rules: List[WorkspaceNotificationRuleRead] = Field(default_factory=list)


class NotificationSettingsUpdate(WriteSchema):
    whatsapp_enabled: bool = True
    email_enabled: bool = True
    daily_digest_hour: int = 7
    recipients: List[WorkspaceNotificationRecipientUpdate] = Field(default_factory=list)
    rules: List[WorkspaceNotificationRuleUpdate] = Field(default_factory=list)


class BrandingSocialProof(WriteSchema):
    id: str = Field(min_length=1, max_length=120)
    kind: Literal["testimonial", "metric", "credential", "case_result"]
    label: str = Field(min_length=1, max_length=180)
    value: str = Field(min_length=1, max_length=500)
    approved_for_external_use: bool = False


class BrandingSettingsRead(CamelCaseSchema):
    brand_name: str = "Meu Executivo"
    short_name: str = "KLM"
    tagline: str = "Operação jurídica inteligente"
    legal_name: str = "Meu Executivo"
    domain: str = "klmadvogados.com"
    default_locale: str = "pt-BR"
    logo_url: Optional[str] = "/logo.svg"
    logo_dark_url: Optional[str] = "/logo.svg"
    icon_url: Optional[str] = "/logo-icon.png"
    favicon_url: Optional[str] = "/favicon.svg"
    apple_touch_icon_url: Optional[str] = "/logo-icon.png"
    letterhead_url: Optional[str] = None
    signature_url: Optional[str] = None
    primary_color: str = "#DCA74C"
    accent_color: str = "#C69237"
    foreground_color: str = "#131720"
    background_color: str = "#FAFAF9"
    sidebar_color: str = "#050505"
    success_color: str = "#237A3B"
    warning_color: str = "#D98E04"
    danger_color: str = "#B94A48"
    sans_family: str = "'Geist Variable', 'Geist', sans-serif"
    legal_family: str = "'Geist Variable', 'Geist', sans-serif"
    default_title: str = "Meu Executivo | Operação jurídica inteligente"
    title_template: str = "%s | Meu Executivo"
    description: str = "Atendimento, CRM, operação jurídica e inteligência comercial."
    application_name: str = "Meu Executivo"
    author: str = "Meu Executivo"
    theme_color: str = "#050505"
    email: str = "contato@klmadvogados.com"
    support_email: str = "contato@klmadvogados.com"
    phone: Optional[str] = None
    website_url: str = "https://klmadvogados.com"
    registered_name: str = "Meu Executivo"
    registration_number: Optional[str] = None
    bar_registration: Optional[str] = None
    address: Optional[str] = None
    social_proof: List[BrandingSocialProof] = Field(default_factory=list)
    layout_density: Literal["compact", "comfortable", "spacious"] = "comfortable"


class BrandingSettingsUpdate(WriteSchema):
    brand_name: str = Field(default="Meu Executivo", min_length=1, max_length=180)
    short_name: str = Field(default="KLM", min_length=1, max_length=40)
    tagline: str = Field(default="Operação jurídica inteligente", max_length=240)
    legal_name: str = Field(default="Meu Executivo", min_length=1, max_length=240)
    domain: str = Field(default="klmadvogados.com", min_length=3, max_length=255)
    default_locale: str = Field(default="pt-BR", min_length=2, max_length=20)
    logo_url: Optional[str] = Field(default="/logo.svg", max_length=1000)
    logo_dark_url: Optional[str] = Field(default="/logo.svg", max_length=1000)
    icon_url: Optional[str] = Field(default="/logo-icon.png", max_length=1000)
    favicon_url: Optional[str] = Field(default="/favicon.svg", max_length=1000)
    apple_touch_icon_url: Optional[str] = Field(default="/logo-icon.png", max_length=1000)
    letterhead_url: Optional[str] = Field(default=None, max_length=1000)
    signature_url: Optional[str] = Field(default=None, max_length=1000)
    primary_color: str = Field(default="#DCA74C", pattern=r"^#[0-9A-Fa-f]{6}$")
    accent_color: str = Field(default="#C69237", pattern=r"^#[0-9A-Fa-f]{6}$")
    foreground_color: str = Field(default="#131720", pattern=r"^#[0-9A-Fa-f]{6}$")
    background_color: str = Field(default="#FAFAF9", pattern=r"^#[0-9A-Fa-f]{6}$")
    sidebar_color: str = Field(default="#050505", pattern=r"^#[0-9A-Fa-f]{6}$")
    success_color: str = Field(default="#237A3B", pattern=r"^#[0-9A-Fa-f]{6}$")
    warning_color: str = Field(default="#D98E04", pattern=r"^#[0-9A-Fa-f]{6}$")
    danger_color: str = Field(default="#B94A48", pattern=r"^#[0-9A-Fa-f]{6}$")
    sans_family: str = Field(default="'Geist Variable', 'Geist', sans-serif", max_length=240)
    legal_family: str = Field(default="'Geist Variable', 'Geist', sans-serif", max_length=240)
    default_title: str = Field(default="Meu Executivo | Operação jurídica inteligente", max_length=240)
    title_template: str = Field(default="%s | Meu Executivo", max_length=240)
    description: str = Field(
        default="Atendimento, CRM, operação jurídica e inteligência comercial.",
        max_length=1000,
    )
    application_name: str = Field(default="Meu Executivo", max_length=180)
    author: str = Field(default="Meu Executivo", max_length=240)
    theme_color: str = Field(default="#050505", pattern=r"^#[0-9A-Fa-f]{6}$")
    email: str = Field(default="contato@klmadvogados.com", max_length=320)
    support_email: str = Field(default="contato@klmadvogados.com", max_length=320)
    phone: Optional[str] = Field(default=None, max_length=40)
    website_url: str = Field(default="https://klmadvogados.com", max_length=1000)
    registered_name: str = Field(default="Meu Executivo", max_length=240)
    registration_number: Optional[str] = Field(default=None, max_length=80)
    bar_registration: Optional[str] = Field(default=None, max_length=120)
    address: Optional[str] = Field(default=None, max_length=500)
    social_proof: List[BrandingSocialProof] = Field(default_factory=list, max_length=100)
    layout_density: Literal["compact", "comfortable", "spacious"] = "comfortable"


class SecurityAuditSettingsRead(CamelCaseSchema):
    session_timeout_minutes: int = 30
    require_human_review_for_high_risk: bool = True
    require_stronger_review_for_new_prompts: bool = True
    audit_retention_days: int = 90
    open_api_enabled: bool = True


class SecurityAuditSettingsUpdate(WriteSchema):
    session_timeout_minutes: int = 30
    require_human_review_for_high_risk: bool = True
    require_stronger_review_for_new_prompts: bool = True
    audit_retention_days: int = 90
    open_api_enabled: bool = True


class AdminControlPlaneRead(CamelCaseSchema):
    practice_areas: List[PracticeAreaTemplateRead] = Field(default_factory=list)
    ai_governance: AiGovernanceRead
    notifications: NotificationSettingsRead
    branding: BrandingSettingsRead
    security_audit: SecurityAuditSettingsRead


class AdminControlPlaneUpdate(WriteSchema):
    practice_areas: List[PracticeAreaTemplateUpdate] = Field(default_factory=list)
    ai_governance: Optional[AiGovernanceUpdate] = None
    notifications: Optional[NotificationSettingsUpdate] = None
    branding: Optional[BrandingSettingsUpdate] = None
    security_audit: Optional[SecurityAuditSettingsUpdate] = None


class WorkspaceAreaActivationRead(CamelCaseSchema):
    key: str
    label: str
    description: str
    enabled: bool = False
    selected_flow_template_id: Optional[str] = None
    follow_up_profile: FollowUpProfile
    human_intervention_mode: AutomationStageMode
    checklist: List[str] = Field(default_factory=list)
    flow_options: List[FlowTemplateReferenceRead] = Field(default_factory=list)


class WorkspaceAreaActivationUpdate(WriteSchema):
    key: str
    enabled: bool = False
    selected_flow_template_id: Optional[str] = None
    follow_up_profile: FollowUpProfile
    human_intervention_mode: AutomationStageMode


class WorkspaceOperationsRead(CamelCaseSchema):
    area_activations: List[WorkspaceAreaActivationRead] = Field(default_factory=list)


class WorkspaceOperationsUpdate(WriteSchema):
    area_activations: List[WorkspaceAreaActivationUpdate] = Field(default_factory=list)


class MeetingParticipantRead(CamelCaseSchema):
    id: UUID
    membership_id: UUID
    user_id: UUID
    role: MeetingParticipantRole
    rsvp: MeetingParticipantRsvp
    reminder_minutes_before: Optional[int] = None
    label_snapshot: str
    active: bool
    removed_at: Optional[datetime] = None


class MeetingParticipantWrite(WriteSchema):
    user_id: UUID
    role: MeetingParticipantRole
    rsvp: MeetingParticipantRsvp = MeetingParticipantRsvp.pending
    reminder_minutes_before: Optional[int] = Field(default=None, ge=0, le=10_080)


class MeetingParticipantsUpdate(WriteSchema):
    expected_version: int = Field(ge=1)
    participants: List[MeetingParticipantWrite]
    confirm_participant_conflicts: bool = False
    conflict_reason: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_participants(self) -> "MeetingParticipantsUpdate":
        _validate_meeting_participants(self.participants)
        return self


class MeetingNotificationOutboxRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    meeting_id: Optional[UUID] = None
    meeting_version: int
    event_key: str
    correlation_id: Optional[str] = None
    status: MeetingNotificationStatus
    attempts: int
    available_at: datetime
    claimed_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    last_error_code: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MeetingRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: Optional[UUID] = None
    responsible_user_id: Optional[UUID] = None
    linked_task_id: Optional[UUID] = None
    title: str
    starts_at: datetime
    ends_at: Optional[datetime] = None
    status: MeetingStatus
    natureza: MeetingNature = MeetingNature.comercial
    event_type: MeetingEventType = MeetingEventType.reuniao
    location: Optional[str] = None
    meeting_url: Optional[str] = None
    notes: Optional[str] = None
    recipient_overrides: List[WorkspaceNotificationRecipientRead] = Field(default_factory=list)
    google_calendar_event_id: Optional[str] = None
    reminder_sent_at: Optional[datetime] = None
    version: int = 1
    participants: List[MeetingParticipantRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @field_validator("starts_at", "ends_at")
    @classmethod
    def normalize_persisted_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value


class MeetingCreate(WriteSchema):
    matter_id: Optional[UUID] = None
    responsible_user_id: Optional[UUID] = None
    linked_task_id: Optional[UUID] = None
    title: str
    starts_at: datetime
    ends_at: Optional[datetime] = None
    natureza: MeetingNature = MeetingNature.comercial
    event_type: MeetingEventType = MeetingEventType.reuniao
    location: Optional[str] = None
    meeting_url: Optional[str] = None
    notes: Optional[str] = None
    recipient_overrides: List[WorkspaceNotificationRecipientUpdate] = Field(default_factory=list)
    google_calendar_event_id: Optional[str] = None
    participants: List[MeetingParticipantWrite] = Field(default_factory=list)
    confirm_participant_conflicts: bool = False
    conflict_reason: Optional[str] = Field(default=None, max_length=500)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def validate_explicit_timezone(cls, value: datetime | None) -> datetime | None:
        return _require_explicit_meeting_timezone(value)

    @model_validator(mode="after")
    def validate_interval(self) -> "MeetingCreate":
        _ensure_meeting_ends_after_start(self.starts_at, self.ends_at)
        _validate_meeting_participants(self.participants)
        return self


class MeetingUpdate(WriteSchema):
    expected_version: int = Field(ge=1)
    matter_id: Optional[UUID] = None
    responsible_user_id: Optional[UUID] = None
    linked_task_id: Optional[UUID] = None
    title: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    status: Optional[MeetingStatus] = None
    natureza: Optional[MeetingNature] = None
    event_type: Optional[MeetingEventType] = None
    location: Optional[str] = None
    meeting_url: Optional[str] = None
    notes: Optional[str] = None
    recipient_overrides: Optional[List[WorkspaceNotificationRecipientUpdate]] = None
    google_calendar_event_id: Optional[str] = None
    participants: Optional[List[MeetingParticipantWrite]] = None
    confirm_participant_conflicts: bool = False
    conflict_reason: Optional[str] = Field(default=None, max_length=500)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def validate_explicit_timezone(cls, value: datetime | None) -> datetime | None:
        return _require_explicit_meeting_timezone(value)

    @model_validator(mode="after")
    def validate_complete_interval(self) -> "MeetingUpdate":
        interval_fields = {"starts_at", "ends_at"}
        if interval_fields.issubset(self.model_fields_set) and self.starts_at is not None:
            _ensure_meeting_ends_after_start(self.starts_at, self.ends_at)
        if self.participants is not None:
            _validate_meeting_participants(self.participants)
        return self


def _validate_meeting_participants(
    participants: List[MeetingParticipantWrite],
) -> None:
    user_ids = [item.user_id for item in participants]
    if len(user_ids) != len(set(user_ids)):
        raise ValueError("Cada pessoa pode aparecer uma única vez no compromisso.")
    organizers = [item for item in participants if item.role == MeetingParticipantRole.organizer]
    if len(organizers) > 1:
        raise ValueError("O compromisso aceita no máximo um organizador.")


def _require_explicit_meeting_timezone(value: datetime | None) -> datetime | None:
    if value is not None and value.utcoffset() is None:
        raise ValueError("O horário da reunião deve conter timezone explícito.")
    return value


def _ensure_meeting_ends_after_start(
    starts_at: datetime,
    ends_at: Optional[datetime],
) -> None:
    if ends_at is None:
        return
    try:
        valid_interval = ends_at > starts_at
    except TypeError:
        valid_interval = False
    if not valid_interval:
        raise ValueError("O término da reunião deve ser posterior ao início.")
