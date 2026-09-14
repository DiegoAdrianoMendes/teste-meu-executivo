"""
Schemas Pydantic para ApprovalQueueItem.

Mapeamento de campos (backend → frontend ApprovalItem):
  matter_id      → cardId       (frontend: ApprovalItem.cardId)
  approval_type  → type         (frontend: ApprovalItem.type)
  requested_by   → requestedBy
  resolved_by    → resolvedBy
  resolved_at    → resolvedAt
  created_at     → requestedAt  (frontend: ApprovalItem.requestedAt)
  document_version_url → documentVersionUrl
  client_name    → clientName   (desnormalizado para a tabela da UI)
"""
from datetime import datetime
from uuid import UUID

from pydantic import Field, field_serializer, field_validator

from app.models.approval import ApprovalDecision, ApprovalPriority, ApprovalStatus
from app.schemas.base import CamelCaseSchema, WriteSchema


class ApprovalCreate(WriteSchema):
    matter_id: UUID
    approval_type: str
    intent_key: str | None = Field(default=None, min_length=1, max_length=255)
    title: str
    description: str | None = None
    priority: ApprovalPriority = ApprovalPriority.media
    requested_by: UUID | None = None
    document_version_url: str | None = None

    @field_validator("intent_key", mode="before")
    @classmethod
    def normalize_intent_key(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("intent_key must not be blank")
        return normalized


class ApprovalResolveRequest(WriteSchema):
    status: ApprovalDecision
    resolution_reason: str | None = None
    resolved_by: UUID | None = None


class ApprovalRead(CamelCaseSchema):
    """
    Compatível com a interface ApprovalItem do frontend:
      id, cardId, clientName, type, title, description,
      requestedBy, requestedAt, status, resolvedBy, resolvedAt, priority
    """
    id: UUID
    # Alias: frontend usa 'cardId', não 'matterId'
    matter_id: UUID = Field(serialization_alias="cardId")
    # clientName desnormalizado — populado pelo service antes da serialização
    client_name: str = Field(default="", serialization_alias="clientName")
    # Alias: frontend usa 'type', não 'approvalType'
    approval_type: str = Field(serialization_alias="type")
    intent_key: str = Field(serialization_alias="intentKey")
    title: str
    description: str | None = None
    priority: ApprovalPriority
    status: ApprovalStatus
    # UUID aceita tanto UUID quanto None; serializado como string via field_serializer
    requested_by: UUID | None = Field(default=None, serialization_alias="requestedBy")
    resolved_by: UUID | None = Field(default=None, serialization_alias="resolvedBy")
    resolution_reason: str | None = Field(
        default=None,
        serialization_alias="resolutionReason",
    )
    resolved_at: datetime | None = Field(default=None, serialization_alias="resolvedAt")
    document_version_url: str | None = Field(
        default=None,
        serialization_alias="documentVersionUrl",
    )
    # Alias: frontend usa 'requestedAt', não 'createdAt'
    created_at: datetime = Field(serialization_alias="requestedAt")
    updated_at: datetime
    sensitive_data_hidden: bool = False

    @field_serializer("requested_by", "resolved_by")
    def serialize_uuid_as_str(self, v: UUID | None) -> str | None:
        """Serializa UUID como string para compatibilidade com o frontend."""
        return str(v) if v is not None else None

    @field_serializer("created_at", "updated_at", "resolved_at")
    def serialize_datetime_as_iso(self, v: datetime | None) -> str | None:
        """Serializa datetime como ISO 8601 string."""
        if v is None:
            return None
        return v.isoformat()
