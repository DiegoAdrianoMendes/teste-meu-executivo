"""Checkout recorrente hospedado do Asaas para a assinatura do SaaS."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.billing.port import (
    SaaSBillingGatewayError,
    SaaSCheckoutResult,
    SaaSPortalResult,
    SaaSSubscriptionMutationResult,
)
from app.core.klm_runtime import external_delivery_allowed
from app.models.commercial_catalog import CommercialProvider


class AsaasSaaSBillingAdapter:
    provider = CommercialProvider.asaas
    supports_customer_portal = False

    def __init__(self, *, access_token: str, sandbox: bool = False) -> None:
        if not access_token:
            raise SaaSBillingGatewayError("Credencial Asaas SaaS ausente.")
        self.access_token = access_token
        self.base_url = "https://api-sandbox.asaas.com/v3" if sandbox else "https://api.asaas.com/v3"
        self.checkout_base = "https://sandbox.asaas.com" if sandbox else "https://asaas.com"

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if not external_delivery_allowed():
            raise SaaSBillingGatewayError(
                "Checkout Asaas bloqueado pelo gate de entregas externas."
            )
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers={"access_token": self.access_token, "User-Agent": "MeuExecutivo-SaaS/1.0"},
            timeout=httpx.Timeout(20, connect=5),
        ) as client:
            response = await client.post(path, json=body)
        if response.status_code not in {200, 201}:
            raise SaaSBillingGatewayError(
                f"Asaas recusou o checkout (HTTP {response.status_code})."
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise SaaSBillingGatewayError("Asaas devolveu resposta inválida.")
        return payload

    async def _mutate(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not external_delivery_allowed():
            raise SaaSBillingGatewayError(
                "Administração Asaas bloqueada pelo gate de entregas externas."
            )
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers={"access_token": self.access_token, "User-Agent": "MeuExecutivo-SaaS/1.0"},
            timeout=httpx.Timeout(20, connect=5),
        ) as client:
            response = await client.request(method, path, json=body)
        if method == "DELETE" and response.status_code == 404:
            # Repetir o mesmo comando local não pode transformar um
            # cancelamento já aplicado em erro operacional.
            return {}
        if response.status_code not in {200, 201, 204}:
            raise SaaSBillingGatewayError(
                f"Asaas recusou a alteração da assinatura (HTTP {response.status_code})."
            )
        if response.status_code == 204 or not response.content:
            return {}
        payload = response.json()
        if not isinstance(payload, dict):
            raise SaaSBillingGatewayError("Asaas devolveu alteração de assinatura inválida.")
        return payload

    async def retrieve_checkout(self, checkout_id: str) -> dict[str, Any]:
        if not external_delivery_allowed():
            raise SaaSBillingGatewayError("Consulta Asaas bloqueada pelo gate externo.")
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers={"access_token": self.access_token, "User-Agent": "MeuExecutivo-SaaS/1.0"},
            timeout=httpx.Timeout(20, connect=5),
        ) as client:
            response = await client.get(f"/checkouts/{checkout_id}")
        if response.status_code != 200:
            raise SaaSBillingGatewayError(
                f"Asaas não confirmou o checkout (HTTP {response.status_code})."
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise SaaSBillingGatewayError("Asaas devolveu checkout inválido.")
        return payload

    async def create_checkout_session(self, **kwargs) -> SaaSCheckoutResult:
        metadata = kwargs.get("metadata") or {}
        body = {
            "billingTypes": ["CREDIT_CARD", "PIX"],
            "chargeTypes": ["RECURRENT"],
            "minutesToExpire": 60,
            "externalReference": (
                f"{metadata.get('commercial_checkout_intent_id')}|{metadata.get('workspace_id')}"
            ),
            "callback": {
                "successUrl": kwargs["success_url"],
                "cancelUrl": kwargs["cancel_url"],
                "expiredUrl": kwargs["cancel_url"],
            },
            "items": [{
                "name": kwargs["offer_name"],
                "description": "Assinatura Meu Executivo",
                "quantity": 1,
                "value": round(kwargs["amount_cents"] / 100, 2),
            }],
            "subscription": {
                "cycle": "MONTHLY",
                "nextDueDate": datetime.now(UTC).date().isoformat(),
            },
        }
        payload = await self._post("/checkouts", body)
        session_id = str(payload.get("id") or "")
        url = str(payload.get("link") or payload.get("url") or "")
        if session_id and not url:
            url = f"{self.checkout_base}/checkoutSession/show?id={session_id}"
        if not session_id or not url.startswith("https://"):
            raise SaaSBillingGatewayError("Asaas não devolveu checkout hospedado válido.")
        return SaaSCheckoutResult(session_id, url, payload)

    async def create_billing_portal_session(
        self, *, customer_id: str, return_url: str
    ) -> SaaSPortalResult:
        del customer_id, return_url
        raise SaaSBillingGatewayError(
            "Asaas não oferece portal equivalente; administre a assinatura pelo Meu Executivo."
        )

    async def update_subscription_amount(
        self, *, subscription_id: str, amount_cents: int, idempotency_key: str
    ) -> SaaSSubscriptionMutationResult:
        # A API do Asaas não oferece chave neste endpoint; o comando local é idempotente.
        del idempotency_key
        payload = await self._mutate(
            "PUT",
            f"/subscriptions/{subscription_id}",
            {
                "value": round(amount_cents / 100, 2),
                # Nunca reprificar parcelas pendentes silenciosamente.
                "updatePendingPayments": False,
            },
        )
        return SaaSSubscriptionMutationResult(
            subscription_id=subscription_id,
            provider_status=str(payload.get("status") or "ACTIVE"),
            raw=payload,
        )

    async def cancel_subscription(
        self, *, subscription_id: str, idempotency_key: str
    ) -> SaaSSubscriptionMutationResult:
        del idempotency_key
        payload = await self._mutate("DELETE", f"/subscriptions/{subscription_id}")
        return SaaSSubscriptionMutationResult(
            subscription_id=subscription_id,
            provider_status="DELETED",
            raw=payload,
        )
