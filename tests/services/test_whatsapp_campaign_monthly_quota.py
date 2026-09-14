from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.entitlement import (
    EntitlementKey,
    OverrideMode,
    WorkspaceEntitlementOverride,
)
from app.models.whatsapp import WaConnectionStatus, WhatsAppConnection
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignSendJobStatus,
    CampaignStatus,
    MetaTemplateStatus,
    WhatsAppCampaign,
    WhatsAppCampaignAudience,
    WhatsAppCampaignDeliveryEvent,
    WhatsAppCampaignRecipient,
    WhatsAppCampaignSendJob,
    WhatsAppCampaignTemplateSnapshot,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.commercial_catalog import (
    WorkspaceContractCreateRequest,
    WorkspaceContractLineWrite,
)
from app.schemas.whatsapp_campaign import CampaignLaunchRequest
from app.services.commercial_catalog_service import CommercialCatalogService
from app.services.whatsapp import campaign_service as campaign_service_module
from app.services.whatsapp.campaign_service import (
    CAMPAIGN_CAPABILITY_ERROR_CODE,
    MONTHLY_QUOTA_ERROR_CODE,
    CampaignCapabilityDeniedError,
    WhatsAppCampaignService,
)
from app.services.workspace_service import WorkspaceService


def _workspace(name: str) -> Workspace:
    return Workspace(
        name=name,
        slug=f"monthly-campaign-quota-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )


class FakeConnectionService:
    def __init__(self, connection: WhatsAppConnection) -> None:
        self.connection = connection

    async def get_connection(self, connection_id, workspace_id):
        assert connection_id == self.connection.id
        assert workspace_id == self.connection.workspace_id
        return self.connection

    async def get_default_connection(self, workspace_id, *, connected_only=False):
        assert workspace_id == self.connection.workspace_id
        return self.connection

    async def resolve_meta_access_token(self, connection):
        assert connection.id == self.connection.id
        return "local-test-token"


class AlwaysOkMetaClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send_template_message(self, **kwargs):
        self.calls.append(kwargs["to"])
        return {"messages": [{"id": f"wamid-{len(self.calls)}"}]}


def _service(
    db_session: AsyncSession,
    connection: WhatsAppConnection,
    client: AlwaysOkMetaClient | None = None,
) -> WhatsAppCampaignService:
    return WhatsAppCampaignService(
        db_session,
        connection_service=FakeConnectionService(connection),
        meta_client=client or AlwaysOkMetaClient(),
    )


async def _connection(
    db_session: AsyncSession,
    workspace: Workspace,
) -> WhatsAppConnection:
    connection = WhatsAppConnection(
        workspace_id=workspace.id,
        phone_number_id=f"phone-{uuid4().hex[:12]}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    return connection


async def _draft_campaign(
    db_session: AsyncSession,
    *,
    workspace: Workspace,
    connection: WhatsAppConnection,
    title: str,
) -> WhatsAppCampaign:
    campaign = WhatsAppCampaign(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        title=title,
        status=CampaignStatus.draft,
        scheduled_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add(campaign)
    await db_session.flush()
    db_session.add_all(
        [
            WhatsAppCampaignAudience(campaign_id=campaign.id, filters={}),
            WhatsAppCampaignTemplateSnapshot(
                campaign_id=campaign.id,
                template_name="monthly_quota",
                language="pt_BR",
                status=MetaTemplateStatus.approved,
                header_parameters=[],
                body_parameters=[],
                components=[],
                raw_payload={},
            ),
        ]
    )
    await db_session.flush()
    return campaign


def _preview(phone: str) -> dict:
    return {
        "total": 1,
        "eligible": 1,
        "blocked": 0,
        "sample": [],
        "eligible_rows": [
            {
                "contact": None,
                "matter": None,
                "contact_id": None,
                "matter_id": None,
                "phone": phone,
                "name": "Cliente",
                "context": {},
            }
        ],
    }


async def _legacy_campaign_plan(
    db_session: AsyncSession,
    *,
    limit: int | None,
) -> Plan:
    plan = Plan(
        name=f"Campaign quota {uuid4().hex[:8]}",
        tier=PlanTier.starter,
        price_monthly_cents=29_700,
        price_annually_cents=285_120,
        features={
            "maxContacts": 100_000,
            **(
                {}
                if limit is None
                else {"maxCampaignRecipientsPerMonth": limit}
            ),
        },
    )
    db_session.add(plan)
    await db_session.flush()
    return plan


async def _legacy_campaign_subscription(
    db_session: AsyncSession,
    *,
    workspace: Workspace,
    plan: Plan,
) -> None:
    db_session.add(
        Subscription(
            workspace_id=workspace.id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
        )
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_canonical_campaign_capability_wins_and_denied_contract_never_falls_back(
    db_session: AsyncSession,
    monkeypatch,
    platform_auth_context,
) -> None:
    monkeypatch.setattr(
        campaign_service_module,
        "external_delivery_allowed",
        lambda: True,
    )
    configured = _workspace("Canonical configured")
    denied = _workspace("Canonical denied")
    legacy = _workspace("Legacy fallback")
    db_session.add_all([configured, denied, legacy])
    await db_session.flush()
    legacy_plan = await _legacy_campaign_plan(db_session, limit=17)
    for workspace in (configured, denied, legacy):
        await _legacy_campaign_subscription(
            db_session,
            workspace=workspace,
            plan=legacy_plan,
        )

    catalog = CommercialCatalogService(db_session)
    offers, _ = await catalog.bootstrap_default_catalog(
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="campaign-canonical-catalog",
    )
    start = next(item for item in offers if item.sku == "crm_start")
    base_campaign = start.snapshot.entitlements[EntitlementKey.campaign_recipients]
    configured_campaign = base_campaign.model_copy(update={"enabled": True, "limit_quantity": 2})
    denied_campaign = base_campaign.model_copy(update={"enabled": False, "limit_quantity": None})
    await catalog.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=configured.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku=start.sku,
                    policy_version_id=start.id,
                    entitlement_overrides={EntitlementKey.campaign_recipients: configured_campaign},
                )
            ],
            reason="Franquia canônica explícita para campanhas.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="campaign-configured-contract",
    )
    await catalog.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=denied.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku=start.sku,
                    policy_version_id=start.id,
                    entitlement_overrides={EntitlementKey.campaign_recipients: denied_campaign},
                )
            ],
            reason="Contrato canônico com campanhas explicitamente revogadas.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="campaign-denied-contract",
    )

    configured_connection = await _connection(db_session, configured)
    denied_connection = await _connection(db_session, denied)
    legacy_connection = await _connection(db_session, legacy)
    at = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)

    assert (
        await _service(
            db_session, configured_connection
        )._campaign_recipient_limit(
            workspace_id=configured.id,
            effective_at=at,
        )
        == 2
    )
    with pytest.raises(CampaignCapabilityDeniedError) as denied_capability:
        await _service(db_session, denied_connection)._campaign_recipient_limit(
            workspace_id=denied.id,
            effective_at=at,
        )
    assert denied_capability.value.denial_reason == "not_contracted"
    assert (
        await _service(
            db_session, legacy_connection
        )._campaign_recipient_limit(
            workspace_id=legacy.id,
            effective_at=at,
        )
        == 17
    )

    configured_summary = await WorkspaceService(db_session)._build_plan_summary(
        configured
    )
    denied_summary = await WorkspaceService(db_session)._build_plan_summary(denied)
    assert configured_summary is not None
    assert denied_summary is not None
    assert configured_summary.max_campaign_recipients_per_month == 2
    assert denied_summary.max_campaign_recipients_per_month is None

    campaign = await _draft_campaign(
        db_session,
        workspace=denied,
        connection=denied_connection,
        title="Sem capacidade contratada",
    )
    service = _service(db_session, denied_connection)

    async def preview_denied(_workspace_id, _audience_filter):
        return _preview("5511999990099")

    monkeypatch.setattr(service, "_build_audience_preview", preview_denied)
    campaign_id = campaign.id
    denied_workspace_id = denied.id
    platform_actor_id = platform_auth_context.user_id
    await db_session.commit()

    with pytest.raises(HTTPException) as blocked:
        await service.launch_campaign(
            denied.id,
            campaign_id,
            CampaignLaunchRequest(dry_run=False),
        )
    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == CAMPAIGN_CAPABILITY_ERROR_CODE
    assert blocked.value.detail["denialReason"] == "not_contracted"
    await db_session.rollback()
    assert (
        int(
            await db_session.scalar(
                select(func.count(WhatsAppCampaignRecipient.id)).where(
                    WhatsAppCampaignRecipient.campaign_id == campaign_id
                )
            )
            or 0
        )
        == 0
    )
    assert (
        int(
            await db_session.scalar(
                select(func.count(WhatsAppCampaignSendJob.id)).where(
                    WhatsAppCampaignSendJob.campaign_id == campaign_id
                )
            )
            or 0
        )
        == 0
    )
    persisted_campaign = await db_session.get(WhatsAppCampaign, campaign_id)
    assert persisted_campaign is not None
    assert persisted_campaign.status is CampaignStatus.draft

    # Defense in depth for historical/corrupt rows written before the service
    # validation existed: grant-without-quantity must never become unlimited.
    db_session.add(
        WorkspaceEntitlementOverride(
            workspace_id=denied_workspace_id,
            entitlement=EntitlementKey.campaign_recipients,
            mode=OverrideMode.grant,
            quantity=None,
            reason="Registro histórico inválido para provar fail-closed.",
            created_by_user_id=platform_actor_id,
        )
    )
    await db_session.flush()
    manifest = await catalog.resolve_effective_manifest(denied_workspace_id)
    invalid_capability = manifest.capabilities[
        EntitlementKey.campaign_recipients
    ]
    assert invalid_capability.enabled is False
    assert invalid_capability.permitted is False
    assert invalid_capability.limit is None
    assert invalid_capability.denial_reason == "not_configured"
    with pytest.raises(CampaignCapabilityDeniedError) as invalid_override:
        await service._campaign_recipient_limit(
            workspace_id=denied_workspace_id,
            effective_at=at,
        )
    assert invalid_override.value.denial_reason == "not_configured"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sku", "expected_limit"),
    [
        ("crm_start", 200),
        ("crm_advanced", 1_000),
        ("crm_evolution", 3_000),
    ],
)
async def test_default_canonical_plan_launches_at_campaign_limit_and_blocks_excess(
    db_session: AsyncSession,
    monkeypatch,
    platform_auth_context,
    sku: str,
    expected_limit: int,
) -> None:
    monkeypatch.setattr(
        campaign_service_module,
        "external_delivery_allowed",
        lambda: True,
    )
    workspace = _workspace(f"Default campaign quota {sku}")
    db_session.add(workspace)
    await db_session.flush()
    catalog = CommercialCatalogService(db_session)
    offers, _ = await catalog.bootstrap_default_catalog(
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"default-campaign-catalog-{sku}",
    )
    offer = next(item for item in offers if item.sku == sku)
    await catalog.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku=offer.sku,
                    policy_version_id=offer.id,
                )
            ],
            reason="Contrato canônico sem override para provar a franquia default.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"default-campaign-contract-{sku}",
    )
    connection = await _connection(db_session, workspace)
    at_limit = await _draft_campaign(
        db_session,
        workspace=workspace,
        connection=connection,
        title=f"{sku} no limite",
    )
    over_limit = await _draft_campaign(
        db_session,
        workspace=workspace,
        connection=connection,
        title=f"{sku} acima do limite",
    )
    await db_session.commit()

    service = _service(db_session, connection)
    assert (
        await service._campaign_recipient_limit(
            workspace_id=workspace.id,
            effective_at=datetime.now(UTC),
        )
        == expected_limit
    )

    async def preview_one(_workspace_id, _audience_filter):
        return _preview(f"55119{expected_limit:08d}")

    async def usage_immediately_before_limit(**_kwargs):
        return expected_limit - 1

    monkeypatch.setattr(service, "_build_audience_preview", preview_one)
    monkeypatch.setattr(
        service,
        "_monthly_quota_usage",
        usage_immediately_before_limit,
    )
    launched = await service.launch_campaign(
        workspace.id,
        at_limit.id,
        CampaignLaunchRequest(dry_run=False),
    )
    assert launched.status is CampaignStatus.scheduled
    assert (
        int(
            await db_session.scalar(
                select(func.count(WhatsAppCampaignRecipient.id)).where(
                    WhatsAppCampaignRecipient.campaign_id == at_limit.id
                )
            )
            or 0
        )
        == 1
    )

    async def usage_at_limit(**_kwargs):
        return expected_limit

    monkeypatch.setattr(service, "_monthly_quota_usage", usage_at_limit)
    with pytest.raises(HTTPException) as blocked:
        await service.launch_campaign(
            workspace.id,
            over_limit.id,
            CampaignLaunchRequest(dry_run=False),
        )
    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == MONTHLY_QUOTA_ERROR_CODE
    assert blocked.value.detail["limit"] == expected_limit
    assert blocked.value.detail["used"] == expected_limit
    assert blocked.value.detail["requested"] == 1
    assert blocked.value.detail["remaining"] == 0
    over_limit_id = over_limit.id
    await db_session.rollback()
    assert (
        int(
            await db_session.scalar(
                select(func.count(WhatsAppCampaignRecipient.id)).where(
                    WhatsAppCampaignRecipient.campaign_id == over_limit_id
                )
            )
            or 0
        )
        == 0
    )
    assert (
        int(
            await db_session.scalar(
                select(func.count(WhatsAppCampaignSendJob.id)).where(
                    WhatsAppCampaignSendJob.campaign_id == over_limit_id
                )
            )
            or 0
        )
        == 0
    )


@pytest.mark.asyncio
async def test_legacy_campaign_quota_requires_subscription_and_explicit_limit(
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        campaign_service_module,
        "external_delivery_allowed",
        lambda: True,
    )
    without_subscription = _workspace("Legacy without subscription")
    missing_limit = _workspace("Legacy without explicit campaign limit")
    db_session.add_all([without_subscription, missing_limit])
    await db_session.flush()
    missing_plan = await _legacy_campaign_plan(db_session, limit=None)
    await _legacy_campaign_subscription(
        db_session,
        workspace=missing_limit,
        plan=missing_plan,
    )
    no_subscription_connection = await _connection(
        db_session,
        without_subscription,
    )
    missing_limit_connection = await _connection(db_session, missing_limit)
    at = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)

    with pytest.raises(CampaignCapabilityDeniedError) as not_contracted:
        await _service(
            db_session,
            no_subscription_connection,
        )._campaign_recipient_limit(
            workspace_id=without_subscription.id,
            effective_at=at,
        )
    assert not_contracted.value.denial_reason == "not_contracted"

    with pytest.raises(CampaignCapabilityDeniedError) as not_configured:
        await _service(
            db_session,
            missing_limit_connection,
        )._campaign_recipient_limit(
            workspace_id=missing_limit.id,
            effective_at=at,
        )
    assert not_configured.value.denial_reason == "not_configured"

    campaign = await _draft_campaign(
        db_session,
        workspace=missing_limit,
        connection=missing_limit_connection,
        title="Sem franquia legada explícita",
    )
    service = _service(db_session, missing_limit_connection)

    async def preview_missing_limit(_workspace_id, _audience_filter):
        return _preview("5511999990088")

    monkeypatch.setattr(
        service,
        "_build_audience_preview",
        preview_missing_limit,
    )
    campaign_id = campaign.id
    await db_session.commit()
    with pytest.raises(HTTPException) as blocked:
        await service.launch_campaign(
            missing_limit.id,
            campaign_id,
            CampaignLaunchRequest(dry_run=False),
        )
    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == CAMPAIGN_CAPABILITY_ERROR_CODE
    assert blocked.value.detail["denialReason"] == "not_configured"
    await db_session.rollback()
    assert (
        int(
            await db_session.scalar(
                select(func.count(WhatsAppCampaignRecipient.id)).where(
                    WhatsAppCampaignRecipient.campaign_id == campaign_id
                )
            )
            or 0
        )
        == 0
    )
    assert (
        int(
            await db_session.scalar(
                select(func.count(WhatsAppCampaignSendJob.id)).where(
                    WhatsAppCampaignSendJob.campaign_id == campaign_id
                )
            )
            or 0
        )
        == 0
    )


@pytest.mark.asyncio
async def test_launch_reserves_monthly_quota_atomically_and_replay_is_idempotent(
    db_session: AsyncSession,
    monkeypatch,
):
    monkeypatch.setattr(
        campaign_service_module,
        "external_delivery_allowed",
        lambda: True,
    )
    workspace = _workspace("Launch quota")
    db_session.add(workspace)
    await db_session.flush()
    plan = Plan(
        name=f"Campaign quota {uuid4().hex[:8]}",
        tier=PlanTier.starter,
        price_monthly_cents=29_700,
        price_annually_cents=285_120,
        features={
            "maxCampaignRecipientsPerMonth": 1,
            "maxContacts": 100_000,
        },
    )
    db_session.add(plan)
    await db_session.flush()
    db_session.add(
        Subscription(
            workspace_id=workspace.id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
        )
    )
    connection = await _connection(db_session, workspace)
    first = await _draft_campaign(
        db_session,
        workspace=workspace,
        connection=connection,
        title="Primeira",
    )
    second = await _draft_campaign(
        db_session,
        workspace=workspace,
        connection=connection,
        title="Segunda",
    )
    await db_session.commit()

    service = _service(db_session, connection)

    async def preview_one(_workspace_id, _audience_filter):
        return _preview("5511999990001")

    monkeypatch.setattr(service, "_build_audience_preview", preview_one)

    launched = await service.launch_campaign(
        workspace.id,
        first.id,
        CampaignLaunchRequest(dry_run=False),
    )
    assert launched.status == CampaignStatus.scheduled

    recipient = await db_session.scalar(
        select(WhatsAppCampaignRecipient).where(
            WhatsAppCampaignRecipient.campaign_id == first.id
        )
    )
    assert recipient is not None
    assert recipient.quota_period_start is not None
    assert recipient.quota_reserved_at is not None
    first_reserved_at = recipient.quota_reserved_at

    replayed = await service.launch_campaign(
        workspace.id,
        first.id,
        CampaignLaunchRequest(dry_run=False),
    )
    assert replayed.status == CampaignStatus.scheduled
    assert (
        await db_session.scalar(
            select(func.count(WhatsAppCampaignRecipient.id)).where(
                WhatsAppCampaignRecipient.campaign_id == first.id
            )
        )
        == 1
    )
    await db_session.refresh(recipient)
    assert recipient.quota_reserved_at == first_reserved_at

    with pytest.raises(HTTPException) as blocked:
        await service.launch_campaign(
            workspace.id,
            second.id,
            CampaignLaunchRequest(dry_run=False),
        )
    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == MONTHLY_QUOTA_ERROR_CODE
    assert blocked.value.detail["used"] == 1
    assert blocked.value.detail["remaining"] == 0
    second_id = second.id
    await db_session.rollback()
    assert (
        await db_session.scalar(
            select(func.count(WhatsAppCampaignRecipient.id)).where(
                WhatsAppCampaignRecipient.campaign_id == second_id
            )
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count(WhatsAppCampaignSendJob.id)).where(
                WhatsAppCampaignSendJob.campaign_id == second_id
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_worker_honors_existing_reservation_and_defers_monthly_excess(
    db_session: AsyncSession,
    monkeypatch,
):
    monkeypatch.setattr(
        campaign_service_module,
        "external_delivery_allowed",
        lambda: True,
    )
    workspace = _workspace("Worker quota")
    db_session.add(workspace)
    await db_session.flush()
    connection = await _connection(db_session, workspace)
    campaign = WhatsAppCampaign(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        title="Worker quota",
        status=CampaignStatus.running,
    )
    db_session.add(campaign)
    await db_session.flush()
    db_session.add(
        WhatsAppCampaignTemplateSnapshot(
            campaign_id=campaign.id,
            template_name="monthly_worker",
            language="pt_BR",
            status=MetaTemplateStatus.approved,
            header_parameters=[],
            body_parameters=[],
            components=[],
            raw_payload={},
        )
    )

    now = datetime.now(UTC)
    current_period = now.date().replace(day=1)
    recipients: list[WhatsAppCampaignRecipient] = []
    jobs: list[WhatsAppCampaignSendJob] = []
    for index in range(2):
        recipient = WhatsAppCampaignRecipient(
            campaign_id=campaign.id,
            workspace_id=workspace.id,
                phone=f"551198888000{index}",
            status=CampaignRecipientStatus.queued,
            send_context={},
            quota_period_start=current_period if index == 0 else None,
            quota_reserved_at=now - timedelta(minutes=1) if index == 0 else None,
        )
        db_session.add(recipient)
        await db_session.flush()
        job = WhatsAppCampaignSendJob(
            campaign_id=campaign.id,
            recipient_id=recipient.id,
            workspace_id=workspace.id,
            whatsapp_connection_id=connection.id,
            status=CampaignSendJobStatus.pending,
            scheduled_for=now - timedelta(minutes=2) + timedelta(seconds=index),
        )
        db_session.add(job)
        recipients.append(recipient)
        jobs.append(job)
    await db_session.commit()

    client = AlwaysOkMetaClient()
    service = _service(db_session, connection, client)

    async def limit_one(**_kwargs):
        return 1

    monkeypatch.setattr(service, "_campaign_recipient_limit", limit_one)

    processed = await service._process_ready_jobs(
        campaign.id,
        connection,
        workspace_id=workspace.id,
    )
    assert processed == 1
    assert client.calls == [recipients[0].phone]

    for recipient in recipients:
        await db_session.refresh(recipient)
    for job in jobs:
        await db_session.refresh(job)

    assert recipients[0].status == CampaignRecipientStatus.sent
    assert recipients[0].quota_period_start == current_period
    assert recipients[1].status == CampaignRecipientStatus.queued
    assert recipients[1].quota_period_start is None
    assert recipients[1].quota_reserved_at is None
    assert jobs[1].status == CampaignSendJobStatus.pending
    assert jobs[1].attempt_count == 0
    assert "monthly_campaign_quota_deferred" in (jobs[1].last_error or "")
    assert jobs[1].scheduled_for is not None
    retry_at = jobs[1].scheduled_for
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    assert retry_at > now

    events = list(
        (
            await db_session.scalars(
                select(WhatsAppCampaignDeliveryEvent).where(
                    WhatsAppCampaignDeliveryEvent.campaign_id == campaign.id
                )
            )
        ).all()
    )
    assert any(
        event.payload.get("event") == "monthly_campaign_quota_deferred"
        for event in events
    )

    processed_again = await service._process_ready_jobs(
        campaign.id,
        connection,
        workspace_id=workspace.id,
    )
    assert processed_again == 0
    assert client.calls == [recipients[0].phone]
