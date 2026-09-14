"""APIs autenticadas da Central de SAC do workspace."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
)
from app.models.support_ticket import SupportTicketStatus
from app.models.user import User
from app.schemas.support_ticket import (
    SupportAttachmentCreate,
    SupportAttachmentDownloadRead,
    SupportAttachmentRead,
    SupportAvailabilityStatusRead,
    SupportCollaboratorCreate,
    SupportEscalationStatusRead,
    SupportEventRead,
    SupportMessageCreate,
    SupportMessageRead,
    SupportRatingCreate,
    SupportTicketCreate,
    SupportTicketDetail,
    SupportTicketPage,
    SupportTicketRead,
    SupportUploadAuthorizationRead,
    SupportUserRead,
)
from app.services.local_object_storage_service import (
    LocalObjectConflictError,
    LocalObjectIntegrityError,
    LocalObjectNotFoundError,
    LocalObjectStorageError,
    LocalObjectUnavailableError,
    LocalObjectValidationError,
)
from app.services.support_ticket_service import (
    SupportTicketAccessError,
    SupportTicketConflictError,
    SupportTicketNotFoundError,
    SupportTicketService,
    SupportTicketValidationError,
)

router = APIRouter(prefix="/support", tags=["Central de SAC"])


def _parse_cursor(raw: str | None) -> tuple[datetime, UUID | None] | None:
    if raw is None:
        return None
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return (
            datetime.fromisoformat(str(decoded["timestamp"]).replace("Z", "+00:00")),
            UUID(str(decoded["id"])),
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        # Compatibilidade temporária com cursores ISO emitidos antes do cursor
        # opaco e collision-safe.
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")), None
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "SUPPORT-CURSOR-INVALID", "message": "Cursor inválido."},
            ) from error


def _error(error: Exception) -> HTTPException:
    if isinstance(error, SupportTicketNotFoundError):
        return HTTPException(
            status_code=404,
            detail={"code": "SUPPORT-NOT-FOUND", "message": str(error)},
        )
    if isinstance(error, SupportTicketAccessError):
        return HTTPException(
            status_code=403,
            detail={"code": "SUPPORT-ACCESS-DENIED", "message": str(error)},
        )
    if isinstance(error, SupportTicketConflictError):
        return HTTPException(
            status_code=409,
            detail={"code": "SUPPORT-CONFLICT", "message": str(error)},
        )
    if isinstance(error, LocalObjectNotFoundError):
        return HTTPException(
            status_code=404,
            detail={"code": "SUPPORT-ATTACHMENT-NOT-FOUND", "message": str(error)},
        )
    if isinstance(error, LocalObjectConflictError | LocalObjectIntegrityError):
        return HTTPException(
            status_code=409,
            detail={"code": "SUPPORT-ATTACHMENT-BLOCKED", "message": str(error)},
        )
    if isinstance(error, LocalObjectUnavailableError):
        return HTTPException(
            status_code=503,
            detail={"code": "SUPPORT-STORAGE-UNAVAILABLE", "message": str(error)},
        )
    if isinstance(error, LocalObjectValidationError):
        return HTTPException(
            status_code=422,
            detail={"code": "SUPPORT-ATTACHMENT-INVALID", "message": str(error)},
        )
    return HTTPException(status_code=422, detail={"code": "SUPPORT-INVALID", "message": str(error)})


@router.get(
    "/availability",
    response_model=SupportAvailabilityStatusRead,
    summary="Consultar estado sanitizado do atendimento ao vivo",
)
async def get_support_availability_status(
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(get_current_auth_context),
) -> SupportAvailabilityStatusRead:
    try:
        return SupportAvailabilityStatusRead.model_validate(
            await SupportTicketService(db).availability_status()
        )
    except SupportTicketValidationError as error:
        raise _error(error) from error


@router.get(
    "/escalation-status",
    response_model=SupportEscalationStatusRead,
    summary="Consultar disponibilidade sanitizada de 'Falar com um humano'",
)
async def get_support_escalation_status(
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(get_current_auth_context),
) -> SupportEscalationStatusRead:
    try:
        return SupportEscalationStatusRead.model_validate(
            await SupportTicketService(db).escalation_status()
        )
    except SupportTicketValidationError as error:
        raise _error(error) from error


def _ticket_read(
    ticket,
    reporter: User | None = None,
    assignee: User | None = None,
) -> SupportTicketRead:
    base = SupportTicketRead.model_validate(ticket)
    return base.model_copy(
        update={
            "reporter": (
                SupportUserRead.model_validate(reporter) if reporter is not None else None
            ),
            "assignee": (
                SupportUserRead.model_validate(assignee) if assignee is not None else None
            ),
        }
    )


async def _detail(
    service: SupportTicketService,
    ticket,
    *,
    include_internal: bool,
) -> SupportTicketDetail:
    data = await service.ticket_detail(ticket, include_internal=include_internal)
    base = _ticket_read(
        data["ticket"],
        reporter=data["reporter"],
        assignee=data["assignee"],
    )
    attachments = [
        SupportAttachmentRead(
            id=attachment.id,
            message_id=attachment.message_id,
            storage_object_version_id=attachment.storage_object_version_id,
            original_filename=attachment.original_filename,
            content_type=attachment.content_type,
            byte_size=storage.byte_size or attachment.byte_size,
            state=storage.state,
            av_status=storage.av_status,
            created_at=attachment.created_at,
        )
        for attachment, storage in data["attachments"]
    ]
    events = [
        SupportEventRead.model_validate(event).model_copy(
            update=(
                {}
                if include_internal
                else {"before_data": {}, "after_data": {}}
            )
        )
        for event in data["events"]
        if include_internal
        or event.event_type
        in {
            "created",
            "public_message_added",
            "ticket_updated",
            "closed_by_customer",
            "reopened",
            "rated",
        }
    ]
    return SupportTicketDetail(
        **base.model_dump(),
        messages=[SupportMessageRead.model_validate(item) for item in data["messages"]],
        attachments=attachments,
        events=events,
        collaborator_ids=data["collaborators"],
    )


@router.post(
    "/tickets",
    response_model=SupportTicketRead,
    status_code=status.HTTP_201_CREATED,
    summary="Abrir chamado sem sair da tela atual",
)
async def create_support_ticket(
    payload: SupportTicketCreate,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> SupportTicketRead:
    service = SupportTicketService(db)
    try:
        ticket, created = await service.create_ticket(
            workspace_id=workspace_id,
            user_id=auth.user_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
    except (
        SupportTicketConflictError,
        SupportTicketValidationError,
    ) as error:
        raise _error(error) from error
    if not created:
        response.status_code = status.HTTP_200_OK
    return _ticket_read(ticket)


@router.get(
    "/tickets",
    response_model=SupportTicketPage,
    summary="Listar chamados acessíveis ao usuário",
)
async def list_support_tickets(
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None),
    statuses: list[SupportTicketStatus] | None = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> SupportTicketPage:
    items, next_cursor = await SupportTicketService(db).list_for_user(
        workspace_id=workspace_id,
        user_id=auth.user_id,
        limit=limit,
        cursor=_parse_cursor(cursor),
        statuses=statuses,
    )
    return SupportTicketPage(
        items=[_ticket_read(ticket) for ticket in items],
        next_cursor=next_cursor,
    )


@router.get(
    "/tickets/{ticket_id}",
    response_model=SupportTicketDetail,
    summary="Consultar estado canônico do chamado",
)
async def get_support_ticket(
    ticket_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> SupportTicketDetail:
    service = SupportTicketService(db)
    try:
        ticket = await service.get_ticket(
            ticket_id,
            workspace_id=workspace_id,
            user_id=auth.user_id,
        )
        return await _detail(service, ticket, include_internal=False)
    except (
        SupportTicketNotFoundError,
        SupportTicketAccessError,
    ) as error:
        raise _error(error) from error


@router.post(
    "/tickets/{ticket_id}/messages",
    response_model=SupportMessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Responder ao chamado de forma idempotente",
)
async def add_support_message(
    ticket_id: UUID,
    payload: SupportMessageCreate,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> SupportMessageRead:
    service = SupportTicketService(db)
    try:
        ticket = await service.get_ticket(
            ticket_id,
            workspace_id=workspace_id,
            user_id=auth.user_id,
            for_update=True,
        )
        actor = await db.get(User, auth.user_id)
        message, created = await service.add_message(
            ticket,
            actor_user_id=auth.user_id,
            actor_label=actor.name if actor else "Usuário da conta",
            payload=payload,
            idempotency_key=idempotency_key,
            platform=False,
        )
    except (
        SupportTicketNotFoundError,
        SupportTicketAccessError,
        SupportTicketConflictError,
        SupportTicketValidationError,
    ) as error:
        raise _error(error) from error
    if not created:
        response.status_code = status.HTTP_200_OK
    return SupportMessageRead.model_validate(message)


@router.post(
    "/tickets/{ticket_id}/attachments",
    response_model=SupportUploadAuthorizationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Autorizar anexo privado em quarentena",
)
async def authorize_support_attachment(
    ticket_id: UUID,
    payload: SupportAttachmentCreate,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> SupportUploadAuthorizationRead:
    service = SupportTicketService(db)
    try:
        ticket = await service.get_ticket(
            ticket_id,
            workspace_id=workspace_id,
            user_id=auth.user_id,
        )
        attachment, authorization = await service.authorize_attachment(
            ticket,
            actor_user_id=auth.user_id,
            payload=payload.upload,
            message_id=payload.message_id,
            idempotency_key=idempotency_key,
        )
    except (
        SupportTicketNotFoundError,
        SupportTicketAccessError,
        SupportTicketValidationError,
    ) as error:
        raise _error(error) from error
    return SupportUploadAuthorizationRead(
        attachment_id=attachment.id,
        storage_object_version_id=authorization.storage_object.id,
        upload_url=(
            authorization.upload_url
            if hasattr(authorization, "upload_url")
            else authorization.ticket.url
        ),
        upload_required=getattr(authorization, "upload_required", True),
        upload_headers=getattr(
            authorization,
            "upload_headers",
            {"Content-Type": attachment.content_type},
        ),
        expires_at=getattr(authorization, "expires_at", None)
        or authorization.ticket.expires_at,
    )


@router.post(
    "/tickets/{ticket_id}/attachments/{attachment_id}/confirm",
    response_model=SupportAttachmentRead,
    summary="Confirmar upload privado e iniciar antivírus",
)
async def confirm_support_attachment(
    ticket_id: UUID,
    attachment_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> SupportAttachmentRead:
    service = SupportTicketService(db)
    try:
        ticket = await service.get_ticket(
            ticket_id,
            workspace_id=workspace_id,
            user_id=auth.user_id,
        )
        attachment, storage = await service.confirm_attachment_upload(
            ticket,
            attachment_id=attachment_id,
            actor_user_id=auth.user_id,
        )
    except (
        SupportTicketNotFoundError,
        SupportTicketAccessError,
        SupportTicketConflictError,
        SupportTicketValidationError,
        LocalObjectStorageError,
    ) as error:
        raise _error(error) from error
    return SupportAttachmentRead(
        id=attachment.id,
        message_id=attachment.message_id,
        storage_object_version_id=attachment.storage_object_version_id,
        original_filename=attachment.original_filename,
        content_type=attachment.content_type,
        byte_size=storage.byte_size or attachment.byte_size,
        state=storage.state,
        av_status=storage.av_status,
        created_at=attachment.created_at,
    )


@router.get(
    "/tickets/{ticket_id}/attachments/{attachment_id}/download-ticket",
    response_model=SupportAttachmentDownloadRead,
    summary="Autorizar download somente de anexo limpo",
)
async def create_support_attachment_download_ticket(
    ticket_id: UUID,
    attachment_id: UUID,
    expires_in: int = Query(default=300, ge=30, le=900),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> SupportAttachmentDownloadRead:
    service = SupportTicketService(db)
    try:
        ticket = await service.get_ticket(
            ticket_id,
            workspace_id=workspace_id,
            user_id=auth.user_id,
        )
        attachment, authorization = (
            await service.create_attachment_download_authorization(
                ticket,
                attachment_id=attachment_id,
                actor_user_id=auth.user_id,
                expires_in=expires_in,
            )
        )
    except (
        SupportTicketNotFoundError,
        SupportTicketAccessError,
        SupportTicketConflictError,
        SupportTicketValidationError,
        LocalObjectStorageError,
    ) as error:
        raise _error(error) from error
    return SupportAttachmentDownloadRead(
        attachment_id=attachment.id,
        download_url=getattr(authorization, "download_url", None)
        or authorization.ticket.url,
        expires_at=getattr(authorization, "expires_at", None)
        or authorization.ticket.expires_at,
    )


@router.post(
    "/tickets/{ticket_id}/collaborators",
    response_model=SupportTicketDetail,
    summary="Convidar colaborador da mesma conta",
)
async def add_support_collaborator(
    ticket_id: UUID,
    payload: SupportCollaboratorCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> SupportTicketDetail:
    service = SupportTicketService(db)
    try:
        ticket = await service.get_ticket(
            ticket_id,
            workspace_id=workspace_id,
            user_id=auth.user_id,
        )
        await service.add_collaborator(
            ticket,
            actor_user_id=auth.user_id,
            collaborator_user_id=payload.user_id,
        )
        return await _detail(service, ticket, include_internal=False)
    except (
        SupportTicketNotFoundError,
        SupportTicketAccessError,
        SupportTicketValidationError,
    ) as error:
        raise _error(error) from error


@router.post(
    "/tickets/{ticket_id}/close",
    response_model=SupportTicketRead,
    summary="Confirmar solução e encerrar chamado",
)
async def close_support_ticket(
    ticket_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> SupportTicketRead:
    service = SupportTicketService(db)
    try:
        ticket = await service.get_ticket(
            ticket_id,
            workspace_id=workspace_id,
            user_id=auth.user_id,
            for_update=True,
        )
        return _ticket_read(
            await service.close_by_customer(ticket, actor_user_id=auth.user_id)
        )
    except (
        SupportTicketNotFoundError,
        SupportTicketAccessError,
        SupportTicketConflictError,
    ) as error:
        raise _error(error) from error


@router.post(
    "/tickets/{ticket_id}/reopen",
    response_model=SupportTicketRead,
    summary="Reabrir chamado dentro do prazo",
)
async def reopen_support_ticket(
    ticket_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> SupportTicketRead:
    service = SupportTicketService(db)
    try:
        ticket = await service.get_ticket(
            ticket_id,
            workspace_id=workspace_id,
            user_id=auth.user_id,
            for_update=True,
        )
        return _ticket_read(await service.reopen(ticket, actor_user_id=auth.user_id))
    except (
        SupportTicketNotFoundError,
        SupportTicketAccessError,
        SupportTicketConflictError,
    ) as error:
        raise _error(error) from error


@router.post(
    "/tickets/{ticket_id}/rating",
    response_model=SupportTicketRead,
    summary="Avaliar atendimento",
)
async def rate_support_ticket(
    ticket_id: UUID,
    payload: SupportRatingCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> SupportTicketRead:
    service = SupportTicketService(db)
    try:
        ticket = await service.get_ticket(
            ticket_id,
            workspace_id=workspace_id,
            user_id=auth.user_id,
            for_update=True,
        )
        return _ticket_read(
            await service.rate(
                ticket,
                actor_user_id=auth.user_id,
                payload=payload,
            )
        )
    except (
        SupportTicketNotFoundError,
        SupportTicketAccessError,
        SupportTicketConflictError,
    ) as error:
        raise _error(error) from error
