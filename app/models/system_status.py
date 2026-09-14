from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, UUIDMixin


class SystemStatusObservation(UUIDMixin, Base):
    """Amostra global e não sensível da saúde técnica da plataforma."""

    __tablename__ = "system_status_observations"

    overall_status: Mapped[str] = mapped_column(String(32), nullable=False)
    components: Mapped[dict] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict, server_default="{}"
    )
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_system_status_observed_at", "observed_at"),
        Index("ix_system_status_overall_observed", "overall_status", "observed_at"),
    )
