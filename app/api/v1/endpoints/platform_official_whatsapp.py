from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.klm_runtime import external_delivery_allowed
from app.core.platform_access import require_platform_module
from app.core.platform_audit import record_canonical_platform_mutation
from app.core.platform_rbac import is_platform_root
from app.models.meta_webhook_operations import MetaWebhookReceipt
from app.models.platform_official_whatsapp import (
    PlatformOfficialWhatsAppConnection,
    PlatformOfficialWhatsAppRole,
)
from app.models.user import User
from app.schemas.platform_whatsapp import (
    PlatformOfficialWhatsAppActiveUpdate,
    PlatformOfficialWhatsAppConnectionRead,
    PlatformOfficialWhatsAppConnectManual,
    PlatformOfficialWhatsAppEmbeddedCallback,
    PlatformOfficialWhatsAppTestResult,
    PlatformOfficialWhatsAppTestSend,
)
from app.schemas.whatsapp import WhatsAppMetaAutomationPolicyUpdate, WhatsAppMetaOperationsRead
from app.services.platform_official_whatsapp_service import PlatformOfficialWhatsAppService
from app.services.whatsapp.meta_webhook_operations_service import (
    MetaWebhookOperationsService,
    meta_webhook_catalog_with_events,
)
from app.services.whatsapp.webhooks.subscription_service import MetaWebhookControlPlaneService

router = APIRouter(prefix="/platform/official-whatsapp", tags=["Platform Official WhatsApp"])
require_platform_whatsapp_access = require_platform_module("whatsapp")


def _connection_snapshot(
    connection: PlatformOfficialWhatsAppConnection | PlatformOfficialWhatsAppConnectionRead,
) -> tuple[str, str, dict]:
    def value(*names: str):
        for name in names:
            if hasattr(connection, name):
                return getattr(connection, name)
        return None

    role = value("role")
    connection_type = value("connection_type", "connectionType")
    connection_status = value("status")
    phone_number_id = value("phone_number_id", "phoneNumberId")
    business_account_id = value("business_account_id", "businessAccountId")
    waba_id = value("waba_id", "wabaId")
    display_phone = value("display_phone_number", "displayPhoneNumber")
    identifier = value("id") or getattr(role, "value", role)
    return (
        "platform_official_whatsapp_connection",
        str(identifier),
        {
            "role": getattr(role, "value", role),
            "connectionType": getattr(connection_type, "value", connection_type),
            "isActive": bool(value("is_active", "isActive")),
            "status": getattr(connection_status, "value", connection_status),
            "phoneNumberIdConfigured": bool(phone_number_id),
            "businessAccountConfigured": bool(business_account_id),
            "wabaConfigured": bool(waba_id),
            "displayPhoneLast4": (
                str(display_phone)[-4:] if display_phone and len(str(display_phone)) >= 4 else None
            ),
            "displayName": value("display_name", "displayName"),
            "webhookRegistered": bool(value("webhook_registered", "webhookRegistered")),
            "connectedAt": value("connected_at", "connectedAt"),
            "disconnectedAt": value("disconnected_at", "disconnectedAt"),
            "updatedAt": value("updated_at", "updatedAt"),
            "lastErrorPresent": bool(
                value("last_error_code", "lastError") or value("last_error_message")
            ),
        },
    )


def _require_external_delivery(action: str) -> None:
    if not external_delivery_allowed():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "KLM-EXT-001",
                "message": (
                    f"{action} bloqueado enquanto entregas externas estiverem desabilitadas."
                ),
            },
        )


@router.get(
    "",
    response_model=list[PlatformOfficialWhatsAppConnectionRead],
    summary="Listar canais oficiais de WhatsApp da plataforma",
)
async def list_platform_official_whatsapp(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_whatsapp_access),
) -> list[PlatformOfficialWhatsAppConnectionRead]:
    return await PlatformOfficialWhatsAppService(db).list_connections()


@router.get(
    "/meta-operations",
    response_model=WhatsAppMetaOperationsRead,
    summary="Cobertura operacional dos webhooks Meta no Blackbox",
)
async def list_platform_meta_operations(
    field: str | None = Query(default=None, max_length=80),
    workspace_id: UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_whatsapp_access),
) -> dict:
    # Payloads tenant podem conter metadados privados. Somente platform_owner
    # recebe a visão transversal; os demais operadores veem o catálogo de
    # capacidade sem qualquer conteúdo de cliente.
    events = []
    control: dict = {
        "catalog": meta_webhook_catalog_with_events([]),
        "receipts": [],
        "receiptCount": 0,
        "latestReceiptAt": None,
    }
    if is_platform_root(actor):
        service = MetaWebhookOperationsService(db)
        tenant_events = [
            service.serialize_event(event)
            for event in await service.list_events(
                workspace_id=None,
                field=field,
                limit=limit,
            )
        ]
        platform_events = [
            service.serialize_platform_event(event)
            for event in await service.list_platform_events(field=field, limit=limit)
        ]
        events = sorted(
            [*tenant_events, *platform_events],
            key=lambda item: item["occurredAt"],
            reverse=True,
        )[:limit]
        control = await MetaWebhookControlPlaneService(db).platform_operations(
            field=field, workspace_id=workspace_id
        )
    return {
        **control,
        "events": events,
        "eventCount": len(events),
        "latestEventAt": events[0]["occurredAt"] if events else None,
    }


@router.get(
    "/webhooks",
    response_model=WhatsAppMetaOperationsRead,
    summary="Alias estável da Central de webhooks Meta no Blackbox",
)
async def list_platform_webhooks(
    field: str | None = Query(default=None, max_length=80),
    workspace_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_whatsapp_access),
) -> dict:
    return await list_platform_meta_operations(
        field=field, workspace_id=workspace_id, limit=200, db=db, actor=actor
    )


def _require_platform_root(actor: User) -> None:
    if not is_platform_root(actor):
        raise HTTPException(status_code=403, detail={"code": "PLATFORM_ROOT_REQUIRED"})


@router.post("/subscriptions/preview", summary="Prévia root da reconciliação Meta")
async def preview_platform_meta_subscriptions(
    workspace_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_whatsapp_access),
) -> dict:
    _require_platform_root(actor)
    return await MetaWebhookControlPlaneService(db).preview_reconciliation(
        workspace_id=workspace_id
    )


@router.post(
    "/subscriptions/reconcile",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enfileirar reconciliação Meta root para um workspace",
)
async def reconcile_platform_meta_subscriptions(
    workspace_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_whatsapp_access),
) -> dict:
    _require_platform_root(actor)
    _require_external_delivery("Reconciliação de assinaturas Meta")
    service = MetaWebhookControlPlaneService(db)
    job = await service.create_reconciliation_job(
        workspace_id=workspace_id, requested_by_user_id=actor.id
    )
    await db.commit()
    try:
        from app.workers.meta_webhook_tasks import reconcile_meta_webhook_subscriptions

        reconcile_meta_webhook_subscriptions.apply_async(
            args=[str(workspace_id), str(job.id)], retry=False
        )
    except Exception as exc:
        job.status = "failed"
        job.sanitized_error_code = "META_WEBHOOK_QUEUE_UNAVAILABLE"
        db.add(job)
        await db.commit()
        raise HTTPException(
            status_code=503, detail={"code": "META_WEBHOOK_QUEUE_UNAVAILABLE"}
        ) from exc
    return service.serialize_job(job)


@router.get(
    "/subscriptions/jobs/{job_id}",
    summary="Consultar job root de reconciliação Meta",
)
async def get_platform_meta_subscription_job(
    job_id: UUID,
    workspace_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_whatsapp_access),
) -> dict:
    _require_platform_root(actor)
    service = MetaWebhookControlPlaneService(db)
    job = await service.get_reconciliation_job(workspace_id=workspace_id, job_id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "META_WEBHOOK_JOB_NOT_FOUND"})
    return service.serialize_job(job)


@router.post(
    "/meta-operations/receipts/{receipt_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reprocessar receipt Meta falho como root",
)
async def retry_platform_meta_receipt(
    receipt_id: UUID,
    workspace_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_whatsapp_access),
) -> dict:
    _require_platform_root(actor)
    receipt = await db.get(MetaWebhookReceipt, receipt_id)
    if receipt is None or receipt.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail={"code": "META_WEBHOOK_RECEIPT_NOT_FOUND"})
    if receipt.status == "processed":
        return MetaWebhookControlPlaneService.serialize_receipt(receipt)
    if receipt.attempt_count >= 3:
        raise HTTPException(
            status_code=409,
            detail={"code": "META_WEBHOOK_RECEIPT_RETRY_EXHAUSTED"},
        )
    receipt.status = "retrying"
    db.add(receipt)
    await db.commit()
    try:
        from app.workers.meta_webhook_tasks import retry_meta_webhook_receipt

        retry_meta_webhook_receipt.apply_async(
            args=[str(workspace_id), str(receipt.id)], retry=False
        )
    except Exception as exc:
        receipt.status = "failed"
        receipt.sanitized_error_code = "META_WEBHOOK_QUEUE_UNAVAILABLE"
        db.add(receipt)
        await db.commit()
        raise HTTPException(
            status_code=503, detail={"code": "META_WEBHOOK_QUEUE_UNAVAILABLE"}
        ) from exc
    return MetaWebhookControlPlaneService.serialize_receipt(receipt)


@router.patch(
    "/automation-policies/{field}",
    summary="Atualizar política Meta de um workspace como root",
)
async def update_platform_meta_automation_policy(
    field: str,
    payload: WhatsAppMetaAutomationPolicyUpdate,
    workspace_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_whatsapp_access),
) -> dict:
    _require_platform_root(actor)
    try:
        policy = await MetaWebhookControlPlaneService(db).update_policy(
            workspace_id=workspace_id,
            field=field,
            enabled=payload.enabled,
            mode=payload.mode,
            minimum_severity=payload.minimum_severity,
            expected_version=payload.expected_version,
            actor_id=actor.id,
        )
    except ValueError as exc:
        code = str(exc)
        raise HTTPException(
            status_code=409 if code.endswith("CONFLICT") else 422,
            detail={"code": code},
        ) from exc
    return {
        "field": policy.field,
        "enabled": policy.enabled,
        "mode": policy.mode,
        "minimumSeverity": policy.minimum_severity,
        "version": policy.version,
    }


@router.put(
    "/{role}/manual",
    response_model=PlatformOfficialWhatsAppConnectionRead,
    summary="Conectar canal oficial por credenciais manuais da Meta",
)
async def connect_platform_official_whatsapp_manual(
    role: PlatformOfficialWhatsAppRole,
    payload: PlatformOfficialWhatsAppConnectManual,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_whatsapp_access),
) -> PlatformOfficialWhatsAppConnectionRead:
    _require_external_delivery("Conexão manual do WhatsApp oficial")
    service = PlatformOfficialWhatsAppService(db)
    existing = await service.get_connection(role)
    before = _connection_snapshot(existing) if existing is not None else None
    connection = await service.connect_manual(
        role=role,
        payload=payload,
        user_id=context.user_id,
    )
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=_connection_snapshot(connection),
        module="whatsapp",
        action="official_whatsapp.connect_manual",
        reason=f"Canal oficial {role.value} configurado por credenciais manuais.",
        reason_source="system",
    )
    return connection


@router.post(
    "/{role}/embedded-signup",
    response_model=PlatformOfficialWhatsAppConnectionRead,
    summary="Concluir Embedded Signup do canal oficial",
)
async def complete_platform_official_whatsapp_embedded_signup(
    role: PlatformOfficialWhatsAppRole,
    payload: PlatformOfficialWhatsAppEmbeddedCallback,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_whatsapp_access),
) -> PlatformOfficialWhatsAppConnectionRead:
    _require_external_delivery("Embedded Signup do WhatsApp oficial")
    service = PlatformOfficialWhatsAppService(db)
    existing = await service.get_connection(role)
    before = _connection_snapshot(existing) if existing is not None else None
    connection = await service.complete_embedded_signup(
        role=role,
        payload=payload,
        user_id=context.user_id,
    )
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=_connection_snapshot(connection),
        module="whatsapp",
        action="official_whatsapp.embedded_signup",
        reason=f"Embedded Signup concluído para o canal oficial {role.value}.",
        reason_source="system",
    )
    return connection


@router.patch(
    "/{role}/active",
    response_model=PlatformOfficialWhatsAppConnectionRead,
    summary="Ativar ou desativar canal oficial",
)
async def set_platform_official_whatsapp_active(
    role: PlatformOfficialWhatsAppRole,
    payload: PlatformOfficialWhatsAppActiveUpdate,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_whatsapp_access),
) -> PlatformOfficialWhatsAppConnectionRead:
    service = PlatformOfficialWhatsAppService(db)
    existing = await service.get_connection(role)
    before = _connection_snapshot(existing) if existing is not None else None
    connection = await service.set_active(role=role, payload=payload, user_id=context.user_id)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=_connection_snapshot(connection),
        module="whatsapp",
        action="official_whatsapp.set_active",
        reason=f"Canal oficial {role.value} {'ativado' if payload.is_active else 'desativado'}.",
        reason_source="system",
    )
    return connection


@router.delete(
    "/{role}",
    response_model=PlatformOfficialWhatsAppConnectionRead,
    summary="Desconectar canal oficial",
)
async def disconnect_platform_official_whatsapp(
    role: PlatformOfficialWhatsAppRole,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_whatsapp_access),
) -> PlatformOfficialWhatsAppConnectionRead:
    service = PlatformOfficialWhatsAppService(db)
    existing = await service.get_connection(role)
    before = _connection_snapshot(existing) if existing is not None else None
    connection = await service.disconnect(role=role, user_id=context.user_id)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=_connection_snapshot(connection),
        module="whatsapp",
        action="official_whatsapp.disconnect",
        reason=f"Canal oficial {role.value} desconectado e credenciais locais removidas.",
        reason_source="system",
    )
    return connection


@router.post(
    "/{role}/test",
    response_model=PlatformOfficialWhatsAppTestResult,
    summary="Enviar mensagem de teste pelo canal oficial",
)
async def send_platform_official_whatsapp_test(
    role: PlatformOfficialWhatsAppRole,
    payload: PlatformOfficialWhatsAppTestSend,
    request: Request,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
    ),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_whatsapp_access),
) -> PlatformOfficialWhatsAppTestResult:
    _require_external_delivery("Envio de teste pelo WhatsApp oficial")
    service = PlatformOfficialWhatsAppService(db)
    existing = await service.get_connection(role)
    before = _connection_snapshot(existing) if existing is not None else None
    result = await service.send_test_message(
        role=role,
        to=payload.to,
        body=payload.body,
        idempotency_key=idempotency_key,
        requested_by_user_id=context.user_id,
    )
    current = await service.get_connection(role)
    after = _connection_snapshot(current) if current is not None else before
    if after is not None:
        after[2]["testSucceeded"] = result.success
        after[2]["destinationLast4"] = payload.to[-4:] if len(payload.to) >= 4 else None
        after[2]["bodyLength"] = len(payload.body)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="whatsapp",
        action="official_whatsapp.test_send",
        reason=f"Envio de teste solicitado para o canal oficial {role.value}.",
        reason_source="system",
    )
    return result
