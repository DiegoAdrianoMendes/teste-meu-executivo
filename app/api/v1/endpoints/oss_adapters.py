"""Control plane and signed inbound webhooks for local OSS adapters."""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import quote
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    AuthContext,
    can_view_sensitive_lead_data,
    get_active_membership,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.rbac import has_effective_permission
from app.core.request_body import read_bounded_body
from app.db.tenant import WorkspaceContextError, set_workspace_context
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    OSSBridgeProvider,
)
from app.models.user import AgentType, User
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.oss_bridge import (
    BottleCRMFinanceContextRead,
    BottleCRMFunnelContextRead,
    BottleCRMMatterContextRead,
    BottleCRMWorkContextRead,
    ChatterMateAgentSyncRead,
    ChatterMateContextRead,
    ChatterMateFlowContextRead,
    ChatterMateFlowSyncRead,
    ChatwootRoutingContextRead,
    ChatwootRoutingWrite,
    ChatwootThreadSyncRead,
    OSSCapabilityRegistryRead,
    OSSRuntimeStatusRead,
    OSSServiceLaunchContextWrite,
    OSSServiceLaunchRead,
    RocketChatThreadContextRead,
)
from app.services.bottlecrm_client import BottleCRMClientError
from app.services.bottlecrm_operations_service import BottleCRMOperationsService
from app.services.bottlecrm_sync_service import BottleCRMSyncService
from app.services.chattermate_client import ChatterMateClientError
from app.services.chattermate_flow_service import ChatterMateFlowService
from app.services.chattermate_operations_service import ChatterMateOperationsService
from app.services.chatwoot_bridge_service import (
    ChatwootAccountBindingError,
    ChatwootBridgeService,
    chatwoot_account_id,
)
from app.services.chatwoot_client import ChatwootClient, ChatwootClientError
from app.services.chatwoot_operations_service import ChatwootOperationsService
from app.services.chatwoot_sync_service import ChatwootSyncService
from app.services.klm_operations_service import KlmOperationsService
from app.services.oss_bridge_service import (
    ExternalEntityLinkConflictError,
    IntegrationEventPayloadConflictError,
)
from app.services.oss_capability_registry import oss_capability_registry
from app.services.oss_launch_service import (
    OSSLaunchError,
    consume_launch_ticket,
    issue_launch_ticket,
)
from app.services.oss_motor_routes import (
    CHATTERMATE_AGENT_TABS,
    CHATWOOT_CONVERSATION_CAPABILITIES,
    ROCKETCHAT_ROOM_TABS,
)
from app.services.oss_runtime_service import oss_runtime_status
from app.services.rocketchat_bridge_service import RocketChatBridgeService
from app.services.rocketchat_client import RocketChatClient, RocketChatClientError
from app.services.rocketchat_operations_service import RocketChatOperationsService

router = APIRouter(prefix="/oss-adapters", tags=["OSS adapters"])

_OSS_ADMIN_ROLES = {WorkspaceRole.workspace_owner, WorkspaceRole.workspace_admin}
_OSS_BINDING_ENTITY_TYPES = {
    "chatwoot": "account",
    "rocketchat": "account",
    "chattermate": "organization",
    "bottlecrm": "organization",
}


async def _authorized_launch_binding(
    claims: dict,
    db: AsyncSession,
) -> tuple[User, WorkspaceMembership, ExternalEntityLink]:
    """Revalidate the signed actor and exact workspace/provider binding."""

    try:
        workspace_id = UUID(str(claims["workspaceId"]))
        actor_user_id = UUID(str(claims["actorUserId"]))
        membership_id = UUID(str(claims["membershipId"]))
        service = str(claims["service"])
        claimed_role = str(claims["actorRole"])
        provider = OSSBridgeProvider(service)
        external_entity_type = _OSS_BINDING_ENTITY_TYPES[service]
    except (KeyError, TypeError, ValueError) as error:
        raise OSSLaunchError("Identidade do bilhete local inválida.") from error

    await set_workspace_context(db, workspace_id)
    membership = await db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.id == membership_id,
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == actor_user_id,
            WorkspaceMembership.status == MembershipStatus.active,
        )
    )
    user = await db.scalar(
        select(User).where(
            User.id == actor_user_id,
            User.agent_type == AgentType.human,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
    )
    if membership is None or user is None:
        raise OSSLaunchError("O usuário do bilhete não possui mais acesso ativo.")
    if membership.role.value != claimed_role or membership.role not in _OSS_ADMIN_ROLES:
        raise OSSLaunchError("O papel administrativo do bilhete não é mais válido.")
    if not has_effective_permission(
        membership.role,
        "integrations",
        "manage",
        membership.permission_grants,
        membership.permission_denials,
    ):
        raise OSSLaunchError("A permissão administrativa do bilhete foi revogada.")

    bindings = list(
        await db.scalars(
            select(ExternalEntityLink).where(
                ExternalEntityLink.workspace_id == workspace_id,
                ExternalEntityLink.provider == provider,
                ExternalEntityLink.klm_entity_type == "workspace",
                ExternalEntityLink.klm_entity_id == workspace_id,
                ExternalEntityLink.external_entity_type == external_entity_type,
                ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
            )
        )
    )
    if len(bindings) != 1:
        raise OSSLaunchError("O workspace não possui um vínculo único e ativo com este motor.")
    binding = bindings[0]
    if binding.external_account_id != binding.external_entity_id:
        raise OSSLaunchError("O vínculo de identidade do motor está inconsistente.")
    return user, membership, binding


def _require_bound_motor_identity(binding: ExternalEntityLink, observed_id: object) -> None:
    if str(observed_id or "").strip() != binding.external_account_id:
        raise OSSLaunchError("A sessão do motor pertence a outra conta ou organização.")


def _rocketchat_account_id(info: dict) -> str:
    account_id = str(
        info.get("hashedWorkspaceUrl")
        or info.get("cloudWorkspaceId")
        or info.get("uniqueID")
        or ""
    ).strip()
    if not account_id:
        raise OSSLaunchError("Rocket.Chat não informou sua identidade estável.")
    return account_id


async def _record_admin_motor_audit(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    actor_user_id: UUID,
    membership_id: UUID,
    service: str,
    event_type: str,
    summary: str,
    claims: dict | None = None,
) -> None:
    context = (claims or {}).get("context") or {}
    nonce = str((claims or {}).get("nonce") or "")
    await KlmOperationsService(db).record_audit(
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        actor_type="human",
        actor_label="Usuário KLM",
        event_type=event_type,
        entity_type="workspace_membership",
        entity_id=membership_id,
        summary=summary,
        correlation_id=(hashlib.sha256(nonce.encode("utf-8")).hexdigest()[:24] if nonce else None),
        after_data={
            "service": service,
            "capabilityId": context.get("capabilityId"),
            "surface": context.get("surface"),
            "adminSession": True,
            "externalActionsExecuted": 0,
        },
    )


def _launch_capability_key(claims: dict) -> str:
    capability_id = str((claims.get("context") or {}).get("capabilityId") or "")
    return capability_id.partition(".")[2]


async def _resolve_contextual_destination(
    claims: dict,
    db: AsyncSession,
) -> str | None:
    """Replace a safe fallback with a case/conversation-specific upstream route."""

    service = str(claims.get("service") or "")
    context = claims.get("context") or {}
    fallback = context.get("destinationPath")
    capability_key = _launch_capability_key(claims)
    workspace_id = UUID(str(claims["workspaceId"]))
    await set_workspace_context(db, workspace_id)

    raw_thread_id = context.get("threadId")
    thread_id: UUID | None = None
    if raw_thread_id:
        try:
            thread_id = UUID(str(raw_thread_id))
        except ValueError:
            thread_id = None

    raw_flow_id = context.get("flowId")
    flow_id: UUID | None = None
    if raw_flow_id:
        try:
            flow_id = UUID(str(raw_flow_id))
        except ValueError:
            flow_id = None

    if (
        service == "chatwoot"
        and thread_id is not None
        and capability_key in CHATWOOT_CONVERSATION_CAPABILITIES
    ):
        conversation_id = await ChatwootOperationsService(db).conversation_id(
            workspace_id, thread_id
        )
        if conversation_id:
            account_id = quote(str(settings.KLM_CHATWOOT_ACCOUNT_ID), safe="")
            conversation = quote(str(conversation_id), safe="")
            return f"/app/accounts/{account_id}/conversations/{conversation}"

    if (
        service == "rocketchat"
        and thread_id is not None
        and capability_key in ROCKETCHAT_ROOM_TABS
    ):
        target = await RocketChatOperationsService(db).launch_target(
            workspace_id, thread_id
        )
        if target:
            room_type = target["roomType"]
            prefix = {"p": "group", "c": "channel", "d": "direct"}.get(
                room_type, "group"
            )
            route_identity = (
                target["roomId"] if room_type == "d" else target["roomName"]
            )
            destination = f"/{prefix}/{quote(route_identity, safe='')}"
            tab = ROCKETCHAT_ROOM_TABS[capability_key]
            return f"{destination}/{quote(tab, safe='')}" if tab else destination

    if service == "chattermate" and capability_key in CHATTERMATE_AGENT_TABS:
        agent_id = (
            await ChatterMateFlowService(db).flow_agent_id(workspace_id, flow_id)
            if flow_id is not None
            else await ChatterMateOperationsService(db).launch_agent_id(workspace_id)
        )
        if agent_id:
            tab = CHATTERMATE_AGENT_TABS[capability_key]
            return (
                f"/ai-agents?agent={quote(agent_id, safe='')}"
                f"&tab={quote(tab, safe='')}"
            )

    return str(fallback) if fallback else None


@router.get(
    "/status",
    response_model=OSSRuntimeStatusRead,
    summary="Estado real dos sidecars e padrões OSS locais",
)
async def get_oss_runtime_status(
    _: AuthContext = Depends(require_permission("integrations", "view")),
) -> OSSRuntimeStatusRead:
    return OSSRuntimeStatusRead.model_validate(await oss_runtime_status())


@router.get(
    "/capabilities",
    response_model=OSSCapabilityRegistryRead,
    summary="Matriz explícita de incorporação das capacidades OSS comunitárias",
)
async def get_oss_capability_registry(
    _: AuthContext = Depends(require_permission("integrations", "view")),
) -> OSSCapabilityRegistryRead:
    return OSSCapabilityRegistryRead.model_validate(oss_capability_registry())


@router.post(
    "/services/{service}/launch",
    response_model=OSSServiceLaunchRead,
    summary="Emitir abertura autenticada e local de um módulo OSS",
)
async def launch_oss_service(
    service: str,
    payload: OSSServiceLaunchContextWrite | None = None,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("integrations", "manage")),
) -> OSSServiceLaunchRead:
    try:
        membership = await get_active_membership(auth, db)
        if membership.role not in _OSS_ADMIN_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Somente owners e administradores podem abrir a sessão completa do motor.",
            )
        context = payload.model_dump(by_alias=True, exclude_none=True) if payload else None
        launch = issue_launch_ticket(
            service,
            workspace_id,
            actor_user_id=auth.user_id,
            membership_id=membership.id,
            actor_role=membership.role.value,
            context=context,
        )
        await _record_admin_motor_audit(
            db,
            workspace_id=workspace_id,
            actor_user_id=auth.user_id,
            membership_id=membership.id,
            service=service,
            event_type="oss_admin_session_ticket_issued",
            summary=f"Bilhete administrativo de uso único emitido para {service}.",
            claims={"context": context or {}},
        )
        return OSSServiceLaunchRead.model_validate(launch)
    except OSSLaunchError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/chatwoot/sync",
    summary="Projetar contatos, conversas e mensagens KLM no Chatwoot local",
)
async def sync_chatwoot_workspace(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
) -> dict:
    try:
        result = await ChatwootSyncService(db).sync_workspace(workspace_id)
    except (ChatwootClientError, LookupError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"status": "synced", **result}


@router.post(
    "/chatwoot/threads/{thread_id}/sync",
    response_model=ChatwootThreadSyncRead,
    summary="Projetar uma conversa KLM específica no Chatwoot local",
)
async def sync_chatwoot_thread(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> ChatwootThreadSyncRead:
    try:
        result = await ChatwootSyncService(db).sync_thread_by_id(
            workspace_id,
            thread_id,
            auth.user_id,
        )
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ChatwootClientError, RuntimeError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ChatwootThreadSyncRead.model_validate(result)


@router.get(
    "/chatwoot/threads/{thread_id}/context",
    response_model=ChatwootRoutingContextRead,
    summary="Ler o contexto operacional Chatwoot vinculado à thread KLM",
)
async def get_chatwoot_thread_context(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> ChatwootRoutingContextRead:
    try:
        context = await ChatwootOperationsService(db).context(workspace_id, thread_id)
    except ChatwootClientError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ChatwootRoutingContextRead.model_validate(context)


@router.patch(
    "/chatwoot/threads/{thread_id}/routing",
    response_model=ChatwootRoutingContextRead,
    summary="Atualizar status, prioridade, labels e atribuição no Chatwoot local",
)
async def update_chatwoot_thread_routing(
    thread_id: UUID,
    payload: ChatwootRoutingWrite,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> ChatwootRoutingContextRead:
    try:
        context = await ChatwootOperationsService(db).update_routing(
            workspace_id=workspace_id,
            thread_id=thread_id,
            actor_user_id=auth.user_id,
            changes=payload.model_dump(exclude_unset=True),
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ChatwootClientError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ChatwootRoutingContextRead.model_validate(context)


@router.post(
    "/rocketchat/sync",
    summary="Reconciliar canais, salas de caso e notas internas no Rocket.Chat local",
)
async def sync_rocketchat_workspace(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
) -> dict:
    try:
        result = await RocketChatBridgeService(db).sync_workspace(workspace_id)
    except (RocketChatClientError, LookupError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"status": "synced", **result}


@router.get(
    "/rocketchat/threads/{thread_id}/context",
    response_model=RocketChatThreadContextRead,
    summary="Projetar sala, membros, histórico e discussões Rocket.Chat no KLM",
)
async def get_rocketchat_thread_context(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> RocketChatThreadContextRead:
    try:
        context = await RocketChatOperationsService(db).context(workspace_id, thread_id)
    except RocketChatClientError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not can_view_sensitive:
        context = {
            **context,
            "room": {
                **(context.get("room") or {}),
                "name": "sala-restrita",
                "fname": "Sala interna restrita",
            }
            if context.get("room")
            else None,
            "messages": [
                {
                    **message,
                    "msg": "[conteúdo oculto pela permissão de dados do lead]",
                    "user": {"name": "Membro da equipe"},
                }
                for message in context.get("messages", [])
            ],
            "discussions": [
                {**discussion, "name": "discussão-restrita", "fname": "Discussão restrita"}
                for discussion in context.get("discussions", [])
            ],
            "sensitiveDataHidden": True,
        }
    return RocketChatThreadContextRead.model_validate(context)


@router.post(
    "/rocketchat/threads/{thread_id}/sync",
    summary="Projetar somente a conversa interna selecionada no Rocket.Chat local",
)
async def sync_rocketchat_thread(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> dict:
    try:
        result = await RocketChatBridgeService(db).sync_thread(workspace_id, thread_id)
    except (RocketChatClientError, LookupError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"status": "synced", **result}


@router.get(
    "/chattermate/context",
    response_model=ChatterMateContextRead,
    summary="Projetar agentes, workflows, conhecimento e telemetria ChatterMate no KLM",
)
async def get_chattermate_context(
    time_range: str = Query(default="7d", pattern="^(24h|7d|30d|90d)$"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("agents", "view")),
) -> ChatterMateContextRead:
    try:
        context = await ChatterMateOperationsService(db).context(workspace_id, time_range)
    except (ChatterMateClientError, PermissionError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ChatterMateContextRead.model_validate(context)


@router.post(
    "/chattermate/sync-agents",
    response_model=ChatterMateAgentSyncRead,
    summary="Reconciliar os agentes canônicos do KLM no ChatterMate local",
)
async def sync_chattermate_agents(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("agents", "manage")),
) -> ChatterMateAgentSyncRead:
    try:
        result = await ChatterMateOperationsService(db).sync_agents(workspace_id, auth.user_id)
    except (ChatterMateClientError, PermissionError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ChatterMateAgentSyncRead.model_validate({"status": "synced", **result})


@router.get(
    "/chattermate/flows/{flow_id}/context",
    response_model=ChatterMateFlowContextRead,
    summary="Conferir o espelho visual ChatterMate de um flow canônico do KLM",
)
async def get_chattermate_flow_context(
    flow_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("automations", "manage")),
) -> ChatterMateFlowContextRead:
    try:
        context = await ChatterMateFlowService(db).context(workspace_id, flow_id)
    except (ChatterMateClientError, LookupError, PermissionError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ChatterMateFlowContextRead.model_validate(context)


@router.post(
    "/chattermate/flows/{flow_id}/sync",
    response_model=ChatterMateFlowSyncRead,
    summary="Espelhar em modo draft um flow canônico no editor visual ChatterMate",
)
async def sync_chattermate_flow(
    flow_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("automations", "manage")),
) -> ChatterMateFlowSyncRead:
    try:
        result = await ChatterMateFlowService(db).sync_flow(
            workspace_id,
            flow_id,
            auth.user_id,
        )
    except (ChatterMateClientError, LookupError, PermissionError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ChatterMateFlowSyncRead.model_validate(result)


@router.post(
    "/bottlecrm/sync",
    summary="Projetar clientes, operadoras, demandas e tarefas no BottleCRM local",
)
async def sync_bottlecrm_workspace(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("integrations", "manage")),
) -> dict:
    try:
        result = await BottleCRMSyncService(db).sync_workspace(workspace_id)
    except (BottleCRMClientError, LookupError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"status": "synced", **result}


@router.get(
    "/bottlecrm/funnel/context",
    response_model=BottleCRMFunnelContextRead,
    summary="Ler pipeline, aging, metas e SLA projetados no BottleCRM local",
)
async def get_bottlecrm_funnel_context(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("kanban", "view")),
) -> BottleCRMFunnelContextRead:
    try:
        context = await BottleCRMOperationsService(db).funnel_context(
            workspace_id,
            include_sensitive=can_view_sensitive,
        )
    except BottleCRMClientError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return BottleCRMFunnelContextRead.model_validate(context)


@router.get(
    "/bottlecrm/matters/{matter_id}/context",
    response_model=BottleCRMMatterContextRead,
    summary="Ler o espelho BottleCRM vinculado a um caso jurídico KLM",
)
async def get_bottlecrm_matter_context(
    matter_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("lista", "view")),
) -> BottleCRMMatterContextRead:
    try:
        context = await BottleCRMOperationsService(db).matter_context(
            workspace_id,
            matter_id,
            include_sensitive=can_view_sensitive,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except BottleCRMClientError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return BottleCRMMatterContextRead.model_validate(context)


@router.get(
    "/bottlecrm/work/context",
    response_model=BottleCRMWorkContextRead,
    summary="Ler tarefas, kanban operacional e boards do BottleCRM local",
)
async def get_bottlecrm_work_context(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("lista", "view")),
) -> BottleCRMWorkContextRead:
    try:
        context = await BottleCRMOperationsService(db).work_context(
            workspace_id,
            include_sensitive=can_view_sensitive,
        )
    except BottleCRMClientError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return BottleCRMWorkContextRead.model_validate(context)


@router.get(
    "/bottlecrm/finance/context",
    response_model=BottleCRMFinanceContextRead,
    summary="Ler faturamento, propostas e aging financeiro do BottleCRM local",
)
async def get_bottlecrm_finance_context(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("finance", "view")),
) -> BottleCRMFinanceContextRead:
    try:
        context = await BottleCRMOperationsService(db).finance_context(
            workspace_id,
            include_sensitive=can_view_sensitive,
        )
    except BottleCRMClientError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return BottleCRMFinanceContextRead.model_validate(context)


@router.post(
    "/rocketchat/webhooks/{workspace_id}",
    summary="Ingerir conversa interna do Rocket.Chat local",
)
async def receive_rocketchat_webhook(
    workspace_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    raw_body = await read_bounded_body(
        request,
        too_large_detail="Webhook Rocket.Chat excede 2 MiB.",
    )
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail="JSON Rocket.Chat inválido.") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload Rocket.Chat deve ser um objeto.")
    token = str(payload.get("token") or "")
    expected_token = settings.KLM_ROCKETCHAT_WEBHOOK_TOKEN.strip()
    if not expected_token or not hmac.compare_digest(token, expected_token):
        raise HTTPException(status_code=403, detail="Token Rocket.Chat inválido.")
    try:
        await set_workspace_context(db, workspace_id)
        return await RocketChatBridgeService(db).ingest_webhook(
            workspace_id=workspace_id,
            payload=payload,
        )
    except WorkspaceContextError as error:
        raise HTTPException(status_code=403, detail="Workspace não autorizado.") from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except (LookupError, ValueError, RocketChatClientError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get(
    "/local-session/{service}",
    include_in_schema=False,
)
async def open_local_oss_session(
    service: str,
    ticket: str = Query(min_length=32, max_length=4096),
    db: AsyncSession = Depends(get_db),
):
    try:
        claims = consume_launch_ticket(ticket, service)
    except OSSLaunchError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error

    try:
        actor_user, membership, binding = await _authorized_launch_binding(claims, db)
        destination = await _resolve_contextual_destination(claims, db)
    except (OSSLaunchError, WorkspaceContextError) as error:
        raise HTTPException(status_code=403, detail="Workspace local não autorizado.") from error
    except (ChatwootClientError, ChatterMateClientError, RocketChatClientError) as error:
        raise HTTPException(
            status_code=503,
            detail="Não foi possível resolver o contexto no motor local.",
        ) from error

    if service == "chatwoot":
        if not settings.KLM_CHATWOOT_ADMIN_EMAIL or not settings.KLM_CHATWOOT_ADMIN_PASSWORD:
            raise HTTPException(status_code=503, detail="Login local do Chatwoot não configurado.")
        try:
            account = await ChatwootClient().account()
            _require_bound_motor_identity(binding, account.get("id"))
            async with httpx.AsyncClient(
                base_url=settings.KLM_CHATWOOT_URL,
                timeout=10,
                follow_redirects=False,
            ) as client:
                login = await client.post(
                    "/auth/sign_in",
                    json={
                        "email": settings.KLM_CHATWOOT_ADMIN_EMAIL,
                        "password": settings.KLM_CHATWOOT_ADMIN_PASSWORD,
                    },
                )
        except OSSLaunchError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except (httpx.HTTPError, ChatwootClientError) as error:
            raise HTTPException(status_code=503, detail="Chatwoot local indisponível.") from error
        if login.status_code != 200:
            raise HTTPException(status_code=503, detail="Login local do Chatwoot rejeitado.")
        auth_headers = {
            key: login.headers.get(key, "")
            for key in ("access-token", "client", "uid", "expiry", "token-type")
        }
        if not auth_headers["access-token"] or not auth_headers["client"]:
            raise HTTPException(status_code=503, detail="Chatwoot não retornou uma sessão válida.")
        response = RedirectResponse(
            url=destination or f"/app/accounts/{settings.KLM_CHATWOOT_ACCOUNT_ID}/dashboard",
            status_code=303,
        )
        response.set_cookie(
            "cw_d_session_info",
            json.dumps(auth_headers, separators=(",", ":")),
            max_age=3600,
            path="/",
            httponly=False,
            secure=False,
            samesite="lax",
        )
        await _record_admin_motor_audit(
            db,
            workspace_id=membership.workspace_id,
            actor_user_id=actor_user.id,
            membership_id=membership.id,
            service=service,
            event_type="oss_admin_session_opened",
            summary="Sessão administrativa local do Chatwoot aberta.",
            claims=claims,
        )
        return response

    if service == "rocketchat":
        if not settings.KLM_ROCKETCHAT_ADMIN_USERNAME or not settings.KLM_ROCKETCHAT_ADMIN_PASSWORD:
            raise HTTPException(
                status_code=503, detail="Login local do Rocket.Chat não configurado."
            )
        try:
            info = await RocketChatClient().info()
            _require_bound_motor_identity(binding, _rocketchat_account_id(info))
            async with httpx.AsyncClient(
                base_url=settings.KLM_ROCKETCHAT_URL,
                timeout=10,
                follow_redirects=False,
            ) as client:
                login = await client.post(
                    "/api/v1/login",
                    json={
                        "user": settings.KLM_ROCKETCHAT_ADMIN_USERNAME,
                        "password": settings.KLM_ROCKETCHAT_ADMIN_PASSWORD,
                    },
                )
        except OSSLaunchError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except (httpx.HTTPError, RocketChatClientError, ValueError) as error:
            raise HTTPException(
                status_code=503, detail="Rocket.Chat local indisponível."
            ) from error
        data = (login.json().get("data") if login.status_code == 200 else None) or {}
        resume_token = str(data.get("authToken") or "")
        if not resume_token:
            raise HTTPException(status_code=503, detail="Login local do Rocket.Chat rejeitado.")
        destination = str(destination or "/")
        separator = "&" if "?" in destination else "?"
        response = RedirectResponse(
            url=f"{destination}{separator}resumeToken={resume_token}", status_code=303
        )
        await _record_admin_motor_audit(
            db,
            workspace_id=membership.workspace_id,
            actor_user_id=actor_user.id,
            membership_id=membership.id,
            service=service,
            event_type="oss_admin_session_opened",
            summary="Sessão administrativa local do Rocket.Chat aberta.",
            claims=claims,
        )
        return response

    if service == "chattermate":
        if not settings.KLM_CHATTERMATE_ADMIN_EMAIL or not settings.KLM_CHATTERMATE_ADMIN_PASSWORD:
            raise HTTPException(
                status_code=503,
                detail="Login local do ChatterMate não configurado.",
            )
        try:
            async with httpx.AsyncClient(
                base_url=settings.KLM_CHATTERMATE_URL,
                timeout=15,
                follow_redirects=False,
            ) as client:
                login = await client.post(
                    "/api/v1/users/login",
                    data={
                        "username": settings.KLM_CHATTERMATE_ADMIN_EMAIL,
                        "password": settings.KLM_CHATTERMATE_ADMIN_PASSWORD,
                    },
                )
        except httpx.HTTPError as error:
            raise HTTPException(
                status_code=503,
                detail="ChatterMate local indisponível.",
            ) from error
        data = login.json() if login.status_code == 200 else {}
        chatter_user = data.get("user") if isinstance(data, dict) else None
        if (
            not isinstance(chatter_user, dict)
            or chatter_user.get("email") != settings.KLM_CHATTERMATE_ADMIN_EMAIL
        ):
            raise HTTPException(status_code=503, detail="Login local do ChatterMate rejeitado.")
        try:
            _require_bound_motor_identity(binding, chatter_user.get("organization_id"))
        except OSSLaunchError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        response = RedirectResponse(url=str(destination or "/"), status_code=303)
        for cookie in login.headers.get_list("set-cookie"):
            response.raw_headers.append((b"set-cookie", cookie.encode("latin-1")))
        await _record_admin_motor_audit(
            db,
            workspace_id=membership.workspace_id,
            actor_user_id=actor_user.id,
            membership_id=membership.id,
            service=service,
            event_type="oss_admin_session_opened",
            summary="Sessão administrativa local do ChatterMate aberta.",
            claims=claims,
        )
        return response

    if service == "bottlecrm":
        bridge_secret = settings.KLM_BOTTLECRM_BRIDGE_SECRET.strip()
        if len(bridge_secret) < 32:
            raise HTTPException(
                status_code=503,
                detail="Sessão local do BottleCRM não configurada.",
            )
        try:
            async with httpx.AsyncClient(
                base_url=settings.KLM_BOTTLECRM_URL,
                timeout=15,
                follow_redirects=False,
            ) as client:
                login = await client.post(
                    "/api/auth/klm-local-session/",
                    headers={"X-KLM-Local-Secret": bridge_secret},
                )
        except httpx.HTTPError as error:
            raise HTTPException(status_code=503, detail="BottleCRM local indisponível.") from error
        try:
            data = login.json() if login.status_code == 200 else {}
        except ValueError:
            data = {}
        access_token = str(data.get("access_token") or "")
        refresh_token = str(data.get("refresh_token") or "")
        org_id = str(data.get("org_id") or "")
        if not access_token or not refresh_token or not org_id:
            raise HTTPException(status_code=503, detail="Sessão local do BottleCRM rejeitada.")
        try:
            _require_bound_motor_identity(binding, org_id)
        except OSSLaunchError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error

        bootstrap_payload = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "org_id": org_id,
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            .decode("ascii")
            .rstrip("=")
        )
        response = RedirectResponse(url=str(destination or "/"), status_code=303)
        for name, value, max_age in (
            ("jwt_access", access_token, 3600),
            ("jwt_refresh", refresh_token, 14 * 24 * 3600),
            ("org", org_id, 14 * 24 * 3600),
        ):
            response.set_cookie(
                name,
                value,
                max_age=max_age,
                path="/",
                httponly=True,
                secure=False,
                samesite="lax",
            )
        response.set_cookie(
            "klm_bottle_bootstrap",
            bootstrap_payload,
            max_age=30,
            path="/",
            httponly=False,
            secure=False,
            samesite="strict",
        )
        await _record_admin_motor_audit(
            db,
            workspace_id=membership.workspace_id,
            actor_user_id=actor_user.id,
            membership_id=membership.id,
            service=service,
            event_type="oss_admin_session_opened",
            summary="Sessão administrativa local do BottleCRM aberta.",
            claims=claims,
        )
        return response

    raise HTTPException(status_code=404, detail="Módulo local desconhecido.")


def _verify_chatwoot_signature(raw_body: bytes, request: Request) -> str:
    secret = settings.KLM_CHATWOOT_WEBHOOK_SECRET
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook Chatwoot não configurado.",
        )
    if len(raw_body) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Webhook Chatwoot excede 2 MiB.")
    delivery_id = request.headers.get("X-Chatwoot-Delivery", "").strip()
    timestamp = request.headers.get("X-Chatwoot-Timestamp", "").strip()
    signature = request.headers.get("X-Chatwoot-Signature", "").strip()
    if not delivery_id or len(delivery_id) > 255 or not timestamp or not signature:
        raise HTTPException(status_code=403, detail="Headers assinados do Chatwoot ausentes.")
    try:
        timestamp_value = int(timestamp)
    except ValueError as error:
        raise HTTPException(status_code=403, detail="Timestamp Chatwoot inválido.") from error
    if abs(int(time.time()) - timestamp_value) > settings.KLM_CHATWOOT_WEBHOOK_MAX_AGE_SECONDS:
        raise HTTPException(status_code=403, detail="Webhook Chatwoot expirado.")
    expected = (
        "sha256="
        + hmac.new(
            secret.encode("utf-8"),
            timestamp.encode("ascii") + b"." + raw_body,
            hashlib.sha256,
        ).hexdigest()
    )
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=403, detail="Assinatura Chatwoot inválida.")
    return delivery_id


@router.post(
    "/chatwoot/webhooks/{workspace_id}",
    summary="Ingerir evento Chatwoot assinado e deduplicado",
)
async def receive_chatwoot_webhook(
    workspace_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    raw_body = await read_bounded_body(
        request,
        too_large_detail="Webhook Chatwoot excede 2 MiB.",
    )
    delivery_id = _verify_chatwoot_signature(raw_body, request)
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail="JSON Chatwoot inválido.") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload Chatwoot deve ser um objeto.")

    try:
        payload_account = chatwoot_account_id(payload)
        await set_workspace_context(db, workspace_id)
    except ChatwootAccountBindingError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except WorkspaceContextError as error:
        raise HTTPException(
            status_code=403,
            detail="Workspace do webhook não autorizado.",
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    configured_account = settings.KLM_CHATWOOT_ACCOUNT_ID.strip()
    if configured_account and payload_account != configured_account:
        raise HTTPException(status_code=403, detail="Conta Chatwoot não autorizada.")

    try:
        return await ChatwootBridgeService(db).ingest(
            workspace_id=workspace_id,
            delivery_id=delivery_id,
            payload=payload,
            account_id=payload_account,
        )
    except ChatwootAccountBindingError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except IntegrationEventPayloadConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ExternalEntityLinkConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
