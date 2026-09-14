"""
Modelo: AgentRun
Trilha persistente e auditável de execuções de agentes de IA.
"""
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, FlexibleUUID, UUIDMixin

if TYPE_CHECKING:
    from app.models.human_override import HumanOverride
    from app.models.matter import Matter
    from app.models.user import User


class AgentRunStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    waiting_human = "waiting_human"
    cancelled = "cancelled"
    waiting_data = "waiting_data"
    waiting_tool = "waiting_tool"
    waiting_external = "waiting_external"
    paused_budget = "paused_budget"


class AgentRun(UUIDMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "dedupe_key",
            name="uq_agent_runs_workspace_dedupe_key",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # V2 context is additive; matter_id remains required for legacy runs.
    context_type: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    context_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    deployment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    triggered_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    agent_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    trigger_source: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    thread_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(AgentRunStatus, name="agent_run_status_enum"),
        nullable=False,
        default=AgentRunStatus.queued,
        index=True,
    )
    input_payload: Mapped[dict | None] = mapped_column(FlexibleJSON(), nullable=True)
    output_payload: Mapped[dict | None] = mapped_column(FlexibleJSON(), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)

    # ── Rastreamento de tokens e custo (para billing e observabilidade) ──
    model_used: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ai_provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    input_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0"))

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    matter: Mapped["Matter | None"] = relationship("Matter", back_populates="agent_runs")
    triggered_by_user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[triggered_by_user_id]
    )
    overrides: Mapped[list["HumanOverride"]] = relationship(
        "HumanOverride",
        back_populates="agent_run",
        cascade="all, delete-orphan",
        order_by="HumanOverride.created_at.desc()",
    )

    def __repr__(self) -> str:
        return f"<AgentRun id={self.id} agent={self.agent_name} status={self.status}>"
