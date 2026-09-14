"""Typed contracts for dimensional finance commands."""

from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import CamelCaseSchema, WriteSchema


class CostCenterWrite(WriteSchema):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=500)
    active: bool = True
    parent_id: UUID | None = None
    type: str = Field(default="operational", min_length=1, max_length=40)
    owner_user_id: UUID | None = None
    status: Literal["active", "inactive", "archived"] = "active"


class CostCenterRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    code: str
    name: str
    description: str | None = None
    active: bool
    version: int
    parent_id: UUID | None = None
    type: str
    owner_user_id: UUID | None = None
    status: str


class CostCenterUpdate(WriteSchema):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=500)
    parent_id: UUID | None = None
    type: str | None = Field(default=None, min_length=1, max_length=40)
    owner_user_id: UUID | None = None
    status: Literal["active", "inactive", "archived"] | None = None
    expected_version: int = Field(ge=1)


class CostCenterImpactPreview(WriteSchema):
    patch: dict[str, Any] = Field(default_factory=dict)
    expected_version: int = Field(ge=1)


class FinanceLookupResolve(WriteSchema):
    entity_type: Literal["entry", "client", "matter", "contract", "team", "user"]
    ids: list[UUID] = Field(min_length=1, max_length=100)


class AllocationWrite(WriteSchema):
    finance_entry_id: UUID
    cost_center_id: UUID
    percentage: Decimal = Field(gt=0, le=100, max_digits=7, decimal_places=4)
    amount: Decimal | None = Field(default=None, max_digits=15, decimal_places=2)
    audit_metadata: dict[str, Any] = Field(default_factory=dict)
    client_id: UUID | None = None
    contract_id: UUID | None = None
    matter_id: UUID | None = None
    team_id: UUID | None = None
    user_id: UUID | None = None
    source: Literal["manual", "rule", "import", "reconciliation"] = "manual"


class AllocationRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    finance_entry_id: UUID
    cost_center_id: UUID
    percentage: Decimal
    amount: Decimal | None = None
    version: int
    audit_metadata: dict[str, Any]
    client_id: UUID | None = None
    contract_id: UUID | None = None
    matter_id: UUID | None = None
    team_id: UUID | None = None
    user_id: UUID | None = None
    source: str


class ForecastWrite(WriteSchema):
    period_start: date
    period_end: date
    revenue: Decimal = Decimal("0")
    expense: Decimal = Decimal("0")
    assumptions: dict[str, Any] = Field(default_factory=dict)
    scenario: Literal["conservative", "base", "optimistic"] = "base"
    horizon: Literal["30d", "90d", "180d", "365d", "3y"] = "90d"

    @model_validator(mode="after")
    def valid_period(self):
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        return self


class ForecastRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    period_start: date
    period_end: date
    revenue: Decimal
    expense: Decimal
    assumptions: dict[str, Any]
    scenario: str
    horizon: str
    version: int


class ForecastGenerateWrite(WriteSchema):
    as_of: date
    horizon: Literal["30d", "90d", "180d", "365d", "3y"] = "90d"
    scenarios: list[Literal["conservative", "base", "optimistic"]] = Field(
        default_factory=lambda: ["conservative", "base", "optimistic"], min_length=1
    )


class ValuationWrite(WriteSchema):
    method: Literal["dcf_simplified", "revenue_multiple", "ebitda_multiple"]
    valuation_date: date
    enterprise_value: Decimal = Field(ge=0)
    assumptions: dict[str, Any] = Field(min_length=1)
    premise_quality: Literal["verified", "partial", "unverified"] = "unverified"


class ValuationRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    method: str
    valuation_date: date
    enterprise_value: Decimal
    assumptions: dict[str, Any]
    premise_quality: str
    version: int


class ValuationCalculateWrite(WriteSchema):
    method: Literal["dcf_simplified", "revenue_multiple", "ebitda_multiple"]
    as_of: date
    assumptions: dict[str, Any] = Field(min_length=1)


class TaxProfileWrite(WriteSchema):
    regime: Literal["simples_anexo_iv", "lucro_presumido", "lucro_real", "other"]
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    effective_from: date
    municipality: str | None = Field(default=None, max_length=120)
    sources: list[dict[str, Any]] = Field(min_length=1)
    reform_calendar: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    expected_version: int | None = Field(default=None, ge=1)


class TaxProfileRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    regime: str
    tax_rate: Decimal
    effective_from: date
    effective_until: date | None = None
    municipality: str | None = None
    sources: list[dict[str, Any]]
    reform_calendar: dict[str, Any]
    settings: dict[str, Any]
    version: int


class FinanceAnalyticsQuery(WriteSchema):
    cost_center_id: UUID | None = None
    client_id: UUID | None = None
    contract_id: UUID | None = None
    matter_id: UUID | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class FinanceDreQuery(WriteSchema):
    mode: Literal["cash", "accrual"] = "cash"
    period_start: date
    period_end: date
    cost_center_id: UUID | None = None
    client_id: UUID | None = None
    contract_id: UUID | None = None
    matter_id: UUID | None = None
    granularity: Literal["monthly", "annual"] | None = None
    accumulated: bool = False
    compare_previous: bool = False

    @model_validator(mode="after")
    def valid_period(self):
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        return self


class TaxReformSimulationWrite(WriteSchema):
    annual_revenue: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    annual_expense: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    current_effective_rate: Decimal | None = Field(default=None, ge=0, le=100)
    transition_rates: dict[str, Decimal] = Field(default_factory=dict)
    price_pass_through_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)

    @model_validator(mode="after")
    def valid_transition_rates(self):
        for year, rate in self.transition_rates.items():
            if not str(year).isdigit() or not Decimal("0") <= rate <= Decimal("100"):
                raise ValueError("transition_rates must map years to percentages from 0 to 100")
        return self


class TaxReformReviewRequest(WriteSchema):
    request_key: str = Field(min_length=8, max_length=180, pattern=r"^[A-Za-z0-9._:/-]+$")
    reason: str = Field(min_length=8, max_length=500)
    simulation: TaxReformSimulationWrite
    sla_minutes: int = Field(default=240, ge=1, le=10080)


class BudgetLineWrite(WriteSchema):
    period_start: date
    period_end: date
    cost_center_id: UUID | None = None
    category: str | None = Field(default=None, max_length=80)
    entry_type: Literal["receivable", "expense", "refund"]
    amount: Decimal = Field(ge=0, max_digits=15, decimal_places=2)
    assumptions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_period(self):
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        return self


class BudgetLineRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    period_start: date
    period_end: date
    cost_center_id: UUID | None = None
    category: str | None = None
    entry_type: str
    amount: Decimal
    assumptions: dict[str, Any]
    status: str
    version: int
