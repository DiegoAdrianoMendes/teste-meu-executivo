"""API coesa da operação KLM, sempre local até aprovação explícita."""

import hashlib
import logging
from datetime import datetime
from math import ceil
from typing import Literal
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import or_, select
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
    get_current_workspace_id,
    get_db,
    require_permission,
    require_workspace_admin,
)
from app.core.klm_runtime import get_runtime_status, local_bootstrap_allowed
from app.core.lead_privacy import (
    mask_acquisition_event,
    mask_agent_run,
    mask_audit_event,
    mask_email_ingest,
    mask_external_intent,
    mask_follow_up,
    mask_klm_funnel,
    mask_klm_message,
    mask_klm_thread,
    mask_legal_artifact,
    mask_process_monitoring,
)
from app.core.platform_rbac import has_platform_permission, is_platform_root
from app.db.post_commit import queue_funnel_automation_wakeup
from app.db.session import sqlite_message_write_lock
from app.models.entitlement import EntitlementKey
from app.models.klm_operations import (
    FollowUpStatus,
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    TechnicalRequestSeverity,
    TechnicalRequestStatus,
    ThreadStatus,
)
from app.models.matter import HealthSubtheme
from app.models.support_ticket import (
    SupportMessageVisibility,
    SupportTicket,
    SupportTicketPriority,
    SupportTicketQueue,
    SupportTicketSeverity,
    SupportTicketStatus,
    SupportTicketType,
)
from app.models.user import User
from app.schemas.identity import LocalOutboxMessageRead
from app.schemas.klm_operations import (
    AcquisitionEventCreate,
    AcquisitionEventRead,
    AdsFeedbackDispatchRead,
    AdsMeasurementConfigRead,
    AdsMeasurementConfigUpdate,
    AdvancedKpiReportRead,
    AgentAutonomyPolicyCreate,
    AgentAutonomyPolicyRead,
    AgentAutonomyPolicyUpdate,
    AgentAutonomySettingRead,
    AgentAutonomyUpdate,
    AgentDefinitionRead,
    AgentExecutionCreate,
    AgentExecutionRead,
    AgentHealthSimulationCreate,
    AgentRegistryRead,
    AgentRunRead,
    AgentStateUpdate,
    AgentToolsUpdate,
    AreaActivationUpdate,
    AreaBlueprintCreate,
    AreaBlueprintRead,
    AreaBlueprintUpdate,
    AudioRequestCreate,
    AudioRequestRead,
    AuditEventRead,
    CtwaBackfillReport,
    CtwaBackfillRequest,
    DashboardRead,
    EmailIngestRead,
    EmailIngestRequest,
    ExternalActionIntentCreate,
    ExternalActionIntentRead,
    FollowUpCreate,
    FollowUpRead,
    FollowUpSuppressRequest,
    FollowUpUpdate,
    FunnelMoveRead,
    FunnelMoveRequest,
    FunnelRead,
    InternalMessageFeedEntryRead,
    KpiRead,
    LegalArtifactSubmitRead,
    LegalArtifactVersionCreate,
    LegalArtifactVersionRead,
    LocalAgentBudgetE2ESeedRead,
    LocalAgentBudgetE2ESeedRequest,
    LocalDiagnosticsRead,
    LocalFinanceE2ESeedRead,
    LocalFinanceE2ESeedRequest,
    LocalSourceBindingRead,
    LocalVerificationCleanupRead,
    MatterFinancialProjectionRead,
    MatterFinancialProjectionUpdate,
    MessageEvidenceChainRead,
    MessageEvidenceExportRead,
    OmnichannelMessageCreate,
    OmnichannelMessageRead,
    OmnichannelThreadCreate,
    OmnichannelThreadRead,
    OmnichannelThreadUpdate,
    ProcessMonitoringCreate,
    ProcessMonitoringEventCreate,
    ProcessMonitoringRead,
    ProcessMonitoringUpdate,
    QualityRead,
    RuntimeStatusRead,
    TechnicalRequestCreate,
    TechnicalRequestRead,
    TechnicalRequestUpdate,
    ToolCatalogItemRead,
    TrackingConversionFeedbackPrepareRead,
    TrackingSummaryRead,
)
from app.schemas.messaging_cadence import (
    MessagingCadencePlanRead,
    MessagingCadencePreviewRequest,
    MessagingCadenceSettingsRead,
    MessagingCadenceSettingsUpdate,
)
from app.schemas.support_ticket import (
    SupportMessageCreate,
    SupportTechnicalContext,
    SupportTicketCreate,
    SupportTicketUpdate,
)
from app.schemas.user import TokenResponse
from app.services.agent_governance_service import AgentGovernanceViolation
from app.services.auth_service import AuthService
from app.services.autonomy_policy_service import AutonomyPolicyService
from app.services.configuration_service import ConfigConflictError
from app.services.conversation_ownership_service import ConversationOwnershipDenied
from app.services.entitlement_service import EntitlementEnforcementService, EntitlementError
from app.services.identity_outbox_service import LocalIdentityOutboxService
from app.services.klm_agent_service import KlmAgentService
from app.services.klm_local_seed_service import KlmLocalSeedService
from app.services.klm_local_verification_service import KlmLocalVerificationService
from app.services.klm_operations_service import FunnelVersionConflict, KlmOperationsService
from app.services.message_evidence_service import MessageEvidenceService
from app.services.messaging_cadence_service import MessagingCadenceService
from app.services.omnichannel_orchestration_service import (
    OmnichannelOrchestrationService,
)
from app.services.oss_capability_registry import local_runtime_source_binding
from app.services.support_ticket_service import (
    SupportTicketAccessError,
    SupportTicketConflictError,
    SupportTicketNotFoundError,
    SupportTicketService,
    SupportTicketValidationError,
)
from app.services.whatsapp.ctwa_backfill_service import CtwaBackfillService
from app.services.workspace_analytics_service import WorkspaceAnalyticsService
from app.websockets.manager import ws_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/klm", tags=["KLM Operations"])
_PRIVATE_MEDIA_METADATA_KEYS = ("audioUrl", "mediaUrl")
_LEGACY_TECHNICAL_STATUS_TO_SUPPORT = {
    TechnicalRequestStatus.received: SupportTicketStatus.new,
    TechnicalRequestStatus.analyzing: SupportTicketStatus.in_progress,
    TechnicalRequestStatus.patch_ready: SupportTicketStatus.validating,
    TechnicalRequestStatus.validating: SupportTicketStatus.validating,
    TechnicalRequestStatus.resolved: SupportTicketStatus.resolved,
    TechnicalRequestStatus.rejected: SupportTicketStatus.cancelled,
}
_SUPPORT_STATUS_TO_LEGACY_TECHNICAL = {
    SupportTicketStatus.new: TechnicalRequestStatus.received,
    SupportTicketStatus.triage: TechnicalRequestStatus.analyzing,
    SupportTicketStatus.in_progress: TechnicalRequestStatus.analyzing,
    SupportTicketStatus.waiting_customer: TechnicalRequestStatus.analyzing,
    SupportTicketStatus.waiting_internal: TechnicalRequestStatus.analyzing,
    SupportTicketStatus.validating: TechnicalRequestStatus.patch_ready,
    SupportTicketStatus.resolved: TechnicalRequestStatus.resolved,
    SupportTicketStatus.closed: TechnicalRequestStatus.resolved,
    SupportTicketStatus.cancelled: TechnicalRequestStatus.rejected,
}


def _legacy_technical_read(
    ticket: SupportTicket,
    *,
    status_override: TechnicalRequestStatus | None = None,
    resolution: str | None = None,
    evidence_override: list[dict] | None = None,
) -> TechnicalRequestRead:
    context = dict(ticket.technical_context or {})
    return TechnicalRequestRead(
        id=ticket.id,
        workspace_id=ticket.workspace_id,
        created_by_user_id=ticket.reporter_user_id,
        assigned_to_user_id=ticket.assigned_to_user_id,
        title=ticket.subject,
        description=ticket.description,
        route=ticket.route,
        severity=TechnicalRequestSeverity(ticket.severity.value),
        status=status_override or _SUPPORT_STATUS_TO_LEGACY_TECHNICAL[ticket.status],
        evidence=(
            evidence_override
            if evidence_override is not None
            else ([{"kind": "technical_context", **context}] if context else [])
        ),
        resolution=resolution,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )


async def _legacy_technical_ticket(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    request_id: UUID,
) -> SupportTicket | None:
    return await db.scalar(
        select(SupportTicket).where(
            SupportTicket.workspace_id == workspace_id,
            or_(
                SupportTicket.id == request_id,
                SupportTicket.legacy_technical_request_id == request_id,
            ),
            SupportTicket.queue == SupportTicketQueue.technical,
        )
    )


async def _require_saas_area_manager(db: AsyncSession, auth: AuthContext) -> User:
    actor = await db.scalar(select(User).where(User.id == auth.user_id))
    allowed = bool(
        actor
        and (
            is_platform_root(actor)
            or has_platform_permission(actor, "workspace_configuration", "manage")
        )
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AREA_UNLOCK_RESTRICTED",
                "message": "Somente a equipe autorizada do SaaS pode desbloquear áreas.",
            },
        )
    return actor


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, AgentGovernanceViolation):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code, "message": str(error)},
        )
    if isinstance(error, ConversationOwnershipDenied):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONVERSATION_OWNERSHIP_DENIED", "message": str(error)},
        )
    if isinstance(error, LookupError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


def _validate_private_media_metadata(payload: OmnichannelMessageCreate) -> None:
    expected = urlsplit(settings.PAPERCLIP_PUBLIC_URL.rstrip("/"))
    expected_origin = (expected.scheme.lower(), expected.hostname, expected.port)
    for key in _PRIVATE_MEDIA_METADATA_KEYS:
        value = payload.message_metadata.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Metadado {key} precisa apontar para mídia privada do KLM.",
            )
        try:
            parsed = urlsplit(value.strip())
            has_absolute_origin = bool(parsed.scheme or parsed.netloc)
            candidate_origin = (parsed.scheme.lower(), parsed.hostname, parsed.port)
            query = parse_qs(parsed.query, keep_blank_values=True)
        except ValueError:
            parsed = urlsplit("")
            has_absolute_origin = True
            candidate_origin = ("", None, None)
            query = {}
        if (
            parsed.path != "/api/v1/local-storage/download"
            or parsed.fragment
            or parsed.username
            or parsed.password
            or set(query) != {"ticket"}
            or len(query["ticket"]) != 1
            or not query["ticket"][0]
            or (has_absolute_origin and candidate_origin != expected_origin)
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Metadado {key} rejeitado: use somente um ticket temporário "
                    "do armazenamento privado do KLM."
                ),
            )


def _mask_tracking_summary(data: dict) -> dict:
    masked = {**data, "feedback_events": []}
    private_fields = {
        "account_id",
        "account_name",
        "campaign_id",
        "campaign_name",
        "ad_set_id",
        "ad_set_name",
        "ad_id",
        "ad_name",
        "creative_id",
        "creative_name",
        "form_id",
        "form_name",
        "landing_page_url",
        "source_url",
        "provider_console_url",
        "sample_journey_id",
        "sample_thread_id",
        "sample_matter_id",
    }
    masked["attribution_rows"] = [
        {key: None if key in private_fields else value for key, value in row.items()}
        for row in data.get("attribution_rows", [])
    ]
    return masked


async def _authenticated_message_payload(
    db: AsyncSession,
    auth: AuthContext,
    payload: OmnichannelMessageCreate,
) -> OmnichannelMessageCreate:
    _validate_private_media_metadata(payload)
    if payload.author_type == MessageAuthorType.human:
        if payload.direction not in {MessageDirection.outbound, MessageDirection.internal}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Usuários autenticados não podem registrar mensagens como recebidas.",
            )
        if (
            payload.direction == MessageDirection.internal
            and payload.channel != OmnichannelChannel.internal
        ) or (
            payload.direction == MessageDirection.outbound
            and payload.channel == OmnichannelChannel.internal
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Canal e direção da mensagem humana são incompatíveis.",
            )
        actor_name = await db.scalar(select(User.name).where(User.id == auth.user_id))
        if not actor_name:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Usuário autenticado não encontrado.",
            )
        return payload.model_copy(update={"author_label": actor_name})

    local_fixture_allowed = (
        settings.uses_local_infrastructure
        and settings.is_local_only
        and settings.KLM_LOCAL_BOOTSTRAP_ENABLED
    )
    if not local_fixture_allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Mensagens de cliente, agente ou sistema entram somente pelos canais "
                "e serviços internos autenticados."
            ),
        )
    return payload.model_copy(
        update={
            "message_metadata": {
                **payload.message_metadata,
                "localSimulation": True,
                "externalActionsExecuted": 0,
            }
        }
    )


def _mask_agent_execution(value: AgentExecutionRead) -> AgentExecutionRead:
    return value.model_copy(update={"runs": [mask_agent_run(item) for item in value.runs]})


@router.get("/runtime", response_model=RuntimeStatusRead, summary="Gate local da operação")
async def runtime_status() -> RuntimeStatusRead:
    return RuntimeStatusRead.model_validate(get_runtime_status())


@router.post(
    "/diagnostics",
    response_model=LocalDiagnosticsRead,
    summary="Executar diagnóstico real do runtime local",
)
async def run_local_diagnostics(
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("admin", "view")),
    db: AsyncSession = Depends(get_db),
) -> LocalDiagnosticsRead:
    result = await KlmOperationsService(db).run_local_diagnostics(
        workspace_id,
        auth.user_id,
    )
    return LocalDiagnosticsRead.model_validate(result)


@router.get(
    "/messaging-cadence/settings",
    response_model=MessagingCadenceSettingsRead,
    summary="Ler política de cadência humana dos agentes",
)
async def get_messaging_cadence_settings(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("configuracoes", "view")),
    db: AsyncSession = Depends(get_db),
) -> MessagingCadenceSettingsRead:
    return await MessagingCadenceService(db).get_settings(workspace_id)


@router.put(
    "/messaging-cadence/settings",
    response_model=MessagingCadenceSettingsRead,
    summary="Atualizar política de cadência humana dos agentes",
)
async def update_messaging_cadence_settings(
    payload: MessagingCadenceSettingsUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("configuracoes", "edit")),
    db: AsyncSession = Depends(get_db),
) -> MessagingCadenceSettingsRead:
    result = await MessagingCadenceService(db).update_settings(
        workspace_id,
        payload,
        actor_user_id=auth.user_id,
        actor_label="Administrador do workspace",
    )
    await db.commit()
    return result


@router.post(
    "/messaging-cadence/preview",
    response_model=MessagingCadencePlanRead,
    summary="Simular atraso e digitação sem enviar mensagem",
)
async def preview_messaging_cadence(
    payload: MessagingCadencePreviewRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("configuracoes", "view")),
    db: AsyncSession = Depends(get_db),
) -> MessagingCadencePlanRead:
    return await MessagingCadenceService(db).plan(
        workspace_id,
        text=payload.text,
        idempotency_key=payload.idempotency_key,
        now=payload.now,
        count_recent_messages=False,
    )


@router.post(
    "/local/session",
    response_model=TokenResponse,
    summary="Criar sessão idempotente apenas no ambiente local",
)
async def local_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    client_host = request.client.host if request.client else ""
    if not local_bootstrap_allowed(client_host):
        raise HTTPException(status_code=404, detail="Recurso indisponível.")
    user, workspace = await KlmLocalSeedService(db).ensure()
    auth_service = AuthService(db)
    loaded_user = await auth_service.get_user_by_id(user.id)
    if not loaded_user:
        raise HTTPException(status_code=500, detail="Sessão local não pôde ser criada.")
    return await auth_service.issue_new_session(
        loaded_user,
        preferred_workspace_id=workspace.id,
        request=request,
    )


@router.post(
    "/local/platform-session",
    response_model=TokenResponse,
    summary="Criar sessão local exclusiva da Central Blackbox",
)
async def local_platform_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    client_host = request.client.host if request.client else ""
    if not local_bootstrap_allowed(client_host):
        raise HTTPException(status_code=404, detail="Recurso indisponível.")
    platform_user = await KlmLocalSeedService(db).ensure_platform_identity()
    auth_service = AuthService(db)
    loaded_user = await auth_service.get_user_by_id(platform_user.id)
    if not loaded_user:
        raise HTTPException(
            status_code=500,
            detail="Sessão local da Blackbox não pôde ser criada.",
        )
    return await auth_service.issue_new_session(
        loaded_user,
        preferred_workspace_id=None,
        request=request,
    )


@router.post(
    "/local/platform-session/{role_package}",
    response_model=TokenResponse,
    summary="Criar sessão local de uma função interna da Central Blackbox",
)
async def local_platform_role_session(
    role_package: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    client_host = request.client.host if request.client else ""
    if not local_bootstrap_allowed(client_host):
        raise HTTPException(status_code=404, detail="Recurso indisponível.")
    try:
        platform_user = await KlmLocalSeedService(db).ensure_platform_staff_identity(role_package)
    except LookupError as error:
        raise HTTPException(
            status_code=404,
            detail="Função interna local não encontrada.",
        ) from error
    auth_service = AuthService(db)
    loaded_user = await auth_service.get_user_by_id(platform_user.id)
    if not loaded_user:
        raise HTTPException(
            status_code=500,
            detail="Sessão local da função interna não pôde ser criada.",
        )
    return await auth_service.issue_new_session(
        loaded_user,
        preferred_workspace_id=None,
        request=request,
    )


@router.get(
    "/local/source-binding",
    response_model=LocalSourceBindingRead,
    summary="Confirmar o código carregado somente no runtime local",
)
async def read_local_source_binding(
    request: Request,
    _: AuthContext = Depends(require_permission("configuracoes", "view")),
) -> LocalSourceBindingRead:
    client_host = request.client.host if request.client else ""
    if not local_bootstrap_allowed(client_host):
        raise HTTPException(status_code=404, detail="Recurso indisponível.")
    return LocalSourceBindingRead.model_validate(local_runtime_source_binding())


@router.get(
    "/local/invitations/{invitation_id}/delivery",
    response_model=LocalOutboxMessageRead,
    summary="Ler somente um convite pendente do workspace no runtime local",
)
async def read_local_workspace_invitation(
    invitation_id: UUID,
    request: Request,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("users", "manage")),
    db: AsyncSession = Depends(get_db),
) -> LocalOutboxMessageRead:
    client_host = request.client.host if request.client else ""
    if not local_bootstrap_allowed(client_host):
        raise HTTPException(status_code=404, detail="Recurso indisponível.")
    result = await LocalIdentityOutboxService(db).read_workspace_invitation(
        invitation_id,
        workspace_id=workspace_id,
        actor_user_id=auth.user_id,
    )
    await db.commit()
    return result


@router.delete(
    "/local/verification-artifacts",
    response_model=LocalVerificationCleanupRead,
    summary="Limpar somente artefatos transitórios do QA local",
)
async def cleanup_local_verification_artifacts(
    request: Request,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("configuracoes", "edit")),
    db: AsyncSession = Depends(get_db),
) -> LocalVerificationCleanupRead:
    client_host = request.client.host if request.client else ""
    if not local_bootstrap_allowed(client_host):
        raise HTTPException(status_code=404, detail="Recurso indisponível.")
    result = await KlmLocalVerificationService(db).cleanup(
        workspace_id,
        actor_user_id=auth.user_id,
    )
    await db.commit()
    return LocalVerificationCleanupRead.model_validate(result)


@router.post(
    "/local/agent-budget-e2e-seed",
    response_model=LocalAgentBudgetE2ESeedRead,
    summary="Consumir orçamento de agente somente no QA local",
)
async def seed_local_agent_budget_e2e(
    body: LocalAgentBudgetE2ESeedRequest,
    request: Request,
    intent: str | None = Header(default=None, alias="X-KLM-Local-Seed-Intent"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalAgentBudgetE2ESeedRead:
    client_host = request.client.host if request.client else ""
    if not local_bootstrap_allowed(client_host) or intent != body.run_id:
        raise HTTPException(status_code=404, detail="Recurso indisponível.")
    try:
        result = await KlmLocalVerificationService(db).seed_agent_budget_e2e(
            workspace_id,
            deployment_id=body.deployment_id,
            run_id=body.run_id,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Deployment não encontrado.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await db.commit()
    return LocalAgentBudgetE2ESeedRead.model_validate(result)


@router.post(
    "/local/finance-e2e-seed",
    response_model=LocalFinanceE2ESeedRead,
    summary="Criar massa financeira isolada somente no QA local",
)
async def seed_local_finance_e2e(
    body: LocalFinanceE2ESeedRequest,
    request: Request,
    intent: str | None = Header(default=None, alias="X-KLM-Local-Seed-Intent"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalFinanceE2ESeedRead:
    client_host = request.client.host if request.client else ""
    if not local_bootstrap_allowed(client_host) or intent != body.run_id:
        raise HTTPException(status_code=404, detail="Recurso indisponível.")
    result = await KlmLocalVerificationService(db).seed_finance_e2e(
        workspace_id,
        run_id=body.run_id,
        record_count=body.record_count,
    )
    await db.commit()
    return LocalFinanceE2ESeedRead.model_validate(result)


@router.delete(
    "/local/finance-e2e-seed/{run_id}",
    summary="Remover massa financeira isolada somente no QA local",
)
async def cleanup_local_finance_e2e(
    run_id: str,
    request: Request,
    intent: str | None = Header(default=None, alias="X-KLM-Local-Seed-Intent"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int | str]:
    client_host = request.client.host if request.client else ""
    if (
        not local_bootstrap_allowed(client_host)
        or intent != run_id
        or not 4 <= len(run_id) <= 40
        or not all(character.isalnum() or character in "_-" for character in run_id)
    ):
        raise HTTPException(status_code=404, detail="Recurso indisponível.")
    removed = await KlmLocalVerificationService(db).cleanup_finance_e2e(workspace_id, run_id=run_id)
    await db.commit()
    return {"runId": run_id, "artifactsRemoved": removed}


@router.get("/dashboard", response_model=DashboardRead, summary="Read model executivo")
async def dashboard(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("dashboard", "view")),
    db: AsyncSession = Depends(get_db),
) -> DashboardRead:
    data = await KlmOperationsService(db).dashboard(workspace_id)
    return DashboardRead.model_validate(data)


@router.get("/kpis", response_model=KpiRead, summary="KPIs calculados no banco local")
async def kpis(
    period_days: int | None = Query(default=None, ge=1, le=3650),
    health_subtheme: HealthSubtheme | None = Query(default=None),
    legal_area_key: str | None = Query(default=None, max_length=80),
    legal_product_key: str | None = Query(default=None, max_length=100),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("relatorios", "view")),
    db: AsyncSession = Depends(get_db),
) -> KpiRead:
    return KpiRead.model_validate(
        await KlmOperationsService(db).kpis(
            workspace_id,
            period_days=period_days,
            health_subtheme=health_subtheme,
            legal_area_key=legal_area_key,
            legal_product_key=legal_product_key,
        )
    )


@router.get(
    "/kpis/advanced",
    response_model=AdvancedKpiReportRead,
    summary="Relatório avançado multiárea do workspace",
)
async def advanced_kpis(
    period_days: int = Query(default=30, ge=1, le=365),
    legal_area_key: str | None = Query(default=None, max_length=80),
    legal_product_key: str | None = Query(default=None, max_length=100),
    channel: str | None = Query(default=None, max_length=100),
    responsible_user_id: UUID | None = Query(default=None),
    stage: str | None = Query(default=None, max_length=80),
    category: str | None = Query(default=None, max_length=80),
    query: str | None = Query(default=None, max_length=160),
    only_with_data: bool = Query(default=False),
    sort_by: Literal["label", "category", "value"] = Query(default="category"),
    sort_direction: Literal["asc", "desc"] = Query(default="asc"),
    limit: int = Query(default=500, ge=1, le=1000),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("relatorios", "view")),
    db: AsyncSession = Depends(get_db),
) -> AdvancedKpiReportRead:
    return AdvancedKpiReportRead.model_validate(
        await WorkspaceAnalyticsService(db).advanced_report(
            workspace_id=workspace_id,
            period_days=period_days,
            legal_area_key=legal_area_key,
            legal_product_key=legal_product_key,
            channel=channel,
            responsible_user_id=responsible_user_id,
            stage=stage,
            category=category,
            query=query,
            only_with_data=only_with_data,
            sort_by=sort_by,
            sort_direction=sort_direction,
            limit=limit,
        )
    )


@router.get("/funnel", response_model=FunnelRead, summary="Funil comercial de saúde")
async def funnel(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("kanban", "view")),
) -> FunnelRead:
    response = FunnelRead.model_validate(await KlmOperationsService(db).funnel(workspace_id))
    return response if can_view_sensitive else mask_klm_funnel(response)


@router.patch(
    "/matters/{matter_id}/financial-projection",
    response_model=MatterFinancialProjectionRead,
    summary="Versionar projeção econômica local do caso",
)
async def update_matter_financial_projection(
    matter_id: UUID,
    payload: MatterFinancialProjectionUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("kanban", "edit")),
    # Versionar fee/percentual/valor é ação financeira — kanban:edit sozinho
    # deixaria qualquer papel com edição de kanban (ex.: legal_manager,
    # operator) mexer em projeção econômica sem nenhuma permissão de
    # finanças. Exige as duas (RBAC dedicado, ver app/core/rbac.py).
    __: AuthContext = Depends(require_permission("finance", "manage")),
    db: AsyncSession = Depends(get_db),
) -> MatterFinancialProjectionRead:
    try:
        data = await KlmOperationsService(db).update_matter_financial_projection(
            workspace_id, matter_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return MatterFinancialProjectionRead.model_validate(data)


@router.patch(
    "/funnel/{matter_id}/stage",
    response_model=FunnelMoveRead,
    summary="Mover etapa e acionar a orquestração interna do CRM",
)
async def move_funnel_stage(
    matter_id: UUID,
    payload: FunnelMoveRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("kanban", "edit")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        move = await KlmOperationsService(db).move_funnel_matter(
            workspace_id,
            matter_id,
            auth.user_id,
            payload.column_id,
            payload.micro_status,
            str(payload.operation_id),
            payload.expected_board_version,
            payload.source_column_id,
            payload.before_id,
            payload.after_id,
            payload.position,
        )
    except FunnelVersionConflict as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "funnel_version_conflict",
                "message": str(error),
                "currentBoardVersion": error.current_version,
            },
        ) from error
    except ValueError as error:
        raise _http_error(error) from error
    if not move:
        raise HTTPException(status_code=404, detail="Caso não encontrado.")
    await db.commit()
    if not move.deduplicated:
        await ws_manager.broadcast_board_update(
            "CARD_MOVED",
            {
                "matter_id": str(move.matter_id),
                "column_id": move.column_id.value,
                "micro_status": move.micro_status.value,
                "card_order": move.card_order,
                "board_version": move.board_version,
                "operation_id": move.operation_id,
                "source": "klm_funnel",
            },
            workspace_id=workspace_id,
        )
    agents_triggered: list[str] = []
    agent_run_ids: list[UUID] = []
    approval_id: UUID | None = None
    automation_status: Literal["queued", "completed", "waiting_human", "failed"] = "queued"
    automation_message = "A orquestração foi enfileirada para execução assíncrona."
    specialist_by_stage = {
        "triagem_sdr": "sdr",
        "documentos": "juridico",
        "reuniao": "secretaria",
        "proposta": "closer",
        "contrato": "closer",
        "pagamento": "financeiro",
    }
    transition_agents = ["crm", "orquestrador"]
    specialist = specialist_by_stage.get(move.column_id.value)
    if specialist and specialist not in transition_agents:
        transition_agents.append(specialist)
    if not move.deduplicated:
        agents_triggered.extend(transition_agents)
        await KlmOperationsService(db).record_audit(
            workspace_id=workspace_id,
            actor_user_id=auth.user_id,
            actor_type="system",
            actor_label="Orquestração do CRM",
            event_type="funnel_move_automation_requested",
            entity_type="matter",
            entity_id=move.matter_id,
            summary="Automação pós-movimento enfileirada.",
            correlation_id=move.operation_id,
            after_data={
                "columnId": move.column_id.value,
                "agentsTriggered": transition_agents,
                "status": "queued",
            },
        )
        queue_funnel_automation_wakeup(
            db, workspace_id=workspace_id, operation_id=move.operation_id
        )
    await db.commit()
    automation_message = "A orquestração foi enfileirada para execução assíncrona."
    return FunnelMoveRead(
        matter_id=move.matter_id,
        column_id=move.column_id,
        micro_status=move.micro_status,
        card_order=move.card_order,
        board_version=move.board_version,
        operation_id=UUID(move.operation_id),
        agent_dispatched=bool(agents_triggered),
        deduplicated=move.deduplicated,
        automation_status=automation_status,
        agents_triggered=agents_triggered,
        agent_run_ids=agent_run_ids,
        approval_id=approval_id,
        automation_message=automation_message,
    )


@router.get("/quality", response_model=QualityRead, summary="Qualidade operacional local")
async def quality(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("agents", "view")),
    db: AsyncSession = Depends(get_db),
) -> QualityRead:
    return QualityRead.model_validate(await KlmOperationsService(db).quality(workspace_id))


@router.post(
    "/audio/requests",
    response_model=AudioRequestRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar insistência por áudio sem chamar provedor",
)
async def create_audio_request(
    payload: AudioRequestCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> AudioRequestRead:
    enforcer = EntitlementEnforcementService(db)
    reservation = None
    try:
        if (
            payload.transcription_enabled
            or payload.summary_enabled
            or payload.voice_reply_requested
        ):
            reservation = await enforcer.reserve_metered(
                workspace_id=workspace_id,
                entitlement=EntitlementKey.audio_minutes,
                quantity=ceil(payload.duration_seconds / 60),
                idempotency_key=f"audio:{payload.idempotency_key}",
            )
        data = await KlmOperationsService(db).record_audio_request(
            workspace_id, auth.user_id, payload
        )
        await enforcer.commit_metered(
            workspace_id=workspace_id,
            reservation=reservation,
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    response = AudioRequestRead.model_validate(data)
    if can_view_sensitive:
        return response
    return response.model_copy(
        update={
            "message": mask_klm_message(response.message),
            "external_intent": mask_external_intent(response.external_intent)
            if response.external_intent
            else None,
        }
    )


@router.get("/tools", response_model=list[ToolCatalogItemRead], summary="Catálogo de tools")
async def tools_catalog() -> list[ToolCatalogItemRead]:
    return [
        ToolCatalogItemRead.model_validate(item) for item in KlmOperationsService.tool_catalog()
    ]


@router.get(
    "/agents",
    response_model=AgentRegistryRead,
    summary="Registro versionado dos agentes API-first",
)
async def agent_registry(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("agents", "view")),
    db: AsyncSession = Depends(get_db),
) -> AgentRegistryRead:
    return AgentRegistryRead.model_validate(await KlmAgentService(db).registry(workspace_id))


@router.patch(
    "/agents/{agent_key}/state",
    response_model=AgentDefinitionRead,
    summary="Pausar ou retomar agente com auditoria",
)
async def update_agent_state(
    agent_key: str,
    payload: AgentStateUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("agents", "manage")),
    db: AsyncSession = Depends(get_db),
) -> AgentDefinitionRead:
    try:
        data = await KlmAgentService(db).set_state(
            workspace_id, auth.user_id, agent_key, payload.state, payload.reason
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    except LookupError as error:
        raise _http_error(error) from error
    return AgentDefinitionRead.model_validate(data)


@router.patch(
    "/agents/{agent_key}/tools",
    response_model=AgentDefinitionRead,
    summary="Atualizar ferramentas permitidas do agente com auditoria",
)
async def update_agent_tools(
    agent_key: str,
    payload: AgentToolsUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("agents", "manage")),
    db: AsyncSession = Depends(get_db),
) -> AgentDefinitionRead:
    try:
        data = await KlmAgentService(db).set_tools(
            workspace_id=workspace_id,
            user_id=auth.user_id,
            agent_key=agent_key,
            tools=payload.tools,
            reason=payload.reason,
            expected_etag=payload.expected_etag,
        )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except ConfigConflictError as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "AGENT_TOOLS_STALE", "message": str(error)},
        ) from error
    except LookupError as error:
        await db.rollback()
        raise _http_error(error) from error
    return AgentDefinitionRead.model_validate(data)


@router.get(
    "/agents/autonomy",
    response_model=list[AgentAutonomySettingRead],
    summary="Configuração de autonomia (human-in-the-loop) por agente do workspace",
)
async def list_agent_autonomy_settings(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AgentAutonomySettingRead]:
    data = await KlmAgentService(db).get_agent_autonomy_settings(workspace_id)
    return [AgentAutonomySettingRead.model_validate(item) for item in data]


@router.put(
    "/agents/{agent_key}/autonomy",
    response_model=AgentAutonomySettingRead,
    summary=(
        "Definir modo de autonomia e banda de negociação pré-aprovada do agente, com auditoria"
    ),
)
async def update_agent_autonomy(
    agent_key: str,
    payload: AgentAutonomyUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> AgentAutonomySettingRead:
    try:
        data = await KlmAgentService(db).set_agent_autonomy(
            workspace_id=workspace_id,
            user_id=auth.user_id,
            agent_key=agent_key,
            autonomy_mode=payload.autonomy_mode,
            negotiation_floor_pct=payload.negotiation_floor_pct,
            negotiation_band_pct=payload.negotiation_band_pct,
            reason=payload.reason,
            expected_etag=payload.expected_etag,
        )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except ConfigConflictError as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "AGENT_AUTONOMY_STALE", "message": str(error)},
        ) from error
    except LookupError as error:
        await db.rollback()
        raise _http_error(error) from error
    return AgentAutonomySettingRead.model_validate(data)


# ── Plano Mestre §14 — políticas de autonomia A0-A4 por evento (admin) ──
# Distinto do modo HIL simples acima (/agents/{agent_key}/autonomy): esta é
# a regra fina por evento (área/cliente/carteira/agente/ação), ver
# `AutonomyPolicyService`.


@router.get(
    "/agents/autonomy/policies",
    response_model=list[AgentAutonomyPolicyRead],
    summary="Listar políticas de autonomia por evento (Plano Mestre §14)",
)
async def list_agent_autonomy_policies(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AgentAutonomyPolicyRead]:
    rows = await AutonomyPolicyService(db).list_policies(workspace_id)
    return [AgentAutonomyPolicyRead.model_validate(row) for row in rows]


@router.post(
    "/agents/autonomy/policies",
    response_model=AgentAutonomyPolicyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar política de autonomia por evento (Plano Mestre §14)",
)
async def create_agent_autonomy_policy(
    payload: AgentAutonomyPolicyCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> AgentAutonomyPolicyRead:
    try:
        policy = await AutonomyPolicyService(db).create_policy(
            workspace_id=workspace_id,
            user_id=auth.user_id,
            payload=payload.model_dump(),
        )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except LookupError as error:
        await db.rollback()
        raise _http_error(error) from error
    return AgentAutonomyPolicyRead.model_validate(policy)


@router.patch(
    "/agents/autonomy/policies/{policy_id}",
    response_model=AgentAutonomyPolicyRead,
    summary="Atualizar política de autonomia por evento (Plano Mestre §14)",
)
async def update_agent_autonomy_policy(
    policy_id: UUID,
    payload: AgentAutonomyPolicyUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> AgentAutonomyPolicyRead:
    try:
        policy = await AutonomyPolicyService(db).update_policy(
            workspace_id=workspace_id,
            policy_id=policy_id,
            user_id=auth.user_id,
            payload=payload.model_dump(exclude={"reason"}),
        )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except LookupError as error:
        await db.rollback()
        raise _http_error(error) from error
    return AgentAutonomyPolicyRead.model_validate(policy)


@router.delete(
    "/agents/autonomy/policies/{policy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remover política de autonomia por evento (Plano Mestre §14)",
)
async def delete_agent_autonomy_policy(
    policy_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        await AutonomyPolicyService(db).delete_policy(
            workspace_id=workspace_id,
            policy_id=policy_id,
            user_id=auth.user_id,
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise _http_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/agents/runs",
    response_model=list[AgentRunRead],
    summary="Execuções auditadas dos agentes",
)
async def list_agent_runs(
    matter_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("agents", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> list[AgentRunRead]:
    runs = await KlmAgentService(db).list_runs(workspace_id, matter_id, limit)
    return runs if can_view_sensitive else [mask_agent_run(item) for item in runs]


@router.post(
    "/agents/runs",
    response_model=AgentExecutionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Executar agente no runtime API-first aprovado",
)
async def execute_agent_locally(
    payload: AgentExecutionCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("agents", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> AgentExecutionRead:
    try:
        data = await KlmAgentService(db).execute(
            workspace_id=workspace_id,
            user_id=auth.user_id,
            agent_key=payload.agent_key,
            matter_id=payload.matter_id,
            thread_id=payload.thread_id,
            request_human=payload.request_human,
            instructions=payload.instructions,
            idempotency_key=payload.idempotency_key,
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    response = AgentExecutionRead.model_validate(data)
    return response if can_view_sensitive else _mask_agent_execution(response)


@router.post(
    "/agents/simulations/health-intake",
    response_model=AgentExecutionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Simular triagem completa sem custo de modelo",
)
async def simulate_health_intake(
    payload: AgentHealthSimulationCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("agents", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> AgentExecutionRead:
    try:
        data = await KlmAgentService(db).simulate_health_intake(
            workspace_id=workspace_id,
            user_id=auth.user_id,
            matter_id=payload.matter_id,
            thread_id=payload.thread_id,
            idempotency_key=payload.idempotency_key,
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    response = AgentExecutionRead.model_validate(data)
    return response if can_view_sensitive else _mask_agent_execution(response)


@router.post(
    "/agents/simulations/intake",
    response_model=AgentExecutionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Simular a equipe da área e do produto sem custo de modelo",
)
async def simulate_intake(
    payload: AgentHealthSimulationCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("agents", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> AgentExecutionRead:
    try:
        data = await KlmAgentService(db).simulate_intake(
            workspace_id=workspace_id,
            user_id=auth.user_id,
            matter_id=payload.matter_id,
            thread_id=payload.thread_id,
            idempotency_key=payload.idempotency_key,
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    response = AgentExecutionRead.model_validate(data)
    return response if can_view_sensitive else _mask_agent_execution(response)


@router.get("/areas", response_model=list[AreaBlueprintRead], summary="Registry de áreas")
async def list_areas(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("configuracoes", "view")),
    db: AsyncSession = Depends(get_db),
) -> list[AreaBlueprintRead]:
    return await KlmOperationsService(db).list_areas(workspace_id)


@router.post(
    "/areas",
    response_model=AreaBlueprintRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar blueprint local",
)
async def create_area(
    payload: AreaBlueprintCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("configuracoes", "edit")),
    db: AsyncSession = Depends(get_db),
) -> AreaBlueprintRead:
    try:
        return await KlmOperationsService(db).create_area(workspace_id, auth.user_id, payload)
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error


@router.patch("/areas/{area_id}", response_model=AreaBlueprintRead, summary="Atualizar blueprint")
async def update_area(
    area_id: UUID,
    payload: AreaBlueprintUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("configuracoes", "edit")),
    db: AsyncSession = Depends(get_db),
) -> AreaBlueprintRead:
    try:
        area = await KlmOperationsService(db).update_area(
            workspace_id, area_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    if not area:
        raise HTTPException(status_code=404, detail="Área não encontrada.")
    return area


@router.post(
    "/areas/{area_id}/materialize",
    response_model=AreaBlueprintRead,
    summary="Materializar blueprint genérico somente no ambiente local",
)
async def materialize_area(
    area_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("configuracoes", "edit")),
    db: AsyncSession = Depends(get_db),
) -> AreaBlueprintRead:
    try:
        area = await KlmOperationsService(db).materialize_area(workspace_id, area_id, auth.user_id)
    except ValueError as error:
        raise _http_error(error) from error
    if not area:
        raise HTTPException(status_code=404, detail="Área não encontrada.")
    return area


@router.post(
    "/areas/{area_id}/validate",
    response_model=AreaBlueprintRead,
    summary="Validar blueprint sem ativar",
)
async def validate_area(
    area_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("configuracoes", "edit")),
    db: AsyncSession = Depends(get_db),
) -> AreaBlueprintRead:
    try:
        area = await KlmOperationsService(db).validate_area(workspace_id, area_id, auth.user_id)
    except ValueError as error:
        raise _http_error(error) from error
    if not area:
        raise HTTPException(status_code=404, detail="Área não encontrada.")
    return area


@router.post(
    "/areas/{area_id}/activate",
    response_model=AreaBlueprintRead,
    summary="Ativar área validada somente na operação local",
)
async def activate_area(
    area_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> AreaBlueprintRead:
    await _require_saas_area_manager(db, auth)
    try:
        area = await KlmOperationsService(db).activate_area(workspace_id, area_id, auth.user_id)
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    except ValueError as error:
        raise _http_error(error) from error
    if not area:
        raise HTTPException(status_code=404, detail="Área não encontrada.")
    return area


@router.patch(
    "/areas/{area_id}/activation",
    response_model=AreaBlueprintRead,
    summary="Desbloquear ou congelar área jurídica",
)
async def set_area_activation(
    area_id: UUID,
    payload: AreaActivationUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> AreaBlueprintRead:
    await _require_saas_area_manager(db, auth)
    try:
        area = await KlmOperationsService(db).set_area_activation(
            workspace_id,
            area_id,
            auth.user_id,
            enabled=payload.enabled,
            allow_over_plan=payload.force_over_plan,
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    if not area:
        raise HTTPException(status_code=404, detail="Área não encontrada.")
    return area


@router.get(
    "/omnichannel/threads",
    response_model=list[OmnichannelThreadRead],
    summary="Fila omnichannel unificada",
)
async def list_threads(
    channel: OmnichannelChannel | None = None,
    thread_status: ThreadStatus | None = Query(default=None, alias="status"),
    search: str | None = None,
    assigned_human_id: UUID | None = None,
    assigned_team_id: UUID | None = None,
    owner_agent_key: str | None = Query(default=None, max_length=100),
    queue: str | None = Query(default=None, max_length=60),
    score_min: int | None = Query(default=None, ge=0, le=100),
    score_max: int | None = Query(default=None, ge=0, le=100),
    thread_priority_min: int | None = Query(default=None, ge=0, le=100),
    thread_priority_max: int | None = Query(default=None, ge=0, le=100),
    matter_priority: str | None = None,
    urgency: str | None = None,
    classification: str | None = None,
    demand_type: str | None = Query(default=None, max_length=220),
    health_subtheme: str | None = None,
    column_id: str | None = None,
    lead_source: str | None = Query(default=None, max_length=100),
    tag: str | None = Query(default=None, max_length=60),
    has_unread: bool | None = None,
    is_favorite: bool | None = None,
    flow_status: str | None = Query(default=None, max_length=50),
    sla_state: str | None = Query(default=None, pattern="^(overdue|on_time|without_sla)$"),
    last_message_after: datetime | None = None,
    last_message_before: datetime | None = None,
    sort_by: str = Query(default="recent", pattern="^(recent|score|priority|unread|sla)$"),
    sort_direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=500, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> list[OmnichannelThreadRead]:
    threads = await KlmOperationsService(db).list_threads(
        workspace_id,
        channel,
        thread_status,
        search,
        limit,
        include_sensitive_search=can_view_sensitive,
        assigned_human_id=assigned_human_id,
        assigned_team_id=assigned_team_id,
        owner_agent_key=owner_agent_key,
        queue=queue,
        score_min=score_min,
        score_max=score_max,
        thread_priority_min=thread_priority_min,
        thread_priority_max=thread_priority_max,
        matter_priority=matter_priority,
        urgency=urgency,
        classification=classification,
        demand_type=demand_type,
        health_subtheme=health_subtheme,
        column_id=column_id,
        lead_source=lead_source,
        tag=tag,
        has_unread=has_unread,
        is_favorite=is_favorite,
        flow_status=flow_status,
        sla_state=sla_state,
        last_message_after=last_message_after,
        last_message_before=last_message_before,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )
    return threads if can_view_sensitive else [mask_klm_thread(item) for item in threads]


@router.post(
    "/omnichannel/threads",
    response_model=OmnichannelThreadRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar thread local",
)
async def create_thread(
    payload: OmnichannelThreadCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> OmnichannelThreadRead:
    try:
        thread = await KlmOperationsService(db).create_thread(workspace_id, auth.user_id, payload)
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return thread if can_view_sensitive else mask_klm_thread(thread)


@router.patch(
    "/omnichannel/threads/{thread_id}",
    response_model=OmnichannelThreadRead,
    summary="Atualizar handoff ou dados da thread",
)
async def update_thread(
    thread_id: UUID,
    payload: OmnichannelThreadUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> OmnichannelThreadRead:
    try:
        thread = await KlmOperationsService(db).update_thread(
            workspace_id, thread_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    if not thread:
        raise HTTPException(status_code=404, detail="Thread não encontrada.")
    return thread if can_view_sensitive else mask_klm_thread(thread)


@router.get(
    "/omnichannel/internal-feed",
    response_model=list[InternalMessageFeedEntryRead],
    summary="Feed interno agregado sem N+1 por conversa",
)
async def list_internal_message_feed(
    limit: int = Query(default=500, ge=1, le=1000),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> list[InternalMessageFeedEntryRead]:
    rows = await KlmOperationsService(db).list_internal_message_feed(workspace_id, limit)
    return [
        InternalMessageFeedEntryRead(
            message=message if can_view_sensitive else mask_klm_message(message),
            thread=thread if can_view_sensitive else mask_klm_thread(thread),
        )
        for message, thread in rows
    ]


@router.get(
    "/omnichannel/threads/{thread_id}/messages",
    response_model=list[OmnichannelMessageRead],
    summary="Mensagens com autoria e canal",
)
async def list_messages(
    thread_id: UUID,
    limit: int = Query(default=200, ge=1, le=1000),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> list[OmnichannelMessageRead]:
    try:
        messages = await KlmOperationsService(db).list_messages(workspace_id, thread_id, limit)
    except LookupError as error:
        raise _http_error(error) from error
    return messages if can_view_sensitive else [mask_klm_message(item) for item in messages]


@router.get(
    "/omnichannel/threads/{thread_id}/messages/{message_id}/evidence",
    response_model=MessageEvidenceChainRead,
    summary="Consultar cadeia probatória imutável da mensagem",
)
async def get_message_evidence(
    thread_id: UUID,
    message_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> MessageEvidenceChainRead:
    try:
        data = await MessageEvidenceService(db).read_chain(
            workspace_id,
            thread_id,
            message_id,
            include_sensitive=can_view_sensitive,
        )
    except LookupError as error:
        raise _http_error(error) from error
    return MessageEvidenceChainRead.model_validate(data)


@router.get(
    "/omnichannel/threads/{thread_id}/messages/{message_id}/evidence/export",
    response_model=MessageEvidenceExportRead,
    summary="Exportar manifesto verificável da mensagem",
)
async def export_message_evidence(
    thread_id: UUID,
    message_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> MessageEvidenceExportRead:
    try:
        data = await MessageEvidenceService(db).export_manifest(
            workspace_id,
            thread_id,
            message_id,
            include_sensitive=can_view_sensitive,
        )
    except LookupError as error:
        raise _http_error(error) from error
    return MessageEvidenceExportRead.model_validate(data)


@router.post(
    "/omnichannel/threads/{thread_id}/messages",
    response_model=OmnichannelMessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar mensagem local",
)
async def create_message(
    thread_id: UUID,
    payload: OmnichannelMessageCreate,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> OmnichannelMessageRead:
    normalized_payload = await _authenticated_message_payload(db, auth, payload)
    try:
        if settings.DATABASE_URL.lower().startswith("sqlite"):
            async with sqlite_message_write_lock:
                message, deduplicated = await KlmOperationsService(db).create_message(
                    workspace_id, thread_id, auth.user_id, normalized_payload
                )
                # get_db normally commits after the response. Commit before
                # releasing the local writer so the next replay can observe
                # the unique idempotency row instead of hitting SQLITE_BUSY.
                await db.commit()
        else:
            message, deduplicated = await KlmOperationsService(db).create_message(
                workspace_id, thread_id, auth.user_id, normalized_payload
            )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(deduplicated).lower()
    dispatch_state = "local_only"
    external_actions: int | None = 0
    if normalized_payload.scheduled_at is not None:
        dispatch_state = "scheduled"
    elif (
        normalized_payload.direction == MessageDirection.outbound
        and message.delivery_status == MessageDeliveryStatus.queued
    ):
        # Commit the canonical message, structured collaboration facts and
        # mention notifications atomically before crossing the provider gate.
        # A replay with no durable attempt checkpoint resumes here safely;
        # once an attempt exists, the dispatcher returns its recorded state.
        await db.commit()
        dispatch = await OmnichannelOrchestrationService(db).dispatch_immediate_message(
            workspace_id=workspace_id,
            message_id=message.id,
        )
        dispatch_state = dispatch.state
        external_actions = dispatch.external_actions_executed
        await db.refresh(message)
    elif deduplicated:
        immediate_state = (message.message_metadata or {}).get("immediateDispatch")
        if isinstance(immediate_state, dict):
            dispatch_state = str(immediate_state.get("state") or "local_only")
            raw_external_actions = immediate_state.get("externalActionsExecuted")
            external_actions = (
                int(raw_external_actions)
                if isinstance(raw_external_actions, int)
                else None
                if raw_external_actions is None and dispatch_state == "delivery_uncertain"
                else 0
            )
    response.headers["X-KLM-Dispatch-State"] = dispatch_state
    response.headers["X-KLM-External-Actions-Executed"] = (
        str(external_actions) if external_actions is not None else "unknown"
    )
    return message if can_view_sensitive else mask_klm_message(message)


@router.post(
    "/omnichannel/email-ingest",
    response_model=EmailIngestRead,
    status_code=status.HTTP_201_CREATED,
    summary="Ingerir e-mail local e indexar anexos no RAG",
)
async def ingest_email(
    payload: EmailIngestRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> EmailIngestRead:
    await enforce_workspace_feature_or_http(db, workspace_id, EntitlementKey.email)
    try:
        result = await KlmOperationsService(db).ingest_email(workspace_id, auth.user_id, payload)
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    response = EmailIngestRead.model_validate(result)
    return response if can_view_sensitive else mask_email_ingest(response)


@router.get("/follow-ups", response_model=list[FollowUpRead], summary="Fila de follow-up")
async def list_follow_ups(
    follow_up_status: FollowUpStatus | None = Query(default=None, alias="status"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> list[FollowUpRead]:
    follow_ups = await KlmOperationsService(db).list_follow_ups(workspace_id, follow_up_status)
    return follow_ups if can_view_sensitive else [mask_follow_up(item) for item in follow_ups]


@router.post(
    "/follow-ups",
    response_model=FollowUpRead,
    status_code=status.HTTP_201_CREATED,
    summary="Programar follow-up local",
)
async def create_follow_up(
    payload: FollowUpCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> FollowUpRead:
    try:
        follow_up = await KlmOperationsService(db).schedule_follow_up(
            workspace_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return follow_up if can_view_sensitive else mask_follow_up(follow_up)


@router.patch(
    "/follow-ups/{follow_up_id}",
    response_model=FollowUpRead,
    summary="Editar follow-up local",
)
async def update_follow_up(
    follow_up_id: UUID,
    payload: FollowUpUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> FollowUpRead:
    try:
        follow_up = await KlmOperationsService(db).update_follow_up(
            workspace_id,
            follow_up_id,
            auth.user_id,
            payload,
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return follow_up if can_view_sensitive else mask_follow_up(follow_up)


@router.post(
    "/follow-ups/threads/{thread_id}/suppress",
    summary="Suprimir follow-up somente após bloqueio",
)
async def suppress_follow_ups(
    thread_id: UUID,
    payload: FollowUpSuppressRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        count = await KlmOperationsService(db).suppress_thread_follow_ups(
            workspace_id,
            thread_id,
            auth.user_id,
            payload.reason,
            payload.blocked_by_client,
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return {"suppressed": count, "threadStatus": "blocked"}


@router.get(
    "/tracking/events",
    response_model=list[AcquisitionEventRead],
    summary="Ledger de aquisição",
)
async def list_tracking_events(
    limit: int = Query(default=500, ge=1, le=2000),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("relatorios", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> list[AcquisitionEventRead]:
    events = await KlmOperationsService(db).list_acquisition_events(workspace_id, limit)
    return events if can_view_sensitive else [mask_acquisition_event(item) for item in events]


@router.post(
    "/tracking/events",
    response_model=AcquisitionEventRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar evento idempotente",
)
async def create_tracking_event(
    payload: AcquisitionEventCreate,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("relatorios", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> AcquisitionEventRead:
    try:
        event, deduplicated = await KlmOperationsService(db).record_acquisition_event(
            workspace_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(deduplicated).lower()
    return event if can_view_sensitive else mask_acquisition_event(event)


@router.post(
    "/tracking/backfill-ctwa",
    response_model=CtwaBackfillReport,
    summary="Backfill CTWA histórico (T1.2)",
)
async def backfill_ctwa(
    payload: CtwaBackfillRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("admin", "manage")),
    db: AsyncSession = Depends(get_db),
) -> CtwaBackfillReport:
    # No endpoint HTTP o número de lotes é sempre limitado (o ilimitado é
    # exclusivo do CLI): evita varredura completa síncrona dentro do request.
    bounded_max_batches = payload.max_batches if payload.max_batches is not None else 20
    report = await CtwaBackfillService(db).run(
        workspace_id=workspace_id,
        dry_run=payload.dry_run,
        batch_size=payload.batch_size,
        max_batches=bounded_max_batches,
    )
    return CtwaBackfillReport.model_validate(report.as_dict())


@router.get(
    "/tracking/summary",
    response_model=TrackingSummaryRead,
    summary="Resumo de tracking por canal",
)
async def tracking_summary(
    period_days: int | None = Query(default=None, ge=1, le=3650),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("relatorios", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> TrackingSummaryRead:
    data = await KlmOperationsService(db).tracking_summary(workspace_id, period_days=period_days)
    return TrackingSummaryRead.model_validate(
        data if can_view_sensitive else _mask_tracking_summary(data)
    )


@router.get(
    "/tracking/campaigns/ad-spend",
    summary="Gasto de campanha ao vivo (Meta Ads / Google Ads conectados)",
)
async def campaign_ad_spend_snapshot(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("relatorios", "view")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Auditoria 2026-07-26, item 10: `MetaAdsService`/`GoogleAdsService`
    tinham fetch de spend pronto e zero caller no app — este endpoint é o
    caller. Best-effort: sem conta conectada ou gate externo fechado,
    responde `available: false` com o motivo, nunca inventa número (o CAC
    calculado a partir de `AcquisitionEvent(ad_spend)` em `/tracking/summary`
    continua a fonte de verdade quando não há integração ao vivo)."""
    from app.services.ads_spend_snapshot_service import (
        fetch_google_campaign_spend_snapshot,
        fetch_meta_campaign_spend_snapshot,
    )

    meta_snapshot = await fetch_meta_campaign_spend_snapshot(db, workspace_id)
    google_snapshot = await fetch_google_campaign_spend_snapshot(db, workspace_id)
    return {"metaAds": meta_snapshot, "googleAds": google_snapshot}


@router.post(
    "/tracking/conversion-feedback/prepare",
    response_model=TrackingConversionFeedbackPrepareRead,
    summary="Preparar feedback de conversões sem executar APIs externas",
)
async def prepare_tracking_conversion_feedback(
    period_days: int | None = Query(default=None, ge=1, le=3650),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
    db: AsyncSession = Depends(get_db),
) -> TrackingConversionFeedbackPrepareRead:
    data = await KlmOperationsService(db).prepare_conversion_feedback(
        workspace_id, auth.user_id, period_days=period_days
    )
    return TrackingConversionFeedbackPrepareRead.model_validate(data)


@router.post(
    "/tracking/conversion-feedback/dispatch",
    response_model=AdsFeedbackDispatchRead,
    summary="Despachar conversões preparadas (worker de CAPI, gated)",
)
async def dispatch_tracking_conversion_feedback(
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
    db: AsyncSession = Depends(get_db),
) -> AdsFeedbackDispatchRead:
    data = await KlmOperationsService(db).dispatch_prepared_conversion_feedback(
        workspace_id, auth.user_id
    )
    return AdsFeedbackDispatchRead.model_validate(data)


@router.get(
    "/tracking/ads-measurement-config",
    response_model=AdsMeasurementConfigRead,
    summary="Ler configuração de mensuração Meta (CAPI), segredo mascarado",
)
async def get_ads_measurement_config(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
    db: AsyncSession = Depends(get_db),
) -> AdsMeasurementConfigRead:
    data = await KlmOperationsService(db).get_ads_measurement_config(workspace_id)
    return AdsMeasurementConfigRead.model_validate(data)


@router.put(
    "/tracking/ads-measurement-config",
    response_model=AdsMeasurementConfigRead,
    summary="Salvar dataset/pixel/token CAPI (cifrado; nunca retorna o segredo)",
)
async def put_ads_measurement_config(
    payload: AdsMeasurementConfigUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
    db: AsyncSession = Depends(get_db),
) -> AdsMeasurementConfigRead:
    data = await KlmOperationsService(db).save_ads_measurement_config(
        workspace_id,
        auth.user_id,
        dataset_id=payload.dataset_id,
        pixel_id=payload.pixel_id,
        access_token=payload.access_token,
    )
    return AdsMeasurementConfigRead.model_validate(data)


@router.get(
    "/technical-requests",
    response_model=list[TechnicalRequestRead],
    summary="Fila do TI Agent",
)
async def list_technical_requests(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("admin", "view")),
    db: AsyncSession = Depends(get_db),
) -> list[TechnicalRequestRead]:
    service = SupportTicketService(db)
    await service.migrate_legacy_requests(workspace_id=workspace_id)
    tickets, _ = await service.list_platform(
        workspace_id=workspace_id,
        queues=[SupportTicketQueue.technical],
        limit=100,
    )
    return [_legacy_technical_read(ticket) for ticket in tickets]


@router.post(
    "/technical-requests",
    response_model=TechnicalRequestRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar solicitação técnica local",
)
async def create_technical_request(
    payload: TechnicalRequestCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("admin", "manage")),
    db: AsyncSession = Depends(get_db),
) -> TechnicalRequestRead:
    service = SupportTicketService(db)
    description = payload.description.strip()
    if len(description) < 10:
        description = f"{description} (enviado pela API técnica legada)"
    ticket, _ = await service.create_ticket(
        workspace_id=workspace_id,
        user_id=auth.user_id,
        payload=SupportTicketCreate(
            ticket_type=SupportTicketType.problem,
            subject=payload.title,
            description=description,
            priority=(
                SupportTicketPriority.urgent
                if payload.severity == TechnicalRequestSeverity.critical
                else SupportTicketPriority.normal
            ),
            severity=SupportTicketSeverity(payload.severity.value),
            technical_context=SupportTechnicalContext(
                page=payload.route,
                action="Solicitação recebida pela API técnica legada",
            ),
        ),
        idempotency_key=f"legacy-technical-adapter:{uuid4()}",
        queue_override=SupportTicketQueue.technical,
    )
    await service.record_legacy_adapter_evidence(
        ticket,
        actor_user_id=auth.user_id,
        evidence=payload.evidence,
    )
    await KlmOperationsService(db).record_audit(
        workspace_id=workspace_id,
        actor_user_id=auth.user_id,
        actor_type="human",
        actor_label="Usuário KLM",
        event_type="technical_request_created",
        entity_type="technical_request",
        entity_id=ticket.id,
        summary=f"Solicitação técnica '{ticket.subject}' criada.",
    )
    await db.refresh(ticket)
    return _legacy_technical_read(ticket, evidence_override=payload.evidence)


@router.patch(
    "/technical-requests/{request_id}",
    response_model=TechnicalRequestRead,
    summary="Atualizar solicitação técnica",
)
async def update_technical_request(
    request_id: UUID,
    payload: TechnicalRequestUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("admin", "manage")),
    db: AsyncSession = Depends(get_db),
) -> TechnicalRequestRead:
    service = SupportTicketService(db)
    try:
        await service.migrate_legacy_requests(workspace_id=workspace_id)
        ticket = await _legacy_technical_ticket(
            db,
            workspace_id=workspace_id,
            request_id=request_id,
        )
        if ticket is None:
            raise SupportTicketNotFoundError("Solicitação técnica não encontrada.")

        if payload.resolution:
            actor = await db.get(User, auth.user_id)
            body_hash = hashlib.sha256(payload.resolution.encode("utf-8")).hexdigest()[:24]
            await service.add_message(
                ticket,
                actor_user_id=auth.user_id,
                actor_label=actor.name if actor is not None else "Usuário da conta",
                payload=SupportMessageCreate(
                    body=payload.resolution,
                    visibility=SupportMessageVisibility.public,
                ),
                idempotency_key=f"legacy-resolution:{ticket.id}:{body_hash}",
                platform=False,
            )

        target_status = (
            _LEGACY_TECHNICAL_STATUS_TO_SUPPORT[payload.status]
            if payload.status is not None
            else None
        )
        if (
            target_status is not None
            and ticket.status != target_status
            and ticket.status
            in {
                SupportTicketStatus.new,
                SupportTicketStatus.resolved,
                SupportTicketStatus.closed,
                SupportTicketStatus.cancelled,
            }
            and target_status
            not in {
                SupportTicketStatus.in_progress,
                SupportTicketStatus.cancelled,
            }
        ):
            ticket = await service.update_ticket(
                ticket,
                actor_user_id=auth.user_id,
                payload=SupportTicketUpdate(
                    expected_version=ticket.version,
                    status=SupportTicketStatus.in_progress,
                ),
            )

        update_data = {"expected_version": ticket.version}
        if target_status is not None:
            update_data["status"] = target_status
        if payload.severity is not None:
            update_data["severity"] = SupportTicketSeverity(payload.severity.value)
        if payload.assigned_to_user_id is not None:
            update_data["assigned_to_user_id"] = payload.assigned_to_user_id
        if len(update_data) > 1:
            ticket = await service.update_ticket(
                ticket,
                actor_user_id=auth.user_id,
                payload=SupportTicketUpdate(**update_data),
            )
        if payload.evidence is not None:
            await service.record_legacy_adapter_evidence(
                ticket,
                actor_user_id=auth.user_id,
                evidence=payload.evidence,
            )
        await KlmOperationsService(db).record_audit(
            workspace_id=workspace_id,
            actor_user_id=auth.user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="technical_request_updated",
            entity_type="technical_request",
            entity_id=ticket.id,
            summary=f"Solicitação técnica '{ticket.subject}' atualizada.",
        )
        await db.refresh(ticket)
    except SupportTicketNotFoundError:
        raise HTTPException(status_code=404, detail="Solicitação técnica não encontrada.")
    except SupportTicketAccessError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except SupportTicketConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SupportTicketValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _legacy_technical_read(
        ticket,
        status_override=payload.status,
        resolution=payload.resolution,
        evidence_override=payload.evidence,
    )


@router.get(
    "/legal-artifacts",
    response_model=list[LegalArtifactVersionRead],
    summary="Listar versões jurídicas persistidas",
)
async def list_legal_artifacts(
    matter_id: UUID | None = Query(default=None),
    kind: str | None = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("lista", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> list[LegalArtifactVersionRead]:
    artifacts = await KlmOperationsService(db).list_legal_artifacts(
        workspace_id, matter_id=matter_id, kind=kind
    )
    return artifacts if can_view_sensitive else [mask_legal_artifact(item) for item in artifacts]


@router.get(
    "/legal-artifacts/{artifact_id}",
    response_model=LegalArtifactVersionRead,
    summary="Consultar uma versão jurídica exata",
)
async def get_legal_artifact(
    artifact_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("lista", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> LegalArtifactVersionRead:
    artifact = await KlmOperationsService(db).get_legal_artifact(workspace_id, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Documento jurídico não encontrado.")
    return artifact if can_view_sensitive else mask_legal_artifact(artifact)


@router.post(
    "/legal-artifacts",
    response_model=LegalArtifactVersionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar uma versão jurídica imutável",
)
async def create_legal_artifact(
    payload: LegalArtifactVersionCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("kanban", "edit")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> LegalArtifactVersionRead:
    try:
        artifact = await KlmOperationsService(db).create_legal_artifact_version(
            workspace_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return artifact if can_view_sensitive else mask_legal_artifact(artifact)


@router.post(
    "/legal-artifacts/{artifact_id}/submit",
    response_model=LegalArtifactSubmitRead,
    summary="Enviar uma versão jurídica para aprovação humana",
)
async def submit_legal_artifact(
    artifact_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("aprovacoes", "approve")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> LegalArtifactSubmitRead:
    try:
        artifact, approval = await KlmOperationsService(db).submit_legal_artifact_for_approval(
            workspace_id, auth.user_id, artifact_id
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    serialized = artifact if can_view_sensitive else mask_legal_artifact(artifact)
    return LegalArtifactSubmitRead(
        artifact=LegalArtifactVersionRead.model_validate(serialized),
        approval_id=approval.id,
    )


@router.get(
    "/process-monitoring",
    response_model=list[ProcessMonitoringRead],
    summary="Listar processos vinculados aos cards",
)
async def list_process_monitoring(
    matter_id: UUID | None = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("lista", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> list[ProcessMonitoringRead]:
    records = await KlmOperationsService(db).list_process_monitoring_records(
        workspace_id, matter_id
    )
    return records if can_view_sensitive else [mask_process_monitoring(item) for item in records]


@router.post(
    "/process-monitoring",
    response_model=ProcessMonitoringRead,
    status_code=status.HTTP_201_CREATED,
    summary="Vincular processo a um card",
)
async def create_process_monitoring(
    payload: ProcessMonitoringCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("kanban", "edit")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> ProcessMonitoringRead:
    try:
        record = await KlmOperationsService(db).create_process_monitoring_record(
            workspace_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return record if can_view_sensitive else mask_process_monitoring(record)


@router.patch(
    "/process-monitoring/{record_id}",
    response_model=ProcessMonitoringRead,
    summary="Atualizar status e prazo processual",
)
async def update_process_monitoring(
    record_id: UUID,
    payload: ProcessMonitoringUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("kanban", "edit")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> ProcessMonitoringRead:
    record = await KlmOperationsService(db).update_process_monitoring_record(
        workspace_id, auth.user_id, record_id, payload
    )
    if not record:
        raise HTTPException(status_code=404, detail="Processo monitorado não encontrado.")
    return record if can_view_sensitive else mask_process_monitoring(record)


@router.post(
    "/process-monitoring/{record_id}/events",
    response_model=ProcessMonitoringRead,
    summary="Registrar movimentação processual local",
)
async def create_process_monitoring_event(
    record_id: UUID,
    payload: ProcessMonitoringEventCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("kanban", "edit")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> ProcessMonitoringRead:
    record = await KlmOperationsService(db).add_process_monitoring_event(
        workspace_id, auth.user_id, record_id, payload
    )
    if not record:
        raise HTTPException(status_code=404, detail="Processo monitorado não encontrado.")
    return record if can_view_sensitive else mask_process_monitoring(record)


@router.get("/activity", response_model=list[AuditEventRead], summary="Trilha KLM")
async def list_activity(
    limit: int = Query(default=200, ge=1, le=1000),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("audit", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> list[AuditEventRead]:
    events = await KlmOperationsService(db).list_activity(workspace_id, limit)
    return events if can_view_sensitive else [mask_audit_event(item) for item in events]


@router.get(
    "/external-intents",
    response_model=list[ExternalActionIntentRead],
    summary="Intenções externas bloqueadas",
)
async def list_external_intents(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("integrations", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> list[ExternalActionIntentRead]:
    intents = await KlmOperationsService(db).list_external_intents(workspace_id)
    return intents if can_view_sensitive else [mask_external_intent(item) for item in intents]


@router.post(
    "/external-intents",
    response_model=ExternalActionIntentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar intenção sem executar",
)
async def create_external_intent(
    payload: ExternalActionIntentCreate,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> ExternalActionIntentRead:
    try:
        intent, deduplicated = await KlmOperationsService(db).create_external_intent(
            workspace_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(deduplicated).lower()
    return intent if can_view_sensitive else mask_external_intent(intent)


@router.post(
    "/external-intents/{intent_id}/approve-local",
    response_model=ExternalActionIntentRead,
    summary="Aprovar apenas na simulação",
)
async def approve_external_intent_locally(
    intent_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> ExternalActionIntentRead:
    intent = await KlmOperationsService(db).approve_intent_locally(
        workspace_id, intent_id, auth.user_id
    )
    if not intent:
        raise HTTPException(status_code=404, detail="Intenção não encontrada.")
    return intent if can_view_sensitive else mask_external_intent(intent)


@router.post(
    "/external-intents/{intent_id}/reject-local",
    response_model=ExternalActionIntentRead,
    summary="Rejeitar intenção apenas no ambiente local",
)
async def reject_external_intent_locally(
    intent_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> ExternalActionIntentRead:
    intent = await KlmOperationsService(db).reject_intent_locally(
        workspace_id, intent_id, auth.user_id
    )
    if not intent:
        raise HTTPException(status_code=404, detail="Intenção não encontrada.")
    return intent if can_view_sensitive else mask_external_intent(intent)
