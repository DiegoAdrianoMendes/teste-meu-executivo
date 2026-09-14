"""Fábrica dos gateways usados para cobrar a assinatura do Meu Executivo.

Não confundir com ``app.payments``: aquele módulo usa a credencial Asaas de
cada escritório para a operação financeira do próprio cliente. Aqui as
credenciais são globais da plataforma e só cobram o contrato SaaS.
"""
from __future__ import annotations

from app.billing.asaas_adapter import AsaasSaaSBillingAdapter
from app.billing.mercado_pago_adapter import MercadoPagoSaaSBillingAdapter
from app.billing.port import (
    SaaSBillingCredentialError,
    SaaSBillingGatewayPort,
    SaaSCheckoutResult,
    SaaSPortalResult,
    SaaSSubscriptionMutationResult,
)
from app.billing.stripe_adapter import StripeAdapter, StripeGatewayPort
from app.core.config import settings
from app.models.commercial_catalog import CommercialProvider


class StripeCredentialError(SaaSBillingCredentialError):
    """Compatibilidade com os consumidores legados do gateway Stripe."""


def get_stripe_gateway() -> StripeGatewayPort:
    secret_key = settings.STRIPE_SECRET_KEY
    if not secret_key:
        raise StripeCredentialError(
            "STRIPE_SECRET_KEY não configurada — impossível iniciar checkout/portal Stripe."
        )
    return StripeAdapter(secret_key=secret_key)


class _StripeSaaSGateway:
    provider = CommercialProvider.stripe
    supports_customer_portal = True

    def __init__(self, adapter: StripeGatewayPort) -> None:
        self.adapter = adapter

    async def create_checkout_session(self, **kwargs) -> SaaSCheckoutResult:
        result = await self.adapter.create_checkout_session(
            workspace_id=kwargs["workspace_id"],
            plan_price_id=kwargs["provider_price_id"],
            amount_cents=kwargs["amount_cents"],
            offer_name=kwargs["offer_name"],
            success_url=kwargs["success_url"],
            cancel_url=kwargs["cancel_url"],
            customer_email=kwargs.get("customer_email"),
            stripe_customer_id=kwargs.get("provider_customer_id"),
            metadata=kwargs.get("metadata"),
            idempotency_key=kwargs.get("idempotency_key"),
        )
        return SaaSCheckoutResult(result.session_id, result.url, result.raw)

    async def create_billing_portal_session(
        self, *, customer_id: str, return_url: str
    ) -> SaaSPortalResult:
        result = await self.adapter.create_billing_portal_session(
            customer_id=customer_id, return_url=return_url
        )
        return SaaSPortalResult(result.session_id, result.url, result.raw)

    async def update_subscription_amount(
        self, *, subscription_id: str, amount_cents: int, idempotency_key: str
    ) -> SaaSSubscriptionMutationResult:
        del subscription_id, amount_cents, idempotency_key
        raise SaaSBillingCredentialError(
            "A assinatura Stripe deve ser alterada no portal hospedado."
        )

    async def cancel_subscription(
        self, *, subscription_id: str, idempotency_key: str
    ) -> SaaSSubscriptionMutationResult:
        del subscription_id, idempotency_key
        raise SaaSBillingCredentialError(
            "A assinatura Stripe deve ser cancelada no portal hospedado."
        )


def get_saas_billing_gateway(provider: CommercialProvider | str) -> SaaSBillingGatewayPort:
    provider = CommercialProvider(provider)
    if provider == CommercialProvider.stripe:
        return _StripeSaaSGateway(get_stripe_gateway())
    if provider == CommercialProvider.asaas:
        if not settings.SAAS_ASAAS_ACCESS_TOKEN.strip():
            raise SaaSBillingCredentialError("Asaas SaaS não configurado neste ambiente.")
        return AsaasSaaSBillingAdapter(
            access_token=settings.SAAS_ASAAS_ACCESS_TOKEN,
            sandbox=settings.SAAS_ASAAS_SANDBOX,
        )
    if provider == CommercialProvider.mercado_pago:
        if not settings.MERCADO_PAGO_ACCESS_TOKEN.strip():
            raise SaaSBillingCredentialError("Mercado Pago não configurado neste ambiente.")
        return MercadoPagoSaaSBillingAdapter(
            access_token=settings.MERCADO_PAGO_ACCESS_TOKEN
        )
    raise SaaSBillingCredentialError("Provedor de assinatura SaaS não suportado.")


__all__ = [
    "StripeCredentialError",
    "get_stripe_gateway",
    "get_saas_billing_gateway",
]
