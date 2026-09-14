import asyncio
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
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.klm_operations import ExternalActionIntent, ExternalActionIntentStatus
from app.models.subscription_access import (
    SubscriptionAccessStatus,
    SubscriptionAccessTransition,
)
from app.schemas.local_billing import (
    LocalBillingCancellationMode,
    LocalPlanCatalogUpdate,
    LocalPlanEntitlementWrite,
)
from app.services.entitlement_service import EntitlementResolver
from app.services.local_billing_service import (
    LocalBillingConflictError,
    LocalBillingDirectionError,
    LocalBillingDisabledError,
    LocalBillingService,
    LocalBillingStateError,
)
from app.services.subscription_access_policy import SubscriptionAccessTimeConflictError

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_trial_provisioning_seeds_typed_catalog_and_replays_without_duplicates(
    db_session,
    auth_context,
):
    service = LocalBillingService(db_session, clock=lambda: NOW)

    created = await service.provision_trial(
        workspace_id=auth_context.workspace_id,
        plan_tier="basic",
        idempotency_key="trial-001",
        actor_user_id=auth_context.user_id,
    )
    replayed = await service.provision_trial(
        workspace_id=auth_context.workspace_id,
        plan_tier="basic",
        idempotency_key="trial-001",
        actor_user_id=auth_context.user_id,
    )

    assert created.replayed is False
    assert replayed.replayed is True
    assert created.portal.subscription is not None
    assert created.portal.subscription.plan_tier == PlanTier.starter
    assert created.portal.subscription.amount_cents == 0
    assert created.portal.access.state == SubscriptionAccessStatus.trial
    assert created.portal.access.trial_ends_at == NOW + timedelta(days=14)
    assert all(item.configured for item in created.portal.entitlements)
    assert len(created.portal.entitlements) == len(EntitlementKey)

    # The deprecated table now contains only the three public Core projections;
    # Free/Enterprise are not sellable catalog products in V1.
    assert await db_session.scalar(select(func.count(Plan.id))) == 3
    assert await db_session.scalar(select(func.count(PlanEntitlement.id))) == (
        3 * len(EntitlementKey)
    )
    assert await db_session.scalar(select(func.count(Subscription.id))) == 1
    assert await db_session.scalar(select(func.count(ExternalActionIntent.id))) == 1
    assert await db_session.scalar(select(func.count(SubscriptionAccessTransition.id))) == 2

    subscription = await db_session.scalar(select(Subscription))
    assert subscription is not None
    starter_plan = await db_session.get(Plan, subscription.plan_id)
    assert starter_plan is not None
    assert starter_plan.features["canonicalManaged"] is True
    assert starter_plan.features["canonicalSku"] == "crm_start"
    assert subscription.stripe_customer_id is None
    assert subscription.stripe_subscription_id is None
    assert subscription.stripe_payment_method_id is None

    with pytest.raises(LocalBillingConflictError, match="chave idempotente"):
        await service.provision_trial(
            workspace_id=auth_context.workspace_id,
            plan_tier=PlanTier.pro,
            idempotency_key="trial-001",
            actor_user_id=auth_context.user_id,
        )


@pytest.mark.asyncio
async def test_full_local_lifecycle_uses_access_policy_for_every_state(
    db_session,
    auth_context,
):
    clock = [NOW]
    service = LocalBillingService(db_session, clock=lambda: clock[0])
    await service.provision_trial(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.pro,
        idempotency_key="lifecycle-trial",
        actor_user_id=auth_context.user_id,
    )

    with pytest.raises(LocalBillingDisabledError, match="demonstração"):
        await service.checkout(
            workspace_id=auth_context.workspace_id,
            plan_tier=PlanTier.pro,
            interval=BillingInterval.monthly,
            idempotency_key="lifecycle-checkout",
            actor_user_id=auth_context.user_id,
        )

    upgrade = await service.change_plan(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.business,
        direction="upgrade",
        interval=None,
        idempotency_key="lifecycle-upgrade",
        actor_user_id=auth_context.user_id,
    )
    assert upgrade.portal.subscription is not None
    assert upgrade.portal.subscription.plan_tier == PlanTier.business
    await db_session.commit()

    with pytest.raises(LocalBillingDirectionError, match="upgrade válido"):
        await service.change_plan(
            workspace_id=auth_context.workspace_id,
            plan_tier=PlanTier.starter,
            direction="upgrade",
            interval=None,
            idempotency_key="invalid-upgrade",
            actor_user_id=auth_context.user_id,
        )
    await db_session.rollback()

    # The failed command was rolled back with its intent; restore the tenant context
    # through a fresh service before continuing the same committed test scenario.
    service = LocalBillingService(db_session, clock=lambda: clock[0])
    downgrade = await service.change_plan(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.starter,
        direction="downgrade",
        interval=BillingInterval.monthly,
        idempotency_key="lifecycle-downgrade",
        actor_user_id=auth_context.user_id,
    )
    assert downgrade.portal.subscription is not None
    assert downgrade.portal.subscription.plan_tier == PlanTier.starter
    assert downgrade.portal.subscription.interval == BillingInterval.monthly

    dunning = await service.start_dunning(
        workspace_id=auth_context.workspace_id,
        grace_days=3,
        idempotency_key="lifecycle-dunning",
        actor_user_id=auth_context.user_id,
    )
    assert dunning.portal.access.state == SubscriptionAccessStatus.grace
    assert dunning.portal.access.write_allowed is True

    clock[0] = NOW + timedelta(days=3)
    await service.access_policy.reconcile(
        auth_context.workspace_id,
        evaluated_at=clock[0],
    )
    expired = await service.get_portal(auth_context.workspace_id)
    assert expired.access.state == SubscriptionAccessStatus.past_due
    assert expired.access.read_allowed is True
    assert expired.access.write_allowed is False

    suspended = await service.suspend(
        workspace_id=auth_context.workspace_id,
        idempotency_key="lifecycle-suspend",
        actor_user_id=auth_context.user_id,
    )
    assert suspended.portal.access.state == SubscriptionAccessStatus.suspended
    assert suspended.portal.access.read_allowed is False

    reactivated = await service.reactivate(
        workspace_id=auth_context.workspace_id,
        idempotency_key="lifecycle-reactivate",
        actor_user_id=auth_context.user_id,
    )
    assert reactivated.portal.access.state == SubscriptionAccessStatus.active

    canceled = await service.cancel(
        workspace_id=auth_context.workspace_id,
        idempotency_key="lifecycle-cancel",
        actor_user_id=auth_context.user_id,
        mode=LocalBillingCancellationMode.immediate,
        reason="Customer requested immediate cancellation.",
    )
    assert canceled.portal.access.state == SubscriptionAccessStatus.canceled
    assert canceled.portal.access.read_allowed is True
    assert canceled.portal.access.write_allowed is False


@pytest.mark.asyncio
async def test_legacy_catalog_edit_cannot_override_canonical_contract_entitlements(
    db_session,
    auth_context,
):
    service = LocalBillingService(db_session, clock=lambda: NOW)
    updated = await service.update_catalog_item(
        PlanTier.pro,
        LocalPlanCatalogUpdate(
            price_monthly_cents=80_000,
            price_annually_cents=768_000,
            entitlements=[
                LocalPlanEntitlementWrite(
                    entitlement=EntitlementKey.users,
                    limit_quantity=7,
                )
            ],
        ),
    )
    quote = await service.quote(PlanTier.pro, BillingInterval.annually)
    await service.provision_trial(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.pro,
        idempotency_key="catalog-trial",
        actor_user_id=auth_context.user_id,
    )
    resolved = await EntitlementResolver(db_session).resolve(
        auth_context.workspace_id,
        EntitlementKey.users,
    )

    assert updated.price_monthly_cents == 80_000
    assert quote.amount_cents == 768_000
    assert quote.external_effects == 0
    assert resolved.limit_quantity == 10
    assert resolved.source.value == "commercial_contract"

    catalog_again = await service.list_catalog()
    pro = next(item for item in catalog_again if item.tier == PlanTier.pro)
    users = next(
        item for item in pro.entitlements if item.entitlement == EntitlementKey.users
    )
    assert pro.price_monthly_cents == 67_900
    assert users.limit_quantity == 10


@pytest.mark.asyncio
async def test_catalog_bootstrap_preserves_existing_legacy_plan_limits(db_session):
    plan = Plan(
        name="Existing Pro",
        tier=PlanTier.pro,
        description="Existing editable catalog row.",
        price_monthly_cents=55_000,
        price_annually_cents=528_000,
        max_users=6,
        max_matters=None,
        max_tokens_per_month=250,
        max_ai_agents=12,
        features={
            "digitalSignature": True,
            "maxConnectedWhatsapps": 3,
            "maxAiConversationsPerMonth": 250,
            "automationLimit": None,
        },
        is_active=True,
        is_public=True,
        sort_order=2,
    )
    db_session.add(plan)
    await db_session.flush()

    await LocalBillingService(db_session, clock=lambda: NOW).ensure_catalog()
    rows = list(
        (
            await db_session.scalars(
                select(PlanEntitlement).where(PlanEntitlement.plan_id == plan.id)
            )
        ).all()
    )
    limits = {row.entitlement: row.limit_quantity for row in rows}

    assert plan.price_monthly_cents == 55_000
    assert limits[EntitlementKey.users] == 6
    assert limits[EntitlementKey.tokens] == 250
    assert limits[EntitlementKey.agents] == 12
    assert limits[EntitlementKey.whatsapp_numbers] == 3
    assert limits[EntitlementKey.ai_conversations] == 250
    assert limits[EntitlementKey.automations] is None
    assert plan.features["calendar"] is True
    assert plan.features["digitalSignature"] is True


@pytest.mark.asyncio
async def test_export_and_closure_are_replayable_intents_without_lifecycle_effects(
    db_session,
    auth_context,
):
    service = LocalBillingService(db_session, clock=lambda: NOW)
    await service.provision_trial(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.pro,
        idempotency_key="intent-trial",
        actor_user_id=auth_context.user_id,
    )

    exported = await service.create_export_intent(
        workspace_id=auth_context.workspace_id,
        idempotency_key="export-001",
        actor_user_id=auth_context.user_id,
        export_format="zip",
        scopes=["documents", "workspace", "documents"],
        reason="Customer requested a local export plan.",
    )
    export_replay = await service.create_export_intent(
        workspace_id=auth_context.workspace_id,
        idempotency_key="export-001",
        actor_user_id=auth_context.user_id,
        export_format="zip",
        scopes=["workspace", "documents"],
        reason="Customer requested a local export plan.",
    )
    closure = await service.create_closure_intent(
        workspace_id=auth_context.workspace_id,
        idempotency_key="closure-001",
        actor_user_id=auth_context.user_id,
        confirm_closure=True,
        reason="Customer requested account closure review.",
    )

    assert exported.intent.status == ExternalActionIntentStatus.awaiting_approval
    assert exported.intent.payload["externalEffects"] == 0
    assert export_replay.replayed is True
    assert closure.intent.status == ExternalActionIntentStatus.awaiting_approval
    assert closure.portal.access.state == SubscriptionAccessStatus.trial
    assert closure.portal.external_effects == 0

    with pytest.raises(ValueError, match="confirm_closure"):
        await service.create_closure_intent(
            workspace_id=auth_context.workspace_id,
            idempotency_key="closure-not-confirmed",
            actor_user_id=auth_context.user_id,
            confirm_closure=False,
            reason="Not confirmed.",
        )


@pytest.mark.asyncio
async def test_portal_never_overwrites_billing_source_from_stale_access_projection(
    db_session,
    auth_context,
):
    service = LocalBillingService(db_session, clock=lambda: NOW)
    await service.checkout(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.pro,
        interval=BillingInterval.monthly,
        idempotency_key="portal-source-checkout",
        actor_user_id=auth_context.user_id,
    )
    subscription = await db_session.scalar(select(Subscription))
    assert subscription is not None
    subscription.status = SubscriptionStatus.unpaid
    await db_session.flush()

    portal = await service.get_portal(auth_context.workspace_id)

    assert portal.subscription is not None
    assert portal.subscription.status == SubscriptionStatus.unpaid
    assert portal.access.state == SubscriptionAccessStatus.active
    assert subscription.status == SubscriptionStatus.unpaid


@pytest.mark.asyncio
async def test_client_effective_at_is_bounded_and_never_schedules_current_state(
    db_session,
    auth_context,
):
    service = LocalBillingService(db_session, clock=lambda: NOW)

    with pytest.raises(ValueError, match="30 seconds"):
        await service.provision_trial(
            workspace_id=auth_context.workspace_id,
            plan_tier=PlanTier.pro,
            idempotency_key="future-trial-rejected",
            actor_user_id=auth_context.user_id,
            effective_at=NOW + timedelta(seconds=31),
        )

    accepted = await service.provision_trial(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.pro,
        idempotency_key="future-trial-clamped",
        actor_user_id=auth_context.user_id,
        effective_at=NOW + timedelta(seconds=10),
    )
    transitions = list(
        (
            await db_session.scalars(
                select(SubscriptionAccessTransition).order_by(
                    SubscriptionAccessTransition.occurred_at.asc()
                )
            )
        ).all()
    )

    assert accepted.portal.access.trial_ends_at == NOW + timedelta(days=14)
    assert transitions[-1].occurred_at.replace(tzinfo=UTC) == NOW
    assert await db_session.scalar(select(func.count(ExternalActionIntent.id))) == 1


@pytest.mark.asyncio
async def test_first_subscription_creation_is_deterministically_serialized_locally(
    db_session,
    auth_context,
):
    service = LocalBillingService(db_session, clock=lambda: NOW)
    await service.ensure_catalog()
    plan = await service._plan(PlanTier.pro, require_public=True)

    async def create_first_subscription():
        return await service._get_or_create_subscription(
            auth_context.workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            current_period_start=NOW,
            current_period_end=NOW + timedelta(days=30),
            trial_end=None,
            amount_cents=plan.price_monthly_cents,
            currency="BRL",
        )

    first, second = await asyncio.gather(
        create_first_subscription(),
        create_first_subscription(),
    )

    assert first[0].id == second[0].id
    assert sorted((first[1], second[1])) == [False, True]
    assert await db_session.scalar(select(func.count(Subscription.id))) == 1


@pytest.mark.asyncio
async def test_paid_cancellation_defaults_to_period_end_and_immediate_requires_reason(
    db_session,
    auth_context,
):
    service = LocalBillingService(db_session, clock=lambda: NOW)
    checkout = await service.checkout(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.pro,
        interval=BillingInterval.monthly,
        idempotency_key="cancel-contract-checkout",
        actor_user_id=auth_context.user_id,
    )
    assert checkout.portal.subscription is not None
    original_period_end = checkout.portal.subscription.current_period_end

    scheduled = await service.cancel(
        workspace_id=auth_context.workspace_id,
        idempotency_key="cancel-at-period-end",
        actor_user_id=auth_context.user_id,
    )

    assert scheduled.portal.subscription is not None
    assert scheduled.portal.subscription.status == SubscriptionStatus.active
    assert scheduled.portal.subscription.cancel_at_period_end is True
    assert scheduled.portal.subscription.current_period_end is not None
    assert original_period_end is not None
    assert scheduled.portal.subscription.current_period_end.replace(
        tzinfo=UTC
    ) == original_period_end.replace(tzinfo=UTC)
    assert scheduled.portal.subscription.cancelled_at is None
    assert scheduled.portal.access.state == SubscriptionAccessStatus.active

    with pytest.raises(ValueError, match="requires a non-empty reason"):
        await service.cancel(
            workspace_id=auth_context.workspace_id,
            idempotency_key="cancel-immediate-no-reason",
            actor_user_id=auth_context.user_id,
            mode=LocalBillingCancellationMode.immediate,
        )

    immediate = await service.cancel(
        workspace_id=auth_context.workspace_id,
        idempotency_key="cancel-immediate",
        actor_user_id=auth_context.user_id,
        mode=LocalBillingCancellationMode.immediate,
        reason="Customer requested immediate closure.",
    )
    assert immediate.portal.subscription is not None
    assert immediate.portal.subscription.status == SubscriptionStatus.cancelled
    assert immediate.portal.subscription.cancel_at_period_end is False
    assert immediate.portal.subscription.cancelled_at is not None
    assert immediate.portal.subscription.cancelled_at.replace(tzinfo=UTC) == NOW
    assert immediate.portal.access.state == SubscriptionAccessStatus.canceled


@pytest.mark.asyncio
async def test_change_plan_rejects_pending_period_end_cancellation(
    db_session,
    auth_context,
):
    service = LocalBillingService(db_session, clock=lambda: NOW)
    checkout = await service.checkout(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.pro,
        interval=BillingInterval.monthly,
        idempotency_key="pending-change-checkout",
        actor_user_id=auth_context.user_id,
    )
    assert checkout.portal.subscription is not None
    original_plan_id = checkout.portal.subscription.plan_id
    original_period_end = checkout.portal.subscription.current_period_end
    await service.cancel(
        workspace_id=auth_context.workspace_id,
        idempotency_key="pending-change-cancel",
        actor_user_id=auth_context.user_id,
    )

    with pytest.raises(LocalBillingStateError, match="cancellation at period end"):
        await service.change_plan(
            workspace_id=auth_context.workspace_id,
            plan_tier=PlanTier.business,
            direction="upgrade",
            interval=BillingInterval.annually,
            idempotency_key="pending-change-upgrade",
            actor_user_id=auth_context.user_id,
        )

    subscription = await db_session.scalar(select(Subscription))
    assert subscription is not None
    assert subscription.plan_id == original_plan_id
    assert subscription.cancel_at_period_end is True
    assert subscription.current_period_end is not None
    assert original_period_end is not None
    assert subscription.current_period_end.replace(tzinfo=UTC) == original_period_end.replace(
        tzinfo=UTC
    )


@pytest.mark.parametrize(
    ("offset", "expected_status", "expected_access", "expected_scheduled"),
    [
        pytest.param(
            timedelta(seconds=-1),
            SubscriptionStatus.active,
            SubscriptionAccessStatus.active,
            True,
            id="before",
        ),
        pytest.param(
            timedelta(0),
            SubscriptionStatus.cancelled,
            SubscriptionAccessStatus.canceled,
            False,
            id="exact",
        ),
        pytest.param(
            timedelta(seconds=1),
            SubscriptionStatus.cancelled,
            SubscriptionAccessStatus.canceled,
            False,
            id="after",
        ),
    ],
)
@pytest.mark.asyncio
async def test_first_period_end_cancellation_uses_server_time_at_due_boundary(
    db_session,
    auth_context,
    offset: timedelta,
    expected_status: SubscriptionStatus,
    expected_access: SubscriptionAccessStatus,
    expected_scheduled: bool,
):
    clock = [NOW]
    service = LocalBillingService(db_session, clock=lambda: clock[0])
    checkout = await service.checkout(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.pro,
        interval=BillingInterval.monthly,
        idempotency_key=f"first-period-end-checkout-{offset.total_seconds()}",
        actor_user_id=auth_context.user_id,
    )
    assert checkout.portal.subscription is not None
    period_start = checkout.portal.subscription.current_period_start
    period_end = checkout.portal.subscription.current_period_end
    assert period_start is not None
    assert period_end is not None
    period_start = period_start.replace(tzinfo=UTC)
    period_end = period_end.replace(tzinfo=UTC)
    semantic_time = period_start + timedelta(days=5)
    server_now = period_end + offset
    clock[0] = server_now

    result = await service.cancel(
        workspace_id=auth_context.workspace_id,
        idempotency_key=f"first-period-end-cancel-{offset.total_seconds()}",
        actor_user_id=auth_context.user_id,
        mode=LocalBillingCancellationMode.period_end,
        effective_at=semantic_time,
    )
    replay = await service.cancel(
        workspace_id=auth_context.workspace_id,
        idempotency_key=f"first-period-end-cancel-{offset.total_seconds()}",
        actor_user_id=auth_context.user_id,
        mode=LocalBillingCancellationMode.period_end,
        effective_at=semantic_time,
    )
    subscription = await db_session.scalar(select(Subscription))
    events = list(
        (
            await db_session.scalars(
                select(SubscriptionAccessTransition).where(
                    SubscriptionAccessTransition.workspace_id
                    == auth_context.workspace_id
                )
            )
        ).all()
    )

    assert result.portal.subscription is not None
    assert result.portal.subscription.status == expected_status
    assert result.portal.subscription.cancel_at_period_end is expected_scheduled
    assert result.portal.access.state == expected_access
    assert replay.replayed is True
    assert replay.portal == result.portal
    assert subscription is not None
    assert subscription.status == expected_status
    assert subscription.cancel_at_period_end is expected_scheduled
    assert not any(
        event.reason == "subscription_period_end_canceled" for event in events
    )

    cancellation_event = next(
        event
        for event in events
        if event.reason
        in {
            "local_subscription_cancellation_scheduled",
            "local_subscription_canceled",
        }
    )
    expected_effective_at = semantic_time if expected_scheduled else server_now
    assert cancellation_event.occurred_at.replace(tzinfo=UTC) == expected_effective_at
    if expected_scheduled:
        assert subscription.cancelled_at is None
    else:
        assert subscription.cancelled_at is not None
        assert subscription.cancelled_at.replace(tzinfo=UTC) == server_now


@pytest.mark.parametrize(
    (
        "action",
        "offset",
        "first_access_status",
        "first_subscription_status",
        "command_reason",
        "expected_command_events",
    ),
    [
        pytest.param(
            "dunning",
            timedelta(seconds=-1),
            SubscriptionAccessStatus.grace,
            SubscriptionStatus.past_due,
            "local_dunning_started",
            1,
            id="dunning-before",
        ),
        pytest.param(
            "dunning",
            timedelta(0),
            SubscriptionAccessStatus.canceled,
            SubscriptionStatus.cancelled,
            "local_dunning_started",
            0,
            id="dunning-exact",
        ),
        pytest.param(
            "dunning",
            timedelta(seconds=1),
            SubscriptionAccessStatus.canceled,
            SubscriptionStatus.cancelled,
            "local_dunning_started",
            0,
            id="dunning-after",
        ),
        pytest.param(
            "suspend",
            timedelta(seconds=-1),
            SubscriptionAccessStatus.suspended,
            SubscriptionStatus.unpaid,
            "local_subscription_suspended",
            1,
            id="suspend-before",
        ),
        pytest.param(
            "suspend",
            timedelta(0),
            SubscriptionAccessStatus.canceled,
            SubscriptionStatus.cancelled,
            "local_subscription_suspended",
            0,
            id="suspend-exact",
        ),
        pytest.param(
            "suspend",
            timedelta(seconds=1),
            SubscriptionAccessStatus.canceled,
            SubscriptionStatus.cancelled,
            "local_subscription_suspended",
            0,
            id="suspend-after",
        ),
    ],
)
@pytest.mark.asyncio
async def test_temporal_status_commands_finalize_due_period_end_cancellation_first(
    db_session,
    auth_context,
    action: str,
    offset: timedelta,
    first_access_status: SubscriptionAccessStatus,
    first_subscription_status: SubscriptionStatus,
    command_reason: str,
    expected_command_events: int,
):
    clock = [NOW]
    service = LocalBillingService(db_session, clock=lambda: clock[0])
    checkout = await service.checkout(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.pro,
        interval=BillingInterval.monthly,
        idempotency_key=f"period-end-{action}-checkout-{offset.total_seconds()}",
        actor_user_id=auth_context.user_id,
    )
    assert checkout.portal.subscription is not None
    period_end = checkout.portal.subscription.current_period_end
    assert period_end is not None
    period_end = period_end.replace(tzinfo=UTC)
    await service.cancel(
        workspace_id=auth_context.workspace_id,
        idempotency_key=f"period-end-{action}-cancel-{offset.total_seconds()}",
        actor_user_id=auth_context.user_id,
    )

    clock[0] = period_end + offset
    command_key = f"period-end-{action}-command-{offset.total_seconds()}"

    async def execute():
        if action == "dunning":
            return await service.start_dunning(
                workspace_id=auth_context.workspace_id,
                grace_days=3,
                idempotency_key=command_key,
                actor_user_id=auth_context.user_id,
            )
        return await service.suspend(
            workspace_id=auth_context.workspace_id,
            idempotency_key=command_key,
            actor_user_id=auth_context.user_id,
        )

    first = await execute()

    assert first.portal.subscription is not None
    assert first.portal.subscription.status == first_subscription_status
    assert first.portal.access.state == first_access_status

    clock[0] = period_end + timedelta(seconds=1)
    replay = await execute()
    events_before_reads = list(
        (
            await db_session.scalars(
                select(SubscriptionAccessTransition).where(
                    SubscriptionAccessTransition.workspace_id
                    == auth_context.workspace_id
                )
            )
        ).all()
    )
    portal = await service.get_portal(auth_context.workspace_id)
    access = await service.access_policy.decide(auth_context.workspace_id)
    events_after_reads = list(
        (
            await db_session.scalars(
                select(SubscriptionAccessTransition).where(
                    SubscriptionAccessTransition.workspace_id
                    == auth_context.workspace_id
                )
            )
        ).all()
    )
    subscription = await db_session.scalar(select(Subscription))

    assert replay.replayed is True
    assert replay.portal == first.portal
    assert portal.subscription is not None
    assert portal.subscription.status == SubscriptionStatus.cancelled
    assert portal.subscription.cancel_at_period_end is False
    assert portal.subscription.cancelled_at is not None
    assert portal.subscription.cancelled_at.replace(tzinfo=UTC) == period_end
    assert portal.access.state == SubscriptionAccessStatus.canceled
    assert portal.access.read_allowed is True
    assert portal.access.write_allowed is False
    assert access == portal.access
    assert subscription is not None
    assert subscription.status == SubscriptionStatus.cancelled
    assert subscription.cancel_at_period_end is False
    assert len(events_after_reads) == len(events_before_reads)

    cancellation_events = [
        event
        for event in events_after_reads
        if event.reason == "subscription_period_end_canceled"
    ]
    command_events = [
        event for event in events_after_reads if event.reason == command_reason
    ]
    assert len(cancellation_events) == 1
    assert cancellation_events[0].occurred_at.replace(tzinfo=UTC) == period_end
    assert len(command_events) == expected_command_events


@pytest.mark.asyncio
async def test_server_deadline_wins_over_backdated_dunning_effective_at(
    db_session,
    auth_context,
):
    clock = [NOW]
    service = LocalBillingService(db_session, clock=lambda: clock[0])
    checkout = await service.checkout(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.pro,
        interval=BillingInterval.monthly,
        idempotency_key="backdated-dunning-checkout",
        actor_user_id=auth_context.user_id,
    )
    assert checkout.portal.subscription is not None
    period_start = checkout.portal.subscription.current_period_start
    period_end = checkout.portal.subscription.current_period_end
    assert period_start is not None
    assert period_end is not None
    period_start = period_start.replace(tzinfo=UTC)
    period_end = period_end.replace(tzinfo=UTC)
    await service.cancel(
        workspace_id=auth_context.workspace_id,
        idempotency_key="backdated-dunning-cancel",
        actor_user_id=auth_context.user_id,
    )

    lock_ticks = [
        period_end - timedelta(seconds=1),
        period_end + timedelta(seconds=1),
    ]

    def advancing_clock():
        if len(lock_ticks) > 1:
            return lock_ticks.pop(0)
        return lock_ticks[0]

    service.clock = advancing_clock
    service.access_policy.clock = advancing_clock
    result = await service.start_dunning(
        workspace_id=auth_context.workspace_id,
        grace_days=3,
        idempotency_key="backdated-dunning-command",
        actor_user_id=auth_context.user_id,
        effective_at=period_start + timedelta(days=5),
    )
    events = list(
        (
            await db_session.scalars(
                select(SubscriptionAccessTransition).where(
                    SubscriptionAccessTransition.workspace_id
                    == auth_context.workspace_id
                )
            )
        ).all()
    )

    assert result.portal.subscription is not None
    assert result.portal.subscription.status == SubscriptionStatus.cancelled
    assert result.portal.subscription.cancel_at_period_end is False
    assert result.portal.subscription.cancelled_at is not None
    assert result.portal.subscription.cancelled_at.replace(tzinfo=UTC) == period_end
    assert result.portal.access.state == SubscriptionAccessStatus.canceled
    assert [event.reason for event in events].count("subscription_period_end_canceled") == 1
    assert not any(event.reason == "local_dunning_started" for event in events)


@pytest.mark.asyncio
async def test_noop_access_event_is_part_of_the_temporal_floor(
    db_session,
    auth_context,
):
    service = LocalBillingService(db_session, clock=lambda: NOW)
    await service.checkout(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.pro,
        interval=BillingInterval.monthly,
        idempotency_key="noop-floor-checkout",
        actor_user_id=auth_context.user_id,
    )
    noop_at = NOW + timedelta(minutes=10)
    await service.access_policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.active,
        idempotency_key="noop-floor-confirmation",
        source="test",
        reason="active_noop_confirmation",
        effective_at=noop_at,
    )

    with pytest.raises(SubscriptionAccessTimeConflictError, match="latest persisted"):
        await service.access_policy.transition(
            auth_context.workspace_id,
            SubscriptionAccessStatus.grace,
            idempotency_key="noop-floor-backdated-dunning",
            source="test",
            reason="backdated_after_noop",
            effective_at=NOW + timedelta(minutes=5),
            grace_ends_at=NOW + timedelta(days=1),
        )

    events = await service.access_policy.audit_trail(auth_context.workspace_id)
    assert events[-1].reason == "active_noop_confirmation"
    assert events[-1].applied is False
    assert events[-1].occurred_at == noop_at

    await service.access_policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.canceled,
        idempotency_key="noop-floor-later-cancellation",
        source="test",
        reason="later_cancellation",
        effective_at=NOW + timedelta(minutes=20),
    )
    replay = await service.access_policy.transition(
        auth_context.workspace_id,
        SubscriptionAccessStatus.active,
        idempotency_key="noop-floor-confirmation",
        source="test",
        reason="active_noop_confirmation",
        effective_at=noop_at,
    )
    decision = await service.access_policy.decide(auth_context.workspace_id)

    assert replay.replayed is True
    assert replay.applied is False
    assert decision.state == SubscriptionAccessStatus.canceled


@pytest.mark.asyncio
async def test_explicit_reactivation_after_due_cancellation_remains_allowed(
    db_session,
    auth_context,
):
    clock = [NOW]
    service = LocalBillingService(db_session, clock=lambda: clock[0])
    checkout = await service.checkout(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.pro,
        interval=BillingInterval.monthly,
        idempotency_key="later-reactivation-checkout",
        actor_user_id=auth_context.user_id,
    )
    assert checkout.portal.subscription is not None
    period_end = checkout.portal.subscription.current_period_end
    assert period_end is not None
    period_end = period_end.replace(tzinfo=UTC)
    await service.cancel(
        workspace_id=auth_context.workspace_id,
        idempotency_key="later-reactivation-cancel",
        actor_user_id=auth_context.user_id,
    )

    clock[0] = period_end + timedelta(seconds=2)
    reactivated = await service.reactivate(
        workspace_id=auth_context.workspace_id,
        idempotency_key="later-reactivation-command",
        actor_user_id=auth_context.user_id,
        effective_at=period_end + timedelta(seconds=1),
    )
    events = await service.access_policy.audit_trail(auth_context.workspace_id)
    cancellation = next(
        event for event in events if event.reason == "subscription_period_end_canceled"
    )
    reactivation = next(
        event for event in events if event.reason == "local_subscription_reactivated"
    )

    assert reactivated.portal.subscription is not None
    assert reactivated.portal.subscription.status == SubscriptionStatus.active
    assert reactivated.portal.subscription.cancel_at_period_end is False
    assert reactivated.portal.access.state == SubscriptionAccessStatus.active
    assert cancellation.occurred_at == period_end
    assert reactivation.occurred_at == period_end + timedelta(seconds=1)
    assert cancellation.to_version < reactivation.to_version


@pytest.mark.asyncio
async def test_due_period_end_cancellation_wins_over_plan_change(
    db_session,
    auth_context,
):
    clock = [NOW]
    service = LocalBillingService(db_session, clock=lambda: clock[0])
    checkout = await service.checkout(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.pro,
        interval=BillingInterval.monthly,
        idempotency_key="due-plan-change-checkout",
        actor_user_id=auth_context.user_id,
    )
    assert checkout.portal.subscription is not None
    original_plan_id = checkout.portal.subscription.plan_id
    period_end = checkout.portal.subscription.current_period_end
    assert period_end is not None
    period_end = period_end.replace(tzinfo=UTC)
    await service.cancel(
        workspace_id=auth_context.workspace_id,
        idempotency_key="due-plan-change-cancel",
        actor_user_id=auth_context.user_id,
    )

    clock[0] = period_end + timedelta(seconds=1)
    result = await service.change_plan(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.business,
        direction="upgrade",
        interval=BillingInterval.annually,
        idempotency_key="due-plan-change-upgrade",
        actor_user_id=auth_context.user_id,
    )
    replay = await service.change_plan(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.business,
        direction="upgrade",
        interval=BillingInterval.annually,
        idempotency_key="due-plan-change-upgrade",
        actor_user_id=auth_context.user_id,
    )
    subscription = await db_session.scalar(select(Subscription))
    events = list(
        (
            await db_session.scalars(
                select(SubscriptionAccessTransition).where(
                    SubscriptionAccessTransition.workspace_id
                    == auth_context.workspace_id
                )
            )
        ).all()
    )

    assert result.portal.subscription is not None
    assert result.portal.subscription.status == SubscriptionStatus.cancelled
    assert result.portal.subscription.plan_id == original_plan_id
    assert result.portal.access.state == SubscriptionAccessStatus.canceled
    assert replay.replayed is True
    assert replay.portal == result.portal
    assert subscription is not None
    assert subscription.plan_id == original_plan_id
    assert subscription.status == SubscriptionStatus.cancelled
    assert [event.reason for event in events].count("subscription_period_end_canceled") == 1
    assert not any(event.reason == "local_plan_upgrade" for event in events)


@pytest.mark.asyncio
async def test_replay_returns_original_response_snapshot_after_later_plan_change(
    db_session,
    auth_context,
):
    service = LocalBillingService(db_session, clock=lambda: NOW)
    original = await service.checkout(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.pro,
        interval=BillingInterval.monthly,
        idempotency_key="snapshot-checkout",
        actor_user_id=auth_context.user_id,
    )
    await service.change_plan(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.business,
        direction="upgrade",
        interval=None,
        idempotency_key="snapshot-upgrade",
        actor_user_id=auth_context.user_id,
    )

    replay = await service.checkout(
        workspace_id=auth_context.workspace_id,
        plan_tier=PlanTier.pro,
        interval=BillingInterval.monthly,
        idempotency_key="snapshot-checkout",
        actor_user_id=auth_context.user_id,
    )
    current = await service.get_portal(auth_context.workspace_id)

    assert original.portal.subscription is not None
    assert replay.portal == original.portal
    assert replay.replayed is True
    assert current.subscription is not None
    assert replay.portal.subscription.plan_tier == PlanTier.pro
    assert current.subscription.plan_tier == PlanTier.business
    assert "_localBillingResponseSnapshot" not in replay.intent.payload


@pytest.mark.asyncio
async def test_service_mutations_fail_closed_in_production(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    service = LocalBillingService(db_session, clock=lambda: NOW)

    with pytest.raises(LocalBillingDisabledError):
        await service.provision_trial(
            workspace_id=auth_context.workspace_id,
            plan_tier=PlanTier.pro,
            idempotency_key="production-trial",
            actor_user_id=auth_context.user_id,
        )

    assert await db_session.scalar(select(func.count(Subscription.id))) == 0
    assert await db_session.scalar(select(func.count(ExternalActionIntent.id))) == 0
