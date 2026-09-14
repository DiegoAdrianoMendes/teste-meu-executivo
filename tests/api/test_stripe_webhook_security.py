from __future__ import annotations

import hashlib
import hmac
import json
import time
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.v1.endpoints.stripe_webhook import _handle_subscription_updated
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

STRIPE_WEBHOOK_URL = "/api/v1/webhooks/stripe"


def _open_external_gate(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)


def _stripe_request(secret: str) -> tuple[bytes, str]:
    timestamp = int(time.time())
    body = json.dumps(
        {
            "id": "evt_security_closure",
            "created": timestamp,
            "type": "security.closure.test",
            "data": {"object": {}},
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signed_payload = f"{timestamp}.".encode() + body
    signature = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return body, f"t={timestamp},v1={signature}"


def _signed_event(secret: str, event: dict) -> tuple[bytes, str]:
    body = json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")
    timestamp = int(time.time())
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode() + body,
        hashlib.sha256,
    ).hexdigest()
    return body, f"t={timestamp},v1={signature}"


async def _create_stripe_subscription(
    db_session,
    *,
    stripe_subscription_id: str,
    status: SubscriptionStatus = SubscriptionStatus.trialing,
) -> Subscription:
    suffix = uuid4().hex[:8]
    plan = Plan(
        name=f"Stripe webhook {suffix}",
        tier=PlanTier.starter,
        price_monthly_cents=12900,
        price_annually_cents=129000,
        max_users=3,
        max_tokens_per_month=1000,
        max_ai_agents=1,
        features={},
        is_active=True,
        is_public=False,
        sort_order=102,
    )
    workspace = Workspace(
        name=f"Stripe webhook {suffix}",
        slug=f"stripe-webhook-{suffix}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add_all((plan, workspace))
    await db_session.flush()
    subscription = Subscription(
        workspace_id=workspace.id,
        plan_id=plan.id,
        stripe_subscription_id=stripe_subscription_id,
        status=status,
        interval=BillingInterval.monthly,
        amount_cents=12900,
        currency="BRL",
    )
    db_session.add(subscription)
    await db_session.commit()
    return subscription


@pytest.mark.asyncio
async def test_stripe_webhook_respects_external_gate(client, monkeypatch):
    secret = "whsec_test_security"
    body, signature = _stripe_request(secret)
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)

    response = await client.post(
        STRIPE_WEBHOOK_URL,
        content=body,
        headers={"Content-Type": "application/json", "stripe-signature": signature},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KLM-EXT-001"


@pytest.mark.asyncio
async def test_stripe_never_accepts_unsigned_json_in_development(client, monkeypatch):
    _open_external_gate(monkeypatch)
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test_security")

    response = await client.post(
        STRIPE_WEBHOOK_URL,
        json={"id": "evt_unsigned", "type": "security.test", "data": {"object": {}}},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_stripe_webhook_fails_closed_without_configured_secret(client, monkeypatch):
    _open_external_gate(monkeypatch)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "")

    response = await client.post(
        STRIPE_WEBHOOK_URL,
        json={"id": "evt_no_secret", "type": "security.test", "data": {"object": {}}},
    )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_stripe_webhook_rejects_invalid_signature(client, monkeypatch):
    _open_external_gate(monkeypatch)
    secret = "whsec_test_security"
    body, signature = _stripe_request(secret)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)

    response = await client.post(
        STRIPE_WEBHOOK_URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            "stripe-signature": f"{signature.split(',', 1)[0]},v1=invalid",
        },
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_stripe_webhook_accepts_current_valid_signature(client, monkeypatch):
    _open_external_gate(monkeypatch)
    secret = "whsec_test_security"
    body, signature = _stripe_request(secret)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)

    response = await client.post(
        STRIPE_WEBHOOK_URL,
        content=body,
        headers={"Content-Type": "application/json", "stripe-signature": signature},
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}


@pytest.mark.asyncio
async def test_stripe_recognized_handler_failure_returns_5xx_for_retry(
    client,
    monkeypatch,
):
    _open_external_gate(monkeypatch)
    secret = "whsec_test_security"
    timestamp = int(time.time())
    body = json.dumps(
        {
            "id": "evt_transient_failure",
            "created": timestamp,
            "type": "invoice.paid",
            "data": {"object": {"id": "in_transient"}},
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    signature = hmac.new(
        secret.encode(),
        f"{timestamp}.".encode() + body,
        hashlib.sha256,
    ).hexdigest()
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)

    async def fail_handler(*_args, **_kwargs):
        raise RuntimeError("transient database failure with private context")

    monkeypatch.setattr(
        "app.api.v1.endpoints.stripe_webhook._handle_subscription_updated",
        fail_handler,
    )

    response = await client.post(
        STRIPE_WEBHOOK_URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            "stripe-signature": f"t={timestamp},v1={signature}",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Falha temporária ao processar evento Stripe."
    assert "private context" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "event_object"),
    [
        (
            "invoice.paid",
            {
                "id": "in_missing_subscription",
                "subscription": "sub_missing_invoice",
                "lines": {"data": []},
            },
        ),
        (
            "customer.subscription.updated",
            {"id": "sub_missing_update", "status": "active"},
        ),
        (
            "checkout.session.completed",
            {
                "id": "cs_missing_subscription",
                "customer": "cus_missing_checkout",
                "subscription": "sub_missing_checkout",
                "metadata": {
                    "workspace_id": "00000000-0000-0000-0000-000000000001"
                },
            },
        ),
    ],
    ids=("invoice", "subscription", "checkout"),
)
async def test_recognized_event_without_local_subscription_is_retryable_without_receipt(
    client,
    db_session,
    monkeypatch,
    event_type,
    event_object,
):
    _open_external_gate(monkeypatch)
    secret = "whsec_missing_subscription"
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)
    event = {
        "id": f"evt_missing_{event_type.replace('.', '_')}",
        "created": 1_800_000_050,
        "type": event_type,
        "data": {"object": event_object},
    }
    body, signature = _signed_event(secret, event)

    response = await client.post(
        STRIPE_WEBHOOK_URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            "stripe-signature": signature,
        },
    )

    assert response.status_code == 503, response.text
    assert response.json()["detail"] == (
        "Assinatura local indisponível para o evento Stripe."
    )
    assert (
        await db_session.scalar(select(func.count(StripeWebhookEventReceipt.id)))
        == 0
    )


@pytest.mark.asyncio
async def test_invoice_events_use_parent_subscription_and_map_payment(db_session):
    plan = Plan(
        name="Stripe Security Plan",
        tier=PlanTier.starter,
        price_monthly_cents=12900,
        price_annually_cents=129000,
        max_users=3,
        max_tokens_per_month=1000,
        max_ai_agents=1,
        features={},
        is_active=True,
        is_public=False,
        sort_order=99,
    )
    workspace = Workspace(
        name="Stripe Workspace",
        slug=f"stripe-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add_all((plan, workspace))
    await db_session.flush()
    subscription = Subscription(
        workspace_id=workspace.id,
        plan_id=plan.id,
        stripe_subscription_id="sub_parent_correct",
        status=SubscriptionStatus.past_due,
        interval=BillingInterval.monthly,
        amount_cents=12900,
        currency="BRL",
    )
    db_session.add(subscription)
    await db_session.flush()
    invoice = {
        "object": {
            "id": "in_not_the_subscription",
            "parent": {
                "type": "subscription_details",
                "subscription_details": {"subscription": "sub_parent_correct"},
            },
            "status": "paid",
            "total": 14900,
            "amount_paid": 14900,
            "currency": "brl",
            "default_payment_method": "pm_invoice",
            "lines": {"data": [{"period": {"start": 1_700_000_000, "end": 1_702_592_000}}]},
            "status_transitions": {"paid_at": 1_700_000_100},
        }
    }

    await _handle_subscription_updated(invoice, db_session, event_type="invoice.paid")

    assert subscription.status == SubscriptionStatus.active
    assert subscription.amount_cents == 14900
    assert subscription.stripe_payment_method_id == "pm_invoice"
    assert subscription.last_stripe_event["invoice_id"] == "in_not_the_subscription"
    assert subscription.last_stripe_event["payment_status"] == "paid"

    await _handle_subscription_updated(invoice, db_session, event_type="invoice.paid")
    assert subscription.status == SubscriptionStatus.active
    assert subscription.amount_cents == 14900
    assert subscription.last_stripe_event["payment_status"] == "paid"

    await _handle_subscription_updated(
        invoice,
        db_session,
        event_type="invoice.payment_failed",
    )
    assert subscription.status == SubscriptionStatus.past_due
    assert subscription.last_stripe_event["payment_status"] == "failed"


@pytest.mark.asyncio
async def test_stripe_event_receipt_ignores_replay(client, db_session, monkeypatch):
    _open_external_gate(monkeypatch)
    secret = "whsec_replay"
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)
    event = {
        "id": "evt_unique_replay",
        "created": 1_800_000_000,
        "type": "security.replay.test",
        "data": {"object": {}},
    }
    body, signature = _signed_event(secret, event)
    headers = {
        "Content-Type": "application/json",
        "stripe-signature": signature,
    }

    first = await client.post(STRIPE_WEBHOOK_URL, content=body, headers=headers)
    replay = await client.post(STRIPE_WEBHOOK_URL, content=body, headers=headers)

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json() == {"received": True}
    assert (
        await db_session.scalar(select(func.count(StripeWebhookEventReceipt.id)))
        == 1
    )


@pytest.mark.asyncio
async def test_stripe_rejects_reused_event_id_with_different_payload(
    client,
    db_session,
    monkeypatch,
):
    _open_external_gate(monkeypatch)
    secret = "whsec_conflicting_replay"
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)
    event = {
        "id": "evt_conflicting_replay",
        "created": 1_800_000_000,
        "type": "security.replay.test",
        "data": {"object": {"attempt": 1}},
    }
    first_body, first_signature = _signed_event(secret, event)
    first = await client.post(
        STRIPE_WEBHOOK_URL,
        content=first_body,
        headers={
            "Content-Type": "application/json",
            "stripe-signature": first_signature,
        },
    )

    conflicting_event = {
        **event,
        "data": {"object": {"attempt": 2}},
    }
    conflicting_body, conflicting_signature = _signed_event(
        secret,
        conflicting_event,
    )
    conflicting = await client.post(
        STRIPE_WEBHOOK_URL,
        content=conflicting_body,
        headers={
            "Content-Type": "application/json",
            "stripe-signature": conflicting_signature,
        },
    )

    assert first.status_code == 200, first.text
    assert conflicting.status_code == 409, conflicting.text
    assert (
        await db_session.scalar(select(func.count(StripeWebhookEventReceipt.id)))
        == 1
    )


@pytest.mark.asyncio
async def test_stripe_older_event_cannot_regress_subscription(
    client,
    db_session,
    monkeypatch,
):
    _open_external_gate(monkeypatch)
    secret = "whsec_ordering"
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)
    plan = Plan(
        name="Stripe Ordering Plan",
        tier=PlanTier.starter,
        price_monthly_cents=12900,
        price_annually_cents=129000,
        max_users=3,
        max_tokens_per_month=1000,
        max_ai_agents=1,
        features={},
        is_active=True,
        is_public=False,
        sort_order=100,
    )
    workspace = Workspace(
        name="Stripe Ordering Workspace",
        slug=f"stripe-order-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add_all((plan, workspace))
    await db_session.flush()
    subscription = Subscription(
        workspace_id=workspace.id,
        plan_id=plan.id,
        stripe_subscription_id="sub_ordered",
        status=SubscriptionStatus.trialing,
        interval=BillingInterval.monthly,
        amount_cents=12900,
        currency="BRL",
    )
    db_session.add(subscription)
    await db_session.commit()

    newest = {
        "id": "evt_order_new",
        "created": 1_800_000_200,
        "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_ordered", "status": "active"}},
    }
    stale = {
        "id": "evt_order_old",
        "created": 1_800_000_100,
        "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_ordered", "status": "past_due"}},
    }
    for event in (newest, stale):
        body, signature = _signed_event(secret, event)
        response = await client.post(
            STRIPE_WEBHOOK_URL,
            content=body,
            headers={
                "Content-Type": "application/json",
                "stripe-signature": signature,
            },
        )
        assert response.status_code == 200, response.text

    await db_session.refresh(subscription)
    assert subscription.status == SubscriptionStatus.active
    assert subscription.last_stripe_event_id == "evt_order_new"
    assert subscription.last_stripe_event_created == 1_800_000_200
    assert (
        await db_session.scalar(select(func.count(StripeWebhookEventReceipt.id)))
        == 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_order",
    [
        ("invoice.paid", "invoice.payment_failed"),
        ("invoice.payment_failed", "invoice.paid"),
    ],
    ids=("paid-then-failed", "failed-then-paid"),
)
async def test_same_timestamp_payment_state_uses_conservative_precedence(
    client,
    db_session,
    monkeypatch,
    event_order,
):
    _open_external_gate(monkeypatch)
    secret = "whsec_payment_tie"
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)
    subscription = await _create_stripe_subscription(
        db_session,
        stripe_subscription_id="sub_payment_tie",
    )
    events = {
        "invoice.paid": {
            "id": "evt_payment_tie_paid",
            "created": 1_800_000_300,
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": "in_payment_tie_paid",
                    "subscription": "sub_payment_tie",
                    "status": "paid",
                    "lines": {"data": []},
                }
            },
        },
        "invoice.payment_failed": {
            "id": "evt_payment_tie_failed",
            "created": 1_800_000_300,
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "id": "in_payment_tie_failed",
                    "subscription": "sub_payment_tie",
                    "status": "open",
                    "lines": {"data": []},
                }
            },
        },
    }

    for event_type in event_order:
        body, signature = _signed_event(secret, events[event_type])
        response = await client.post(
            STRIPE_WEBHOOK_URL,
            content=body,
            headers={
                "Content-Type": "application/json",
                "stripe-signature": signature,
            },
        )
        assert response.status_code == 200, response.text

    await db_session.refresh(subscription)
    assert subscription.status == SubscriptionStatus.past_due
    assert subscription.last_stripe_event_id == "evt_payment_tie_failed"
    assert subscription.last_stripe_event_created == 1_800_000_300
    assert (
        await db_session.scalar(select(func.count(StripeWebhookEventReceipt.id)))
        == 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("invoice_type", "event_order"),
    [
        ("invoice.paid", ("deleted", "invoice")),
        ("invoice.paid", ("invoice", "deleted")),
        ("invoice.payment_failed", ("deleted", "invoice")),
        ("invoice.payment_failed", ("invoice", "deleted")),
    ],
    ids=(
        "deleted-then-paid",
        "paid-then-deleted",
        "deleted-then-failed",
        "failed-then-deleted",
    ),
)
async def test_same_timestamp_invoice_cannot_regress_deleted_subscription(
    client,
    db_session,
    monkeypatch,
    invoice_type,
    event_order,
):
    _open_external_gate(monkeypatch)
    secret = "whsec_terminal_tie"
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)
    subscription = await _create_stripe_subscription(
        db_session,
        stripe_subscription_id="sub_terminal_tie",
        status=SubscriptionStatus.active,
    )
    events = {
        "deleted": {
            "id": "evt_terminal_tie_deleted",
            "created": 1_800_000_400,
            "type": "customer.subscription.deleted",
            "data": {
                "object": {"id": "sub_terminal_tie", "status": "canceled"}
            },
        },
        "invoice": {
            "id": f"evt_terminal_tie_{invoice_type.replace('.', '_')}",
            "created": 1_800_000_400,
            "type": invoice_type,
            "data": {
                "object": {
                    "id": "in_terminal_tie",
                    "subscription": "sub_terminal_tie",
                    "lines": {"data": []},
                }
            },
        },
    }

    for event_name in event_order:
        body, signature = _signed_event(secret, events[event_name])
        response = await client.post(
            STRIPE_WEBHOOK_URL,
            content=body,
            headers={
                "Content-Type": "application/json",
                "stripe-signature": signature,
            },
        )
        assert response.status_code == 200, response.text

    await db_session.refresh(subscription)
    assert subscription.status == SubscriptionStatus.cancelled
    assert subscription.last_stripe_event_id == "evt_terminal_tie_deleted"
    assert subscription.last_stripe_event_created == 1_800_000_400
    assert (
        await db_session.scalar(select(func.count(StripeWebhookEventReceipt.id)))
        == 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unique_field,duplicate_value",
    [
        ("stripe_customer_id", "cus_unique_non_null"),
        ("stripe_subscription_id", "sub_unique_non_null"),
    ],
)
async def test_non_null_stripe_ids_are_unique(
    db_session,
    unique_field,
    duplicate_value,
):
    plan = Plan(
        name=f"Uniqueness {unique_field}",
        tier=PlanTier.pro,
        price_monthly_cents=100,
        price_annually_cents=1000,
        max_users=1,
        max_tokens_per_month=100,
        max_ai_agents=1,
        features={},
        is_active=True,
        is_public=False,
        sort_order=101,
    )
    workspaces = [
        Workspace(
            name=f"Stripe unique {index}",
            slug=f"stripe-unique-{index}-{uuid4().hex[:8]}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        for index in range(4)
    ]
    db_session.add_all((plan, *workspaces))
    await db_session.flush()

    db_session.add_all(
        (
            Subscription(
                workspace_id=workspaces[0].id,
                plan_id=plan.id,
                status=SubscriptionStatus.trialing,
                interval=BillingInterval.monthly,
            ),
            Subscription(
                workspace_id=workspaces[1].id,
                plan_id=plan.id,
                status=SubscriptionStatus.trialing,
                interval=BillingInterval.monthly,
            ),
        )
    )
    await db_session.flush()

    duplicate_a = Subscription(
        workspace_id=workspaces[2].id,
        plan_id=plan.id,
        status=SubscriptionStatus.trialing,
        interval=BillingInterval.monthly,
        **{unique_field: duplicate_value},
    )
    duplicate_b = Subscription(
        workspace_id=workspaces[3].id,
        plan_id=plan.id,
        status=SubscriptionStatus.trialing,
        interval=BillingInterval.monthly,
        **{unique_field: duplicate_value},
    )
    db_session.add_all((duplicate_a, duplicate_b))
    with pytest.raises(IntegrityError):
        await db_session.flush()
