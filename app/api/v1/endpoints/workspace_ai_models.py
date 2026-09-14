"""
Roteamento configurável de modelos de IA por classe de tarefa do workspace.

GET: qualquer membro autenticado lê a configuração efetiva (override do
workspace ou padrão da plataforma) e o catálogo selecionável com preço por
1M de tokens — a UI congelada por permissão continua mostrando dados reais.
PATCH: restrito a owner/admin do workspace; toda mudança é validada contra o
catálogo do gateway (fail-closed), auditada e nunca abre gate externo.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
    require_workspace_admin,
)
from app.schemas.workspace_ai_models import (
    WorkspaceAiModelRoutingRead,
    WorkspaceAiModelRoutingUpdate,
)
from app.services.workspace_ai_model_routing_service import (
    AiModelRoutingWorkspaceNotFoundError,
    UnknownAiModelRouteError,
    UnknownAiTaskClassError,
    WorkspaceAiModelRoutingService,
)

router = APIRouter(prefix="/workspaces/ai-models", tags=["Workspaces"])


def _require_member_admin(actor: AuthContext) -> None:
    if actor.is_support_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AI-MODEL-004",
                "message": "A operação exige owner ou admin do workspace.",
            },
        )


def _http_error_for(error: Exception) -> HTTPException:
    if isinstance(error, AiModelRoutingWorkspaceNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "AI-MODEL-001", "message": str(error)},
        )
    if isinstance(error, UnknownAiTaskClassError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "AI-MODEL-002", "message": str(error)},
        )
    if isinstance(error, UnknownAiModelRouteError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "AI-MODEL-003", "message": str(error)},
        )
    raise error


@router.get(
    "",
    response_model=WorkspaceAiModelRoutingRead,
    summary="Roteamento de modelos por classe de tarefa e catálogo selecionável",
)
async def get_ai_model_routing(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceAiModelRoutingRead:
    try:
        return await WorkspaceAiModelRoutingService(db).get_routing(workspace_id)
    except AiModelRoutingWorkspaceNotFoundError as error:
        raise _http_error_for(error) from error


@router.patch(
    "",
    response_model=WorkspaceAiModelRoutingRead,
    summary="Atualizar o roteamento de modelos por classe de tarefa",
)
async def update_ai_model_routing(
    payload: WorkspaceAiModelRoutingUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    actor: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceAiModelRoutingRead:
    _require_member_admin(actor)
    try:
        return await WorkspaceAiModelRoutingService(db).update_routing(
            workspace_id=workspace_id,
            payload=payload,
            actor_user_id=actor.user_id,
        )
    except (
        AiModelRoutingWorkspaceNotFoundError,
        UnknownAiTaskClassError,
        UnknownAiModelRouteError,
    ) as error:
        raise _http_error_for(error) from error
