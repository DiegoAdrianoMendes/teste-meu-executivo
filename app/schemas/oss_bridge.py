"""Schemas for generic OSS bridge persistence operations."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from pydantic import (
    AliasChoices,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models.oss_bridge import (
    ExternalEntitySyncState,
    IntegrationEventReceiptStatus,
    OSSBridgeProvider,
    sanitize_oss_json,
)
from app.schemas.base import CamelCaseSchema, WriteSchema

Identifier100 = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]
Identifier255 = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
JSONObject = dict[str, JsonValue]


def _sanitize_object(value: Any) -> JSONObject:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("OSS bridge metadata and payload must be JSON objects.")
    return cast(JSONObject, sanitize_oss_json(value))


class ExternalEntityLinkUpsert(WriteSchema):
    provider: OSSBridgeProvider
    klm_entity_type: Identifier100
    klm_entity_id: UUID
    external_account_id: Identifier255
    external_entity_type: Identifier100
    external_entity_id: Identifier255
    metadata: JSONObject = Field(default_factory=dict)
    sync_state: ExternalEntitySyncState = ExternalEntitySyncState.pending
    last_synced_at: datetime | None = None
    sync_error: str | None = Field(default=None, max_length=4000)

    _sanitize_metadata = field_validator("metadata", mode="before")(_sanitize_object)


class ExternalEntityLinkRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    provider: OSSBridgeProvider
    klm_entity_type: str
    klm_entity_id: UUID
    external_account_id: str
    external_entity_type: str
    external_entity_id: str
    metadata: JSONObject = Field(
        default_factory=dict,
        validation_alias=AliasChoices("link_metadata", "metadata"),
    )
    sync_state: ExternalEntitySyncState
    last_synced_at: datetime | None = None
    sync_error: str | None = None
    created_at: datetime
    updated_at: datetime


class IntegrationEventReceiptCreate(WriteSchema):
    provider: OSSBridgeProvider
    external_event_id: Identifier255
    payload: JSONObject = Field(default_factory=dict)
    status: IntegrationEventReceiptStatus = IntegrationEventReceiptStatus.received


class IntegrationEventReceiptRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    provider: OSSBridgeProvider
    external_event_id: str
    payload_hash: str
    status: IntegrationEventReceiptStatus
    payload: JSONObject
    received_at: datetime
    processed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class OSSServiceStatusRead(CamelCaseSchema):
    key: str
    name: str
    role: str
    state: str
    state_label: str
    operational: bool = False
    auth_state: str = "unknown"
    integration_state: str = "offline"
    source_state: str = "missing"
    source_head: str | None = None
    source_components: dict[str, str] = Field(default_factory=dict)
    source_dirty: bool = False
    source_details: list[str] = Field(default_factory=list)
    local_url: str | None = None
    version: str
    reference_version: str | None = None
    runtime_policy: str = "pinned"
    runtime_notice: str | None = None
    source_commit: str | None = None
    capabilities: list[str]
    preserved_features: list[str]
    offline_capabilities: list[str] = Field(default_factory=list)
    gated_capabilities: list[str] = Field(default_factory=list)
    embeddable: bool = False
    last_checked_at: datetime


class OSSRuntimeStatusRead(CamelCaseSchema):
    mode: str = "local_only"
    external_actions_enabled: bool = False
    services: list[OSSServiceStatusRead]


class OSSCapabilityCommandsEndpointRead(CamelCaseSchema):
    enabled: bool
    state: Literal["dispatcher_available", "disabled_no_dispatcher"]
    path: str
    replacement_path: str


class OSSCapabilityRead(CamelCaseSchema):
    id: str
    provider: str
    key: str
    label: str
    category: str
    description: str
    disposition: str
    source_disposition: str
    current_exposure: str
    source_exposure: str
    surface: str
    route: str | None = None
    target_route: str | None = None
    source_route: str | None = None
    source_target_route: str | None = None
    native_experience: bool = False
    native_experience_verified: bool = False
    native_route: str
    native_mode: str
    primary_experience: str = "native"
    primary_deep_link: bool = False
    external_execution_state: str = "local_only"
    round_trip: bool = False
    launchable: bool = False
    motor_path: str | None = None
    integration_level: str = "unexposed"
    contract_eligible: bool = False
    contract_verified: bool = False
    external_effect_verified: bool = False
    commands_endpoint: OSSCapabilityCommandsEndpointRead
    verification_level: Literal[
        "contract_and_native_read",
        "local_equivalent",
        "configuration_only",
        "responsive_substitution",
    ]
    reason: str
    source_reason: str
    upstream_evidence: list[str]
    klm_evidence: list[str] = Field(default_factory=list)
    test_evidence: list[str] = Field(default_factory=list)


class OSSCapabilitySummaryRead(CamelCaseSchema):
    total: int
    by_provider: dict[str, int]
    by_disposition: dict[str, int]
    by_exposure: dict[str, int]
    by_integration_level: dict[str, int] = Field(default_factory=dict)
    unclassified: int
    native_experience: int = 0
    native_experience_verified: int = 0
    native_route: int = 0
    primary_deep_link: int = 0
    contract_eligible: int = 0
    contract_verified: int = 0
    external_effect_verified: int = 0
    gated_configuration: int = 0


class OSSCapabilityProofRunRead(CamelCaseSchema):
    id: str
    status: Literal["passed"]
    command: list[str]
    generated_at: str | None = None
    output_sha256: str
    artifact_sha256: str


class OSSCapabilityQAVerificationRead(CamelCaseSchema):
    state: Literal["verified", "partial", "unverified"]
    reason: str
    manifest_path: str
    generated_at: str | None = None
    native_ids_verified: bool = False
    contract_ids_verified: bool = False
    action_ids_verified: bool = False
    verified_native_capability_ids: list[str] = Field(default_factory=list)
    verified_contract_capability_ids: list[str] = Field(default_factory=list)
    verified_action_ids_digest: str | None = None
    action_occurrences_contract_validated: int = 0
    unique_commands_contract_validated: int = 0
    external_effects_executed: int | None = None
    contract_digest: str
    native_ids_digest: str
    native_count: int
    contract_ids_digest: str
    contract_count: int
    action_ids_digest: str
    action_count: int
    verification_file_count: int = 0
    verification_files_digest: str | None = None
    runtime_source_digest: str
    runtime_source_file_count: int = 0
    runtime_source_matches_current: bool = False
    proof_runs: list[OSSCapabilityProofRunRead] = Field(default_factory=list)


class OSSCapabilityRegistryRead(CamelCaseSchema):
    schema_version: str
    scope: str
    capabilities: list[OSSCapabilityRead]
    summary: OSSCapabilitySummaryRead
    qa_verification: OSSCapabilityQAVerificationRead


class ChatwootProjectionStatsRead(CamelCaseSchema):
    total_messages: int = Field(ge=0)
    projected_messages: int = Field(ge=0)
    received_messages: int = Field(ge=0)
    pending_messages: int = Field(ge=0)
    failed_messages: int = Field(ge=0)
    last_synced_at: datetime | None = None
    external_channel_delivery_enabled: Literal[False] = False


class ChatwootThreadSyncRead(ChatwootProjectionStatsRead):
    status: Literal["synced"] = "synced"
    thread_id: UUID
    conversation_id: str
    projected_now: int = Field(ge=0)
    contact_created: bool = False
    rate_limited: bool = False
    source_of_truth: Literal["klm"] = "klm"


class ChatwootRoutingContextRead(CamelCaseSchema):
    linked: bool
    thread_id: UUID
    conversation_id: str | None = None
    conversation: JSONObject | None = None
    inboxes: list[JSONObject] = Field(default_factory=list)
    teams: list[JSONObject] = Field(default_factory=list)
    labels: list[JSONObject] = Field(default_factory=list)
    agents: list[JSONObject] = Field(default_factory=list)
    canned_responses: list[JSONObject] = Field(default_factory=list)
    projection: ChatwootProjectionStatsRead
    source_of_truth: Literal["klm"] = "klm"


class ChatwootRoutingWrite(WriteSchema):
    status: Literal["open", "resolved", "pending", "snoozed"] | None = None
    priority: Literal["low", "medium", "high", "urgent"] | None = None
    assignee_id: int | None = Field(default=None, ge=1)
    team_id: int | None = Field(default=None, ge=1)
    labels: list[str] | None = Field(default=None, max_length=50)

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [label.strip() for label in value]
        if any(not label or len(label) > 100 for label in normalized):
            raise ValueError("Cada label deve conter de 1 a 100 caracteres.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Labels duplicadas não são permitidas.")
        return normalized

    @model_validator(mode="after")
    def validate_change(self) -> ChatwootRoutingWrite:
        if not self.model_fields_set:
            raise ValueError("Informe ao menos uma alteração de roteamento.")
        return self


class RocketChatThreadContextRead(CamelCaseSchema):
    linked: bool
    thread_id: UUID
    room_id: str | None = None
    room_type: Literal["c", "p"] | None = None
    room: JSONObject | None = None
    members: list[JSONObject] = Field(default_factory=list)
    messages: list[JSONObject] = Field(default_factory=list)
    discussions: list[JSONObject] = Field(default_factory=list)
    runtime_compatibility: str
    audited_reference: str
    source_of_truth: Literal["klm"] = "klm"
    sensitive_data_hidden: bool = False


class ChatterMateContextRead(CamelCaseSchema):
    linked: bool
    organization_id: str
    agents: list[JSONObject] = Field(default_factory=list)
    workflows: list[JSONObject] = Field(default_factory=list)
    analytics: JSONObject = Field(default_factory=dict)
    performance: JSONObject = Field(default_factory=dict)
    people: JSONObject = Field(default_factory=dict)
    people_stats: JSONObject = Field(default_factory=dict)
    mcp_tools: list[JSONObject] = Field(default_factory=list)
    groups: list[JSONObject] = Field(default_factory=list)
    notifications: list[JSONObject] = Field(default_factory=list)
    knowledge: JSONObject = Field(default_factory=dict)
    time_range: Literal["24h", "7d", "30d", "90d"] = "7d"
    source_of_truth: Literal["klm"] = "klm"
    sensitive_data_hidden: bool = False
    external_execution_enabled: Literal[False] = False


class ChatterMateAgentSyncRead(CamelCaseSchema):
    status: Literal["synced"] = "synced"
    created: int = Field(ge=0)
    updated: int = Field(ge=0)
    adopted: int = Field(default=0, ge=0)
    conflicts: int = Field(default=0, ge=0)
    seen: int = Field(ge=0)


class ChatterMateFlowSyncRead(CamelCaseSchema):
    status: Literal["synced"] = "synced"
    flow_id: UUID
    agent_id: str
    workflow_id: str
    agent_action: Literal["created", "updated", "unchanged"]
    workflow_action: Literal["created", "updated", "unchanged"]
    graph_action: Literal["replaced", "unchanged"]
    node_count: int = Field(ge=0)
    connection_count: int = Field(ge=0)
    revision: int = Field(ge=0)
    source_digest: str = Field(min_length=64, max_length=64)
    source_of_truth: Literal["klm"] = "klm"
    external_execution_enabled: Literal[False] = False


class ChatterMateFlowContextRead(CamelCaseSchema):
    linked: bool
    flow_id: UUID
    revision: int = Field(ge=0)
    agent_id: str | None = None
    workflow_id: str | None = None
    workflow_status: str | None = None
    node_count: int = Field(default=0, ge=0)
    connection_count: int = Field(default=0, ge=0)
    source_digest: str | None = Field(default=None, min_length=64, max_length=64)
    digest_matches: bool = False
    graph_matches: bool = False
    stale_link: bool = False
    source_of_truth: Literal["klm"] = "klm"
    external_execution_enabled: Literal[False] = False


class BottleCRMProjectionRead(CamelCaseSchema):
    linked: bool
    organization_id: str
    total_links: int = Field(ge=0)
    synced_links: int = Field(ge=0)
    failed_links: int = Field(ge=0)
    stale_links: int = Field(ge=0)
    by_entity_type: dict[str, int] = Field(default_factory=dict)
    last_synced_at: datetime | None = None
    source_of_truth: Literal["klm"] = "klm"
    external_execution_enabled: Literal[False] = False


class BottleCRMItemRead(CamelCaseSchema):
    id: str
    entity_type: str
    klm_entity_id: UUID | None = None
    klm_entity_type: str | None = None
    name: str
    stage: str | None = None
    status: str | None = None
    priority: str | None = None
    amount: float | None = None
    currency: str = "BRL"
    probability: int | None = None
    days_in_stage: int | None = None
    aging_status: str | None = None
    due_date: str | None = None
    next_follow_up: str | None = None
    overdue: bool = False
    sla_breached: bool = False
    escalation_count: int = 0


class BottleCRMColumnRead(CamelCaseSchema):
    id: str
    name: str
    order: int
    color: str | None = None
    stage_type: str | None = None
    wip_limit: int | None = None
    item_count: int = Field(ge=0)
    items: list[BottleCRMItemRead] = Field(default_factory=list)


class BottleCRMGoalRead(CamelCaseSchema):
    id: str
    name: str
    period_type: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    target: float = 0
    achieved: float = 0
    percent: float = 0
    active: bool = True


class BottleCRMAgingConfigRead(CamelCaseSchema):
    stage: str
    expected_days: int = Field(ge=1)
    warning_days: int | None = Field(default=None, ge=0)


class BottleCRMFunnelContextRead(CamelCaseSchema):
    projection: BottleCRMProjectionRead
    counts: dict[str, int] = Field(default_factory=dict)
    urgent_counts: dict[str, int] = Field(default_factory=dict)
    pipeline: list[JSONObject] = Field(default_factory=list)
    revenue: JSONObject = Field(default_factory=dict)
    opportunity_columns: list[BottleCRMColumnRead] = Field(default_factory=list)
    lead_columns: list[BottleCRMColumnRead] = Field(default_factory=list)
    case_columns: list[BottleCRMColumnRead] = Field(default_factory=list)
    goals: list[BottleCRMGoalRead] = Field(default_factory=list)
    aging_config: list[BottleCRMAgingConfigRead] = Field(default_factory=list)
    notifications_unread: int = Field(ge=0)
    sensitive_data_hidden: bool = False


class BottleCRMRecordRead(BottleCRMItemRead):
    sync_state: ExternalEntitySyncState
    last_synced_at: datetime | None = None
    first_response_deadline: str | None = None
    resolution_deadline: str | None = None


class BottleCRMMatterContextRead(CamelCaseSchema):
    projection: BottleCRMProjectionRead
    matter_id: UUID
    records: list[BottleCRMRecordRead] = Field(default_factory=list)
    sensitive_data_hidden: bool = False


class BottleCRMBoardRead(CamelCaseSchema):
    id: str
    name: str
    description: str | None = None
    column_count: int = Field(ge=0)


class BottleCRMWorkContextRead(CamelCaseSchema):
    projection: BottleCRMProjectionRead
    task_columns: list[BottleCRMColumnRead] = Field(default_factory=list)
    tasks: list[BottleCRMItemRead] = Field(default_factory=list)
    boards: list[BottleCRMBoardRead] = Field(default_factory=list)
    notifications_unread: int = Field(ge=0)
    sensitive_data_hidden: bool = False


class BottleCRMFinanceItemRead(CamelCaseSchema):
    id: str
    entity_type: str
    name: str
    number: str | None = None
    status: str | None = None
    amount: float | None = None
    amount_due: float | None = None
    currency: str = "BRL"
    issue_date: str | None = None
    due_date: str | None = None
    valid_until: str | None = None
    category: str | None = None
    active: bool = True


class BottleCRMFinanceBucketRead(CamelCaseSchema):
    bucket: str
    count: int = Field(ge=0)
    amount: float = Field(ge=0)


class BottleCRMRevenuePointRead(CamelCaseSchema):
    period: str | None = None
    revenue: float = Field(ge=0)
    count: int = Field(ge=0)


class BottleCRMFinanceContextRead(CamelCaseSchema):
    projection: BottleCRMProjectionRead
    summary: JSONObject = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)
    overdue: JSONObject = Field(default_factory=dict)
    recent_activity: JSONObject = Field(default_factory=dict)
    estimate_summary: dict[str, int] = Field(default_factory=dict)
    invoices: list[BottleCRMFinanceItemRead] = Field(default_factory=list)
    estimates: list[BottleCRMFinanceItemRead] = Field(default_factory=list)
    products: list[BottleCRMFinanceItemRead] = Field(default_factory=list)
    aging: list[BottleCRMFinanceBucketRead] = Field(default_factory=list)
    revenue_series: list[BottleCRMRevenuePointRead] = Field(default_factory=list)
    notifications_unread: int = Field(ge=0)
    sensitive_data_hidden: bool = False


class OSSServiceLaunchRead(CamelCaseSchema):
    service: str
    launch_url: str
    expires_at: datetime
    embeddable: bool = False
    context: dict[str, JsonValue] = Field(default_factory=dict)


class OSSServiceLaunchContextWrite(WriteSchema):
    return_to: str = Field(default="/app", min_length=4, max_length=512)
    capability_id: str | None = Field(default=None, max_length=160)
    matter_id: UUID | None = None
    thread_id: UUID | None = None
    flow_id: UUID | None = None
    surface: str | None = Field(default=None, max_length=100)

    @field_validator("return_to")
    @classmethod
    def validate_return_to(cls, value: str) -> str:
        if not value.startswith("/app") or value.startswith("//") or "\\" in value:
            raise ValueError("returnTo deve apontar para uma rota interna do KLM.")
        return value
