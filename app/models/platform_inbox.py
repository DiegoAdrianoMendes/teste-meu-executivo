"""Inbox omnichannel própria da operação interna do Meu Executivo."""
from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


class PlatformInboxChannel(str, enum.Enum):
    whatsapp = "whatsapp"
    instagram = "instagram"
    messenger = "messenger"
    email_sales = "email_sales"
    email_support = "email_support"
    webchat = "webchat"


class PlatformInboxQueue(str, enum.Enum):
    sales = "sales"
    support = "support"


class PlatformInboxStatus(str, enum.Enum):
    open = "open"
    pending = "pending"
    resolved = "resolved"


class PlatformInboxDirection(str, enum.Enum):
    inbound = "inbound"
    outbound = "outbound"
    internal = "internal"


class PlatformInboxDeliveryStatus(str, enum.Enum):
    received = "received"
    pending = "pending"
    sent = "sent"
    delivered = "delivered"
    read = "read"
    failed = "failed"
    not_configured = "not_configured"


def _enum(enum_type: type[enum.Enum], name: str, length: int = 40) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
        length=length,
    )


class PlatformInboxConversation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_inbox_conversations"
    __table_args__ = (
        UniqueConstraint("channel", "external_thread_id", name="uq_platform_inbox_channel_thread"),
        Index("ix_platform_inbox_queue_status_last", "queue", "status", "last_message_at"),
        Index("ix_platform_inbox_assignee_status", "assigned_to_user_id", "status"),
    )

    channel: Mapped[PlatformInboxChannel] = mapped_column(
        _enum(PlatformInboxChannel, "platform_inbox_channel_enum"), nullable=False, index=True
    )
    external_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    public_access_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    queue: Mapped[PlatformInboxQueue] = mapped_column(
        _enum(PlatformInboxQueue, "platform_inbox_queue_enum"), nullable=False
    )
    status: Mapped[PlatformInboxStatus] = mapped_column(
        _enum(PlatformInboxStatus, "platform_inbox_status_enum"),
        nullable=False,
        default=PlatformInboxStatus.open,
        server_default=PlatformInboxStatus.open.value,
    )
    assigned_to_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    prospect_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("growth_prospects.id", ondelete="SET NULL"), nullable=True
    )
    support_ticket_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("support_tickets.id", ondelete="SET NULL"), nullable=True
    )
    workspace_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    subject: Mapped[str | None] = mapped_column(String(240), nullable=True)
    last_message_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    first_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    metadata_json: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    messages: Mapped[list["PlatformInboxMessage"]] = relationship(
        "PlatformInboxMessage", back_populates="conversation", cascade="all, delete-orphan",
        order_by="PlatformInboxMessage.occurred_at.asc()",
    )


class PlatformInboxMessage(UUIDMixin, Base):
    __tablename__ = "platform_inbox_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "idempotency_key", name="uq_platform_inbox_message_idempotency"),
        UniqueConstraint("external_message_id", name="uq_platform_inbox_external_message"),
        Index("ix_platform_inbox_message_conversation_time", "conversation_id", "occurred_at"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_inbox_conversations.id", ondelete="CASCADE"), nullable=False
    )
    direction: Mapped[PlatformInboxDirection] = mapped_column(
        _enum(PlatformInboxDirection, "platform_inbox_direction_enum"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_status: Mapped[PlatformInboxDeliveryStatus] = mapped_column(
        _enum(PlatformInboxDeliveryStatus, "platform_inbox_delivery_status_enum"), nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_label: Mapped[str] = mapped_column(String(200), nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    metadata_json: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    conversation: Mapped[PlatformInboxConversation] = relationship(
        "PlatformInboxConversation", back_populates="messages"
    )
