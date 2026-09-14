"""
Modelo editorial do blog público da KLM Advogados.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User


class BlogPostStatus(str, Enum):
    draft = "draft"
    scheduled = "scheduled"
    published = "published"
    archived = "archived"


class BlogPost(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "blog_posts"

    slug: Mapped[str] = mapped_column(String(180), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    subtitle: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author_name: Mapped[str] = mapped_column(String(140), nullable=False, default="Equipe Meu Executivo")
    author_role: Mapped[Optional[str]] = mapped_column(String(140), nullable=True)
    status: Mapped[BlogPostStatus] = mapped_column(String(20), nullable=False, default=BlogPostStatus.draft, index=True)
    featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    content_blocks: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    cover_media: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    og_media: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    categories: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    tags: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    seo: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    indexing: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    seo_audit: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    ai_config: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    reading_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
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
