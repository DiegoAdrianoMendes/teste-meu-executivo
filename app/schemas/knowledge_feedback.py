"""
Schemas Pydantic para KnowledgeArticleFeedback.
"""
from datetime import datetime
from uuid import UUID

from app.models.knowledge_feedback import KnowledgeFeedbackValue
from app.schemas.base import CamelCaseSchema, WriteSchema


class KnowledgeArticleFeedbackCreate(WriteSchema):
    value: KnowledgeFeedbackValue


class KnowledgeArticleFeedbackRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    article_id: str
    value: KnowledgeFeedbackValue
    created_at: datetime
    updated_at: datetime


class KnowledgeArticleFeedbackSummary(CamelCaseSchema):
    article_id: str
    up_count: int
    down_count: int
    my_feedback: KnowledgeFeedbackValue | None = None
