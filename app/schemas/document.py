"""
Schemas Pydantic para Document.
Compatíveis com a interface DocumentItem do frontend TypeScript.

Mapeamento de campos (backend → frontend DocumentItem):
  doc_type  → type   (frontend: DocumentItem.type)
  s3_url    → url    (frontend: DocumentItem.url)
  file_size → size   (frontend: DocumentItem.size)

Campos com alias explícito pois divergem do padrão camelCase automático.
"""
from datetime import datetime
from uuid import UUID

from pydantic import AliasChoices, Field, model_validator

from app.models.document import DocumentStatus, DocumentType
from app.schemas.base import CamelCaseSchema, WriteSchema


class DocumentCreate(WriteSchema):
    name: str
    doc_type: DocumentType = DocumentType.outro
    status: DocumentStatus = DocumentStatus.aguardando
    file_size: str | None = None
    mime_type: str | None = None
    checksum_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )


class DocumentRead(CamelCaseSchema):
    """
    Compatível com a interface DocumentItem do frontend:
      id, name, type, status, url, createdAt, createdBy, size

    Campos com alias explícito (divergem do padrão camelCase automático):
      doc_type  → 'type'   (frontend usa DocumentItem.type)
      s3_url    → 'url'    (frontend usa DocumentItem.url)
      file_size → 'size'   (frontend usa DocumentItem.size)
    """
    id: UUID
    matter_id: UUID
    name: str
    # Frontend: DocumentItem.type — alias explícito pois 'docType' ≠ 'type'
    type: DocumentType | None = Field(
        default=None,
        validation_alias=AliasChoices("type", "doc_type", "docType"),
    )
    status: DocumentStatus
    # Frontend: DocumentItem.url — alias explícito pois 's3Url' ≠ 'url'
    url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("url", "s3_url", "s3Url"),
    )
    # Frontend: DocumentItem.size — alias explícito pois 'fileSize' ≠ 'size'
    size: str | None = Field(
        default=None,
        validation_alias=AliasChoices("size", "file_size", "fileSize"),
    )
    mime_type: str | None = None
    created_by: UUID | None = None
    created_at: datetime

    @model_validator(mode="after")
    def never_serialize_persistent_storage_url(self):
        object.__setattr__(self, "url", None)
        return self


class PresignedUrlResponse(CamelCaseSchema):
    """Resposta com URL pré-assinada do S3 para upload direto."""
    upload_url: str | None
    upload_required: bool = True
    document_id: UUID
    s3_key: str
    expires_in: int
    upload_headers: dict[str, str] = Field(default_factory=dict)


class DocumentDownloadUrlResponse(CamelCaseSchema):
    download_url: str
    expires_in: int
