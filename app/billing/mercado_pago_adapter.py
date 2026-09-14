"""Assinatura recorrente hospedada do Mercado Pago para o SaaS."""
from __future__ import annotations

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


class MercadoPagoSaaSBillingAdapter:
    provider = CommercialProvider.mercado_pago
    supports_customer_portal = False

    def __init__(self, *, access_token: str) -> None:
        if not access_token:
            raise SaaSBillingGatewayError("Credencial Mercado Pago ausente.")
        self.access_token = access_token

    async def _post(self, path: str, body: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        if not external_delivery_allowed():
            raise SaaSBillingGatewayError(
                "Checkout Mercado Pago bloqueado pelo gate de entregas externas."
            )
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "X-Idempotency-Key": idempotency_key,
        }
        async with httpx.AsyncClient(
            base_url="https://api.mercadopago.com", headers=headers,
            timeout=httpx.Timeout(20, connect=5),
        ) as client:
            response = await client.post(path, json=body)
        if response.status_code not in {200, 201}:
            raise SaaSBillingGatewayError(
                f"Mercado Pago recusou a assinatura (HTTP {response.status_code})."
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise SaaSBillingGatewayError("Mercado Pago devolveu resposta inválida.")
        return payload

    async def _put(
        self, path: str, body: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        if not external_delivery_allowed():
            raise SaaSBillingGatewayError(
                "Administração Mercado Pago bloqueada pelo gate de entregas externas."
            )
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "X-Idempotency-Key": idempotency_key,
        }
        async with httpx.AsyncClient(
            base_url="https://api.mercadopago.com",
            headers=headers,
            timeout=httpx.Timeout(20, connect=5),
        ) as client:
            response = await client.put(path, json=body)
        if response.status_code != 200:
            raise SaaSBillingGatewayError(
                f"Mercado Pago recusou a alteração da assinatura (HTTP {response.status_code})."
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise SaaSBillingGatewayError(
                "Mercado Pago devolveu alteração de assinatura inválida."
            )
        return payload

    async def retrieve_preapproval(self, preapproval_id: str) -> dict[str, Any]:
        if not external_delivery_allowed():
            raise SaaSBillingGatewayError(
                "Consulta Mercado Pago bloqueada pelo gate externo."
            )
        async with httpx.AsyncClient(
            base_url="https://api.mercadopago.com",
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=httpx.Timeout(20, connect=5),
        ) as client:
            response = await client.get(f"/preapproval/{preapproval_id}")
        if response.status_code != 200:
            raise SaaSBillingGatewayError(
                f"Mercado Pago não confirmou a assinatura (HTTP {response.status_code})."
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise SaaSBillingGatewayError("Mercado Pago devolveu assinatura inválida.")
        return payload

    async def create_checkout_session(self, **kwargs) -> SaaSCheckoutResult:
        email = kwargs.get("customer_email")
        if not email:
            raise SaaSBillingGatewayError("E-mail do assinante é obrigatório no Mercado Pago.")
        metadata = kwargs.get("metadata") or {}
        body = {
            "reason": kwargs["offer_name"],
            "external_reference": (
                f"{metadata.get('commercial_checkout_intent_id')}|{metadata.get('workspace_id')}"
            ),
            "payer_email": email,
            "back_url": kwargs["success_url"],
            "status": "pending",
            "auto_recurring": {
                "frequency": 1,
                "frequency_type": "months",
                "transaction_amount": round(kwargs["amount_cents"] / 100, 2),
                "currency_id": "BRL",
            },
        }
        notification_url = str(kwargs.get("notification_url") or "").strip()
        if not notification_url.startswith("https://"):
            raise SaaSBillingGatewayError(
                "URL pública HTTPS do webhook Mercado Pago é obrigatória."
            )
        body["notification_url"] = notification_url
        payload = await self._post(
            "/preapproval", body,
            kwargs.get("idempotency_key") or f"meu-executivo-{kwargs['workspace_id']}",
        )
        session_id = str(payload.get("id") or "")
        url = str(payload.get("init_point") or "")
        if not session_id or not url.startswith("https://"):
            raise SaaSBillingGatewayError("Mercado Pago não devolveu checkout hospedado válido.")
        return SaaSCheckoutResult(session_id, url, payload)

    async def create_billing_portal_session(
        self, *, customer_id: str, return_url: str
    ) -> SaaSPortalResult:
        del customer_id, return_url
        raise SaaSBillingGatewayError(
            "Mercado Pago não oferece portal equivalente; "
            "administre a assinatura pelo Meu Executivo."
        )

    async def update_subscription_amount(
        self, *, subscription_id: str, amount_cents: int, idempotency_key: str
    ) -> SaaSSubscriptionMutationResult:
        payload = await self._put(
            f"/preapproval/{subscription_id}",
            {
                "auto_recurring": {
                    "transaction_amount": round(amount_cents / 100, 2),
                    "currency_id": "BRL",
                }
            },
            idempotency_key,
        )
        return SaaSSubscriptionMutationResult(
            subscription_id=subscription_id,
            provider_status=str(payload.get("status") or "authorized"),
            raw=payload,
        )

    async def cancel_subscription(
        self, *, subscription_id: str, idempotency_key: str
    ) -> SaaSSubscriptionMutationResult:
        payload = await self._put(
            f"/preapproval/{subscription_id}",
            {"status": "cancelled"},
            idempotency_key,
        )
        return SaaSSubscriptionMutationResult(
            subscription_id=subscription_id,
            provider_status=str(payload.get("status") or "cancelled"),
            raw=payload,
        )
