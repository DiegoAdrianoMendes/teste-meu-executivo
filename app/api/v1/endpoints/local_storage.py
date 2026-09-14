"""Armazenamento de arquivos estritamente local para a fase de simulação."""
from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from typing import BinaryIO, NoReturn
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.api.v1.entitlement_errors import entitlement_http_exception
from app.core.config import settings
from app.core.deps import (
    AuthContext,
    can_view_sensitive_lead_data,
    get_current_auth_context,
    get_current_permissions,
    get_current_workspace_id,
    get_db,
    require_permission,
    require_workspace_admin,
)
from app.core.rbac import Permission
from app.core.security import verify_token
from app.models.storage_object import StorageAVStatus
from app.models.workspace_membership import WorkspaceRole
from app.schemas.storage_object import (
    StorageDownloadTicketRead,
    StorageObjectRead,
    StorageRetentionUpdate,
    StorageUploadRequest,
    StorageUploadTicketRead,
)
from app.services.entitlement_service import EntitlementError
from app.services.local_object_storage_service import (
    LocalObjectConflictError,
    LocalObjectIntegrityError,
    LocalObjectNotFoundError,
    LocalObjectRetentionError,
    LocalObjectStorageError,
    LocalObjectStorageService,
    LocalObjectTicketError,
    LocalObjectUnavailableError,
    inspect_payload_handle,
)
from app.services.s3_service import S3Service

router = APIRouter(prefix="/local-storage", tags=["Local Storage"])


def _has_permission(
    permissions: list[Permission],
    module: str,
    action: str,
) -> bool:
    return any(
        permission.module == module and permission.action == action
        for permission in permissions
    )


def _require_admin_option(context: AuthContext, requested: bool) -> None:
    if not requested:
        return
    if context.role not in {WorkspaceRole.workspace_owner, WorkspaceRole.workspace_admin}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "AUTH-007", "message": "Opção restrita à administração."},
        )


def _require_bound_object_access(
    storage_object,
    *,
    can_view_sensitive: bool,
    can_edit_sensitive: bool = False,
    mutation: bool = False,
) -> None:
    if storage_object.matter_id is None and storage_object.client_id is None:
        return
    if not can_view_sensitive:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "STORAGE-006", "message": "Objeto não encontrado."},
        )
    if mutation and not can_edit_sensitive:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "AUTH-012", "message": "Permissão insuficiente para esta ação."},
        )


def _request_ticket(request: Request, query_ticket: str | None) -> str:
    header_ticket = request.headers.get("x-storage-ticket")
    if header_ticket and query_ticket and header_ticket != query_ticket:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "STORAGE-012", "message": "Tickets de arquivo divergentes."},
        )
    ticket = header_ticket or query_ticket
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "STORAGE-005", "message": "Ticket de arquivo ausente."},
        )
    return ticket


async def _stage_limited_body(request: Request, *, workspace_id: UUID) -> str:
    declared_header = request.headers.get("content-length")
    if declared_header is not None:
        try:
            declared_size = int(declared_header)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "STORAGE-004", "message": "Content-Length inválido."},
            ) from exc
        if declared_size < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "STORAGE-004", "message": "Content-Length inválido."},
            )
        if declared_size > settings.LOCAL_STORAGE_MAX_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    staging_key = LocalObjectStorageService.staging_key(workspace_id)
    try:
        handle = await asyncio.to_thread(S3Service.create_local_object, staging_key)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "STORAGE-011", "message": "Staging seguro indisponível."},
        ) from exc
    received = 0
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > settings.LOCAL_STORAGE_MAX_BYTES:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
            await asyncio.to_thread(handle.write, chunk)
        await asyncio.to_thread(handle.flush)
        await asyncio.to_thread(os.fsync, handle.fileno())
        return staging_key
    except Exception:
        handle.close()
        try:
            await asyncio.to_thread(
                S3Service.delete_local_object,
                staging_key,
                missing_ok=True,
            )
        except Exception:
            pass
        raise
    finally:
        if not handle.closed:
            handle.close()


def _raise_storage_error(exc: LocalObjectStorageError) -> NoReturn:
    if isinstance(exc, LocalObjectTicketError):
        status_code = status.HTTP_401_UNAUTHORIZED
        code = "STORAGE-005"
    elif isinstance(exc, LocalObjectNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
        code = "STORAGE-006"
    elif isinstance(exc, LocalObjectRetentionError):
        status_code = status.HTTP_409_CONFLICT
        code = "STORAGE-007"
    elif isinstance(exc, LocalObjectConflictError):
        status_code = status.HTTP_409_CONFLICT
        code = "STORAGE-008"
    elif isinstance(exc, LocalObjectIntegrityError):
        status_code = status.HTTP_409_CONFLICT
        code = "STORAGE-009"
    elif isinstance(exc, LocalObjectUnavailableError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        code = "STORAGE-011"
    else:
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        code = "STORAGE-010"
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": str(exc)},
    ) from exc


def _stream_handle(handle: BinaryIO):
    try:
        while chunk := handle.read(1024 * 1024):
            yield chunk
    finally:
        handle.close()


def _content_disposition(filename: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(filename, safe='')}"


def _remove_temporary_export(handle: BinaryIO, storage_key: str) -> None:
    handle.close()
    try:
        S3Service.delete_local_object(storage_key, missing_ok=True)
    except (OSError, ValueError):
        pass


async def _receive_versioned_upload(
    *,
    request: Request,
    ticket: str,
    db: AsyncSession,
):
    service = LocalObjectStorageService(db)
    try:
        payload = await service.validate_upload_ticket(ticket)
        await service.preflight_upload_ticket(
            ticket,
            validated_payload=payload,
        )
        workspace_id = UUID(str(payload["workspace_id"]))
        staging_key = await _stage_limited_body(request, workspace_id=workspace_id)
        return await service.finalize_staged_upload(
            ticket=ticket,
            staging_key=staging_key,
            request_content_type=request.headers.get("content-type"),
            validated_payload=payload,
        )
    except LocalObjectStorageError as exc:
        _raise_storage_error(exc)
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc


def _public_asset_ticket(ticket: str, *, operation: str) -> dict | None:
    payload = verify_token(ticket)
    if not payload or payload.get("operation") != operation:
        return None
    key = str(payload.get("s3_key") or "")
    if payload.get("token_type") != "local_storage" or not key.startswith(
        "public-site/media/"
    ):
        return None
    try:
        S3Service.local_object_path(key)
    except ValueError:
        return None
    return payload


async def _receive_public_asset(
    *,
    request: Request,
    payload: dict,
) -> None:
    staging_key = await _stage_limited_body(request, workspace_id=UUID(int=0))
    try:
        expected_type = str(payload.get("content_type") or "").split(";", 1)[0].lower()
        actual_type = (request.headers.get("content-type") or "").split(";", 1)[0].lower()
        if not expected_type or actual_type != expected_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "STORAGE-003",
                    "message": "Tipo do arquivo diverge do upload autorizado.",
                },
            )
        with await asyncio.to_thread(S3Service.open_local_object, staging_key) as handle:
            inspection = await asyncio.to_thread(
                inspect_payload_handle,
                handle,
                expected_type,
            )
        if (
            inspection.av_status is not StorageAVStatus.simulated_unverified
            or inspection.av_reason is not None
            or inspection.mime_reason is not None
            or inspection.detected_content_type != expected_type
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "STORAGE-013",
                    "message": "Mídia pública reprovada pela triagem local.",
                },
            )
        await asyncio.to_thread(
            S3Service.replace_local_object,
            staging_key,
            str(payload["s3_key"]),
        )
    finally:
        try:
            await asyncio.to_thread(
                S3Service.delete_local_object,
                staging_key,
                missing_ok=True,
            )
        except Exception:
            pass


@router.put("/upload", status_code=status.HTTP_204_NO_CONTENT)
async def upload_local_file(
    request: Request,
    ticket: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    resolved_ticket = _request_ticket(request, ticket)
    public_payload = _public_asset_ticket(
        resolved_ticket,
        operation="public_asset_upload",
    )
    if public_payload is not None:
        await _receive_public_asset(request=request, payload=public_payload)
    else:
        await _receive_versioned_upload(request=request, ticket=resolved_ticket, db=db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/download")
async def download_local_file(
    request: Request,
    ticket: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    resolved_ticket = _request_ticket(request, ticket)
    public_payload = _public_asset_ticket(
        resolved_ticket,
        operation="public_asset_download",
    )
    if public_payload is not None:
        try:
            handle = await asyncio.to_thread(
                S3Service.open_local_object,
                str(public_payload["s3_key"]),
            )
        except (OSError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Arquivo não encontrado.",
            ) from None
        return StreamingResponse(
            _stream_handle(handle),
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    try:
        resolved = await LocalObjectStorageService(db).resolve_download(
            ticket=resolved_ticket
        )
    except LocalObjectStorageError as exc:
        _raise_storage_error(exc)
    storage_object = resolved.storage_object
    return StreamingResponse(
        _stream_handle(resolved.handle),
        media_type=storage_object.detected_content_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": _content_disposition(storage_object.original_filename),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.put(
    "/object-upload",
    response_model=StorageObjectRead,
    summary="Enviar bytes para uma versão autorizada e isolada",
)
async def upload_versioned_local_object(
    request: Request,
    ticket: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    resolved_ticket = _request_ticket(request, ticket)
    return await _receive_versioned_upload(
        request=request,
        ticket=resolved_ticket,
        db=db,
    )


@router.get(
    "/object-download",
    summary="Baixar uma versão local com ticket tenant-bound",
)
async def download_versioned_local_object(
    request: Request,
    ticket: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    resolved_ticket = _request_ticket(request, ticket)
    try:
        resolved = await LocalObjectStorageService(db).resolve_download(
            ticket=resolved_ticket
        )
    except LocalObjectStorageError as exc:
        _raise_storage_error(exc)
    storage_object = resolved.storage_object
    return StreamingResponse(
        _stream_handle(resolved.handle),
        media_type=storage_object.detected_content_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": _content_disposition(storage_object.original_filename),
            "ETag": f'"sha256:{storage_object.checksum_sha256}"',
            "X-Checksum-SHA256": storage_object.checksum_sha256 or "",
            "X-Content-Type-Options": "nosniff",
            "X-Object-Version": str(storage_object.version),
        },
    )


@router.get(
    "/media-download",
    summary="Entregar mídia com ticket tenant-bound e finalidade exclusiva",
)
async def download_versioned_local_media(
    request: Request,
    ticket: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    resolved_ticket = _request_ticket(request, ticket)
    try:
        resolved = await LocalObjectStorageService(db).resolve_download(
            ticket=resolved_ticket,
            purpose="media_delivery",
        )
    except LocalObjectStorageError as exc:
        _raise_storage_error(exc)
    storage_object = resolved.storage_object
    return StreamingResponse(
        _stream_handle(resolved.handle),
        media_type=storage_object.detected_content_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": _content_disposition(storage_object.original_filename),
            "ETag": f'"sha256:{storage_object.checksum_sha256}"',
            "X-Checksum-SHA256": storage_object.checksum_sha256 or "",
            "X-Content-Type-Options": "nosniff",
            "X-Object-Version": str(storage_object.version),
        },
    )


@router.post(
    "/objects",
    response_model=StorageUploadTicketRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar objeto e autorizar a primeira versão",
)
async def create_local_object(
    data: StorageUploadRequest,
    matter_id: UUID | None = Query(default=None, alias="matterId"),
    client_id: UUID | None = Query(default=None, alias="clientId"),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("kanban", "edit")),
    permissions: list[Permission] = Depends(get_current_permissions),
):
    bound_matter_id = getattr(data, "matter_id", None) or matter_id
    bound_client_id = getattr(data, "client_id", None) or client_id
    if (bound_matter_id or bound_client_id) and not _has_permission(
        permissions,
        "lead_data",
        "edit",
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "AUTH-012", "message": "Permissão insuficiente para esta ação."},
        )
    try:
        authorization = await LocalObjectStorageService(db).create_upload_authorization(
            workspace_id=workspace_id,
            actor_user_id=context.user_id,
            filename=data.filename,
            content_type=data.content_type,
            byte_size=data.byte_size,
            checksum_sha256=data.checksum_sha256,
            retention_until=data.retention_until,
            expires_in=data.expires_in,
            matter_id=bound_matter_id,
            client_id=bound_client_id,
        )
    except LocalObjectStorageError as exc:
        _raise_storage_error(exc)
    return StorageUploadTicketRead(
        object=authorization.storage_object,
        upload_url=authorization.ticket.url,
        expires_at=authorization.ticket.expires_at,
    )


@router.get(
    "/objects",
    response_model=list[StorageObjectRead],
    summary="Listar objetos locais do workspace",
)
async def list_local_objects(
    include_deleted: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_permission("kanban", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    _require_admin_option(context, include_deleted)
    return await LocalObjectStorageService(db).list_objects(
        workspace_id=workspace_id,
        include_deleted=include_deleted,
        include_sensitive=can_view_sensitive,
    )


@router.get(
    "/objects/{object_id}",
    response_model=StorageObjectRead,
    summary="Ler metadados da versão mais recente",
)
async def get_local_object(
    object_id: UUID,
    include_deleted: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_permission("kanban", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    _require_admin_option(context, include_deleted)
    try:
        storage_object = await LocalObjectStorageService(db).get_version(
            workspace_id=workspace_id,
            object_id=object_id,
            include_deleted=include_deleted,
        )
        _require_bound_object_access(
            storage_object,
            can_view_sensitive=can_view_sensitive,
        )
        return storage_object
    except LocalObjectStorageError as exc:
        _raise_storage_error(exc)


@router.get(
    "/objects/{object_id}/versions",
    response_model=list[StorageObjectRead],
    summary="Listar versões de um objeto",
)
async def list_local_object_versions(
    object_id: UUID,
    include_deleted: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_permission("kanban", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    _require_admin_option(context, include_deleted)
    try:
        versions = await LocalObjectStorageService(db).list_versions(
            workspace_id=workspace_id,
            object_id=object_id,
            include_deleted=include_deleted,
        )
        _require_bound_object_access(
            versions[0],
            can_view_sensitive=can_view_sensitive,
        )
        return versions
    except LocalObjectStorageError as exc:
        _raise_storage_error(exc)


@router.post(
    "/objects/{object_id}/versions",
    response_model=StorageUploadTicketRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar e autorizar uma nova versão imutável",
)
async def create_local_object_version(
    object_id: UUID,
    data: StorageUploadRequest,
    matter_id: UUID | None = Query(default=None, alias="matterId"),
    client_id: UUID | None = Query(default=None, alias="clientId"),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("kanban", "edit")),
    permissions: list[Permission] = Depends(get_current_permissions),
):
    try:
        service = LocalObjectStorageService(db)
        current = await service.get_version(
            workspace_id=workspace_id,
            object_id=object_id,
        )
        _require_bound_object_access(
            current,
            can_view_sensitive=_has_permission(permissions, "lead_data", "view"),
            can_edit_sensitive=_has_permission(permissions, "lead_data", "edit"),
            mutation=True,
        )
        authorization = await service.create_upload_authorization(
            workspace_id=workspace_id,
            actor_user_id=context.user_id,
            filename=data.filename,
            content_type=data.content_type,
            byte_size=data.byte_size,
            checksum_sha256=data.checksum_sha256,
            retention_until=data.retention_until,
            expires_in=data.expires_in,
            object_id=object_id,
            matter_id=getattr(data, "matter_id", None) or matter_id,
            client_id=getattr(data, "client_id", None) or client_id,
        )
    except LocalObjectStorageError as exc:
        _raise_storage_error(exc)
    return StorageUploadTicketRead(
        object=authorization.storage_object,
        upload_url=authorization.ticket.url,
        expires_at=authorization.ticket.expires_at,
    )


@router.post(
    "/objects/{object_id}/download-ticket",
    response_model=StorageDownloadTicketRead,
    summary="Autorizar download de uma versão disponível",
)
async def create_local_object_download_ticket(
    object_id: UUID,
    version: int | None = Query(default=None, ge=1),
    expires_in: int = Query(default=300, ge=30, le=900),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("kanban", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    try:
        service = LocalObjectStorageService(db)
        current = await service.get_version(
            workspace_id=workspace_id,
            object_id=object_id,
            version=version,
        )
        _require_bound_object_access(
            current,
            can_view_sensitive=can_view_sensitive,
        )
        authorization = await service.create_download_authorization(
            workspace_id=workspace_id,
            object_id=object_id,
            version=version,
            expires_in=expires_in,
        )
    except LocalObjectStorageError as exc:
        _raise_storage_error(exc)
    return StorageDownloadTicketRead(
        object_id=object_id,
        version=authorization.storage_object.version,
        download_url=authorization.ticket.url,
        expires_at=authorization.ticket.expires_at,
    )


@router.put(
    "/objects/{object_id}/retention",
    response_model=StorageObjectRead,
    summary="Estender retenção de todas as versões",
)
async def extend_local_object_retention(
    object_id: UUID,
    data: StorageRetentionUpdate,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
):
    try:
        return await LocalObjectStorageService(db).extend_retention(
            workspace_id=workspace_id,
            object_id=object_id,
            retention_until=data.retention_until,
        )
    except LocalObjectStorageError as exc:
        _raise_storage_error(exc)


@router.delete(
    "/objects/{object_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Excluir logicamente todas as versões",
)
async def delete_local_object(
    object_id: UUID,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_workspace_admin),
    permissions: list[Permission] = Depends(get_current_permissions),
):
    try:
        service = LocalObjectStorageService(db)
        current = await service.get_version(
            workspace_id=workspace_id,
            object_id=object_id,
            include_deleted=True,
        )
        _require_bound_object_access(
            current,
            can_view_sensitive=_has_permission(permissions, "lead_data", "view"),
            can_edit_sensitive=_has_permission(permissions, "lead_data", "edit"),
            mutation=True,
        )
        from app.services.document_service import DocumentService

        linked_document = await DocumentService(db).delete(
            object_id,
            workspace_id,
            actor_user_id=context.user_id,
        )
        if not linked_document:
            await service.soft_delete(
                workspace_id=workspace_id,
                object_id=object_id,
                actor_user_id=context.user_id,
            )
    except LocalObjectStorageError as exc:
        _raise_storage_error(exc)
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/objects/{object_id}/restore",
    response_model=StorageObjectRead,
    summary="Restaurar objeto excluído logicamente",
)
async def restore_local_object(
    object_id: UUID,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_workspace_admin),
    permissions: list[Permission] = Depends(get_current_permissions),
):
    try:
        service = LocalObjectStorageService(db)
        current = await service.get_version(
            workspace_id=workspace_id,
            object_id=object_id,
            include_deleted=True,
        )
        _require_bound_object_access(
            current,
            can_view_sensitive=_has_permission(permissions, "lead_data", "view"),
            can_edit_sensitive=_has_permission(permissions, "lead_data", "edit"),
            mutation=True,
        )
        from app.services.document_service import DocumentService

        linked_document = await DocumentService(db).restore(
            object_id,
            workspace_id,
            actor_user_id=context.user_id,
        )
        if linked_document is not None:
            return await service.get_version(
                workspace_id=workspace_id,
                object_id=object_id,
            )
        return await service.restore(
            workspace_id=workspace_id,
            object_id=object_id,
            actor_user_id=context.user_id,
        )
    except LocalObjectStorageError as exc:
        _raise_storage_error(exc)
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc


@router.get(
    "/objects/{object_id}/export",
    summary="Exportar versões e manifesto para ZIP local temporário",
)
async def export_local_object(
    object_id: UUID,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("lista", "export")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    try:
        service = LocalObjectStorageService(db)
        current = await service.get_version(
            workspace_id=workspace_id,
            object_id=object_id,
        )
        _require_bound_object_access(
            current,
            can_view_sensitive=can_view_sensitive,
        )
        exported = await service.export_object(
            workspace_id=workspace_id,
            object_id=object_id,
        )
    except LocalObjectStorageError as exc:
        _raise_storage_error(exc)
    return StreamingResponse(
        _stream_handle(exported.handle),
        media_type="application/zip",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Object-Version-Count": str(exported.version_count),
            "Content-Disposition": _content_disposition(
                f"storage-object-{object_id}.zip"
            ),
        },
        background=BackgroundTask(
            _remove_temporary_export,
            exported.handle,
            exported.storage_key,
        ),
    )


@router.post(
    "/reconcile",
    summary="Reconciliar transições locais interrompidas",
)
async def reconcile_local_storage(
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
):
    try:
        report = await LocalObjectStorageService(db).reconcile_workspace(
            workspace_id=workspace_id
        )
    except LocalObjectStorageError as exc:
        _raise_storage_error(exc)
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc
    return asdict(report)
