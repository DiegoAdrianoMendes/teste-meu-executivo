"""
Modelo: RevenueProjection
Projeções financeiras e comerciais do card.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter


class RevenueProjection(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "revenue_projections"
    __table_args__ = (
        UniqueConstraint("matter_id", name="uq_revenue_projections_matter"),
    )

    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    estimated_claim_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    projected_revenue: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    success_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence_level: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    payback_days: Mapped[Optional[int]] = mapped_column(nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="BRL")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    projection_premises: Mapped[dict] = mapped_column(
        FlexibleJSON(),
        nullable=False,
        default=dict,
    )
    projection_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    matter: Mapped["Matter"] = relationship("Matter", back_populates="revenue_projection")
