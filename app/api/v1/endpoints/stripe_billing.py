"""
Endpoints: Checkout e portal de billing Stripe da PLATAFORMA (F5).

Rotas:
  POST /api/v1/billing/checkout-session
  POST /api/v1/billing/portal-session

Distinto de `app/api/v1/endpoints/asaas_webhook.py`/`app/payments` (gateway
Asaas usado pelos escritórios-cliente para cobrar os clientes deles): aqui o
"cliente" é o próprio workspace do KLM Hub pagando a assinatura da
plataforma. Ambos exigem admin do workspace (`require_workspace_admin`).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_workspace_admin
from app.schemas.stripe_billing import (
    StripeBillingPortalSessionCreate,
    StripeBillingPortalSessionRead,
    StripeCheckoutSessionCreate,
    StripeCheckoutSessionRead,
)
from app.services import stripe_billing_service

router = APIRouter(prefix="/billing", tags=["Billing Stripe"])


@router.post("/checkout-session", response_model=StripeCheckoutSessionRead)
async def create_checkout_session(
    payload: StripeCheckoutSessionCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> StripeCheckoutSessionRead:
    return await stripe_billing_service.create_checkout_session(
        db,
        workspace_id=workspace_id,
        actor_user_id=context.user_id,
        payload=payload,
    )


@router.post("/portal-session", response_model=StripeBillingPortalSessionRead)
async def create_billing_portal_session(
    payload: StripeBillingPortalSessionCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> StripeBillingPortalSessionRead:
    return await stripe_billing_service.create_billing_portal_session(
        db,
        workspace_id=workspace_id,
        payload=payload,
    )
