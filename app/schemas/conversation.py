from datetime import datetime
"""
Schemas Pydantic para Conversation.

Mapeamento de campos (backend → frontend):
  agent_id    → agentId
  is_internal → isInternal
  is_read     → isRead
  matter_id   → matterId
"""
from typing import Optional
from uuid import UUID
from pydantic import field_serializer
from app.schemas.base import CamelCaseSchema, WriteSchema


class ConversationCreate(WriteSchema):
    message: str
    is_internal: bool = False
    agent_id: Optional[UUID] = None


class ConversationRead(CamelCaseSchema):
    """
    Compatível com a interface ConversationItem do frontend:
      id, agentId, message, timestamp, isInternal
    """
    id: UUID
    matter_id: UUID
    agent_id: Optional[UUID] = None
    message: str
    is_internal: bool
    is_read: bool
    timestamp: datetime

    @field_serializer("id", "matter_id", "agent_id")
    def serialize_uuid(self, value: Optional[UUID]) -> Optional[str]:
        return str(value) if value else None
