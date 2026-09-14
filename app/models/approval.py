"""
Modelo: ApprovalQueueItem
Fila de aprovação humana — separada do Kanban, mas referenciando o card.
Suporta o fluxo Human-in-the-loop.
"""
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.user import User


class ApprovalStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    changes_requested = "changes_requested"


class ApprovalDecision(StrEnum):
    approved = "approved"
    rejected = "rejected"
    changes_requested = "changes_requested"


class ApprovalPriority(StrEnum):
    alta = "alta"
    media = "media"
    baixa = "baixa"


class ApprovalQueueItem(UUIDMixin, TimestampMixin, Base):
    """
    Entidade ApprovalQueueItem — item na fila de revisão humana.
    Ao ser resolvido, altera o estado do MatterCard correspondente.
    """
    __tablename__ = "approval_queue_items"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "matter_id",
            name="uq_approval_queue_item_id_matter",
        ),
        Index(
            "uq_approval_pending_intent",
            "matter_id",
            "approval_type",
            "intent_key",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )

    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    approval_type: Mapped[str] = mapped_column(String(100), nullable=False)
    intent_key: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[ApprovalPriority] = mapped_column(
        Enum(ApprovalPriority, name="approval_priority_enum"),
        nullable=False,
        default=ApprovalPriority.media,
        index=True,
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, name="approval_status_enum"),
        nullable=False,
        default=ApprovalStatus.pending,
        index=True,
    )
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Versão do documento/minuta que foi submetida para aprovação
    document_version_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relacionamentos ──
    matter: Mapped["Matter"] = relationship("Matter", back_populates="approval_items")
    requester: Mapped["User | None"] = relationship("User", foreign_keys=[requested_by])
    resolver: Mapped["User | None"] = relationship("User", foreign_keys=[resolved_by])

    def __repr__(self) -> str:
        return f"<ApprovalQueueItem id={self.id} status={self.status} matter={self.matter_id}>"
