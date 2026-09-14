"""
Modelo: CardFieldChange
Auditoria granular de alterações relevantes do card.
"""
from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.user import User


class CardFieldChangeActorType(str, enum.Enum):
    human = "human"
    ai = "ai"
    system = "system"


class CardFieldChange(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "card_field_changes"

    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    changed_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_type: Mapped[CardFieldChangeActorType] = mapped_column(
        Enum(CardFieldChangeActorType, name="card_field_change_actor_type_enum"),
        nullable=False,
        default=CardFieldChangeActorType.system,
    )
    field_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    old_value: Mapped[Optional[dict]] = mapped_column(FlexibleJSON(), nullable=True)
    new_value: Mapped[Optional[dict]] = mapped_column(FlexibleJSON(), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    change_source: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    matter: Mapped["Matter"] = relationship("Matter", back_populates="field_changes")
    changed_by_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[changed_by_user_id])
