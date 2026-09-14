from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.ai.accounting import SqlAlchemyModelUsageAccountant
from app.ai.base import AIMessage
from app.ai.errors import ModelRequestReplayError
from app.ai.models import (
    CostEstimate,
    ModelAttribution,
    ModelRequest,
    ProviderName,
    ProviderResponse,
    RouteCandidate,
    TaskClass,
)
from app.db.append_only import ImmutableRecordError
from app.db.base import Base
from app.models.billing import (
    BillingInterval,
    ModelProviderCallJournal,
    ModelProviderCallState,
    Plan,
    PlanTier,
    PlatformModelUsageLedger,
    PlatformTokenUsage,
    PlatformTokenUsageByTask,
    Subscription,
    SubscriptionStatus,
)
from app.models.entitlement import EntitlementKey, PlanEntitlement, WorkspaceMonthlyUsage
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.entitlement_service import EntitlementNotConfiguredError
from app.services.platform_service import PlatformService
from tests.conftest import TestSessionLocal


async def _seed_finite_token_plan(db: AsyncSession, workspace_id, *, limit: int) -> None:
    plan = Plan(
        name="Plano contabilidade IA",
        tier=PlanTier.enterprise,
        price_monthly_cents=0,
        price_annually_cents=0,
        max_users=100,
        max_matters=None,
        max_tokens_per_month=limit,
        max_ai_agents=100,
        features={},
        is_active=True,
        is_public=False,
        sort_order=1,
    )
    db.add(plan)
    await db.flush()
    db.add_all(
        [
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.tokens,
                enabled=True,
                limit_quantity=limit,
                hard_limit=True,
            ),
            Subscription(
                workspace_id=workspace_id,
                plan_id=plan.id,
                status=SubscriptionStatus.active,
                interval=BillingInterval.monthly,
                amount_cents=0,
                currency="BRL",
            ),
        ]
    )
    await db.flush()


@pytest.mark.asyncio
async def test_model_usage_receipt_is_exactly_once_and_rejects_divergent_replay(
    db_session: AsyncSession,
    auth_context,
) -> None:
    values = {
        "workspace_id": auth_context.workspace_id,
        "model_used": "test-model",
        "provider": "fake",
        "input_tokens": 90,
        "output_tokens": 10,
        "cost_usd": Decimal("0.00125000"),
        "task_class": "simple",
        "request_id": "usage-request-1",
        "operation": "test.exactly_once",
        "cached_input_tokens": 5,
        "provider_request_id": "provider-1",
        "price_catalog_version": "prices-v1",
        "route_catalog_version": "routes-v1",
    }

    await PlatformService.log_model_usage(db_session, **values)
    await PlatformService.log_model_usage(db_session, **values)

    assert await db_session.scalar(select(func.count(PlatformModelUsageLedger.id))) == 1
    aggregate = await db_session.scalar(select(PlatformTokenUsage))
    task_aggregate = await db_session.scalar(select(PlatformTokenUsageByTask))
    assert aggregate is not None
    assert task_aggregate is not None
    assert aggregate.total_runs == task_aggregate.total_runs == 1
    assert aggregate.total_input_tokens == task_aggregate.total_input_tokens == 90
    assert aggregate.total_output_tokens == task_aggregate.total_output_tokens == 10

    with pytest.raises(ValueError, match="medição divergente"):
        await PlatformService.log_model_usage(
            db_session,
            **{**values, "output_tokens": 11},
        )

    assert aggregate.total_runs == task_aggregate.total_runs == 1


@pytest.mark.asyncio
async def test_model_usage_receipt_is_append_only_in_the_orm(
    db_session: AsyncSession,
    auth_context,
) -> None:
    await PlatformService.log_model_usage(
        db_session,
        workspace_id=auth_context.workspace_id,
        model_used="test-model",
        provider="fake",
        input_tokens=1,
        output_tokens=1,
        cost_usd=Decimal("0"),
        task_class="simple",
        request_id="immutable-usage-1",
    )
    receipt = await db_session.scalar(select(PlatformModelUsageLedger))
    assert receipt is not None

    receipt.operation = "tampered"
    with pytest.raises(ImmutableRecordError, match="immutable"):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_sql_accountant_reserves_finite_quota_and_settles_once(
    db_session: AsyncSession,
    auth_context,
) -> None:
    await _seed_finite_token_plan(db_session, auth_context.workspace_id, limit=1_000)
    await db_session.commit()
    accountant = SqlAlchemyModelUsageAccountant(TestSessionLocal)
    request = ModelRequest(
        messages=[AIMessage("user", "teste")],
        workspace_id=str(auth_context.workspace_id),
        agent_name="sdr",
        task_class=TaskClass.SIMPLE,
        max_output_tokens=50,
        request_id="accountant-request-1",
        attribution=ModelAttribution(operation="test.accounting"),
    )
    route = RouteCandidate(ProviderName.OPENAI, "test-model")
    estimate = CostEstimate(
        amount_usd=Decimal("0.01"),
        input_tokens=100,
        output_tokens=50,
        catalog_version="prices-v1",
    )

    reservation = await accountant.reserve(request=request, estimate=estimate, route=route)
    await accountant.mark_started(
        reservation=reservation,
        estimate=estimate,
        route=route,
    )
    response = ProviderResponse(
        text="ok",
        model="test-model",
        input_tokens=40,
        output_tokens=10,
        cached_input_tokens=2,
        provider_request_id="provider-accounting-1",
    )
    await accountant.settle(
        reservation=reservation,
        request=request,
        route=route,
        response=response,
        actual_cost_usd=Decimal("0.0005"),
        price_catalog_version="prices-v1",
        route_catalog_version="routes-v1",
    )
    await accountant.settle(
        reservation=reservation,
        request=request,
        route=route,
        response=response,
        actual_cost_usd=Decimal("0.0005"),
        price_catalog_version="prices-v1",
        route_catalog_version="routes-v1",
    )

    async with TestSessionLocal() as verification:
        receipt_count = await verification.scalar(
            select(func.count(PlatformModelUsageLedger.id)).where(
                PlatformModelUsageLedger.workspace_id == auth_context.workspace_id
            )
        )
        usage = await verification.scalar(
            select(WorkspaceMonthlyUsage).where(
                WorkspaceMonthlyUsage.workspace_id == auth_context.workspace_id,
                WorkspaceMonthlyUsage.entitlement == EntitlementKey.tokens,
            )
        )
        aggregate = await verification.scalar(
            select(PlatformTokenUsage).where(
                PlatformTokenUsage.workspace_id == auth_context.workspace_id
            )
        )
        receipt = await verification.scalar(
            select(PlatformModelUsageLedger).where(
                PlatformModelUsageLedger.workspace_id == auth_context.workspace_id
            )
        )
        journal = await verification.scalar(
            select(ModelProviderCallJournal).where(
                ModelProviderCallJournal.workspace_id == auth_context.workspace_id,
                ModelProviderCallJournal.request_id == request.request_id,
            )
        )
        assert receipt_count == 1
        assert usage is not None
        assert usage.consumed_quantity == 50
        assert usage.reserved_quantity == 0
        assert aggregate is not None
        assert aggregate.total_runs == 1
        assert aggregate.total_input_tokens == 40
        assert aggregate.total_output_tokens == 10
        assert receipt is not None
        assert receipt.provider_request_id == "provider-accounting-1"
        assert receipt.price_catalog_version == "prices-v1"
        assert receipt.route_catalog_version == "routes-v1"
        assert journal is not None
        assert journal.state == ModelProviderCallState.succeeded
        assert journal.actual_input_tokens == 40
        assert journal.actual_output_tokens == 10
        assert journal.provider_request_id == "provider-accounting-1"
        assert journal.period_start.day == 1


@pytest.mark.asyncio
async def test_sql_accountant_blocks_external_provider_without_finite_plan(
    db_session: AsyncSession,
    auth_context,
) -> None:
    await db_session.commit()
    accountant = SqlAlchemyModelUsageAccountant(TestSessionLocal)
    request = ModelRequest(
        messages=[AIMessage("user", "teste")],
        workspace_id=str(auth_context.workspace_id),
        agent_name="sdr",
        task_class=TaskClass.SIMPLE,
        max_output_tokens=20,
        request_id="accountant-missing-plan",
    )
    estimate = CostEstimate(
        amount_usd=Decimal("0.01"),
        input_tokens=10,
        output_tokens=20,
        catalog_version="prices-v1",
    )

    with pytest.raises(EntitlementNotConfiguredError, match="franquia finita"):
        await accountant.reserve(
            request=request,
            estimate=estimate,
            route=RouteCandidate(ProviderName.OPENAI, "test-model"),
        )

    async with TestSessionLocal() as verification:
        failed_claim_count = await verification.scalar(
            select(func.count(ModelProviderCallJournal.id)).where(
                ModelProviderCallJournal.workspace_id == auth_context.workspace_id,
                ModelProviderCallJournal.request_id == request.request_id,
            )
        )
        assert failed_claim_count == 0

    local_reservation = await accountant.reserve(
        request=request,
        estimate=estimate,
        route=RouteCandidate(ProviderName.FAKE, "local-fake"),
    )
    assert local_reservation.entitlement_reservation_id is None
    assert local_reservation.reserved_tokens == 30


@pytest.mark.asyncio
async def test_sql_claim_blocks_sequential_and_divergent_replay_before_provider(
    db_session: AsyncSession,
    auth_context,
) -> None:
    await _seed_finite_token_plan(db_session, auth_context.workspace_id, limit=1_000)
    await db_session.commit()
    accountant = SqlAlchemyModelUsageAccountant(TestSessionLocal)
    route = RouteCandidate(ProviderName.OPENAI, "test-model")
    estimate = CostEstimate(
        amount_usd=Decimal("0.01"),
        input_tokens=25,
        output_tokens=15,
        catalog_version="prices-v1",
    )
    original = ModelRequest(
        messages=[AIMessage("user", "mesmo conteúdo")],
        workspace_id=str(auth_context.workspace_id),
        agent_name="sdr",
        task_class=TaskClass.SIMPLE,
        max_output_tokens=15,
        request_id="durable-replay-1",
        attribution=ModelAttribution(operation="test.replay"),
    )

    reservation = await accountant.reserve(
        request=original,
        estimate=estimate,
        route=route,
    )

    with pytest.raises(ModelRequestReplayError) as repeated:
        await accountant.reserve(request=original, estimate=estimate, route=route)
    assert repeated.value.state == ModelProviderCallState.claimed.value
    assert repeated.value.divergent is False

    divergent = ModelRequest(
        messages=[AIMessage("user", "conteúdo alterado")],
        workspace_id=original.workspace_id,
        agent_name=original.agent_name,
        task_class=original.task_class,
        max_output_tokens=original.max_output_tokens,
        request_id=original.request_id,
        attribution=original.attribution,
    )
    with pytest.raises(ModelRequestReplayError) as changed:
        await accountant.reserve(request=divergent, estimate=estimate, route=route)
    assert changed.value.state == ModelProviderCallState.claimed.value
    assert changed.value.divergent is True

    await accountant.release(reservation)


@pytest.mark.asyncio
async def test_sql_claim_blocks_concurrent_replay_across_sessions(tmp_path) -> None:
    database_path = tmp_path / "model-claim-concurrency.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        poolclass=NullPool,
        connect_args={"timeout": 10},
    )
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with session_factory() as seed:
            workspace = Workspace(
                name="Workspace claim concorrente",
                slug=f"claim-concorrente-{tmp_path.name}",
                status=WorkspaceStatus.active,
                setup_mode=WorkspaceSetupMode.self_service,
                is_active=True,
                settings={},
            )
            seed.add(workspace)
            await seed.flush()
            await _seed_finite_token_plan(seed, workspace.id, limit=1_000)
            await seed.commit()
            workspace_id = workspace.id

        accountant = SqlAlchemyModelUsageAccountant(session_factory)
        model_request = ModelRequest(
            messages=[AIMessage("user", "claim simultâneo")],
            workspace_id=str(workspace_id),
            agent_name="sdr",
            task_class=TaskClass.SIMPLE,
            max_output_tokens=20,
            request_id="concurrent-sql-claim-1",
            attribution=ModelAttribution(operation="test.concurrent-claim"),
        )
        route = RouteCandidate(ProviderName.OPENAI, "test-model")
        estimate = CostEstimate(
            amount_usd=Decimal("0.01"),
            input_tokens=30,
            output_tokens=20,
            catalog_version="prices-v1",
        )

        outcomes = await asyncio.gather(
            accountant.reserve(request=model_request, estimate=estimate, route=route),
            accountant.reserve(request=model_request, estimate=estimate, route=route),
            return_exceptions=True,
        )

        reservations = [
            outcome for outcome in outcomes if not isinstance(outcome, BaseException)
        ]
        replays = [
            outcome for outcome in outcomes if isinstance(outcome, ModelRequestReplayError)
        ]
        assert len(reservations) == 1
        assert len(replays) == 1
        assert replays[0].state == ModelProviderCallState.claimed.value

        async with session_factory() as verification:
            assert await verification.scalar(
                select(func.count(ModelProviderCallJournal.id)).where(
                    ModelProviderCallJournal.workspace_id == workspace_id,
                    ModelProviderCallJournal.request_id == model_request.request_id,
                )
            ) == 1
            usage = await verification.scalar(
                select(WorkspaceMonthlyUsage).where(
                    WorkspaceMonthlyUsage.workspace_id == workspace_id,
                    WorkspaceMonthlyUsage.entitlement == EntitlementKey.tokens,
                )
            )
            assert usage is not None
            assert usage.reserved_quantity == 50
            assert usage.consumed_quantity == 0

        await accountant.release(reservations[0])
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_uncertain_state_is_durable_and_cannot_be_reclaimed(
    db_session: AsyncSession,
    auth_context,
) -> None:
    await _seed_finite_token_plan(db_session, auth_context.workspace_id, limit=1_000)
    await db_session.commit()
    accountant = SqlAlchemyModelUsageAccountant(TestSessionLocal)
    model_request = ModelRequest(
        messages=[AIMessage("user", "teste timeout")],
        workspace_id=str(auth_context.workspace_id),
        agent_name="juridico",
        task_class=TaskClass.COMPLEX,
        max_output_tokens=50,
        request_id="uncertain-request-1",
        attribution=ModelAttribution(operation="test.uncertain"),
    )
    route = RouteCandidate(ProviderName.OPENAI, "test-model")
    estimate = CostEstimate(
        amount_usd=Decimal("0.01"),
        input_tokens=50,
        output_tokens=50,
        catalog_version="prices-v1",
    )
    reservation = await accountant.reserve(
        request=model_request,
        estimate=estimate,
        route=route,
    )
    await accountant.mark_started(
        reservation=reservation,
        estimate=estimate,
        route=route,
    )
    await accountant.mark_uncertain(
        reservation=reservation,
        route=route,
        error_code="provider_timeout",
    )

    async with TestSessionLocal() as verification:
        journal = await verification.scalar(
            select(ModelProviderCallJournal).where(
                ModelProviderCallJournal.workspace_id == auth_context.workspace_id,
                ModelProviderCallJournal.request_id == model_request.request_id,
            )
        )
        assert journal is not None
        assert journal.state == ModelProviderCallState.uncertain
        assert journal.error_code == "provider_timeout"
        assert journal.resolved_at is None

    with pytest.raises(ModelRequestReplayError) as replay:
        await accountant.reserve(
            request=model_request,
            estimate=estimate,
            route=route,
        )
    assert replay.value.state == ModelProviderCallState.uncertain.value


@pytest.mark.asyncio
async def test_usage_dates_are_recorded_in_the_current_utc_day(
    db_session: AsyncSession,
    auth_context,
) -> None:
    await PlatformService.log_model_usage(
        db_session,
        workspace_id=auth_context.workspace_id,
        model_used="clock-model",
        provider="fake",
        input_tokens=1,
        output_tokens=0,
        cost_usd=Decimal("0"),
        task_class="simple",
        request_id="clock-request",
    )
    usage = await db_session.scalar(select(PlatformTokenUsage))
    assert usage is not None
    assert usage.usage_date.date() == datetime.now(UTC).date()
