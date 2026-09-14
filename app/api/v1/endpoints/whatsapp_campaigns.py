from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.entitlement_errors import entitlement_http_exception
from app.core.deps import (
    AuthContext,
    can_view_sensitive_lead_data,
    get_current_auth_context,
    get_current_user_id,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.klm_runtime import external_delivery_allowed
from app.core.lead_privacy import mask_campaign, mask_opt_in, mask_suppression
from app.core.request_body import read_bounded_body
from app.models.klm_operations import KlmAuditEvent
from app.models.whatsapp_campaign import WhatsAppCampaignRecipient, WhatsAppMetaTemplate
from app.schemas.meta_commercial_rate import WhatsAppCommercialChargeSummaryRead
from app.schemas.whatsapp_campaign import (
    AudienceListCreate,
    AudienceListImportResult,
    AudienceListMemberCreate,
    AudienceListRead,
    AudienceListUpdate,
    CampaignAudienceFilter,
    CampaignCreate,
    CampaignLaunchRequest,
    CampaignRead,
    CampaignUpdate,
    MetaTemplateAnalysisRead,
    MetaTemplateAnalysisRequest,
    MetaTemplateRead,
    MetaTemplateSyncRequest,
    OptInRecordCreate,
    OptInRecordRead,
    SuppressionEntryCreate,
    SuppressionEntryRead,
    TemplateDraftCreate,
    TemplateDraftRead,
    TemplateDraftUpdate,
    TemplateSubmitRequest,
    TemplateSubmitResult,
    UnifiedTemplateRead,
    UtilityTemplateCatalogRead,
    WhatsAppQuickSendCreate,
    WhatsAppSequenceStartCreate,
    WhatsAppSequenceStartRead,
    WhatsAppTemplateIntent,
    WhatsAppTemplatePickerRead,
    WhatsAppVariablesRead,
)
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.entitlement_service import EntitlementError
from app.services.flow_sequence_service import start_picker_sequence
from app.services.meta_ai_service import MetaAIService, assess_oab_risk
from app.services.meta_commercial_rate_service import (
    MetaCommercialRateService,
    WhatsAppCommercialCampaignNotFoundError,
)
from app.services.whatsapp.utility_template_catalog import UTILITY_TEMPLATE_BY_KEY
from app.services.whatsapp_picker_service import WhatsAppPickerService
from app.services.whatsapp_service import WhatsAppService

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp Campaigns"])


def _get_service(db: AsyncSession = Depends(get_db)) -> WhatsAppService:
    return WhatsAppService(db)


def _get_commercial_rate_service(
    db: AsyncSession = Depends(get_db),
) -> MetaCommercialRateService:
    return MetaCommercialRateService(db)


def _get_meta_ai_service(db: AsyncSession = Depends(get_db)) -> MetaAIService:
    return MetaAIService(db)


def _get_picker_service(db: AsyncSession = Depends(get_db)) -> WhatsAppPickerService:
    return WhatsAppPickerService(db)


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
    "/audience-lists",
    response_model=list[AudienceListRead],
    summary="Listar listas de disparo do workspace",
)
async def list_audience_lists(
    include_archived: bool = Query(default=False),
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    return await svc.list_audience_lists(
        workspace_id,
        include_archived=include_archived,
        mask_sensitive=not can_view_sensitive,
    )


@router.post(
    "/audience-lists",
    response_model=AudienceListRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar lista de disparo",
)
async def create_audience_list(
    payload: AudienceListCreate,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    current_user_id: UUID = Depends(get_current_user_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    return await svc.create_audience_list(workspace_id, payload, current_user_id)


@router.get(
    "/audience-lists/{audience_list_id}",
    response_model=AudienceListRead,
    summary="Detalhar lista de disparo",
)
async def get_audience_list(
    audience_list_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    return await svc.get_audience_list(
        workspace_id,
        audience_list_id,
        mask_sensitive=not can_view_sensitive,
    )


@router.patch(
    "/audience-lists/{audience_list_id}",
    response_model=AudienceListRead,
    summary="Atualizar lista de disparo",
)
async def update_audience_list(
    audience_list_id: UUID,
    payload: AudienceListUpdate,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    return await svc.update_audience_list(workspace_id, audience_list_id, payload)


@router.post(
    "/audience-lists/{audience_list_id}/members",
    response_model=AudienceListRead,
    summary="Adicionar contato à lista",
)
async def add_audience_list_member(
    audience_list_id: UUID,
    payload: AudienceListMemberCreate,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    return await svc.add_audience_list_member(workspace_id, audience_list_id, payload)


@router.post(
    "/audience-lists/{audience_list_id}/import-csv",
    response_model=AudienceListImportResult,
    summary="Importar CSV na lista",
)
async def import_audience_list_csv(
    audience_list_id: UUID,
    request: Request,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    raw = await read_bounded_body(
        request,
        max_bytes=10 * 1024 * 1024,
        too_large_detail={
            "code": "WA-LIST-009",
            "message": "O CSV excede o limite local de 10 MB.",
        },
    )
    try:
        csv_text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "WA-LIST-010", "message": "O CSV precisa estar em UTF-8."},
        ) from exc
    filename = request.headers.get("x-file-name")
    return await svc.import_audience_list_csv(
        workspace_id,
        audience_list_id,
        csv_text,
        filename=filename[:255] if filename else None,
    )


@router.get("/audience-lists/{audience_list_id}/export.csv", summary="Exportar lista em CSV")
async def export_audience_list_csv(
    audience_list_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    if not can_view_sensitive:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "LEAD-DATA-HIDDEN",
                "message": "Seu perfil não pode exportar dados de leads.",
            },
        )
    audience_list = await svc.get_audience_list(workspace_id, audience_list_id)
    content = await svc.export_audience_list_csv(workspace_id, audience_list_id)
    safe_name = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_" for char in audience_list.name
    )
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name or "lista"}.csv"'},
    )


@router.get(
    "/campaigns",
    response_model=list[CampaignRead],
    summary="Listar campanhas oficiais do workspace",
)
async def list_campaigns(
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    campaigns = await svc.list_campaigns(workspace_id)
    return campaigns if can_view_sensitive else [mask_campaign(item) for item in campaigns]


@router.get(
    "/templates/picker",
    response_model=WhatsAppTemplatePickerRead,
    summary="Listar templates aprovados para envio na conversa",
)
async def get_template_picker(
    thread_id: UUID | None = Query(default=None, alias="threadId"),
    matter_id: UUID | None = Query(default=None, alias="matterId"),
    search: str | None = Query(default=None, max_length=120),
    intent: WhatsAppTemplateIntent | None = Query(default=None),
    picker: WhatsAppPickerService = Depends(_get_picker_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    current_user_id: UUID = Depends(get_current_user_id),
    _: object = Depends(require_permission("mensagens", "view")),
) -> WhatsAppTemplatePickerRead:
    return await picker.picker(
        workspace_id,
        thread_id=thread_id,
        matter_id=matter_id,
        user_id=current_user_id,
        search=search,
        intent=intent,
    )


@router.get(
    "/variables",
    response_model=WhatsAppVariablesRead,
    summary="Resolver variáveis disponíveis para envio",
)
async def get_whatsapp_variables(
    contact_id: UUID | None = Query(default=None, alias="contactId"),
    matter_id: UUID | None = Query(default=None, alias="matterId"),
    picker: WhatsAppPickerService = Depends(_get_picker_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
) -> WhatsAppVariablesRead:
    return await picker.variables(workspace_id, contact_id=contact_id, matter_id=matter_id)


@router.post(
    "/quick-send",
    response_model=CampaignRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar e lançar campanha oficial para um contato",
)
async def quick_send(
    payload: WhatsAppQuickSendCreate,
    request: Request,
    svc: WhatsAppService = Depends(_get_service),
    picker: WhatsAppPickerService = Depends(_get_picker_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    current_user_id: UUID = Depends(get_current_user_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    _require_external_delivery("Envio rápido de template")
    idempotency_key = (
        payload.idempotency_key
        or request.headers.get("Idempotency-Key")
        or request.headers.get("idempotency-key")
    )
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "WA-QUICK-SEND-IDEMPOTENCY",
                "message": "Idempotency-Key é obrigatório para envio rápido.",
            },
        )
    idempotency_key = idempotency_key.strip()
    contact, _matter = await picker._resolve_context(
        workspace_id,
        thread_id=payload.thread_id,
        contact_id=payload.contact_id,
        matter_id=payload.matter_id,
        # S9: envio oficial nunca escolhe um contato arbitrário do caso.
        require_unambiguous_contact=True,
    )
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "WA-QUICK-SEND-404", "message": "Contato da conversa não encontrado."},
        )
    template_id = payload.template_id or payload.meta_template_id
    if template_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "WA-QUICK-SEND-001", "message": "Informe um template aprovado."},
        )
    connection_id = payload.connection_id or payload.whatsapp_connection_id
    if connection_id is None:
        connection_id = await picker.db.scalar(
            select(WhatsAppMetaTemplate.whatsapp_connection_id).where(
                WhatsAppMetaTemplate.id == template_id,
                WhatsAppMetaTemplate.workspace_id == workspace_id,
            )
        )
    if connection_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "WA-QUICK-SEND-002",
                "message": "Template não encontrado neste workspace.",
            },
        )
    body_bindings: dict[int, str] = {}
    header_bindings: dict[int, str] = {}
    button_bindings: dict[int, str] = {}
    for key, value in payload.bindings.items():
        digits = "".join(character for character in key if character.isdigit())
        if not digits:
            continue
        normalized_key = key.casefold()
        if normalized_key.startswith("header"):
            target = header_bindings
        elif normalized_key.startswith("button"):
            target = button_bindings
        else:
            target = body_bindings
        target[int(digits)] = value
    campaign_payload = CampaignCreate(
        whatsapp_connection_id=connection_id,
        title=payload.title or "Envio rápido de template",
        meta_template_id=template_id,
        header_parameters=payload.header_parameters
        or [
            header_bindings.get(index, "")
            for index in range(1, max(header_bindings, default=0) + 1)
        ],
        body_parameters=payload.body_parameters
        or [body_bindings.get(index, "") for index in range(1, max(body_bindings, default=0) + 1)],
        button_parameters=payload.button_parameters
        or [
            button_bindings.get(index, "")
            for index in range(1, max(button_bindings, default=0) + 1)
        ],
        audience_filter=CampaignAudienceFilter(
            contact_ids=[contact.id],
            include_crm_contacts=True,
            include_unlinked_contacts=True,
        ),
    )
    try:
        campaign = await svc.create_campaign(
            workspace_id,
            campaign_payload,
            current_user_id,
            idempotency_key=idempotency_key,
        )
        campaign = await svc.launch_campaign(
            workspace_id,
            campaign.id,
            CampaignLaunchRequest(dry_run=False),
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    return campaign if can_view_sensitive else mask_campaign(campaign)


@router.post(
    "/sequences/start",
    status_code=status.HTTP_201_CREATED,
    response_model=WhatsAppSequenceStartRead,
    summary="Iniciar sequência aprovada para um contato",
)
async def start_sequence(
    payload: WhatsAppSequenceStartCreate,
    request: Request,
    svc: WhatsAppService = Depends(_get_service),
    picker: WhatsAppPickerService = Depends(_get_picker_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    current_user_id: UUID = Depends(get_current_user_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    _require_external_delivery("Início de sequência")
    request_key = (
        payload.idempotency_key
        or request.headers.get("Idempotency-Key")
        or request.headers.get("idempotency-key")
    )
    if not request_key or not request_key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "WA-SEQUENCE-IDEMPOTENCY",
                "message": "Idempotency-Key é obrigatório para iniciar uma sequência.",
            },
        )
    if payload.idempotency_key != request_key:
        payload = payload.model_copy(update={"idempotency_key": request_key.strip()})
    campaign = await start_picker_sequence(
        svc.db,
        workspace_id=workspace_id,
        actor_user_id=current_user_id,
        payload=payload,
        picker=picker,
        campaign_service=svc.campaign_service,
    )
    first_recipient_id = await svc.db.scalar(
        select(WhatsAppCampaignRecipient.id)
        .where(WhatsAppCampaignRecipient.campaign_id == campaign.id)
        .order_by(WhatsAppCampaignRecipient.created_at.asc())
        .limit(1)
    )
    return {
        "campaignId": str(campaign.id),
        "flowId": str(campaign.follow_up_flow_id) if campaign.follow_up_flow_id else None,
        "firstRecipientId": str(first_recipient_id),
        "dispatchState": "accepted",
        "code": "WA-SEQUENCE-DISPATCH-ACCEPTED",
        "status": getattr(campaign.status, "value", campaign.status),
        "message": "Sequência iniciada; a primeira etapa foi encaminhada pela campanha oficial.",
    }


@router.get(
    "/commercial-charges",
    response_model=WhatsAppCommercialChargeSummaryRead,
    summary="Resumir cobranças comerciais de campanhas oficiais",
)
async def get_commercial_charges(
    campaign_id: UUID | None = Query(default=None, alias="campaignId"),
    svc: MetaCommercialRateService = Depends(_get_commercial_rate_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
    __: object = Depends(require_permission("finance", "view")),
) -> WhatsAppCommercialChargeSummaryRead:
    try:
        return await svc.summarize_workspace_campaigns(
            workspace_id=workspace_id,
            campaign_id=campaign_id,
        )
    except WhatsAppCommercialCampaignNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "WA-COMMERCIAL-CAMPAIGN-NOT-FOUND", "message": str(error)},
        ) from error


@router.post(
    "/campaigns",
    response_model=CampaignRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar campanha oficial",
)
async def create_campaign(
    payload: CampaignCreate,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    current_user_id: UUID = Depends(get_current_user_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    campaign = await svc.create_campaign(workspace_id, payload, current_user_id)
    return campaign if can_view_sensitive else mask_campaign(campaign)


@router.get(
    "/campaigns/{campaign_id}", response_model=CampaignRead, summary="Detalhar campanha oficial"
)
async def get_campaign(
    campaign_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    campaign = await svc.get_campaign(workspace_id, campaign_id)
    return campaign if can_view_sensitive else mask_campaign(campaign)


@router.patch(
    "/campaigns/{campaign_id}", response_model=CampaignRead, summary="Atualizar campanha oficial"
)
async def update_campaign(
    campaign_id: UUID,
    payload: CampaignUpdate,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    campaign = await svc.update_campaign(workspace_id, campaign_id, payload)
    return campaign if can_view_sensitive else mask_campaign(campaign)


@router.post(
    "/campaigns/{campaign_id}/launch",
    response_model=CampaignRead,
    summary="Dry-run ou lançamento da campanha",
)
async def launch_campaign(
    campaign_id: UUID,
    payload: CampaignLaunchRequest,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    if not payload.dry_run:
        _require_external_delivery("Lançamento de campanha")
    try:
        # V4 (backend): o clique explícito em "Lançar" fora de rascunho recebe 409;
        # o envio rápido e os flows mantêm o replay silencioso.
        campaign = await svc.launch_campaign(
            workspace_id, campaign_id, payload, reject_terminal_status=True
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    return campaign if can_view_sensitive else mask_campaign(campaign)


@router.post(
    "/campaigns/{campaign_id}/pause", response_model=CampaignRead, summary="Pausar campanha"
)
async def pause_campaign(
    campaign_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    campaign = await svc.pause_campaign(workspace_id, campaign_id)
    return campaign if can_view_sensitive else mask_campaign(campaign)


@router.post(
    "/campaigns/{campaign_id}/resume", response_model=CampaignRead, summary="Retomar campanha"
)
async def resume_campaign(
    campaign_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    _require_external_delivery("Retomada de campanha")
    campaign = await svc.resume_campaign(workspace_id, campaign_id)
    return campaign if can_view_sensitive else mask_campaign(campaign)


@router.post(
    "/campaigns/{campaign_id}/stop", response_model=CampaignRead, summary="Encerrar campanha"
)
async def stop_campaign(
    campaign_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    campaign = await svc.stop_campaign(workspace_id, campaign_id)
    return campaign if can_view_sensitive else mask_campaign(campaign)


@router.get(
    "/meta-templates",
    response_model=list[MetaTemplateRead],
    summary="Listar templates oficiais sincronizados",
)
async def list_meta_templates(
    connection_id: Optional[UUID] = Query(default=None),  # noqa: UP045
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
):
    return await svc.list_meta_templates(workspace_id, connection_id=connection_id)


@router.post(
    "/meta-templates/analyze",
    response_model=MetaTemplateAnalysisRead,
    summary="Analisar template com IA antes da submissão à Meta",
)
async def analyze_meta_template(
    payload: MetaTemplateAnalysisRequest,
    svc: MetaAIService = Depends(_get_meta_ai_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(get_current_auth_context),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    try:
        analysis = await svc.analyze_whatsapp_template(
            workspace_id=str(workspace_id),
            name=payload.name,
            category_intent=payload.category_intent,
            body=payload.body,
            sample_variables=payload.sample_variables,
            practice_area=payload.practice_area,
        )
        if payload.template_id is not None:
            actor_label = await resolve_human_actor_label(svc.db, auth_context.user_id)
            template = await svc.db.scalar(
                select(WhatsAppMetaTemplate).where(
                    WhatsAppMetaTemplate.id == payload.template_id,
                    WhatsAppMetaTemplate.workspace_id == workspace_id,
                )
            )
            if template is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "WA-TPL-003", "message": "Template não encontrado."},
                )
            template.raw_payload = {
                **(template.raw_payload or {}),
                "ai_analysis": analysis,
                "ai_analysis_at": datetime.now(UTC).isoformat(),
            }
            svc.db.add(template)
            svc.db.add(
                KlmAuditEvent(
                    workspace_id=workspace_id,
                    actor_user_id=auth_context.user_id,
                    actor_type="user",
                    actor_label=actor_label,
                    event_type="whatsapp_template_oab_risk_analyzed",
                    entity_type="whatsapp_meta_template",
                    entity_id=template.id,
                    summary=f"Risco ético do template '{template.name}' analisado.",
                    after_data={
                        "oabRisk": analysis.get("oab_risk", {}),
                        "metaCategorySuggested": analysis.get("meta_category_suggested"),
                    },
                )
            )
            await svc.db.commit()
        return analysis
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "WA-TEMPLATE-AI-001",
                "message": (
                    "A análise por IA não está disponível agora. Verifique a configuração "
                    "dos modelos e tente novamente."
                ),
            },
        ) from exc


@router.post(
    "/meta-templates/sync",
    response_model=list[MetaTemplateRead],
    summary="Sincronizar templates do WABA",
)
async def sync_meta_templates(
    payload: MetaTemplateSyncRequest,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    _require_external_delivery("Sincronização de templates da Meta")
    return await svc.sync_meta_templates(workspace_id, connection_id=payload.connection_id)


@router.get(
    "/meta-templates/unified",
    response_model=list[UnifiedTemplateRead],
    summary="Listar templates unificados (rascunhos locais + sincronizados da Meta)",
)
async def list_unified_templates(
    connection_id: UUID | None = Query(default=None),
    include_removed_source: bool = Query(default=False),
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
):
    return await svc.list_unified_templates(
        workspace_id,
        connection_id=connection_id,
        include_removed_source=include_removed_source,
    )


@router.delete(
    "/meta-templates/{template_id}/local-record",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remover registro local de template já enviado à Meta",
)
async def remove_local_template_record(
    template_id: UUID,
    confirm_history_loss: bool = Query(default=False),
    svc: WhatsAppService = Depends(_get_service),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(get_current_auth_context),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    label = await resolve_human_actor_label(db, auth_context.user_id)
    await svc.remove_local_template_record(
        workspace_id,
        template_id,
        confirm_history_loss=confirm_history_loss,
        actor_user_id=auth_context.user_id,
        actor_label=label,
    )
    return None


@router.post(
    "/meta-template-drafts",
    response_model=TemplateDraftRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar rascunho de template Meta",
)
async def create_template_draft(
    payload: TemplateDraftCreate,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    return await svc.create_template_draft(workspace_id, payload)


@router.get(
    "/meta-template-drafts/pending-internal-approval",
    response_model=list[UnifiedTemplateRead],
    summary="Listar templates aguardando aprovação interna antes da Meta",
)
async def list_pending_template_internal_approvals(
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("aprovacoes", "view")),
):
    return await svc.list_pending_template_approvals(workspace_id)


@router.get(
    "/meta-template-drafts/{template_id}",
    response_model=TemplateDraftRead,
    summary="Detalhar rascunho de template Meta",
)
async def get_template_draft(
    template_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
):
    return await svc.get_template_draft(workspace_id, template_id)


@router.patch(
    "/meta-template-drafts/{template_id}",
    response_model=TemplateDraftRead,
    summary="Editar rascunho de template Meta",
)
async def update_template_draft(
    template_id: UUID,
    payload: TemplateDraftUpdate,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    return await svc.update_template_draft(workspace_id, template_id, payload)


@router.delete(
    "/meta-template-drafts/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Excluir rascunho de template Meta",
)
async def delete_template_draft(
    template_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    await svc.delete_template_draft(workspace_id, template_id)
    return None


@router.post(
    "/meta-template-drafts/{template_id}/submit",
    response_model=TemplateSubmitResult,
    summary="Submeter rascunho de template à Meta (gated)",
)
async def submit_template_draft(
    template_id: UUID,
    payload: TemplateSubmitRequest | None = None,
    svc: WhatsAppService = Depends(_get_service),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(get_current_auth_context),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    label = await resolve_human_actor_label(db, auth_context.user_id)
    template = await db.scalar(
        select(WhatsAppMetaTemplate).where(
            WhatsAppMetaTemplate.id == template_id,
            WhatsAppMetaTemplate.workspace_id == workspace_id,
        )
    )
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "WA-TPL-003", "message": "Template não encontrado."},
        )
    builder = (template.raw_payload or {}).get("builder_components", {})
    body = str(builder.get("body_text") or "") if isinstance(builder, dict) else ""
    deterministic_risk = assess_oab_risk(body)
    saved_analysis = (template.raw_payload or {}).get("ai_analysis", {})
    saved_oab_risk = saved_analysis.get("oab_risk", {}) if isinstance(saved_analysis, dict) else {}
    high_risk = deterministic_risk["level"] == "high" or saved_oab_risk.get("level") == "high"
    if high_risk and not (payload and payload.oab_risk_acknowledged):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "WA-TPL-OAB-ACK-REQUIRED",
                "message": "Confirme ciência do alerta ético antes de submeter este template.",
                "oabRisk": {
                    "level": "high",
                    "signals": list(
                        dict.fromkeys(
                            [str(item) for item in saved_oab_risk.get("signals", [])]
                            + deterministic_risk["signals"]
                        )
                    ),
                    "requiresAcknowledgement": True,
                },
            },
        )
    if high_risk:
        acknowledged_at = datetime.now(UTC)
        acknowledgement = {
            "acknowledgedByUserId": str(auth_context.user_id),
            "acknowledgedByLabel": label,
            "acknowledgedAt": acknowledged_at.isoformat(),
            "riskLevel": "high",
        }
        template.raw_payload = {
            **(template.raw_payload or {}),
            "oab_risk_acknowledgement": acknowledgement,
        }
        db.add(template)
        db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=auth_context.user_id,
                actor_type="user",
                actor_label=label,
                event_type="whatsapp_template_oab_risk_acknowledged",
                entity_type="whatsapp_meta_template",
                entity_id=template.id,
                summary=f"{label} confirmou ciência do alerta ético do template '{template.name}'.",
                after_data=acknowledgement,
            )
        )
        await db.flush()
    return await svc.submit_template_draft(
        workspace_id,
        template_id,
        requested_by_user_id=auth_context.user_id,
        requester_role=auth_context.role,
        requester_label=label,
    )


@router.post(
    "/meta-template-drafts/{template_id}/approve-internal",
    response_model=TemplateSubmitResult,
    summary="Aprovar internamente a submissão de um template à Meta",
)
async def approve_template_internal_submission(
    template_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(get_current_auth_context),
    _: object = Depends(require_permission("aprovacoes", "approve")),
):
    label = await resolve_human_actor_label(db, auth_context.user_id)
    return await svc.approve_template_internal_submission(
        workspace_id,
        template_id,
        approver_user_id=auth_context.user_id,
        approver_label=label,
    )


@router.post(
    "/meta-template-drafts/{template_id}/clone-rejected",
    response_model=TemplateDraftRead,
    status_code=status.HTTP_201_CREATED,
    summary="Corrigir e reenviar: clonar template rejeitado como novo rascunho",
)
async def clone_rejected_template_as_draft(
    template_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    return await svc.clone_rejected_template_as_draft(workspace_id, template_id)


@router.get(
    "/utility-template-library",
    response_model=UtilityTemplateCatalogRead,
    summary="Acompanhar a biblioteca UTILITY provisionada na WABA do workspace",
)
async def list_utility_template_library(
    connection_id: UUID | None = Query(default=None),
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
):
    return await svc.list_utility_template_catalog(workspace_id, connection_id)


@router.post(
    "/utility-template-library/{catalog_key}/retry",
    response_model=UtilityTemplateCatalogRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reenfileirar um item rejeitado/falho da biblioteca UTILITY",
)
async def retry_utility_template_library_item(
    catalog_key: str,
    connection_id: UUID | None = Query(default=None),
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
):
    if catalog_key not in UTILITY_TEMPLATE_BY_KEY:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "WA-TPL-CATALOG-UNKNOWN",
                "message": "Template não pertence à biblioteca publicada.",
            },
        )
    return await svc.retry_utility_template_catalog_item(
        workspace_id,
        catalog_key,
        connection_id,
    )


@router.get(
    "/opt-ins", response_model=list[OptInRecordRead], summary="Listar opt-ins do canal WhatsApp"
)
async def list_opt_ins(
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    records = await svc.list_opt_ins(workspace_id)
    return records if can_view_sensitive else [mask_opt_in(item) for item in records]


@router.post("/opt-ins", response_model=OptInRecordRead, summary="Cadastrar ou atualizar opt-in")
async def upsert_opt_in(
    payload: OptInRecordCreate,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    record = await svc.upsert_opt_in(workspace_id, payload)
    return record if can_view_sensitive else mask_opt_in(record)


@router.get(
    "/suppressions",
    response_model=list[SuppressionEntryRead],
    summary="Listar telefones suprimidos",
)
async def list_suppressions(
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    records = await svc.list_suppressions(workspace_id)
    return records if can_view_sensitive else [mask_suppression(item) for item in records]


@router.post(
    "/suppressions", response_model=SuppressionEntryRead, summary="Cadastrar ou atualizar supressão"
)
async def upsert_suppression(
    payload: SuppressionEntryCreate,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    record = await svc.upsert_suppression(workspace_id, payload)
    return record if can_view_sensitive else mask_suppression(record)
