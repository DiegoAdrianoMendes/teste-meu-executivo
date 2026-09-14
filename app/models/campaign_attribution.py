"""
Modelo: CampaignAttribution
Origem e atribuição comercial do card/caso.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter


class CampaignAttribution(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "campaign_attributions"
    __table_args__ = (
        UniqueConstraint("matter_id", name="uq_campaign_attributions_matter"),
    )

    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    medium: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    campaign: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    content: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    term: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    channel: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    first_touch_at: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    last_touch_at: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    matter: Mapped["Matter"] = relationship("Matter", back_populates="campaign_attribution")
