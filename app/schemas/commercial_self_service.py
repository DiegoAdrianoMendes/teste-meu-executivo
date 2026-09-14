"""Public catalog and tenant commercial self-service contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.schemas.base import CamelCaseSchema, WriteSchema
from app.schemas.commercial_catalog import (
    WorkspaceContractRead,
    WorkspaceContractRevisionPreviewRead,
)


class PublicCommercialOfferRead(CamelCaseSchema):
    sku: str
    name: str
    description: str | None
    family: Literal["crm", "legal", "internal"]
    kind: Literal["base_plan", "solution"]
    is_recommended: bool
    is_quote_only: bool
    price_monthly_cents: int | None
    currency: Literal["BRL"]
    cta: str
    highlights: list[str]


class PublicCommercialCatalogRead(CamelCaseSchema):
    items: list[PublicCommercialOfferRead]
    generated_at: datetime
    state: Literal["available", "not_published"]


class CommercialAccountRequestCreate(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    request_type: Literal["change_base_plan", "activate_legal", "cancel_legal", "cancel_contract"]
    target_sku: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{2,79}$")
    expected_contract_version: int = Field(ge=1)
    reason: str = Field(min_length=8, max_length=500)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        return value.strip()


class CommercialAccountRequestRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    requested_by_user_id: UUID | None
    request_type: str
    target_sku: str | None
    reason: str
    status: str
    contract_version: int
    version: int
    created_at: datetime
    resolved_at: datetime | None
    resolution_note: str | None


class CommercialAccountSummaryRead(CamelCaseSchema):
    contract: WorkspaceContractRead
    requests: list[CommercialAccountRequestRead]
    automatic_checkout_available: bool
    billing_portal_available: bool
    provider_subscription_linked: bool
    provider_subscription_status: str | None
    provider_state: Literal["not_configured", "gate_closed", "partially_bound", "configured"]
    provider_message: str
    active_provider: Literal["stripe", "asaas", "mercado_pago"] | None = None
    available_providers: list[Literal["stripe", "asaas", "mercado_pago"]] = Field(
        default_factory=list
    )


class CommercialCheckoutSessionCreate(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    target_sku: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    expected_contract_version: int = Field(ge=1)
    reason: str = Field(min_length=8, max_length=500)
    provider: Literal["stripe", "asaas", "mercado_pago"] | None = None

    @field_validator("reason")
    @classmethod
    def strip_checkout_reason(cls, value: str) -> str:
        return value.strip()


class CommercialCheckoutSessionRead(CamelCaseSchema):
    intent_id: UUID
    status: Literal["session_created"]
    checkout_url: str
    provider: Literal["stripe", "asaas", "mercado_pago"]


class CommercialBillingPortalRead(CamelCaseSchema):
    portal_url: str


class CommercialWorkspaceProviderStateRead(CamelCaseSchema):
    provider: Literal["stripe", "asaas", "mercado_pago"] | None = None
    provider_state: Literal["not_configured", "gate_closed", "partially_bound", "configured"]
    provider_message: str
    customer_linked: bool
    subscription_linked: bool
    billing_portal_available: bool
    provider_status: str | None


class CommercialProviderPriceBindingWrite(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    provider: Literal["stripe", "asaas", "mercado_pago"] = "stripe"
    provider_price_id: str | None = Field(default=None, min_length=3, max_length=200)
    billing_interval: Literal["monthly"] = "monthly"
    reason: str = Field(min_length=8, max_length=500)

    @field_validator("provider_price_id", "reason")
    @classmethod
    def strip_binding_values(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_stripe_price(self):
        if self.provider == "stripe" and not self.provider_price_id:
            raise ValueError("O Price ID publicado pelo Stripe é obrigatório.")
        return self


class CommercialProviderPriceBindingRead(CamelCaseSchema):
    id: UUID
    sku: str
    offer_version: int
    provider: Literal["stripe", "asaas", "mercado_pago"]
    billing_interval: Literal["monthly", "annual"]
    provider_price_id: str
    status: Literal["active", "inactive"]
    version: int
    updated_at: datetime


class CommercialProviderPriceBindingPage(CamelCaseSchema):
    items: list[CommercialProviderPriceBindingRead]


class CommercialAccountPreviewRead(CamelCaseSchema):
    request_type: str
    target_sku: str | None
    contract: WorkspaceContractRead
    revision: WorkspaceContractRevisionPreviewRead | None
    warnings: list[str]
    eligible: bool


class CommercialAccountRequestResolve(WriteSchema):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    status: Literal["resolved", "rejected", "cancelled"]
    note: str = Field(min_length=8, max_length=500)

    @field_validator("note")
    @classmethod
    def strip_note(cls, value: str) -> str:
        return value.strip()


class CommercialAccountRequestExecute(WriteSchema):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    note: str = Field(min_length=8, max_length=500)

    @field_validator("note")
    @classmethod
    def strip_note(cls, value: str) -> str:
        return value.strip()


class CommercialAccountRequestPage(CamelCaseSchema):
    items: list[CommercialAccountRequestRead]
    total: int
