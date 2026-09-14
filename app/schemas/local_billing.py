"""Typed contracts for the local-only SaaS billing simulator."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field

from app.models.billing import BillingInterval, PlanTier, SubscriptionStatus
from app.models.entitlement import EntitlementKey
from app.models.klm_operations import ExternalActionIntentStatus
from app.schemas.base import CamelCaseSchema, WriteSchema
from app.schemas.subscription_access import SubscriptionAccessDecision

LocalBillingExportScope = Annotated[str, Field(min_length=1, max_length=80)]


class LocalPlanEntitlementWrite(WriteSchema):
    entitlement: EntitlementKey
    enabled: bool = True
    limit_quantity: int | None = Field(default=None, ge=0)
    hard_limit: bool = True


class LocalPlanEntitlementRead(CamelCaseSchema):
    entitlement: EntitlementKey
    enabled: bool
    limit_quantity: int | None = None
    hard_limit: bool
    source: str = "plan_entitlement"
    configured: bool = True


class LocalPlanCatalogUpdate(WriteSchema):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    price_monthly_cents: int | None = Field(default=None, ge=0)
    price_annually_cents: int | None = Field(default=None, ge=0)
    max_matters: int | None = Field(default=None, ge=0)
    is_active: bool | None = None
    is_public: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)
    entitlements: list[LocalPlanEntitlementWrite] | None = None


class LocalPlanCatalogItemRead(CamelCaseSchema):
    id: UUID
    name: str
    tier: PlanTier
    description: str | None = None
    price_monthly_cents: int
    price_annually_cents: int
    currency: str = "BRL"
    max_matters: int | None = None
    is_active: bool
    is_public: bool
    sort_order: int
    entitlements: list[LocalPlanEntitlementRead]


class LocalBillingQuoteRequest(WriteSchema):
    plan_tier: PlanTier
    interval: BillingInterval = BillingInterval.monthly


class LocalBillingQuoteRead(CamelCaseSchema):
    plan: LocalPlanCatalogItemRead
    interval: BillingInterval
    amount_cents: int
    currency: str = "BRL"
    simulated: bool = True
    external_effects: int = 0


class LocalBillingIdempotentRequest(WriteSchema):
    idempotency_key: str = Field(min_length=1, max_length=160)
    effective_at: datetime | None = None


class LocalBillingTrialRequest(LocalBillingIdempotentRequest):
    plan_tier: PlanTier = PlanTier.pro
    trial_days: int = Field(default=14, ge=1, le=90)


class LocalBillingCheckoutRequest(LocalBillingIdempotentRequest):
    plan_tier: PlanTier
    interval: BillingInterval = BillingInterval.monthly


class LocalBillingPlanChangeRequest(LocalBillingIdempotentRequest):
    plan_tier: PlanTier
    interval: BillingInterval | None = None


class LocalBillingDunningRequest(LocalBillingIdempotentRequest):
    grace_days: int = Field(default=7, ge=1, le=30)


class LocalBillingLifecycleRequest(LocalBillingIdempotentRequest):
    reason: str | None = Field(default=None, max_length=240)


class LocalBillingCancellationMode(enum.StrEnum):
    period_end = "period_end"
    immediate = "immediate"


class LocalBillingCancellationRequest(LocalBillingIdempotentRequest):
    mode: LocalBillingCancellationMode = LocalBillingCancellationMode.period_end
    reason: str | None = Field(default=None, max_length=240)


class LocalBillingExportIntentRequest(LocalBillingIdempotentRequest):
    export_format: Literal["json", "csv", "zip"] = "zip"
    scopes: list[LocalBillingExportScope] = Field(
        default_factory=lambda: ["workspace"],
        min_length=1,
        max_length=20,
    )
    reason: str | None = Field(default=None, max_length=240)


class LocalBillingClosureIntentRequest(LocalBillingIdempotentRequest):
    confirm_closure: bool
    reason: str = Field(min_length=1, max_length=240)


class LocalBillingIntentRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    requested_by_user_id: UUID | None = None
    action_type: str
    provider: str | None = None
    idempotency_key: str
    status: ExternalActionIntentStatus
    payload: dict[str, Any]
    reason: str
    created_at: datetime


class LocalBillingSubscriptionRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    plan_id: UUID
    plan_tier: PlanTier
    plan_name: str
    status: SubscriptionStatus
    interval: BillingInterval
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    trial_end: datetime | None = None
    cancelled_at: datetime | None = None
    cancel_at_period_end: bool = False
    amount_cents: int
    currency: str


class LocalBillingPortalRead(CamelCaseSchema):
    workspace_id: UUID
    subscription: LocalBillingSubscriptionRead | None = None
    access: SubscriptionAccessDecision
    entitlements: list[LocalPlanEntitlementRead] = Field(default_factory=list)
    recent_intents: list[LocalBillingIntentRead] = Field(default_factory=list)
    simulated: bool = True
    external_effects: int = 0


class LocalBillingCommandResult(CamelCaseSchema):
    action: str
    replayed: bool
    intent: LocalBillingIntentRead
    portal: LocalBillingPortalRead
