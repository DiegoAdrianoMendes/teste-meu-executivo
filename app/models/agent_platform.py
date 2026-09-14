"""Durable contracts for agent deployments, flow nodes and human decisions."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, FlexibleUUID


class DeploymentStatus(enum.StrEnum):
    draft = "draft"
    active = "active"
    paused = "paused"
    retired = "retired"


class HumanDecisionStatus(enum.StrEnum):
    pending = "pending"
    decided = "decided"
    expired = "expired"
    cancelled = "cancelled"


class HumanDeliveryStatus(enum.StrEnum):
    pending = "pending"
    delivered = "delivered"
    failed = "failed"
    not_configured = "not_configured"


class AgentDeployment(Base):
    __tablename__ = "agent_deployments"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "agent_key", "version", name="uq_agent_deployments_key_version"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(FlexibleUUID(), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    role_package: Mapped[str] = mapped_column(
        String(80), nullable=False, default="legal_operations"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[DeploymentStatus] = mapped_column(
        String(20), nullable=False, default=DeploymentStatus.draft, index=True
    )
    config: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    gates: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    # Typed V2 bindings; config remains the backwards-compatible source during migration.
    tool_bindings: Mapped[list[str]] = mapped_column(
        FlexibleJSON(), nullable=False, default=list
    )
    knowledge_bindings: Mapped[list[str]] = mapped_column(
        FlexibleJSON(), nullable=False, default=list
    )
    autonomy_policy: Mapped[dict[str, Any]] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    human_routing_policy: Mapped[dict[str, Any]] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    budget_policy: Mapped[dict[str, Any]] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class FlowNodeDefinition(Base):
    __tablename__ = "flow_node_definitions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "node_type", "version", name="uq_flow_node_definitions_type_version"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(FlexibleUUID(), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    schema: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HumanDecision(Base):
    __tablename__ = "human_decisions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "decision_key", name="uq_human_decisions_key"),
    )
    id: Mapped[uuid.UUID] = mapped_column(FlexibleUUID(), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    flow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("flow_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decision_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[HumanDecisionStatus] = mapped_column(
        String(20), nullable=False, default=HumanDecisionStatus.pending, index=True
    )
    options: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    decision: Mapped[dict[str, Any] | None] = mapped_column(FlexibleJSON(), nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    required_roles: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    required_skills: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_team_id: Mapped[uuid.UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    routing_path: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    max_hops: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HumanNotificationDelivery(Base):
    """Durable evidence that a required human was actually called."""

    __tablename__ = "human_notification_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "decision_id",
            "recipient_user_id",
            "channel",
            "event_key",
            name="uq_human_delivery_recipient_event",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(FlexibleUUID(), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("human_decisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    event_key: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[HumanDeliveryStatus] = mapped_column(
        String(24), nullable=False, default=HumanDeliveryStatus.pending, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_receipt_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
