"""Versioned configuration documents for platform and tenant scopes."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin


class ConfigScope(StrEnum):
    platform = "platform"
    workspace = "workspace"
    role = "role"
    user = "user"


class ConfigDocument(UUIDMixin, TimestampMixin, Base):
    """Current tenant configuration plus its optimistic-concurrency metadata."""

    __tablename__ = "config_documents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "scope_key", name="uq_config_document_scope_key"),
        Index("ix_config_document_workspace_namespace", "workspace_id", "namespace"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    namespace: Mapped[str] = mapped_column(String(160), nullable=False)
    scope: Mapped[ConfigScope] = mapped_column(
        Enum(ConfigScope, name="config_scope_enum", native_enum=False), nullable=False
    )
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    role_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    etag: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ConfigDocumentVersion(UUIDMixin, Base):
    """Immutable tenant configuration revision."""

    __tablename__ = "config_document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_config_document_version"),
        Index("ix_config_version_document_created", "document_id", "created_at"),
    )

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("config_documents.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0")
    payload: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlatformConfigDocument(UUIDMixin, TimestampMixin, Base):
    """Global configuration, isolated from tenant-writable RLS tables."""

    __tablename__ = "platform_config_documents"
    __table_args__ = (UniqueConstraint("namespace", name="uq_platform_config_namespace"),)

    namespace: Mapped[str] = mapped_column(String(160), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    etag: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class PlatformConfigDocumentVersion(UUIDMixin, Base):
    """Immutable global configuration revision."""

    __tablename__ = "platform_config_document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_platform_config_document_version"),
    )

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("platform_config_documents.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0")
    payload: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
