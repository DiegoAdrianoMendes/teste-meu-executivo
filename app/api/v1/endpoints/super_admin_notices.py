"""Endpoints isolados da Central de Avisos do Super Admin."""
from __future__ import annotations

import hashlib
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_access import require_platform_permission
from app.core.platform_audit import record_canonical_platform_mutation
from app.models.super_admin_notice import SuperAdminNoticeStatus
from app.models.user import User
from app.schemas.super_admin_notice import (
    SuperAdminNoticeAuditEventPageRead,
    SuperAdminNoticeAuditEventRead,
    SuperAdminNoticeCreate,
    SuperAdminNoticeDispatchRead,
    SuperAdminNoticeInboxItemRead,
    SuperAdminNoticeInboxRead,
    SuperAdminNoticeListRead,
    SuperAdminNoticePublicationRead,
    SuperAdminNoticeRead,
    SuperAdminNoticeReceiptListRead,
    SuperAdminNoticeRecipientPreviewRead,
    SuperAdminNoticeSchedule,
    SuperAdminNoticeUpdate,
)
from app.services.super_admin_notice_service import (
    SuperAdminNoticeService,
    SuperAdminNoticeServiceError,
)

router = APIRouter(tags=["Central de Avisos"])

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=200,
        description="Chave unica da operacao por 24 horas.",
    ),
]


require_notice_manager = require_platform_permission("announcements", "manage")


def _notice_snapshot(notice: SuperAdminNoticeRead) -> tuple[str, str, dict]:
    audience = notice.audience
    rule = audience.rule
    return (
        "platform_notice",
        str(notice.id),
        {
            "title": notice.title,
            "bodySha256": hashlib.sha256(notice.body.encode("utf-8")).hexdigest(),
            "bodyLength": len(notice.body),
            "status": notice.status.value,
            "audienceKind": audience.kind.value,
            "workspaceCount": len(audience.workspace_ids),
            "userCount": len(audience.user_ids),
            "ruleField": rule.field.value if rule else None,
            "ruleValueCount": len(rule.values) if rule else 0,
            "channels": [channel.value for channel in notice.channels],
            "requiresAcknowledgement": notice.requires_acknowledgement,
            "scheduledAt": notice.scheduled_at,
            "publishedAt": notice.published_at,
            "cancelledAt": notice.cancelled_at,
            "version": notice.version,
        },
    )


async def _audit_notice(
    request: Request,
    context: AuthContext,
    db: AsyncSession,
    *,
    before_snapshot: tuple[str, str, dict] | None,
    after_snapshot: tuple[str, str, dict] | None,
    action: str,
    reason: str,
) -> None:
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        module="announcements",
        action=action,
        reason=reason,
        reason_source="system",
    )


def _raise_service_error(error: SuperAdminNoticeServiceError) -> NoReturn:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


@router.get(
    "/notices/inbox",
    response_model=SuperAdminNoticeInboxRead,
    summary="Listar avisos in-app do usuario autenticado",
)
async def notice_inbox(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    context: AuthContext = Depends(get_current_auth_context),
) -> SuperAdminNoticeInboxRead:
    return await SuperAdminNoticeService(db).inbox(
        user_id=context.user_id,
        workspace_id=context.workspace_id,
        offset=offset,
        limit=limit,
    )


@router.put(
    "/notices/inbox/{receipt_id}/read",
    response_model=SuperAdminNoticeInboxItemRead,
    summary="Marcar recibo in-app como lido",
)
async def read_notice(
    receipt_id: UUID,
    db: AsyncSession = Depends(get_db),
    context: AuthContext = Depends(get_current_auth_context),
) -> SuperAdminNoticeInboxItemRead:
    try:
        return await SuperAdminNoticeService(db).mark_read(
            receipt_id,
            user_id=context.user_id,
            workspace_id=context.workspace_id,
        )
    except SuperAdminNoticeServiceError as error:
        _raise_service_error(error)


@router.put(
    "/notices/inbox/{receipt_id}/acknowledgement",
    response_model=SuperAdminNoticeInboxItemRead,
    summary="Registrar acknowledgement do aviso in-app",
)
async def acknowledge_notice(
    receipt_id: UUID,
    db: AsyncSession = Depends(get_db),
    context: AuthContext = Depends(get_current_auth_context),
) -> SuperAdminNoticeInboxItemRead:
    try:
        return await SuperAdminNoticeService(db).acknowledge(
            receipt_id,
            user_id=context.user_id,
            workspace_id=context.workspace_id,
        )
    except SuperAdminNoticeServiceError as error:
        _raise_service_error(error)


@router.post(
    "/platform/notices",
    response_model=SuperAdminNoticeRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar aviso em rascunho",
)
async def create_notice(
    payload: SuperAdminNoticeCreate,
    request: Request,
    idempotency_key: IdempotencyKey,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_notice_manager),
    context: AuthContext = Depends(get_current_auth_context),
) -> SuperAdminNoticeRead:
    try:
        service = SuperAdminNoticeService(db)
        notice = await service.create(
            payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        if not service.operation_was_replayed:
            await _audit_notice(
                request,
                context,
                db,
                before_snapshot=None,
                after_snapshot=_notice_snapshot(notice),
                action="notice.create",
                reason="Rascunho de comunicado criado.",
            )
        return notice
    except SuperAdminNoticeServiceError as error:
        _raise_service_error(error)


@router.get(
    "/platform/notices",
    response_model=SuperAdminNoticeListRead,
    summary="Listar avisos administrados pela plataforma",
)
async def list_notices(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    notice_status: SuperAdminNoticeStatus | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_notice_manager),
) -> SuperAdminNoticeListRead:
    return await SuperAdminNoticeService(db).list(
        offset=offset,
        limit=limit,
        notice_status=notice_status,
    )


@router.post(
    "/platform/notices/dispatch-due",
    response_model=SuperAdminNoticeDispatchRead,
    summary="Publicar avisos agendados vencidos",
)
async def dispatch_due_notices(
    request: Request,
    idempotency_key: IdempotencyKey,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_notice_manager),
    context: AuthContext = Depends(get_current_auth_context),
) -> SuperAdminNoticeDispatchRead:
    try:
        service = SuperAdminNoticeService(db)
        result = await service.dispatch_due(
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
            limit=limit,
        )
        publication_ids = [str(item.notice.id) for item in result.publications]
        result_snapshot = (
            "platform_notice_dispatch",
            idempotency_key,
            {
                "processedCount": result.processed_count,
                "noticeIds": publication_ids[:200],
                "noticeIdsTruncated": len(publication_ids) > 200,
            },
        )
        if not service.operation_was_replayed:
            await _audit_notice(
                request,
                context,
                db,
                before_snapshot=(
                    "platform_notice_dispatch",
                    idempotency_key,
                    {"processedCount": 0},
                ),
                after_snapshot=result_snapshot,
                action="notice.dispatch_due",
                reason="Despacho local dos comunicados agendados vencidos.",
            )
        return result
    except SuperAdminNoticeServiceError as error:
        _raise_service_error(error)


@router.get(
    "/platform/notices/{notice_id}",
    response_model=SuperAdminNoticeRead,
    summary="Consultar aviso",
)
async def get_notice(
    notice_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_notice_manager),
) -> SuperAdminNoticeRead:
    try:
        return await SuperAdminNoticeService(db).get(notice_id)
    except SuperAdminNoticeServiceError as error:
        _raise_service_error(error)


@router.patch(
    "/platform/notices/{notice_id}",
    response_model=SuperAdminNoticeRead,
    summary="Editar aviso ainda nao publicado",
)
async def update_notice(
    notice_id: UUID,
    payload: SuperAdminNoticeUpdate,
    request: Request,
    idempotency_key: IdempotencyKey,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_notice_manager),
    context: AuthContext = Depends(get_current_auth_context),
) -> SuperAdminNoticeRead:
    try:
        service = SuperAdminNoticeService(db)
        before = _notice_snapshot(await service.get(notice_id))
        notice = await service.update(
            notice_id,
            payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        if not service.operation_was_replayed:
            await _audit_notice(
                request,
                context,
                db,
                before_snapshot=before,
                after_snapshot=_notice_snapshot(notice),
                action="notice.update",
                reason="Conteúdo ou audiência do comunicado atualizado.",
            )
        return notice
    except SuperAdminNoticeServiceError as error:
        _raise_service_error(error)


@router.get(
    "/platform/notices/{notice_id}/recipients/preview",
    response_model=SuperAdminNoticeRecipientPreviewRead,
    summary="Pre-visualizar destinatarios resolvidos",
)
async def preview_notice_recipients(
    notice_id: UUID,
    sample_limit: int = Query(default=50, ge=1, le=200, alias="sampleLimit"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_notice_manager),
) -> SuperAdminNoticeRecipientPreviewRead:
    try:
        return await SuperAdminNoticeService(db).preview_recipients(
            notice_id,
            actor_user_id=actor.id,
            sample_limit=sample_limit,
        )
    except SuperAdminNoticeServiceError as error:
        _raise_service_error(error)


@router.post(
    "/platform/notices/{notice_id}/schedule",
    response_model=SuperAdminNoticeRead,
    summary="Agendar aviso",
)
async def schedule_notice(
    notice_id: UUID,
    payload: SuperAdminNoticeSchedule,
    request: Request,
    idempotency_key: IdempotencyKey,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_notice_manager),
    context: AuthContext = Depends(get_current_auth_context),
) -> SuperAdminNoticeRead:
    try:
        service = SuperAdminNoticeService(db)
        before = _notice_snapshot(await service.get(notice_id))
        notice = await service.schedule(
            notice_id,
            payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        if not service.operation_was_replayed:
            await _audit_notice(
                request,
                context,
                db,
                before_snapshot=before,
                after_snapshot=_notice_snapshot(notice),
                action="notice.schedule",
                reason="Comunicado agendado para publicação local.",
            )
        return notice
    except SuperAdminNoticeServiceError as error:
        _raise_service_error(error)


@router.post(
    "/platform/notices/{notice_id}/publish",
    response_model=SuperAdminNoticePublicationRead,
    summary="Publicar aviso e registrar entregas locais",
)
async def publish_notice(
    notice_id: UUID,
    request: Request,
    idempotency_key: IdempotencyKey,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_notice_manager),
    context: AuthContext = Depends(get_current_auth_context),
) -> SuperAdminNoticePublicationRead:
    try:
        service = SuperAdminNoticeService(db)
        before = _notice_snapshot(await service.get(notice_id))
        publication = await service.publish(
            notice_id,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        if not service.operation_was_replayed:
            await _audit_notice(
                request,
                context,
                db,
                before_snapshot=before,
                after_snapshot=_notice_snapshot(publication.notice),
                action="notice.publish",
                reason="Comunicado publicado nos canais locais configurados.",
            )
        return publication
    except SuperAdminNoticeServiceError as error:
        _raise_service_error(error)


@router.post(
    "/platform/notices/{notice_id}/cancel",
    response_model=SuperAdminNoticeRead,
    summary="Cancelar ou retirar aviso",
)
async def cancel_notice(
    notice_id: UUID,
    request: Request,
    idempotency_key: IdempotencyKey,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_notice_manager),
    context: AuthContext = Depends(get_current_auth_context),
) -> SuperAdminNoticeRead:
    try:
        service = SuperAdminNoticeService(db)
        before = _notice_snapshot(await service.get(notice_id))
        notice = await service.cancel(
            notice_id,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        if not service.operation_was_replayed:
            await _audit_notice(
                request,
                context,
                db,
                before_snapshot=before,
                after_snapshot=_notice_snapshot(notice),
                action="notice.cancel",
                reason="Comunicado cancelado ou retirado da distribuição local.",
            )
        return notice
    except SuperAdminNoticeServiceError as error:
        _raise_service_error(error)


@router.get(
    "/platform/notices/{notice_id}/receipts",
    response_model=SuperAdminNoticeReceiptListRead,
    summary="Listar recibos por destinatario e canal",
)
async def list_notice_receipts(
    notice_id: UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_notice_manager),
) -> SuperAdminNoticeReceiptListRead:
    try:
        return await SuperAdminNoticeService(db).list_receipts(
            notice_id,
            offset=offset,
            limit=limit,
        )
    except SuperAdminNoticeServiceError as error:
        _raise_service_error(error)


@router.get(
    "/platform/notices/{notice_id}/audit",
    response_model=list[SuperAdminNoticeAuditEventRead],
    summary="Consultar trilha de auditoria do aviso",
    deprecated=True,
)
async def list_notice_audit(
    notice_id: UUID,
    limit: int = Query(default=200, ge=1, le=1_000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_notice_manager),
) -> list[SuperAdminNoticeAuditEventRead]:
    try:
        return await SuperAdminNoticeService(db).list_audit(
            notice_id,
            limit=limit,
        )
    except SuperAdminNoticeServiceError as error:
        _raise_service_error(error)


@router.get(
    "/platform/notices/{notice_id}/audit/page",
    response_model=SuperAdminNoticeAuditEventPageRead,
    summary="Paginar trilha de auditoria do aviso",
)
async def page_notice_audit(
    notice_id: UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_notice_manager),
) -> SuperAdminNoticeAuditEventPageRead:
    try:
        items, total = await SuperAdminNoticeService(db).page_audit(
            notice_id,
            offset=offset,
            limit=limit,
        )
        return SuperAdminNoticeAuditEventPageRead(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(items) < total,
        )
    except SuperAdminNoticeServiceError as error:
        _raise_service_error(error)
