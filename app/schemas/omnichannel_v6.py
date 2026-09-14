"""Typed contracts for the canonical KLM omnichannel core."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator

from app.core.tenant_cost_privacy import strip_internal_provider_cost_fields
from app.models.klm_operations import (
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
)
from app.schemas.base import (
    CamelCaseSchema as _BaseCamelCaseSchema,
)
from app.schemas.base import (
    WriteSchema as _BaseWriteSchema,
)
from app.schemas.klm_operations import (
    OmnichannelMessageCollaborationCreate,
    OmnichannelMessageRead,
    OmnichannelThreadRead,
    normalize_message_collaboration,
)
from app.services.whatsapp.helpers import normalize_phone_number


class CamelCaseSchema(_BaseCamelCaseSchema):
    """Omnichannel reads with a tenant-safe provider-cost boundary."""

    @field_validator("*", mode="before", check_fields=False)
    @classmethod
    def hide_internal_provider_costs(cls, value: Any, info) -> Any:
        return strip_internal_provider_cost_fields(value, parent_key=info.field_name)


class WriteSchema(_BaseWriteSchema):
    """Omnichannel writes that cannot smuggle provider costs in metadata."""

    @field_validator("*", mode="before", check_fields=False)
    @classmethod
    def hide_internal_provider_costs(cls, value: Any, info) -> Any:
        return strip_internal_provider_cost_fields(value, parent_key=info.field_name)


ChannelProvider = Literal[
    "meta_cloud",
    "qr_gateway",
    "meta_ads",
    "meta_instagram",
    "meta_messenger",
    "tiktok_business",
    "tiktok_ads",
    "google_mail",
    "microsoft_graph",
    "imap_smtp",
    "site",
    "lovable",
    "local_fixture",
]


class ChannelConnectionCreate(WriteSchema):
    channel: OmnichannelChannel
    provider: ChannelProvider
    connection_key: str = Field(min_length=2, max_length=120, pattern=r"^[a-z0-9][a-z0-9:_-]+$")
    display_name: str = Field(min_length=2, max_length=180)
    external_account_id: str | None = Field(default=None, max_length=180)
    transport_mode: Literal[
        "cloud_api",
        "coexistence",
        "official_api",
        "qr_gateway",
        "webhook",
        "oauth",
        "local_fixture",
    ] = "official_api"
    is_default: bool = False
    capabilities: dict[str, Any] = Field(default_factory=dict)
    limits: dict[str, Any] = Field(default_factory=dict)
    routing_policy: dict[str, Any] = Field(default_factory=dict)
    configuration: dict[str, Any] = Field(default_factory=dict)
    credential_reference: str | None = Field(default=None, max_length=255)


class ChannelConnectionUpdate(WriteSchema):
    expected_version: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=2, max_length=180)
    external_account_id: str | None = Field(default=None, max_length=180)
    is_default: bool | None = None
    capabilities: dict[str, Any] | None = None
    limits: dict[str, Any] | None = None
    routing_policy: dict[str, Any] | None = None
    configuration: dict[str, Any] | None = None
    credential_reference: str | None = Field(default=None, max_length=255)
    action: Literal["save", "pause", "resume", "disconnect_local"] = "save"


class ChannelConnectionRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    channel: str
    provider: str
    connection_key: str
    display_name: str
    external_account_id: str | None = None
    transport_mode: str
    status: str
    is_default: bool
    capabilities: dict[str, Any]
    limits: dict[str, Any]
    routing_policy: dict[str, Any]
    health: dict[str, Any]
    configuration: dict[str, Any]
    credential_reference: str | None = None
    webhook_state: str
    version: int
    verified_at: datetime | None = None
    connected_at: datetime | None = None
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ChannelConnectionValidationRead(CamelCaseSchema):
    connection: ChannelConnectionRead
    ready_for_external_setup: bool
    missing_configuration: list[str]
    supported_capabilities: list[str]
    configuration_required: list[str]
    unsupported_capabilities: list[str]
    external_gate_open: bool = False


class WebhookCredentialConfigure(WriteSchema):
    expected_version: int = Field(default=0, ge=0)
    action: Literal["save", "rotate", "disable", "enable"] = "save"
    signing_secret: SecretStr | None = Field(default=None, min_length=32, max_length=512)
    verify_token: SecretStr | None = Field(default=None, min_length=16, max_length=512)
    timestamp_tolerance_seconds: int | None = Field(default=None, ge=30, le=3600)

    @model_validator(mode="after")
    def validate_secret_for_rotation(self) -> WebhookCredentialConfigure:
        if self.action == "rotate" and self.signing_secret is None:
            raise ValueError("A rotação exige um novo segredo de assinatura.")
        return self


class WebhookCredentialStatusRead(CamelCaseSchema):
    connection_id: UUID
    provider: str
    connection_key: str
    state: str
    algorithm: str
    signing_secret_configured: bool
    verify_token_configured: bool
    timestamp_tolerance_seconds: int
    version: int
    webhook_path: str
    external_gate_open: bool = False
    last_verified_at: datetime | None = None
    rotated_at: datetime | None = None
    updated_at: datetime


class WebhookCenterSetupStepRead(CamelCaseSchema):
    key: str
    label: str
    completed: bool
    detail: str


class WebhookCenterReceiptRead(CamelCaseSchema):
    id: UUID
    connection_id: UUID
    provider: str
    connection_key: str
    external_event_id: str
    external_lead_id: str | None = None
    payload_sha256: str
    signature_scheme: str
    signature_timestamp: datetime | None = None
    received_at: datetime
    processing_state: str
    lead_capture_receipt_id: UUID | None = None
    lead_capture_state: str | None = None
    hydration_job_id: UUID | None = None
    hydration_state: str | None = None
    thread_id: UUID | None = None
    matter_id: UUID | None = None
    identity_id: UUID | None = None
    error_code: str | None = None


class WebhookCenterConnectionRead(CamelCaseSchema):
    connection: ChannelConnectionRead
    credential: WebhookCredentialStatusRead | None = None
    setup_progress: int = Field(ge=0, le=100)
    setup_steps: list[WebhookCenterSetupStepRead] = Field(default_factory=list)
    receipt_count: int = 0
    processed_count: int = 0
    awaiting_hydration_count: int = 0
    failed_count: int = 0
    blocked_count: int = 0
    latest_receipt_at: datetime | None = None


class WebhookCenterRead(CamelCaseSchema):
    connection_count: int
    configured_connection_count: int
    active_credential_count: int
    receipt_window_count: int
    receipt_total_count: int
    receipt_match_count: int
    processed_count: int
    awaiting_hydration_count: int
    failed_count: int
    blocked_count: int
    idempotency_enforced: bool = True
    external_gate_open: bool = False
    connections: list[WebhookCenterConnectionRead] = Field(default_factory=list)
    receipts: list[WebhookCenterReceiptRead] = Field(default_factory=list)
    generated_at: datetime


class OmnichannelIdentityRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    display_name: str
    canonical_phone: str | None = None
    canonical_email: str | None = None
    identity_state: str
    source: str
    merge_version: int
    identity_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class IdentityMergeRequest(WriteSchema):
    source_identity_id: UUID
    expected_source_version: int = Field(ge=1)
    expected_target_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=180)
    reason: str = Field(min_length=4, max_length=1000)


class IdentitySplitRequest(WriteSchema):
    endpoint_ids: list[UUID] = Field(default_factory=list, max_length=100)
    thread_ids: list[UUID] = Field(default_factory=list, max_length=100)
    display_name: str = Field(min_length=1, max_length=255)
    canonical_phone: str | None = Field(default=None, max_length=32)
    canonical_email: str | None = Field(default=None, max_length=255)
    expected_source_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=180)
    reason: str = Field(min_length=4, max_length=1000)

    @model_validator(mode="after")
    def require_selection(self) -> IdentitySplitRequest:
        if not self.endpoint_ids and not self.thread_ids:
            raise ValueError("Selecione ao menos um endpoint ou uma conversa para separar.")
        if len(set(self.endpoint_ids)) != len(self.endpoint_ids):
            raise ValueError("A lista de endpoints contém itens repetidos.")
        if len(set(self.thread_ids)) != len(self.thread_ids):
            raise ValueError("A lista de conversas contém itens repetidos.")
        return self


class IdentityReviewRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    identity_id: UUID | None = None
    candidate_identity_id: UUID | None = None
    thread_id: UUID | None = None
    matter_id: UUID | None = None
    created_by_user_id: UUID | None = None
    resolved_by_user_id: UUID | None = None
    review_type: str
    reason_code: str
    status: str
    idempotency_key: str
    evidence: dict[str, Any]
    resolution: dict[str, Any]
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class IdentityMutationEventRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    source_identity_id: UUID
    target_identity_id: UUID
    actor_user_id: UUID | None = None
    operation: str
    idempotency_key: str
    before_snapshot: dict[str, Any]
    after_snapshot: dict[str, Any]
    occurred_at: datetime


class IdentityMutationRead(CamelCaseSchema):
    source: OmnichannelIdentityRead
    target: OmnichannelIdentityRead
    event: IdentityMutationEventRead
    moved_endpoint_ids: list[UUID] = Field(default_factory=list)
    moved_thread_ids: list[UUID] = Field(default_factory=list)
    deduplicated: bool = False


class OmnichannelEndpointRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    identity_id: UUID
    channel: str
    provider: str
    connection_id: UUID | None = None
    connection_key: str
    external_account_id: str | None = None
    external_user_id: str
    address: str | None = None
    normalized_address: str | None = None
    verified_at: datetime | None = None
    consent_state: str
    blocked_at: datetime | None = None
    last_seen_at: datetime | None = None
    capabilities: dict[str, Any]
    endpoint_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class OmnichannelBindingRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    thread_id: UUID
    endpoint_id: UUID | None = None
    provider: str
    connection_id: UUID | None = None
    connection_key: str
    external_thread_id: str
    binding_state: str
    last_cursor: str | None = None
    last_synced_at: datetime | None = None
    binding_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class WorkspaceTeamCreate(WriteSchema):
    name: str = Field(min_length=2, max_length=180)
    slug: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str | None = None
    team_type: str = Field(default="service", min_length=2, max_length=40)
    color: str = Field(default="#1a9948", pattern=r"^#[0-9A-Fa-f]{6}$")
    capacity: int = Field(default=0, ge=0, le=10000)
    routing_strategy: Literal[
        "manual", "round_robin", "least_loaded", "skill_based", "priority"
    ] = "manual"
    schedule: dict[str, Any] = Field(default_factory=dict)


class WorkspaceTeamUpdate(WriteSchema):
    name: str | None = Field(default=None, min_length=2, max_length=180)
    description: str | None = None
    team_type: str | None = Field(default=None, min_length=2, max_length=40)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    capacity: int | None = Field(default=None, ge=0, le=10000)
    routing_strategy: (
        Literal["manual", "round_robin", "least_loaded", "skill_based", "priority"] | None
    ) = None
    schedule: dict[str, Any] | None = None
    is_active: bool | None = None


class WorkspaceTeamRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    name: str
    slug: str
    description: str | None = None
    team_type: str
    color: str
    capacity: int
    routing_strategy: str
    schedule: dict[str, Any]
    is_active: bool
    member_count: int = 0
    available_count: int = 0
    open_thread_count: int = 0
    created_at: datetime
    updated_at: datetime


class WorkspaceTeamMemberCreate(WriteSchema):
    user_id: UUID
    member_role: Literal["member", "lead", "supervisor"] = "member"
    routing_weight: int = Field(default=100, ge=1, le=1000)
    max_concurrent: int = Field(default=0, ge=0, le=10000)
    is_available: bool = True


class WorkspaceTeamMemberRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    team_id: UUID
    membership_id: UUID
    user_id: UUID
    user_name: str
    member_role: str
    routing_weight: int
    max_concurrent: int
    is_available: bool
    created_at: datetime
    updated_at: datetime


class ThreadTransferRequest(WriteSchema):
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=180)
    to_team_id: UUID | None = None
    to_user_id: UUID | None = None
    queue: str | None = Field(default=None, min_length=1, max_length=60)
    reason: str | None = Field(default=None, max_length=1000)
    source: Literal["human", "agent", "rule", "system"] = "human"

    @model_validator(mode="after")
    def require_destination(self) -> ThreadTransferRequest:
        if self.to_team_id is None and self.to_user_id is None and self.queue is None:
            raise ValueError("Informe equipe, usuário ou fila de destino.")
        return self


class OmnichannelRoutingEventRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    thread_id: UUID
    actor_user_id: UUID | None = None
    from_user_id: UUID | None = None
    to_user_id: UUID | None = None
    from_team_id: UUID | None = None
    to_team_id: UUID | None = None
    action: str
    reason: str | None = None
    source: str
    expected_thread_version: int
    result_thread_version: int
    idempotency_key: str
    correlation_id: str | None = None
    occurred_at: datetime


class ThreadTransferRead(CamelCaseSchema):
    thread: OmnichannelThreadRead
    event: OmnichannelRoutingEventRead
    deduplicated: bool = False


class CanonicalChannelEventIngest(WriteSchema):
    provider: str = Field(min_length=2, max_length=40)
    connection_key: str = Field(min_length=1, max_length=120)
    channel: OmnichannelChannel
    event_type: str = Field(min_length=3, max_length=100)
    external_event_id: str = Field(min_length=1, max_length=255)
    external_thread_id: str = Field(min_length=1, max_length=255)
    external_user_id: str = Field(min_length=1, max_length=255)
    external_account_id: str | None = Field(default=None, max_length=180)
    display_name: str = Field(min_length=1, max_length=255)
    profile_avatar_url: str | None = Field(default=None, max_length=1000)
    address: str | None = Field(default=None, max_length=255)
    canonical_phone: str | None = Field(default=None, max_length=32)
    canonical_email: str | None = Field(default=None, max_length=255)
    client_id: UUID | None = None
    matter_id: UUID | None = None
    parent_external_event_id: str | None = Field(default=None, max_length=255)
    direction: MessageDirection | None = None
    author_type: MessageAuthorType | None = None
    author_label: str | None = Field(default=None, max_length=180)
    body: str | None = None
    content_type: str = Field(default="text", min_length=1, max_length=40)
    language: str | None = Field(default=None, max_length=16)
    visibility: Literal["client_private", "public", "internal", "provider_private"] = (
        "client_private"
    )
    public_url: str | None = Field(default=None, max_length=1000)
    actionability: Literal[
        "informational", "reply_required", "qualification", "commercial", "urgent"
    ] = "informational"
    occurred_at: datetime | None = None
    deadline_at: datetime | None = None
    cursor: str | None = Field(default=None, max_length=500)
    correlation: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("canonical_email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        normalized = value.strip().lower() if value else None
        return normalized or None

    @field_validator("profile_avatar_url")
    @classmethod
    def validate_profile_avatar_url(cls, value: str | None) -> str | None:
        normalized = value.strip() if value else None
        if not normalized:
            return None
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("profileAvatarUrl precisa ser uma URL HTTP(S) válida.")
        return normalized

    @model_validator(mode="after")
    def validate_message_event(self) -> CanonicalChannelEventIngest:
        if self.direction is not None and not (self.body or "").strip():
            raise ValueError("Eventos de mensagem precisam de conteúdo.")
        if self.author_type is not None and self.direction is None:
            raise ValueError("authorType exige direction.")
        return self


class OmnichannelChannelEventRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    identity_id: UUID | None = None
    thread_id: UUID
    binding_id: UUID | None = None
    parent_event_id: UUID | None = None
    connection_id: UUID | None = None
    provider: str
    connection_key: str
    event_type: str
    external_event_id: str
    payload_sha256: str
    actor_external_id: str | None = None
    visibility: str
    public_url: str | None = None
    actionability: str
    deadline_at: datetime | None = None
    occurred_at: datetime
    received_at: datetime
    correlation: dict[str, Any]
    event_payload: dict[str, Any]
    event_metadata: dict[str, Any]


class ChannelConnectionSummaryRead(CamelCaseSchema):
    id: UUID
    display_name: str
    external_account_id: str | None = None
    transport_mode: str
    status: str
    is_default: bool
    capabilities: dict[str, Any] = Field(default_factory=dict)


class ChannelEventInboxItemRead(CamelCaseSchema):
    event: OmnichannelChannelEventRead
    thread: OmnichannelThreadRead
    identity: OmnichannelIdentityRead | None = None
    message: OmnichannelMessageRead | None = None
    connection: ChannelConnectionSummaryRead | None = None
    channel: str


class ChannelEventInboxRead(CamelCaseSchema):
    items: list[ChannelEventInboxItemRead]
    has_more: bool = False
    generated_at: datetime


class MessageDeliveryReceiptIngest(WriteSchema):
    message_id: UUID | None = None
    provider: str = Field(min_length=2, max_length=40)
    connection_key: str = Field(min_length=1, max_length=120)
    external_event_id: str = Field(min_length=1, max_length=255)
    external_message_id: str = Field(min_length=1, max_length=255)
    status: MessageDeliveryStatus
    provider_status: str | None = Field(default=None, max_length=80)
    occurred_at: datetime | None = None
    error_code: str | None = Field(default=None, max_length=120)
    error_message: str | None = Field(default=None, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status")
    @classmethod
    def validate_outbound_status(cls, value: MessageDeliveryStatus) -> MessageDeliveryStatus:
        if value in {MessageDeliveryStatus.local_draft, MessageDeliveryStatus.received}:
            raise ValueError("Recibos de saída não aceitam status local_draft ou received.")
        return value


class InternalMessageDeliveryReceiptIngest(MessageDeliveryReceiptIngest):
    """Trusted provider receipt with fields persisted only for platform accounting."""

    cost_amount: Decimal | None = Field(default=None, ge=0)
    cost_currency: str | None = Field(default=None, min_length=3, max_length=3)
    cost_quantity: Decimal = Field(default=Decimal("1"), gt=0)
    cost_unit: str = Field(default="message", min_length=1, max_length=40)
    pricing_category: str | None = Field(default=None, max_length=120)
    provider_invoice_reference: str | None = Field(default=None, max_length=255)

    @field_validator("cost_currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @model_validator(mode="after")
    def validate_cost_pair(self) -> InternalMessageDeliveryReceiptIngest:
        if (self.cost_amount is None) != (self.cost_currency is None):
            raise ValueError("costAmount e costCurrency devem ser informados juntos.")
        return self


class MessageDeliveryEventRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    message_id: UUID
    thread_id: UUID
    connection_id: UUID
    provider: str
    connection_key: str
    external_event_id: str
    external_message_id: str
    provider_status: str
    projected_status_before: str
    projected_status_after: str
    applied: bool
    ignored_reason: str | None = None
    payload_sha256: str
    occurred_at: datetime
    received_at: datetime
    error_code: str | None = None
    error_message: str | None = None
    event_payload: dict[str, Any]
    event_metadata: dict[str, Any]


class MessageCostLedgerEntryRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    message_id: UUID
    thread_id: UUID
    delivery_event_id: UUID
    provider: str
    connection_key: str
    entry_type: str
    amount: Decimal
    currency: str
    quantity: Decimal
    unit: str
    pricing_category: str | None = None
    provider_invoice_reference: str | None = None
    idempotency_key: str
    occurred_at: datetime
    recorded_at: datetime
    entry_metadata: dict[str, Any]


class MessageDeliveryReceiptRead(CamelCaseSchema):
    message: OmnichannelMessageRead
    event: MessageDeliveryEventRead
    deduplicated: bool = False


class OmnichannelActionIntentCreate(WriteSchema):
    event_id: UUID | None = None
    connection_id: UUID | None = None
    action_type: Literal[
        "public_reply",
        "private_reply",
        "comment_like",
        "comment_hide",
        "comment_delete",
        "send_message",
        "mark_answered",
    ]
    target_external_id: str | None = Field(default=None, max_length=255)
    body: str | None = None
    scheduled_at: datetime | None = None
    idempotency_key: str = Field(min_length=8, max_length=180)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_action_content(self) -> OmnichannelActionIntentCreate:
        if self.action_type in {"public_reply", "private_reply", "send_message"}:
            if not (self.body or "").strip():
                raise ValueError("Esta ação precisa do conteúdo da mensagem.")
        if self.action_type.startswith("comment_") or self.action_type.endswith("reply"):
            if self.event_id is None:
                raise ValueError("A ação social precisa do evento de origem.")
        return self


class OmnichannelActionIntentRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    thread_id: UUID
    event_id: UUID | None = None
    connection_id: UUID | None = None
    requested_by_user_id: UUID | None = None
    action_type: str
    target_external_id: str | None = None
    body: str | None = None
    state: str
    required_capability: str
    capability_state: str
    idempotency_key: str
    payload_sha256: str
    scheduled_at: datetime | None = None
    attempt_count: int = 0
    max_attempts: int = 5
    next_attempt_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    lease_generation: int = 0
    delivery_attempted_at: datetime | None = None
    provider_supports_idempotency: bool = False
    provider_action_id: str | None = None
    delivery_uncertain_at: datetime | None = None
    completed_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    action_payload: dict[str, Any]
    provider_receipt: dict[str, Any]
    failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class OmnichannelActionIntentEventRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    action_intent_id: UUID
    thread_id: UUID
    connection_id: UUID | None = None
    provider: str
    action_type: str
    event_type: str
    previous_state: str
    next_state: str
    idempotency_key: str
    payload_sha256: str
    lease_owner: str | None = None
    lease_generation: int | None = None
    external_action_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    occurred_at: datetime
    received_at: datetime
    event_metadata: dict[str, Any]


class AutomationRuleCreate(WriteSchema):
    name: str = Field(min_length=2, max_length=180)
    slug: str = Field(min_length=2, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str | None = None
    channel: OmnichannelChannel | None = None
    trigger_type: Literal[
        "message.received",
        "direct_message",
        "post_comment",
        "page_comment",
        "keyword_comment",
        "story_reply",
        "story_mention",
        "new_follower",
        "lead_form",
        "scheduled",
    ]
    status: Literal["draft", "active", "paused"] = "draft"
    priority: int = Field(default=100, ge=0, le=10000)
    coexist_with_ai: bool = True
    stop_on_match: bool = False
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(min_length=1)
    quiet_hours: dict[str, Any] = Field(default_factory=dict)
    rate_limits: dict[str, Any] = Field(default_factory=dict)


class AutomationRuleUpdate(WriteSchema):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=2, max_length=180)
    description: str | None = None
    status: Literal["draft", "active", "paused"] | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)
    coexist_with_ai: bool | None = None
    stop_on_match: bool | None = None
    trigger_config: dict[str, Any] | None = None
    conditions: list[dict[str, Any]] | None = None
    actions: list[dict[str, Any]] | None = None
    quiet_hours: dict[str, Any] | None = None
    rate_limits: dict[str, Any] | None = None


class AutomationRuleRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    created_by_user_id: UUID | None = None
    name: str
    slug: str
    description: str | None = None
    channel: str | None = None
    trigger_type: str
    status: str
    priority: int
    coexist_with_ai: bool
    stop_on_match: bool
    trigger_config: dict[str, Any]
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    quiet_hours: dict[str, Any]
    rate_limits: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime


class AutomationSimulationRequest(WriteSchema):
    event_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=180)


class AutomationRunEventRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    run_id: UUID
    sequence: int
    action_key: str
    state: str
    duration_ms: int
    input_snapshot: dict[str, Any]
    output_snapshot: dict[str, Any]
    occurred_at: datetime


class AutomationRunRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    rule_id: UUID
    event_id: UUID
    thread_id: UUID
    state: str
    idempotency_key: str
    current_action: int
    context: dict[str, Any]
    result: dict[str, Any]
    error: dict[str, Any]
    started_at: datetime
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    trace: list[AutomationRunEventRead] = Field(default_factory=list)


class AutomationRunSummaryRead(CamelCaseSchema):
    id: UUID
    rule_id: UUID
    rule_name: str
    state: str
    current_action: int
    matched: bool
    prepared_actions: int
    coexist_with_ai: bool
    started_at: datetime
    completed_at: datetime | None = None


class OmnichannelChannelSummaryRead(CamelCaseSchema):
    endpoint_id: UUID
    binding_id: UUID | None = None
    channel: str
    provider: str
    connection_id: UUID | None = None
    connection_key: str
    connection_name: str | None = None
    connection_status: str | None = None
    transport_mode: str | None = None
    external_account_id: str | None = None
    address: str | None = None
    external_user_id: str
    consent_state: str
    blocked_at: datetime | None = None
    last_seen_at: datetime | None = None
    binding_count: int = 0
    capabilities: dict[str, Any] = Field(default_factory=dict)


class OmnichannelCopilotRequest(WriteSchema):
    action: Literal[
        "summarize",
        "suggest_reply",
        "rewrite",
        "unanswered",
        "extract_tasks",
        "meeting_brief",
    ]
    draft: str | None = None
    tone: Literal["acolhedor", "objetivo", "formal", "simples"] = "acolhedor"
    goal: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_draft_for_rewrite(self) -> OmnichannelCopilotRequest:
        if self.action == "rewrite" and not (self.draft or "").strip():
            raise ValueError("Informe o rascunho que será reescrito.")
        return self


class OmnichannelCopilotRead(CamelCaseSchema):
    action: str
    engine: str
    content: str
    suggestions: list[str] = Field(default_factory=list)
    unanswered_questions: list[str] = Field(default_factory=list)
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    evidence_message_ids: list[UUID] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    requires_model: bool = False
    external_requests: int = 0
    generated_at: datetime


class CanonicalChannelEventIngestRead(CamelCaseSchema):
    identity: OmnichannelIdentityRead
    endpoint: OmnichannelEndpointRead
    binding: OmnichannelBindingRead
    thread: OmnichannelThreadRead
    event: OmnichannelChannelEventRead
    message: OmnichannelMessageRead | None = None
    deduplicated: bool = False


class LeadCaptureIngest(WriteSchema):
    provider: Literal["meta_ads", "tiktok_ads", "site", "lovable", "local_fixture"]
    connection_key: str = Field(min_length=1, max_length=120)
    external_lead_id: str = Field(min_length=1, max_length=255)
    external_form_id: str | None = Field(default=None, max_length=255)
    matter_id: UUID | None = None
    channel: OmnichannelChannel = OmnichannelChannel.webchat
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=255)
    demand_type: str = Field(
        default="Triagem de negativa de plano de saúde",
        min_length=2,
        max_length=300,
    )
    health_subtheme: str | None = Field(default=None, max_length=80)
    health_object_name: str | None = Field(default=None, max_length=300)
    health_insurer: str | None = Field(default=None, max_length=200)
    health_denial_reason: str | None = None
    medical_urgency_hours: int | None = Field(default=None, ge=1, le=8760)
    source: str | None = Field(default=None, max_length=120)
    campaign: str | None = Field(default=None, max_length=255)
    ad_set: str | None = Field(default=None, max_length=255)
    ad: str | None = Field(default=None, max_length=255)
    provider_references: dict[str, str] = Field(default_factory=dict)
    click_ids: dict[str, str] = Field(default_factory=dict)
    fields: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime | None = None
    proactive_message_template: str | None = Field(default=None, max_length=180)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        if not str(value or "").strip():
            return None
        normalized = normalize_phone_number(value)
        if normalized is None:
            raise ValueError(
                "Informe DDI, DDD e número válidos. Exemplo aceito pela Meta: 5561999999999"
            )
        return normalized

    @model_validator(mode="after")
    def require_reachable_identity(self) -> LeadCaptureIngest:
        if not self.phone and not self.email:
            raise ValueError("O lead precisa ter telefone ou e-mail para deduplicação.")
        return self


class LeadCaptureReceiptRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    connection_id: UUID | None = None
    provider: str
    connection_key: str
    external_lead_id: str
    external_form_id: str | None = None
    payload_sha256: str
    state: str
    mapping_version: str
    identity_id: UUID | None = None
    thread_id: UUID | None = None
    matter_id: UUID | None = None
    result: dict[str, Any]
    error_code: str | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class LeadCaptureIngestRead(CamelCaseSchema):
    receipt: LeadCaptureReceiptRead
    identity: OmnichannelIdentityRead
    endpoint: OmnichannelEndpointRead
    binding: OmnichannelBindingRead
    thread: OmnichannelThreadRead
    message: OmnichannelMessageRead | None = None
    client_id: UUID
    matter_id: UUID
    deduplicated: bool = False


class LeadHydrationJobRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    connection_id: UUID
    public_ingress_receipt_id: UUID
    lead_capture_receipt_id: UUID | None = None
    provider: str
    connection_key: str
    external_event_id: str
    external_lead_id: str
    external_form_id: str | None = None
    locator_payload: dict[str, Any]
    payload_sha256: str
    state: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None = None
    lease_expires_at: datetime | None = None
    fetch_attempted_at: datetime | None = None
    hydrated_at: datetime | None = None
    completed_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    provider_receipt: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class LeadHydrationEventRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    hydration_job_id: UUID
    connection_id: UUID
    provider: str
    external_lead_id: str
    event_type: str
    previous_state: str
    next_state: str
    idempotency_key: str
    payload_sha256: str
    lease_owner: str | None = None
    lease_generation: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    occurred_at: datetime
    received_at: datetime
    event_metadata: dict[str, Any]


class LeadHydrationAction(WriteSchema):
    action: Literal["retry", "cancel"]


class PublicLeadCaptureIngressItemRead(CamelCaseSchema):
    external_lead_id: str
    state: Literal["processed", "awaiting_hydration"]
    lead_capture_receipt_id: UUID | None = None
    hydration_job_id: UUID | None = None
    deduplicated: bool = False


class PublicLeadCaptureIngressRead(CamelCaseSchema):
    status: Literal["accepted"] = "accepted"
    provider: str
    external_event_id: str
    ingress_receipt_id: UUID
    state: Literal["processed", "awaiting_hydration", "partially_processed"] = "processed"
    lead_capture_receipt_id: UUID | None = None
    hydration_job_id: UUID | None = None
    item_count: int = 1
    items: list[PublicLeadCaptureIngressItemRead] = Field(default_factory=list)
    deduplicated: bool = False
    processed_at: datetime


class PublicSocialIngressItemRead(CamelCaseSchema):
    external_event_id: str
    ingress_receipt_id: UUID
    channel_event_id: UUID
    thread_id: UUID
    identity_id: UUID
    message_id: UUID | None = None
    deduplicated: bool = False
    automation_run_ids: list[UUID] = Field(default_factory=list)


class PublicSocialIngressRead(CamelCaseSchema):
    status: Literal["accepted"] = "accepted"
    provider: str
    inspected_count: int = 0
    accepted_count: int = 0
    ignored_count: int = 0
    deduplicated_count: int = 0
    items: list[PublicSocialIngressItemRead] = Field(default_factory=list)
    processed_at: datetime


class RawLeadCaptureIngest(WriteSchema):
    raw_payload: dict[str, Any]
    proactive_message_template: str | None = Field(default=None, max_length=180)


class LeadCaptureInboxItemRead(CamelCaseSchema):
    receipt: LeadCaptureReceiptRead
    identity: OmnichannelIdentityRead | None = None
    thread: OmnichannelThreadRead | None = None
    acquisition: dict[str, Any] = Field(default_factory=dict)


class LeadCaptureInboxRead(CamelCaseSchema):
    items: list[LeadCaptureInboxItemRead] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False
    counts_by_provider: dict[str, int] = Field(default_factory=dict)
    counts_by_state: dict[str, int] = Field(default_factory=dict)
    generated_at: datetime


class ScheduledMessageCreate(WriteSchema):
    endpoint_id: UUID | None = None
    connection_key: str = Field(min_length=1, max_length=120)
    channel: OmnichannelChannel
    body: str | None = None
    template_id: str | None = Field(default=None, max_length=180)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    message_payload: dict[str, Any] = Field(default_factory=dict)
    collaboration: OmnichannelMessageCollaborationCreate | None = Field(
        default=None,
        exclude=True,
    )
    scheduled_at: datetime
    timezone: str = Field(default="America/Sao_Paulo", min_length=1, max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=180)

    @model_validator(mode="after")
    def require_content(self) -> ScheduledMessageCreate:
        raw_message_payload = dict(self.message_payload or {})
        embedded_collaboration = raw_message_payload.pop("collaboration", None)
        normalized_collaboration = normalize_message_collaboration(
            self.collaboration or embedded_collaboration
        )
        if self.collaboration is not None and embedded_collaboration is not None:
            if normalize_message_collaboration(embedded_collaboration) != self.collaboration:
                raise ValueError(
                    "Os contextos de colaboração do agendamento são divergentes."
                )
        if (
            not (self.body or "").strip()
            and not self.template_id
            and not self.attachments
            and not raw_message_payload
        ):
            raise ValueError("A mensagem agendada precisa de conteúdo, modelo ou anexo.")
        if self.channel == OmnichannelChannel.whatsapp:
            from app.services.whatsapp.cloud_payload import normalize_internal_payload

            raw_message_payload = normalize_internal_payload(
                raw_message_payload,
                body=self.body,
                template_id=self.template_id,
                attachments=self.attachments,
            )
        if normalized_collaboration is not None:
            self.collaboration = normalized_collaboration
            raw_message_payload["collaboration"] = normalized_collaboration.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )
        self.message_payload = raw_message_payload
        return self


class ScheduledMessageUpdate(WriteSchema):
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=180)
    scheduled_at: datetime | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=80)
    action: Literal["reschedule", "pause", "resume", "cancel"]
    reason: str | None = Field(default=None, max_length=1000)


class ScheduledMessageRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    thread_id: UUID
    endpoint_id: UUID | None = None
    connection_id: UUID | None = None
    canonical_message_id: UUID | None = None
    created_by_user_id: UUID | None = None
    author_type: str
    author_label: str
    connection_key: str
    channel: str
    body: str | None = None
    template_id: str | None = None
    attachments: list[dict[str, Any]]
    message_payload: dict[str, Any]
    scheduled_at: datetime
    timezone: str
    state: str
    lease_generation: int
    attempts: int
    max_attempts: int
    next_attempt_at: datetime | None = None
    delivery_attempted_at: datetime | None = None
    provider_supports_idempotency: bool
    provider_message_id: str | None = None
    delivery_uncertain_at: datetime | None = None
    completed_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    idempotency_key: str
    cancellation_reason: str | None = None
    provider_receipt: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ScheduledMessageEventRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    scheduled_message_id: UUID
    thread_id: UUID
    connection_id: UUID | None = None
    canonical_message_id: UUID | None = None
    provider: str
    event_type: str
    previous_state: str
    next_state: str
    idempotency_key: str
    payload_sha256: str
    lease_owner: str | None = None
    lease_generation: int | None = None
    external_event_id: str | None = None
    external_message_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    occurred_at: datetime
    received_at: datetime
    event_metadata: dict[str, Any]


class OmnichannelAuditFindingRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    thread_id: UUID
    matter_id: UUID | None = None
    message_id: UUID | None = None
    finding_type: str
    severity: Literal["info", "warning", "critical"]
    state: Literal["open", "retry_scheduled", "waiting_human", "resolved", "dismissed"]
    title: str
    summary: str
    dedupe_key: str
    evidence: dict[str, Any]
    remediation: dict[str, Any]
    detected_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None = None
    retry_count: int
    max_retries: int
    next_retry_at: datetime | None = None
    internal_note_message_id: UUID | None = None
    retry_schedule_id: UUID | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class OmnichannelAuditDashboardRead(CamelCaseSchema):
    findings: list[OmnichannelAuditFindingRead] = Field(default_factory=list)
    total: int = 0
    open_count: int = 0
    critical_count: int = 0
    retry_scheduled_count: int = 0
    waiting_human_count: int = 0
    resolved_count: int = 0
    inspected_threads: int = 0
    healthy_threads: int = 0
    coverage_percent: float = 100.0
    response_coverage_percent: float = 100.0
    delivery_integrity_percent: float = 100.0
    generated_at: datetime


class OmnichannelAuditRunRequest(WriteSchema):
    thread_id: UUID | None = None
    limit: int = Field(default=250, ge=1, le=500)


class OmnichannelAuditFindingAction(WriteSchema):
    action: Literal["resolve", "dismiss", "retry"]
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=1000)


class CallSessionCreate(WriteSchema):
    endpoint_id: UUID | None = None
    connection_key: str = Field(min_length=1, max_length=120)
    channel: OmnichannelChannel
    direction: Literal["inbound", "outbound"]
    permission_state: Literal["unknown", "requested", "granted", "denied"] = "unknown"
    idempotency_key: str = Field(min_length=8, max_length=180)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CallSessionTransition(WriteSchema):
    idempotency_key: str = Field(min_length=8, max_length=180)
    state: Literal[
        "permission_requested",
        "approved_local",
        "ringing",
        "active",
        "completed",
        "failed",
        "cancelled",
    ]
    permission_state: Literal["unknown", "requested", "granted", "denied"] | None = None
    provider_call_id: str | None = Field(default=None, max_length=255)
    failure_reason: str | None = Field(default=None, max_length=1000)
    summary: str | None = None
    duration_seconds: int | None = Field(default=None, ge=0, le=86400)
    external_event_id: str | None = Field(default=None, max_length=255)
    occurred_at: datetime | None = None
    provider_payload: dict[str, Any] = Field(default_factory=dict)


class InternalCallSessionTransition(CallSessionTransition):
    """Trusted call transition carrying platform-only provider accounting."""

    cost_amount: Decimal | None = Field(default=None, ge=0)
    cost_currency: str | None = Field(default=None, min_length=3, max_length=3)
    pricing_category: str | None = Field(default=None, max_length=120)
    provider_invoice_reference: str | None = Field(default=None, max_length=255)

    @field_validator("cost_currency")
    @classmethod
    def normalize_call_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @model_validator(mode="after")
    def validate_call_cost_pair(self) -> InternalCallSessionTransition:
        if (self.cost_amount is None) != (self.cost_currency is None):
            raise ValueError("Custo de chamada exige amount e currency em conjunto.")
        return self


class CallSessionRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    thread_id: UUID
    endpoint_id: UUID | None = None
    connection_id: UUID | None = None
    connection_key: str
    channel: str
    direction: str
    state: str
    permission_state: str
    provider_call_id: str | None = None
    requested_at: datetime
    approved_at: datetime | None = None
    started_at: datetime | None = None
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: int
    failure_reason: str | None = None
    transcript_object_id: UUID | None = None
    recording_object_id: UUID | None = None
    summary: str | None = None
    idempotency_key: str
    call_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class CallEventRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    call_id: UUID
    thread_id: UUID
    connection_id: UUID | None = None
    provider: str
    idempotency_key: str
    external_event_id: str | None = None
    previous_state: str
    next_state: str
    payload_sha256: str
    provider_call_id: str | None = None
    duration_seconds: int | None = None
    failure_reason: str | None = None
    occurred_at: datetime
    received_at: datetime
    event_metadata: dict[str, Any]


class CallCostLedgerEntryRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    call_id: UUID
    thread_id: UUID
    call_event_id: UUID
    provider: str
    amount: Decimal
    currency: str
    quantity: Decimal
    unit: str
    pricing_category: str | None = None
    provider_invoice_reference: str | None = None
    idempotency_key: str
    occurred_at: datetime
    recorded_at: datetime
    entry_metadata: dict[str, Any]


class CallEventEvidenceRead(CamelCaseSchema):
    event: CallEventRead


class TimelineItemRead(CamelCaseSchema):
    id: UUID
    kind: Literal["message", "channel_event", "routing", "scheduled_message", "call"]
    occurred_at: datetime
    channel: str | None = None
    event_type: str
    direction: str | None = None
    author_label: str | None = None
    body: str | None = None
    visibility: str = "internal"
    status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ThreadTimelineRead(CamelCaseSchema):
    thread_id: UUID
    items: list[TimelineItemRead]
    next_cursor: str | None = None


class ThreadOmnichannelContextRead(CamelCaseSchema):
    thread: OmnichannelThreadRead
    identity: OmnichannelIdentityRead | None = None
    channels: list[OmnichannelChannelSummaryRead] = Field(default_factory=list)
    bindings: list[OmnichannelBindingRead] = Field(default_factory=list)
    action_intents: list[OmnichannelActionIntentRead] = Field(default_factory=list)
    scheduled_messages: list[ScheduledMessageRead] = Field(default_factory=list)
    calls: list[CallSessionRead] = Field(default_factory=list)
    automation_runs: list[AutomationRunSummaryRead] = Field(default_factory=list)
    timeline: list[TimelineItemRead] = Field(default_factory=list)
    external_gate_open: bool = False
    generated_at: datetime
