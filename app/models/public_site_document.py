"""
Modelo: PublicSiteDocument
Armazena os documentos globais do site público da plataforma.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User


class PublicSiteDocument(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "public_site_documents"

    key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    draft_content: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    published_content: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    published_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    updated_by_user: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[updated_by_user_id],
    )
    published_by_user: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[published_by_user_id],
    )

    def __repr__(self) -> str:
        return f"<PublicSiteDocument key={self.key} version={self.version}>"
