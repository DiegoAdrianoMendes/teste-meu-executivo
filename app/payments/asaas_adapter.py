"""Adapter real do Asaas — httpx async, atrás de `external_delivery_allowed()`.

Molde dos adapters de IA do projeto (ver `app/services/chatwoot_client.py`,
`app/services/google_ads_service.py`): fail-closed, timeout/retry
conservador, mapeamento de erro claro. NÃO implementa nada além do que o
plano (`docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md`, seção 3) registra
como verificado — campos "a confirmar no sandbox" são passados adiante de
forma opcional/tolerante, nunca inventados.
"""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.klm_runtime import external_delivery_allowed
from app.payments.port import (
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

ASAAS_PROD_BASE_URL = "https://api.asaas.com/v3"
ASAAS_SANDBOX_BASE_URL = "https://api-sandbox.asaas.com/v3"


class AsaasAdapterError(RuntimeError):
    """Erro de negócio ao chamar a API do Asaas (payload rejeitado, etc)."""


class AsaasNotAllowedError(RuntimeError):
    """Chamada ao Asaas bloqueada: o gate `external_delivery_allowed()` está
    fechado. Fail-closed — nunca contornar."""

    def __init__(self, action: str) -> None:
        super().__init__(
            f"{action} bloqueada: ações externas do Meu Executivo estão desabilitadas "
            "(external_delivery_allowed() == False)."
        )


def _decimal_str(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return format(value, "f")


def _optional_decimal(payload: dict[str, Any], key: str) -> Optional[Decimal]:
    raw = payload.get(key)
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (ValueError, ArithmeticError):
        return None


def _optional_date(payload: dict[str, Any], key: str) -> Optional[date]:
    raw = payload.get(key)
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


class AsaasAdapter(PaymentGatewayPort):
    """Implementação real da `PaymentGatewayPort` sobre a API do Asaas.

    `access_token` é a chave decifrada do `IntegrationConnection` do
    workspace (nunca logada, nunca devolvida em resposta). O header de
    autenticação é literalmente `access_token` (não é `Authorization:
    Bearer`) — plano §3.1.
    """

    def __init__(
        self,
        access_token: str,
        *,
        sandbox: Optional[bool] = None,
        base_url: Optional[str] = None,
        user_agent: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        if not access_token:
            raise AsaasAdapterError("Chave de acesso Asaas ausente para este workspace.")
        self._access_token = access_token
        use_sandbox = settings.ASAAS_SANDBOX if sandbox is None else sandbox
        self.base_url = base_url or (
            ASAAS_SANDBOX_BASE_URL if use_sandbox else ASAAS_PROD_BASE_URL
        )
        self.user_agent = user_agent or settings.ASAAS_USER_AGENT
        self.timeout_seconds = (
            settings.ASAAS_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        )
        self.max_retries = settings.ASAAS_MAX_RETRIES if max_retries is None else max_retries
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "access_token": self._access_token,
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200, 201),
    ) -> dict[str, Any]:
        if not external_delivery_allowed():
            raise AsaasNotAllowedError(f"{method} {path}")

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url,
                    headers=self._headers(),
                    timeout=httpx.Timeout(self.timeout_seconds, connect=5.0),
                    transport=self._transport,
                ) as client:
                    response = await client.request(method, path, json=json, params=params)
                break
            except httpx.TransportError as error:
                last_error = error
                if attempt >= self.max_retries:
                    raise AsaasAdapterError(
                        f"Asaas indisponível ao chamar {method} {path}: {error}"
                    ) from error
                await asyncio.sleep(min(0.5 * (attempt + 1), 2.0))
        else:  # pragma: no cover - loop sempre retorna ou levanta
            raise AsaasAdapterError(str(last_error))

        if response.status_code not in expected:
            detail = response.text[:500]
            raise AsaasAdapterError(
                f"Asaas rejeitou {method} {path} (HTTP {response.status_code}): {detail}"
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise AsaasAdapterError(f"Asaas retornou JSON inválido em {method} {path}.") from error
        if not isinstance(payload, dict):
            raise AsaasAdapterError(f"Asaas retornou um contrato inesperado em {method} {path}.")
        return payload

    async def create_customer(self, request: CustomerRequest) -> CustomerResult:
        body: dict[str, Any] = {"name": request.name}
        if request.external_reference:
            body["externalReference"] = request.external_reference
        if request.email:
            body["email"] = request.email
        if request.phone:
            body["phone"] = request.phone
        if request.mobile_phone:
            body["mobilePhone"] = request.mobile_phone
        if request.cpf_cnpj:
            body["cpfCnpj"] = request.cpf_cnpj
        if request.address_line:
            body["address"] = request.address_line
        if request.notification_disabled is not None:
            body["notificationDisabled"] = request.notification_disabled

        payload = await self._request("POST", "/customers", json=body)
        return CustomerResult(external_id=str(payload.get("id")), raw=payload)

    async def create_charge(self, request: ChargeRequest) -> ChargeResult:
        body: dict[str, Any] = {
            "customer": request.customer_external_id,
            "billingType": request.billing_type.value,
            "value": float(request.value),
            "dueDate": request.due_date.isoformat(),
        }
        if request.external_reference:
            body["externalReference"] = request.external_reference
        if request.description:
            body["description"] = request.description
        if request.installment_count:
            body["installmentCount"] = request.installment_count
            if request.installment_value is not None:
                body["installmentValue"] = float(request.installment_value)
            elif request.total_value is not None:
                body["totalValue"] = float(request.total_value)
        if request.discount is not None:
            discount: dict[str, Any] = {}
            if request.discount.value is not None:
                discount["value"] = float(request.discount.value)
            if request.discount.due_date_limit_days is not None:
                discount["dueDateLimitDays"] = request.discount.due_date_limit_days
            if request.discount.type:
                discount["type"] = request.discount.type
            if discount:
                body["discount"] = discount
        if request.fine is not None:
            fine: dict[str, Any] = {}
            if request.fine.value is not None:
                fine["value"] = float(request.fine.value)
            if request.fine.type:
                fine["type"] = request.fine.type
            if fine:
                body["fine"] = fine
        if request.interest is not None and request.interest.value is not None:
            body["interest"] = {"value": float(request.interest.value)}
        if request.split:
            body["split"] = [
                {
                    "walletId": item.wallet_id,
                    **({"fixedValue": float(item.fixed_value)} if item.fixed_value is not None else {}),
                    **(
                        {"percentualValue": float(item.percentual_value)}
                        if item.percentual_value is not None
                        else {}
                    ),
                }
                for item in request.split
            ]
        if request.callback_url:
            callback: dict[str, Any] = {"successUrl": request.callback_url}
            if request.callback_auto_redirect is not None:
                callback["autoRedirect"] = request.callback_auto_redirect
            body["callback"] = callback

        payload = await self._request("POST", "/payments", json=body)
        return self._charge_result_from_payload(payload)

    def _charge_result_from_payload(self, payload: dict[str, Any]) -> ChargeResult:
        billing_type_raw = payload.get("billingType")
        billing_type = None
        if billing_type_raw:
            try:
                from app.payments.port import BillingType

                billing_type = BillingType(billing_type_raw)
            except ValueError:
                billing_type = None
        return ChargeResult(
            external_id=str(payload.get("id")),
            status=str(payload.get("status") or ""),
            billing_type=billing_type,
            value=_optional_decimal(payload, "value"),
            due_date=_optional_date(payload, "dueDate"),
            invoice_url=payload.get("invoiceUrl"),
            bank_slip_url=payload.get("bankSlipUrl"),
            external_reference=payload.get("externalReference"),
            raw=payload,
        )

    async def get_pix_qr(self, charge_external_id: str) -> PixQrCodeResult:
        payload = await self._request("GET", f"/payments/{charge_external_id}/pixQrCode")
        return PixQrCodeResult(
            encoded_image=payload.get("encodedImage"),
            payload=payload.get("payload"),
            expiration_date=payload.get("expirationDate"),
            raw=payload,
        )

    async def create_subscription(self, request: SubscriptionRequest) -> SubscriptionResult:
        body: dict[str, Any] = {
            "customer": request.customer_external_id,
            "billingType": request.billing_type.value,
            "value": float(request.value),
            "cycle": request.cycle,
            "nextDueDate": request.next_due_date.isoformat(),
        }
        if request.external_reference:
            body["externalReference"] = request.external_reference
        if request.description:
            body["description"] = request.description
        if request.end_date:
            body["endDate"] = request.end_date.isoformat()
        if request.max_payments is not None:
            body["maxPayments"] = request.max_payments
        if request.discount is not None and request.discount.value is not None:
            body["discount"] = {"value": float(request.discount.value)}
        if request.fine is not None and request.fine.value is not None:
            body["fine"] = {"value": float(request.fine.value)}
        if request.interest is not None and request.interest.value is not None:
            body["interest"] = {"value": float(request.interest.value)}
        if request.split:
            body["split"] = [
                {
                    "walletId": item.wallet_id,
                    **({"fixedValue": float(item.fixed_value)} if item.fixed_value is not None else {}),
                    **(
                        {"percentualValue": float(item.percentual_value)}
                        if item.percentual_value is not None
                        else {}
                    ),
                }
                for item in request.split
            ]

        payload = await self._request("POST", "/subscriptions", json=body)
        return SubscriptionResult(
            external_id=str(payload.get("id")),
            status=str(payload.get("status") or ""),
            raw=payload,
        )

    async def create_payment_link(self, request: PaymentLinkRequest) -> PaymentLinkResult:
        body: dict[str, Any] = {
            "name": request.name,
            "chargeType": request.charge_type,
            "billingType": request.billing_type.value,
        }
        if request.value is not None:
            body["value"] = float(request.value)
        if request.description:
            body["description"] = request.description
        if request.due_date_limit_days is not None:
            body["dueDateLimitDays"] = request.due_date_limit_days
        if request.max_installment_count is not None:
            body["maxInstallmentCount"] = request.max_installment_count
        if request.end_date:
            body["endDate"] = request.end_date.isoformat()
        if request.external_reference:
            body["externalReference"] = request.external_reference

        payload = await self._request("POST", "/paymentLinks", json=body)
        return PaymentLinkResult(
            external_id=str(payload.get("id")),
            url=str(payload.get("url") or ""),
            raw=payload,
        )

    async def cancel_charge(self, charge_external_id: str) -> ChargeResult:
        payload = await self._request("DELETE", f"/payments/{charge_external_id}")
        return self._charge_result_from_payload(payload)

    async def get_statement(
        self,
        start: date,
        finish: date,
        offset: int = 0,
        limit: int = 100,
    ) -> StatementPage:
        params = {
            "startDate": start.isoformat(),
            "finishDate": finish.isoformat(),
            "offset": offset,
            "limit": min(limit, 100),
        }
        payload = await self._request("GET", "/financialTransactions", params=params)
        raw_entries = payload.get("data") or []
        entries = tuple(
            StatementEntry(
                transaction_type=str(item.get("type") or ""),
                value=_optional_decimal(item, "value") or Decimal("0"),
                date=_optional_date(item, "date"),
                description=item.get("description"),
                raw=item,
            )
            for item in raw_entries
            if isinstance(item, dict)
        )
        return StatementPage(
            entries=entries,
            total_count=payload.get("totalCount"),
            has_more=bool(payload.get("hasMore")),
        )
