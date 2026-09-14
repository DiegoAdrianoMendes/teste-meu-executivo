"""Contrato comum dos provedores de assinatura da plataforma."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from app.models.commercial_catalog import CommercialProvider


class SaaSBillingGatewayError(RuntimeError):
    """Falha segura ao preparar ou administrar uma assinatura SaaS."""


class SaaSBillingCredentialError(SaaSBillingGatewayError):
    """Credencial global do provedor ausente."""


@dataclass(frozen=True)
class SaaSCheckoutResult:
    session_id: str
    url: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SaaSPortalResult:
    session_id: str
    url: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SaaSSubscriptionMutationResult:
    subscription_id: str
    provider_status: str
    raw: dict[str, Any] = field(default_factory=dict)


class SaaSBillingGatewayPort(Protocol):
    provider: CommercialProvider
    supports_customer_portal: bool

    async def create_checkout_session(
        self,
        *,
        workspace_id: UUID,
        provider_price_id: str,
        amount_cents: int,
        offer_name: str,
        success_url: str,
        cancel_url: str,
        customer_email: str | None,
        notification_url: str | None = None,
        provider_customer_id: str | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> SaaSCheckoutResult: ...

    async def create_billing_portal_session(
        self, *, customer_id: str, return_url: str
    ) -> SaaSPortalResult: ...

    async def update_subscription_amount(
        self,
        *,
        subscription_id: str,
        amount_cents: int,
        idempotency_key: str,
    ) -> SaaSSubscriptionMutationResult: ...

    async def cancel_subscription(
        self,
        *,
        subscription_id: str,
        idempotency_key: str,
    ) -> SaaSSubscriptionMutationResult: ...
