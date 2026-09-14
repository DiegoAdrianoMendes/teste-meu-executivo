"""Persistência do conselho executivo consultivo do workspace.

Os executivos são deliberadamente read-only no v1: estas entidades guardam
perfis, conversas, evidências e relatórios, mas não oferecem qualquer vínculo
com executores ou ferramentas mutáveis.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin


class ExecutiveKey(str, enum.Enum):
    ceo = "ceo"
    coo = "coo"
    cfo = "cfo"
    cro = "cro"
    cpo = "cpo"
    cto = "cto"
    cio = "cio"
    chro = "chro"


class ExecutiveThreadVisibility(str, enum.Enum):
    private = "private"
    shared = "shared"


class ExecutiveThreadStatus(str, enum.Enum):
    active = "active"
    archived = "archived"
    deleted = "deleted"


class ExecutiveThreadMode(str, enum.Enum):
    direct = "direct"
    roundtable = "roundtable"


class ExecutiveRoundStatus(str, enum.Enum):
    received = "received"
    context_planning = "context_planning"
    retrieving_context = "retrieving_context"
    executive_selection = "executive_selection"
    executives_responding = "executives_responding"
    ceo_synthesizing = "ceo_synthesizing"
    completed = "completed"
    partial_failure = "partial_failure"
    failed = "failed"
    cancelled = "cancelled"


class ExecutiveAuthorType(str, enum.Enum):
    human = "human"
    executive = "executive"
    system = "system"


class ExecutiveMessageKind(str, enum.Enum):
    question = "question"
    response = "response"
    synthesis = "synthesis"
    notice = "notice"
    error = "error"


class ExecutiveReportStatus(str, enum.Enum):
    scheduled = "scheduled"
    collecting_data = "collecting_data"
    generating = "generating"
    published = "published"
    published_with_gaps = "published_with_gaps"
    partial_failure = "partial_failure"
    failed = "failed"
    superseded = "superseded"


class ExecutiveProfile(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "executive_profiles"
    __table_args__ = (
        UniqueConstraint("workspace_id", "key", name="uq_executive_profile_workspace_key"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[ExecutiveKey] = mapped_column(
        Enum(ExecutiveKey, name="executive_key_enum", native_enum=False), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    mention: Mapped[str] = mapped_column(String(16), nullable=False)
    role: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    domain_manifest: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    allowed_read_capabilities: Mapped[list] = mapped_column(
        FlexibleJSON(), nullable=False, default=list
    )
    forbidden_capabilities: Mapped[list] = mapped_column(
        FlexibleJSON(), nullable=False, default=list
    )
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False, default="v1")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class ExecutiveThread(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "executive_threads"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_executive_thread_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "created_by_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_executive_thread_creator_membership",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(240), nullable=True)
    title_manually_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    visibility: Mapped[ExecutiveThreadVisibility] = mapped_column(
        Enum(ExecutiveThreadVisibility, name="executive_thread_visibility_enum", native_enum=False),
        nullable=False,
        default=ExecutiveThreadVisibility.private,
    )
    status: Mapped[ExecutiveThreadStatus] = mapped_column(
        Enum(ExecutiveThreadStatus, name="executive_thread_status_enum", native_enum=False),
        nullable=False,
        default=ExecutiveThreadStatus.active,
    )
    mode: Mapped[ExecutiveThreadMode] = mapped_column(
        Enum(ExecutiveThreadMode, name="executive_thread_mode_enum", native_enum=False),
        nullable=False,
    )
    selected_executives: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    ceo_always_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutiveThreadShare(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "executive_thread_shares"
    __table_args__ = (
        UniqueConstraint("thread_id", "user_id", name="uq_executive_thread_share_user"),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["executive_threads.workspace_id", "executive_threads.id"],
            name="fk_executive_share_workspace_thread",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_executive_share_user_membership",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "granted_by_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_executive_share_granter_membership",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    thread_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    granted_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    permission: Mapped[str] = mapped_column(String(32), nullable=False, default="view_and_reply")
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutiveRound(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "executive_rounds"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_executive_round_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["executive_threads.workspace_id", "executive_threads.id"],
            name="fk_executive_round_workspace_thread",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    thread_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    user_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "executive_messages.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_executive_round_user_message",
        ),
        nullable=True,
    )
    status: Mapped[ExecutiveRoundStatus] = mapped_column(
        Enum(ExecutiveRoundStatus, name="executive_round_status_enum", native_enum=False),
        nullable=False,
        default=ExecutiveRoundStatus.received,
        index=True,
    )
    requested_executives: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    selected_by_ceo: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    selection_reasons: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    context_scope: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExecutiveSourceReceipt(UUIDMixin, Base):
    __tablename__ = "executive_source_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["executive_threads.workspace_id", "executive_threads.id"],
            name="fk_executive_receipt_workspace_thread",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "round_id"],
            ["executive_rounds.workspace_id", "executive_rounds.id"],
            name="fk_executive_receipt_workspace_round",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    thread_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    round_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    executive_key: Mapped[ExecutiveKey | None] = mapped_column(
        Enum(ExecutiveKey, name="executive_receipt_key_enum", native_enum=False), nullable=True
    )
    pipeline_version: Mapped[str] = mapped_column(String(40), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    period: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    structured_queries: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    selected_sources: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    rejected_sources: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    insufficiency_reasons: Mapped[list] = mapped_column(
        FlexibleJSON(), nullable=False, default=list
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class ExecutiveMessage(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "executive_messages"
    __table_args__ = (
        UniqueConstraint("round_id", "sequence", name="uq_executive_message_round_sequence"),
        ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["executive_threads.workspace_id", "executive_threads.id"],
            name="fk_executive_message_workspace_thread",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "round_id"],
            ["executive_rounds.workspace_id", "executive_rounds.id"],
            name="fk_executive_message_workspace_round",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "author_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_executive_message_author_membership",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    thread_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    round_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    author_type: Mapped[ExecutiveAuthorType] = mapped_column(
        Enum(ExecutiveAuthorType, name="executive_author_type_enum", native_enum=False),
        nullable=False,
    )
    author_user_id: Mapped[UUID | None] = mapped_column(nullable=True)
    executive_key: Mapped[ExecutiveKey | None] = mapped_column(
        Enum(ExecutiveKey, name="executive_message_key_enum", native_enum=False), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    message_kind: Mapped[ExecutiveMessageKind] = mapped_column(
        Enum(ExecutiveMessageKind, name="executive_message_kind_enum", native_enum=False),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="completed")
    source_receipt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("executive_source_receipts.id", ondelete="SET NULL"), nullable=True
    )
    model_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )


class ExecutiveReport(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "executive_reports"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "generation_key", name="uq_executive_report_generation_key"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[ExecutiveReportStatus] = mapped_column(
        Enum(ExecutiveReportStatus, name="executive_report_status_enum", native_enum=False),
        nullable=False,
        default=ExecutiveReportStatus.scheduled,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    executive_participants: Mapped[list] = mapped_column(
        FlexibleJSON(), nullable=False, default=list
    )
    source_receipt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("executive_source_receipts.id", ondelete="SET NULL"), nullable=True
    )
    generation_key: Mapped[str] = mapped_column(String(100), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("executive_reports.id", ondelete="SET NULL"), nullable=True
    )
