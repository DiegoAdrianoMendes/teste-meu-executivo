"""
Modelos globais para Google Business Profile / Maps.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User


class GoogleBusinessConnectionStatus(str, Enum):
    disconnected = "disconnected"
    pending = "pending"
    connected = "connected"
    error = "error"


class GoogleBusinessPublishStatus(str, Enum):
    draft = "draft"
    queued = "queued"
    scheduled = "scheduled"
    published = "published"
    failed = "failed"
    requires_review = "requires_review"


class GoogleBusinessProfileConnection(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "google_business_profile_connections"

    business_name: Mapped[str] = mapped_column(String(180), nullable=False, default="Meu Executivo")
    account_name: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    location_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    location_name: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    status: Mapped[GoogleBusinessConnectionStatus] = mapped_column(
        String(20),
        nullable=False,
        default=GoogleBusinessConnectionStatus.disconnected,
        index=True,
    )
    profile_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    client_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    client_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    access_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scopes: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    settings: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    updated_by_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[updated_by_user_id])


class GoogleBusinessPublishJob(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "google_business_publish_jobs"

    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("google_business_profile_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    target_id: Mapped[Optional[UUID]] = mapped_column(nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    status: Mapped[GoogleBusinessPublishStatus] = mapped_column(
        String(20),
        nullable=False,
        default=GoogleBusinessPublishStatus.draft,
        index=True,
    )
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    provider_response: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    connection: Mapped["GoogleBusinessProfileConnection"] = relationship("GoogleBusinessProfileConnection")
    created_by_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[created_by_user_id])
