"""Schemas Pydantic para o DMS jurídico (Onda 7, Plano Mestre §11)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.models.document_asset import (
    DocumentAssetSource,
    DocumentAssetType,
    DocumentClassificationMethod,
    DocumentOCRStatus,
)
from app.models.storage_object import StorageAVStatus, StorageObjectState
from app.schemas.base import CamelCaseSchema, WriteSchema


class DocumentAssetRead(CamelCaseSchema):
    id: UUID
    storage_object_id: UUID = Field(serialization_alias="storageObjectId")
    content_sha256: str = Field(serialization_alias="contentSha256")
    original_filename: str = Field(serialization_alias="originalFilename")
    matter_id: UUID | None = Field(default=None, serialization_alias="matterId")
    client_id: UUID | None = Field(default=None, serialization_alias="clientId")
    document_type: DocumentAssetType = Field(serialization_alias="documentType")
    classification_method: DocumentClassificationMethod = Field(
        serialization_alias="classificationMethod"
    )
    classification_confidence: float | None = Field(
        default=None, serialization_alias="classificationConfidence"
    )
    ocr_status: DocumentOCRStatus = Field(serialization_alias="ocrStatus")
    created_at: datetime = Field(serialization_alias="createdAt")


class DocumentAssetStorageRead(CamelCaseSchema):
    state: StorageObjectState
    av_status: StorageAVStatus = Field(serialization_alias="avStatus")
    quarantine_reason: str | None = Field(default=None, serialization_alias="quarantineReason")
    byte_size: int | None = Field(default=None, serialization_alias="byteSize")
    version: int


class DocumentAssetDetailRead(DocumentAssetRead):
    storage: DocumentAssetStorageRead | None = None


class DocumentAssetReferenceRead(CamelCaseSchema):
    id: UUID
    document_asset_id: UUID = Field(serialization_alias="documentAssetId")
    source: DocumentAssetSource
    original_filename: str = Field(serialization_alias="originalFilename")
    external_ref: str | None = Field(default=None, serialization_alias="externalRef")
    matter_id: UUID | None = Field(default=None, serialization_alias="matterId")
    created_at: datetime = Field(serialization_alias="createdAt")


class DocumentAssetIngestResponse(CamelCaseSchema):
    asset: DocumentAssetRead
    reference: DocumentAssetReferenceRead
    created_new_asset: bool = Field(serialization_alias="createdNewAsset")
    deduplicated: bool


class DocumentShareLinkCreate(WriteSchema):
    expires_at: datetime
    watermark_enabled: bool = False
    max_downloads: int | None = None


class DocumentShareLinkIssuedRead(CamelCaseSchema):
    id: UUID
    document_asset_id: UUID = Field(serialization_alias="documentAssetId")
    token: str
    expires_at: datetime = Field(serialization_alias="expiresAt")
    watermark_enabled: bool = Field(serialization_alias="watermarkEnabled")
    max_downloads: int | None = Field(default=None, serialization_alias="maxDownloads")


class DocumentShareLinkRead(CamelCaseSchema):
    id: UUID
    document_asset_id: UUID = Field(serialization_alias="documentAssetId")
    expires_at: datetime = Field(serialization_alias="expiresAt")
    watermark_enabled: bool = Field(serialization_alias="watermarkEnabled")
    max_downloads: int | None = Field(default=None, serialization_alias="maxDownloads")
    download_count: int = Field(serialization_alias="downloadCount")
    revoked_at: datetime | None = Field(default=None, serialization_alias="revokedAt")
    created_at: datetime = Field(serialization_alias="createdAt")


class DocumentShareDownloadRead(CamelCaseSchema):
    id: UUID
    share_link_id: UUID = Field(serialization_alias="shareLinkId")
    downloaded_at: datetime = Field(serialization_alias="downloadedAt")
    downloaded_by_user_id: UUID | None = Field(
        default=None, serialization_alias="downloadedByUserId"
    )
    downloader_ip: str | None = Field(default=None, serialization_alias="downloaderIp")


class DocumentShareRedeemRequest(WriteSchema):
    token: str


class SyncConflictReportRequest(WriteSchema):
    provider: str
    external_id: str
    matter_id: UUID | None = None
    local_checksum: str | None = None
    remote_checksum: str | None = None
