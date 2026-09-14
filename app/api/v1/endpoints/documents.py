"""
Endpoint: Documents
Upload e gerenciamento de documentos via S3 (presigned URLs).
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.entitlement_errors import entitlement_http_exception
from app.core.deps import (
    can_view_sensitive_lead_data,
    get_current_user_id,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.lead_privacy import mask_document
from app.schemas.document import (
    DocumentCreate,
    DocumentDownloadUrlResponse,
    DocumentRead,
    PresignedUrlResponse,
)
from app.services.document_service import DocumentService
from app.services.entitlement_service import EntitlementError

router = APIRouter(tags=["Documentos"])


@router.get("/matters/{matter_id}/documents", response_model=list[DocumentRead])
async def list_documents(
    matter_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    __: object = Depends(require_permission("kanban", "view")),
):
    service = DocumentService(db)
    documents = await service.get_by_matter(matter_id, workspace_id)
    return documents if can_view_sensitive else [mask_document(item) for item in documents]


@router.post(
    "/matters/{matter_id}/documents",
    response_model=PresignedUrlResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Iniciar upload de documento",
)
async def create_document(
    matter_id: UUID,
    data: DocumentCreate,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("kanban", "edit")),
    __: object = Depends(require_permission("lead_data", "edit")),
):
    """
    Cria o registro do documento e retorna uma URL pré-assinada do S3.
    O frontend deve fazer PUT diretamente nessa URL para enviar o arquivo.
    """
    service = DocumentService(db)
    try:
        return await service.create_with_presigned_url(
            matter_id,
            data,
            workspace_id=workspace_id,
            uploaded_by=current_user_id,
            idempotency_key=idempotency_key,
        )
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc


@router.post(
    "/documents/{document_id}/confirm",
    response_model=DocumentRead,
    summary="Confirmar upload",
)
async def confirm_upload(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    __: object = Depends(require_permission("kanban", "edit")),
):
    """Chamado pelo frontend após o upload direto ao S3 ser concluído com sucesso."""
    service = DocumentService(db)
    try:
        doc = await service.confirm_upload(
            document_id,
            workspace_id,
            actor_user_id=current_user_id,
        )
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Documento não encontrado.",
        )
    return doc if can_view_sensitive else mask_document(doc)


@router.get(
    "/documents/{document_id}/download-url",
    response_model=DocumentDownloadUrlResponse,
    summary="Gerar link temporário para download autorizado",
)
async def create_document_download_url(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("kanban", "view")),
    __: object = Depends(require_permission("lead_data", "view")),
):
    try:
        return await DocumentService(db).create_download_url(
            document_id,
            workspace_id,
            actor_user_id=current_user_id,
        )
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("kanban", "delete")),
    __: object = Depends(require_permission("lead_data", "edit")),
):
    service = DocumentService(db)
    try:
        deleted = await service.delete(
            document_id,
            workspace_id,
            actor_user_id=current_user_id,
        )
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Documento não encontrado.",
        )


@router.post(
    "/documents/{document_id}/restore",
    response_model=DocumentRead,
    summary="Restaurar vínculo documental e objeto versionado",
)
async def restore_document(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("kanban", "edit")),
    __: object = Depends(require_permission("lead_data", "edit")),
):
    try:
        document = await DocumentService(db).restore(
            document_id,
            workspace_id,
            actor_user_id=current_user_id,
        )
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Documento não encontrado.",
        )
    return document
