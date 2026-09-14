from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.entitlement import EntitlementKey, PlanEntitlement, UsageReservationStatus
from app.services.entitlement_service import (
    ENTITLEMENT_CATALOG,
    FEATURE_ENTITLEMENTS,
    METERED_ENTITLEMENTS,
    STOCK_ENTITLEMENTS,
    EntitlementDisabledError,
    EntitlementEnforcementService,
    EntitlementIdempotencyConflictError,
    MonthlyQuotaExceededError,
    MonthlyUsageService,
    UsageReservationNotFoundError,
    UsageReservationStateError,
)
from app.services.local_billing_service import LocalBillingService


def test_entitlement_catalog_is_complete_and_categories_do_not_overlap() -> None:
    categories = (STOCK_ENTITLEMENTS, METERED_ENTITLEMENTS, FEATURE_ENTITLEMENTS)

    assert set(ENTITLEMENT_CATALOG) == set(EntitlementKey)
    assert set().union(*categories) == set(EntitlementKey)
    assert all(left.isdisjoint(right) for index, left in enumerate(categories) for right in categories[index + 1 :])


@pytest.mark.asyncio
async def test_local_catalog_seeds_every_typed_entitlement_for_every_plan(
    db_session: AsyncSession,
) -> None:
    plans = await LocalBillingService(db_session).ensure_catalog()

    for plan in plans:
        count = int(
            await db_session.scalar(
                select(func.count(PlanEntitlement.id)).where(
                    PlanEntitlement.plan_id == plan.id
                )
            )
            or 0
        )
        assert count == len(EntitlementKey)


@pytest.mark.asyncio
async def test_disabled_typed_feature_fails_closed(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    plan = Plan(
        name="Plano feature local",
        tier=PlanTier.business,
        price_monthly_cents=0,
        price_annually_cents=0,
        max_users=10,
        max_matters=None,
        max_tokens_per_month=100,
        max_ai_agents=10,
        features={},
        is_active=True,
        is_public=False,
        sort_order=1,
    )
    db_session.add(plan)
    await db_session.flush()
    db_session.add_all(
        [
            Subscription(
                workspace_id=auth_context.workspace_id,
                plan_id=plan.id,
                status=SubscriptionStatus.active,
                interval=BillingInterval.monthly,
                amount_cents=0,
                currency="BRL",
            ),
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.digital_signature,
                enabled=False,
                limit_quantity=None,
                hard_limit=True,
            ),
        ]
    )
    await db_session.flush()

    with pytest.raises(EntitlementDisabledError):
        await EntitlementEnforcementService(db_session).enforce_feature(
            workspace_id=auth_context.workspace_id,
            entitlement=EntitlementKey.digital_signature,
        )


async def _seed_subscription(
    db: AsyncSession,
    auth_context: AuthContext,
    *,
    limit: int,
) -> None:
    plan = Plan(
        name="Plano quota local",
        tier=PlanTier.pro,
        price_monthly_cents=0,
        price_annually_cents=0,
        max_users=10,
        max_matters=None,
        max_tokens_per_month=100,
        max_ai_agents=10,
        features={},
        is_active=True,
        is_public=False,
        sort_order=1,
    )
    db.add(plan)
    await db.flush()
    db.add(
        Subscription(
            workspace_id=auth_context.workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            amount_cents=0,
            currency="BRL",
        )
    )
    db.add(
        PlanEntitlement(
            plan_id=plan.id,
            entitlement=EntitlementKey.tokens,
            enabled=True,
            limit_quantity=limit,
            hard_limit=True,
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_usage_reservation_is_idempotent_and_enforces_hard_limit(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_subscription(db_session, auth_context, limit=10)
    service = MonthlyUsageService(db_session)

    reservation = await service.reserve(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.tokens,
        quantity=6,
        idempotency_key="agent-run:one",
    )
    replay = await service.reserve(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.tokens,
        quantity=6,
        idempotency_key="agent-run:one",
    )
    assert replay.id == reservation.id
    assert (await service.get_usage(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.tokens,
    )).reserved_quantity == 6

    with pytest.raises(EntitlementIdempotencyConflictError):
        await service.reserve(
            workspace_id=auth_context.workspace_id,
            entitlement=EntitlementKey.tokens,
            quantity=5,
            idempotency_key="agent-run:one",
        )
    with pytest.raises(MonthlyQuotaExceededError):
        await service.reserve(
            workspace_id=auth_context.workspace_id,
            entitlement=EntitlementKey.tokens,
            quantity=5,
            idempotency_key="agent-run:over-limit",
        )


@pytest.mark.asyncio
async def test_commit_release_and_workspace_scope_are_fail_closed(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_subscription(db_session, auth_context, limit=10)
    service = MonthlyUsageService(db_session)
    committed = await service.reserve(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.tokens,
        quantity=6,
        idempotency_key="agent-run:commit",
    )
    committed = await service.commit(
        workspace_id=auth_context.workspace_id,
        reservation_id=committed.id,
    )
    assert committed.status == UsageReservationStatus.committed
    assert committed.committed_quantity == 6
    replay = await service.commit(
        workspace_id=auth_context.workspace_id,
        reservation_id=committed.id,
    )
    assert replay.status == UsageReservationStatus.committed
    with pytest.raises(UsageReservationStateError):
        await service.release(
            workspace_id=auth_context.workspace_id,
            reservation_id=committed.id,
        )

    released = await service.reserve(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.tokens,
        quantity=4,
        idempotency_key="agent-run:release",
    )
    released = await service.release(
        workspace_id=auth_context.workspace_id,
        reservation_id=released.id,
    )
    assert released.status == UsageReservationStatus.released

    snapshot = await service.get_usage(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.tokens,
    )
    assert snapshot.consumed_quantity == 6
    assert snapshot.reserved_quantity == 0

    from uuid import uuid4

    with pytest.raises(UsageReservationNotFoundError):
        await service.commit(
            workspace_id=uuid4(),
            reservation_id=committed.id,
        )
