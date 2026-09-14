from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_audit_event import PlatformAuditEvent
from app.models.meta_commercial_rate import (
    META_PRICING_SOURCE_URL,
    MetaCommercialRateCard,
)
from app.models.omnichannel_v6 import OmnichannelMessageCostLedgerEntry
from app.models.user import User
from app.models.whatsapp import ConnectionType, WaConnectionStatus, WhatsAppConnection
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignStatus,
    MetaTemplateStatus,
    WhatsAppCampaign,
    WhatsAppCampaignRecipient,
    WhatsAppCampaignTemplateSnapshot,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole
from app.services.platform_audit_event_service import PlatformAuditEventService


pytestmark = pytest.mark.usefixtures("active_subscription")


def _rate_payload(
    *,
    category: str = "marketing",
    country_code: str = "BR",
    currency: str = "BRL",
    commercial_rate: str = "0.25000000",
    effective_from: datetime,
    effective_to: datetime | None = None,
    is_active: bool = True,
) -> dict:
    return {
        "category": category,
        "countryCode": country_code,
        "currency": currency,
        "commercialRate": commercial_rate,
        "effectiveFrom": effective_from.isoformat(),
        "effectiveTo": effective_to.isoformat() if effective_to else None,
        "sourceUrl": META_PRICING_SOURCE_URL,
        "checkedAt": datetime.now(UTC).isoformat(),
        "isActive": is_active,
    }


async def _workspace(db: AsyncSession, label: str) -> Workspace:
    workspace = Workspace(
        name=f"Workspace cobrança {label}",
        slug=f"workspace-cobranca-{label}-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db.add(workspace)
    await db.flush()
    return workspace


async def _campaign(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    title: str,
    category: str | None = "marketing",
    eligible_recipients: int = 0,
    recipients: list[dict] | None = None,
    launched_at: datetime | None = None,
) -> WhatsAppCampaign:
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        phone_number_id=f"pn-commercial-{uuid4().hex}",
        phone_number="+5511999999999",
        status=WaConnectionStatus.connected,
    )
    db.add(connection)
    await db.flush()
    campaign = WhatsAppCampaign(
        workspace_id=workspace_id,
        whatsapp_connection_id=connection.id,
        title=title,
        status=CampaignStatus.running,
        eligible_recipients=eligible_recipients,
        total_recipients=eligible_recipients,
        launched_at=launched_at,
    )
    db.add(campaign)
    await db.flush()
    db.add(
        WhatsAppCampaignTemplateSnapshot(
            campaign_id=campaign.id,
            template_name=f"template_{uuid4().hex[:8]}",
            category=category,
            language="pt_BR",
            status=MetaTemplateStatus.approved,
            header_parameters=[],
            body_parameters=[],
            components=[],
            raw_payload={},
        )
    )
    for item in recipients or []:
        db.add(
            WhatsAppCampaignRecipient(
                campaign_id=campaign.id,
                workspace_id=workspace_id,
                phone=item["phone"],
                status=item.get("status", CampaignRecipientStatus.queued),
                send_context=item.get("send_context", {}),
                delivered_at=item.get("delivered_at"),
                read_at=item.get("read_at"),
            )
        )
    await db.flush()
    return campaign


def _currency_totals(payload: dict) -> dict[str, dict]:
    return {item["currency"]: item for item in payload["totalsByCurrency"]}


@pytest.mark.asyncio
async def test_root_manages_versioned_rate_cards_idempotently_and_support_cannot(
    platform_client,
    db_session: AsyncSession,
    platform_auth_context,
) -> None:
    empty = await platform_client.get("/api/v1/platform/meta-commercial-rate-cards")
    assert empty.status_code == 200, empty.text
    assert empty.json() == []

    first_start = datetime(2026, 1, 1, tzinfo=UTC)
    first_end = datetime(2026, 7, 1, tzinfo=UTC)
    first_payload = _rate_payload(
        effective_from=first_start,
        effective_to=first_end,
    )
    created = await platform_client.put(
        "/api/v1/platform/meta-commercial-rate-cards/meta-marketing-br-brl-v1",
        json=first_payload,
    )
    replayed = await platform_client.put(
        "/api/v1/platform/meta-commercial-rate-cards/meta-marketing-br-brl-v1",
        json=first_payload,
    )
    assert created.status_code == 200, created.text
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["id"] == created.json()["id"]
    assert created.json()["commercialRate"] == "0.25000000"
    assert created.json()["sourceUrl"] == META_PRICING_SOURCE_URL
    canonical = list(
        await db_session.scalars(
            select(PlatformAuditEvent)
            .where(
                PlatformAuditEvent.resource_id == created.json()["id"],
                PlatformAuditEvent.action == "meta_rate_card.upsert",
            )
            .order_by(PlatformAuditEvent.occurred_at.asc())
        )
    )
    assert len(canonical) == 2
    assert canonical[0].before is None
    assert canonical[0].after["commercialRate"] == "0.25000000"
    assert canonical[1].changed_paths == []

    adjacent = await platform_client.put(
        "/api/v1/platform/meta-commercial-rate-cards/meta-marketing-br-brl-v2",
        json=_rate_payload(
            commercial_rate="0.30000000",
            effective_from=first_end,
        ),
    )
    assert adjacent.status_code == 200, adjacent.text

    overlapping = await platform_client.put(
        "/api/v1/platform/meta-commercial-rate-cards/meta-marketing-br-brl-overlap",
        json=_rate_payload(
            commercial_rate="0.27500000",
            effective_from=first_end - timedelta(days=1),
            effective_to=first_end + timedelta(days=1),
        ),
    )
    assert overlapping.status_code == 409, overlapping.text
    assert overlapping.json()["detail"]["code"] == "META-COMMERCIAL-RATE-CONFLICT"

    invalid_source = await platform_client.put(
        "/api/v1/platform/meta-commercial-rate-cards/meta-invalid-source",
        json={
            **_rate_payload(
                category="utility",
                effective_from=first_start,
            ),
            "sourceUrl": "https://example.com/rates",
        },
    )
    assert invalid_source.status_code == 422

    listed = await platform_client.get("/api/v1/platform/meta-commercial-rate-cards")
    assert listed.status_code == 200, listed.text
    assert [item["rateCardKey"] for item in listed.json()] == [
        "meta-marketing-br-brl-v2",
        "meta-marketing-br-brl-v1",
    ]

    platform_user = await db_session.get(User, platform_auth_context.user_id)
    assert platform_user is not None
    platform_user.is_platform_admin = False
    platform_user.is_internal_staff = True
    platform_user.platform_access_active = True
    platform_user.platform_role_package = "support"
    await db_session.flush()

    support_list = await platform_client.get("/api/v1/platform/meta-commercial-rate-cards")
    support_write = await platform_client.put(
        "/api/v1/platform/meta-commercial-rate-cards/meta-support-forbidden",
        json=_rate_payload(
            category="authentication",
            country_code="*",
            currency="USD",
            effective_from=first_start,
        ),
    )
    assert support_list.status_code == 403
    assert support_write.status_code == 403
    assert support_write.json()["detail"]["code"] == "PLATFORM-ROOT-REQUIRED"


@pytest.mark.asyncio
async def test_meta_rate_card_rolls_back_when_canonical_audit_fails(
    platform_client,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    before_count = int(
        (await db_session.scalar(select(func.count(MetaCommercialRateCard.id)))) or 0
    )
    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.put(
            "/api/v1/platform/meta-commercial-rate-cards/meta-audit-rollback",
            json=_rate_payload(effective_from=datetime(2028, 1, 1, tzinfo=UTC)),
        )
    db_session.expire_all()
    after_count = int(
        (await db_session.scalar(select(func.count(MetaCommercialRateCard.id)))) or 0
    )
    assert after_count == before_count


@pytest.mark.asyncio
async def test_tenant_cannot_access_root_rate_card_management(client) -> None:
    listed = await client.get("/api/v1/platform/meta-commercial-rate-cards")
    written = await client.put(
        "/api/v1/platform/meta-commercial-rate-cards/meta-tenant-forbidden",
        json=_rate_payload(
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )

    assert listed.status_code == 403
    assert written.status_code == 403
    assert written.json()["detail"]["code"] == "BLACKBOX-IDENTITY-REQUIRED"


@pytest.mark.asyncio
async def test_commercial_charge_summary_requires_finance_permission(
    client,
    db_session: AsyncSession,
    auth_context,
) -> None:
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    assert membership is not None
    membership.role = WorkspaceRole.operator
    auth_context.role = WorkspaceRole.operator
    await db_session.flush()

    response = await client.get("/api/v1/whatsapp/commercial-charges")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_tenant_summary_exposes_only_commercial_charges_grouped_by_currency(
    client,
    db_session: AsyncSession,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    db_session.add_all(
        [
            MetaCommercialRateCard(
                rate_card_key="meta-marketing-br-brl-current",
                category="marketing",
                country_code="BR",
                currency="BRL",
                commercial_rate=Decimal("0.25000000"),
                effective_from=now - timedelta(days=30),
                source_url=META_PRICING_SOURCE_URL,
                checked_at=now,
                is_active=True,
            ),
            MetaCommercialRateCard(
                rate_card_key="meta-marketing-global-usd-current",
                category="marketing",
                country_code="*",
                currency="USD",
                commercial_rate=Decimal("0.10000000"),
                effective_from=now - timedelta(days=30),
                source_url=META_PRICING_SOURCE_URL,
                checked_at=now,
                is_active=True,
            ),
        ]
    )
    campaign = await _campaign(
        db_session,
        workspace_id=auth_context.workspace_id,
        title="Campanha comercial visível",
        category="MARKETING",
        eligible_recipients=4,
        launched_at=now,
        recipients=[
            {
                "phone": "+5511988880001",
                "status": CampaignRecipientStatus.delivered,
                "delivered_at": now,
            },
            {
                "phone": "+14155550002",
                "status": CampaignRecipientStatus.read,
                "send_context": {"countryCode": "us"},
                "read_at": now,
            },
            {
                "phone": "14155550003",
                "status": CampaignRecipientStatus.failed,
            },
        ],
    )
    db_session.add(
        OmnichannelMessageCostLedgerEntry(
            workspace_id=auth_context.workspace_id,
            message_id=uuid4(),
            thread_id=uuid4(),
            delivery_event_id=uuid4(),
            provider="PRIVATE-INTERNAL-PROVIDER-MARKER",
            connection_key="private-internal-connection",
            amount=Decimal("999.999999"),
            currency="CHF",
            quantity=Decimal("1"),
            unit="message",
            pricing_category="private-internal-category",
            idempotency_key=f"private-ledger-{uuid4()}",
            occurred_at=now,
            entry_metadata={"privateLedgerMarker": True},
        )
    )
    await db_session.flush()

    response = await client.get(
        "/api/v1/whatsapp/commercial-charges",
        params={"campaignId": str(campaign.id)},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["workspaceId"] == str(auth_context.workspace_id)
    assert payload["requestedCampaignId"] == str(campaign.id)
    assert payload["chargeBasis"] == "delivered_messages"
    assert payload["internalProviderCostExposed"] is False
    assert "PRIVATE-INTERNAL-PROVIDER-MARKER" not in response.text
    assert "privateLedgerMarker" not in response.text
    assert "private-internal-category" not in response.text

    campaign_summary = payload["campaigns"][0]
    assert campaign_summary["templateCategory"] == "marketing"
    assert campaign_summary["eligibleRecipients"] == 4
    assert campaign_summary["confirmedRecipients"] == 2
    assert campaign_summary["missingRates"] == []

    totals = _currency_totals(campaign_summary)
    assert totals["BRL"]["estimatedRecipientCount"] == 1
    assert totals["BRL"]["confirmedRecipientCount"] == 1
    assert Decimal(totals["BRL"]["estimatedCommercialCharge"]) == Decimal("0.25")
    assert Decimal(totals["BRL"]["confirmedCommercialCharge"]) == Decimal("0.25")
    assert totals["USD"]["estimatedRecipientCount"] == 3
    assert totals["USD"]["confirmedRecipientCount"] == 1
    assert Decimal(totals["USD"]["estimatedCommercialCharge"]) == Decimal("0.30")
    assert Decimal(totals["USD"]["confirmedCommercialCharge"]) == Decimal("0.10")
    assert _currency_totals(payload) == totals

    lines = campaign_summary["commercialChargeLines"]
    assert {line["commercialRate"] for line in lines} == {"0.10000000", "0.25000000"}
    assert {line["rateSourceUrl"] for line in lines} == {META_PRICING_SOURCE_URL}
    assert all(line["rateEffectiveFrom"] for line in lines)
    br_line = next(line for line in lines if line["recipientCountryCode"] == "BR")
    us_line = next(line for line in lines if line["recipientCountryCode"] == "US")
    aggregate_line = next(
        line
        for line in lines
        if line["countryResolution"] == "campaign_aggregate_fallback"
    )
    assert br_line["countryResolution"] == "phone_prefix_55"
    assert br_line["rateMatch"] == "exact"
    assert br_line["rateCardKey"] == "meta-marketing-br-brl-current"
    assert us_line["countryResolution"] == "send_context"
    assert us_line["rateMatch"] == "global_fallback"
    assert us_line["rateCountryCode"] == "*"
    assert aggregate_line["rateMatch"] == "global_fallback"


@pytest.mark.asyncio
async def test_tenant_summary_reports_missing_rate_and_invalid_country_explicitly(
    client,
    db_session: AsyncSession,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    db_session.add(
        MetaCommercialRateCard(
            rate_card_key="meta-marketing-global-inactive",
            category="marketing",
            country_code="*",
            currency="USD",
            commercial_rate=Decimal("0.10000000"),
            effective_from=now - timedelta(days=30),
            source_url=META_PRICING_SOURCE_URL,
            checked_at=now,
            is_active=False,
        )
    )
    campaign = await _campaign(
        db_session,
        workspace_id=auth_context.workspace_id,
        title="Campanha sem rate ativo",
        category="marketing",
        eligible_recipients=1,
        launched_at=now,
        recipients=[
            {
                "phone": "+5511988880999",
                "status": CampaignRecipientStatus.delivered,
                "send_context": {"countryCode": "BRA"},
                "delivered_at": now,
            }
        ],
    )
    await db_session.flush()

    response = await client.get(
        "/api/v1/whatsapp/commercial-charges",
        params={"campaignId": str(campaign.id)},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["totalsByCurrency"] == []
    assert len(payload["missingRates"]) == 2
    assert {item["chargeStage"] for item in payload["missingRates"]} == {
        "estimated",
        "confirmed",
    }
    assert {item["reason"] for item in payload["missingRates"]} == {
        "no_effective_rate"
    }
    assert {item["recipientCountryCode"] for item in payload["missingRates"]} == {"*"}
    assert {item["countryResolution"] for item in payload["missingRates"]} == {
        "invalid_send_context_fallback"
    }
    assert all(item["recipientCount"] == 1 for item in payload["missingRates"])
    assert all(item["candidateRateCardKeys"] == [] for item in payload["missingRates"])


@pytest.mark.asyncio
async def test_commercial_charge_summary_is_workspace_isolated(
    client,
    db_session: AsyncSession,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    own_campaign = await _campaign(
        db_session,
        workspace_id=auth_context.workspace_id,
        title="Campanha do workspace ativo",
        eligible_recipients=0,
        launched_at=now,
    )
    other_workspace = await _workspace(db_session, "isolado")
    foreign_campaign = await _campaign(
        db_session,
        workspace_id=other_workspace.id,
        title="FOREIGN-CAMPAIGN-PRIVATE-MARKER",
        eligible_recipients=0,
        launched_at=now,
    )
    await db_session.flush()

    listed = await client.get("/api/v1/whatsapp/commercial-charges")
    foreign = await client.get(
        "/api/v1/whatsapp/commercial-charges",
        params={"campaignId": str(foreign_campaign.id)},
    )

    assert listed.status_code == 200, listed.text
    assert [item["campaignId"] for item in listed.json()["campaigns"]] == [
        str(own_campaign.id)
    ]
    assert "FOREIGN-CAMPAIGN-PRIVATE-MARKER" not in listed.text
    assert foreign.status_code == 404
    assert foreign.json()["detail"]["code"] == "WA-COMMERCIAL-CAMPAIGN-NOT-FOUND"
