"""
Schemas de colaboração interna do inbox e drawer do lead.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.models.matter import KanbanColumnId, MicroStatus
from app.schemas.base import CamelCaseSchema, WriteSchema
from app.schemas.control_plane import MeetingRead
from app.schemas.matter import MatterRead
from app.schemas.whatsapp import WhatsAppContactRead


class ConversationInternalNoteRead(CamelCaseSchema):
    id: UUID
    contact_id: UUID
    matter_id: Optional[UUID] = None
    author_user_id: Optional[UUID] = None
    author_name: Optional[str] = None
    body: str
    mentions: list[str] = Field(default_factory=list)
    note_metadata: dict = Field(default_factory=dict)
    created_at: datetime

class ConversationInternalNoteCreate(WriteSchema):
    body: str = Field(..., min_length=1, max_length=4000)
    mentions: list[str] = Field(default_factory=list)


class MoveConversationStageRequest(WriteSchema):
    column_id: KanbanColumnId
    micro_status: Optional[MicroStatus] = None


class LeadDrawerRead(CamelCaseSchema):
    contact: WhatsAppContactRead
    matter: Optional[MatterRead] = None
    notes: list[ConversationInternalNoteRead] = Field(default_factory=list)
    recent_meetings: list[MeetingRead] = Field(default_factory=list)
    can_edit_matter: bool = False
    can_manage_conversation: bool = False


class MessageOptimizationRequest(WriteSchema):
    body: str = Field(..., min_length=1, max_length=4000)
    tone: str = "clear"


class MessageOptimizationRead(CamelCaseSchema):
    suggestions: list[str] = Field(default_factory=list)
