"""
Modelo: Conversation
Mensagens internas (entre agentes/equipe) e externas (com o cliente)
vinculadas a um caso.
"""
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.user import User


class Conversation(UUIDMixin, Base):
    """
    Entidade Conversation — mensagem no chat do caso.
    is_internal=True: mensagem entre membros da equipe/IA.
    is_internal=False: mensagem enviada ao cliente.
    """
    __tablename__ = "conversations"

    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # ── Relacionamentos ──
    matter: Mapped["Matter"] = relationship("Matter", back_populates="conversations")
    author: Mapped[Optional["User"]] = relationship("User")

    def __repr__(self) -> str:
        return f"<Conversation id={self.id} matter={self.matter_id} internal={self.is_internal}>"
