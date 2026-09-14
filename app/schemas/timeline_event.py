from datetime import datetime
"""
Schemas Pydantic para TimelineEvent.

Mapeamento de campos (backend → frontend):
  event_type  → type       (frontend: TimelineEvent.type)
  agent_id    → agentId
  event_data  → metadata   (frontend: TimelineEvent.metadata)
  timestamp   → timestamp  (igual)
"""
from typing import Any, Dict, Optional
from uuid import UUID
from pydantic import Field, field_serializer
from app.models.timeline_event import TimelineEventType
from app.schemas.base import CamelCaseSchema, WriteSchema


class TimelineEventCreate(WriteSchema):
    event_type: TimelineEventType
    title: str
    description: Optional[str] = None
    agent_id: Optional[UUID] = None
    event_data: Optional[Dict[str, Any]] = None


class TimelineEventRead(CamelCaseSchema):
    """
    Compatível com a interface TimelineEvent do frontend:
      id, type, title, description, timestamp, agentId, metadata
    """
    id: UUID
    matter_id: UUID
    # Alias: frontend usa 'type', não 'eventType'
    event_type: TimelineEventType = Field(serialization_alias="type")
    title: str
    description: Optional[str] = None
    agent_id: Optional[UUID] = None
    timestamp: datetime
    # Alias: frontend usa 'metadata', mas o atributo Python é 'event_data'
    event_data: Optional[Dict[str, Any]] = Field(default=None, serialization_alias="metadata")

    @field_serializer("id", "matter_id", "agent_id")
    def serialize_uuid(self, value: Optional[UUID]) -> Optional[str]:
        return str(value) if value else None
