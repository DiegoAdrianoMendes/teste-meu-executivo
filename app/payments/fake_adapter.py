"""Gateway determinístico para teste e dev local — nunca toca a rede.

IDs previsíveis (contador interno), status configurável por chamada, e um
gerador de eventos sintéticos de webhook para exercitar o dedupe/consumo sem
depender do sandbox real do Asaas.
"""
from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from app.payments.port import (
    BillingType,
    ChargeRequest,
    ChargeResult,
    CustomerRequest,
    CustomerResult,
    PaymentGatewayPort,
    PaymentLinkRequest,
    PaymentLinkResult,
    PixQrCodeResult,
    StatementEntry,
    StatementPage,
    SubscriptionRequest,
    SubscriptionResult,
)


def _make_event(event_id: str, event_type: str, payment_id: str, **payment_extra: Any) -> dict[str, Any]:
    return {
        "id": event_id,
        "event": event_type,
        "dateCreated": datetime.now(timezone.utc).isoformat(),
        "payment": {"id": payment_id, **payment_extra},
    }


class FakeGatewayAdapter(PaymentGatewayPort):
    """Implementação em memória da `PaymentGatewayPort`.

    `default_charge_status` controla o status devolvido por `create_charge`
    (ex.: "PENDING", "CONFIRMED", "RECEIVED"). `fail_next_charge` força uma
    exceção na próxima chamada de `create_charge` para exercitar caminhos de
    erro sem gateway real.
    """

    def __init__(self, *, default_charge_status: str = "PENDING") -> None:
        self.default_charge_status = default_charge_status
        self.fail_next_charge = False
        self._customer_ids = itertools.count(1)
        self._charge_ids = itertools.count(1)
        self._subscription_ids = itertools.count(1)
        self._link_ids = itertools.count(1)
        self._event_ids = itertools.count(1)
        self.created_customers: list[CustomerRequest] = []
        self.created_charges: list[ChargeRequest] = []
        self.created_subscriptions: list[SubscriptionRequest] = []
        self.created_payment_links: list[PaymentLinkRequest] = []
        self.cancelled_charge_ids: list[str] = []
        self._charges_by_id: dict[str, ChargeResult] = {}
        self._statement_entries: list[StatementEntry] = []

    def seed_statement(self, entries: list[StatementEntry]) -> None:
        self._statement_entries.extend(entries)

    async def create_customer(self, request: CustomerRequest) -> CustomerResult:
        self.created_customers.append(request)
        external_id = f"cus_fake_{next(self._customer_ids):06d}"
        return CustomerResult(external_id=external_id, raw={"id": external_id, "name": request.name})

    async def create_charge(self, request: ChargeRequest) -> ChargeResult:
        if self.fail_next_charge:
            self.fail_next_charge = False
            raise RuntimeError("Falha simulada em create_charge (fail_next_charge).")
        self.created_charges.append(request)
        external_id = f"pay_fake_{next(self._charge_ids):06d}"
        result = ChargeResult(
            external_id=external_id,
            status=self.default_charge_status,
            billing_type=request.billing_type,
            value=request.value,
            due_date=request.due_date,
            invoice_url=f"https://sandbox.fake-asaas.local/i/{external_id}",
            bank_slip_url=(
                f"https://sandbox.fake-asaas.local/b/{external_id}"
                if request.billing_type in (BillingType.BOLETO, BillingType.UNDEFINED)
                else None
            ),
            external_reference=request.external_reference,
            raw={"id": external_id, "status": self.default_charge_status},
        )
        self._charges_by_id[external_id] = result
        return result

    async def get_pix_qr(self, charge_external_id: str) -> PixQrCodeResult:
        return PixQrCodeResult(
            encoded_image="ZmFrZS1xci1pbWFnZQ==",
            payload=f"00020126fake-pix-copia-e-cola-{charge_external_id}",
            expiration_date=None,
            raw={"paymentId": charge_external_id},
        )

    async def create_subscription(self, request: SubscriptionRequest) -> SubscriptionResult:
        self.created_subscriptions.append(request)
        external_id = f"sub_fake_{next(self._subscription_ids):06d}"
        return SubscriptionResult(external_id=external_id, status="ACTIVE", raw={"id": external_id})

    async def create_payment_link(self, request: PaymentLinkRequest) -> PaymentLinkResult:
        self.created_payment_links.append(request)
        external_id = f"pl_fake_{next(self._link_ids):06d}"
        return PaymentLinkResult(
            external_id=external_id,
            url=f"https://sandbox.fake-asaas.local/pagar/{external_id}",
            raw={"id": external_id},
        )

    async def cancel_charge(self, charge_external_id: str) -> ChargeResult:
        self.cancelled_charge_ids.append(charge_external_id)
        existing = self._charges_by_id.get(charge_external_id)
        if existing is not None:
            cancelled = replace(existing, status="DELETED")
            self._charges_by_id[charge_external_id] = cancelled
            return cancelled
        return ChargeResult(external_id=charge_external_id, status="DELETED", raw={"id": charge_external_id})

    async def get_statement(
        self,
        start: date,
        finish: date,
        offset: int = 0,
        limit: int = 100,
    ) -> StatementPage:
        window = [
            entry
            for entry in self._statement_entries
            if entry.date is None or (start <= entry.date <= finish)
        ]
        page = tuple(window[offset : offset + limit])
        return StatementPage(
            entries=page,
            total_count=len(window),
            has_more=offset + limit < len(window),
        )

    # -- utilidades síncronas p/ testes de webhook -------------------------

    def next_event_id(self) -> str:
        return f"evt_fake_{next(self._event_ids):06d}"

    def build_webhook_event(
        self,
        event_type: str,
        payment_id: str,
        *,
        event_id: Optional[str] = None,
        **payment_extra: Any,
    ) -> dict[str, Any]:
        """Gera um payload de webhook sintético (`{id, event, dateCreated,
        payment{...}}`), no molde do plano §3.6, para testar dedupe/consumo
        sem depender do Asaas real."""

        return _make_event(
            event_id or self.next_event_id(),
            event_type,
            payment_id,
            **payment_extra,
        )
