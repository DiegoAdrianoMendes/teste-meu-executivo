"""Operação do SAC pela equipe interna do SaaS."""

from __future__ import annotations

import csv
import io
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.support_tickets import _detail, _error, _parse_cursor, _ticket_read
from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_access import (
    require_platform_root,
    require_platform_support_permission,
)
from app.core.platform_audit import (
    record_canonical_platform_mutation,
    snapshot_platform_config_document,
    snapshot_platform_support_saved_filter,
    snapshot_platform_support_ticket,
)
from app.core.security import create_platform_websocket_ticket
from app.models.support_ticket import (
    SupportSavedFilter,
    SupportTicketQueue,
    SupportTicketSeverity,
    SupportTicketStatus,
    SupportTicketType,
)
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.support_ticket import (
    SupportAvailabilitySettingsRead,
    SupportAvailabilitySettingsUpdate,
    SupportAvailabilityStatusRead,
    SupportEscalationSettingsRead,
    SupportEscalationSettingsUpdate,
    SupportMessageCreate,
    SupportMessageRead,
    PlatformSupportTicketCreate,
    SupportMetricsRead,
    SupportSavedFilterCreate,
    SupportSavedFilterRead,
    SupportTicketCreate,
    SupportTicketDetail,
    SupportTicketPage,
    SupportTicketRead,
    SupportTicketUpdate,
)
from app.schemas.workspace import WebSocketTicketRead
from app.services.support_ticket_service import (
    SupportTicketAccessError,
    SupportTicketConflictError,
    SupportTicketNotFoundError,
    SupportTicketService,
    SupportTicketValidationError,
)

router = APIRouter(prefix="/platform/support", tags=["Platform SAC"])

_support_view = require_platform_support_permission("view")
_support_respond = require_platform_support_permission("respond")
_support_triage = require_platform_support_permission("triage")
_support_manage = require_platform_support_permission("manage")
_support_export = require_platform_support_permission("export")


async def _platform_ticket_read(
    db: AsyncSession,
    ticket,
    *,
    workspace_name: str | None = None,
) -> SupportTicketRead:
    if workspace_name is None:
        workspace_name = await db.scalar(
            select(Workspace.name).where(Workspace.id == ticket.workspace_id)
        )
    return _ticket_read(ticket).model_copy(update={"workspace_name": workspace_name})


def _csv_value(value: object | None) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{text}"
    return text


def require_platform_support_reason(
    reason: str = Header(..., alias="X-Operation-Reason", max_length=3000),
) -> str:
    normalized = unquote(reason).strip()
    if not 8 <= len(normalized) <= 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="O motivo deve conter de 8 a 500 caracteres não vazios.",
        )
    return normalized


@router.get(
    "/availability",
    response_model=SupportAvailabilityStatusRead,
    summary="Consultar estado sanitizado do atendimento ao vivo",
)
async def get_support_availability_status(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_support_view),
) -> SupportAvailabilityStatusRead:
    try:
        return SupportAvailabilityStatusRead.model_validate(
            await SupportTicketService(db).availability_status()
        )
    except SupportTicketValidationError as error:
        raise _error(error) from error


@router.get(
    "/availability-settings",
    response_model=SupportAvailabilitySettingsRead,
    summary="Consultar horário persistido do atendimento ao vivo",
)
async def get_platform_support_availability_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_support_view),
) -> SupportAvailabilitySettingsRead:
    try:
        return SupportAvailabilitySettingsRead.model_validate(
            await SupportTicketService(db).availability_settings()
        )
    except SupportTicketValidationError as error:
        raise _error(error) from error


@router.put(
    "/availability-settings",
    response_model=SupportAvailabilitySettingsRead,
    summary="Configurar horário e mensagem do atendimento ao vivo",
)
async def update_platform_support_availability_settings(
    payload: SupportAvailabilitySettingsUpdate,
    request: Request,
    reason: str = Depends(require_platform_support_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_support_manage),
) -> SupportAvailabilitySettingsRead:
    try:
        before = await snapshot_platform_config_document(
            db, "support.availability", for_update=True
        )
        result = SupportAvailabilitySettingsRead.model_validate(
            await SupportTicketService(db).update_availability_settings(
                payload=payload,
                actor_user_id=actor.id,
            )
        )
        after = await snapshot_platform_config_document(db, "support.availability")
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=before, after_snapshot=after,
            module="support", action="update_platform_support_availability_settings",
            reason=reason,
        )
        return result
    except (
        SupportTicketConflictError,
        SupportTicketValidationError,
    ) as error:
        raise _error(error) from error


@router.get(
    "/escalation-settings",
    response_model=SupportEscalationSettingsRead,
    summary="Consultar configuração de 'Falar com um humano'",
)
async def get_platform_support_escalation_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
) -> SupportEscalationSettingsRead:
    try:
        return SupportEscalationSettingsRead.model_validate(
            await SupportTicketService(db).escalation_settings()
        )
    except SupportTicketValidationError as error:
        raise _error(error) from error


@router.put(
    "/escalation-settings",
    response_model=SupportEscalationSettingsRead,
    summary="Configurar agendamento, WhatsApp e horário de 'Falar com um humano'",
)
async def update_platform_support_escalation_settings(
    payload: SupportEscalationSettingsUpdate,
    request: Request,
    reason: str = Depends(require_platform_support_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
) -> SupportEscalationSettingsRead:
    try:
        before = await snapshot_platform_config_document(
            db, "support.escalation", for_update=True
        )
        result = SupportEscalationSettingsRead.model_validate(
            await SupportTicketService(db).update_escalation_settings(
                payload=payload,
                actor_user_id=actor.id,
            )
        )
        after = await snapshot_platform_config_document(db, "support.escalation")
        await record_canonical_platform_mutation(
            request, context, db,
            before_snapshot=before, after_snapshot=after,
            module="support", action="update_platform_support_escalation_settings",
            reason=reason,
        )
        return result
    except (
        SupportTicketConflictError,
        SupportTicketValidationError,
    ) as error:
        raise _error(error) from error


@router.post(
    "/ws-ticket",
    response_model=WebSocketTicketRead,
    summary="Emitir ticket curto do canal interno de SAC",
)
async def create_platform_support_ws_ticket(
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_support_view),
) -> WebSocketTicketRead:
    if context.session_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AUTH-009",
                "message": "Família de sessão revogada ou ausente.",
            },
        )
    result = WebSocketTicketRead(
        ticket=create_platform_websocket_ticket(
            context.user_id,
            session_id=context.session_id,
            auth_epoch=context.auth_epoch,
            session_issued_at_ns=context.session_issued_at_ns,
        ),
        expires_in=30,
    )
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=None,
        after_snapshot=(
            "support_websocket_ticket", str(context.session_id),
            {"sessionId": str(context.session_id), "expiresIn": 30},
        ),
        module="support", action="create_platform_support_ws_ticket",
        reason="Emissão automática do ticket WebSocket do SAC.",
        reason_source="system",
    )
    return result


@router.get(
    "/tickets",
    response_model=SupportTicketPage,
    summary="Listar fila unificada de SAC",
)
async def list_platform_support_tickets(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None, alias="workspaceId"),
    statuses: list[SupportTicketStatus] | None = Query(default=None),
    queues: list[SupportTicketQueue] | None = Query(default=None),
    severities: list[SupportTicketSeverity] | None = Query(default=None),
    ticket_types: list[SupportTicketType] | None = Query(default=None, alias="ticketTypes"),
    assigned_to_user_id: UUID | None = Query(default=None, alias="assignedToUserId"),
    overdue: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=240),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_support_view),
) -> SupportTicketPage:
    items, next_cursor = await SupportTicketService(db).list_platform(
        limit=limit,
        cursor=_parse_cursor(cursor),
        workspace_id=workspace_id,
        statuses=statuses,
        queues=queues,
        severities=severities,
        ticket_types=ticket_types,
        assigned_to_user_id=assigned_to_user_id,
        overdue=overdue,
        search=search,
    )
    workspace_ids = {ticket.workspace_id for ticket in items}
    workspace_names = dict((await db.execute(
        select(Workspace.id, Workspace.name).where(Workspace.id.in_(workspace_ids))
    )).all()) if workspace_ids else {}
    return SupportTicketPage(
        items=[
            await _platform_ticket_read(
                db, ticket, workspace_name=workspace_names.get(ticket.workspace_id)
            )
            for ticket in items
        ],
        next_cursor=next_cursor,
    )


@router.post(
    "/tickets",
    response_model=SupportTicketRead,
    status_code=status.HTTP_201_CREATED,
    summary="Abrir chamado em nome de um usuário da conta",
)
async def create_platform_support_ticket(
    payload: PlatformSupportTicketCreate,
    response: Response,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    reason: str = Depends(require_platform_support_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_support_respond),
) -> SupportTicketRead:
    membership = await db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == payload.workspace_id,
            WorkspaceMembership.user_id == payload.reporter_user_id,
            WorkspaceMembership.status == MembershipStatus.active,
        )
    )
    reporter = await db.get(User, payload.reporter_user_id)
    if membership is None or reporter is None or not reporter.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "SUPPORT-REPORTER-INVALID",
                "message": "Selecione um usuário ativo vinculado à conta informada.",
            },
        )
    service = SupportTicketService(db)
    try:
        ticket, created = await service.create_ticket(
            workspace_id=payload.workspace_id,
            user_id=payload.reporter_user_id,
            payload=SupportTicketCreate.model_validate(
                payload.model_dump(exclude={"workspace_id", "reporter_user_id", "queue"})
            ),
            idempotency_key=idempotency_key,
            queue_override=payload.queue,
            event_actor_user_id=actor.id,
        )
    except (
        SupportTicketConflictError,
        SupportTicketValidationError,
    ) as error:
        raise _error(error) from error
    if not created:
        response.status_code = status.HTTP_200_OK
    after = await snapshot_platform_support_ticket(db, ticket.id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=None if created else after, after_snapshot=after,
        module="support", action="create_platform_support_ticket",
        reason=reason,
    )
    return await _platform_ticket_read(db, ticket)


@router.get(
    "/tickets-export.csv",
    response_class=Response,
    summary="Exportar a fila filtrada do SAC em CSV",
    deprecated=True,
)
async def export_platform_support_tickets(
    workspace_id: UUID | None = Query(default=None, alias="workspaceId"),
    statuses: list[SupportTicketStatus] | None = Query(default=None),
    queues: list[SupportTicketQueue] | None = Query(default=None),
    severities: list[SupportTicketSeverity] | None = Query(default=None),
    ticket_types: list[SupportTicketType] | None = Query(default=None, alias="ticketTypes"),
    assigned_to_user_id: UUID | None = Query(default=None, alias="assignedToUserId"),
    overdue: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=240),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_support_export),
) -> Response:
    tickets, truncated = await SupportTicketService(db).export_platform(
        workspace_id=workspace_id,
        statuses=statuses,
        queues=queues,
        severities=severities,
        ticket_types=ticket_types,
        assigned_to_user_id=assigned_to_user_id,
        overdue=overdue,
        search=search,
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        (
            "Número",
            "Tipo",
            "Assunto",
            "Situação",
            "Severidade",
            "Prioridade",
            "Fila",
            "Conta",
            "Responsável",
            "Criado em",
            "Primeira resposta em",
            "Resolvido em",
        )
    )
    for ticket in tickets:
        writer.writerow(
            (
                _csv_value(ticket.public_number),
                _csv_value(ticket.ticket_type.value),
                _csv_value(ticket.subject),
                _csv_value(ticket.status.value),
                _csv_value(ticket.severity.value),
                _csv_value(ticket.priority.value),
                _csv_value(ticket.queue.value),
                _csv_value(ticket.workspace_id),
                _csv_value(ticket.assigned_to_user_id),
                _csv_value(ticket.created_at.isoformat()),
                _csv_value(
                    ticket.first_responded_at.isoformat()
                    if ticket.first_responded_at
                    else None
                ),
                _csv_value(ticket.resolved_at.isoformat() if ticket.resolved_at else None),
            )
        )
    return Response(
        content=f"\ufeff{output.getvalue()}",
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="sac-tickets.csv"',
            "X-Result-Truncated": "true" if truncated else "false",
        },
    )


@router.get(
    "/tickets/{ticket_id}",
    response_model=SupportTicketDetail,
    summary="Abrir diagnóstico completo e trilha de auditoria",
)
async def get_platform_support_ticket(
    ticket_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_support_view),
) -> SupportTicketDetail:
    service = SupportTicketService(db)
    try:
        ticket = await service.get_ticket(ticket_id, platform=True)
        detail = await _detail(service, ticket, include_internal=True)
        workspace_name = await db.scalar(
            select(Workspace.name).where(Workspace.id == ticket.workspace_id)
        )
        return detail.model_copy(update={"workspace_name": workspace_name})
    except SupportTicketNotFoundError as error:
        raise _error(error) from error


@router.patch(
    "/tickets/{ticket_id}",
    response_model=SupportTicketRead,
    summary="Triar, atribuir ou alterar o chamado com versão otimista",
)
async def update_platform_support_ticket(
    ticket_id: UUID,
    payload: SupportTicketUpdate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_support_triage),
) -> SupportTicketRead:
    service = SupportTicketService(db)
    try:
        ticket = await service.get_ticket(ticket_id, platform=True, for_update=True)
        updated = await service.update_ticket(
            ticket,
            actor_user_id=actor.id,
            payload=payload,
        )
        await db.refresh(updated)
        return await _platform_ticket_read(db, updated)
    except (
        SupportTicketNotFoundError,
        SupportTicketAccessError,
        SupportTicketConflictError,
        SupportTicketValidationError,
    ) as error:
        raise _error(error) from error


@router.post(
    "/tickets/{ticket_id}/messages",
    response_model=SupportMessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Responder publicamente ou registrar nota interna",
)
async def add_platform_support_message(
    ticket_id: UUID,
    payload: SupportMessageCreate,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_support_respond),
) -> SupportMessageRead:
    service = SupportTicketService(db)
    try:
        ticket = await service.get_ticket(ticket_id, platform=True, for_update=True)
        message, created = await service.add_message(
            ticket,
            actor_user_id=actor.id,
            actor_label=actor.name,
            payload=payload,
            idempotency_key=idempotency_key,
            platform=True,
        )
    except (
        SupportTicketNotFoundError,
        SupportTicketConflictError,
    ) as error:
        raise _error(error) from error
    if not created:
        response.status_code = status.HTTP_200_OK
    return SupportMessageRead.model_validate(message)


@router.get(
    "/metrics",
    response_model=SupportMetricsRead,
    summary="Indicadores atuais e comparativo anterior do SAC",
)
async def get_platform_support_metrics(
    period_days: int = Query(default=30, ge=1, le=365, alias="periodDays"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_support_view),
) -> SupportMetricsRead:
    return SupportMetricsRead.model_validate(
        await SupportTicketService(db).metrics(period_days=period_days)
    )


@router.get(
    "/saved-filters",
    response_model=list[SupportSavedFilterRead],
    summary="Listar filtros pessoais salvos",
)
async def list_platform_support_saved_filters(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_support_view),
) -> list[SupportSavedFilterRead]:
    return [
        SupportSavedFilterRead.model_validate(item)
        for item in await SupportTicketService(db).saved_filters(actor.id)
    ]


@router.post(
    "/saved-filters",
    response_model=SupportSavedFilterRead,
    status_code=status.HTTP_201_CREATED,
    summary="Salvar filtro pessoal",
)
async def save_platform_support_filter(
    payload: SupportSavedFilterCreate,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_support_view),
) -> SupportSavedFilterRead:
    existing = await db.scalar(
        select(SupportSavedFilter).where(
            SupportSavedFilter.owner_user_id == actor.id,
            func.lower(SupportSavedFilter.name) == payload.name.strip().lower(),
        )
    )
    before = (
        await snapshot_platform_support_saved_filter(db, existing.id)
        if existing is not None
        else None
    )
    saved = await SupportTicketService(db).save_filter(
        user_id=actor.id,
        name=payload.name,
        filters=payload.filters,
        is_default=payload.is_default,
    )
    after = await snapshot_platform_support_saved_filter(db, saved.id)
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="support", action="save_platform_support_filter",
        reason="Filtro pessoal do SAC salvo pelo operador.",
        reason_source="system",
    )
    return SupportSavedFilterRead.model_validate(saved)


@router.post(
    "/migrate-technical-requests",
    response_model=dict[str, int],
    summary="Incorporar solicitações técnicas antigas sem duplicar registros",
)
async def migrate_technical_requests(
    request: Request,
    workspace_id: UUID | None = Query(default=None, alias="workspaceId"),
    reason: str = Depends(require_platform_support_reason),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_support_manage),
) -> dict[str, int]:
    migrated = await SupportTicketService(db).migrate_legacy_requests(
        workspace_id=workspace_id
    )
    before = (
        "support_technical_migration",
        str(workspace_id or "platform"),
        {"workspaceId": str(workspace_id) if workspace_id else None},
    )
    after = (
        "support_technical_migration",
        str(workspace_id or "platform"),
        {
            "workspaceId": str(workspace_id) if workspace_id else None,
            "migrated": migrated,
        },
    )
    await record_canonical_platform_mutation(
        request, context, db,
        before_snapshot=before, after_snapshot=after,
        module="support", action="migrate_technical_requests", reason=reason,
    )
    return {"migrated": migrated}
