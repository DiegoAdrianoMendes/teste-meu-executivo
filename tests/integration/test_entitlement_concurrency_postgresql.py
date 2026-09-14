from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.entitlement_errors import entitlement_http_exception
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.flow import Flow
from app.models.whatsapp import WaConnectionStatus, WhatsAppConnection, WhatsAppContact
from app.models.whatsapp_campaign import (
    CampaignStatus,
    MetaTemplateStatus,
    WhatsAppCampaign,
    WhatsAppCampaignAudience,
    WhatsAppCampaignRecipient,
    WhatsAppCampaignSendJob,
    WhatsAppCampaignTemplateSnapshot,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.whatsapp_campaign import CampaignLaunchRequest
from app.services.entitlement_service import (
    CurrentStockLimitExceededError,
    EntitlementEnforcementService,
    MonthlyQuotaExceededError,
    MonthlyUsageService,
)
from app.services.whatsapp import campaign_service as campaign_service_module
from app.services.whatsapp.campaign_service import WhatsAppCampaignService

MASTER_URL = os.getenv("KLM_TEST_POSTGRES_MASTER_URL")

if not MASTER_URL:
    raise RuntimeError(
        "PostgreSQL QA requires KLM_TEST_POSTGRES_MASTER_URL; "
        "run scripts/verify_postgresql_qa.sh."
    )


class _PostgresConnectionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_connection(
        self,
        connection_id: UUID,
        workspace_id: UUID,
    ) -> WhatsAppConnection | None:
        return await self.db.scalar(
            select(WhatsAppConnection).where(
                WhatsAppConnection.id == connection_id,
                WhatsAppConnection.workspace_id == workspace_id,
            )
        )

    async def get_default_connection(
        self,
        workspace_id: UUID,
        *,
        connected_only: bool = False,
    ) -> WhatsAppConnection | None:
        statement = select(WhatsAppConnection).where(
            WhatsAppConnection.workspace_id == workspace_id
        )
        if connected_only:
            statement = statement.where(
                WhatsAppConnection.status == WaConnectionStatus.connected
            )
        return await self.db.scalar(statement.order_by(WhatsAppConnection.id))

    async def resolve_meta_access_token(self, connection: WhatsAppConnection) -> str:
        assert connection.status == WaConnectionStatus.connected
        return "postgresql-concurrency-token"


class _RecordingMetaClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send_template_message(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(str(kwargs["to"]))
        return {"messages": [{"id": f"wamid-{len(self.calls)}"}]}


class _LaunchBarrier:
    def __init__(self, parties: int) -> None:
        self.parties = parties
        self.arrived = 0
        self.ready = asyncio.Event()

    async def wait(self) -> None:
        self.arrived += 1
        if self.arrived == self.parties:
            self.ready.set()
        await self.ready.wait()


class _ConcurrentCampaignService(WhatsAppCampaignService):
    def __init__(
        self,
        db: AsyncSession,
        *,
        phone: str,
        barrier: _LaunchBarrier,
        meta_client: _RecordingMetaClient,
    ) -> None:
        super().__init__(
            db,
            connection_service=_PostgresConnectionService(db),
            meta_client=meta_client,
        )
        self._test_phone = phone
        self._test_barrier = barrier

    async def _build_audience_preview(
        self,
        workspace_id: UUID,
        audience_filter: Any,
    ) -> dict[str, Any]:
        assert workspace_id
        assert audience_filter is not None
        # Both real sessions must hold their own campaign lock before they
        # contend for the shared workspace/contact-stock lock.
        await self._test_barrier.wait()
        return {
            "total": 1,
            "eligible": 1,
            "blocked": 0,
            "sample": [
                {
                    "name": self._test_phone,
                    "phone": self._test_phone,
                    "eligible": True,
                    "reason": "ok",
                    "columnId": None,
                    "sources": ["postgresql_concurrency"],
                }
            ],
            "eligible_rows": [
                {
                    "contact": None,
                    "matter": None,
                    "contact_id": None,
                    "matter_id": None,
                    "phone": self._test_phone,
                    "name": self._test_phone,
                    "context": {"source": "postgresql_concurrency"},
                }
            ],
        }


@pytest.mark.asyncio
async def test_concurrent_stock_and_metered_writes_never_oversubscribe() -> None:
    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    workspace_id: UUID | None = None
    plan_id: UUID | None = None

    async with sessions() as db:
        existing_tiers = set((await db.scalars(select(Plan.tier))).all())
        tier = next((candidate for candidate in PlanTier if candidate not in existing_tiers), None)
        assert tier is not None, (
            "O banco efêmero de QA não pode iniciar com todos os tiers de plano ocupados."
        )
        token = uuid4().hex[:12]
        workspace = Workspace(
            name=f"Entitlement concorrente {token}",
            slug=f"entitlement-concurrency-{token}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        plan = Plan(
            name=f"Plano concorrente {token}",
            tier=tier,
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
        db.add_all([workspace, plan])
        await db.flush()
        db.add(
            Subscription(
                workspace_id=workspace.id,
                plan_id=plan.id,
                status=SubscriptionStatus.active,
                interval=BillingInterval.monthly,
                amount_cents=0,
                currency="BRL",
            )
        )
        db.add_all(
            [
                PlanEntitlement(
                    plan_id=plan.id,
                    entitlement=EntitlementKey.tokens,
                    enabled=True,
                    limit_quantity=10,
                    hard_limit=True,
                ),
                PlanEntitlement(
                    plan_id=plan.id,
                    entitlement=EntitlementKey.automations,
                    enabled=True,
                    limit_quantity=1,
                    hard_limit=True,
                ),
            ]
        )
        await db.commit()
        workspace_id = workspace.id
        plan_id = plan.id

    metered_start = asyncio.Event()

    async def consume_tokens(operation: str) -> str:
        assert workspace_id is not None
        async with sessions() as db:
            await metered_start.wait()
            service = MonthlyUsageService(db)
            try:
                reservation = await service.reserve(
                    workspace_id=workspace_id,
                    entitlement=EntitlementKey.tokens,
                    quantity=6,
                    idempotency_key=operation,
                )
                await service.commit(
                    workspace_id=workspace_id,
                    reservation_id=reservation.id,
                )
                await db.commit()
                return "committed"
            except MonthlyQuotaExceededError:
                await db.rollback()
                return "rejected"

    stock_start = asyncio.Event()

    async def create_active_flow(name: str) -> str:
        assert workspace_id is not None
        async with sessions() as db:
            await stock_start.wait()

            async def current_quantity() -> int:
                value = await db.scalar(
                    select(func.count(Flow.id)).where(
                        Flow.workspace_id == workspace_id,
                        Flow.is_template.is_(False),
                        Flow.is_active.is_(True),
                        Flow.deleted_at.is_(None),
                    )
                )
                return int(value or 0)

            try:
                await EntitlementEnforcementService(db).enforce_stock(
                    workspace_id=workspace_id,
                    entitlement=EntitlementKey.automations,
                    load_current_quantity=current_quantity,
                )
                db.add(
                    Flow(
                        workspace_id=workspace_id,
                        name=name,
                        trigger="manual",
                        trigger_config={},
                        nodes=[],
                        edges=[],
                        is_active=True,
                        is_template=False,
                    )
                )
                await db.commit()
                return "committed"
            except CurrentStockLimitExceededError:
                await db.rollback()
                return "rejected"

    try:
        metered_tasks = [
            asyncio.create_task(consume_tokens("concurrent-a")),
            asyncio.create_task(consume_tokens("concurrent-b")),
        ]
        metered_start.set()
        assert sorted(await asyncio.gather(*metered_tasks)) == ["committed", "rejected"]

        async with sessions() as db:
            replay_reservation = await MonthlyUsageService(db).reserve(
                workspace_id=workspace_id,
                entitlement=EntitlementKey.tokens,
                quantity=4,
                idempotency_key="concurrent-commit-replay",
            )
            replay_reservation_id = replay_reservation.id
            await db.commit()

        replay_start = asyncio.Event()

        async def commit_replay() -> str:
            async with sessions() as db:
                await replay_start.wait()
                reservation = await MonthlyUsageService(db).commit(
                    workspace_id=workspace_id,
                    reservation_id=replay_reservation_id,
                )
                await db.commit()
                return reservation.status.value

        replay_tasks = [
            asyncio.create_task(commit_replay()),
            asyncio.create_task(commit_replay()),
        ]
        replay_start.set()
        assert await asyncio.gather(*replay_tasks) == ["committed", "committed"]

        stock_tasks = [
            asyncio.create_task(create_active_flow("Concorrente A")),
            asyncio.create_task(create_active_flow("Concorrente B")),
        ]
        stock_start.set()
        assert sorted(await asyncio.gather(*stock_tasks)) == ["committed", "rejected"]

        async with sessions() as db:
            usage = await MonthlyUsageService(db).get_usage(
                workspace_id=workspace_id,
                entitlement=EntitlementKey.tokens,
            )
            active_flows = int(
                await db.scalar(
                    select(func.count(Flow.id)).where(
                        Flow.workspace_id == workspace_id,
                        Flow.is_active.is_(True),
                        Flow.deleted_at.is_(None),
                    )
                )
                or 0
            )
            assert usage.consumed_quantity == 10
            assert usage.reserved_quantity == 0
            assert active_flows == 1
    finally:
        async with sessions() as db:
            if workspace_id is not None:
                await db.execute(delete(Workspace).where(Workspace.id == workspace_id))
                await db.commit()
            if plan_id is not None:
                await db.execute(delete(Plan).where(Plan.id == plan_id))
                await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_campaign_launches_reserve_one_contact_without_rejected_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two launch transactions at stock limit one must have exactly one winner."""

    assert MASTER_URL is not None
    monkeypatch.setattr(
        campaign_service_module,
        "external_delivery_allowed",
        lambda: True,
    )
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    workspace_id: UUID | None = None
    plan_id: UUID | None = None
    campaign_ids: list[UUID] = []
    phones = [
        f"55619{uuid4().int % 100_000_000:08d}",
        f"55629{uuid4().int % 100_000_000:08d}",
    ]

    try:
        async with sessions() as db:
            existing_tiers = set((await db.scalars(select(Plan.tier))).all())
            tier = next(
                (candidate for candidate in PlanTier if candidate not in existing_tiers),
                None,
            )
            assert tier is not None, (
                "O banco efêmero de QA não pode iniciar com todos os tiers de plano ocupados."
            )
            token = uuid4().hex[:12]
            workspace = Workspace(
                name=f"Campaign contact concurrency {token}",
                slug=f"campaign-contact-concurrency-{token}",
                status=WorkspaceStatus.active,
                setup_mode=WorkspaceSetupMode.self_service,
                is_active=True,
                settings={},
            )
            plan = Plan(
                name=f"Campaign contact concurrency {token}",
                tier=tier,
                price_monthly_cents=0,
                price_annually_cents=0,
                max_users=100,
                max_matters=None,
                max_tokens_per_month=1_000_000,
                max_ai_agents=100,
                features={"maxCampaignRecipientsPerMonth": 100},
                is_active=True,
                is_public=False,
                sort_order=1,
            )
            db.add_all([workspace, plan])
            await db.flush()
            workspace_id = workspace.id
            plan_id = plan.id
            db.add_all(
                [
                    Subscription(
                        workspace_id=workspace.id,
                        plan_id=plan.id,
                        status=SubscriptionStatus.active,
                        interval=BillingInterval.monthly,
                        amount_cents=0,
                        currency="BRL",
                    ),
                    PlanEntitlement(
                        plan_id=plan.id,
                        entitlement=EntitlementKey.contacts,
                        enabled=True,
                        limit_quantity=1,
                        hard_limit=True,
                    ),
                ]
            )
            connection = WhatsAppConnection(
                workspace_id=workspace.id,
                is_default=True,
                phone_number_id=f"campaign-contact-{token}",
                status=WaConnectionStatus.connected,
            )
            db.add(connection)
            await db.flush()
            scheduled_at = datetime.now(UTC) + timedelta(hours=1)
            for index in range(2):
                campaign = WhatsAppCampaign(
                    workspace_id=workspace.id,
                    whatsapp_connection_id=connection.id,
                    title=f"Campanha concorrente {index + 1}",
                    status=CampaignStatus.draft,
                    scheduled_at=scheduled_at,
                )
                db.add(campaign)
                await db.flush()
                db.add_all(
                    [
                        WhatsAppCampaignAudience(
                            campaign_id=campaign.id,
                            filters={"include_crm_contacts": False},
                        ),
                        WhatsAppCampaignTemplateSnapshot(
                            campaign_id=campaign.id,
                            template_name=f"campaign_contact_{index + 1}",
                            language="pt_BR",
                            status=MetaTemplateStatus.approved,
                            header_parameters=[],
                            body_parameters=[],
                            components=[],
                            raw_payload={},
                        ),
                    ]
                )
                campaign_ids.append(campaign.id)
            await db.commit()

        assert workspace_id is not None
        barrier = _LaunchBarrier(2)
        meta_clients = [_RecordingMetaClient(), _RecordingMetaClient()]

        async def launch(index: int) -> dict[str, Any]:
            async with sessions() as db:
                service = _ConcurrentCampaignService(
                    db,
                    phone=phones[index],
                    barrier=barrier,
                    meta_client=meta_clients[index],
                )
                try:
                    campaign = await service.launch_campaign(
                        workspace_id,
                        campaign_ids[index],
                        CampaignLaunchRequest(dry_run=False),
                    )
                    return {
                        "index": index,
                        "result": "scheduled",
                        "status": campaign.status,
                        "error": None,
                    }
                except CurrentStockLimitExceededError as error:
                    await db.rollback()
                    return {
                        "index": index,
                        "result": "rejected",
                        "status": None,
                        "error": error,
                    }

        outcomes = await asyncio.wait_for(
            asyncio.gather(
                asyncio.create_task(launch(0)),
                asyncio.create_task(launch(1)),
            ),
            timeout=15,
        )
        assert sorted(item["result"] for item in outcomes) == [
            "rejected",
            "scheduled",
        ]
        winner = next(item for item in outcomes if item["result"] == "scheduled")
        rejected = next(item for item in outcomes if item["result"] == "rejected")
        assert winner["status"] == CampaignStatus.scheduled
        assert isinstance(rejected["error"], CurrentStockLimitExceededError)
        rejected_http = entitlement_http_exception(rejected["error"])
        assert rejected_http.status_code == 409
        assert rejected_http.detail["code"] == "ENT-003"
        assert meta_clients[rejected["index"]].calls == []

        async with sessions() as db:
            contacts = (
                await db.scalars(
                    select(WhatsAppContact).where(
                        WhatsAppContact.workspace_id == workspace_id
                    )
                )
            ).all()
            campaigns = (
                await db.scalars(
                    select(WhatsAppCampaign)
                    .where(WhatsAppCampaign.id.in_(campaign_ids))
                    .order_by(WhatsAppCampaign.id)
                )
            ).all()
            assert len(contacts) == 1
            assert contacts[0].phone == phones[winner["index"]]
            assert sorted(campaign.status.value for campaign in campaigns) == [
                CampaignStatus.draft.value,
                CampaignStatus.scheduled.value,
            ]
            assert (
                await db.scalar(
                    select(func.count(WhatsAppCampaignRecipient.id)).where(
                        WhatsAppCampaignRecipient.workspace_id == workspace_id
                    )
                )
                == 1
            )
            assert (
                await db.scalar(
                    select(func.count(WhatsAppCampaignSendJob.id)).where(
                        WhatsAppCampaignSendJob.workspace_id == workspace_id
                    )
                )
                == 1
            )
    finally:
        async with sessions() as db:
            if workspace_id is not None:
                await db.execute(
                    delete(Workspace).where(Workspace.id == workspace_id)
                )
                await db.commit()
            if plan_id is not None:
                await db.execute(delete(Plan).where(Plan.id == plan_id))
                await db.commit()
        await engine.dispose()
