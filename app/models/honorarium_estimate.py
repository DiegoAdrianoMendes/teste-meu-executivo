"""
Modelo: HonorariumEstimate
Estimativa comercial de honorários e despesas do caso.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter


class HonorariumEstimate(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "honorarium_estimates"
    __table_args__ = (
        UniqueConstraint("matter_id", name="uq_honorarium_estimates_matter"),
    )

    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contract_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    fixed_fee: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quota_litis_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    expected_expenses: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_estimated_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    matter: Mapped["Matter"] = relationship("Matter", back_populates="honorarium_estimate")
