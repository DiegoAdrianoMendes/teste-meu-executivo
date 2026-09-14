"""
Endpoint: Central de Exceções (Plano Mestre §6/§3).

Generaliza a fila de aprovações (`app/api/v1/endpoints/approvals.py`) —
reaproveita o mesmo gate de capability (`aprovacoes`) porque é o mesmo
domínio de decisão humana, só que aberto a todo tipo de exceção nomeado
no plano, não só a revisões de minuta.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_user_id,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.models.legal_exception import ExceptionCenterStatus, ExceptionKind
from app.schemas.exception_center import (
    ExceptionCenterRead,
    ExceptionEscalateRequest,
    ExceptionResolveRequest,
)
from app.services.exception_center_service import (
    ExceptionCenterAlreadyResolvedError,
    ExceptionCenterNotFoundError,
    ExceptionCenterService,
)

router = APIRouter(prefix="/legal/exceptions", tags=["Central de Exceções"])

require_exceptions_view = require_permission("aprovacoes", "view")
require_exceptions_resolve = require_permission("aprovacoes", "approve")


@router.get(
    "",
    response_model=List[ExceptionCenterRead],
    summary="Listar exceções da Central de Exceções, filtrando por tipo/estado",
)
async def list_exceptions(
    exception_kind: Optional[ExceptionKind] = Query(default=None, alias="kind"),
    exception_status: Optional[ExceptionCenterStatus] = Query(default=None, alias="status"),
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_exceptions_view),
) -> List[ExceptionCenterRead]:
    service = ExceptionCenterService(db, workspace_id)
    items = await service.list_exceptions(
        exception_kind=exception_kind,
        status_filter=exception_status,
        skip=skip,
        limit=limit,
    )
    return [ExceptionCenterRead.model_validate(item) for item in items]


@router.post(
    "/{exception_id}/resolver",
    response_model=ExceptionCenterRead,
    summary="Resolver uma exceção aberta — autoria sempre do usuário autenticado",
)
async def resolve_exception(
    exception_id: UUID,
    resolution: ExceptionResolveRequest,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_exceptions_resolve),
) -> ExceptionCenterRead:
    service = ExceptionCenterService(db, workspace_id)
    try:
        item = await service.resolve(
            exception_id,
            resolved_by=current_user_id,
            resolution_reason=resolution.resolution_reason,
        )
    except ExceptionCenterNotFoundError as error:
        raise service.not_found_http() from error
    except ExceptionCenterAlreadyResolvedError as error:
        raise service.already_resolved_http(error.item) from error
    await db.commit()
    return ExceptionCenterRead.model_validate(item)


@router.post(
    "/{exception_id}/escalar",
    response_model=ExceptionCenterRead,
    status_code=status.HTTP_200_OK,
    summary="Escalar uma exceção aberta — autoria sempre do usuário autenticado",
)
async def escalate_exception(
    exception_id: UUID,
    escalation: ExceptionEscalateRequest,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_exceptions_resolve),
) -> ExceptionCenterRead:
    service = ExceptionCenterService(db, workspace_id)
    try:
        item = await service.escalate(
            exception_id,
            escalated_by=current_user_id,
            escalated_to=escalation.escalated_to,
            resolution_reason=escalation.resolution_reason,
        )
    except ExceptionCenterNotFoundError as error:
        raise service.not_found_http() from error
    except ExceptionCenterAlreadyResolvedError as error:
        raise service.already_resolved_http(error.item) from error
    await db.commit()
    return ExceptionCenterRead.model_validate(item)
