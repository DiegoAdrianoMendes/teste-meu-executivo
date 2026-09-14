"""
Setup e onboarding de workspaces.
"""
from __future__ import annotations

from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
    require_permission,
    require_workspace_admin,
)
from app.core.logging import logger
from app.core.logging_privacy import opaque_ref
from app.core.redis_client import get_shared_redis_client
from app.schemas.ai_costs import (
    AiTokenPackageCatalogResponse,
    WorkspaceAiCostPolicyUpdate,
    WorkspaceAiTokenPackageRequestCreate,
    WorkspaceAiTokenPackageRequestRead,
    WorkspaceAiTokenSummaryRead,
)
from app.schemas.ai_operations import AiOperationsSummary
from app.schemas.commercial_catalog import EffectiveCapabilityManifestRead
from app.schemas.control_plane import WorkspaceOperationsRead, WorkspaceOperationsUpdate
from app.schemas.workspace import (
    WorkspaceAccessPackageRead,
    WorkspaceOnboardingStateRead,
    WorkspaceOnboardingUpdate,
    WorkspaceOwnerChannelRead,
    WorkspaceOwnerChannelUpdate,
    WorkspacePlanSummaryRead,
    WorkspaceSetupRequest,
    WorkspaceSetupResponse,
)
from app.services.ai_cost_service import AiCostService, AiCostWorkspaceNotFoundError
from app.services.ai_operations_service import AiOperationsService
from app.services.ai_token_package_service import (
    AiTokenPackageConflictError,
    AiTokenPackageNotFoundError,
    AiTokenPackageService,
)
from app.services.commercial_catalog_service import (
    CommercialCatalogService,
    CommercialCatalogValidationError,
    CommercialContractNotFoundError,
)
from app.services.workspace_policy_service import WorkspacePolicyService
from app.services.workspace_service import WorkspaceService

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


def _require_member_ai_cost_admin(actor: AuthContext) -> None:
    if actor.is_support_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AI-COST-002",
                "message": "A operação exige owner ou admin do workspace.",
            },
        )


def _require_member_owner_channel_admin(actor: AuthContext) -> None:
    if actor.is_support_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "WKS-010",
                "message": "O canal do dono só pode ser gerido por owner ou admin do workspace.",
            },
        )


@router.post(
    "/setup",
    response_model=WorkspaceSetupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Autosetup ou setup assistido",
)
async def setup_workspace(
    payload: WorkspaceSetupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not settings.SETUP_INVITE_TOKEN or payload.setup_token != settings.SETUP_INVITE_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "WKS-005",
                "message": "Setup indisponível sem token de convite válido.",
            },
        )

    client_ip = request.client.host if request.client else "unknown"
    if settings.SETUP_RATE_LIMIT_PER_HOUR > 0:
        try:
            redis = await get_shared_redis_client(
                settings.REDIS_URL,
                decode_responses=True,
                factory=aioredis.from_url,
            )
            key = f"setup-rate:{client_ip}"
            attempts = await redis.incr(key)
            if attempts == 1:
                await redis.expire(key, 3600)
            if attempts > settings.SETUP_RATE_LIMIT_PER_HOUR:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "code": "WKS-006",
                        "message": "Limite de tentativas de setup excedido. Tente mais tarde.",
                    },
                )
        except HTTPException:
            raise
        except Exception:
            logger.warning(
                "workspace_setup_rate_limit_unavailable",
                client_ref=opaque_ref(client_ip, kind="client_ip"),
            )

    service = WorkspaceService(db)
    response = await service.setup_workspace(payload)
    logger.info(
        "workspace_setup_created",
        workspace_id=str(response.workspace.id),
        workspace_ref=opaque_ref(response.workspace.slug, kind="workspace_slug"),
        client_ref=opaque_ref(client_ip, kind="client_ip"),
        mode=payload.mode.value,
    )
    return response


@router.get(
    "/plan-summary",
    response_model=WorkspacePlanSummaryRead | None,
    summary="Resumo de plano e consumo do workspace ativo",
)
async def get_workspace_plan_summary(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    workspace = await WorkspaceService(db).get_workspace(workspace_id)
    if not workspace:
        return None
    return await WorkspaceService(db)._build_plan_summary(workspace)


@router.get(
    "/capability-manifest",
    response_model=EffectiveCapabilityManifestRead,
    summary="Capacidades comerciais efetivas do workspace ativo",
)
async def get_workspace_capability_manifest(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> EffectiveCapabilityManifestRead:
    """Expõe ao próprio workspace somente o manifesto efetivo já resolvido.

    A rota não aceita ``workspace_id`` do cliente: o escopo vem do token (ou
    da sessão administrativa ativa) e continua sendo validado por
    ``get_current_workspace_id``. Ausência ou inconsistência do contrato é
    um estado operacional explícito; nunca cai silenciosamente no plano
    legado nem inventa capacidades.
    """

    try:
        return await CommercialCatalogService(db).resolve_effective_manifest(workspace_id)
    except CommercialContractNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "COMMERCIAL-MANIFEST-NOT-CONFIGURED",
                "message": "A política comercial desta conta ainda não foi configurada.",
            },
        ) from error
    except CommercialCatalogValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "COMMERCIAL-MANIFEST-INVALID",
                "message": "A política comercial da conta precisa ser revisada.",
            },
        ) from error


@router.get(
    "/me/access-package",
    response_model=WorkspaceAccessPackageRead | None,
    summary="Pacote de acesso do próprio usuário no workspace ativo (self-service)",
)
async def get_my_access_package(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Não exige permissão administrativa: qualquer membro autenticado pode consultar
    o próprio pacote de acesso para a seção "O que meu acesso inclui" do perfil."""
    if context.membership_id is None:
        return None
    return await WorkspaceService(db).get_membership_access_package(context.membership_id)


@router.get(
    "/ai-costs",
    response_model=WorkspaceAiTokenSummaryRead,
    summary="Uso e limite mensal de tokens de IA do workspace ativo",
)
async def get_workspace_ai_costs(
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceAiTokenSummaryRead:
    _require_member_ai_cost_admin(actor)
    try:
        summary = await AiCostService(db).get_summary(workspace_id=workspace_id)
        return WorkspaceAiTokenSummaryRead.model_validate(summary)
    except AiCostWorkspaceNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "AI-COST-001", "message": str(error)},
        ) from error


@router.get(
    "/ai-operations",
    response_model=AiOperationsSummary,
    summary="Operação de IA do workspace ativo (custo por classe, rotas, fallback)",
)
async def get_workspace_ai_operations(
    days: int = 7,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> AiOperationsSummary:
    _require_member_ai_cost_admin(actor)
    data = await AiOperationsService(db).summary(workspace_id=workspace_id, days=days)
    return AiOperationsSummary.model_validate(data)


@router.patch(
    "/ai-costs",
    response_model=WorkspaceAiTokenSummaryRead,
    summary="Atualizar o limite mensal de tokens de IA do workspace ativo",
)
async def update_workspace_ai_costs(
    payload: WorkspaceAiCostPolicyUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceAiTokenSummaryRead:
    _require_member_ai_cost_admin(actor)
    try:
        summary = await AiCostService(db).update_policy(
            workspace_id=workspace_id,
            payload=payload,
            actor_user_id=actor.user_id,
        )
        return WorkspaceAiTokenSummaryRead.model_validate(summary)
    except AiCostWorkspaceNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "AI-COST-001", "message": str(error)},
        ) from error


@router.get(
    "/ai-token-packages",
    response_model=AiTokenPackageCatalogResponse,
    summary="Catálogo e solicitações de pacotes extras de tokens",
)
async def get_workspace_ai_token_packages(
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> AiTokenPackageCatalogResponse:
    _require_member_ai_cost_admin(actor)
    service = AiTokenPackageService(db)
    packages = await service.list_catalog()
    requests = await service.list_workspace_requests(workspace_id=workspace_id)
    return AiTokenPackageCatalogResponse(packages=packages, requests=requests)


@router.post(
    "/ai-token-packages/requests",
    response_model=WorkspaceAiTokenPackageRequestRead,
    status_code=status.HTTP_201_CREATED,
    summary="Solicitar ao suporte um pacote extra de tokens",
)
async def request_workspace_ai_token_package(
    payload: WorkspaceAiTokenPackageRequestCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceAiTokenPackageRequestRead:
    _require_member_ai_cost_admin(actor)
    try:
        return await AiTokenPackageService(db).request_package(
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            payload=payload,
        )
    except AiTokenPackageNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "AI-TOKEN-PACKAGE-001", "message": str(error)},
        ) from error
    except AiTokenPackageConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "AI-TOKEN-PACKAGE-002", "message": str(error)},
        ) from error


@router.get(
    "/settings/operations",
    response_model=WorkspaceOperationsRead,
    summary="Operação simplificada do workspace",
)
async def get_workspace_operations(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("configuracoes", "view")),
    db: AsyncSession = Depends(get_db),
):
    return await WorkspacePolicyService(db).get_workspace_operations(workspace_id)


@router.patch(
    "/settings/operations",
    response_model=WorkspaceOperationsRead,
    summary="Atualizar áreas ativas, follow-up e intervenção humana do workspace",
)
async def update_workspace_operations(
    payload: WorkspaceOperationsUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("configuracoes", "edit")),
    db: AsyncSession = Depends(get_db),
):
    return await WorkspacePolicyService(db).update_workspace_operations(workspace_id, payload)


@router.get(
    "/settings/owner-channel",
    response_model=WorkspaceOwnerChannelRead,
    summary="Canal do dono (WhatsApp) das notificações de rotina do Hermes",
)
async def get_workspace_owner_channel(
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceOwnerChannelRead:
    _require_member_owner_channel_admin(actor)
    return await WorkspaceService(db).get_owner_channel(workspace_id)


@router.put(
    "/settings/owner-channel",
    response_model=WorkspaceOwnerChannelRead,
    summary="Configurar (ou remover) o WhatsApp do dono do escritório",
)
async def update_workspace_owner_channel(
    payload: WorkspaceOwnerChannelUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceOwnerChannelRead:
    """Somente admin/owner do workspace. Configurar o telefone NÃO abre gate
    externo: a entrega real continua condicionada a KLM_EXTERNAL_ACTIONS_ENABLED
    + KLM_ONLINE_APPROVED + MetaPolicyGate (fail-closed)."""
    _require_member_owner_channel_admin(actor)
    return await WorkspaceService(db).update_owner_channel(workspace_id, payload)


@router.get(
    "/onboarding",
    response_model=WorkspaceOnboardingStateRead,
    summary="Estado guiado do onboarding do workspace",
)
async def get_workspace_onboarding(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await WorkspaceService(db).get_workspace_onboarding(workspace_id)


@router.patch(
    "/onboarding",
    response_model=WorkspaceOnboardingStateRead,
    summary="Atualizar o onboarding guiado do workspace",
)
async def update_workspace_onboarding(
    payload: WorkspaceOnboardingUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("configuracoes", "edit")),
    db: AsyncSession = Depends(get_db),
):
    return await WorkspaceService(db).update_workspace_onboarding(workspace_id, payload)
