"""
Modelo: TimelineEvent
Registro append-only de todos os eventos de um caso.
Serve como audit trail e histórico de andamentos.
NUNCA deve ser atualizado ou deletado — apenas inserido.
"""
import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from app.db.base import FlexibleJSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.user import User


class TimelineEventType(str, enum.Enum):
    status_change = "status_change"
    document_added = "document_added"
    comment = "comment"
    task_created = "task_created"
    task_completed = "task_completed"
    approval_requested = "approval_requested"
    approval_resolved = "approval_resolved"
    agent_action = "agent_action"
    deadline_alert = "deadline_alert"
    card_moved = "card_moved"
    webhook_received = "webhook_received"


class TimelineEvent(UUIDMixin, Base):
    """
    Entidade TimelineEvent — registro imutável de eventos do caso.
    Não herda TimestampMixin para garantir que created_at/updated_at
    sejam controlados manualmente (append-only).
    """
    __tablename__ = "timeline_events"

    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    event_type: Mapped[TimelineEventType] = mapped_column(
        Enum(TimelineEventType, name="timeline_event_type_enum"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Timestamp imutável — definido no momento da inserção
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    # Metadados extras em JSONB (ex: before/after de um status_change)
    # Nota: 'metadata' é reservado pelo SQLAlchemy — usamos 'event_data'
    event_data: Mapped[Optional[dict]] = mapped_column("metadata", FlexibleJSON(), nullable=True)

    # ── Relacionamentos ──
    matter: Mapped["Matter"] = relationship("Matter", back_populates="timeline_events")
    agent: Mapped[Optional["User"]] = relationship("User", back_populates="timeline_events")

    def __repr__(self) -> str:
        return f"<TimelineEvent id={self.id} type={self.event_type} matter={self.matter_id}>"
