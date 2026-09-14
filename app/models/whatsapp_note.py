"""
Notas internas e colaboração operacional do inbox do WhatsApp.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.user import User
    from app.models.whatsapp import WhatsAppContact
    from app.models.workspace import Workspace


class WhatsAppConversationNote(UUIDMixin, Base):
    __tablename__ = "whatsapp_conversation_notes"
    __table_args__ = (
        Index("ix_wa_conversation_notes_contact_created", "contact_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[UUID] = mapped_column(
        ForeignKey("whatsapp_contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("matters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    author_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    mentions: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    note_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    contact: Mapped["WhatsAppContact"] = relationship("WhatsAppContact")
    matter: Mapped[Optional["Matter"]] = relationship("Matter")
    author: Mapped[Optional["User"]] = relationship("User")
