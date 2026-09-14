from datetime import datetime
"""
Schemas Pydantic para Notification.

Mapeamento de campos (backend → frontend Notification):
  matter_id         → cardId           (frontend: Notification.cardId)
  notification_type → type             (frontend: Notification.type)
  is_read           → read             (frontend: Notification.read)
"""
from typing import Optional
from uuid import UUID
from pydantic import Field
from app.models.notification import NotificationType
from app.schemas.base import CamelCaseSchema


class NotificationRead(CamelCaseSchema):
    """
    Compatível com a interface Notification do frontend:
      id, title, message, timestamp, read, type, cardId
    """
    id: UUID
    user_id: UUID
    # Alias: frontend usa 'cardId', não 'matterId'
    matter_id: Optional[UUID] = Field(default=None, serialization_alias="cardId")
    title: str
    message: str
    # Alias: frontend usa 'type', não 'notificationType'
    notification_type: NotificationType = Field(serialization_alias="type")
    # Alias: frontend usa 'read', não 'isRead'
    is_read: bool = Field(serialization_alias="read")
    timestamp: datetime
    # created_at mapeado do modelo — o Notification usa 'timestamp' como campo principal
    # mas o service de notificações usa created_at para ordenação
    created_at: Optional[str] = Field(default=None, exclude=True)  # interno, não exposto
