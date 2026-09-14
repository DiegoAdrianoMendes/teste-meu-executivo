"""
Modelos da API externa oficial por workspace.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, UUIDMixin


class WorkspacePublicApiToken(UUIDMixin, Base):
    __tablename__ = "workspace_public_api_tokens"
    __table_args__ = (
        Index("ix_workspace_public_api_tokens_workspace_active", "workspace_id", "is_active"),
        Index("ix_workspace_public_api_tokens_prefix", "token_prefix"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    scopes: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    workspace = relationship("Workspace")
    created_by = relationship("User")
    audit_logs: Mapped[list["PublicApiRequestLog"]] = relationship(
        "PublicApiRequestLog",
        back_populates="token",
        cascade="all, delete-orphan",
    )


class PublicApiMutationState(str, enum.Enum):
    pending = "pending"
    outbox = "outbox"
    completed = "completed"
    uncertain = "uncertain"


class PublicApiMutationReceipt(UUIDMixin, Base):
    __tablename__ = "public_api_mutation_receipts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "token_id",
            "operation",
            "idempotency_key",
            name="uq_public_api_receipt_workspace_token_operation_key",
        ),
        Index(
            "ix_public_api_receipts_workspace_token_created",
            "workspace_id",
            "token_id",
            "created_at",
        ),
        Index("ix_public_api_receipts_state_updated", "state", "updated_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_public_api_tokens.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[PublicApiMutationState] = mapped_column(
        SAEnum(
            PublicApiMutationState,
            name="public_api_mutation_state_enum",
            native_enum=False,
            length=24,
        ),
        nullable=False,
        default=PublicApiMutationState.pending,
    )
    response_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Optional[dict]] = mapped_column(FlexibleJSON(), nullable=True)
    outbox_payload: Mapped[Optional[dict]] = mapped_column(FlexibleJSON(), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    uncertain_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    workspace = relationship("Workspace")
    token = relationship("WorkspacePublicApiToken")


class PublicApiRequestLog(UUIDMixin, Base):
    __tablename__ = "public_api_request_logs"
    __table_args__ = (
        Index("ix_public_api_request_logs_workspace_requested", "workspace_id", "requested_at"),
        Index("ix_public_api_request_logs_token_requested", "token_id", "requested_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_public_api_tokens.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(80), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    request_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    workspace = relationship("Workspace")
    token = relationship("WorkspacePublicApiToken", back_populates="audit_logs")
