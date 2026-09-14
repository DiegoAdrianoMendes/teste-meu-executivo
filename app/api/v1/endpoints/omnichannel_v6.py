"""Native KLM endpoints for canonical omnichannel operations."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    can_view_sensitive_lead_data,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.lead_privacy import mask_klm_message, mask_klm_thread
from app.schemas.ads_manager import (
    AdsManagerEntityRead,
    AdsManagerImportReceiptRead,
    AdsManagerSummaryRead,
    AdsSnapshotIngest,
    AdsSnapshotIngestRead,
)
from app.schemas.omnichannel_v6 import (
    AutomationRuleCreate,
    AutomationRuleRead,
    AutomationRuleUpdate,
    AutomationRunRead,
    AutomationRunSummaryRead,
    AutomationSimulationRequest,
    CallEventEvidenceRead,
    CallEventRead,
    CallSessionCreate,
    CallSessionRead,
    CallSessionTransition,
    CanonicalChannelEventIngest,
    CanonicalChannelEventIngestRead,
    ChannelConnectionCreate,
    ChannelConnectionRead,
    ChannelConnectionSummaryRead,
    ChannelConnectionUpdate,
    ChannelConnectionValidationRead,
    ChannelEventInboxItemRead,
    ChannelEventInboxRead,
    IdentityMergeRequest,
    IdentityMutationEventRead,
    IdentityMutationRead,
    IdentityReviewRead,
    IdentitySplitRequest,
    LeadCaptureInboxItemRead,
    LeadCaptureInboxRead,
    LeadCaptureIngest,
    LeadCaptureIngestRead,
    LeadHydrationAction,
    LeadHydrationEventRead,
    LeadHydrationJobRead,
    MessageDeliveryEventRead,
    MessageDeliveryReceiptIngest,
    MessageDeliveryReceiptRead,
    OmnichannelActionIntentCreate,
    OmnichannelActionIntentEventRead,
    OmnichannelActionIntentRead,
    OmnichannelAuditDashboardRead,
    OmnichannelAuditFindingAction,
    OmnichannelAuditFindingRead,
    OmnichannelAuditRunRequest,
    OmnichannelBindingRead,
    OmnichannelChannelEventRead,
    OmnichannelChannelSummaryRead,
    OmnichannelCopilotRead,
    OmnichannelCopilotRequest,
    OmnichannelEndpointRead,
    OmnichannelIdentityRead,
    OmnichannelRoutingEventRead,
    RawLeadCaptureIngest,
    ScheduledMessageCreate,
    ScheduledMessageEventRead,
    ScheduledMessageRead,
    ScheduledMessageUpdate,
    ThreadOmnichannelContextRead,
    ThreadTimelineRead,
    ThreadTransferRead,
    ThreadTransferRequest,
    TimelineItemRead,
    WebhookCenterRead,
    WebhookCredentialConfigure,
    WebhookCredentialStatusRead,
    WorkspaceTeamCreate,
    WorkspaceTeamMemberCreate,
    WorkspaceTeamMemberRead,
    WorkspaceTeamRead,
    WorkspaceTeamUpdate,
)
from app.services.ads_manager_service import AdsManagerService
from app.services.conversation_auditor_service import (
    ConversationAuditorService,
    ConversationAuditVersionConflictError,
)
from app.services.lead_capture_adapter_service import LeadCaptureAdapterService
from app.services.lead_hydration_service import LeadHydrationService
from app.services.omnichannel_copilot_service import OmnichannelCopilotService
from app.services.omnichannel_orchestration_service import (
    OmnichannelOrchestrationConflictError,
    OmnichannelOrchestrationService,
)
from app.services.omnichannel_v6_service import (
    CanonicalResolution,
    OmnichannelV6Service,
    OmnichannelVersionConflictError,
)
from app.services.omnichannel_webhook_service import (
    OmnichannelWebhookService,
    WebhookVersionConflictError,
)

router = APIRouter(prefix="/klm/omnichannel", tags=["KLM Omnichannel"])


@router.get(
    "/webhook-center",
    response_model=WebhookCenterRead,
    summary="Central nativa de webhooks, recibos e hidratação",
)
async def get_webhook_center(
    provider: str | None = Query(default=None, max_length=40),
    state: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=200, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("integrations", "view")),
) -> WebhookCenterRead:
    result = await OmnichannelWebhookService(db).webhook_center(
        workspace_id,
        provider=provider,
        state=state,
        limit=limit,
    )
    return WebhookCenterRead.model_validate(result)


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    headers = None
    if isinstance(error, OmnichannelVersionConflictError):
        headers = {"X-KLM-Current-Version": str(error.current_version)}
    if isinstance(error, OmnichannelOrchestrationConflictError) and error.current_version:
        headers = {"X-KLM-Current-Version": str(error.current_version)}
    if isinstance(error, ConversationAuditVersionConflictError):
        headers = {"X-KLM-Current-Version": str(error.current_version)}
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=str(error),
        headers=headers,
    )


@router.get(
    "/audit/findings",
    response_model=OmnichannelAuditDashboardRead,
    summary="Painel do Auditor omnichannel independente",
)
async def list_audit_findings(
    thread_id: UUID | None = Query(default=None),
    state: str | None = Query(default=None, max_length=24),
    severity: str | None = Query(default=None, max_length=16),
    finding_type: str | None = Query(default=None, max_length=64),
    query: str | None = Query(default=None, max_length=180),
    limit: int = Query(default=100, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> OmnichannelAuditDashboardRead:
    return await ConversationAuditorService(db).dashboard(
        workspace_id,
        thread_id=thread_id,
        state=state,
        severity=severity,
        finding_type=finding_type,
        query=query,
        limit=limit,
    )


@router.post(
    "/audit/run",
    response_model=OmnichannelAuditDashboardRead,
    summary="Executar auditoria local imediata",
)
async def run_conversation_audit(
    payload: OmnichannelAuditRunRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> OmnichannelAuditDashboardRead:
    try:
        return await ConversationAuditorService(db).scan_workspace(
            workspace_id,
            thread_id=payload.thread_id,
            limit=payload.limit,
        )
    except LookupError as error:
        raise _http_error(error) from error


@router.post(
    "/audit/findings/{finding_id}/actions",
    response_model=OmnichannelAuditFindingRead,
    summary="Resolver, descartar ou tentar remediar um apontamento",
)
async def act_on_audit_finding(
    finding_id: UUID,
    payload: OmnichannelAuditFindingAction,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> OmnichannelAuditFindingRead:
    try:
        return await ConversationAuditorService(db).act_on_finding(
            workspace_id,
            finding_id,
            payload,
            user_id=auth.user_id,
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error


@router.get(
    "/connections",
    response_model=list[ChannelConnectionRead],
    summary="Listar conexões omnichannel nativas",
)
async def list_connections(
    channel: str | None = Query(default=None, max_length=40),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("integrations", "view")),
) -> list[ChannelConnectionRead]:
    return await OmnichannelOrchestrationService(db).list_connections(workspace_id, channel=channel)


@router.post(
    "/connections",
    response_model=ChannelConnectionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Configurar canal local sem ativar API externa",
)
async def create_connection(
    payload: ChannelConnectionCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
) -> ChannelConnectionRead:
    try:
        return await OmnichannelOrchestrationService(db).create_connection(
            workspace_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error


@router.patch(
    "/connections/{connection_id}",
    response_model=ChannelConnectionRead,
    summary="Atualizar conexão com trava de versão",
)
async def update_connection(
    connection_id: UUID,
    payload: ChannelConnectionUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
) -> ChannelConnectionRead:
    try:
        return await OmnichannelOrchestrationService(db).update_connection(
            workspace_id, connection_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error


@router.post(
    "/connections/{connection_id}/validate-local",
    response_model=ChannelConnectionValidationRead,
    summary="Validar prontidão local sem chamar o provedor",
)
async def validate_connection_local(
    connection_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
) -> ChannelConnectionValidationRead:
    try:
        result = await OmnichannelOrchestrationService(db).validate_connection_local(
            workspace_id, connection_id
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return ChannelConnectionValidationRead.model_validate(result)


@router.get(
    "/connections/{connection_id}/webhook-credential",
    response_model=WebhookCredentialStatusRead,
    summary="Ver estado da credencial assinada sem revelar segredos",
)
async def get_webhook_credential_status(
    connection_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
) -> WebhookCredentialStatusRead:
    try:
        result = await OmnichannelWebhookService(db).credential_status(
            workspace_id, connection_id
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return WebhookCredentialStatusRead.model_validate(result)


@router.put(
    "/connections/{connection_id}/webhook-credential",
    response_model=WebhookCredentialStatusRead,
    summary="Configurar ou rotacionar segredo assinado localmente",
)
async def configure_webhook_credential(
    connection_id: UUID,
    payload: WebhookCredentialConfigure,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
) -> WebhookCredentialStatusRead:
    try:
        result = await OmnichannelWebhookService(db).configure_credential(
            workspace_id, connection_id, auth.user_id, payload
        )
    except WebhookVersionConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
            headers={"X-KLM-Current-Version": str(error.current_version)},
        ) from error
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return WebhookCredentialStatusRead.model_validate(result)


def _team_read(
    team: Any,
    *,
    member_count: int = 0,
    available_count: int = 0,
    open_thread_count: int = 0,
) -> WorkspaceTeamRead:
    return WorkspaceTeamRead.model_validate(
        {
            "id": team.id,
            "workspace_id": team.workspace_id,
            "name": team.name,
            "slug": team.slug,
            "description": team.description,
            "team_type": team.team_type,
            "color": team.color,
            "capacity": team.capacity,
            "routing_strategy": team.routing_strategy,
            "schedule": team.schedule,
            "is_active": team.is_active,
            "member_count": member_count,
            "available_count": available_count,
            "open_thread_count": open_thread_count,
            "created_at": team.created_at,
            "updated_at": team.updated_at,
        }
    )


def _member_read(member: Any, user: Any) -> WorkspaceTeamMemberRead:
    return WorkspaceTeamMemberRead.model_validate(
        {
            "id": member.id,
            "workspace_id": member.workspace_id,
            "team_id": member.team_id,
            "membership_id": member.membership_id,
            "user_id": member.user_id,
            "user_name": user.name,
            "member_role": member.member_role,
            "routing_weight": member.routing_weight,
            "max_concurrent": member.max_concurrent,
            "is_available": member.is_available,
            "created_at": member.created_at,
            "updated_at": member.updated_at,
        }
    )


def _identity_read(identity: Any, *, can_view_sensitive: bool) -> OmnichannelIdentityRead:
    result = OmnichannelIdentityRead.model_validate(identity)
    if can_view_sensitive:
        return result
    return result.model_copy(
        update={"display_name": "Lead protegido", "canonical_phone": None, "canonical_email": None}
    )


def _endpoint_read(endpoint: Any, *, can_view_sensitive: bool) -> OmnichannelEndpointRead:
    result = OmnichannelEndpointRead.model_validate(endpoint)
    if can_view_sensitive:
        return result
    return result.model_copy(
        update={"address": None, "normalized_address": None, "endpoint_metadata": {}}
    )


def _event_read(
    resolution: CanonicalResolution,
    event: Any,
    message: Any,
    *,
    deduplicated: bool,
    can_view_sensitive: bool,
) -> CanonicalChannelEventIngestRead:
    identity = _identity_read(resolution.identity, can_view_sensitive=can_view_sensitive)
    endpoint = _endpoint_read(resolution.endpoint, can_view_sensitive=can_view_sensitive)
    thread = mask_klm_thread(resolution.thread) if not can_view_sensitive else resolution.thread
    event_read = OmnichannelChannelEventRead.model_validate(event)
    message_read = None
    if message is not None:
        message_read = mask_klm_message(message) if not can_view_sensitive else message
    if not can_view_sensitive:
        event_read = event_read.model_copy(
            update={"actor_external_id": None, "event_payload": {"redacted": True}}
        )
    return CanonicalChannelEventIngestRead(
        identity=identity,
        endpoint=endpoint,
        binding=OmnichannelBindingRead.model_validate(resolution.binding),
        thread=thread,
        event=event_read,
        message=message_read,
        deduplicated=deduplicated,
    )


def _lead_capture_read(
    result: dict[str, Any], *, can_view_sensitive: bool
) -> LeadCaptureIngestRead:
    identity = _identity_read(result["identity"], can_view_sensitive=can_view_sensitive)
    endpoint = _endpoint_read(result["endpoint"], can_view_sensitive=can_view_sensitive)
    thread = result["thread"]
    message = result["message"]
    return LeadCaptureIngestRead(
        receipt=result["receipt"],
        identity=identity,
        endpoint=endpoint,
        binding=result["binding"],
        thread=thread if can_view_sensitive else mask_klm_thread(thread),
        message=(message if message is None or can_view_sensitive else mask_klm_message(message)),
        client_id=result["client_id"],
        matter_id=result["matter_id"],
        deduplicated=result["deduplicated"],
    )


def _lead_hydration_read(
    job: Any, *, can_view_sensitive: bool
) -> LeadHydrationJobRead:
    result = LeadHydrationJobRead.model_validate(job)
    if can_view_sensitive:
        return result
    return result.model_copy(
        update={
            "external_event_id": "protected",
            "external_lead_id": "protected",
            "external_form_id": None,
            "locator_payload": {},
            "provider_receipt": {
                "state": result.state,
                "externalRequests": result.provider_receipt.get("externalRequests", 0),
            },
            "last_error_message": None,
        }
    )


def _lead_hydration_event_read(
    event: Any, *, can_view_sensitive: bool
) -> LeadHydrationEventRead:
    result = LeadHydrationEventRead.model_validate(event)
    if can_view_sensitive:
        return result
    return result.model_copy(
        update={
            "external_lead_id": "protected",
            "error_message": None,
            "event_metadata": {},
        }
    )


def _identity_mutation_read(
    result: dict[str, Any], *, can_view_sensitive: bool
) -> IdentityMutationRead:
    return IdentityMutationRead(
        source=_identity_read(result["source"], can_view_sensitive=can_view_sensitive),
        target=_identity_read(result["target"], can_view_sensitive=can_view_sensitive),
        event=IdentityMutationEventRead.model_validate(result["event"]),
        moved_endpoint_ids=result["moved_endpoint_ids"],
        moved_thread_ids=result["moved_thread_ids"],
        deduplicated=result["deduplicated"],
    )


@router.get(
    "/identity-reviews",
    response_model=list[IdentityReviewRead],
    summary="Listar revisões de identidade e contexto jurídico",
)
async def list_identity_reviews(
    review_status: str | None = Query(default="open", alias="status", max_length=24),
    limit: int = Query(default=100, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("lead_data", "view")),
) -> list[IdentityReviewRead]:
    reviews = await OmnichannelV6Service(db).list_identity_reviews(
        workspace_id,
        status=review_status,
        limit=limit,
    )
    return [IdentityReviewRead.model_validate(item) for item in reviews]


@router.post(
    "/identities/{target_identity_id}/merge",
    response_model=IdentityMutationRead,
    summary="Unir identidades com trava de versão e auditoria",
)
async def merge_identities(
    target_identity_id: UUID,
    payload: IdentityMergeRequest,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("lead_data", "edit")),
) -> IdentityMutationRead:
    try:
        result = await OmnichannelV6Service(db).merge_identities(
            workspace_id,
            auth.user_id,
            target_identity_id,
            payload,
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(result["deduplicated"]).lower()
    return _identity_mutation_read(result, can_view_sensitive=can_view_sensitive)


@router.post(
    "/identities/{identity_id}/split",
    response_model=IdentityMutationRead,
    summary="Separar identidade com seleção explícita e auditoria",
)
async def split_identity(
    identity_id: UUID,
    payload: IdentitySplitRequest,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("lead_data", "edit")),
) -> IdentityMutationRead:
    try:
        result = await OmnichannelV6Service(db).split_identity(
            workspace_id,
            auth.user_id,
            identity_id,
            payload,
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(result["deduplicated"]).lower()
    return _identity_mutation_read(result, can_view_sensitive=can_view_sensitive)


@router.get("/teams", response_model=list[WorkspaceTeamRead], summary="Listar equipes nativas")
async def list_teams(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> list[WorkspaceTeamRead]:
    rows = await OmnichannelV6Service(db).list_teams(workspace_id)
    return [
        _team_read(
            row["team"],
            member_count=row["member_count"],
            available_count=row["available_count"],
            open_thread_count=row["open_thread_count"],
        )
        for row in rows
    ]


@router.post(
    "/teams",
    response_model=WorkspaceTeamRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar equipe nativa",
)
async def create_team(
    payload: WorkspaceTeamCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("users", "manage")),
) -> WorkspaceTeamRead:
    try:
        team = await OmnichannelV6Service(db).create_team(workspace_id, auth.user_id, payload)
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return _team_read(team)


@router.patch("/teams/{team_id}", response_model=WorkspaceTeamRead, summary="Atualizar equipe")
async def update_team(
    team_id: UUID,
    payload: WorkspaceTeamUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("users", "manage")),
) -> WorkspaceTeamRead:
    try:
        team = await OmnichannelV6Service(db).update_team(
            workspace_id, team_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return _team_read(team)


@router.get(
    "/teams/{team_id}/members",
    response_model=list[WorkspaceTeamMemberRead],
    summary="Listar membros da equipe",
)
async def list_team_members(
    team_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> list[WorkspaceTeamMemberRead]:
    try:
        rows = await OmnichannelV6Service(db).list_team_members(workspace_id, team_id)
    except LookupError as error:
        raise _http_error(error) from error
    return [_member_read(member, user) for member, user in rows]


@router.post(
    "/teams/{team_id}/members",
    response_model=WorkspaceTeamMemberRead,
    status_code=status.HTTP_201_CREATED,
    summary="Adicionar ou atualizar membro da equipe",
)
async def add_team_member(
    team_id: UUID,
    payload: WorkspaceTeamMemberCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("users", "manage")),
) -> WorkspaceTeamMemberRead:
    try:
        member, user = await OmnichannelV6Service(db).add_team_member(
            workspace_id, team_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return _member_read(member, user)


@router.delete(
    "/teams/{team_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remover membro da equipe",
)
async def remove_team_member(
    team_id: UUID,
    user_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("users", "manage")),
) -> Response:
    try:
        await OmnichannelV6Service(db).remove_team_member(
            workspace_id, team_id, user_id, auth.user_id
        )
    except LookupError as error:
        raise _http_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/threads/{thread_id}/transfer",
    response_model=ThreadTransferRead,
    summary="Transferir conversa com trava de versão",
)
async def transfer_thread(
    thread_id: UUID,
    payload: ThreadTransferRequest,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> ThreadTransferRead:
    try:
        thread, event, deduplicated = await OmnichannelV6Service(db).transfer_thread(
            workspace_id, thread_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(deduplicated).lower()
    return ThreadTransferRead(
        thread=thread if can_view_sensitive else mask_klm_thread(thread),
        event=OmnichannelRoutingEventRead.model_validate(event),
        deduplicated=deduplicated,
    )


@router.post(
    "/events/ingest",
    response_model=CanonicalChannelEventIngestRead,
    status_code=status.HTTP_201_CREATED,
    summary="Ingerir evento canônico de canal no runtime local",
)
async def ingest_event(
    payload: CanonicalChannelEventIngest,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
) -> CanonicalChannelEventIngestRead:
    try:
        resolution, event, message, deduplicated = await OmnichannelV6Service(db).ingest_event(
            workspace_id, payload
        )
    except (ValueError, LookupError, RuntimeError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(deduplicated).lower()
    return _event_read(
        resolution,
        event,
        message,
        deduplicated=deduplicated,
        can_view_sensitive=can_view_sensitive,
    )


@router.post(
    "/message-delivery-events/ingest",
    response_model=MessageDeliveryReceiptRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar recibo imutável de entrega sem executar envio externo",
)
async def ingest_message_delivery_event(
    payload: MessageDeliveryReceiptIngest,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
) -> MessageDeliveryReceiptRead:
    try:
        message, event, _cost_entry, deduplicated = (
            await OmnichannelV6Service(db).ingest_delivery_receipt(workspace_id, payload)
        )
    except (ValueError, LookupError, RuntimeError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(deduplicated).lower()
    return MessageDeliveryReceiptRead(
        message=message if can_view_sensitive else mask_klm_message(message),
        event=event,
        deduplicated=deduplicated,
    )


@router.get(
    "/messages/{message_id}/delivery-events",
    response_model=list[MessageDeliveryEventRead],
    summary="Listar trilha imutável de entrega da mensagem",
)
async def list_message_delivery_events(
    message_id: UUID,
    limit: int = Query(default=200, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> list[MessageDeliveryEventRead]:
    try:
        return await OmnichannelV6Service(db).list_delivery_events(
            workspace_id, message_id, limit=limit
        )
    except LookupError as error:
        raise _http_error(error) from error


@router.get(
    "/events",
    response_model=ChannelEventInboxRead,
    summary="Listar eventos do inbox social e omnichannel",
)
async def list_channel_events(
    channel: str | None = Query(default=None, max_length=40),
    event_type: str | None = Query(default=None, max_length=100),
    visibility: str | None = Query(default=None, max_length=40),
    actionability: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=200, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> ChannelEventInboxRead:
    rows, has_more = await OmnichannelV6Service(db).list_channel_events(
        workspace_id,
        channel=channel,
        event_type=event_type,
        visibility=visibility,
        actionability=actionability,
        limit=limit,
    )
    items: list[ChannelEventInboxItemRead] = []
    for row in rows:
        identity = row["identity"]
        thread = row["thread"]
        event = OmnichannelChannelEventRead.model_validate(row["event"])
        message = row["message"]
        connection = row["connection"]
        if not can_view_sensitive:
            identity = (
                _identity_read(identity, can_view_sensitive=False)
                if identity is not None
                else None
            )
            thread = mask_klm_thread(thread)
            event = event.model_copy(
                update={"actor_external_id": None, "event_payload": {"redacted": True}}
            )
            message = mask_klm_message(message) if message is not None else None
        elif identity is not None:
            identity = _identity_read(identity, can_view_sensitive=True)
        items.append(
            ChannelEventInboxItemRead(
                event=event,
                thread=thread,
                identity=identity,
                message=message,
                connection=(
                    ChannelConnectionSummaryRead.model_validate(connection)
                    if connection is not None
                    else None
                ),
                channel=row["channel"],
            )
        )
    return ChannelEventInboxRead(items=items, has_more=has_more, generated_at=datetime.now(UTC))


@router.post(
    "/threads/{thread_id}/actions",
    response_model=OmnichannelActionIntentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Preparar resposta, moderação ou mensagem sem efeito externo",
)
async def create_action_intent(
    thread_id: UUID,
    payload: OmnichannelActionIntentCreate,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> OmnichannelActionIntentRead:
    try:
        intent, deduplicated = await OmnichannelOrchestrationService(db).create_action_intent(
            workspace_id, thread_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(deduplicated).lower()
    return intent


@router.get(
    "/action-intents/{intent_id}/events",
    response_model=list[OmnichannelActionIntentEventRead],
    summary="Consultar evidências imutáveis da entrega de uma ação",
)
async def list_action_intent_events(
    intent_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> list[OmnichannelActionIntentEventRead]:
    try:
        rows = await OmnichannelOrchestrationService(db).list_action_intent_events(
            workspace_id, intent_id
        )
    except LookupError as error:
        raise _http_error(error) from error
    events = [OmnichannelActionIntentEventRead.model_validate(row) for row in rows]
    if can_view_sensitive:
        return events
    return [
        row.model_copy(
            update={
                "error_message": None,
                "event_metadata": {"sensitiveDataHidden": True},
            }
        )
        for row in events
    ]


@router.get(
    "/threads/{thread_id}/context",
    response_model=ThreadOmnichannelContextRead,
    summary="Carregar Atendimento 360 canônico em uma chamada",
)
async def thread_context(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> ThreadOmnichannelContextRead:
    try:
        result = await OmnichannelOrchestrationService(db).thread_context(workspace_id, thread_id)
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error

    thread = result["thread"]
    identity = result["identity"]
    channels = [OmnichannelChannelSummaryRead.model_validate(row) for row in result["channels"]]
    action_intents = [
        OmnichannelActionIntentRead.model_validate(row) for row in result["action_intents"]
    ]
    timeline = [TimelineItemRead.model_validate(row) for row in result["timeline"]]
    if not can_view_sensitive:
        thread = mask_klm_thread(thread)
        identity = (
            _identity_read(identity, can_view_sensitive=False) if identity is not None else None
        )
        channels = [
            row.model_copy(update={"address": None, "external_user_id": "[protegido]"})
            for row in channels
        ]
        action_intents = [
            row.model_copy(update={"body": None, "action_payload": {"redacted": True}})
            for row in action_intents
        ]
        timeline = [
            row.model_copy(
                update={
                    "body": "[conteúdo protegido pelo administrador]" if row.body else None,
                    "metadata": {"sensitiveDataHidden": True},
                }
            )
            for row in timeline
        ]
    elif identity is not None:
        identity = _identity_read(identity, can_view_sensitive=True)

    return ThreadOmnichannelContextRead(
        thread=thread,
        identity=identity,
        channels=channels,
        bindings=[OmnichannelBindingRead.model_validate(row) for row in result["bindings"]],
        action_intents=action_intents,
        scheduled_messages=result["scheduled_messages"],
        calls=result["calls"],
        automation_runs=[
            AutomationRunSummaryRead.model_validate(row) for row in result["automation_runs"]
        ],
        timeline=timeline,
        external_gate_open=result["external_gate_open"],
        generated_at=result["generated_at"],
    )


@router.post(
    "/threads/{thread_id}/copilot",
    response_model=OmnichannelCopilotRead,
    summary="Gerar apoio local rastreável sem chamada externa",
)
async def run_thread_copilot(
    thread_id: UUID,
    payload: OmnichannelCopilotRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> OmnichannelCopilotRead:
    if not can_view_sensitive:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="O administrador ocultou os dados necessários para este apoio.",
        )
    try:
        result = await OmnichannelCopilotService(db).run(workspace_id, thread_id, payload)
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return OmnichannelCopilotRead.model_validate(result)


@router.get(
    "/automation-rules",
    response_model=list[AutomationRuleRead],
    summary="Listar flows omnichannel nativos",
)
async def list_automation_rules(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("automations", "manage")),
) -> list[AutomationRuleRead]:
    return await OmnichannelOrchestrationService(db).list_automation_rules(workspace_id)


@router.post(
    "/automation-rules",
    response_model=AutomationRuleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar flow de mensagem, comentário ou lead",
)
async def create_automation_rule(
    payload: AutomationRuleCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("automations", "manage")),
) -> AutomationRuleRead:
    try:
        return await OmnichannelOrchestrationService(db).create_automation_rule(
            workspace_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error


@router.patch(
    "/automation-rules/{rule_id}",
    response_model=AutomationRuleRead,
    summary="Editar, pausar ou ativar flow",
)
async def update_automation_rule(
    rule_id: UUID,
    payload: AutomationRuleUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("automations", "manage")),
) -> AutomationRuleRead:
    try:
        return await OmnichannelOrchestrationService(db).update_automation_rule(
            workspace_id, rule_id, auth.user_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error


@router.post(
    "/automation-rules/{rule_id}/simulate",
    response_model=AutomationRunRead,
    summary="Simular flow com trace em tempo real e zero chamadas externas",
)
async def simulate_automation_rule(
    rule_id: UUID,
    payload: AutomationSimulationRequest,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("automations", "manage")),
) -> AutomationRunRead:
    try:
        run, trace, deduplicated = await OmnichannelOrchestrationService(db).simulate_automation(
            workspace_id, rule_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(deduplicated).lower()
    return AutomationRunRead.model_validate(
        {
            **{
                field: getattr(run, field)
                for field in AutomationRunRead.model_fields
                if field != "trace"
            },
            "trace": trace,
        }
    )


@router.post(
    "/lead-captures/ingest",
    response_model=LeadCaptureIngestRead,
    status_code=status.HTTP_201_CREATED,
    summary="Capturar lead de anúncio, formulário ou site",
)
async def ingest_lead_capture(
    payload: LeadCaptureIngest,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
) -> LeadCaptureIngestRead:
    try:
        result = await OmnichannelV6Service(db).ingest_lead_capture(
            workspace_id, auth.user_id, payload
        )
    except (ValueError, LookupError, RuntimeError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(result["deduplicated"]).lower()
    return _lead_capture_read(result, can_view_sensitive=can_view_sensitive)


@router.post(
    "/connections/{connection_id}/ads-manager/snapshots",
    response_model=AdsSnapshotIngestRead,
    status_code=status.HTTP_201_CREATED,
    summary="Importar snapshot tipado do Gestor de Anúncios",
)
async def ingest_ads_manager_snapshot(
    connection_id: UUID,
    payload: AdsSnapshotIngest,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
) -> AdsSnapshotIngestRead:
    try:
        result = await AdsManagerService(db).ingest_snapshot(
            workspace_id,
            connection_id,
            payload,
        )
    except (LookupError, ValueError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(result["deduplicated"]).lower()
    return AdsSnapshotIngestRead.model_validate(result)


@router.get(
    "/ads-manager/entities",
    response_model=list[AdsManagerEntityRead],
    summary="Listar hierarquia nativa de contas, campanhas, anúncios e formulários",
)
async def list_ads_manager_entities(
    connection_id: UUID | None = Query(default=None),
    provider: str | None = Query(default=None, max_length=40),
    entity_type: str | None = Query(default=None, max_length=24),
    entity_status: str | None = Query(default=None, alias="status", max_length=40),
    limit: int = Query(default=500, ge=1, le=2000),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("integrations", "view")),
) -> list[AdsManagerEntityRead]:
    return await AdsManagerService(db).list_entities(
        workspace_id,
        connection_id=connection_id,
        provider=provider,
        entity_type=entity_type,
        status=entity_status,
        limit=limit,
    )


@router.get(
    "/ads-manager/imports",
    response_model=list[AdsManagerImportReceiptRead],
    summary="Listar recibos imutáveis de sincronização de anúncios",
)
async def list_ads_manager_imports(
    provider: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("integrations", "view")),
) -> list[AdsManagerImportReceiptRead]:
    return await AdsManagerService(db).list_imports(
        workspace_id,
        provider=provider,
        limit=limit,
    )


@router.get(
    "/ads-manager/summary",
    response_model=AdsManagerSummaryRead,
    summary="Consolidar mídia paga, captação real e conversão jurídica",
)
async def get_ads_manager_summary(
    date_from: date = Query(default_factory=lambda: date.today() - timedelta(days=29)),
    date_to: date = Query(default_factory=date.today),
    provider: str | None = Query(default=None, max_length=40),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("integrations", "view")),
) -> AdsManagerSummaryRead:
    try:
        result = await AdsManagerService(db).summary(
            workspace_id,
            date_from=date_from,
            date_to=date_to,
            provider=provider,
        )
    except ValueError as error:
        raise _http_error(error) from error
    return AdsManagerSummaryRead.model_validate(result)


@router.post(
    "/connections/{connection_id}/lead-captures/ingest-raw",
    response_model=LeadCaptureIngestRead,
    status_code=status.HTTP_201_CREATED,
    summary="Normalizar e capturar payload bruto sem chamada externa",
)
async def ingest_raw_lead_capture(
    connection_id: UUID,
    payload: RawLeadCaptureIngest,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
) -> LeadCaptureIngestRead:
    try:
        connection = await OmnichannelOrchestrationService(db).get_connection(
            workspace_id, connection_id
        )
        if connection.status in {"paused", "disconnected_local"}:
            raise ValueError("A fonte está pausada ou desconectada localmente.")
        if connection.provider not in {"meta_ads", "tiktok_ads", "site", "lovable"}:
            raise ValueError("Esta conexão não é uma fonte de captação compatível.")
        normalized = LeadCaptureAdapterService.normalize(
            connection,
            payload.raw_payload,
            proactive_message_template=payload.proactive_message_template,
        )
        result = await OmnichannelV6Service(db).ingest_lead_capture(
            workspace_id, auth.user_id, normalized
        )
    except (ValueError, LookupError, RuntimeError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(result["deduplicated"]).lower()
    response.headers["X-KLM-Normalizer"] = "native-v6"
    return _lead_capture_read(result, can_view_sensitive=can_view_sensitive)


@router.get(
    "/lead-captures",
    response_model=LeadCaptureInboxRead,
    summary="Listar captações de anúncios, formulários e site",
)
async def list_lead_captures(
    provider: str | None = Query(default=None, max_length=40),
    capture_state: str | None = Query(default=None, alias="state", max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("integrations", "view")),
) -> LeadCaptureInboxRead:
    result = await OmnichannelV6Service(db).list_lead_capture_receipts(
        workspace_id,
        provider=provider,
        state=capture_state,
        limit=limit,
        offset=offset,
    )
    items = []
    for item in result["items"]:
        identity = item["identity"]
        thread = item["thread"]
        items.append(
            LeadCaptureInboxItemRead(
                receipt=item["receipt"],
                identity=(
                    _identity_read(identity, can_view_sensitive=can_view_sensitive)
                    if identity
                    else None
                ),
                thread=(
                    thread if can_view_sensitive or thread is None else mask_klm_thread(thread)
                ),
                acquisition=item["acquisition"],
            )
        )
    return LeadCaptureInboxRead(
        items=items,
        total=result["total"],
        has_more=result["has_more"],
        counts_by_provider=result["counts_by_provider"],
        counts_by_state=result["counts_by_state"],
        generated_at=result["generated_at"],
    )


@router.get(
    "/lead-hydrations",
    response_model=list[LeadHydrationJobRead],
    summary="Listar buscas pendentes de dados de Lead Ads",
)
async def list_lead_hydrations(
    provider: str | None = Query(default=None, max_length=40),
    hydration_state: str | None = Query(default=None, alias="state", max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("integrations", "view")),
) -> list[LeadHydrationJobRead]:
    rows = await LeadHydrationService(db).list(
        workspace_id,
        provider=provider,
        state=hydration_state,
        limit=limit,
    )
    return [
        _lead_hydration_read(item, can_view_sensitive=can_view_sensitive)
        for item in rows
    ]


@router.patch(
    "/lead-hydrations/{job_id}",
    response_model=LeadHydrationJobRead,
    summary="Reenfileirar ou cancelar uma hidratação de lead",
)
async def transition_lead_hydration(
    job_id: UUID,
    payload: LeadHydrationAction,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
) -> LeadHydrationJobRead:
    try:
        job = await LeadHydrationService(db).transition(
            workspace_id,
            job_id,
            action=payload.action,
        )
    except (LookupError, ValueError) as error:
        raise _http_error(error) from error
    return _lead_hydration_read(job, can_view_sensitive=can_view_sensitive)


@router.get(
    "/lead-hydrations/{job_id}/events",
    response_model=list[LeadHydrationEventRead],
    summary="Listar evidências imutáveis da hidratação de lead",
)
async def list_lead_hydration_events(
    job_id: UUID,
    limit: int = Query(default=200, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("integrations", "view")),
) -> list[LeadHydrationEventRead]:
    try:
        rows = await LeadHydrationService(db).list_events(
            workspace_id,
            job_id,
            limit=limit,
        )
    except LookupError as error:
        raise _http_error(error) from error
    return [
        _lead_hydration_event_read(item, can_view_sensitive=can_view_sensitive)
        for item in rows
    ]


@router.get(
    "/threads/{thread_id}/scheduled-messages",
    response_model=list[ScheduledMessageRead],
    summary="Listar mensagens agendadas",
)
async def list_scheduled_messages(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> list[ScheduledMessageRead]:
    try:
        return await OmnichannelV6Service(db).list_scheduled_messages(workspace_id, thread_id)
    except LookupError as error:
        raise _http_error(error) from error


@router.post(
    "/threads/{thread_id}/scheduled-messages",
    response_model=ScheduledMessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Agendar mensagem sem envio externo no runtime local",
)
async def create_scheduled_message(
    thread_id: UUID,
    payload: ScheduledMessageCreate,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> ScheduledMessageRead:
    try:
        item, deduplicated = await OmnichannelV6Service(db).create_scheduled_message(
            workspace_id, thread_id, payload, user_id=auth.user_id
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(deduplicated).lower()
    return item


@router.patch(
    "/scheduled-messages/{scheduled_message_id}",
    response_model=ScheduledMessageRead,
    summary="Pausar, retomar, cancelar ou reagendar mensagem",
)
async def update_scheduled_message(
    scheduled_message_id: UUID,
    payload: ScheduledMessageUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> ScheduledMessageRead:
    try:
        return await OmnichannelV6Service(db).update_scheduled_message(
            workspace_id, scheduled_message_id, payload, user_id=auth.user_id
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error


@router.get(
    "/scheduled-messages/{scheduled_message_id}/events",
    response_model=list[ScheduledMessageEventRead],
    summary="Listar evidências imutáveis do agendamento",
)
async def list_scheduled_message_events(
    scheduled_message_id: UUID,
    limit: int = Query(default=200, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> list[ScheduledMessageEventRead]:
    try:
        return await OmnichannelV6Service(db).list_scheduled_message_events(
            workspace_id, scheduled_message_id, limit=limit
        )
    except LookupError as error:
        raise _http_error(error) from error


@router.get(
    "/threads/{thread_id}/calls",
    response_model=list[CallSessionRead],
    summary="Listar chamadas da conversa",
)
async def list_calls(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> list[CallSessionRead]:
    try:
        return await OmnichannelV6Service(db).list_calls(workspace_id, thread_id)
    except LookupError as error:
        raise _http_error(error) from error


@router.post(
    "/threads/{thread_id}/calls",
    response_model=CallSessionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar intenção de chamada local",
)
async def create_call(
    thread_id: UUID,
    payload: CallSessionCreate,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> CallSessionRead:
    try:
        call, deduplicated = await OmnichannelV6Service(db).create_call(
            workspace_id, thread_id, payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(deduplicated).lower()
    return call


@router.patch("/calls/{call_id}", response_model=CallSessionRead, summary="Atualizar chamada")
async def transition_call(
    call_id: UUID,
    payload: CallSessionTransition,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> CallSessionRead:
    try:
        return await OmnichannelV6Service(db).transition_call(workspace_id, call_id, payload)
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error


@router.get(
    "/calls/{call_id}/events",
    response_model=list[CallEventEvidenceRead],
    summary="Listar evidências imutáveis da chamada",
)
async def list_call_events(
    call_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> list[CallEventEvidenceRead]:
    try:
        evidence = await OmnichannelV6Service(db).list_call_events(workspace_id, call_id)
    except LookupError as error:
        raise _http_error(error) from error
    return [
        CallEventEvidenceRead(
            event=CallEventRead.model_validate(event),
        )
        for event, _costs in evidence
    ]


@router.get(
    "/threads/{thread_id}/timeline",
    response_model=ThreadTimelineRead,
    summary="Linha do tempo canônica da conversa",
)
async def thread_timeline(
    thread_id: UUID,
    limit: int = Query(default=200, ge=1, le=1000),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> ThreadTimelineRead:
    try:
        items = await OmnichannelV6Service(db).timeline(workspace_id, thread_id, limit=limit)
    except LookupError as error:
        raise _http_error(error) from error
    reads = [TimelineItemRead.model_validate(item) for item in items]
    if not can_view_sensitive:
        reads = [
            item.model_copy(
                update={
                    "body": "[conteúdo protegido pelo administrador]" if item.body else None,
                    "metadata": {"sensitiveDataHidden": True},
                }
            )
            for item in reads
        ]
    return ThreadTimelineRead(thread_id=thread_id, items=reads)
