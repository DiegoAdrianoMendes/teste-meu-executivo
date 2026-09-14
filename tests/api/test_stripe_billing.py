"""Testes F5 — Stripe Checkout/Portal (billing da plataforma) + integração
com o webhook Stripe já existente (`app/api/v1/endpoints/stripe_webhook.py`).

Cobre: gate fechado -> adapter levanta; assinatura inválida -> 400 sem
recibo; evento válido (fake) -> subscription atualizada; replay do mesmo
event id -> no-op; timestamp velho -> rejeita.
"""
from __future__ import annotations

import json
import time
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

import app.billing.stripe_adapter as stripe_adapter_module
import app.services.stripe_billing_service as stripe_billing_service_module
from app.billing.fake_stripe import FakeStripeAdapter
from app.billing.stripe_adapter import (
    StripeAdapter,
    StripeNotAllowedError,
    StripeSignatureError,
    _flatten_stripe_params,
)
from app.core.config import settings
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    StripeWebhookEventReceipt,
    Subscription,
    SubscriptionStatus,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole
from app.services.commercial_catalog_service import CommercialCatalogService

STRIPE_WEBHOOK_URL = "/api/v1/webhooks/stripe"


def _open_external_gate(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)


async def _make_plan(db_session, *, suffix: str | None = None) -> Plan:
    suffix = suffix or uuid4().hex[:8]
    plan = Plan(
        name=f"Stripe Billing Plan {suffix}",
        tier=PlanTier.starter,
        price_monthly_cents=29700,
        price_annually_cents=297000,
        max_users=5,
        max_matters=None,
        max_tokens_per_month=1_000_000,
        max_ai_agents=3,
        features={},
        stripe_product_id=f"prod_fake_{suffix}",
        stripe_price_monthly_id=f"price_month_{suffix}",
        stripe_price_annually_id=f"price_year_{suffix}",
        is_active=True,
        is_public=True,
        sort_order=1,
    )
    db_session.add(plan)
    await db_session.flush()
    return plan


# ── app/billing/stripe_adapter.py: choke point + parsing puro ──────────────


def test_flatten_stripe_params_uses_bracket_notation():
    body = {
        "mode": "subscription",
        "line_items": [{"price": "price_123", "quantity": 1}],
        "metadata": {"workspace_id": "abc"},
    }
    flattened = dict(_flatten_stripe_params(body))
    assert flattened["mode"] == "subscription"
    assert flattened["line_items[0][price]"] == "price_123"
    assert flattened["line_items[0][quantity]"] == "1"
    assert flattened["metadata[workspace_id]"] == "abc"


@pytest.mark.asyncio
async def test_stripe_adapter_raises_when_gate_closed(monkeypatch):
    monkeypatch.setattr(stripe_adapter_module, "external_delivery_allowed", lambda: False)
    adapter = StripeAdapter(secret_key="sk_test_fake")

    with pytest.raises(StripeNotAllowedError):
        await adapter.create_checkout_session(
            workspace_id=uuid4(),
            plan_price_id="price_month_x",
            success_url="https://app.local/ok",
            cancel_url="https://app.local/cancel",
        )


@pytest.mark.asyncio
async def test_stripe_adapter_create_checkout_session_via_mock_transport(monkeypatch):
    monkeypatch.setattr(stripe_adapter_module, "external_delivery_allowed", lambda: True)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/checkout/sessions"
        body = request.content.decode("utf-8")
        assert "line_items%5B0%5D%5Bprice%5D=price_month_x" in body
        return httpx.Response(200, json={"id": "cs_test_1", "url": "https://checkout.stripe.com/cs_test_1"})

    adapter = StripeAdapter(
        secret_key="sk_test_fake",
        transport=httpx.MockTransport(handler),
    )
    result = await adapter.create_checkout_session(
        workspace_id=uuid4(),
        plan_price_id="price_month_x",
        success_url="https://app.local/ok",
        cancel_url="https://app.local/cancel",
    )
    assert result.session_id == "cs_test_1"
    assert result.url == "https://checkout.stripe.com/cs_test_1"


@pytest.mark.asyncio
async def test_stripe_checkout_can_charge_effective_canonical_amount(monkeypatch):
    monkeypatch.setattr(stripe_adapter_module, "external_delivery_allowed", lambda: True)

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8")
        assert "price_data%5D%5Bunit_amount%5D=60900" in body
        assert "price_data%5D%5Bcurrency%5D=brl" in body
        assert "product_data%5D%5Bname%5D=Advanced" in body
        assert "line_items%5B0%5D%5Bprice%5D=" not in body
        return httpx.Response(200, json={"id": "cs_effective", "url": "https://checkout.stripe.com/effective"})

    adapter = StripeAdapter(secret_key="sk_test_fake", transport=httpx.MockTransport(handler))
    await adapter.create_checkout_session(
        workspace_id=uuid4(),
        plan_price_id="price_advanced_list",
        amount_cents=60900,
        offer_name="Advanced",
        success_url="https://app.local/ok",
        cancel_url="https://app.local/cancel",
    )


@pytest.mark.asyncio
async def test_stripe_adapter_create_billing_portal_session_via_mock_transport(monkeypatch):
    monkeypatch.setattr(stripe_adapter_module, "external_delivery_allowed", lambda: True)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/billing_portal/sessions"
        return httpx.Response(200, json={"id": "bps_test_1", "url": "https://billing.stripe.com/bps_test_1"})

    adapter = StripeAdapter(secret_key="sk_test_fake", transport=httpx.MockTransport(handler))
    result = await adapter.create_billing_portal_session(
        customer_id="cus_test_1", return_url="https://app.local/settings"
    )
    assert result.session_id == "bps_test_1"
    assert result.url == "https://billing.stripe.com/bps_test_1"


def test_verify_webhook_signature_accepts_valid_signature():
    adapter = StripeAdapter(secret_key="sk_test_fake")
    payload = json.dumps({"id": "evt_1", "type": "test"}).encode("utf-8")
    sig_header = FakeStripeAdapter.sign_event(payload, "whsec_test")

    event = adapter.verify_webhook_signature(payload, sig_header, "whsec_test")
    assert event["id"] == "evt_1"


def test_verify_webhook_signature_rejects_old_timestamp():
    adapter = StripeAdapter(secret_key="sk_test_fake")
    payload = json.dumps({"id": "evt_old", "type": "test"}).encode("utf-8")
    old_timestamp = int(time.time()) - 3600
    sig_header = FakeStripeAdapter.sign_event(payload, "whsec_test", timestamp=old_timestamp)

    with pytest.raises(StripeSignatureError):
        adapter.verify_webhook_signature(payload, sig_header, "whsec_test")


# ── app/billing/fake_stripe.py: determinístico ──────────────────────────────


@pytest.mark.asyncio
async def test_fake_stripe_adapter_is_deterministic_and_never_touches_network():
    fake = FakeStripeAdapter()
    first = await fake.create_checkout_session(
        workspace_id=uuid4(),
        plan_price_id="price_month_x",
        success_url="https://app.local/ok",
        cancel_url="https://app.local/cancel",
    )
    second = await fake.create_checkout_session(
        workspace_id=uuid4(),
        plan_price_id="price_month_x",
        success_url="https://app.local/ok",
        cancel_url="https://app.local/cancel",
    )
    assert first.session_id == "cs_fake_000001"
    assert second.session_id == "cs_fake_000002"
    assert len(fake.created_checkout_sessions) == 2

    portal = await fake.create_billing_portal_session(
        customer_id="cus_fake_1", return_url="https://app.local/settings"
    )
    assert portal.session_id == "bps_fake_000001"


# ── Endpoints: POST /api/v1/billing/checkout-session e /portal-session ─────


@pytest.mark.asyncio
async def test_checkout_session_endpoint_404_when_plan_unknown(client, db_session, auth_context):
    response = await client.post(
        "/api/v1/billing/checkout-session",
        json={
            "planPriceId": "price_does_not_exist",
            "successUrl": "https://app.local/ok",
            "cancelUrl": "https://app.local/cancel",
        },
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "STRIPE-BILLING-404"


@pytest.mark.asyncio
async def test_checkout_session_endpoint_returns_503_when_gate_closed(
    client, db_session, auth_context, monkeypatch
):
    plan = await _make_plan(db_session)
    monkeypatch.setattr(
        stripe_billing_service_module,
        "get_stripe_gateway",
        lambda: StripeAdapter(secret_key="sk_test_fake"),
    )
    monkeypatch.setattr(stripe_adapter_module, "external_delivery_allowed", lambda: False)

    response = await client.post(
        "/api/v1/billing/checkout-session",
        json={
            "planPriceId": plan.stripe_price_monthly_id,
            "successUrl": "https://app.local/ok",
            "cancelUrl": "https://app.local/cancel",
        },
    )

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "KLM-EXT-001"
    count = await db_session.scalar(
        select(func.count(Subscription.id)).where(Subscription.workspace_id == auth_context.workspace_id)
    )
    assert count == 0


@pytest.mark.asyncio
async def test_checkout_session_endpoint_creates_local_subscription_with_fake_gateway(
    client, db_session, auth_context, monkeypatch
):
    plan = await _make_plan(db_session)
    fake_gateway = FakeStripeAdapter()
    monkeypatch.setattr(stripe_billing_service_module, "get_stripe_gateway", lambda: fake_gateway)

    response = await client.post(
        "/api/v1/billing/checkout-session",
        json={
            "planPriceId": plan.stripe_price_monthly_id,
            "successUrl": "https://app.local/ok",
            "cancelUrl": "https://app.local/cancel",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["sessionId"] == "cs_fake_000001"
    assert payload["url"]
    assert len(fake_gateway.created_checkout_sessions) == 1
    assert fake_gateway.created_checkout_sessions[0]["customer_email"] == "teste@example.com"

    subscription = await db_session.scalar(
        select(Subscription).where(Subscription.workspace_id == auth_context.workspace_id)
    )
    assert subscription is not None
    assert subscription.plan_id == plan.id
    assert subscription.status == SubscriptionStatus.trialing


@pytest.mark.asyncio
async def test_checkout_is_blocked_before_external_call_when_canonical_catalog_is_active(
    client, db_session, auth_context, monkeypatch
):
    await CommercialCatalogService(db_session).bootstrap_default_catalog(
        actor_user_id=auth_context.user_id,
        idempotency_key="stripe-canonical-catalog",
    )
    fake_gateway = FakeStripeAdapter()
    monkeypatch.setattr(
        stripe_billing_service_module,
        "get_stripe_gateway",
        lambda: fake_gateway,
    )

    response = await client.post(
        "/api/v1/billing/checkout-session",
        json={
            "planPriceId": "price_not_relevant_after_canonical_gate",
            "successUrl": "https://app.local/ok",
            "cancelUrl": "https://app.local/cancel",
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "STRIPE-BILLING-CANONICAL-409"
    assert fake_gateway.created_checkout_sessions == []


@pytest.mark.asyncio
async def test_checkout_session_endpoint_reuses_existing_subscription_idempotently(
    client, db_session, auth_context, monkeypatch
):
    plan = await _make_plan(db_session)
    fake_gateway = FakeStripeAdapter()
    monkeypatch.setattr(stripe_billing_service_module, "get_stripe_gateway", lambda: fake_gateway)

    first = await client.post(
        "/api/v1/billing/checkout-session",
        json={
            "planPriceId": plan.stripe_price_monthly_id,
            "successUrl": "https://app.local/ok",
            "cancelUrl": "https://app.local/cancel",
        },
    )
    second = await client.post(
        "/api/v1/billing/checkout-session",
        json={
            "planPriceId": plan.stripe_price_monthly_id,
            "successUrl": "https://app.local/ok",
            "cancelUrl": "https://app.local/cancel",
        },
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    count = await db_session.scalar(
        select(func.count(Subscription.id)).where(Subscription.workspace_id == auth_context.workspace_id)
    )
    assert count == 1


@pytest.mark.asyncio
async def test_portal_session_endpoint_404_without_stripe_customer(client, db_session, auth_context):
    response = await client.post(
        "/api/v1/billing/portal-session",
        json={"returnUrl": "https://app.local/settings"},
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "STRIPE-BILLING-404"


@pytest.mark.asyncio
async def test_portal_session_endpoint_returns_url_with_fake_gateway(
    client, db_session, auth_context, monkeypatch
):
    plan = await _make_plan(db_session)
    subscription = Subscription(
        workspace_id=auth_context.workspace_id,
        plan_id=plan.id,
        status=SubscriptionStatus.active,
        interval=BillingInterval.monthly,
        stripe_customer_id="cus_existing_1",
        stripe_subscription_id="sub_existing_1",
        amount_cents=29700,
    )
    db_session.add(subscription)
    await db_session.commit()

    fake_gateway = FakeStripeAdapter()
    monkeypatch.setattr(stripe_billing_service_module, "get_stripe_gateway", lambda: fake_gateway)

    response = await client.post(
        "/api/v1/billing/portal-session",
        json={"returnUrl": "https://app.local/settings"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["sessionId"] == "bps_fake_000001"
    assert fake_gateway.created_portal_sessions[0]["customer_id"] == "cus_existing_1"


@pytest.mark.asyncio
async def test_checkout_and_portal_endpoints_require_workspace_admin(
    client, db_session, auth_context
):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.role = WorkspaceRole.viewer
    await db_session.commit()
    auth_context.role = WorkspaceRole.viewer

    checkout_response = await client.post(
        "/api/v1/billing/checkout-session",
        json={
            "planPriceId": "price_whatever",
            "successUrl": "https://app.local/ok",
            "cancelUrl": "https://app.local/cancel",
        },
    )
    portal_response = await client.post(
        "/api/v1/billing/portal-session",
        json={"returnUrl": "https://app.local/settings"},
    )

    assert checkout_response.status_code == 403
    assert portal_response.status_code == 403


# ── Integração com o webhook Stripe existente, assinado via FakeStripeAdapter ──


async def _create_workspace_subscription(
    db_session, *, stripe_subscription_id: str, status: SubscriptionStatus = SubscriptionStatus.trialing
) -> Subscription:
    suffix = uuid4().hex[:8]
    plan = await _make_plan(db_session, suffix=suffix)
    workspace = Workspace(
        name=f"Stripe Billing WS {suffix}",
        slug=f"stripe-billing-{suffix}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    subscription = Subscription(
        workspace_id=workspace.id,
        plan_id=plan.id,
        stripe_subscription_id=stripe_subscription_id,
        status=status,
        interval=BillingInterval.monthly,
        amount_cents=29700,
    )
    db_session.add(subscription)
    await db_session.commit()
    return subscription


@pytest.mark.asyncio
async def test_webhook_valid_fake_signed_event_updates_subscription(client, db_session, monkeypatch):
    _open_external_gate(monkeypatch)
    secret = "whsec_f5_valid"
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)
    subscription = await _create_workspace_subscription(
        db_session, stripe_subscription_id="sub_f5_valid"
    )

    event = {
        "id": "evt_f5_valid",
        "created": int(time.time()),
        "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_f5_valid", "status": "active"}},
    }
    body = json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = FakeStripeAdapter.sign_event(body, secret)

    response = await client.post(
        STRIPE_WEBHOOK_URL,
        content=body,
        headers={"Content-Type": "application/json", "stripe-signature": signature},
    )

    assert response.status_code == 200, response.text
    await db_session.refresh(subscription)
    assert subscription.status == SubscriptionStatus.active


@pytest.mark.asyncio
async def test_webhook_replay_same_event_id_is_noop(client, db_session, monkeypatch):
    _open_external_gate(monkeypatch)
    secret = "whsec_f5_replay"
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)
    await _create_workspace_subscription(db_session, stripe_subscription_id="sub_f5_replay")

    event = {
        "id": "evt_f5_replay",
        "created": int(time.time()),
        "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_f5_replay", "status": "active"}},
    }
    body = json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = FakeStripeAdapter.sign_event(body, secret)
    headers = {"Content-Type": "application/json", "stripe-signature": signature}

    first = await client.post(STRIPE_WEBHOOK_URL, content=body, headers=headers)
    replay = await client.post(STRIPE_WEBHOOK_URL, content=body, headers=headers)

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    receipt_count = await db_session.scalar(
        select(func.count(StripeWebhookEventReceipt.id)).where(
            StripeWebhookEventReceipt.event_id == "evt_f5_replay"
        )
    )
    assert receipt_count == 1


@pytest.mark.asyncio
async def test_webhook_rejects_old_timestamp(client, db_session, monkeypatch):
    _open_external_gate(monkeypatch)
    secret = "whsec_f5_stale"
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)

    event = {
        "id": "evt_f5_stale",
        "created": int(time.time()) - 3600,
        "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_does_not_matter", "status": "active"}},
    }
    body = json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")
    old_signature = FakeStripeAdapter.sign_event(
        body, secret, timestamp=int(time.time()) - 3600
    )

    response = await client.post(
        STRIPE_WEBHOOK_URL,
        content=body,
        headers={"Content-Type": "application/json", "stripe-signature": old_signature},
    )

    assert response.status_code == 400, response.text
    receipt_count = await db_session.scalar(
        select(func.count(StripeWebhookEventReceipt.id)).where(
            StripeWebhookEventReceipt.event_id == "evt_f5_stale"
        )
    )
    assert receipt_count == 0


@pytest.mark.asyncio
async def test_webhook_invalid_signature_persists_no_receipt(client, db_session, monkeypatch):
    _open_external_gate(monkeypatch)
    secret = "whsec_f5_invalid"
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)

    event = {
        "id": "evt_f5_invalid",
        "created": int(time.time()),
        "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_does_not_matter", "status": "active"}},
    }
    body = json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")
    wrong_signature = FakeStripeAdapter.sign_event(body, "whsec_completely_different_secret")

    response = await client.post(
        STRIPE_WEBHOOK_URL,
        content=body,
        headers={"Content-Type": "application/json", "stripe-signature": wrong_signature},
    )

    assert response.status_code == 400, response.text
    receipt_count = await db_session.scalar(
        select(func.count(StripeWebhookEventReceipt.id)).where(
            StripeWebhookEventReceipt.event_id == "evt_f5_invalid"
        )
    )
    assert receipt_count == 0
