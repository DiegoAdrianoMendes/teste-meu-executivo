"""
Endpoint: Approvals
Fila de aprovação humana (Human-in-the-loop).
"""
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    can_view_sensitive_lead_data,
    get_current_user_id,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.lead_privacy import mask_approval
from app.schemas.approval import ApprovalCreate, ApprovalRead, ApprovalResolveRequest
from app.services.approval_service import ApprovalService

router = APIRouter(prefix="/approvals", tags=["Aprovações"])


@router.get(
    "/pending",
    response_model=list[ApprovalRead],
    summary="Listar aprovações pendentes",
)
async def list_pending(
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("aprovacoes", "view")),
):
    """
    Retorna todos os itens pendentes na fila de aprovação.
    Inclui clientName desnormalizado para a tabela da UI.
    """
    service = ApprovalService(db, workspace_id)
    items = await service.get_pending()
    return items if can_view_sensitive else [mask_approval(item) for item in items]


@router.get("", response_model=list[ApprovalRead], summary="Listar todas as aprovações")
async def list_all(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("aprovacoes", "view")),
):
    """Retorna todas as aprovações (pendentes e resolvidas)."""
    service = ApprovalService(db, workspace_id)
    items = await service.get_all(skip=skip, limit=limit)
    return items if can_view_sensitive else [mask_approval(item) for item in items]


@router.post(
    "",
    response_model=ApprovalRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar aprovação",
)
async def create_approval(
    data: ApprovalCreate,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("aprovacoes", "approve")),
):
    """Cria um item na fila de aprovação e sinaliza o card como needsApproval=true."""
    data.requested_by = current_user_id
    service = ApprovalService(db, workspace_id)
    item = await service.create(data)
    return item if can_view_sensitive else mask_approval(item)


@router.post("/{approval_id}/resolve", response_model=ApprovalRead, summary="Resolver aprovação")
async def resolve_approval(
    approval_id: UUID,
    resolution: ApprovalResolveRequest,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("aprovacoes", "approve")),
):
    """
    Aprova, rejeita ou solicita alterações em um item da fila.

    Loop Human-in-the-loop:
    - approved: limpa needsApproval, avança para checklist_final.
    - rejected: devolve para ajustes_solicitados e persiste o reprocessamento.
    - changes_requested: mantém em revisão_interna e persiste o reprocessamento.
    """
    resolution.resolved_by = current_user_id
    service = ApprovalService(db, workspace_id)
    item = await service.resolve(approval_id, resolution)
    return item if can_view_sensitive else mask_approval(item)
