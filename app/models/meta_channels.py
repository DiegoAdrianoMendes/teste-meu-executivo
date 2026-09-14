"""
Modelos compartilhados para canais oficiais da Meta além do WhatsApp.

Escopo:
- conexões por workspace para Instagram e Messenger
- conversas e mensagens sociais
- mensagens aprovadas para automação
- regras simples por canal
- campanhas channel-aware
- notas internas do inbox social
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, UUIDMixin
from app.models.whatsapp import HumanControlState, WaMessageDirection, WaMessageStatus, WaQueueStatus

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.user import User
    from app.models.workspace import Workspace


class MetaChannelType(str, enum.Enum):
    instagram = "INSTAGRAM"
    messenger = "MESSENGER"


class MetaConnectionStatus(str, enum.Enum):
    disconnected = "disconnected"
    pending = "pending"
    connected = "connected"
    error = "error"


class SocialEventType(str, enum.Enum):
    direct_message = "direct_message"
    story_reply = "story_reply"
    story_mention = "story_mention"
    post_comment = "post_comment"
    keyword_comment = "keyword_comment"
    page_message = "page_message"
    page_comment = "page_comment"
    entry_point = "entry_point"


class FollowRelationshipStatus(str, enum.Enum):
    following = "following"
    not_following = "not_following"
    unknown = "unknown"


class ApprovedMessageStatus(str, enum.Enum):
    draft = "draft"
    in_review = "in_review"
    approved = "approved"
    archived = "archived"


class MetaCampaignStatus(str, enum.Enum):
    draft = "draft"
    scheduled = "scheduled"
    running = "running"
    paused = "paused"
    completed = "completed"
    stopped = "stopped"
    failed = "failed"


def _meta_channel_enum(name: str) -> SAEnum:
    return SAEnum(
        MetaChannelType,
        name=name,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


class MetaChannelConnection(UUIDMixin, Base):
    __tablename__ = "meta_channel_connections"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "channel_type",
            "external_account_id",
            name="uq_meta_channel_connections_workspace_channel_external",
        ),
        Index("ix_meta_channel_connections_workspace_channel", "workspace_id", "channel_type"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_type: Mapped[MetaChannelType] = mapped_column(
        _meta_channel_enum("meta_channel_type_enum"),
        nullable=False,
        index=True,
    )
    page_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    instagram_account_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    external_account_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    account_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    access_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scopes: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    capability_matrix: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    status: Mapped[MetaConnectionStatus] = mapped_column(
        SAEnum(MetaConnectionStatus, name="meta_connection_status_enum"),
        nullable=False,
        default=MetaConnectionStatus.pending,
    )
    webhook_registered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")


class MetaSocialConversation(UUIDMixin, Base):
    __tablename__ = "meta_social_conversations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "channel_type",
            "external_thread_id",
            name="uq_meta_social_conversations_workspace_channel_thread",
        ),
        Index("ix_meta_social_conversations_workspace_channel", "workspace_id", "channel_type"),
        Index("ix_meta_social_conversations_workspace_last_message", "workspace_id", "last_message_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_type: Mapped[MetaChannelType] = mapped_column(
        _meta_channel_enum("meta_social_channel_type_enum"),
        nullable=False,
        index=True,
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("meta_channel_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("matters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    external_thread_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    external_participant_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    participant_name: Mapped[str] = mapped_column(String(255), nullable=False)
    participant_handle: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    participant_avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    event_type: Mapped[SocialEventType] = mapped_column(
        SAEnum(SocialEventType, name="meta_social_event_type_enum"),
        nullable=False,
        default=SocialEventType.direct_message,
    )
    queue_status: Mapped[WaQueueStatus] = mapped_column(
        SAEnum(WaQueueStatus, name="meta_queue_status_enum"),
        nullable=False,
        default=WaQueueStatus.ai,
    )
    human_control_state: Mapped[HumanControlState] = mapped_column(
        SAEnum(HumanControlState, name="meta_human_control_state_enum"),
        nullable=False,
        default=HumanControlState.copilot,
    )
    follow_status: Mapped[FollowRelationshipStatus] = mapped_column(
        SAEnum(FollowRelationshipStatus, name="meta_follow_status_enum"),
        nullable=False,
        default=FollowRelationshipStatus.unknown,
    )
    source_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    source_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    active_flow_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    next_follow_up_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_message_preview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    policy_state: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    is_public_thread: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    connection: Mapped["MetaChannelConnection"] = relationship("MetaChannelConnection")
    matter: Mapped[Optional["Matter"]] = relationship("Matter")
    assigned_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[assigned_user_id])
    messages: Mapped[list["MetaSocialMessage"]] = relationship(
        "MetaSocialMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="MetaSocialMessage.timestamp",
    )


class MetaSocialMessage(UUIDMixin, Base):
    __tablename__ = "meta_social_messages"
    __table_args__ = (
        UniqueConstraint("external_message_id", name="uq_meta_social_messages_external_message"),
        Index("ix_meta_social_messages_conversation_timestamp", "conversation_id", "timestamp"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("meta_social_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_message_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    direction: Mapped[WaMessageDirection] = mapped_column(
        SAEnum(WaMessageDirection, name="meta_message_direction_enum"),
        nullable=False,
    )
    status: Mapped[WaMessageStatus] = mapped_column(
        SAEnum(WaMessageStatus, name="meta_message_status_enum"),
        nullable=False,
        default=WaMessageStatus.pending,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sent_by_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_public_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    message_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped["MetaSocialConversation"] = relationship("MetaSocialConversation", back_populates="messages")
    sent_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[sent_by_id])


class MetaApprovedMessage(UUIDMixin, Base):
    __tablename__ = "meta_approved_messages"
    __table_args__ = (
        Index("ix_meta_approved_messages_workspace_channel", "workspace_id", "channel_type"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_type: Mapped[MetaChannelType] = mapped_column(
        _meta_channel_enum("meta_approved_message_channel_enum"),
        nullable=False,
        index=True,
    )
    message_kind: Mapped[str] = mapped_column(String(80), nullable=False, default="direct_message")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    cta_label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    cta_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status: Mapped[ApprovedMessageStatus] = mapped_column(
        SAEnum(ApprovedMessageStatus, name="meta_approved_message_status_enum"),
        nullable=False,
        default=ApprovedMessageStatus.draft,
    )
    requires_following: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    restrictions: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    created_by_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    approved_by_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    created_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[created_by_id])
    approved_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[approved_by_id])


class MetaAutomationRule(UUIDMixin, Base):
    __tablename__ = "meta_automation_rules"
    __table_args__ = (
        Index("ix_meta_automation_rules_workspace_channel", "workspace_id", "channel_type"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_type: Mapped[MetaChannelType] = mapped_column(
        _meta_channel_enum("meta_automation_channel_enum"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[SocialEventType] = mapped_column(
        SAEnum(SocialEventType, name="meta_social_event_type_enum"),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    keyword: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    approved_message_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("meta_approved_messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action_config: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    approved_message: Mapped[Optional["MetaApprovedMessage"]] = relationship("MetaApprovedMessage")


class MetaChannelCampaign(UUIDMixin, Base):
    __tablename__ = "meta_channel_campaigns"
    __table_args__ = (
        Index("ix_meta_channel_campaigns_workspace_channel", "workspace_id", "channel_type"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_type: Mapped[MetaChannelType] = mapped_column(
        _meta_channel_enum("meta_campaign_channel_enum"),
        nullable=False,
        index=True,
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("meta_channel_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    approved_message_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("meta_approved_messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[MetaCampaignStatus] = mapped_column(
        SAEnum(MetaCampaignStatus, name="meta_campaign_status_enum"),
        nullable=False,
        default=MetaCampaignStatus.draft,
    )
    audience_filter: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    policy_summary: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    launched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    total_recipients: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    eligible_recipients: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_recipients: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    read_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    connection: Mapped["MetaChannelConnection"] = relationship("MetaChannelConnection")
    created_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[created_by_id])
    approved_message: Mapped[Optional["MetaApprovedMessage"]] = relationship("MetaApprovedMessage")


class MetaConversationNote(UUIDMixin, Base):
    __tablename__ = "meta_conversation_notes"
    __table_args__ = (
        Index("ix_meta_conversation_notes_conversation_created", "conversation_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_type: Mapped[MetaChannelType] = mapped_column(
        _meta_channel_enum("meta_note_channel_enum"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("meta_social_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("matters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    author_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    author_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    mentions: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    note_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    conversation: Mapped["MetaSocialConversation"] = relationship("MetaSocialConversation")
    matter: Mapped[Optional["Matter"]] = relationship("Matter")
    author: Mapped[Optional["User"]] = relationship("User", foreign_keys=[author_user_id])
