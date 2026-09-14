"""Strict contracts for local-ready Meta Cloud API Groups support."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.schemas.base import CamelCaseSchema, WriteSchema

MetaGroupWebhookField = Literal[
    "group_lifecycle_update",
    "group_participants_update",
    "group_settings_update",
    "group_status_update",
]
MetaGroupProductMode = Literal[
    "official_api",
    "cloud_api",
    "business_app",
    "coexistence",
    "multi_solution",
    "unsupported",
]
MetaGroupKind = Literal["local_internal", "meta_cloud_group"]
MetaGroupObaState = Literal["unknown", "eligible", "ineligible"]
MetaGroupObaEvidenceSource = Literal[
    "unverified",
    "meta_api",
    "meta_business_manager",
]


class StrictMetaGroupWrite(WriteSchema):
    model_config = ConfigDict(extra="forbid")


class MetaGroupCapabilityConfigure(StrictMetaGroupWrite):
    expected_version: int = Field(default=0, ge=0)
    phone_number_id: str = Field(min_length=1, max_length=180)
    waba_id: str | None = Field(default=None, min_length=1, max_length=180)
    developer_app_configured: bool = False
    webhook_callback_verified: bool = False
    subscribed_webhook_fields: list[MetaGroupWebhookField] = Field(
        default_factory=list,
        max_length=4,
    )
    oba_eligibility_state: MetaGroupObaState = "unknown"
    oba_evidence_source: MetaGroupObaEvidenceSource = "unverified"
    oba_evidence_reference: str | None = Field(default=None, min_length=3, max_length=500)
    oba_evidence_recorded_at: datetime | None = None
    evidence_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("subscribed_webhook_fields")
    @classmethod
    def validate_unique_webhook_fields(
        cls, fields: list[MetaGroupWebhookField]
    ) -> list[MetaGroupWebhookField]:
        if len(fields) != len(set(fields)):
            raise ValueError("Each Meta group webhook field may appear only once.")
        return fields

    @model_validator(mode="after")
    def validate_external_oba_evidence(self) -> MetaGroupCapabilityConfigure:
        if self.oba_evidence_source == "unverified":
            if self.oba_evidence_reference or self.oba_evidence_recorded_at:
                raise ValueError("Unverified OBA state cannot carry a provider evidence reference.")
        elif not self.oba_evidence_reference or not self.oba_evidence_recorded_at:
            raise ValueError(
                "Externally evidenced OBA state requires reference and observation time."
            )
        return self


class MetaGroupOfficialLimitsRead(CamelCaseSchema):
    invite_only: Literal[True] = True
    max_participants: Literal[8] = 8
    max_groups_per_business_number: Literal[10000] = 10000


class MetaGroupCapabilityGateRead(CamelCaseSchema):
    workspace_id: UUID
    connection_id: UUID
    provider: str
    product_mode: MetaGroupProductMode
    developer_app_state: Literal["not_ready", "ready"]
    developer_app_ready: bool
    developer_app_missing_requirements: list[str] = Field(default_factory=list)
    required_webhook_fields: list[MetaGroupWebhookField]
    subscribed_webhook_fields: list[MetaGroupWebhookField]
    oba_eligibility_state: MetaGroupObaState
    oba_evidence_source: MetaGroupObaEvidenceSource
    oba_gate_open: bool
    product_mode_supported: bool
    coexistence_denied: bool
    business_app_denied: bool
    multi_solution_denied: bool
    runtime_external_actions_enabled: bool
    external_capability_gate_open: bool
    external_execution_gate_open: Literal[False] = False
    external_dispatch_implemented: Literal[False] = False
    local_preparation_available: Literal[True] = True
    capability_denial_reasons: list[str] = Field(default_factory=list)
    execution_denial_reasons: list[str] = Field(default_factory=list)
    limits: MetaGroupOfficialLimitsRead = Field(default_factory=MetaGroupOfficialLimitsRead)
    observed_at: datetime


class MetaGroupCapabilityRead(CamelCaseSchema):
    id: UUID | None = None
    workspace_id: UUID
    connection_id: UUID
    configured: bool
    phone_number_id: str | None = None
    waba_id: str | None = None
    developer_app_configured: bool
    webhook_callback_verified: bool
    oba_evidence_reference: str | None = None
    oba_evidence_recorded_at: datetime | None = None
    evidence_metadata: dict[str, Any] = Field(default_factory=dict)
    version: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    gate: MetaGroupCapabilityGateRead
    deduplicated: bool = False


class MetaGroupCreate(StrictMetaGroupWrite):
    subject: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2048)
    join_approval_mode: Literal["auto_approve", "approval_required"] = "auto_approve"

    @field_validator("subject")
    @classmethod
    def strip_subject(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Group subject cannot be blank.")
        return value


class MetaGroupOfficialCreationPrepare(MetaGroupCreate):
    """Prepare an official creation request; no local group projection is created."""


class MetaGroupOperationRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    group_id: UUID | None = None
    connection_id: UUID
    requested_by_user_id: UUID | None = None
    operation_type: Literal[
        "create_local_group",
        "prepare_official_group_creation",
        "prepare_group_message",
    ]
    state: Literal["local_ready", "blocked_by_capability"]
    idempotency_key: str
    request_payload_sha256: str
    request_payload: dict[str, Any]
    capability_snapshot: dict[str, Any]
    external_dispatch_executed: Literal[False] = False
    created_at: datetime
    updated_at: datetime


class MetaGroupPinnedMessageSet(StrictMetaGroupWrite):
    text: str = Field(min_length=1, max_length=4096)


class MetaGroupPinnedMessageRead(CamelCaseSchema):
    text: str
    pinned_by_user_id: UUID | None = None
    pinned_at: datetime


class MetaGroupRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    connection_id: UUID
    capability_id: UUID | None = None
    created_by_user_id: UUID | None = None
    group_kind: MetaGroupKind
    is_local_internal: bool
    is_official_meta_group: bool
    provider_group_id: str | None = None
    provider_request_id: str | None = None
    subject: str
    description: str | None = None
    join_approval_mode: Literal["auto_approve", "approval_required"]
    state: str
    invite_link: str | None = None
    invite_link_simulated: bool = False
    participant_count: int = Field(ge=1, le=8)
    create_idempotency_key: str
    request_payload_sha256: str
    last_lifecycle_at: datetime | None = None
    last_message_preview: str | None = None
    last_message_at: datetime | None = None
    pinned_message: MetaGroupPinnedMessageRead | None = None
    version: int
    group_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    gate: MetaGroupCapabilityGateRead


class MetaGroupCreateRead(CamelCaseSchema):
    group: MetaGroupRead
    operation: MetaGroupOperationRead
    deduplicated: bool = False


class MetaGroupOfficialCreationPreparationRead(CamelCaseSchema):
    operation: MetaGroupOperationRead
    gate: MetaGroupCapabilityGateRead
    group_projection_created: Literal[False] = False
    projection_source: Literal["verified_group_lifecycle_update"] = (
        "verified_group_lifecycle_update"
    )
    provider_request_path: str | None = None
    provider_request_payload_ready: bool
    provider_request_payload: dict[str, Any] | None = None
    external_dispatch_executed: Literal[False] = False
    blocked_reasons: list[str] = Field(default_factory=list)
    deduplicated: bool = False


class MetaGroupCollectionRead(CamelCaseSchema):
    items: list[MetaGroupRead] = Field(default_factory=list)
    total: int = 0


class MetaGroupInviteLinkRotateRead(CamelCaseSchema):
    group: MetaGroupRead
    rotated: bool
    simulated: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    deduplicated: bool = False


MetaGroupParticipantState = Literal[
    "invited", "join_requested", "joined", "left", "removed", "rejected"
]
MetaGroupParticipantSource = Literal["invite_link", "join_request", "provider_webhook"]


class MetaGroupParticipantRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    group_id: UUID
    provider_user_id: str
    wa_id: str | None = None
    business_scoped_user_id: str | None = None
    username: str | None = None
    join_request_id: str | None = None
    state: MetaGroupParticipantState
    source: MetaGroupParticipantSource
    role: Literal["member", "admin"]
    joined_at: datetime | None = None
    left_at: datetime | None = None
    participant_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class MetaGroupParticipantCollectionRead(CamelCaseSchema):
    items: list[MetaGroupParticipantRead] = Field(default_factory=list)
    total: int = 0


class MetaGroupParticipantRemoveRead(CamelCaseSchema):
    participant: MetaGroupParticipantRead
    group: MetaGroupRead
    executed: bool
    prepared: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    deduplicated: bool = False


class MetaGroupMessageRead(CamelCaseSchema):
    id: UUID
    group_id: UUID
    requested_by_user_id: UUID | None = None
    text: str | None = None
    message: dict[str, Any]
    state: Literal["local_ready", "blocked_by_capability"]
    external_dispatch_executed: Literal[False] = False
    created_at: datetime


class MetaGroupMessageCollectionRead(CamelCaseSchema):
    items: list[MetaGroupMessageRead] = Field(default_factory=list)
    total: int = 0


class MetaGroupMessagePrepare(StrictMetaGroupWrite):
    message: dict[str, Any]
    require_external_capability: bool = False

    @field_validator("message")
    @classmethod
    def require_message_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("A group message payload is required.")
        return value


class MetaGroupMessagePreparationRead(CamelCaseSchema):
    group: MetaGroupRead
    operation: MetaGroupOperationRead
    recipient_type: Literal["group"] = "group"
    to_group_id: str | None = None
    provider_payload_ready: bool
    provider_payload: dict[str, Any] | None = None
    external_dispatch_executed: Literal[False] = False
    blocked_reasons: list[str] = Field(default_factory=list)
    deduplicated: bool = False


class MetaGroupWebhookParticipant(StrictMetaGroupWrite):
    wa_id: str | None = Field(default=None, min_length=1, max_length=80)
    user_id: str | None = Field(default=None, min_length=1, max_length=255)
    username: str | None = Field(default=None, min_length=1, max_length=255)
    join_request_id: str | None = Field(default=None, min_length=1, max_length=255)
    role: Literal["member", "admin"] = "member"

    @model_validator(mode="after")
    def require_provider_identifier(self) -> MetaGroupWebhookParticipant:
        if not self.user_id and not self.wa_id and not self.username:
            raise ValueError("A provider participant identifier is required.")
        return self


class MetaGroupLifecycleIngest(StrictMetaGroupWrite):
    webhook_field: MetaGroupWebhookField
    external_event_id: str = Field(min_length=1, max_length=255)
    provider_group_id: str = Field(min_length=1, max_length=512)
    prepared_operation_id: UUID | None = None
    event_type: str = Field(min_length=1, max_length=80)
    provider_request_id: str | None = Field(default=None, max_length=255)
    status: str | None = Field(default=None, max_length=80)
    subject: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=2048)
    invite_link: str | None = Field(default=None, max_length=1000)
    join_approval_mode: Literal["auto_approve", "approval_required"] | None = None
    participants: list[MetaGroupWebhookParticipant] = Field(
        default_factory=list,
        max_length=8,
    )
    occurred_at: datetime
    ingress_evidence_source: Literal["verified_provider_adapter"]
    ingress_evidence_reference: str = Field(min_length=3, max_length=500)
    event_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_event_shape(self) -> MetaGroupLifecycleIngest:
        if self.webhook_field == "group_participants_update" and not self.participants:
            raise ValueError("Participant updates require at least one participant.")
        return self


class MetaGroupLifecycleEventRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    group_id: UUID | None = None
    operation_id: UUID | None = None
    connection_id: UUID
    actor_user_id: UUID | None = None
    source: Literal["local_control", "provider_webhook"]
    webhook_field: MetaGroupWebhookField | None = None
    event_type: str
    external_event_id: str | None = None
    idempotency_key: str
    payload_sha256: str
    event_payload: dict[str, Any]
    state_snapshot: dict[str, Any]
    occurred_at: datetime
    received_at: datetime


class MetaGroupLifecycleIngestRead(CamelCaseSchema):
    event: MetaGroupLifecycleEventRead
    group: MetaGroupRead
    deduplicated: bool = False


__all__ = [
    "MetaGroupCapabilityConfigure",
    "MetaGroupCapabilityGateRead",
    "MetaGroupCapabilityRead",
    "MetaGroupCollectionRead",
    "MetaGroupCreate",
    "MetaGroupCreateRead",
    "MetaGroupInviteLinkRotateRead",
    "MetaGroupKind",
    "MetaGroupLifecycleEventRead",
    "MetaGroupLifecycleIngest",
    "MetaGroupLifecycleIngestRead",
    "MetaGroupMessageCollectionRead",
    "MetaGroupMessagePreparationRead",
    "MetaGroupMessagePrepare",
    "MetaGroupMessageRead",
    "MetaGroupObaEvidenceSource",
    "MetaGroupObaState",
    "MetaGroupOfficialLimitsRead",
    "MetaGroupOfficialCreationPreparationRead",
    "MetaGroupOfficialCreationPrepare",
    "MetaGroupOperationRead",
    "MetaGroupParticipantCollectionRead",
    "MetaGroupParticipantRead",
    "MetaGroupParticipantRemoveRead",
    "MetaGroupParticipantSource",
    "MetaGroupParticipantState",
    "MetaGroupPinnedMessageRead",
    "MetaGroupPinnedMessageSet",
    "MetaGroupProductMode",
    "MetaGroupRead",
    "MetaGroupWebhookField",
    "MetaGroupWebhookParticipant",
]
