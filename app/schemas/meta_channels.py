from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import ConfigDict, Field

from app.models.meta_channels import (
    ApprovedMessageStatus,
    FollowRelationshipStatus,
    MetaCampaignStatus,
    MetaChannelType,
    MetaConnectionStatus,
    SocialEventType,
)
from app.models.matter import KanbanColumnId
from app.models.whatsapp import HumanControlState, WaMessageDirection, WaMessageStatus, WaQueueStatus
from app.schemas.base import CamelCaseSchema, WriteSchema
from app.schemas.control_plane import MeetingRead
from app.schemas.matter import MatterRead


class MetaChannelConnectionRead(CamelCaseSchema):
    id: UUID
    channel_type: MetaChannelType
    page_id: Optional[str] = None
    instagram_account_id: Optional[str] = None
    external_account_id: Optional[str] = None
    account_name: Optional[str] = None
    username: Optional[str] = None
    scopes: list[str] = Field(default_factory=list)
    capability_matrix: dict[str, Any] = Field(default_factory=dict)
    status: MetaConnectionStatus
    webhook_registered: bool
    last_error: Optional[str] = None
    connected_at: Optional[datetime] = None
    disconnected_at: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None
    updated_at: datetime

class MetaChannelConnectionCreate(WriteSchema):
    page_id: Optional[str] = None
    instagram_account_id: Optional[str] = None
    external_account_id: Optional[str] = None
    account_name: Optional[str] = None
    username: Optional[str] = None
    scopes: list[str] = Field(default_factory=list)
    capability_matrix: dict[str, Any] = Field(default_factory=dict)
    status: MetaConnectionStatus = MetaConnectionStatus.pending
    webhook_registered: bool = False
    last_error: Optional[str] = None


class MetaSocialMessageRead(CamelCaseSchema):
    id: UUID
    direction: WaMessageDirection
    status: WaMessageStatus
    body: str
    author_name: Optional[str] = None
    is_public_reply: bool = False
    message_metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None

class MetaSocialConversationRead(CamelCaseSchema):
    id: UUID
    channel_type: MetaChannelType
    connection_id: UUID
    matter_id: Optional[UUID] = None
    assigned_user_id: Optional[UUID] = None
    assigned_user_name: Optional[str] = None
    external_thread_id: str
    external_participant_id: Optional[str] = None
    participant_name: str
    participant_handle: Optional[str] = None
    participant_avatar_url: Optional[str] = None
    event_type: SocialEventType
    queue_status: WaQueueStatus
    human_control_state: HumanControlState
    follow_status: FollowRelationshipStatus
    source_title: Optional[str] = None
    source_url: Optional[str] = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    active_flow_label: Optional[str] = None
    next_follow_up_at: Optional[datetime] = None
    last_message_preview: Optional[str] = None
    last_message_at: Optional[datetime] = None
    unread_count: int = 0
    policy_state: Optional[str] = None
    is_public_thread: bool = False
    messages: list[MetaSocialMessageRead] = Field(default_factory=list)
    sensitive_data_hidden: bool = False


class MetaSocialConversationCreate(WriteSchema):
    connection_id: UUID
    matter_id: Optional[UUID] = None
    external_thread_id: str
    external_participant_id: Optional[str] = None
    participant_name: str
    participant_handle: Optional[str] = None
    participant_avatar_url: Optional[str] = None
    event_type: SocialEventType = SocialEventType.direct_message
    queue_status: WaQueueStatus = WaQueueStatus.ai
    human_control_state: HumanControlState = HumanControlState.copilot
    follow_status: FollowRelationshipStatus = FollowRelationshipStatus.unknown
    source_title: Optional[str] = None
    source_url: Optional[str] = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    active_flow_label: Optional[str] = None
    next_follow_up_at: Optional[datetime] = None
    policy_state: Optional[str] = None
    is_public_thread: bool = False
    initial_message: Optional[str] = None


class MetaCommentThreadRead(CamelCaseSchema):
    id: UUID
    conversation_id: UUID
    participant_name: str
    participant_handle: Optional[str] = None
    event_type: SocialEventType
    source_title: Optional[str] = None
    source_url: Optional[str] = None
    follow_status: FollowRelationshipStatus
    last_message_preview: Optional[str] = None
    last_message_at: Optional[datetime] = None
    policy_state: Optional[str] = None
    sensitive_data_hidden: bool = False


class FollowStatusRefreshRequest(WriteSchema):
    status: FollowRelationshipStatus


class MetaSendMessageRequest(WriteSchema):
    body: str
    is_public_reply: bool = False


class MetaCopilotRequest(WriteSchema):
    tone: str = "clear"
    goal: Optional[str] = None


class MetaCopilotRead(CamelCaseSchema):
    model_config = ConfigDict(
        alias_generator=CamelCaseSchema.model_config.get("alias_generator"),
        populate_by_name=True,
        from_attributes=True,
        protected_namespaces=(),
    )

    summary: str
    recommended_action: str
    reply_suggestions: list[str] = Field(default_factory=list)
    suggested_public_reply: bool = False
    automation_enabled: bool = False
    automation_rule_id: Optional[UUID] = None
    automation_title: Optional[str] = None
    model_used: Optional[str] = None
    provider: Optional[str] = None


class MetaApprovedMessageRead(CamelCaseSchema):
    id: UUID
    channel_type: MetaChannelType
    message_kind: str
    title: str
    body: str
    cta_label: Optional[str] = None
    cta_url: Optional[str] = None
    status: ApprovedMessageStatus
    requires_following: bool = False
    restrictions: dict[str, Any] = Field(default_factory=dict)
    created_by_id: Optional[UUID] = None
    approved_by_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime

class MetaApprovedMessageCreate(WriteSchema):
    message_kind: str = "direct_message"
    title: str
    body: str
    cta_label: Optional[str] = None
    cta_url: Optional[str] = None
    status: ApprovedMessageStatus = ApprovedMessageStatus.draft
    requires_following: bool = False
    restrictions: dict[str, Any] = Field(default_factory=dict)


class MetaAutomationRuleRead(CamelCaseSchema):
    id: UUID
    channel_type: MetaChannelType
    title: str
    event_type: SocialEventType
    enabled: bool = True
    keyword: Optional[str] = None
    approved_message_id: Optional[UUID] = None
    action_config: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime

class MetaAutomationRuleCreate(WriteSchema):
    title: str
    event_type: SocialEventType
    enabled: bool = True
    keyword: Optional[str] = None
    approved_message_id: Optional[UUID] = None
    action_config: dict[str, Any] = Field(default_factory=dict)


class MetaCampaignRead(CamelCaseSchema):
    id: UUID
    channel_type: MetaChannelType
    connection_id: UUID
    approved_message_id: Optional[UUID] = None
    title: str
    description: Optional[str] = None
    status: MetaCampaignStatus
    audience_filter: dict[str, Any] = Field(default_factory=dict)
    policy_summary: dict[str, Any] = Field(default_factory=dict)
    scheduled_at: Optional[datetime] = None
    launched_at: Optional[datetime] = None
    total_recipients: int = 0
    eligible_recipients: int = 0
    blocked_recipients: int = 0
    sent_count: int = 0
    delivered_count: int = 0
    read_count: int = 0
    failed_count: int = 0
    created_at: datetime
    updated_at: datetime

class MetaCampaignCreate(WriteSchema):
    connection_id: UUID
    approved_message_id: Optional[UUID] = None
    title: str
    description: Optional[str] = None
    audience_filter: dict[str, Any] = Field(default_factory=dict)
    policy_summary: dict[str, Any] = Field(default_factory=dict)
    scheduled_at: Optional[datetime] = None


class MetaCampaignUpdate(WriteSchema):
    approved_message_id: Optional[UUID] = None
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[MetaCampaignStatus] = None
    audience_filter: Optional[dict[str, Any]] = None
    policy_summary: Optional[dict[str, Any]] = None
    scheduled_at: Optional[datetime] = None


class MetaConversationNoteRead(CamelCaseSchema):
    id: UUID
    conversation_id: UUID
    matter_id: Optional[UUID] = None
    author_user_id: Optional[UUID] = None
    author_name: Optional[str] = None
    body: str
    mentions: list[str] = Field(default_factory=list)
    note_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

class MetaConversationNoteCreate(WriteSchema):
    body: str
    mentions: list[str] = Field(default_factory=list)


class MetaLeadDrawerRead(CamelCaseSchema):
    conversation: MetaSocialConversationRead
    matter: Optional[MatterRead] = None
    notes: list[MetaConversationNoteRead] = Field(default_factory=list)
    recent_meetings: list[MeetingRead] = Field(default_factory=list)
    can_edit_matter: bool = False
    can_manage_conversation: bool = False


class MetaMoveStageRequest(WriteSchema):
    column_id: KanbanColumnId
    micro_status: Optional[str] = None


class MetaMatterUpdateRequest(WriteSchema):
    responsible_human: Optional[str] = None
    next_action: Optional[str] = None
    executive_summary: Optional[str] = None
    strategy_notes: Optional[str] = None
    priority: Optional[str] = None
    urgency: Optional[str] = None
    classification: Optional[str] = None
    next_deadline: Optional[datetime] = None
