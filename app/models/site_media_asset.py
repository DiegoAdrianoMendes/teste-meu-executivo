"""
Biblioteca global de mídia do site/blog.
"""
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User


class SiteMediaAssetStatus(str, Enum):
    draft = "draft"
    ready = "ready"
    processing = "processing"
    failed = "failed"


class SiteMediaAsset(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "site_media_assets"

    key: Mapped[str] = mapped_column(String(180), nullable=False, unique=True, index=True)
    label: Mapped[str] = mapped_column(String(180), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    file_kind: Mapped[str] = mapped_column(String(40), nullable=False, default="image")
    extension: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[SiteMediaAssetStatus] = mapped_column(String(20), nullable=False, default=SiteMediaAssetStatus.draft, index=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="upload")
    s3_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    public_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    alt_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    usage_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    generation_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    updated_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_by_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[created_by_user_id])
    updated_by_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[updated_by_user_id])
