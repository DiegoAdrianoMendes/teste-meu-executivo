"""Gateway Stripe determinístico para teste e dev local — nunca toca a rede.

Molde de `app/payments/fake_adapter.py`. Sessões com IDs previsíveis
(contador interno). `sign_event` deixa os testes assinarem um payload de
webhook sintético com a mesma lógica HMAC do adapter real, sem depender do
Stripe de verdade.
"""
from __future__ import annotations

import itertools
import time
from typing import Any, Optional
from uuid import UUID

from app.billing._signature import compute_stripe_signature, verify_stripe_signature
from app.billing.stripe_adapter import (
    StripeBillingPortalSessionResult,
    StripeCheckoutSessionResult,
)


class FakeStripeAdapter:
    """Implementação em memória do `StripeGatewayPort`."""

    def __init__(self) -> None:
        self._session_ids = itertools.count(1)
        self._portal_ids = itertools.count(1)
        self.created_checkout_sessions: list[dict[str, Any]] = []
        self.created_portal_sessions: list[dict[str, Any]] = []

    async def create_checkout_session(
        self,
        *,
        workspace_id: UUID,
        plan_price_id: str,
        success_url: str,
        cancel_url: str,
        customer_email: Optional[str] = None,
        stripe_customer_id: Optional[str] = None,
    ) -> StripeCheckoutSessionResult:
        self.created_checkout_sessions.append(
            {
                "workspace_id": str(workspace_id),
                "plan_price_id": plan_price_id,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "customer_email": customer_email,
                "stripe_customer_id": stripe_customer_id,
            }
        )
        session_id = f"cs_fake_{next(self._session_ids):06d}"
        return StripeCheckoutSessionResult(
            session_id=session_id,
            url=f"https://checkout.fake-stripe.local/pay/{session_id}",
            raw={"id": session_id, "object": "checkout.session"},
        )

    async def create_billing_portal_session(
        self, *, customer_id: str, return_url: str
    ) -> StripeBillingPortalSessionResult:
        self.created_portal_sessions.append(
            {"customer_id": customer_id, "return_url": return_url}
        )
        portal_id = f"bps_fake_{next(self._portal_ids):06d}"
        return StripeBillingPortalSessionResult(
            session_id=portal_id,
            url=f"https://billing.fake-stripe.local/p/{portal_id}",
            raw={"id": portal_id, "object": "billing_portal.session"},
        )

    def verify_webhook_signature(
        self, payload: bytes, sig_header: str, secret: str
    ) -> dict[str, Any]:
        return verify_stripe_signature(payload, sig_header, secret)

    @staticmethod
    def sign_event(payload: bytes, secret: str, *, timestamp: Optional[int] = None) -> str:
        """Gera o header `stripe-signature` sintético para um payload de
        teste, com a mesma fórmula HMAC-SHA256 usada pelo Stripe real."""
        ts = int(time.time()) if timestamp is None else timestamp
        signature = compute_stripe_signature(payload, secret, ts)
        return f"t={ts},v1={signature}"
