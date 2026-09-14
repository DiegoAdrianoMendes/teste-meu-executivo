"""
Modelo: ConversationInsight
Snapshot estruturado da leitura da conversa do card/WhatsApp.
Usado por agentes, relatórios e futuras integrações de conversão.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Boolean, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.workspace import Workspace


class ConversationInsight(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "conversation_insights"
    __table_args__ = (
        UniqueConstraint("matter_id", name="uq_conversation_insights_matter_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_channel: Mapped[str] = mapped_column(String(50), nullable=False, default="whatsapp")
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    funnel_signal: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    close_intent: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    document_readiness: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    urgency_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    sentiment: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    trust_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    no_show_risk: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    objections: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    requested_documents: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    missing_information: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    recommended_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    next_best_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    estimated_conversion_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    closed_signal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    conversion_platforms: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    raw_payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    last_message_at: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    matter: Mapped["Matter"] = relationship("Matter", back_populates="conversation_insight")
