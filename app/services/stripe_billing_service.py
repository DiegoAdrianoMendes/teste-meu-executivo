"""Serviço: checkout e portal de billing Stripe da PLATAFORMA (F5).

Fail-closed: com o gate `external_delivery_allowed()` fechado, o
`StripeAdapter` levanta `StripeNotAllowedError` no choke point `_request`
antes de qualquer chamada de rede (ver `app/billing/stripe_adapter.py`).
`get_stripe_gateway` é importado no escopo do módulo para permitir
monkeypatch nos testes, no mesmo molde de `finance_charge_service.py` e
`app/payments/factory.get_gateway_for_workspace`.

Atualiza o `Subscription` de `app/models/billing.py` — o par Plan/Subscription
já espelha o Stripe (`stripe_customer_id`, `stripe_subscription_id`, status)
e já é a fonte que `app/api/v1/endpoints/stripe_webhook.py` sincroniza. Não
há modelo paralelo de assinatura criado aqui.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.factory import StripeCredentialError, get_stripe_gateway
from app.billing.stripe_adapter import (
    StripeAdapterError,
    StripeCheckoutSessionResult,
    StripeGatewayPort,
    StripeNotAllowedError,
)
from app.core.klm_runtime import EXTERNAL_ACTION_BLOCKED_CODE
from app.models.billing import BillingInterval, Plan, Subscription, SubscriptionStatus
from app.models.commercial_catalog import CommercialOffer
from app.models.user import User
from app.schemas.stripe_billing import (
    StripeBillingPortalSessionCreate,
    StripeBillingPortalSessionRead,
    StripeCheckoutSessionCreate,
    StripeCheckoutSessionRead,
)


def _stripe_blocked_response(action: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": EXTERNAL_ACTION_BLOCKED_CODE,
            "message": f"{action} bloqueado enquanto entregas externas estiverem desabilitadas.",
        },
    )


async def _resolve_plan_by_price_id(db: AsyncSession, plan_price_id: str) -> Plan:
    plan = await db.scalar(
        select(Plan).where(
            or_(
                Plan.stripe_price_monthly_id == plan_price_id,
                Plan.stripe_price_annually_id == plan_price_id,
            )
        )
    )
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "STRIPE-BILLING-404",
                "message": "Nenhum plano local corresponde a este price id do Stripe.",
            },
        )
    return plan


async def _get_or_create_local_subscription(
    db: AsyncSession, *, workspace_id: UUID, plan: Plan, interval: BillingInterval
) -> Subscription:
    """Garante uma linha `Subscription` local antes do checkout — o webhook
    (`_handle_checkout_completed`) só consegue vincular `stripe_customer_id`/
    `stripe_subscription_id` a uma assinatura já existente. Race-safe via
    SAVEPOINT, mesmo padrão de `LocalBillingService._get_or_create_subscription`."""
    existing = await db.scalar(
        select(Subscription).where(Subscription.workspace_id == workspace_id).with_for_update()
    )
    if existing is not None:
        if existing.plan_id != plan.id:
            existing.plan_id = plan.id
        existing.interval = interval
        return existing

    subscription = Subscription(
        workspace_id=workspace_id,
        plan_id=plan.id,
        status=SubscriptionStatus.trialing,
        interval=interval,
        amount_cents=0,
    )
    try:
        async with db.begin_nested():
            db.add(subscription)
            await db.flush()
    except IntegrityError:
        existing = await db.scalar(
            select(Subscription).where(Subscription.workspace_id == workspace_id).with_for_update()
        )
        if existing is None:
            raise
        return existing
    return subscription


async def create_checkout_session(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    actor_user_id: UUID,
    payload: StripeCheckoutSessionCreate,
) -> StripeCheckoutSessionRead:
    if await db.scalar(select(CommercialOffer.id).limit(1)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "STRIPE-BILLING-CANONICAL-409",
                "message": (
                    "Checkout automático ainda não está ligado ao contrato canônico. "
                    "Use “Quero uma demonstração”; nenhum estado local foi alterado."
                ),
            },
        )
    plan = await _resolve_plan_by_price_id(db, payload.plan_price_id)
    interval = (
        BillingInterval.annually
        if payload.plan_price_id == plan.stripe_price_annually_id
        else BillingInterval.monthly
    )
    # Do not materialize local billing state before the external delivery gate
    # succeeds. Besides keeping the fail-closed contract honest, this avoids a
    # released SQLite savepoint surviving a later rollback in local/test mode.
    subscription = await db.scalar(
        select(Subscription).where(Subscription.workspace_id == workspace_id)
    )
    customer_email = await db.scalar(select(User.email).where(User.id == actor_user_id))

    try:
        gateway: StripeGatewayPort = get_stripe_gateway()
        result: StripeCheckoutSessionResult = await gateway.create_checkout_session(
            workspace_id=workspace_id,
            plan_price_id=payload.plan_price_id,
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
            customer_email=customer_email,
            stripe_customer_id=(subscription.stripe_customer_id if subscription else None),
        )
    except StripeNotAllowedError:
        await db.rollback()
        raise _stripe_blocked_response("Checkout Stripe") from None
    except (StripeCredentialError, StripeAdapterError) as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "STRIPE-BILLING-503", "message": str(error)},
        ) from error

    if subscription is None:
        subscription = await _get_or_create_local_subscription(
            db, workspace_id=workspace_id, plan=plan, interval=interval
        )
        await db.flush()
    await db.commit()
    return StripeCheckoutSessionRead(session_id=result.session_id, url=result.url)


async def create_billing_portal_session(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    payload: StripeBillingPortalSessionCreate,
) -> StripeBillingPortalSessionRead:
    subscription = await db.scalar(
        select(Subscription).where(Subscription.workspace_id == workspace_id)
    )
    if subscription is None or not subscription.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "STRIPE-BILLING-404",
                "message": "Workspace ainda não tem customer Stripe (nenhum checkout concluído).",
            },
        )

    try:
        gateway: StripeGatewayPort = get_stripe_gateway()
        result = await gateway.create_billing_portal_session(
            customer_id=subscription.stripe_customer_id,
            return_url=payload.return_url,
        )
    except StripeNotAllowedError:
        raise _stripe_blocked_response("Portal de billing Stripe") from None
    except (StripeCredentialError, StripeAdapterError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "STRIPE-BILLING-503", "message": str(error)},
        ) from error

    return StripeBillingPortalSessionRead(session_id=result.session_id, url=result.url)
