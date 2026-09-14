"""Contrato do gateway de pagamentos (F1): FakeGatewayAdapter completo,
AsaasAdapter com httpx mockado, e gate fail-closed.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.core.config import settings
from app.payments.asaas_adapter import (
    ASAAS_SANDBOX_BASE_URL,
    AsaasAdapter,
    AsaasAdapterError,
    AsaasNotAllowedError,
)
from app.payments.fake_adapter import FakeGatewayAdapter
from app.payments.port import (
    BillingType,
    ChargeRequest,
    CustomerRequest,
    PaymentLinkRequest,
    SubscriptionRequest,
)


def _open_external_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "online")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)


def _close_external_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)


# ---------------------------------------------------------------------------
# FakeGatewayAdapter — cobertura completa da porta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_adapter_create_customer_and_charge() -> None:
    fake = FakeGatewayAdapter()

    customer = await fake.create_customer(CustomerRequest(name="Cliente Teste", email="c@example.com"))
    assert customer.external_id.startswith("cus_fake_")

    charge = await fake.create_charge(
        ChargeRequest(
            customer_external_id=customer.external_id,
            billing_type=BillingType.PIX,
            value=Decimal("150.00"),
            due_date=date(2026, 8, 1),
            external_reference="entry-123",
        )
    )
    assert charge.external_id.startswith("pay_fake_")
    assert charge.status == "PENDING"
    assert charge.external_reference == "entry-123"
    assert fake.created_charges[0].billing_type == BillingType.PIX


@pytest.mark.asyncio
async def test_fake_adapter_pix_qr_subscription_link_cancel_statement() -> None:
    fake = FakeGatewayAdapter(default_charge_status="CONFIRMED")

    customer = await fake.create_customer(CustomerRequest(name="Cliente"))
    charge = await fake.create_charge(
        ChargeRequest(
            customer_external_id=customer.external_id,
            billing_type=BillingType.UNDEFINED,
            value=Decimal("300"),
            due_date=date(2026, 9, 1),
        )
    )

    qr = await fake.get_pix_qr(charge.external_id)
    assert qr.payload is not None

    subscription = await fake.create_subscription(
        SubscriptionRequest(
            customer_external_id=customer.external_id,
            billing_type=BillingType.CREDIT_CARD,
            value=Decimal("99.90"),
            cycle="MONTHLY",
            next_due_date=date(2026, 9, 1),
        )
    )
    assert subscription.external_id.startswith("sub_fake_")
    assert subscription.status == "ACTIVE"

    link = await fake.create_payment_link(
        PaymentLinkRequest(name="Honorários", charge_type="DETACHED")
    )
    assert link.url.endswith(link.external_id)

    cancelled = await fake.cancel_charge(charge.external_id)
    assert cancelled.status == "DELETED"
    assert charge.external_id in fake.cancelled_charge_ids

    from app.payments.port import StatementEntry

    fake.seed_statement(
        [
            StatementEntry(
                transaction_type="PAYMENT_RECEIVED",
                value=Decimal("300"),
                date=date(2026, 9, 2),
            ),
            StatementEntry(
                transaction_type="PAYMENT_FEE",
                value=Decimal("-4.99"),
                date=date(2026, 9, 2),
            ),
        ]
    )
    page = await fake.get_statement(date(2026, 9, 1), date(2026, 9, 30))
    assert page.total_count == 2
    assert len(page.entries) == 2


@pytest.mark.asyncio
async def test_fake_adapter_fail_next_charge() -> None:
    fake = FakeGatewayAdapter()
    fake.fail_next_charge = True
    with pytest.raises(RuntimeError):
        await fake.create_charge(
            ChargeRequest(
                customer_external_id="cus_1",
                billing_type=BillingType.PIX,
                value=Decimal("10"),
                due_date=date(2026, 8, 1),
            )
        )
    # a próxima chamada volta ao normal
    result = await fake.create_charge(
        ChargeRequest(
            customer_external_id="cus_1",
            billing_type=BillingType.PIX,
            value=Decimal("10"),
            due_date=date(2026, 8, 1),
        )
    )
    assert result.status == "PENDING"


def test_fake_adapter_builds_synthetic_webhook_events() -> None:
    fake = FakeGatewayAdapter()
    event = fake.build_webhook_event("PAYMENT_RECEIVED", "pay_fake_000001")
    assert event["event"] == "PAYMENT_RECEIVED"
    assert event["payment"]["id"] == "pay_fake_000001"
    assert event["id"].startswith("evt_fake_")

    event_2 = fake.build_webhook_event("PAYMENT_RECEIVED", "pay_fake_000001", event_id="evt_fixed_1")
    assert event_2["id"] == "evt_fixed_1"


# ---------------------------------------------------------------------------
# AsaasAdapter — httpx mockado, payload e headers corretos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asaas_adapter_create_charge_sends_correct_payload_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_external_gate(monkeypatch)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "pay_123",
                "status": "PENDING",
                "billingType": "UNDEFINED",
                "value": 500.0,
                "dueDate": "2026-08-15",
                "externalReference": "entry-999",
            },
        )

    adapter = AsaasAdapter(
        access_token="$aact_hmlg_fake",
        sandbox=True,
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.create_charge(
        ChargeRequest(
            customer_external_id="cus_asaas_1",
            billing_type=BillingType.UNDEFINED,
            value=Decimal("500.00"),
            due_date=date(2026, 8, 15),
            external_reference="entry-999",
            installment_count=3,
            installment_value=Decimal("166.67"),
        )
    )

    assert result.external_id == "pay_123"
    assert result.status == "PENDING"
    assert result.external_reference == "entry-999"

    assert captured["url"] == f"{ASAAS_SANDBOX_BASE_URL}/payments"
    headers = captured["headers"]
    assert headers["access_token"] == "$aact_hmlg_fake"
    assert headers["content-type"] == "application/json"
    assert "user-agent" in headers

    body = captured["body"]
    assert body["billingType"] == "UNDEFINED"
    assert body["externalReference"] == "entry-999"
    assert body["installmentCount"] == 3
    assert body["installmentValue"] == 166.67
    assert body["customer"] == "cus_asaas_1"
    assert body["dueDate"] == "2026-08-15"


@pytest.mark.asyncio
async def test_asaas_adapter_get_pix_qr_uses_separate_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_external_gate(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/payments/pay_123/pixQrCode")
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "encodedImage": "base64==",
                "payload": "00020126copia-e-cola",
                "expirationDate": "2027-08-15",
            },
        )

    adapter = AsaasAdapter(
        access_token="$aact_hmlg_fake",
        transport=httpx.MockTransport(handler),
    )
    qr = await adapter.get_pix_qr("pay_123")
    assert qr.payload == "00020126copia-e-cola"
    assert qr.encoded_image == "base64=="


@pytest.mark.asyncio
async def test_asaas_adapter_maps_http_error_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    _open_external_gate(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"errors": [{"description": "customer inválido"}]})

    adapter = AsaasAdapter(access_token="$aact_hmlg_fake", transport=httpx.MockTransport(handler))
    with pytest.raises(AsaasAdapterError) as exc_info:
        await adapter.create_charge(
            ChargeRequest(
                customer_external_id="cus_invalid",
                billing_type=BillingType.PIX,
                value=Decimal("10"),
                due_date=date(2026, 8, 1),
            )
        )
    assert "400" in str(exc_info.value)


@pytest.mark.asyncio
async def test_asaas_adapter_get_statement_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    _open_external_gate(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["startDate"] == "2026-08-01"
        assert request.url.params["finishDate"] == "2026-08-31"
        assert request.url.params["limit"] == "100"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"type": "PAYMENT_RECEIVED", "value": 500.0, "date": "2026-08-05"},
                    {"type": "PAYMENT_FEE", "value": -4.99, "date": "2026-08-05"},
                ],
                "totalCount": 2,
                "hasMore": False,
            },
        )

    adapter = AsaasAdapter(access_token="$aact_hmlg_fake", transport=httpx.MockTransport(handler))
    page = await adapter.get_statement(date(2026, 8, 1), date(2026, 8, 31))
    assert page.total_count == 2
    assert page.entries[0].transaction_type == "PAYMENT_RECEIVED"
    assert page.entries[1].value == Decimal("-4.99")


# ---------------------------------------------------------------------------
# Gate fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asaas_adapter_blocks_when_gate_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _close_external_gate(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - não deve ser chamado
        raise AssertionError("Nenhuma chamada de rede deveria ocorrer com o gate fechado.")

    adapter = AsaasAdapter(access_token="$aact_hmlg_fake", transport=httpx.MockTransport(handler))
    with pytest.raises(AsaasNotAllowedError):
        await adapter.create_charge(
            ChargeRequest(
                customer_external_id="cus_1",
                billing_type=BillingType.PIX,
                value=Decimal("10"),
                due_date=date(2026, 8, 1),
            )
        )


def test_asaas_adapter_requires_access_token() -> None:
    with pytest.raises(AsaasAdapterError):
        AsaasAdapter(access_token="")
