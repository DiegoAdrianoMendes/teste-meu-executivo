"""Stable HTTP representation for entitlement enforcement failures."""
from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entitlement import EntitlementKey
from app.services.entitlement_service import (
    CurrentStockLimitExceededError,
    EntitlementDisabledError,
    EntitlementEnforcementService,
    EntitlementError,
    EntitlementIdempotencyConflictError,
    EntitlementNotConfiguredError,
    EntitlementWorkspaceNotFoundError,
    MonthlyQuotaExceededError,
    UsageInvariantError,
    UsageReservationNotFoundError,
    UsageReservationStateError,
)


def entitlement_http_exception(error: EntitlementError) -> HTTPException:
    detail: dict[str, object]
    if isinstance(error, CurrentStockLimitExceededError):
        detail = {
            "code": "ENT-003",
            "message": "O limite corrente deste recurso foi atingido.",
            "entitlement": error.entitlement.value,
            "usage": error.usage_quantity,
            "limit": error.limit_quantity,
            "remaining": error.remaining_quantity,
            "limitQuantity": error.limit_quantity,
            "currentQuantity": error.current_quantity,
            "requestedQuantity": error.requested_quantity,
        }
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    if isinstance(error, MonthlyQuotaExceededError):
        detail = {
            "code": "ENT-004",
            "message": "A quota mensal deste recurso foi atingida.",
            "entitlement": error.entitlement.value,
            "usage": error.usage_quantity,
            "limit": error.limit_quantity,
            "remaining": error.remaining_quantity,
            "limitQuantity": error.limit_quantity,
            "consumedQuantity": error.consumed_quantity,
            "reservedQuantity": error.reserved_quantity,
            "requestedQuantity": error.requested_quantity,
        }
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    if isinstance(error, EntitlementDisabledError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ENT-002",
                "message": "Este recurso não está habilitado no plano do workspace.",
            },
        )
    if isinstance(error, EntitlementNotConfiguredError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ENT-001",
                "message": "O plano do workspace não possui configuração válida para este recurso.",
            },
        )
    if isinstance(error, EntitlementIdempotencyConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ENT-005",
                "message": "A chave idempotente já foi usada com dados diferentes.",
            },
        )
    if isinstance(error, (UsageReservationStateError, UsageInvariantError)):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ENT-006",
                "message": "O estado da reserva de uso é incompatível com esta operação.",
            },
        )
    if isinstance(error, (EntitlementWorkspaceNotFoundError, UsageReservationNotFoundError)):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ENT-007", "message": "Recurso de entitlement não encontrado."},
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "ENT-000", "message": "A operação foi bloqueada pelo plano."},
    )


async def enforce_workspace_feature_or_http(
    db: AsyncSession,
    workspace_id: UUID,
    entitlement: EntitlementKey,
) -> None:
    try:
        await EntitlementEnforcementService(db).enforce_feature(
            workspace_id=workspace_id,
            entitlement=entitlement,
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
