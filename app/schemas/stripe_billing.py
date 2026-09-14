"""Contratos dos endpoints de checkout/portal Stripe da plataforma."""
from __future__ import annotations

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema


class StripeCheckoutSessionCreate(WriteSchema):
    plan_price_id: str = Field(min_length=1, max_length=120)
    success_url: str = Field(min_length=1, max_length=2000)
    cancel_url: str = Field(min_length=1, max_length=2000)


class StripeCheckoutSessionRead(CamelCaseSchema):
    session_id: str
    url: str


class StripeBillingPortalSessionCreate(WriteSchema):
    return_url: str = Field(min_length=1, max_length=2000)


class StripeBillingPortalSessionRead(CamelCaseSchema):
    session_id: str
    url: str
