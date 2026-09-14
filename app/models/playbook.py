"""Executable sales playbooks built on immutable configuration revisions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, UUIDMixin


class PlaybookLifecycleState(StrEnum):
    draft = "draft"
    in_review = "in_review"
    approved = "approved"
    published = "published"
    superseded = "superseded"


class PlaybookScopeDimension(StrEnum):
    area = "area"
    channel = "channel"
    stage = "stage"
    agent = "agent"
    offer = "offer"


class PlaybookSimulationStatus(StrEnum):
    passed = "passed"
    failed = "failed"
    needs_review = "needs_review"


class PlaybookVersionGovernance(UUIDMixin, Base):
    """Append-only lifecycle event for one immutable playbook revision."""

    __tablename__ = "playbook_version_governance"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version",
            "sequence",
            name="uq_playbook_governance_sequence",
        ),
        UniqueConstraint(
            "document_id",
            "version",
            "state",
            name="uq_playbook_governance_state",
        ),
        CheckConstraint(
            "(state = 'draft' AND sequence = 1) OR "
            "(state = 'in_review' AND sequence = 2) OR "
            "(state = 'approved' AND sequence = 3) OR "
            "(state = 'published' AND sequence = 4) OR "
            "(state = 'superseded' AND sequence = 5)",
            name="ck_playbook_governance_state_sequence",
        ),
        Index(
            "ix_playbook_governance_workspace_document",
            "workspace_id",
            "document_id",
            "version",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("config_documents.id", ondelete="CASCADE"), nullable=False
    )
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("config_document_versions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[PlaybookLifecycleState] = mapped_column(
        Enum(
            PlaybookLifecycleState,
            name="playbook_lifecycle_state_enum",
            native_enum=False,
        ),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlaybookScopeBinding(UUIDMixin, Base):
    """Normalized, immutable resolver binding for a playbook revision."""

    __tablename__ = "playbook_scope_bindings"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version",
            "dimension",
            "value",
            name="uq_playbook_scope_binding",
        ),
        Index(
            "ix_playbook_scope_resolver",
            "workspace_id",
            "dimension",
            "value",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("config_documents.id", ondelete="CASCADE"), nullable=False
    )
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("config_document_versions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension: Mapped[PlaybookScopeDimension] = mapped_column(
        Enum(
            PlaybookScopeDimension,
            name="playbook_scope_dimension_enum",
            native_enum=False,
        ),
        nullable=False,
    )
    value: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlaybookSimulationRun(UUIDMixin, Base):
    """Immutable local evaluation result, pinned to a content digest and version."""

    __tablename__ = "playbook_simulation_runs"
    __table_args__ = (
        CheckConstraint(
            "external_actions_executed = 0",
            name="ck_playbook_simulation_local_only",
        ),
        CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0 AND estimated_cost_brl >= 0",
            name="ck_playbook_simulation_nonnegative_usage",
        ),
        Index(
            "ix_playbook_simulation_document_created",
            "workspace_id",
            "document_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("config_documents.id", ondelete="CASCADE"), nullable=False
    )
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("config_document_versions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[PlaybookSimulationStatus] = mapped_column(
        Enum(
            PlaybookSimulationStatus,
            name="playbook_simulation_status_enum",
            native_enum=False,
        ),
        nullable=False,
    )
    scenario: Mapped[str] = mapped_column(String(240), nullable=False)
    customer_message: Mapped[str] = mapped_column(String(4096), nullable=False)
    context: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    response: Mapped[str] = mapped_column(String(8192), nullable=False)
    next_action: Mapped[str] = mapped_column(String(1000), nullable=False)
    policy_trace: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    sources: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    autonomy_mode: Mapped[str] = mapped_column(String(80), nullable=False)
    human_approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    external_actions_executed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_brl: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0")
    )
    model_key: Mapped[str] = mapped_column(
        String(120), nullable=False, default="local-deterministic-v1"
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
