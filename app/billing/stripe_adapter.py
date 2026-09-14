"""Adapter real do Stripe (billing da PLATAFORMA) — httpx async, sem SDK.

Molde de `app/payments/asaas_adapter.py`: fail-closed, timeout/retry
conservador, choke point único (`_request`) que checa
`external_delivery_allowed()` antes de qualquer chamada de rede.

A API do Stripe recebe `application/x-www-form-urlencoded` com notação de
colchetes para estruturas aninhadas (`line_items[0][price]=...`), não JSON —
por isso `_flatten_stripe_params` em vez de `json=`.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol
from uuid import UUID

import httpx

from app.billing._signature import StripeSignatureError, verify_stripe_signature
from app.core.klm_runtime import external_delivery_allowed

STRIPE_API_BASE_URL = "https://api.stripe.com/v1"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_RETRIES = 2


class StripeAdapterError(RuntimeError):
    """Erro de negócio ao chamar a API do Stripe (payload rejeitado, etc)."""


class StripeNotAllowedError(RuntimeError):
    """Chamada ao Stripe bloqueada: o gate `external_delivery_allowed()` está
    fechado. Fail-closed — nunca contornar."""

    def __init__(self, action: str) -> None:
        super().__init__(
            f"{action} bloqueada: ações externas do Meu Executivo estão desabilitadas "
            "(external_delivery_allowed() == False)."
        )


@dataclass(frozen=True)
class StripeCheckoutSessionResult:
    session_id: str
    url: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StripeBillingPortalSessionResult:
    session_id: str
    url: str
    raw: dict[str, Any] = field(default_factory=dict)


class StripeGatewayPort(Protocol):
    """Contrato mínimo usado pelos endpoints de billing — implementado tanto
    pelo `StripeAdapter` real quanto pelo `FakeStripeAdapter` de teste."""

    async def create_checkout_session(
        self,
        *,
        workspace_id: UUID,
        plan_price_id: str,
        amount_cents: int | None = None,
        offer_name: str | None = None,
        success_url: str,
        cancel_url: str,
        customer_email: Optional[str] = None,
        stripe_customer_id: Optional[str] = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> StripeCheckoutSessionResult: ...

    async def create_billing_portal_session(
        self, *, customer_id: str, return_url: str
    ) -> StripeBillingPortalSessionResult: ...


def _flatten_stripe_params(data: dict[str, Any]) -> dict[str, str]:
    """Achata um dict aninhado em `{chave[colchetes]: valor}` no formato que
    a API form-encoded do Stripe espera.

    Devolve um `dict` (não uma lista de pares) porque `httpx` só trata
    `data=` como formulário quando é um `Mapping` — uma lista de tuplas cai
    no caminho de `content=` bruto e quebra o `AsyncClient` (stream só
    síncrono). A notação de colchetes já garante chaves únicas mesmo para
    listas (`line_items[0][price]`, `line_items[1][price]`, ...).
    """
    out: dict[str, str] = {}

    def walk(key: str, value: Any) -> None:
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                walk(f"{key}[{sub_key}]", sub_value)
        elif isinstance(value, (list, tuple)):
            for index, sub_value in enumerate(value):
                walk(f"{key}[{index}]", sub_value)
        elif value is None:
            return
        elif isinstance(value, bool):
            out[key] = "true" if value else "false"
        else:
            out[key] = str(value)

    for top_key, top_value in data.items():
        walk(top_key, top_value)
    return out


class StripeAdapter:
    """Implementação real do `StripeGatewayPort` sobre a API REST do Stripe.

    `secret_key` é a chave secreta da conta Stripe da plataforma
    (`STRIPE_SECRET_KEY`, global — não por workspace, ao contrário do Asaas).
    """

    def __init__(
        self,
        secret_key: str,
        *,
        base_url: Optional[str] = None,
        user_agent: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        if not secret_key:
            raise StripeAdapterError("Chave secreta do Stripe ausente para esta plataforma.")
        self._secret_key = secret_key
        self.base_url = base_url or STRIPE_API_BASE_URL
        self.user_agent = user_agent or "klm-legal-hub-backend/1.0 (+stripe-billing)"
        self.timeout_seconds = (
            DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        )
        self.max_retries = DEFAULT_MAX_RETRIES if max_retries is None else max_retries
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        expected: tuple[int, ...] = (200, 201),
    ) -> dict[str, Any]:
        """Choke point único: TODA chamada de rede ao Stripe passa por aqui.
        Fail-closed — levanta `StripeNotAllowedError` se o gate estiver
        fechado, antes de sequer montar o cliente HTTP."""
        if not external_delivery_allowed():
            raise StripeNotAllowedError(f"{method} {path}")

        headers = self._headers()
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        form_data = _flatten_stripe_params(data) if data else None

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url,
                    auth=(self._secret_key, ""),
                    headers=headers,
                    timeout=httpx.Timeout(self.timeout_seconds, connect=5.0),
                    transport=self._transport,
                ) as client:
                    response = await client.request(method, path, data=form_data)
                break
            except httpx.TransportError as error:
                last_error = error
                if attempt >= self.max_retries:
                    raise StripeAdapterError(
                        f"Stripe indisponível ao chamar {method} {path}: {error}"
                    ) from error
                await asyncio.sleep(min(0.5 * (attempt + 1), 2.0))
        else:  # pragma: no cover - loop sempre retorna ou levanta
            raise StripeAdapterError(str(last_error))

        if response.status_code not in expected:
            detail = response.text[:500]
            raise StripeAdapterError(
                f"Stripe rejeitou {method} {path} (HTTP {response.status_code}): {detail}"
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise StripeAdapterError(f"Stripe retornou JSON inválido em {method} {path}.") from error
        if not isinstance(payload, dict):
            raise StripeAdapterError(f"Stripe retornou um contrato inesperado em {method} {path}.")
        return payload

    async def create_checkout_session(
        self,
        *,
        workspace_id: UUID,
        plan_price_id: str,
        amount_cents: int | None = None,
        offer_name: str | None = None,
        success_url: str,
        cancel_url: str,
        customer_email: Optional[str] = None,
        stripe_customer_id: Optional[str] = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> StripeCheckoutSessionResult:
        checkout_metadata = {"workspace_id": str(workspace_id), **(metadata or {})}
        if amount_cents is not None:
            if amount_cents <= 0 or not offer_name:
                raise StripeAdapterError("Valor e nome efetivos são obrigatórios no checkout.")
            line_item = {
                "price_data": {
                    "currency": "brl",
                    "unit_amount": amount_cents,
                    "recurring": {"interval": "month"},
                    "product_data": {"name": offer_name},
                },
                "quantity": 1,
            }
        else:
            line_item = {"price": plan_price_id, "quantity": 1}
        body: dict[str, Any] = {
            "mode": "subscription",
            "line_items": [line_item],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": str(workspace_id),
            "metadata": checkout_metadata,
            "subscription_data": {"metadata": checkout_metadata},
        }
        if stripe_customer_id:
            body["customer"] = stripe_customer_id
        elif customer_email:
            body["customer_email"] = customer_email

        payload = await self._request(
            "POST",
            "/checkout/sessions",
            data=body,
            idempotency_key=idempotency_key or f"checkout_{workspace_id}_{plan_price_id}",
        )
        return StripeCheckoutSessionResult(
            session_id=str(payload.get("id")),
            url=str(payload.get("url") or ""),
            raw=payload,
        )

    async def create_billing_portal_session(
        self, *, customer_id: str, return_url: str
    ) -> StripeBillingPortalSessionResult:
        body: dict[str, Any] = {"customer": customer_id, "return_url": return_url}
        payload = await self._request("POST", "/billing_portal/sessions", data=body)
        return StripeBillingPortalSessionResult(
            session_id=str(payload.get("id")),
            url=str(payload.get("url") or ""),
            raw=payload,
        )

    def verify_webhook_signature(
        self, payload: bytes, sig_header: str, secret: str
    ) -> dict[str, Any]:
        """Verificação manual HMAC-SHA256 do header `stripe-signature`
        (`t=timestamp,v1=assinatura`), tolerância de 5 minutos, comparação em
        tempo constante. Levanta `StripeSignatureError` — nunca aceita
        payload não assinado ou expirado."""
        return verify_stripe_signature(payload, sig_header, secret)


__all__ = [
    "StripeAdapter",
    "StripeAdapterError",
    "StripeNotAllowedError",
    "StripeSignatureError",
    "StripeCheckoutSessionResult",
    "StripeBillingPortalSessionResult",
    "StripeGatewayPort",
]
