"""
Modelo: Meeting
Agenda interna de reuniões e checkpoints operacionais do workspace.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.user import User
    from app.models.workspace import Workspace


class MeetingStatus(str, enum.Enum):
    scheduled = "scheduled"
    completed = "completed"
    cancelled = "cancelled"
    no_show = "no_show"


class MeetingNature(str, enum.Enum):
    """Natureza do compromisso — comercial (funil/cliente) ou jurídico
    (audiência, sustentação, prazo interno, revisão). Alimenta o read-model
    unificado `GET /agenda` (comercial + jurídico numa única lista, com
    filtro `?natureza=`). Default é `comercial`, espelhando a origem
    histórica desta entidade (booking público/interno, sempre comercial)."""

    comercial = "comercial"
    juridico = "juridico"


class MeetingEventType(str, enum.Enum):
    """Tipo do evento dentro da agenda — `prazo_fatal` é o único
    INVIOLÁVEL: quando a origem é `ProcessPublication` (módulo de
    controladoria), ele já nasce com contagem e memória de cálculo
    próprias (`app/services/prazo_calculator.py`), nunca recalculadas
    aqui. Um `Meeting` também pode nascer com `event_type=prazo_fatal`
    para representar um prazo fatal agendado manualmente na agenda."""

    reuniao = "reuniao"
    audiencia = "audiencia"
    sustentacao = "sustentacao"
    prazo_interno = "prazo_interno"
    prazo_fatal = "prazo_fatal"
    revisao = "revisao"
    personalizado = "personalizado"


class MeetingParticipantRole(str, enum.Enum):
    organizer = "organizer"
    attendee = "attendee"
    observer = "observer"


class MeetingParticipantRsvp(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    declined = "declined"
    tentative = "tentative"


class MeetingNotificationStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    delivered = "delivered"
    failed = "failed"
    disabled = "disabled"


class Meeting(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "meetings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_meetings_workspace_id"),
        UniqueConstraint("matter_id", "id", name="uq_meetings_matter_id"),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_meetings_workspace_matter",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["matter_id", "linked_task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_meetings_matter_task",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "linked_task_id IS NULL OR matter_id IS NOT NULL",
            name="ck_meetings_linked_task_requires_matter",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    matter_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        nullable=True,
        index=True,
    )
    responsible_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    linked_task_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        nullable=True,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[MeetingStatus] = mapped_column(
        Enum(MeetingStatus, name="meeting_status_enum"),
        nullable=False,
        default=MeetingStatus.scheduled,
        index=True,
    )
    # ── Agenda unificada (comercial + jurídico, GET /agenda) ──
    # CHECK constraint (native_enum=False), mesmo padrão de
    # app/models/process_monitoring.py — evita ALTER TYPE em Postgres e
    # funciona em SQLite via `op.batch_alter_table` na migração.
    natureza: Mapped[MeetingNature] = mapped_column(
        Enum(
            MeetingNature,
            name="meeting_natureza_enum",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda values: [item.value for item in values],
        ),
        nullable=False,
        default=MeetingNature.comercial,
        server_default=MeetingNature.comercial.value,
        index=True,
    )
    event_type: Mapped[MeetingEventType] = mapped_column(
        Enum(
            MeetingEventType,
            name="meeting_event_type_enum",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda values: [item.value for item in values],
        ),
        nullable=False,
        default=MeetingEventType.reuniao,
        server_default=MeetingEventType.reuniao.value,
    )
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meeting_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipient_overrides: Mapped[list | None] = mapped_column(
        FlexibleJSON(), nullable=True, default=list
    )
    google_calendar_event_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    workspace: Mapped[Workspace] = relationship(
        "Workspace",
        back_populates="meetings",
        foreign_keys=[workspace_id],
    )
    matter: Mapped[Matter | None] = relationship(
        "Matter",
        back_populates="meetings",
        primaryjoin=(
            "and_(Matter.id == foreign(Meeting.matter_id), "
            "Matter.workspace_id == Meeting.workspace_id)"
        ),
        foreign_keys=[matter_id],
    )
    responsible_user: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[responsible_user_id],
        back_populates="responsible_meetings",
    )
    participants: Mapped[list[MeetingParticipant]] = relationship(
        "MeetingParticipant",
        back_populates="meeting",
        cascade="all, delete-orphan",
        order_by="MeetingParticipant.created_at.asc()",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Meeting id={self.id} title={self.title} starts_at={self.starts_at}>"


class MeetingParticipant(UUIDMixin, TimestampMixin, Base):
    """Internal workspace participant; external recipients remain separate."""

    __tablename__ = "meeting_participants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "meeting_id"],
            ["meetings.workspace_id", "meetings.id"],
            name="fk_meeting_participants_workspace_meeting",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["membership_id", "workspace_id", "user_id"],
            [
                "workspace_memberships.id",
                "workspace_memberships.workspace_id",
                "workspace_memberships.user_id",
            ],
            name="fk_meeting_participants_membership_binding",
            ondelete="RESTRICT",
        ),
        Index(
            "ux_meeting_participant_active_user",
            "meeting_id",
            "user_id",
            unique=True,
            postgresql_where=text("active = true"),
            sqlite_where=text("active = 1"),
        ),
        Index(
            "ux_meeting_participant_active_organizer",
            "meeting_id",
            unique=True,
            postgresql_where=text("active = true AND role = 'organizer'"),
            sqlite_where=text("active = 1 AND role = 'organizer'"),
        ),
        Index("ix_meeting_participants_workspace_meeting", "workspace_id", "meeting_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    membership_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    meeting_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    role: Mapped[MeetingParticipantRole] = mapped_column(
        Enum(
            MeetingParticipantRole,
            name="meeting_participant_role_enum",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda values: [item.value for item in values],
        ),
        nullable=False,
    )
    rsvp: Mapped[MeetingParticipantRsvp] = mapped_column(
        Enum(
            MeetingParticipantRsvp,
            name="meeting_participant_rsvp_enum",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda values: [item.value for item in values],
        ),
        nullable=False,
        default=MeetingParticipantRsvp.pending,
        server_default=MeetingParticipantRsvp.pending.value,
    )
    reminder_minutes_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        index=True,
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    meeting: Mapped[Meeting] = relationship("Meeting", back_populates="participants")


class MeetingNotificationOutbox(UUIDMixin, TimestampMixin, Base):
    """Durable intent for an operational meeting notification.

    The row is inserted in the same transaction as the meeting and contains no
    recipient data or message body. The worker resolves current, authorized
    recipients only after the domain commit succeeds.
    """

    __tablename__ = "meeting_notification_outbox"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "meeting_id"],
            ["meetings.workspace_id", "meetings.id"],
            name="fk_meeting_notification_outbox_workspace_meeting",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "dedupe_key",
            name="uq_meeting_notification_outbox_dedupe",
        ),
        Index(
            "ix_meeting_notification_outbox_due",
            "status",
            "available_at",
            "created_at",
        ),
        Index(
            "ix_meeting_notification_outbox_workspace_due",
            "workspace_id",
            "status",
            "available_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    meeting_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        nullable=True,
        index=True,
    )
    meeting_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_key: Mapped[str] = mapped_column(String(80), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        index=True,
    )
    status: Mapped[MeetingNotificationStatus] = mapped_column(
        Enum(
            MeetingNotificationStatus,
            name="meeting_notification_status_enum",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda values: [item.value for item in values],
        ),
        nullable=False,
        default=MeetingNotificationStatus.pending,
        server_default=MeetingNotificationStatus.pending.value,
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    claim_token: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class MeetingCreationReceipt(UUIDMixin, TimestampMixin, Base):
    """Immutable tenant receipt for ``POST /meetings``.

    Only a digest of the request is retained.  The meeting and its receipt are
    inserted in the same database transaction, so a client may safely reuse
    one key after a timeout without creating a second appointment or replaying
    its audit/outbox side effects.
    """

    __tablename__ = "meeting_creation_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "meeting_id"],
            ["meetings.workspace_id", "meetings.id"],
            name="fk_meeting_creation_receipts_workspace_meeting",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "actor_user_id",
            "idempotency_key",
            name="uq_meeting_creation_receipts_actor_key",
        ),
        CheckConstraint(
            "length(payload_hash) = 64",
            name="ck_meeting_creation_receipts_payload_hash_length",
        ),
        Index(
            "ix_meeting_creation_receipts_workspace_created",
            "workspace_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    meeting_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)


register_append_only_model(MeetingCreationReceipt)
