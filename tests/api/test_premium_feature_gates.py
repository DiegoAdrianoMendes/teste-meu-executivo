"""API contracts for boolean premium entitlements."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.finance import (
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
)


async def _seed_feature_plan(
    db: AsyncSession,
    workspace_id: UUID,
    features: dict[EntitlementKey, bool],
) -> dict[EntitlementKey, PlanEntitlement]:
    plan = Plan(
        name=f"Plano feature gate {uuid4().hex[:8]}",
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
    db.add(
        Subscription(
            workspace_id=workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            amount_cents=0,
            currency="BRL",
        )
    )
    rows = {
        entitlement: PlanEntitlement(
            plan_id=plan.id,
            entitlement=entitlement,
            enabled=enabled,
            limit_quantity=None,
            hard_limit=True,
        )
        for entitlement, enabled in features.items()
    }
    db.add_all(rows.values())
    await db.flush()
    return rows


def _assert_disabled(response) -> None:
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "ENT-002"


@pytest.mark.asyncio
async def test_internal_agenda_remains_available_without_external_calendar_sync(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_feature_plan(
        db_session,
        auth_context.workspace_id,
        {EntitlementKey.calendar: False},
    )
    starts_at = datetime.now(UTC) + timedelta(days=2)
    await db_session.commit()

    settings = await client.get("/api/v1/agenda/settings")
    assert settings.status_code == 200, settings.text

    manual_settings = await client.put(
        "/api/v1/agenda/settings",
        json={"provider": "manual"},
    )
    assert manual_settings.status_code == 200, manual_settings.text

    # ``calendar`` means provider connection/synchronisation, not the local
    # agenda.  Start therefore cannot configure Google/Microsoft integration.
    _assert_disabled(
        await client.put(
            "/api/v1/agenda/settings",
            json={
                "provider": "google_meet",
                "defaultMeetingUrl": "https://meet.google.com/test-room",
            },
        )
    )

    created = await client.post(
        "/api/v1/meetings",
        headers={"Idempotency-Key": f"meeting-premium-{uuid4().hex}"},
        json={
            "title": "Reunião interna do Start",
            "startsAt": starts_at.isoformat(),
            "endsAt": (starts_at + timedelta(minutes=45)).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    link = await client.post(
        "/api/v1/agenda/booking-links",
        json={"title": "Link interno do Start"},
    )
    assert link.status_code == 201, link.text

    revoked = await client.post(f"/api/v1/agenda/booking-links/{link.json()['id']}/revoke")
    assert revoked.status_code == 200, revoked.text
    removed = await client.delete(
        f"/api/v1/meetings/{created.json()['id']}",
        params={"expectedVersion": created.json()["version"]},
    )
    assert removed.status_code == 204, removed.text


@pytest.mark.asyncio
async def test_email_ingest_is_blocked_before_creating_customer_context(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_feature_plan(
        db_session,
        auth_context.workspace_id,
        {EntitlementKey.email: False},
    )
    await db_session.commit()

    response = await client.post(
        "/api/v1/klm/omnichannel/email-ingest",
        json={
            "messageId": "premium-email-disabled",
            "senderEmail": "lead@example.com",
            "senderName": "Lead",
            "subject": "Negativa",
            "body": "Mensagem que nao deve entrar no contexto.",
        },
    )

    _assert_disabled(response)


@pytest.mark.asyncio
async def test_signature_and_custom_webhook_activation_require_their_features(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_feature_plan(
        db_session,
        auth_context.workspace_id,
        {
            EntitlementKey.digital_signature: False,
            EntitlementKey.webhooks: False,
        },
    )
    existing = IntegrationConnection(
        workspace_id=auth_context.workspace_id,
        provider=IntegrationProvider.zapsign,
        status=IntegrationStatus.connected,
        label="ZapSign legado",
        capabilities=["sign_documents"],
        settings={},
    )
    db_session.add(existing)
    await db_session.commit()

    _assert_disabled(
        await client.post(
            "/api/v1/finance/integrations",
            json={
                "provider": "docusign",
                "status": "connected",
                "label": "DocuSign bloqueado",
            },
        )
    )
    _assert_disabled(
        await client.post(
            "/api/v1/finance/integrations",
            json={
                "provider": "custom",
                "status": "connected",
                "label": "Webhook bloqueado",
                "webhookUrl": "https://example.com/hook",
                "capabilities": ["webhooks"],
            },
        )
    )
    _assert_disabled(
        await client.post(
            "/api/v1/integrations/connect/zapsign",
            json={"api_token": "nao-deve-ser-validado"},
        )
    )
    _assert_disabled(
        await client.post(
            f"/api/v1/whatsapp/conversations/{uuid4()}/send-contract",
            json={"templateId": "contract-template"},
            headers={"Idempotency-Key": "synthetic-contract-send-gate-test"},
        )
    )

    disconnected = await client.patch(
        f"/api/v1/finance/integrations/{existing.id}",
        json={"status": "disconnected"},
    )
    assert disconnected.status_code == 200, disconnected.text
    assert disconnected.json()["status"] == "disconnected"
