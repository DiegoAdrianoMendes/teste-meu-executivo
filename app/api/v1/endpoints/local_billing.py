"""Local-only SaaS billing catalog, portal, and lifecycle simulator."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.platform_access import require_platform_permission
from app.core.deps import (
    AuthContext,
    get_current_workspace_id,
    get_db,
    require_finance_access,
    require_workspace_admin,
)
from app.models.billing import PlanTier
from app.models.user import User
from app.schemas.local_billing import (
    LocalBillingCancellationRequest,
    LocalBillingCheckoutRequest,
    LocalBillingClosureIntentRequest,
    LocalBillingCommandResult,
    LocalBillingDunningRequest,
    LocalBillingExportIntentRequest,
    LocalBillingIntentRead,
    LocalBillingLifecycleRequest,
    LocalBillingPlanChangeRequest,
    LocalBillingPortalRead,
    LocalBillingQuoteRead,
    LocalBillingQuoteRequest,
    LocalBillingTrialRequest,
    LocalPlanCatalogItemRead,
    LocalPlanCatalogUpdate,
)
from app.services.local_billing_service import (
    LocalBillingConflictError,
    LocalBillingDisabledError,
    LocalBillingNotFoundError,
    LocalBillingService,
)

router = APIRouter(prefix="/local-billing", tags=["Local Billing"])
require_platform_billing_manage = require_platform_permission("billing", "manage")

T = TypeVar("T")


def require_local_billing_mutation_enabled() -> None:
    if settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "BILLING-LOCAL-DISABLED",
                "message": "Local billing mutations are disabled in production.",
            },
        )


async def _service_call(operation: Awaitable[T]) -> T:
    try:
        return await operation
    except LocalBillingNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "BILLING-404", "message": str(error)},
        ) from error
    except LocalBillingDisabledError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "BILLING-LOCAL-DISABLED", "message": str(error)},
        ) from error
    except LocalBillingConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "BILLING-409", "message": str(error)},
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "BILLING-422", "message": str(error)},
        ) from error


@router.get("/catalog", response_model=list[LocalPlanCatalogItemRead])
async def list_local_catalog(
    db: AsyncSession = Depends(get_db),
) -> list[LocalPlanCatalogItemRead]:
    return await _service_call(LocalBillingService(db).list_catalog(public_only=True))


@router.put(
    "/catalog/{tier}",
    response_model=LocalPlanCatalogItemRead,
    dependencies=[Depends(require_local_billing_mutation_enabled)],
)
async def update_local_catalog_item(
    tier: PlanTier,
    payload: LocalPlanCatalogUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_billing_manage),
) -> LocalPlanCatalogItemRead:
    return await _service_call(LocalBillingService(db).update_catalog_item(tier, payload))


@router.post("/catalog/quote", response_model=LocalBillingQuoteRead)
async def quote_local_checkout(
    payload: LocalBillingQuoteRequest,
    db: AsyncSession = Depends(get_db),
) -> LocalBillingQuoteRead:
    return await _service_call(
        LocalBillingService(db).quote(payload.plan_tier, payload.interval)
    )


@router.get("/portal", response_model=LocalBillingPortalRead)
async def get_local_billing_portal(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
) -> LocalBillingPortalRead:
    return await _service_call(LocalBillingService(db).get_portal(workspace_id))


@router.get("/intents", response_model=list[LocalBillingIntentRead])
async def list_local_billing_intents(
    limit: int = Query(default=50, ge=1, le=200),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
) -> list[LocalBillingIntentRead]:
    return await _service_call(
        LocalBillingService(db).list_intents(workspace_id, limit=limit)
    )


@router.post(
    "/trial",
    response_model=LocalBillingCommandResult,
    dependencies=[Depends(require_local_billing_mutation_enabled)],
)
async def start_local_trial(
    payload: LocalBillingTrialRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalBillingCommandResult:
    return await _service_call(
        LocalBillingService(db).provision_trial(
            workspace_id=workspace_id,
            plan_tier=payload.plan_tier,
            idempotency_key=payload.idempotency_key,
            actor_user_id=context.user_id,
            trial_days=payload.trial_days,
            effective_at=payload.effective_at,
        )
    )


@router.post(
    "/checkout",
    response_model=LocalBillingCommandResult,
    dependencies=[Depends(require_local_billing_mutation_enabled)],
)
async def simulate_local_checkout(
    payload: LocalBillingCheckoutRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalBillingCommandResult:
    return await _service_call(
        LocalBillingService(db).checkout(
            workspace_id=workspace_id,
            plan_tier=payload.plan_tier,
            interval=payload.interval,
            idempotency_key=payload.idempotency_key,
            actor_user_id=context.user_id,
            effective_at=payload.effective_at,
        )
    )


async def _change_plan(
    *,
    direction: str,
    payload: LocalBillingPlanChangeRequest,
    workspace_id: UUID,
    context: AuthContext,
    db: AsyncSession,
) -> LocalBillingCommandResult:
    return await _service_call(
        LocalBillingService(db).change_plan(
            workspace_id=workspace_id,
            plan_tier=payload.plan_tier,
            direction=direction,
            interval=payload.interval,
            idempotency_key=payload.idempotency_key,
            actor_user_id=context.user_id,
            effective_at=payload.effective_at,
        )
    )


@router.post(
    "/upgrade",
    response_model=LocalBillingCommandResult,
    dependencies=[Depends(require_local_billing_mutation_enabled)],
)
async def upgrade_local_plan(
    payload: LocalBillingPlanChangeRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalBillingCommandResult:
    return await _change_plan(
        direction="upgrade",
        payload=payload,
        workspace_id=workspace_id,
        context=context,
        db=db,
    )


@router.post(
    "/downgrade",
    response_model=LocalBillingCommandResult,
    dependencies=[Depends(require_local_billing_mutation_enabled)],
)
async def downgrade_local_plan(
    payload: LocalBillingPlanChangeRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalBillingCommandResult:
    return await _change_plan(
        direction="downgrade",
        payload=payload,
        workspace_id=workspace_id,
        context=context,
        db=db,
    )


@router.post(
    "/dunning",
    response_model=LocalBillingCommandResult,
    dependencies=[Depends(require_local_billing_mutation_enabled)],
)
async def start_local_dunning(
    payload: LocalBillingDunningRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalBillingCommandResult:
    return await _service_call(
        LocalBillingService(db).start_dunning(
            workspace_id=workspace_id,
            grace_days=payload.grace_days,
            idempotency_key=payload.idempotency_key,
            actor_user_id=context.user_id,
            effective_at=payload.effective_at,
        )
    )


@router.post(
    "/suspend",
    response_model=LocalBillingCommandResult,
    dependencies=[Depends(require_local_billing_mutation_enabled)],
)
async def suspend_local_subscription(
    payload: LocalBillingLifecycleRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalBillingCommandResult:
    return await _service_call(
        LocalBillingService(db).suspend(
            workspace_id=workspace_id,
            idempotency_key=payload.idempotency_key,
            actor_user_id=context.user_id,
            reason=payload.reason,
            effective_at=payload.effective_at,
        )
    )


@router.post(
    "/reactivate",
    response_model=LocalBillingCommandResult,
    dependencies=[Depends(require_local_billing_mutation_enabled)],
)
async def reactivate_local_subscription(
    payload: LocalBillingLifecycleRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalBillingCommandResult:
    return await _service_call(
        LocalBillingService(db).reactivate(
            workspace_id=workspace_id,
            idempotency_key=payload.idempotency_key,
            actor_user_id=context.user_id,
            reason=payload.reason,
            effective_at=payload.effective_at,
        )
    )


@router.post(
    "/cancel",
    response_model=LocalBillingCommandResult,
    dependencies=[Depends(require_local_billing_mutation_enabled)],
)
async def cancel_local_subscription(
    payload: LocalBillingCancellationRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalBillingCommandResult:
    return await _service_call(
        LocalBillingService(db).cancel(
            workspace_id=workspace_id,
            idempotency_key=payload.idempotency_key,
            actor_user_id=context.user_id,
            mode=payload.mode,
            reason=payload.reason,
            effective_at=payload.effective_at,
        )
    )


@router.post(
    "/intents/export",
    response_model=LocalBillingCommandResult,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_local_billing_mutation_enabled)],
)
async def create_local_export_intent(
    payload: LocalBillingExportIntentRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalBillingCommandResult:
    return await _service_call(
        LocalBillingService(db).create_export_intent(
            workspace_id=workspace_id,
            idempotency_key=payload.idempotency_key,
            actor_user_id=context.user_id,
            export_format=payload.export_format,
            scopes=payload.scopes,
            reason=payload.reason,
            effective_at=payload.effective_at,
        )
    )


@router.post(
    "/intents/closure",
    response_model=LocalBillingCommandResult,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_local_billing_mutation_enabled)],
)
async def create_local_closure_intent(
    payload: LocalBillingClosureIntentRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> LocalBillingCommandResult:
    return await _service_call(
        LocalBillingService(db).create_closure_intent(
            workspace_id=workspace_id,
            idempotency_key=payload.idempotency_key,
            actor_user_id=context.user_id,
            confirm_closure=payload.confirm_closure,
            reason=payload.reason,
            effective_at=payload.effective_at,
        )
    )
