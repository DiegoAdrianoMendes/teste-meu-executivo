"""
Modelo: HumanOverride
Registro auditável de intervenção humana sobre saídas de IA.
"""
import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, UUIDMixin

if TYPE_CHECKING:
    from app.models.agent_run import AgentRun
    from app.models.matter import Matter
    from app.models.user import User


class HumanOverrideDecision(str, enum.Enum):
    accepted = "accepted"
    corrected = "corrected"
    rejected = "rejected"


class HumanOverride(UUIDMixin, Base):
    __tablename__ = "human_overrides"
    __table_args__ = (
        UniqueConstraint("agent_run_id", name="uq_human_overrides_agent_run_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decided_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    decision: Mapped[HumanOverrideDecision] = mapped_column(
        Enum(HumanOverrideDecision, name="human_override_decision_enum"),
        nullable=False,
        index=True,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    before_payload: Mapped[Optional[dict]] = mapped_column(FlexibleJSON(), nullable=True)
    after_payload: Mapped[Optional[dict]] = mapped_column(FlexibleJSON(), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    matter: Mapped["Matter"] = relationship("Matter", back_populates="human_overrides")
    agent_run: Mapped["AgentRun"] = relationship("AgentRun", back_populates="overrides")
    decided_by_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[decided_by_user_id])

    def __repr__(self) -> str:
        return f"<HumanOverride id={self.id} decision={self.decision}>"
