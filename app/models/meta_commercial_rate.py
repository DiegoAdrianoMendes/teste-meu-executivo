"""Root-managed commercial rates for official Meta campaign delivery."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

META_PRICING_SOURCE_URL = "https://developers.facebook.com/docs/whatsapp/pricing"


class MetaCommercialRateCard(UUIDMixin, TimestampMixin, Base):
    """One versioned customer-facing rate for an official Meta message."""

    __tablename__ = "meta_commercial_rate_cards"
    __table_args__ = (
        UniqueConstraint(
            "rate_card_key",
            name="uq_meta_commercial_rate_cards_key",
        ),
        CheckConstraint(
            "commercial_rate >= 0",
            name="ck_meta_commercial_rate_nonnegative",
        ),
        CheckConstraint(
            "country_code = '*' OR "
            "(length(country_code) = 2 AND country_code = upper(country_code))",
            name="ck_meta_commercial_rate_country_code",
        ),
        CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_meta_commercial_rate_currency",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_meta_commercial_rate_effective_window",
        ),
        CheckConstraint(
            f"source_url = '{META_PRICING_SOURCE_URL}'",
            name="ck_meta_commercial_rate_source_url",
        ),
        Index(
            "ix_meta_commercial_rate_lookup",
            "category",
            "country_code",
            "is_active",
            "effective_from",
        ),
        Index(
            "ix_meta_commercial_rate_history",
            "category",
            "country_code",
            "currency",
            "effective_from",
        ),
    )

    rate_card_key: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    commercial_rate: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    effective_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )


__all__ = ["META_PRICING_SOURCE_URL", "MetaCommercialRateCard"]
