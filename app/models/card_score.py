"""
Modelo: CardScore
Score operacional/jurídico/comercial agregado do card.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Float, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter


class CardScore(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "card_scores"
    __table_args__ = (
        UniqueConstraint("matter_id", name="uq_card_scores_matter"),
    )

    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    viability_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    urgency_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    evidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    conversion_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    relationship_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    overall_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    matter: Mapped["Matter"] = relationship("Matter", back_populates="card_score")
