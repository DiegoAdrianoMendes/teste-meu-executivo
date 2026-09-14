"""Núcleo canônico de chamados de suporte do Meu Executivo."""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


def _enum(enum_type: type[enum.StrEnum], name: str, *, length: int = 40) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [value.value for value in values],
        length=length,
    )


class SupportTicketType(enum.StrEnum):
    problem = "problem"
    instability = "instability"
    suggestion = "suggestion"


class SupportTicketPriority(enum.StrEnum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class SupportTicketSeverity(enum.StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class SupportTicketQueue(enum.StrEnum):
    support = "support"
    technical = "technical"


class SupportTicketStatus(enum.StrEnum):
    new = "new"
    triage = "triage"
    in_progress = "in_progress"
    waiting_customer = "waiting_customer"
    waiting_internal = "waiting_internal"
    validating = "validating"
    resolved = "resolved"
    closed = "closed"
    cancelled = "cancelled"


class SupportMessageVisibility(enum.StrEnum):
    public = "public"
    internal = "internal"


class SupportDeliveryChannel(enum.StrEnum):
    email = "email"
    whatsapp = "whatsapp"


class SupportDeliveryStatus(enum.StrEnum):
    pending = "pending"
    sent = "sent"
    failed = "failed"
    disabled = "disabled"


class SupportTicket(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        UniqueConstraint("public_number", name="uq_support_ticket_public_number"),
        UniqueConstraint(
            "workspace_id",
            "reporter_user_id",
            "idempotency_key",
            name="uq_support_ticket_idempotency",
        ),
        UniqueConstraint(
            "legacy_technical_request_id",
            name="uq_support_ticket_legacy_request",
        ),
        Index(
            "ix_support_ticket_workspace_status_created",
            "workspace_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_support_ticket_queue_severity_due",
            "queue",
            "severity",
            "first_response_due_at",
        ),
        Index(
            "ix_support_ticket_error_fingerprint",
            "workspace_id",
            "error_fingerprint",
        ),
        CheckConstraint("version > 0", name="ck_support_ticket_version_positive"),
        CheckConstraint(
            "satisfaction_score IS NULL OR "
            "(satisfaction_score >= 1 AND satisfaction_score <= 5)",
            name="ck_support_ticket_satisfaction_range",
        ),
    )

    public_number: Mapped[str] = mapped_column(String(32), nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    reporter_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    assigned_to_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ticket_type: Mapped[SupportTicketType] = mapped_column(
        _enum(SupportTicketType, "support_ticket_type_enum"),
        nullable=False,
        index=True,
    )
    priority: Mapped[SupportTicketPriority] = mapped_column(
        _enum(SupportTicketPriority, "support_ticket_priority_enum"),
        nullable=False,
        default=SupportTicketPriority.normal,
        server_default=SupportTicketPriority.normal.value,
        index=True,
    )
    severity: Mapped[SupportTicketSeverity] = mapped_column(
        _enum(SupportTicketSeverity, "support_ticket_severity_enum"),
        nullable=False,
        default=SupportTicketSeverity.medium,
        server_default=SupportTicketSeverity.medium.value,
        index=True,
    )
    queue: Mapped[SupportTicketQueue] = mapped_column(
        _enum(SupportTicketQueue, "support_ticket_queue_enum"),
        nullable=False,
        index=True,
    )
    status: Mapped[SupportTicketStatus] = mapped_column(
        _enum(SupportTicketStatus, "support_ticket_status_enum"),
        nullable=False,
        default=SupportTicketStatus.new,
        server_default=SupportTicketStatus.new.value,
        index=True,
    )
    subject: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    route: Mapped[str | None] = mapped_column(String(500), nullable=True)
    technical_context: Mapped[dict] = mapped_column(
        FlexibleJSON(),
        nullable=False,
        default=dict,
    )
    error_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linked_error_log_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("error_logs.id", ondelete="SET NULL"),
        nullable=True,
    )
    linked_incident_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_incidents.id", ondelete="SET NULL"), nullable=True
    )
    linked_release_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_releases.id", ondelete="SET NULL"), nullable=True
    )
    support_access_request_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        nullable=True,
    )
    duplicate_of_ticket_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("support_tickets.id", ondelete="SET NULL"),
        nullable=True,
    )
    legacy_technical_request_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("klm_technical_requests.id", ondelete="RESTRICT"),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    first_response_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    resolution_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    first_responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    reopened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    satisfaction_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    satisfaction_comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class SupportTicketCollaborator(UUIDMixin, Base):
    __tablename__ = "support_ticket_collaborators"
    __table_args__ = (
        UniqueConstraint("ticket_id", "user_id", name="uq_support_ticket_collaborator"),
    )

    ticket_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    invited_by_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class SupportTicketMessage(UUIDMixin, Base):
    __tablename__ = "support_ticket_messages"
    __table_args__ = (
        UniqueConstraint(
            "ticket_id",
            "author_user_id",
            "idempotency_key",
            name="uq_support_ticket_message_idempotency",
        ),
        Index("ix_support_ticket_message_ticket_created", "ticket_id", "created_at"),
    )

    ticket_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    author_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    author_label: Mapped[str] = mapped_column(String(200), nullable=False)
    visibility: Mapped[SupportMessageVisibility] = mapped_column(
        _enum(SupportMessageVisibility, "support_message_visibility_enum"),
        nullable=False,
        default=SupportMessageVisibility.public,
        server_default=SupportMessageVisibility.public.value,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class SupportTicketAttachment(UUIDMixin, Base):
    __tablename__ = "support_ticket_attachments"
    __table_args__ = (
        UniqueConstraint(
            "ticket_id",
            "storage_object_version_id",
            name="uq_support_ticket_attachment_object",
        ),
    )

    ticket_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("support_ticket_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    storage_object_version_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("storage_objects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    uploaded_by_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(127), nullable=False)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class SupportTicketEvent(UUIDMixin, Base):
    """Evento imutável de auditoria do ciclo de vida do chamado."""

    __tablename__ = "support_ticket_events"
    __table_args__ = (
        Index("ix_support_ticket_event_ticket_created", "ticket_id", "created_at"),
    )

    ticket_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    before_data: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    after_data: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class SupportSavedFilter(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "support_saved_filters"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "name",
            name="uq_support_saved_filter_owner_name",
        ),
    )

    owner_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    filters: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    is_default: Mapped[bool] = mapped_column(nullable=False, default=False)


class SupportNotificationOutbox(UUIDMixin, TimestampMixin, Base):
    """Entrega transacional minimizada; nunca contém a descrição do chamado."""

    __tablename__ = "support_notification_outbox"
    __table_args__ = (
        UniqueConstraint(
            "ticket_id",
            "recipient_user_id",
            "channel",
            "event_key",
            name="uq_support_notification_delivery",
        ),
    )

    ticket_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    recipient_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    channel: Mapped[SupportDeliveryChannel] = mapped_column(
        _enum(SupportDeliveryChannel, "support_delivery_channel_enum"),
        nullable=False,
    )
    status: Mapped[SupportDeliveryStatus] = mapped_column(
        _enum(SupportDeliveryStatus, "support_delivery_status_enum"),
        nullable=False,
        default=SupportDeliveryStatus.pending,
        server_default=SupportDeliveryStatus.pending.value,
    )
    event_key: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class SupportTranscriptEmail(UUIDMixin, TimestampMixin, Base):
    """F15 — cópia integral da conversa por e-mail ao encerrar o chamado.

    Ao contrário do outbox operacional acima (que nunca guarda a descrição
    do chamado), este registro precisa preservar o conteúdo completo da
    transcrição: fail-closed com o gate de envio externo fechado significa
    ficar ``pending`` com o conteúdo íntegro persistido, nunca descartar a
    mensagem. Idempotente por ``ticket_id`` + ``closure_event_key``
    (encerramento específico, atrelado à versão do chamado naquele
    fechamento) — reencerrar o mesmo fechamento nunca duplica o envio.
    """

    __tablename__ = "support_transcript_emails"
    __table_args__ = (
        UniqueConstraint(
            "ticket_id",
            "closure_event_key",
            name="uq_support_transcript_email_closure",
        ),
        Index(
            "ix_support_transcript_email_workspace_status",
            "workspace_id",
            "status",
        ),
        CheckConstraint(
            "final_status IN ('closed', 'cancelled')",
            name="ck_support_transcript_email_final_status",
        ),
    )

    ticket_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    recipient_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    closure_event_key: Mapped[str] = mapped_column(String(120), nullable=False)
    final_status: Mapped[str] = mapped_column(String(40), nullable=False)
    resolved: Mapped[bool] = mapped_column(nullable=False, default=True)
    unsatisfied: Mapped[bool] = mapped_column(nullable=False, default=False)
    subject: Mapped[str] = mapped_column(String(240), nullable=False)
    transcript_snapshot: Mapped[dict] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    status: Mapped[SupportDeliveryStatus] = mapped_column(
        _enum(SupportDeliveryStatus, "support_transcript_email_status_enum"),
        nullable=False,
        default=SupportDeliveryStatus.pending,
        server_default=SupportDeliveryStatus.pending.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


register_append_only_model(SupportTicketMessage)
register_append_only_model(SupportTicketEvent)


__all__ = [
    "SupportDeliveryChannel",
    "SupportDeliveryStatus",
    "SupportMessageVisibility",
    "SupportNotificationOutbox",
    "SupportSavedFilter",
    "SupportTicket",
    "SupportTicketAttachment",
    "SupportTicketCollaborator",
    "SupportTicketEvent",
    "SupportTicketMessage",
    "SupportTicketPriority",
    "SupportTicketQueue",
    "SupportTicketSeverity",
    "SupportTicketStatus",
    "SupportTicketType",
    "SupportTranscriptEmail",
]
