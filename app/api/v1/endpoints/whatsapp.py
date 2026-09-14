"""
Endpoint: WhatsApp Business Integration.
Mantém as rotas legadas e expõe a nova API multi-conexão por workspace.
"""
import hashlib
import hmac
import io
import json
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.entitlement_errors import (
    enforce_workspace_feature_or_http,
    entitlement_http_exception,
)
from app.core.config import settings
from app.core.deps import (
    AuthContext,
    can_view_sensitive_lead_data,
    get_current_auth_context,
    get_current_user_id,
    get_current_workspace_id,
    get_db,
    has_effective_permission,
    require_any_permission,
    require_permission,
    require_workspace_admin,
)
from app.core.klm_runtime import external_delivery_allowed
from app.core.lead_privacy import (
    mask_whatsapp_contact,
    mask_whatsapp_contact_identity,
    mask_whatsapp_conversation,
)
from app.core.logging_privacy import safe_error_type
from app.core.request_body import read_bounded_body
from app.models.entitlement import EntitlementKey
from app.models.meta_webhook_operations import MetaWebhookReceipt
from app.models.workspace_membership import WorkspaceRole
from app.schemas.whatsapp import (
    ApplyFlowRequest,
    ConversationAssigneeRead,
    ConversationAssignRequest,
    ConversationDocumentSendRequest,
    ConversationHandoffRequest,
    ConversationRead,
    InboundReconciliationRead,
    InboundReconciliationResolveRequest,
    MessageTemplateRead,
    ProviderMediaReconciliationRequest,
    ProviderMediaUploadIntentRead,
    ProviderMediaUploadRead,
    ScheduleConversationMeetingRequest,
    ScheduleConversationMessageRequest,
    SendContractRequest,
    SendDocumentRequest,
    SendMessageRequest,
    SendMessageResponse,
    SendProposalRequest,
    SendStructuredMessageRequest,
    StartWhatsAppConversationRequest,
    TechProviderCallbackRequest,
    WhatsAppConnectionCreateManual,
    WhatsAppConnectionCreateQr,
    WhatsAppConnectionHealthRead,
    WhatsAppConnectionRead,
    WhatsAppConnectionStatus,
    WhatsAppConnectionStatusRead,
    WhatsAppConnectRequest,
    WhatsAppContactIdentityRead,
    WhatsAppContactRead,
    WhatsAppMetaAutomationPolicyUpdate,
    WhatsAppMetaOperationsRead,
    WhatsAppWebhookInfoRead,
)
from app.services.entitlement_service import EntitlementError
from app.services.whatsapp.connection_health_service import (
    ConnectionHealthError,
    WhatsAppConnectionHealthService,
)
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.media_upload import (
    WhatsAppMediaUploadError,
    resolve_media_policy,
    validate_media_size,
)
from app.services.whatsapp.meta_webhook_operations_service import (
    MetaWebhookOperationsService,
    meta_webhook_catalog,
)
from app.services.whatsapp.provider_media_receipt_service import ProviderMediaReceiptError
from app.services.whatsapp.webhook_service import (
    InboundPipelineReconciliationConflictError,
    InboundPipelineReconciliationNotFoundError,
    WebhookSigningIdentityError,
)
from app.services.whatsapp.webhooks.subscription_service import MetaWebhookControlPlaneService
from app.services.whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])


def _get_service(db: AsyncSession = Depends(get_db)) -> WhatsAppService:
    return WhatsAppService(db)


def _get_health_service(db: AsyncSession = Depends(get_db)) -> WhatsAppConnectionHealthService:
    return WhatsAppConnectionHealthService(db)


def _require_external_delivery(action: str) -> None:
    if not external_delivery_allowed():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "KLM-EXT-001",
                "message": f"{action} bloqueado enquanto entregas externas estiverem desabilitadas.",
            },
        )


@router.get("/status", response_model=WhatsAppConnectionStatus, summary="Status da conexão default do workspace")
async def get_whatsapp_status(
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    # V22: sonda de gate das superfícies de mensagem; quem envia mensagem precisa dela.
    # `verifyToken` e `webhookUrl` são segredo de plataforma: só para integrations:view.
    context: AuthContext = Depends(
        require_any_permission(("integrations", "view"), ("mensagens", "send_message"))
    ),
):
    _require_external_delivery("Consulta ao provedor do WhatsApp")
    try:
        result = await svc.get_status(workspace_id)
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    if not await has_effective_permission(context, db, "integrations", "view"):
        result = result.model_copy(update={"verifyToken": "", "webhookUrl": ""})
    return result


@router.post("/connect", response_model=WhatsAppConnectionStatus, summary="Wrapper legado de conexão manual")
async def connect_whatsapp(
    payload: WhatsAppConnectRequest,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "manage")),
):
    _require_external_delivery("Conexão do WhatsApp")
    try:
        await svc.create_manual_connection(
            WhatsAppConnectionCreateManual(
                access_token=payload.access_token,
                phone_number_id=payload.phone_number_id,
                business_account_id=payload.business_account_id,
                is_default=True,
            ),
            workspace_id,
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    return await svc.get_status(workspace_id)


@router.delete("/disconnect", status_code=status.HTTP_204_NO_CONTENT, summary="Wrapper legado de desconexão")
async def disconnect_whatsapp(
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "manage")),
):
    await svc.disconnect(workspace_id)


@router.get("/connections", response_model=List[WhatsAppConnectionRead], summary="Listar conexões WhatsApp do workspace")
async def list_whatsapp_connections(
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    # V22: quem envia mensagem escolhe a conexão ao criar rascunho e campanha; a leitura
    # não expõe segredo. Escritas e saúde da conexão seguem integrations.
    _: object = Depends(
        require_any_permission(("integrations", "view"), ("mensagens", "send_message"))
    ),
):
    return await svc.list_connections(workspace_id)


@router.get(
    "/meta-operations",
    response_model=WhatsAppMetaOperationsRead,
    summary="Cobertura e eventos operacionais dos webhooks Meta",
)
async def list_whatsapp_meta_operations(
    field: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=100, ge=1, le=200),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("integrations", "view")),
):
    legacy_service = MetaWebhookOperationsService(db)
    events = await legacy_service.list_events(
        workspace_id=workspace_id,
        field=field,
        limit=limit,
    )
    serialized = [legacy_service.serialize_event(event) for event in events]
    control = await MetaWebhookControlPlaneService(db).operations(
        workspace_id=workspace_id,
        field=field,
    )
    return {
        **control,
        "events": serialized,
        "eventCount": len(serialized),
        "latestEventAt": serialized[0]["occurredAt"] if serialized else None,
    }


@router.get(
    "/meta-operations/{field}",
    response_model=WhatsAppMetaOperationsRead,
    summary="Detalhar um campo operacional de webhook Meta",
)
async def get_whatsapp_meta_operation(
    field: str,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("integrations", "view")),
):
    try:
        return await MetaWebhookControlPlaneService(db).operations(
            workspace_id=workspace_id,
            field=field,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": str(exc)}) from exc


@router.get(
    "/meta-operations/{field}/receipts",
    summary="Listar recibos sanitizados de um campo Meta",
)
async def list_whatsapp_meta_operation_receipts(
    field: str,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("integrations", "view")),
):
    try:
        result = await MetaWebhookControlPlaneService(db).operations(
            workspace_id=workspace_id,
            field=field,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": str(exc)}) from exc
    return {
        "items": result["receipts"],
        "total": result["receiptCount"],
        "latestReceiptAt": result["latestReceiptAt"],
    }


@router.post(
    "/meta-subscriptions/preview",
    summary="Prévia da reconciliação de assinaturas Meta",
)
async def preview_whatsapp_meta_subscriptions(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("integrations", "manage")),
):
    return await MetaWebhookControlPlaneService(db).preview_reconciliation(
        workspace_id=workspace_id
    )


@router.post(
    "/meta-subscriptions/reconcile",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reconciliar o app com as WABAs do workspace",
)
async def reconcile_whatsapp_meta_subscriptions(
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("integrations", "manage")),
):
    _require_external_delivery("Reconciliação de assinaturas Meta")
    service = MetaWebhookControlPlaneService(db)
    job = await service.create_reconciliation_job(
        workspace_id=workspace_id, requested_by_user_id=actor_id
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
            status_code=503,
            detail={"code": "META_WEBHOOK_QUEUE_UNAVAILABLE"},
        ) from exc
    return service.serialize_job(job)


@router.get(
    "/meta-subscriptions/jobs/{job_id}",
    summary="Consultar job de reconciliação de assinaturas Meta",
)
async def get_whatsapp_meta_subscription_job(
    job_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("integrations", "view")),
):
    service = MetaWebhookControlPlaneService(db)
    job = await service.get_reconciliation_job(workspace_id=workspace_id, job_id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "META_WEBHOOK_JOB_NOT_FOUND"})
    return service.serialize_job(job)


@router.post(
    "/meta-operations/receipts/{receipt_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reprocessar um receipt Meta com falha, no máximo três vezes",
)
async def retry_whatsapp_meta_operation_receipt(
    receipt_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("integrations", "manage")),
):
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
            args=[str(workspace_id), str(receipt_id)], retry=False
        )
    except Exception as exc:
        receipt.status = "failed"
        receipt.sanitized_error_code = "META_WEBHOOK_QUEUE_UNAVAILABLE"
        db.add(receipt)
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail={"code": "META_WEBHOOK_QUEUE_UNAVAILABLE"},
        ) from exc
    return MetaWebhookControlPlaneService.serialize_receipt(receipt)


@router.get(
    "/meta-automation-policies",
    response_model=WhatsAppMetaOperationsRead,
    summary="Listar políticas de automação dos webhooks Meta",
)
async def list_whatsapp_meta_automation_policies(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("automations", "manage")),
):
    return await MetaWebhookControlPlaneService(db).operations(workspace_id=workspace_id)


@router.patch(
    "/meta-automation-policies/{field}",
    summary="Atualizar política de automação de um webhook Meta",
)
async def update_whatsapp_meta_automation_policy(
    field: str,
    payload: WhatsAppMetaAutomationPolicyUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor_id: UUID = Depends(get_current_user_id),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("automations", "manage")),
):
    if payload.enabled and not (
        context.role == WorkspaceRole.workspace_owner
        or context.administrative_session_type == "root_direct"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "META_WEBHOOK_POLICY_OWNER_REQUIRED",
                "message": "Somente o titular pode aceitar uma política de automação Meta.",
            },
        )
    try:
        policy = await MetaWebhookControlPlaneService(db).update_policy(
            workspace_id=workspace_id,
            field=field,
            enabled=payload.enabled,
            mode=payload.mode,
            minimum_severity=payload.minimum_severity,
            expected_version=payload.expected_version,
            actor_id=actor_id,
        )
    except ValueError as exc:
        code = str(exc)
        status_code = 409 if code.endswith("CONFLICT") else 422
        raise HTTPException(status_code=status_code, detail={"code": code}) from exc
    return {
        "field": policy.field,
        "enabled": policy.enabled,
        "mode": policy.mode,
        "minimumSeverity": policy.minimum_severity,
        "version": policy.version,
    }


@router.get(
    "/connections/webhook-info",
    response_model=WhatsAppWebhookInfoRead,
    summary="Dados para configurar o webhook do app Meta no modo manual (B1)",
)
async def get_manual_connection_webhook_info(
    _workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "view")),
):
    """
    Wizard B1 (Manual/BYOA) precisa exibir a URL de callback e o verify token
    para o escritório colar na tela "Webhooks" do próprio app Business no
    developers.facebook.com. Hoje a URL do webhook e o verify token são
    globais por deploy (não por conexão/tenant); expor aqui é só leitura,
    sem criar tabela nova nem migration.
    """
    base_url = settings.PAPERCLIP_PUBLIC_URL.rstrip("/")
    webhook_url = f"{base_url}/api/v1/whatsapp/webhook"
    verify_token = settings.WHATSAPP_VERIFY_TOKEN.strip()
    app_secret_configured = bool(settings.WHATSAPP_APP_SECRET.strip())
    instructions = [
        "Copie a URL de callback e cole no campo \"Callback URL\" da tela Webhooks do seu app.",
        "Copie o token de verificação e cole no campo \"Verify Token\" da mesma tela.",
        "Assine os campos recomendados exibidos abaixo no produto WhatsApp Business Account.",
        "Clique em Verificar e Salvar: a Meta chama esta URL com um desafio (GET) que o KLM responde automaticamente.",
    ]
    if not verify_token:
        instructions.insert(
            0,
            "O KLM ainda não tem um verify token configurado neste ambiente; peça ao time de operações para definir WHATSAPP_VERIFY_TOKEN antes de prosseguir.",
        )
    return WhatsAppWebhookInfoRead(
        webhookUrl=webhook_url,
        verifyToken=verify_token or None,
        verifyTokenConfigured=bool(verify_token),
        appSecretConfigured=app_secret_configured,
        instructions=instructions,
        recommended_fields=[
            item["field"] for item in meta_webhook_catalog() if item["recommended"]
        ],
    )


@router.post(
    "/connections/{connection_id}/media",
    response_model=ProviderMediaUploadRead,
    status_code=status.HTTP_201_CREATED,
    summary="Enviar mídia à Meta e registrar prova imutável para uso nas mensagens",
)
async def upload_provider_media(
    connection_id: UUID,
    upload: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    _require_external_delivery("Upload de mídia para o WhatsApp")
    try:
        policy = resolve_media_policy(upload.content_type)
        digest = hashlib.sha256()
        size_bytes = 0
        while chunk := await upload.read(1024 * 1024):
            size_bytes += len(chunk)
            if size_bytes > policy.max_size_bytes:
                validate_media_size(size_bytes, policy)
            digest.update(chunk)
        validate_media_size(size_bytes, policy)
        await upload.seek(0)
        return await svc.upload_provider_media(
            workspace_id=workspace_id,
            connection_id=connection_id,
            filename=upload.filename or "arquivo",
            content_type=policy.content_type,
            file_content=upload.file,
            source_sha256=digest.hexdigest(),
            source_size_bytes=size_bytes,
            idempotency_key=idempotency_key,
        )
    except WhatsAppMediaUploadError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                if exc.code == "provider_media_too_large"
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ProviderMediaReceiptError as exc:
        detail = {"code": exc.code, "message": str(exc)}
        if exc.intent_id is not None:
            detail["intentId"] = str(exc.intent_id)
        raise HTTPException(
            status_code=exc.status_code,
            detail=detail,
        ) from exc
    except WhatsAppProviderError as exc:
        raise exc.to_http_exception() from exc


@router.get(
    "/connections/{connection_id}/media-intents/{intent_id}",
    response_model=ProviderMediaUploadIntentRead,
    summary="Consultar estado durável e reconciliação de um upload de mídia",
)
async def get_provider_media_upload_intent(
    connection_id: UUID,
    intent_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
):
    try:
        return await svc.get_provider_media_upload_intent(
            workspace_id=workspace_id,
            connection_id=connection_id,
            intent_id=intent_id,
        )
    except ProviderMediaReceiptError as exc:
        detail = {"code": exc.code, "message": str(exc)}
        if exc.intent_id is not None:
            detail["intentId"] = str(exc.intent_id)
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc


@router.post(
    "/connections/{connection_id}/media-intents/{intent_id}/reconciliation",
    response_model=ProviderMediaUploadIntentRead,
    summary="Registrar decisão humana auditável para um upload de estado incerto",
)
async def reconcile_provider_media_upload_intent(
    connection_id: UUID,
    intent_id: UUID,
    payload: ProviderMediaReconciliationRequest,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
):
    try:
        return await svc.reconcile_provider_media_upload_intent(
            workspace_id=workspace_id,
            connection_id=connection_id,
            intent_id=intent_id,
            actor_user_id=context.user_id,
            decision=payload.decision.value,
            reason=payload.reason,
            provider_media_id=payload.provider_media_id,
            provider_receipt=payload.provider_receipt,
        )
    except ProviderMediaReceiptError as exc:
        detail = {"code": exc.code, "message": str(exc)}
        if exc.intent_id is not None:
            detail["intentId"] = str(exc.intent_id)
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc


@router.post(
    "/messages/{message_id}/to-pdf",
    summary="Converter uma imagem recebida em PDF",
)
async def convert_image_message_to_pdf(
    message_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
):
    pdf_bytes = await svc.image_message_to_pdf(message_id, workspace_id)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="mensagem-{message_id}.pdf"'},
    )


@router.post(
    "/messages/{message_id}/inbound-reconciliation",
    response_model=InboundReconciliationRead,
    summary="Resolver entrega externa incerta no pipeline inbound",
)
async def resolve_inbound_reconciliation(
    message_id: UUID,
    payload: InboundReconciliationResolveRequest,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
):
    try:
        return await svc.webhook_service.resolve_inbound_delivery_reconciliation(
            workspace_id=workspace_id,
            message_id=message_id,
            actor_user_id=context.user_id,
            decision=payload.decision.value,
            reason=payload.reason,
        )
    except InboundPipelineReconciliationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "WA-RECON-001", "message": str(exc)},
        ) from exc
    except InboundPipelineReconciliationConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "WA-RECON-002", "message": str(exc)},
        ) from exc


@router.post(
    "/connections/manual",
    response_model=WhatsAppConnectionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar ou atualizar uma conexão manual oficial da Meta",
)
async def create_manual_connection(
    payload: WhatsAppConnectionCreateManual,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "manage")),
):
    _require_external_delivery("Conexão manual do WhatsApp")
    try:
        return await svc.create_manual_connection(payload, workspace_id)
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error


@router.post(
    "/connections/qr",
    response_model=WhatsAppConnectionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar uma conexão WhatsApp via gateway QR",
)
async def create_qr_connection(
    payload: WhatsAppConnectionCreateQr,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "manage")),
):
    _require_external_delivery("Conexão QR do WhatsApp")
    try:
        return await svc.create_qr_connection(payload, workspace_id)
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error


@router.post(
    "/connections/{connection_id}/set-default",
    response_model=WhatsAppConnectionRead,
    summary="Definir conexão default do workspace",
)
async def set_default_connection(
    connection_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "manage")),
):
    return await svc.set_default_connection(workspace_id, connection_id)


@router.delete(
    "/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Desativar/remover operacionalmente uma conexão",
)
async def delete_connection(
    connection_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "manage")),
):
    await svc.delete_connection(workspace_id, connection_id)


@router.get(
    "/connections/{connection_id}/status",
    response_model=WhatsAppConnectionStatusRead,
    summary="Consultar status de uma conexão específica",
)
async def get_connection_status(
    connection_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "view")),
):
    _require_external_delivery("Consulta ao provedor do WhatsApp")
    try:
        return await svc.get_connection_status(workspace_id, connection_id)
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error


@router.get(
    "/connections/{connection_id}/health",
    response_model=WhatsAppConnectionHealthRead,
    summary="Saúde do número: tier, quota diária, quality rating e rampa (W5.6)",
)
async def get_connection_health(
    connection_id: UUID,
    health_svc: WhatsAppConnectionHealthService = Depends(_get_health_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "view")),
):
    """
    Painel de saúde do número (dados locais do KLM). Não depende do gate
    de entregas externas: a sincronização real com a Business Management
    API da Meta é apenas um marcador honesto (`syncedFromMeta`) enquanto
    o gate estiver fechado, mas o estado local sempre é retornado.
    """
    try:
        return await health_svc.get_health(workspace_id=workspace_id, connection_id=connection_id)
    except ConnectionHealthError as error:
        raise error.to_http_exception() from error


@router.get("/webhook", summary="Verificação do webhook da Meta", include_in_schema=False)
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
):
    configured_token = settings.WHATSAPP_VERIFY_TOKEN.strip()
    if (
        configured_token
        and hub_mode == "subscribe"
        and hmac.compare_digest(hub_verify_token, configured_token)
    ):
        return int(hub_challenge)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "WA-004", "message": "Verify token inválido."},
    )


@router.post("/webhook", status_code=status.HTTP_200_OK, summary="Receber eventos do WhatsApp", include_in_schema=False)
async def receive_webhook(
    request: Request,
    svc: WhatsAppService = Depends(_get_service),
    x_hub_signature_256: str = Header(default=""),
):
    payload_bytes = await read_bounded_body(request)
    # A assinatura cobre o envelope inteiro. Antes do HMAC, todos os números e
    # WABAs referenciados precisam convergir para o mesmo owner e o mesmo App
    # Secret efetivo; assim uma chave BYOA legítima não autoriza anexar entries
    # de outro tenant/plataforma no mesmo corpo.
    try:
        candidate_secrets = await svc.resolve_webhook_signing_secrets(
            payload_bytes
        )
    except WebhookSigningIdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "WA-004", "message": "Assinatura do webhook inválida."},
        ) from exc
    if not candidate_secrets:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "WA-005", "message": "Webhook do WhatsApp não configurado."},
        )
    if not svc.verify_webhook_signature(
        payload_bytes, x_hub_signature_256, candidate_secrets=candidate_secrets
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "WA-004", "message": "Assinatura do webhook inválida."},
        )
    try:
        payload = json.loads(payload_bytes)
        if not isinstance(payload, dict):
            raise ValueError("Webhook raiz precisa ser um objeto JSON.")
    except (ValueError, TypeError) as exc:
        logger.error(
            "whatsapp.webhook_payload_invalid",
            extra={"error_type": safe_error_type(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "WA-009",
                "message": "Payload do webhook do WhatsApp não é um objeto JSON válido.",
            },
        ) from exc

    envelope = await svc.capture_webhook_envelope(
        raw_payload=payload_bytes,
        signature_header=x_hub_signature_256,
        request_metadata={
            "method": request.method,
            "path": request.url.path,
            "contentType": request.headers.get("content-type"),
            "contentLength": request.headers.get("content-length"),
            "userAgentSha256": hashlib.sha256(
                request.headers.get("user-agent", "").encode("utf-8")
            ).hexdigest(),
        },
    )
    # O envelope imutável e seu estado mutável são persistidos na mesma
    # transação antes do enqueue. Uma reentrega exata devolve o envelope
    # existente; enqueues duplicados são absorvidos pelo claim/lease do worker.
    await svc.db.commit()

    from app.workers.whatsapp_webhook_tasks import process_inbound_webhook

    try:
        process_inbound_webhook.apply_async(
            kwargs={"envelope_id": str(envelope.id)}
        )
    except Exception as exc:
        # A Meta já entregou um envelope autenticado e durável. O estado
        # `queued` continua consultável; além de uma reentrega exata, o scanner
        # periódico volta a despachá-lo quando o broker se recuperar. A falha
        # do broker não converte o ACK em 5xx nem perde a intenção durável.
        logger.error(
            "whatsapp.webhook_enqueue_failed",
            extra={
                "envelope_id": str(envelope.id),
                "error_type": safe_error_type(exc),
            },
        )
    return {"status": "ok"}


@router.get("/contacts", response_model=List[WhatsAppContactRead], summary="Listar contatos do WhatsApp")
async def list_contacts(
    search: Optional[str] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("mensagens", "view")),
):
    contacts = await svc.list_contacts(workspace_id=workspace_id, search=search, skip=skip, limit=limit)
    return contacts if can_view_sensitive else [mask_whatsapp_contact(item) for item in contacts]


@router.get(
    "/contacts/{contact_id}/identity",
    response_model=WhatsAppContactIdentityRead,
    summary="Identificadores do contato — telefone, BSUID, username e se é endereçável",
)
async def get_contact_identity(
    contact_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("mensagens", "view")),
):
    """Endpoint dedicado, propositalmente pequeno.

    Existe para a tela de conversas mostrar os identificadores SEM alterar o
    payload da conversa, que já funciona e é central. Se esta busca falhar, a
    tela continua exatamente como está hoje: é acréscimo que não arrisca o que
    já está de pé.

    Sem `lead_data.view` o telefone volta mascarado, mas `addressable` continua
    verdadeiro. Saber SE dá para responder não é dado sensível, e esconder isso
    faria o operador tentar e falhar sem entender o motivo.
    """
    contact = await svc.get_contact(workspace_id=workspace_id, contact_id=contact_id)
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "WA-404", "message": "Contato não encontrado."},
        )

    identity = WhatsAppContactIdentityRead(
        id=contact.id,
        phone=contact.phone,
        phoneMasked=False,
        bsuid=contact.bsuid,
        parentBsuid=contact.parent_bsuid,
        username=contact.username,
        addressable=bool(contact.phone or contact.bsuid),
    )
    return identity if can_view_sensitive else mask_whatsapp_contact_identity(identity)


@router.get("/contacts/{contact_id}", response_model=ConversationRead, summary="Obter conversa completa")
async def get_conversation(
    contact_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("mensagens", "view")),
):
    conversation = await svc.get_conversation(contact_id, workspace_id=workspace_id)
    return conversation if can_view_sensitive else mask_whatsapp_conversation(conversation)


@router.post("/contacts/{contact_id}/read", status_code=status.HTTP_204_NO_CONTENT, summary="Marcar conversa como lida")
async def mark_conversation_read(
    contact_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
):
    await svc.mark_as_read(contact_id, workspace_id=workspace_id)


@router.post(
    "/messages",
    response_model=SendMessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enviar mensagem outbound",
)
async def send_message(
    payload: SendMessageRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    svc: WhatsAppService = Depends(_get_service),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    return await svc.send_message(
        payload,
        sent_by_id=current_user_id,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/conversations/start",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Iniciar conversa individual com template oficial aprovado",
)
async def start_conversation(
    payload: StartWhatsAppConversationRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    svc: WhatsAppService = Depends(_get_service),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    _require_external_delivery("Início de conversa pelo WhatsApp")
    conversation = await svc.start_conversation(
        payload,
        sent_by_id=current_user_id,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
    )
    return conversation if can_view_sensitive else mask_whatsapp_conversation(conversation)


@router.post(
    "/messages/document",
    response_model=SendMessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enviar documento outbound",
)
async def send_document(
    payload: SendDocumentRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    svc: WhatsAppService = Depends(_get_service),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    return await svc.send_document(
        payload,
        sent_by_id=current_user_id,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/messages/structured",
    response_model=SendMessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enviar modelo, botões, lista, Flow ou mídia pela API oficial",
)
async def send_structured_message(
    payload: SendStructuredMessageRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    svc: WhatsAppService = Depends(_get_service),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    return await svc.send_structured(
        payload,
        sent_by_id=current_user_id,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/embedded-signup",
    response_model=WhatsAppConnectionStatus,
    summary="Wrapper legado para concluir o Embedded Signup da Meta",
)
async def embedded_signup_connect(
    payload: TechProviderCallbackRequest,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("integrations", "manage")),
):
    _require_external_delivery("Embedded Signup do WhatsApp")
    await svc.complete_embedded_signup(payload, workspace_id)
    return await svc.get_status(workspace_id)


@router.get(
    "/conversations/assignees",
    response_model=List[ConversationAssigneeRead],
    summary="Listar atendentes elegíveis do workspace",
)
async def list_conversation_assignees(
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
):
    return await svc.list_assignable_users(workspace_id)


@router.post(
    "/conversations/{contact_id}/assign",
    response_model=ConversationRead,
    summary="Atribuir ou desatribuir a conversa",
)
async def assign_conversation(
    contact_id: UUID,
    payload: ConversationAssignRequest,
    svc: WhatsAppService = Depends(_get_service),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    conversation = await svc.assign_conversation(contact_id, workspace_id, payload, current_user_id)
    return conversation if can_view_sensitive else mask_whatsapp_conversation(conversation)


@router.post(
    "/conversations/{contact_id}/claim",
    response_model=ConversationRead,
    summary="Assumir conversa para o usuário atual",
)
async def claim_conversation(
    contact_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    conversation = await svc.claim_conversation(contact_id, workspace_id, current_user_id)
    return conversation if can_view_sensitive else mask_whatsapp_conversation(conversation)


@router.post(
    "/conversations/{contact_id}/handoff",
    response_model=ConversationRead,
    summary="Alterar handoff entre IA e atendimento humano",
)
async def handoff_conversation(
    contact_id: UUID,
    payload: ConversationHandoffRequest,
    svc: WhatsAppService = Depends(_get_service),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    conversation = await svc.handoff_conversation(contact_id, workspace_id, payload, current_user_id)
    return conversation if can_view_sensitive else mask_whatsapp_conversation(conversation)


@router.post(
    "/conversations/{contact_id}/apply-flow",
    response_model=ConversationRead,
    summary="Aplicar flow/template ao atendimento",
)
async def apply_flow(
    contact_id: UUID,
    payload: ApplyFlowRequest,
    svc: WhatsAppService = Depends(_get_service),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    try:
        conversation = await svc.apply_flow(contact_id, workspace_id, payload, current_user_id)
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    return conversation if can_view_sensitive else mask_whatsapp_conversation(conversation)


@router.post(
    "/conversations/{contact_id}/schedule-message",
    response_model=ConversationRead,
    summary="Agendar mensagem de follow-up",
)
async def schedule_conversation_message(
    contact_id: UUID,
    payload: ScheduleConversationMessageRequest,
    svc: WhatsAppService = Depends(_get_service),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    conversation = await svc.schedule_message_for_conversation(contact_id, workspace_id, payload, current_user_id)
    return conversation if can_view_sensitive else mask_whatsapp_conversation(conversation)


@router.post(
    "/conversations/{contact_id}/schedule-meeting",
    response_model=ConversationRead,
    summary="Criar reunião a partir do chat",
)
async def schedule_conversation_meeting(
    contact_id: UUID,
    payload: ScheduleConversationMeetingRequest,
    svc: WhatsAppService = Depends(_get_service),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    conversation = await svc.schedule_meeting_for_conversation(contact_id, workspace_id, payload, current_user_id)
    return conversation if can_view_sensitive else mask_whatsapp_conversation(conversation)


@router.post(
    "/conversations/{contact_id}/send-media",
    response_model=ConversationRead,
    summary="Enviar mídia ou documento pelo chat",
)
async def send_conversation_media(
    contact_id: UUID,
    payload: ConversationDocumentSendRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    svc: WhatsAppService = Depends(_get_service),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    _require_external_delivery("Envio de mídia pelo WhatsApp")
    conversation = await svc.send_media_for_conversation(
        contact_id,
        workspace_id,
        payload,
        current_user_id,
        idempotency_key,
    )
    return conversation if can_view_sensitive else mask_whatsapp_conversation(conversation)


@router.post(
    "/conversations/{contact_id}/send-proposal",
    response_model=ConversationRead,
    summary="Enviar proposta comercial pelo chat",
)
async def send_conversation_proposal(
    contact_id: UUID,
    payload: SendProposalRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    svc: WhatsAppService = Depends(_get_service),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    _require_external_delivery("Envio de proposta pelo WhatsApp")
    conversation = await svc.send_proposal_for_conversation(
        contact_id,
        workspace_id,
        payload,
        current_user_id,
        idempotency_key,
    )
    return conversation if can_view_sensitive else mask_whatsapp_conversation(conversation)


@router.post(
    "/conversations/{contact_id}/send-contract",
    response_model=ConversationRead,
    summary="Enviar contrato para assinatura pelo chat",
)
async def send_conversation_contract(
    contact_id: UUID,
    payload: SendContractRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    svc: WhatsAppService = Depends(_get_service),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("mensagens", "send_message")),
    __: object = Depends(require_permission("aprovacoes", "approve")),
):
    await enforce_workspace_feature_or_http(
        db,
        workspace_id,
        EntitlementKey.digital_signature,
    )
    _require_external_delivery("Criação e envio de contrato")
    conversation = await svc.send_contract_for_conversation(
        contact_id,
        workspace_id,
        payload,
        current_user_id,
        idempotency_key,
    )
    return conversation if can_view_sensitive else mask_whatsapp_conversation(conversation)


@router.get(
    "/templates",
    response_model=List[MessageTemplateRead],
    summary="Biblioteca mista de mensagens do chat",
)
async def list_message_templates(
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
):
    return await svc.list_templates(workspace_id)
