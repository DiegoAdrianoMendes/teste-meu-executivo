from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.entitlement import PlanEntitlement
from app.models.klm_operations import ExternalActionIntent
from app.models.subscription_access import (
    SubscriptionAccessState,
    SubscriptionAccessStatus,
    SubscriptionAccessTransition,
)
from app.services.local_billing_service import LocalBillingService


@pytest.mark.asyncio
async def test_public_catalog_quote_and_platform_catalog_edit(
    unauthenticated_client,
    platform_client,
    db_session,
):
    await LocalBillingService(db_session).ensure_catalog()
    await db_session.commit()
    catalog_response = await unauthenticated_client.get("/api/v1/local-billing/catalog")
    quote_response = await unauthenticated_client.post(
        "/api/v1/local-billing/catalog/quote",
        json={"planTier": "pro", "interval": "monthly"},
    )
    private_plan_quote = await unauthenticated_client.post(
        "/api/v1/local-billing/catalog/quote",
        json={"planTier": "enterprise", "interval": "monthly"},
    )
    update_response = await platform_client.put(
        "/api/v1/local-billing/catalog/pro",
        json={
            "priceMonthlyCents": 75_000,
            "entitlements": [
                {
                    "entitlement": "users",
                    "enabled": True,
                    "limitQuantity": 12,
                    "hardLimit": True,
                }
            ],
        },
    )

    assert catalog_response.status_code == 200
    assert {item["tier"] for item in catalog_response.json()} == {
        tier.value for tier in PlanTier if tier != PlanTier.enterprise
    }
    assert quote_response.status_code == 200
    assert quote_response.json()["externalEffects"] == 0
    assert private_plan_quote.status_code == 404
    assert update_response.status_code == 200
    assert update_response.json()["priceMonthlyCents"] == 75_000


@pytest.mark.asyncio
async def test_catalog_get_is_read_only_when_catalog_is_empty(
    unauthenticated_client,
    db_session,
):
    response = await unauthenticated_client.get("/api/v1/local-billing/catalog")

    assert response.status_code == 200
    assert response.json() == []
    assert await db_session.scalar(select(func.count(Plan.id))) == 0
    assert await db_session.scalar(select(func.count(PlanEntitlement.id))) == 0


@pytest.mark.asyncio
async def test_portal_get_does_not_bootstrap_missing_access_projection(
    client,
    db_session,
    auth_context,
):
    plan = Plan(
        name="Read-only portal",
        tier=PlanTier.free,
        price_monthly_cents=0,
        price_annually_cents=0,
        features={},
    )
    db_session.add(plan)
    await db_session.flush()
    subscription = Subscription(
        workspace_id=auth_context.workspace_id,
        plan_id=plan.id,
        status=SubscriptionStatus.active,
        interval=BillingInterval.monthly,
        current_period_start=datetime.now(UTC),
        current_period_end=datetime.now(UTC) + timedelta(days=30),
        amount_cents=0,
    )
    db_session.add(subscription)
    await db_session.commit()

    response = await client.get("/api/v1/local-billing/portal")

    assert response.status_code == 200, response.text
    assert response.json()["access"]["state"] is None
    assert await db_session.scalar(select(func.count(SubscriptionAccessState.id))) == 0
    assert await db_session.scalar(select(func.count(SubscriptionAccessTransition.id))) == 0


@pytest.mark.asyncio
async def test_portal_get_does_not_reconcile_expired_access_projection(
    client,
    db_session,
    auth_context,
):
    now = datetime.now(UTC)
    trial_end = now - timedelta(days=1)
    plan = Plan(
        name="Expired read-only portal",
        tier=PlanTier.free,
        price_monthly_cents=0,
        price_annually_cents=0,
        features={},
    )
    db_session.add(plan)
    await db_session.flush()
    subscription = Subscription(
        workspace_id=auth_context.workspace_id,
        plan_id=plan.id,
        status=SubscriptionStatus.trialing,
        interval=BillingInterval.monthly,
        current_period_start=now - timedelta(days=15),
        current_period_end=trial_end,
        trial_end=trial_end,
        amount_cents=0,
    )
    db_session.add(subscription)
    await db_session.flush()
    access_state = SubscriptionAccessState(
        workspace_id=auth_context.workspace_id,
        subscription_id=subscription.id,
        status=SubscriptionAccessStatus.trial,
        state_version=1,
        status_changed_at=now - timedelta(days=15),
        trial_ends_at=trial_end,
    )
    db_session.add(access_state)
    await db_session.commit()
    access_state_id = access_state.id

    response = await client.get("/api/v1/local-billing/portal")

    assert response.status_code == 200, response.text
    assert response.json()["access"]["state"] == SubscriptionAccessStatus.trial.value
    db_session.expire_all()
    persisted = await db_session.get(SubscriptionAccessState, access_state_id)
    assert persisted is not None
    assert persisted.status == SubscriptionAccessStatus.trial
    assert await db_session.scalar(select(func.count(SubscriptionAccessTransition.id))) == 0


@pytest.mark.asyncio
async def test_portal_get_does_not_finalize_due_period_end_cancellation(
    client,
    db_session,
    auth_context,
):
    now = datetime.now(UTC)
    period_end = now - timedelta(seconds=1)
    plan = Plan(
        name="Due cancellation read-only portal",
        tier=PlanTier.pro,
        price_monthly_cents=69_700,
        price_annually_cents=669_120,
        features={},
    )
    db_session.add(plan)
    await db_session.flush()
    subscription = Subscription(
        workspace_id=auth_context.workspace_id,
        plan_id=plan.id,
        status=SubscriptionStatus.active,
        interval=BillingInterval.monthly,
        current_period_start=period_end - timedelta(days=30),
        current_period_end=period_end,
        cancel_at_period_end=True,
        amount_cents=69_700,
    )
    db_session.add(subscription)
    await db_session.flush()
    access_state = SubscriptionAccessState(
        workspace_id=auth_context.workspace_id,
        subscription_id=subscription.id,
        status=SubscriptionAccessStatus.active,
        state_version=1,
        status_changed_at=period_end - timedelta(days=30),
    )
    db_session.add(access_state)
    await db_session.commit()
    subscription_id = subscription.id
    access_state_id = access_state.id

    response = await client.get("/api/v1/local-billing/portal")

    assert response.status_code == 200, response.text
    assert response.json()["subscription"]["status"] == SubscriptionStatus.active.value
    assert response.json()["subscription"]["cancelAtPeriodEnd"] is True
    assert response.json()["access"]["state"] == SubscriptionAccessStatus.active.value
    db_session.expire_all()
    persisted_subscription = await db_session.get(Subscription, subscription_id)
    persisted_state = await db_session.get(SubscriptionAccessState, access_state_id)
    assert persisted_subscription is not None
    assert persisted_subscription.status == SubscriptionStatus.active
    assert persisted_subscription.cancel_at_period_end is True
    assert persisted_state is not None
    assert persisted_state.status == SubscriptionAccessStatus.active
    assert await db_session.scalar(select(func.count(SubscriptionAccessTransition.id))) == 0


@pytest.mark.asyncio
async def test_workspace_api_trial_is_canonical_and_checkout_fails_closed_without_provider(
    client,
):
    trial = await client.post(
        "/api/v1/local-billing/trial",
        json={"planTier": "pro", "idempotencyKey": "api-trial"},
    )
    checkout = await client.post(
        "/api/v1/local-billing/checkout",
        json={
            "planTier": "pro",
            "interval": "monthly",
            "idempotencyKey": "api-checkout",
        },
    )
    checkout_replay = await client.post(
        "/api/v1/local-billing/checkout",
        json={
            "planTier": "pro",
            "interval": "monthly",
            "idempotencyKey": "api-checkout",
        },
    )
    conflict = await client.post(
        "/api/v1/local-billing/checkout",
        json={
            "planTier": "business",
            "interval": "monthly",
            "idempotencyKey": "api-checkout",
        },
    )
    export_intent = await client.post(
        "/api/v1/local-billing/intents/export",
        json={
            "idempotencyKey": "api-export",
            "exportFormat": "zip",
            "scopes": ["workspace", "documents"],
        },
    )
    portal = await client.get("/api/v1/local-billing/portal")

    assert trial.status_code == 200
    assert checkout.status_code == 403
    assert checkout.json()["detail"]["code"] == "BILLING-LOCAL-DISABLED"
    assert "demonstração" in checkout.json()["detail"]["message"]
    assert checkout_replay.status_code == 403
    assert conflict.status_code == 403
    assert export_intent.status_code == 202
    assert export_intent.json()["intent"]["payload"]["externalEffects"] == 0
    assert portal.status_code == 200
    assert portal.json()["simulated"] is True
    assert portal.json()["externalEffects"] == 0
    assert portal.json()["access"]["state"] == SubscriptionAccessStatus.trial.value


@pytest.mark.asyncio
async def test_local_billing_catalog_edit_requires_platform_admin(client):
    response = await client.put(
        "/api/v1/local-billing/catalog/pro",
        json={"priceMonthlyCents": 1},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_suspended_workspace_can_reach_portal_and_reactivate(client):
    await client.post(
        "/api/v1/local-billing/trial",
        json={"planTier": "pro", "idempotencyKey": "recover-trial"},
    )
    suspended = await client.post(
        "/api/v1/local-billing/suspend",
        json={"idempotencyKey": "recover-suspend"},
    )
    blocked_workspace_write = await client.post(
        "/api/v1/matters",
        json={},
    )
    portal = await client.get("/api/v1/local-billing/portal")
    reactivated = await client.post(
        "/api/v1/local-billing/reactivate",
        json={"idempotencyKey": "recover-reactivate"},
    )
    restored_workspace_route = await client.get("/api/v1/matters")

    assert suspended.status_code == 200
    assert suspended.json()["portal"]["access"]["state"] == "suspended"
    assert blocked_workspace_write.status_code == 403
    assert blocked_workspace_write.json()["detail"]["code"] == (
        "SUBSCRIPTION_ACCESS_DENIED"
    )
    assert portal.status_code == 200
    assert portal.json()["access"]["readAllowed"] is False
    assert reactivated.status_code == 200
    assert reactivated.json()["portal"]["access"]["state"] == "active"
    assert restored_workspace_route.status_code == 200


@pytest.mark.asyncio
async def test_production_http_probe_blocks_local_mutations_but_keeps_reads_safe(
    client,
    db_session,
    monkeypatch,
):
    await LocalBillingService(db_session).ensure_catalog()
    await db_session.commit()
    seeded_catalog = await client.get("/api/v1/local-billing/catalog")
    assert seeded_catalog.status_code == 200
    monkeypatch.setattr(settings, "APP_ENV", "production")

    catalog = await client.get("/api/v1/local-billing/catalog")
    quote = await client.post(
        "/api/v1/local-billing/catalog/quote",
        json={"planTier": "pro", "interval": "monthly"},
    )
    mutation = await client.post(
        "/api/v1/local-billing/trial",
        json={"planTier": "pro", "idempotencyKey": "production-http-trial"},
    )
    intent = await client.post(
        "/api/v1/local-billing/intents/export",
        json={
            "idempotencyKey": "production-http-export",
            "exportFormat": "zip",
            "scopes": ["workspace"],
        },
    )

    assert catalog.status_code == 200
    assert quote.status_code == 200
    assert mutation.status_code == 403
    assert mutation.json()["detail"]["code"] == "BILLING-LOCAL-DISABLED"
    assert intent.status_code == 403
    assert intent.json()["detail"]["code"] == "BILLING-LOCAL-DISABLED"
    assert await db_session.scalar(select(func.count(Subscription.id))) == 0
    assert await db_session.scalar(select(func.count(ExternalActionIntent.id))) == 0
