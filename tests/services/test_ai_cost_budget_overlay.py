from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
    TokenBudget,
)
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.services.entitlement_service import (
    EntitlementLimitSource,
    EntitlementResolver,
    MonthlyQuotaExceededError,
    MonthlyUsageService,
)


@pytest.mark.asyncio
async def test_token_budget_overlay_never_expands_typed_plan_and_can_reduce_it(
    db_session: AsyncSession,
    auth_context,
) -> None:
    plan = Plan(
        name="Plano tipado com overlay",
        tier=PlanTier.enterprise,
        price_monthly_cents=0,
        price_annually_cents=0,
        max_users=100,
        max_matters=None,
        max_tokens_per_month=1_000,
        max_ai_agents=100,
        features={},
        is_active=True,
        is_public=False,
        sort_order=1,
    )
    db_session.add(plan)
    await db_session.flush()
    entitlement = PlanEntitlement(
        plan_id=plan.id,
        entitlement=EntitlementKey.tokens,
        enabled=True,
        limit_quantity=1_000,
        hard_limit=True,
    )
    budget = TokenBudget(
        workspace_id=auth_context.workspace_id,
        monthly_limit=1_500,
        alert_at_pct=80,
        hard_limit=False,
    )
    db_session.add_all(
        [
            entitlement,
            budget,
            Subscription(
                workspace_id=auth_context.workspace_id,
                plan_id=plan.id,
                status=SubscriptionStatus.active,
                interval=BillingInterval.monthly,
                amount_cents=0,
                currency="BRL",
            ),
        ]
    )
    await db_session.flush()
    resolver = EntitlementResolver(db_session)

    plan_wins = await resolver.resolve(auth_context.workspace_id, EntitlementKey.tokens)
    assert plan_wins.limit_quantity == 1_000
    assert plan_wins.hard_limit is True
    assert plan_wins.source == EntitlementLimitSource.plan_entitlement

    budget.monthly_limit = 500
    await db_session.flush()
    soft_budget = await resolver.resolve(auth_context.workspace_id, EntitlementKey.tokens)
    assert soft_budget.limit_quantity == 1_000
    assert soft_budget.hard_limit is True
    assert soft_budget.source == EntitlementLimitSource.plan_entitlement

    budget.hard_limit = True
    await db_session.flush()
    budget_wins = await resolver.resolve(auth_context.workspace_id, EntitlementKey.tokens)
    assert budget_wins.limit_quantity == 500
    assert budget_wins.hard_limit is True
    assert budget_wins.source == EntitlementLimitSource.token_budget

    with pytest.raises(MonthlyQuotaExceededError):
        await MonthlyUsageService(db_session).reserve(
            workspace_id=auth_context.workspace_id,
            entitlement=EntitlementKey.tokens,
            quantity=501,
            idempotency_key="budget-overlay-block",
        )

    budget.monthly_limit = None
    budget.hard_limit = False
    await db_session.flush()
    removed_overlay = await resolver.resolve(auth_context.workspace_id, EntitlementKey.tokens)
    assert removed_overlay.limit_quantity == 1_000
    assert removed_overlay.hard_limit is True
    assert removed_overlay.source == EntitlementLimitSource.plan_entitlement

    entitlement.enabled = False
    budget.monthly_limit = 100
    budget.hard_limit = True
    await db_session.flush()
    disabled = await resolver.resolve(auth_context.workspace_id, EntitlementKey.tokens)
    assert disabled.enabled is False
    assert disabled.source == EntitlementLimitSource.plan_entitlement
