from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.endpoints.saas_billing_webhooks import (
    _verify_mercado_pago_signature,
)
from app.billing.asaas_adapter import AsaasSaaSBillingAdapter
from app.billing.mercado_pago_adapter import MercadoPagoSaaSBillingAdapter


def _request(*, request_id: str, signature: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/webhooks/saas/mercado-pago",
            "query_string": b"",
            "headers": [
                (b"x-request-id", request_id.encode()),
                (b"x-signature", signature.encode()),
            ],
        }
    )


def test_mercado_pago_webhook_requires_canonical_hmac(monkeypatch) -> None:
    secret = "segredo-webhook-mercado-pago"
    data_id = "preapproval_123"
    request_id = "request_456"
    timestamp = int(datetime.now(UTC).timestamp())
    manifest = f"id:{data_id};request-id:{request_id};ts:{timestamp};"
    digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    monkeypatch.setattr(
        "app.api.v1.endpoints.saas_billing_webhooks.settings.MERCADO_PAGO_WEBHOOK_SECRET",
        secret,
    )

    verified_request, verified_timestamp = _verify_mercado_pago_signature(
        _request(request_id=request_id, signature=f"ts={timestamp},v1={digest}"),
        data_id,
    )

    assert verified_request == request_id
    assert verified_timestamp == timestamp


def test_mercado_pago_webhook_rejects_invalid_hmac(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.v1.endpoints.saas_billing_webhooks.settings.MERCADO_PAGO_WEBHOOK_SECRET",
        "segredo-webhook-mercado-pago",
    )
    timestamp = int(datetime.now(UTC).timestamp())

    with pytest.raises(HTTPException) as error:
        _verify_mercado_pago_signature(
            _request(request_id="request_456", signature=f"ts={timestamp},v1=incorreta"),
            "preapproval_123",
        )

    assert error.value.status_code == 401


def test_mercado_pago_webhook_normalizes_alphanumeric_data_id(monkeypatch) -> None:
    secret = "segredo-webhook-mercado-pago"
    data_id = "PREAPPROVAL_ABC123"
    request_id = "request_789"
    timestamp = int(datetime.now(UTC).timestamp())
    manifest = f"id:{data_id.lower()};request-id:{request_id};ts:{timestamp};"
    digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    monkeypatch.setattr(
        "app.api.v1.endpoints.saas_billing_webhooks.settings.MERCADO_PAGO_WEBHOOK_SECRET",
        secret,
    )

    verified_request, _ = _verify_mercado_pago_signature(
        _request(request_id=request_id, signature=f"ts={timestamp},v1={digest}"),
        data_id,
    )

    assert verified_request == request_id


@pytest.mark.asyncio
async def test_mercado_pago_checkout_sets_subscription_webhook_url(monkeypatch) -> None:
    captured: dict = {}
    adapter = MercadoPagoSaaSBillingAdapter(access_token="test-access-token")

    async def fake_post(path: str, body: dict, idempotency_key: str) -> dict:
        captured.update(path=path, body=body, idempotency_key=idempotency_key)
        return {
            "id": "preapproval_123",
            "init_point": "https://www.mercadopago.com.br/subscriptions/checkout",
        }

    monkeypatch.setattr(adapter, "_post", fake_post)
    await adapter.create_checkout_session(
        workspace_id="8ed84069-da6e-44af-a218-fcbb5cf163b6",
        provider_price_id="mercado_pago:offer-version",
        amount_cents=67900,
        offer_name="Advanced",
        success_url="https://app.meuexecutivo.com/obrigado",
        cancel_url="https://app.meuexecutivo.com/app/configuracoes?checkout=cancelled",
        notification_url=(
            "https://api.meuexecutivo.com/api/v1/webhooks/saas/mercado-pago"
            "?source_news=webhooks"
        ),
        customer_email="titular@example.com",
        metadata={
            "commercial_checkout_intent_id": "c0342958-e664-4caa-bca2-ad14a2d67848",
            "workspace_id": "8ed84069-da6e-44af-a218-fcbb5cf163b6",
        },
        idempotency_key="canonical-checkout-idempotency",
    )

    assert captured["path"] == "/preapproval"
    assert captured["body"]["notification_url"].endswith("?source_news=webhooks")
    assert captured["body"]["status"] == "pending"
    assert captured["idempotency_key"] == "canonical-checkout-idempotency"


@pytest.mark.asyncio
async def test_mercado_pago_checkout_fails_closed_without_public_webhook_url() -> None:
    adapter = MercadoPagoSaaSBillingAdapter(access_token="test-access-token")

    with pytest.raises(Exception, match="URL pública HTTPS"):
        await adapter.create_checkout_session(
            workspace_id="8ed84069-da6e-44af-a218-fcbb5cf163b6",
            provider_price_id="mercado_pago:offer-version",
            amount_cents=67900,
            offer_name="Advanced",
            success_url="https://app.meuexecutivo.com/obrigado",
            cancel_url="https://app.meuexecutivo.com/app/configuracoes?checkout=cancelled",
            notification_url=None,
            customer_email="titular@example.com",
            metadata={},
            idempotency_key="canonical-checkout-idempotency",
        )


@pytest.mark.asyncio
async def test_asaas_subscription_update_preserves_pending_installments(monkeypatch) -> None:
    adapter = AsaasSaaSBillingAdapter(access_token="test-access-token", sandbox=True)
    captured: dict = {}

    async def fake_mutate(method: str, path: str, body: dict | None = None) -> dict:
        captured.update(method=method, path=path, body=body)
        return {"id": "sub_asaas", "status": "ACTIVE"}

    monkeypatch.setattr(adapter, "_mutate", fake_mutate)
    result = await adapter.update_subscription_amount(
        subscription_id="sub_asaas",
        amount_cents=67900,
        idempotency_key="provider-command",
    )

    assert captured == {
        "method": "PUT",
        "path": "/subscriptions/sub_asaas",
        "body": {"value": 679.0, "updatePendingPayments": False},
    }
    assert result.provider_status == "ACTIVE"


@pytest.mark.asyncio
async def test_mercado_pago_subscription_update_and_cancel_are_typed(monkeypatch) -> None:
    adapter = MercadoPagoSaaSBillingAdapter(access_token="test-access-token")
    calls: list[tuple[str, dict, str]] = []

    async def fake_put(path: str, body: dict, idempotency_key: str) -> dict:
        calls.append((path, body, idempotency_key))
        return {"id": "preapproval_123", "status": body.get("status", "authorized")}

    monkeypatch.setattr(adapter, "_put", fake_put)
    updated = await adapter.update_subscription_amount(
        subscription_id="preapproval_123",
        amount_cents=124900,
        idempotency_key="change-plan",
    )
    cancelled = await adapter.cancel_subscription(
        subscription_id="preapproval_123",
        idempotency_key="cancel-plan",
    )

    assert calls[0][1]["auto_recurring"] == {
        "transaction_amount": 1249.0,
        "currency_id": "BRL",
    }
    assert calls[1][1] == {"status": "cancelled"}
    assert updated.provider_status == "authorized"
    assert cancelled.provider_status == "cancelled"
