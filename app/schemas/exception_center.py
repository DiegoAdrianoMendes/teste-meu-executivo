"""
Schemas Pydantic para a Central de Exceções (LegalException).

Mesma disciplina de serialização camelCase de app/schemas/approval.py —
`ExceptionCenterRead` é o schema compatível com o frontend; os schemas de
escrita NUNCA aceitam `resolvedBy`/`escalatedBy` — esses campos são
sempre atribuídos pelo endpoint a partir do usuário autenticado (ver
app/api/v1/endpoints/exception_center.py), nunca lidos do payload
(lição do Risco 2).
"""
from datetime import datetime
from uuid import UUID

from pydantic import Field, field_serializer

from app.models.approval import ApprovalPriority
from app.models.legal_exception import ExceptionCenterStatus, ExceptionKind
from app.schemas.base import CamelCaseSchema, WriteSchema


class ExceptionResolveRequest(WriteSchema):
    """Corpo de POST /legal/exceptions/{id}/resolver.

    `resolvedBy` NUNCA é aceito aqui — o endpoint sempre injeta o usuário
    autenticado antes de chamar o service.
    """

    resolution_reason: str | None = None


class ExceptionEscalateRequest(WriteSchema):
    """Corpo de POST /legal/exceptions/{id}/escalar.

    `escalatedTo` é só o destinatário sugerido (não muda quem é o autor
    auditado da escalação — esse vem sempre do autenticado).
    """

    resolution_reason: str | None = None
    escalated_to: UUID | None = None


class ExceptionCenterRead(CamelCaseSchema):
    id: UUID
    matter_id: UUID | None = Field(default=None, serialization_alias="matterId")
    exception_kind: ExceptionKind = Field(serialization_alias="exceptionKind")
    origin: str
    dedupe_key: str = Field(serialization_alias="dedupeKey")
    title: str
    description: str | None = None
    payload: dict | None = None
    priority: ApprovalPriority
    status: ExceptionCenterStatus
    opened_by: UUID | None = Field(default=None, serialization_alias="openedBy")
    resolved_by: UUID | None = Field(default=None, serialization_alias="resolvedBy")
    escalated_to: UUID | None = Field(default=None, serialization_alias="escalatedTo")
    resolution_reason: str | None = Field(
        default=None, serialization_alias="resolutionReason"
    )
    resolved_at: datetime | None = Field(default=None, serialization_alias="resolvedAt")
    escalated_at: datetime | None = Field(default=None, serialization_alias="escalatedAt")
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")

    @field_serializer("matter_id", "opened_by", "resolved_by", "escalated_to")
    def serialize_uuid_as_str(self, v: UUID | None) -> str | None:
        return str(v) if v is not None else None

    @field_serializer("created_at", "updated_at", "resolved_at", "escalated_at")
    def serialize_datetime_as_iso(self, v: datetime | None) -> str | None:
        return v.isoformat() if v is not None else None
