from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.klm_operations import ExternalActionIntent
from app.models.subscription_access import (
    SubscriptionAccessState,
    SubscriptionAccessStatus,
    SubscriptionAccessTransition,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.local_billing_service import LocalBillingService, LocalBillingStateError
from app.services.subscription_access_policy import SubscriptionAccessPolicy

MASTER_URL = os.getenv("KLM_TEST_POSTGRES_MASTER_URL")

if not MASTER_URL:
    raise RuntimeError(
        "PostgreSQL QA requires KLM_TEST_POSTGRES_MASTER_URL; "
        "run scripts/verify_postgresql_qa.sh."
    )


async def _seed_due_paid_subscription(
    sessions: async_sessionmaker[AsyncSession],
    *,
    period_start: datetime,
    period_end: datetime,
    label: str,
) -> tuple[UUID, UUID, UUID]:
    async with sessions() as db:
        plan = await db.scalar(
            select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order.asc())
        )
        if plan is None:
            plan = Plan(
                name="Temporal billing concurrency",
                tier=PlanTier.free,
                price_monthly_cents=0,
                price_annually_cents=0,
                features={},
                is_active=True,
                is_public=False,
            )
            db.add(plan)
            await db.flush()
        token = uuid4().hex[:12]
        workspace = Workspace(
            name=f"{label} {token}",
            slug=f"{label.lower().replace(' ', '-')}-{token}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        db.add(workspace)
        await db.flush()
        subscription = Subscription(
            workspace_id=workspace.id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            current_period_start=period_start,
            current_period_end=period_end,
            cancel_at_period_end=True,
            amount_cents=plan.price_monthly_cents,
            currency="BRL",
        )
        db.add(subscription)
        await db.flush()
        db.add(
            SubscriptionAccessState(
                workspace_id=workspace.id,
                subscription_id=subscription.id,
                status=SubscriptionAccessStatus.active,
                state_version=1,
                status_changed_at=period_start,
            )
        )
        await db.commit()
        return workspace.id, subscription.id, plan.id


async def _delete_workspace(
    sessions: async_sessionmaker[AsyncSession],
    workspace_id: UUID,
) -> None:
    async with sessions() as db:
        await db.execute(delete(Workspace).where(Workspace.id == workspace_id))
        await db.commit()


@pytest.mark.asyncio
async def test_concurrent_first_subscription_creation_returns_one_row() -> None:
    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    workspace_id: UUID | None = None
    created_plan_id: UUID | None = None

    async with sessions() as db:
        plan = await db.scalar(
            select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order.asc())
        )
        if plan is None:
            plan = Plan(
                name="Billing concurrency",
                tier=PlanTier.free,
                price_monthly_cents=0,
                price_annually_cents=0,
                features={},
                is_active=True,
                is_public=False,
            )
            db.add(plan)
            await db.flush()
            created_plan_id = plan.id
        token = uuid4().hex[:12]
        workspace = Workspace(
            name=f"Billing concurrency {token}",
            slug=f"billing-concurrency-{token}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        db.add(workspace)
        await db.commit()
        workspace_id = workspace.id
        plan_id = plan.id

    start = asyncio.Event()
    now = datetime.now(UTC)

    async def create_subscription() -> tuple[UUID, bool]:
        assert workspace_id is not None
        async with sessions() as db:
            await start.wait()
            subscription, created = await LocalBillingService(
                db,
                clock=lambda: now,
            )._get_or_create_subscription(
                workspace_id,
                plan_id=plan_id,
                status=SubscriptionStatus.active,
                interval=BillingInterval.monthly,
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
                trial_end=None,
                amount_cents=0,
                currency="BRL",
            )
            await db.commit()
            return subscription.id, created

    try:
        tasks = [
            asyncio.create_task(create_subscription()),
            asyncio.create_task(create_subscription()),
        ]
        start.set()
        outcomes = await asyncio.gather(*tasks)

        assert outcomes[0][0] == outcomes[1][0]
        assert sorted(created for _, created in outcomes) == [False, True]
        async with sessions() as db:
            count = await db.scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.workspace_id == workspace_id
                )
            )
            assert count == 1
    finally:
        async with sessions() as db:
            if workspace_id is not None:
                await db.execute(delete(Workspace).where(Workspace.id == workspace_id))
            if created_plan_id is not None:
                await db.execute(delete(Plan).where(Plan.id == created_plan_id))
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_period_end_reconciliation_emits_one_event() -> None:
    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    workspace_id: UUID | None = None
    created_plan_id: UUID | None = None
    period_end = datetime.now(UTC)

    async with sessions() as db:
        plan = await db.scalar(
            select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order.asc())
        )
        if plan is None:
            plan = Plan(
                name="Period-end concurrency",
                tier=PlanTier.free,
                price_monthly_cents=0,
                price_annually_cents=0,
                features={},
                is_active=True,
                is_public=False,
            )
            db.add(plan)
            await db.flush()
            created_plan_id = plan.id
        token = uuid4().hex[:12]
        workspace = Workspace(
            name=f"Period-end concurrency {token}",
            slug=f"period-end-concurrency-{token}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        db.add(workspace)
        await db.flush()
        workspace_id = workspace.id
        subscription = Subscription(
            workspace_id=workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            current_period_start=period_end - timedelta(days=30),
            current_period_end=period_end,
            cancel_at_period_end=True,
            amount_cents=1_000,
            currency="BRL",
        )
        db.add(subscription)
        await db.flush()
        db.add(
            SubscriptionAccessState(
                workspace_id=workspace_id,
                subscription_id=subscription.id,
                status=SubscriptionAccessStatus.active,
                state_version=1,
                status_changed_at=period_end - timedelta(days=30),
            )
        )
        await db.commit()
        subscription_id = subscription.id

    start = asyncio.Event()

    async def reconcile() -> SubscriptionAccessStatus | None:
        assert workspace_id is not None
        async with sessions() as db:
            await start.wait()
            decision = await SubscriptionAccessPolicy(
                db,
                clock=lambda: period_end,
            ).reconcile(workspace_id, evaluated_at=period_end)
            await db.commit()
            return decision.state

    try:
        tasks = [asyncio.create_task(reconcile()), asyncio.create_task(reconcile())]
        start.set()
        outcomes = await asyncio.gather(*tasks)

        assert outcomes == [
            SubscriptionAccessStatus.canceled,
            SubscriptionAccessStatus.canceled,
        ]
        async with sessions() as db:
            subscription = await db.get(Subscription, subscription_id)
            event_count = await db.scalar(
                select(func.count(SubscriptionAccessTransition.id)).where(
                    SubscriptionAccessTransition.workspace_id == workspace_id,
                    SubscriptionAccessTransition.reason
                    == "subscription_period_end_canceled",
                )
            )
            assert subscription is not None
            assert subscription.status == SubscriptionStatus.cancelled
            assert subscription.cancel_at_period_end is False
            assert event_count == 1
    finally:
        async with sessions() as db:
            if workspace_id is not None:
                await db.execute(delete(Workspace).where(Workspace.id == workspace_id))
            if created_plan_id is not None:
                await db.execute(delete(Plan).where(Plan.id == created_plan_id))
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_due_cancellation_wins_concurrent_dunning_and_suspension() -> None:
    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    workspace_id: UUID | None = None
    created_plan_id: UUID | None = None
    period_end = datetime.now(UTC)
    command_time = period_end + timedelta(seconds=1)

    async with sessions() as db:
        plan = await db.scalar(
            select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order.asc())
        )
        if plan is None:
            plan = Plan(
                name="Billing command concurrency",
                tier=PlanTier.free,
                price_monthly_cents=0,
                price_annually_cents=0,
                features={},
                is_active=True,
                is_public=False,
            )
            db.add(plan)
            await db.flush()
            created_plan_id = plan.id
        token = uuid4().hex[:12]
        workspace = Workspace(
            name=f"Billing command concurrency {token}",
            slug=f"billing-command-concurrency-{token}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        db.add(workspace)
        await db.flush()
        workspace_id = workspace.id
        subscription = Subscription(
            workspace_id=workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            current_period_start=period_end - timedelta(days=30),
            current_period_end=period_end,
            cancel_at_period_end=True,
            amount_cents=1_000,
            currency="BRL",
        )
        db.add(subscription)
        await db.flush()
        db.add(
            SubscriptionAccessState(
                workspace_id=workspace_id,
                subscription_id=subscription.id,
                status=SubscriptionAccessStatus.active,
                state_version=1,
                status_changed_at=period_end - timedelta(days=30),
            )
        )
        await db.commit()
        subscription_id = subscription.id

    start = asyncio.Event()

    async def run_command(action: str) -> tuple[str, str, str, bool]:
        assert workspace_id is not None
        async with sessions() as db:
            service = LocalBillingService(db, clock=lambda: command_time)
            await start.wait()
            if action == "dunning":
                result = await service.start_dunning(
                    workspace_id=workspace_id,
                    grace_days=3,
                    idempotency_key="concurrent-due-dunning",
                    actor_user_id=None,  # type: ignore[arg-type]
                )
            else:
                result = await service.suspend(
                    workspace_id=workspace_id,
                    idempotency_key="concurrent-due-suspension",
                    actor_user_id=None,  # type: ignore[arg-type]
                )
            await db.commit()
            assert result.portal.subscription is not None
            assert result.portal.access.state is not None
            return (
                action,
                result.portal.subscription.status.value,
                result.portal.access.state.value,
                result.replayed,
            )

    try:
        tasks = [
            asyncio.create_task(run_command("dunning")),
            asyncio.create_task(run_command("suspend")),
        ]
        start.set()
        outcomes = await asyncio.gather(*tasks)

        assert sorted(outcomes) == [
            ("dunning", "cancelled", "canceled", False),
            ("suspend", "cancelled", "canceled", False),
        ]

        async with sessions() as db:
            service = LocalBillingService(db, clock=lambda: command_time)
            dunning_replay = await service.start_dunning(
                workspace_id=workspace_id,
                grace_days=3,
                idempotency_key="concurrent-due-dunning",
                actor_user_id=None,  # type: ignore[arg-type]
            )
            suspension_replay = await service.suspend(
                workspace_id=workspace_id,
                idempotency_key="concurrent-due-suspension",
                actor_user_id=None,  # type: ignore[arg-type]
            )
            await db.commit()

            subscription = await db.get(Subscription, subscription_id)
            access_state = await db.scalar(
                select(SubscriptionAccessState).where(
                    SubscriptionAccessState.workspace_id == workspace_id
                )
            )
            cancellation_events = await db.scalar(
                select(func.count(SubscriptionAccessTransition.id)).where(
                    SubscriptionAccessTransition.workspace_id == workspace_id,
                    SubscriptionAccessTransition.reason
                    == "subscription_period_end_canceled",
                )
            )
            late_command_events = await db.scalar(
                select(func.count(SubscriptionAccessTransition.id)).where(
                    SubscriptionAccessTransition.workspace_id == workspace_id,
                    SubscriptionAccessTransition.reason.in_(
                        ["local_dunning_started", "local_subscription_suspended"]
                    ),
                )
            )

            assert dunning_replay.replayed is True
            assert suspension_replay.replayed is True
            assert dunning_replay.portal.access.state == SubscriptionAccessStatus.canceled
            assert suspension_replay.portal.access.state == SubscriptionAccessStatus.canceled
            assert subscription is not None
            assert subscription.status == SubscriptionStatus.cancelled
            assert subscription.cancel_at_period_end is False
            assert subscription.cancelled_at is not None
            assert subscription.cancelled_at.replace(tzinfo=UTC) == period_end
            assert access_state is not None
            assert access_state.status == SubscriptionAccessStatus.canceled
            assert cancellation_events == 1
            assert late_command_events == 0
    finally:
        async with sessions() as db:
            if workspace_id is not None:
                await db.execute(delete(Workspace).where(Workspace.id == workspace_id))
            if created_plan_id is not None:
                await db.execute(delete(Plan).where(Plan.id == created_plan_id))
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_same_idempotency_key_reconciles_once_and_replays() -> None:
    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    period_start = datetime.now(UTC) - timedelta(days=30)
    period_end = period_start + timedelta(days=30)
    server_now = period_end + timedelta(seconds=1)
    semantic_time = period_start + timedelta(days=5)
    workspace_id, subscription_id, _ = await _seed_due_paid_subscription(
        sessions,
        period_start=period_start,
        period_end=period_end,
        label="Same idempotency billing",
    )
    start = asyncio.Event()

    async def dunning() -> tuple[bool, str, str]:
        async with sessions() as db:
            await start.wait()
            result = await LocalBillingService(db, clock=lambda: server_now).start_dunning(
                workspace_id=workspace_id,
                grace_days=3,
                idempotency_key="same-key-due-dunning",
                actor_user_id=None,  # type: ignore[arg-type]
                effective_at=semantic_time,
            )
            await db.commit()
            assert result.portal.subscription is not None
            assert result.portal.access.state is not None
            return (
                result.replayed,
                result.portal.subscription.status.value,
                result.portal.access.state.value,
            )

    try:
        tasks = [asyncio.create_task(dunning()), asyncio.create_task(dunning())]
        start.set()
        outcomes = await asyncio.gather(*tasks)

        assert sorted(outcomes) == [
            (False, "cancelled", "canceled"),
            (True, "cancelled", "canceled"),
        ]
        async with sessions() as db:
            subscription = await db.get(Subscription, subscription_id)
            cancellation_events = await db.scalar(
                select(func.count(SubscriptionAccessTransition.id)).where(
                    SubscriptionAccessTransition.workspace_id == workspace_id,
                    SubscriptionAccessTransition.reason
                    == "subscription_period_end_canceled",
                )
            )
            dunning_events = await db.scalar(
                select(func.count(SubscriptionAccessTransition.id)).where(
                    SubscriptionAccessTransition.workspace_id == workspace_id,
                    SubscriptionAccessTransition.reason == "local_dunning_started",
                )
            )
            intents = await db.scalar(
                select(func.count(ExternalActionIntent.id)).where(
                    ExternalActionIntent.workspace_id == workspace_id,
                    ExternalActionIntent.idempotency_key
                    == "local-billing:same-key-due-dunning",
                )
            )

            assert subscription is not None
            assert subscription.status == SubscriptionStatus.cancelled
            assert subscription.cancel_at_period_end is False
            assert cancellation_events == 1
            assert dunning_events == 0
            assert intents == 1
    finally:
        await _delete_workspace(sessions, workspace_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_plan_changes_are_noops_after_period_end() -> None:
    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    period_start = datetime.now(UTC) - timedelta(days=30)
    period_end = period_start + timedelta(days=30)
    server_now = period_end + timedelta(seconds=1)
    semantic_time = period_start + timedelta(days=5)
    workspace_id, subscription_id, original_plan_id = await _seed_due_paid_subscription(
        sessions,
        period_start=period_start,
        period_end=period_end,
        label="Concurrent plan change billing",
    )
    start = asyncio.Event()

    async def change_plan(
        *,
        tier: PlanTier,
        direction: str,
        idempotency_key: str,
    ) -> tuple[str, str, str]:
        async with sessions() as db:
            await start.wait()
            result = await LocalBillingService(db, clock=lambda: server_now).change_plan(
                workspace_id=workspace_id,
                plan_tier=tier,
                direction=direction,
                interval=BillingInterval.annually,
                idempotency_key=idempotency_key,
                actor_user_id=None,  # type: ignore[arg-type]
                effective_at=semantic_time,
            )
            await db.commit()
            assert result.portal.subscription is not None
            assert result.portal.access.state is not None
            return (
                direction,
                result.portal.subscription.status.value,
                result.portal.access.state.value,
            )

    try:
        tasks = [
            asyncio.create_task(
                change_plan(
                    tier=PlanTier.business,
                    direction="upgrade",
                    idempotency_key="concurrent-due-upgrade",
                )
            ),
            asyncio.create_task(
                change_plan(
                    tier=PlanTier.starter,
                    direction="downgrade",
                    idempotency_key="concurrent-due-downgrade",
                )
            ),
        ]
        start.set()
        outcomes = await asyncio.gather(*tasks)

        assert sorted(outcomes) == [
            ("downgrade", "cancelled", "canceled"),
            ("upgrade", "cancelled", "canceled"),
        ]
        async with sessions() as db:
            subscription = await db.get(Subscription, subscription_id)
            events = list(
                (
                    await db.scalars(
                        select(SubscriptionAccessTransition).where(
                            SubscriptionAccessTransition.workspace_id == workspace_id
                        )
                    )
                ).all()
            )

            assert subscription is not None
            assert subscription.plan_id == original_plan_id
            assert subscription.status == SubscriptionStatus.cancelled
            assert subscription.cancel_at_period_end is False
            assert [event.reason for event in events].count(
                "subscription_period_end_canceled"
            ) == 1
            assert not any(event.reason.startswith("local_plan_") for event in events)
    finally:
        await _delete_workspace(sessions, workspace_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_later_reactivation_wins_concurrent_backdated_dunning() -> None:
    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    period_start = datetime.now(UTC) - timedelta(days=30)
    period_end = period_start + timedelta(days=30)
    server_now = period_end + timedelta(seconds=3)
    dunning_time = period_end + timedelta(seconds=1)
    reactivation_time = period_end + timedelta(seconds=2)
    workspace_id, subscription_id, _ = await _seed_due_paid_subscription(
        sessions,
        period_start=period_start,
        period_end=period_end,
        label="Concurrent reactivation billing",
    )
    start = asyncio.Event()
    reactivation_has_lock = asyncio.Event()
    dunning_is_waiting = asyncio.Event()

    async def dunning() -> str:
        async with sessions() as db:
            await start.wait()
            await reactivation_has_lock.wait()
            dunning_is_waiting.set()
            try:
                result = await LocalBillingService(
                    db,
                    clock=lambda: server_now,
                ).start_dunning(
                    workspace_id=workspace_id,
                    grace_days=3,
                    idempotency_key="reactivation-race-dunning",
                    actor_user_id=None,  # type: ignore[arg-type]
                    effective_at=dunning_time,
                )
            except LocalBillingStateError:
                await db.rollback()
                return "time_conflict"
            await db.commit()
            assert result.portal.access.state == SubscriptionAccessStatus.canceled
            return "canceled_noop"

    async def reactivate() -> str:
        async with sessions() as db:
            await start.wait()
            await db.scalar(
                select(Subscription)
                .where(Subscription.id == subscription_id)
                .with_for_update()
            )
            reactivation_has_lock.set()
            await dunning_is_waiting.wait()
            result = await LocalBillingService(db, clock=lambda: server_now).reactivate(
                workspace_id=workspace_id,
                idempotency_key="reactivation-race-reactivate",
                actor_user_id=None,  # type: ignore[arg-type]
                effective_at=reactivation_time,
            )
            await db.commit()
            assert result.portal.access.state == SubscriptionAccessStatus.active
            return "reactivated"

    try:
        tasks = [asyncio.create_task(dunning()), asyncio.create_task(reactivate())]
        start.set()
        outcomes = await asyncio.gather(*tasks)

        assert outcomes == ["time_conflict", "reactivated"]
        async with sessions() as db:
            subscription = await db.get(Subscription, subscription_id)
            events = list(
                (
                    await db.scalars(
                        select(SubscriptionAccessTransition)
                        .where(SubscriptionAccessTransition.workspace_id == workspace_id)
                        .order_by(SubscriptionAccessTransition.occurred_at.asc())
                    )
                ).all()
            )
            cancellation_events = [
                event
                for event in events
                if event.reason == "subscription_period_end_canceled"
            ]
            reactivation_events = [
                event
                for event in events
                if event.reason == "local_subscription_reactivated"
            ]

            assert subscription is not None
            assert subscription.status == SubscriptionStatus.active
            assert subscription.cancel_at_period_end is False
            assert subscription.cancelled_at is None
            assert len(cancellation_events) == 1
            assert cancellation_events[0].occurred_at.replace(tzinfo=UTC) == period_end
            assert len(reactivation_events) == 1
            assert reactivation_events[0].occurred_at.replace(
                tzinfo=UTC
            ) == reactivation_time
            assert not any(event.reason == "local_dunning_started" for event in events)
    finally:
        await _delete_workspace(sessions, workspace_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_exact_deadline_reactivation_loses_concurrent_due_reconciliation() -> None:
    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    period_start = datetime.now(UTC) - timedelta(days=30)
    period_end = period_start + timedelta(days=30)
    server_now = period_end + timedelta(seconds=1)
    workspace_id, subscription_id, _ = await _seed_due_paid_subscription(
        sessions,
        period_start=period_start,
        period_end=period_end,
        label="Exact deadline reactivation billing",
    )
    start = asyncio.Event()
    reconciliation_has_lock = asyncio.Event()
    reactivation_started = asyncio.Event()

    async def reconcile_due_cancellation() -> str:
        async with sessions() as db:
            await start.wait()
            await db.scalar(
                select(Subscription)
                .where(Subscription.id == subscription_id)
                .with_for_update()
            )
            reconciliation_has_lock.set()
            await reactivation_started.wait()
            await asyncio.sleep(0)
            result = await LocalBillingService(
                db,
                clock=lambda: server_now,
            ).start_dunning(
                workspace_id=workspace_id,
                grace_days=3,
                idempotency_key="exact-deadline-race-dunning",
                actor_user_id=None,  # type: ignore[arg-type]
                effective_at=period_start + timedelta(days=5),
            )
            await db.commit()
            assert result.portal.access.state == SubscriptionAccessStatus.canceled
            return "canceled"

    async def reactivate_at_deadline() -> str:
        async with sessions() as db:
            await start.wait()
            await reconciliation_has_lock.wait()
            reactivation_started.set()
            try:
                await LocalBillingService(db, clock=lambda: server_now).reactivate(
                    workspace_id=workspace_id,
                    idempotency_key="exact-deadline-race-reactivate",
                    actor_user_id=None,  # type: ignore[arg-type]
                    effective_at=period_end,
                )
            except LocalBillingStateError as error:
                await db.rollback()
                assert "strictly after" in str(error)
                return "time_conflict"
            pytest.fail("Exact-deadline reactivation unexpectedly succeeded.")

    try:
        tasks = [
            asyncio.create_task(reconcile_due_cancellation()),
            asyncio.create_task(reactivate_at_deadline()),
        ]
        start.set()
        outcomes = await asyncio.gather(*tasks)

        assert sorted(outcomes) == ["canceled", "time_conflict"]
        async with sessions() as db:
            subscription = await db.get(Subscription, subscription_id)
            events = list(
                (
                    await db.scalars(
                        select(SubscriptionAccessTransition).where(
                            SubscriptionAccessTransition.workspace_id == workspace_id
                        )
                    )
                ).all()
            )

            assert subscription is not None
            assert subscription.status == SubscriptionStatus.cancelled
            assert subscription.cancel_at_period_end is False
            assert subscription.cancelled_at is not None
            assert subscription.cancelled_at.replace(tzinfo=UTC) == period_end
            assert [event.reason for event in events].count(
                "subscription_period_end_canceled"
            ) == 1
            assert not any(
                event.reason == "local_subscription_reactivated" for event in events
            )
            assert not any(event.reason == "local_dunning_started" for event in events)
    finally:
        await _delete_workspace(sessions, workspace_id)
        await engine.dispose()
