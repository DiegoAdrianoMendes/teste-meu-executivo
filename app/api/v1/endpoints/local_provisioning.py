"""Local-only provisioning plan and reconciliation endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_workspace_admin
from app.schemas.local_provisioning import (
    LocalProvisioningCommandRequest,
    LocalProvisioningCommandResult,
    LocalProvisioningPlanRead,
    LocalProvisioningStateRead,
)
from app.services.local_provisioning_service import (
    LocalProvisioningConflictError,
    LocalProvisioningService,
)

router = APIRouter(prefix="/local-provisioning", tags=["Local Provisioning"])


@router.get("/plan", response_model=LocalProvisioningPlanRead)
async def preview_local_provisioning_plan(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalProvisioningPlanRead:
    return await LocalProvisioningService(db).preview(workspace_id)


@router.get("/status", response_model=LocalProvisioningStateRead)
async def get_local_provisioning_state(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalProvisioningStateRead:
    return await LocalProvisioningService(db).state(workspace_id)


async def _execute(
    *,
    action: str,
    payload: LocalProvisioningCommandRequest,
    workspace_id: UUID,
    context: AuthContext,
    db: AsyncSession,
) -> LocalProvisioningCommandResult:
    service = LocalProvisioningService(db)
    try:
        if action == "apply":
            return await service.apply(
                workspace_id=workspace_id,
                actor_user_id=context.user_id,
                idempotency_key=payload.idempotency_key,
                reason=payload.reason,
            )
        return await service.reconcile(
            workspace_id=workspace_id,
            actor_user_id=context.user_id,
            idempotency_key=payload.idempotency_key,
            reason=payload.reason,
        )
    except LocalProvisioningConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "PROVISIONING-409", "message": str(exc)},
        ) from exc


@router.post("/apply", response_model=LocalProvisioningCommandResult)
async def apply_local_provisioning_plan(
    payload: LocalProvisioningCommandRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalProvisioningCommandResult:
    return await _execute(
        action="apply",
        payload=payload,
        workspace_id=workspace_id,
        context=context,
        db=db,
    )


@router.post("/reconcile", response_model=LocalProvisioningCommandResult)
async def reconcile_local_provisioning_plan(
    payload: LocalProvisioningCommandRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalProvisioningCommandResult:
    return await _execute(
        action="reconcile",
        payload=payload,
        workspace_id=workspace_id,
        context=context,
        db=db,
    )
