"""
Endpoints: DMS jurídico (Onda 7, Plano Mestre §11).

REUSA `LegalDocumentService` (upload/dedup/classificação/OCR honesto via
`LocalObjectStorageService`/`StorageObject`) e `ExceptionCenterService`
(conflito de sincronização). Este router só amarra
`/legal/documents` + capability `legal` por cima do que já existe —
nenhuma lógica de armazenamento, antivírus ou quarentena é duplicada
aqui.

Ordem das rotas é deliberada: os caminhos literais (`/upload`,
`/quarantine`, `/share/redeem`, `/sync-conflicts`, `/share-links/...`)
são declarados ANTES de `/{asset_id}` — o roteador do Starlette casa por
ordem de registro, e `{asset_id}` casaria com qualquer segmento literal
se viesse primeiro.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.entitlement_errors import entitlement_http_exception
from app.core.config import settings
from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_permission
from app.models.document_asset import DocumentAssetSource, DocumentAssetType
from app.schemas.exception_center import ExceptionCenterRead
from app.schemas.legal_document import (
    DocumentAssetDetailRead,
    DocumentAssetIngestResponse,
    DocumentAssetReferenceRead,
    DocumentAssetStorageRead,
    DocumentShareDownloadRead,
    DocumentShareLinkCreate,
    DocumentShareLinkIssuedRead,
    DocumentShareLinkRead,
    DocumentShareRedeemRequest,
    SyncConflictReportRequest,
)
from app.services.entitlement_service import EntitlementError
from app.services.legal_document_service import (
    IngestResult,
    LegalDocumentConflictError,
    LegalDocumentError,
    LegalDocumentNotFoundError,
    LegalDocumentService,
    LegalDocumentValidationError,
)

router = APIRouter(prefix="/legal/documents", tags=["Jurídico — DMS (Onda 7)"])

require_documents_view = require_permission("legal", "view")
require_documents_manage = require_permission("legal", "manage")


def _http_error(error: LegalDocumentError) -> HTTPException:
    if isinstance(error, LegalDocumentNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, LegalDocumentValidationError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    if isinstance(error, LegalDocumentConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


async def _detail_read(
    service: LegalDocumentService, result_asset
) -> DocumentAssetDetailRead:
    storage_object = await service.get_storage_object(result_asset)
    storage_read = (
        DocumentAssetStorageRead.model_validate(storage_object) if storage_object else None
    )
    detail = DocumentAssetDetailRead.model_validate(result_asset)
    detail.storage = storage_read
    return detail


def _ingest_response(result: IngestResult) -> DocumentAssetIngestResponse:
    return DocumentAssetIngestResponse(
        asset=result.asset,
        reference=result.reference,
        created_new_asset=result.created_new_asset,
        deduplicated=result.deduplicated,
    )


async def _read_bounded_upload(file: UploadFile) -> bytes:
    """Bound multipart memory use before the document service receives bytes."""
    payload = bytearray()
    while chunk := await file.read(1024 * 1024):
        payload.extend(chunk)
        if len(payload) > settings.DOCUMENT_AV_MAX_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "code": "LEGAL_DOCUMENT_TOO_LARGE",
                    "message": "Documento excede o limite do pipeline antivírus.",
                    "maxBytes": settings.DOCUMENT_AV_MAX_BYTES,
                },
            )
    return bytes(payload)


@router.post(
    "/upload",
    response_model=DocumentAssetIngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enviar um ou mais documentos — deduplicado por hash de conteúdo",
)
async def upload_document(
    file: UploadFile = File(...),
    matter_id: UUID | None = Form(default=None),
    client_id: UUID | None = Form(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_documents_manage),
    db: AsyncSession = Depends(get_db),
) -> DocumentAssetIngestResponse:
    payload = await _read_bounded_upload(file)
    service = LegalDocumentService(db)
    try:
        result = await service.ingest_bytes(
            workspace_id=workspace_id,
            actor_user_id=auth.user_id,
            filename=file.filename or "documento",
            content_type=file.content_type or "application/octet-stream",
            payload=payload,
            source=DocumentAssetSource.upload_manual,
            matter_id=matter_id,
            client_id=client_id,
        )
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc
    except LegalDocumentError as exc:
        raise _http_error(exc) from exc
    await db.commit()
    return _ingest_response(result)


@router.get(
    "",
    response_model=list[DocumentAssetDetailRead],
    summary="Listar/buscar documentos por texto do nome, tipo ou caso",
)
async def list_documents(
    matter_id: UUID | None = Query(default=None),
    document_type: DocumentAssetType | None = Query(default=None),
    query: str | None = Query(default=None, alias="q"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_documents_view),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentAssetDetailRead]:
    service = LegalDocumentService(db)
    assets = await service.list_assets(
        workspace_id=workspace_id,
        matter_id=matter_id,
        document_type=document_type,
        query=query,
        limit=limit,
        offset=offset,
    )
    return [await _detail_read(service, asset) for asset in assets]


@router.get(
    "/quarantine",
    response_model=list[DocumentAssetDetailRead],
    summary="Listar documentos em quarentena — não processados",
)
async def list_quarantined_documents(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_documents_view),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentAssetDetailRead]:
    service = LegalDocumentService(db)
    assets = await service.list_quarantined(workspace_id=workspace_id)
    return [await _detail_read(service, asset) for asset in assets]


@router.post(
    "/share/redeem",
    summary="Resgatar um link de compartilhamento — registra a trilha de download",
)
async def redeem_share_link(
    payload: DocumentShareRedeemRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_documents_view),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    service = LegalDocumentService(db)
    try:
        result = await service.resolve_share_download(
            workspace_id=workspace_id,
            raw_token=payload.token,
            downloaded_by_user_id=auth.user_id,
        )
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc
    except LegalDocumentError as exc:
        raise _http_error(exc) from exc
    await db.commit()
    asset = await service.get_asset(
        workspace_id=workspace_id, document_asset_id=result.share_link.document_asset_id
    )
    headers = {
        "Content-Disposition": f'attachment; filename="{asset.original_filename}"',
        "X-Document-Watermark": "enabled" if result.share_link.watermark_enabled else "disabled",
    }
    content_type = (
        result.storage_object.detected_content_type
        or result.storage_object.declared_content_type
        or "application/octet-stream"
    )
    return StreamingResponse(result.handle, media_type=content_type, headers=headers)


@router.post(
    "/sync-conflicts",
    response_model=ExceptionCenterRead,
    status_code=status.HTTP_201_CREATED,
    summary="Reportar conflito de sincronização — nunca resolvido silenciosamente",
)
async def report_sync_conflict(
    payload: SyncConflictReportRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_documents_manage),
    db: AsyncSession = Depends(get_db),
) -> ExceptionCenterRead:
    service = LegalDocumentService(db)
    try:
        exception_item = await service.report_sync_conflict(
            workspace_id=workspace_id,
            provider=payload.provider,
            external_id=payload.external_id,
            matter_id=payload.matter_id,
            local_checksum=payload.local_checksum,
            remote_checksum=payload.remote_checksum,
            actor_user_id=auth.user_id,
        )
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc
    await db.commit()
    return ExceptionCenterRead.model_validate(exception_item)


@router.get(
    "/share-links/{share_link_id}/downloads",
    response_model=list[DocumentShareDownloadRead],
    summary="Trilha de downloads de um link de compartilhamento — quem/quando",
)
async def list_share_link_downloads(
    share_link_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_documents_view),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentShareDownloadRead]:
    service = LegalDocumentService(db)
    try:
        downloads = await service.list_share_downloads(
            workspace_id=workspace_id, share_link_id=share_link_id
        )
    except LegalDocumentError as exc:
        raise _http_error(exc) from exc
    return [DocumentShareDownloadRead.model_validate(item) for item in downloads]


@router.post(
    "/share-links/{share_link_id}/revoke",
    response_model=DocumentShareLinkRead,
    summary="Revogar um link de compartilhamento antes da expiração",
)
async def revoke_share_link(
    share_link_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_documents_manage),
    db: AsyncSession = Depends(get_db),
) -> DocumentShareLinkRead:
    service = LegalDocumentService(db)
    try:
        link = await service.revoke_share_link(
            workspace_id=workspace_id, share_link_id=share_link_id
        )
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc
    except LegalDocumentError as exc:
        raise _http_error(exc) from exc
    await db.commit()
    return DocumentShareLinkRead.model_validate(link)


@router.get(
    "/{asset_id}",
    response_model=DocumentAssetDetailRead,
    summary="Detalhe de um documento — classificação, OCR e estado de armazenamento",
)
async def get_document(
    asset_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_documents_view),
    db: AsyncSession = Depends(get_db),
) -> DocumentAssetDetailRead:
    service = LegalDocumentService(db)
    try:
        asset = await service.get_asset(workspace_id=workspace_id, document_asset_id=asset_id)
    except LegalDocumentError as exc:
        raise _http_error(exc) from exc
    return await _detail_read(service, asset)


@router.get(
    "/{asset_id}/references",
    response_model=list[DocumentAssetReferenceRead],
    summary="Versionar/listar cada ingestão (referência) de um documento",
)
async def list_document_references(
    asset_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_documents_view),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentAssetReferenceRead]:
    service = LegalDocumentService(db)
    try:
        references = await service.list_references(
            workspace_id=workspace_id, document_asset_id=asset_id
        )
    except LegalDocumentError as exc:
        raise _http_error(exc) from exc
    return [DocumentAssetReferenceRead.model_validate(item) for item in references]


@router.post(
    "/{asset_id}/share",
    response_model=DocumentShareLinkIssuedRead,
    status_code=status.HTTP_201_CREATED,
    summary="Compartilhar um documento com link de expiração obrigatória",
)
async def create_document_share_link(
    asset_id: UUID,
    payload: DocumentShareLinkCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_documents_manage),
    db: AsyncSession = Depends(get_db),
) -> DocumentShareLinkIssuedRead:
    service = LegalDocumentService(db)
    try:
        issued = await service.create_share_link(
            workspace_id=workspace_id,
            document_asset_id=asset_id,
            actor_user_id=auth.user_id,
            expires_at=payload.expires_at,
            watermark_enabled=payload.watermark_enabled,
            max_downloads=payload.max_downloads,
        )
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc
    except LegalDocumentError as exc:
        raise _http_error(exc) from exc
    await db.commit()
    return DocumentShareLinkIssuedRead(
        id=issued.share_link.id,
        document_asset_id=issued.share_link.document_asset_id,
        token=issued.raw_token,
        expires_at=issued.share_link.expires_at,
        watermark_enabled=issued.share_link.watermark_enabled,
        max_downloads=issued.share_link.max_downloads,
    )
