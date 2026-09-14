"""Durable, tenant-scoped metadata emitted by the canonical AI gateway."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, UUIDMixin


class AiGatewayEvent(UUIDMixin, Base):
    """Queryable fallback telemetry without prompts, responses, or credentials."""

    __tablename__ = "ai_gateway_events"
    __table_args__ = (
        CheckConstraint(
            "latency_ms >= 0 AND input_tokens >= 0 AND output_tokens >= 0 "
            "AND cached_input_tokens >= 0",
            name="ck_ai_gateway_events_nonnegative",
        ),
        Index("ix_ai_gateway_events_workspace_created", "workspace_id", "created_at"),
        Index("ix_ai_gateway_events_request", "workspace_id", "request_id"),
        Index("ix_ai_gateway_events_outcome", "workspace_id", "outcome"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    request_id: Mapped[str] = mapped_column(String(180), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    task_class: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    outcome: Mapped[str] = mapped_column(String(180), nullable=False)
    route_key: Mapped[str] = mapped_column(String(260), nullable=False, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 8), nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 8),
        nullable=True,
    )
    request_started: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    billing_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    operation: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        FlexibleJSON(),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    workspace: Mapped[Workspace] = relationship("Workspace")


from app.models.workspace import Workspace  # noqa: E402
