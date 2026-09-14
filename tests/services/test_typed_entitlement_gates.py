from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.agent_run import AgentRun
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.client import Client
from app.models.entitlement import (
    EntitlementKey,
    EntitlementUsageReservation,
    PlanEntitlement,
    UsageReservationStatus,
    WorkspaceMonthlyUsage,
)
from app.models.matter import Matter, PracticeArea
from app.models.whatsapp import ConnectionType, WaConnectionStatus, WhatsAppConnection
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.agent_runtime_service import AgentRuntimeService
from app.services.entitlement_service import (
    CurrentStockLimitExceededError,
    EntitlementIdempotencyConflictError,
    MonthlyUsageService,
    UsageReservationNotFoundError,
)
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from tests.agent_deployment_helpers import seed_verified_agent_deployments


@pytest.fixture(autouse=True)
async def _explicit_agent_deployment(db_session, auth_context, monkeypatch):
    await seed_verified_agent_deployments(
        db_session, auth_context.workspace_id, {"sdr"}, monkeypatch
    )


async def _seed_plan(
    db: AsyncSession,
    *,
    workspace_ids: list,
    limits: dict[EntitlementKey, int],
) -> Plan:
    plan = Plan(
        name=f"Plano quota {uuid4().hex[:8]}",
        tier=PlanTier.enterprise,
        price_monthly_cents=0,
        price_annually_cents=0,
        max_users=100,
        max_matters=None,
        max_tokens_per_month=1_000_000,
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
            Subscription(
                workspace_id=workspace_id,
                plan_id=plan.id,
                status=SubscriptionStatus.active,
                interval=BillingInterval.monthly,
                amount_cents=0,
                currency="BRL",
            )
            for workspace_id in workspace_ids
        ]
    )
    db.add_all(
        [
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=entitlement,
                enabled=True,
                limit_quantity=limit,
                hard_limit=True,
            )
            for entitlement, limit in limits.items()
        ]
    )
    await db.flush()
    return plan


@pytest.mark.asyncio
async def test_metered_replay_and_reservation_ids_are_isolated_by_workspace(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    workspace_b = Workspace(
        name="Workspace quota B",
        slug=f"workspace-quota-b-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace_b)
    await db_session.flush()
    await _seed_plan(
        db_session,
        workspace_ids=[auth_context.workspace_id, workspace_b.id],
        limits={EntitlementKey.tokens: 6},
    )

    service = MonthlyUsageService(db_session)
    reservation_a = await service.reserve(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.tokens,
        quantity=6,
        idempotency_key="same-operation",
    )
    await service.commit(
        workspace_id=auth_context.workspace_id,
        reservation_id=reservation_a.id,
    )
    replay_a = await service.reserve(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.tokens,
        quantity=6,
        idempotency_key="same-operation",
    )
    await service.commit(
        workspace_id=auth_context.workspace_id,
        reservation_id=replay_a.id,
    )

    reservation_b = await service.reserve(
        workspace_id=workspace_b.id,
        entitlement=EntitlementKey.tokens,
        quantity=6,
        idempotency_key="same-operation",
    )
    await service.commit(
        workspace_id=workspace_b.id,
        reservation_id=reservation_b.id,
    )

    assert replay_a.id == reservation_a.id
    assert reservation_b.id != reservation_a.id
    assert (
        await service.get_usage(
            workspace_id=auth_context.workspace_id,
            entitlement=EntitlementKey.tokens,
        )
    ).consumed_quantity == 6
    assert (
        await service.get_usage(
            workspace_id=workspace_b.id,
            entitlement=EntitlementKey.tokens,
        )
    ).consumed_quantity == 6
    with pytest.raises(UsageReservationNotFoundError):
        await service.release(
            workspace_id=workspace_b.id,
            reservation_id=reservation_a.id,
        )
    with pytest.raises(EntitlementIdempotencyConflictError):
        await service.reserve(
            workspace_id=auth_context.workspace_id,
            entitlement=EntitlementKey.tokens,
            quantity=5,
            idempotency_key="same-operation",
        )


@pytest.mark.asyncio
async def test_agent_run_failure_after_reservations_rolls_back_domain_and_usage(
    db_session: AsyncSession,
    auth_context: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_plan(
        db_session,
        workspace_ids=[auth_context.workspace_id],
        limits={
            EntitlementKey.ai_executions: 1,
            EntitlementKey.ai_conversations: 1,
        },
    )
    client = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente rollback quota",
    )
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.saude_suplementar,
        demand_type="Falha depois da reserva",
    )
    db_session.add(matter)
    await db_session.flush()

    async def fail_after_reservations(*args, **kwargs) -> None:
        raise RuntimeError("falha local depois das reservas")

    monkeypatch.setattr(AgentRuntimeService, "_append_timeline", fail_after_reservations)

    with pytest.raises(RuntimeError, match="depois das reservas"):
        await AgentRuntimeService(db_session).create_run_with_state(
            workspace_id=auth_context.workspace_id,
            matter_id=matter.id,
            agent_name="sdr",
            trigger_source="test:rollback",
            dedupe_key="rollback-after-reservation",
        )

    assert int(await db_session.scalar(select(func.count(AgentRun.id))) or 0) == 0
    assert int(
        await db_session.scalar(select(func.count(EntitlementUsageReservation.id))) or 0
    ) == 0
    assert int(
        await db_session.scalar(select(func.count(WorkspaceMonthlyUsage.id))) or 0
    ) == 0


@pytest.mark.asyncio
async def test_release_is_idempotent_after_a_metered_failure(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan(
        db_session,
        workspace_ids=[auth_context.workspace_id],
        limits={EntitlementKey.tokens: 10},
    )
    service = MonthlyUsageService(db_session)
    reservation = await service.reserve(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.tokens,
        quantity=8,
        idempotency_key="release-replay",
    )

    first = await service.release(
        workspace_id=auth_context.workspace_id,
        reservation_id=reservation.id,
    )
    replay = await service.release(
        workspace_id=auth_context.workspace_id,
        reservation_id=reservation.id,
    )

    assert first.status == replay.status == UsageReservationStatus.released
    usage = await service.get_usage(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.tokens,
    )
    assert usage.consumed_quantity == usage.reserved_quantity == 0


@pytest.mark.asyncio
async def test_disconnected_qr_reactivation_is_gated_before_provider_refresh(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_plan(
        db_session,
        workspace_ids=[auth_context.workspace_id],
        limits={EntitlementKey.whatsapp_numbers: 1},
    )
    active = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        status=WaConnectionStatus.connected,
        phone_number_id="quota-active-number",
        is_default=True,
    )
    disconnected = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.QR_CODE,
        status=WaConnectionStatus.disconnected,
        instance_id="quota-disconnected-instance",
        is_default=False,
    )
    db_session.add_all([active, disconnected])
    await db_session.flush()

    class QrClientProbe:
        calls = 0

        async def get_instance_status(self, *args, **kwargs):
            self.calls += 1
            return {"status": "connected"}

    qr_client = QrClientProbe()
    service = WhatsAppConnectionService(
        db_session,
        qr_client=qr_client,
    )

    await service._enforce_connection_capacity(auth_context.workspace_id, active)
    with pytest.raises(CurrentStockLimitExceededError) as captured:
        await service.refresh_qr_status(disconnected)

    assert captured.value.current_quantity == 1
    assert captured.value.remaining_quantity == 0
    assert qr_client.calls == 0
    assert disconnected.status == WaConnectionStatus.disconnected
