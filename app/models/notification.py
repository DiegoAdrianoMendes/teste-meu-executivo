"""
Modelo: Notification
Notificações do sistema para usuários humanos.
"""
import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.matter import Matter
    from app.models.workspace import Workspace


class NotificationType(str, enum.Enum):
    info = "info"
    warning = "warning"
    urgent = "urgent"
    success = "success"


class Notification(UUIDMixin, Base):
    """
    Entidade Notification — alerta enviado a um usuário.
    """
    __tablename__ = "notifications"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    matter_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("matters.id", ondelete="SET NULL"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    notification_type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="notification_type_enum"),
        nullable=False,
        default=NotificationType.info,
    )
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # ── Relacionamentos ──
    user: Mapped["User"] = relationship("User")
    matter: Mapped[Optional["Matter"]] = relationship("Matter")
    workspace: Mapped[Optional["Workspace"]] = relationship("Workspace")

    def __repr__(self) -> str:
        return f"<Notification id={self.id} user={self.user_id} read={self.is_read}>"
