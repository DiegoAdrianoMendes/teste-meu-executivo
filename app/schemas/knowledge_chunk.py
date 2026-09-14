"""
Schemas Pydantic para KnowledgeChunk.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelCaseSchema


class KnowledgeChunkRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: UUID
    task_id: Optional[UUID] = None
    created_by_user_id: Optional[UUID] = None
    source_type: str
    source_ref: Optional[str] = None
    title: Optional[str] = None
    content_text: str
    summary: Optional[str] = None
    content_hash: str
    rank_hint: int = 0
    tags: List[str] = Field(default_factory=list)
    chunk_metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
