"""Contracts for tenant-aware AI usage and token-budget controls."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.base import CamelCaseSchema, WriteSchema

AiCostAlertLevel = Literal["none", "ok", "warning", "critical", "blocked"]
AiQuotaState = Literal["configured", "local_legacy", "missing", "not_applicable"]


class AiCostModelBreakdownRead(CamelCaseSchema):
    model: str
    provider: str
    runs: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)


class AiTokenModelBreakdownRead(CamelCaseSchema):
    """Tenant-safe breakdown: token telemetry without internal provider cost."""

    model: str
    provider: str
    runs: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class AiCostTaskBreakdownRead(CamelCaseSchema):
    task_class: str
    runs: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)


class AiTokenTaskBreakdownRead(CamelCaseSchema):
    """Tenant-safe task telemetry without model pricing."""

    task_class: str
    runs: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class AiCostRuntimeGatesRead(CamelCaseSchema):
    model_apis_enabled: bool
    online_approved: bool
    external_actions_enabled: bool


class WorkspaceAiCostSummaryRead(CamelCaseSchema):
    workspace_id: UUID | None
    period_start: datetime
    period_end: datetime
    total_runs: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    monthly_limit: int | None
    policy_monthly_limit: int | None
    alert_at_pct: int = Field(ge=1, le=100)
    hard_limit: bool
    used_percent: float | None
    remaining_tokens: int | None
    alert_level: AiCostAlertLevel
    quota_state: AiQuotaState
    by_model: list[AiCostModelBreakdownRead] = Field(default_factory=list)
    by_task: list[AiCostTaskBreakdownRead] = Field(default_factory=list)
    gates: AiCostRuntimeGatesRead


class WorkspaceAiTokenSummaryRead(CamelCaseSchema):
    """Usage and quota exposed to workspace owners and administrators."""

    workspace_id: UUID | None
    period_start: datetime
    period_end: datetime
    total_runs: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    monthly_limit: int | None
    policy_monthly_limit: int | None
    alert_at_pct: int = Field(ge=1, le=100)
    hard_limit: bool
    used_percent: float | None
    remaining_tokens: int | None
    alert_level: AiCostAlertLevel
    quota_state: AiQuotaState
    by_model: list[AiTokenModelBreakdownRead] = Field(default_factory=list)
    by_task: list[AiTokenTaskBreakdownRead] = Field(default_factory=list)
    gates: AiCostRuntimeGatesRead


class WorkspaceAiCostPolicyUpdate(WriteSchema):
    monthly_limit: int | None = Field(
        default=None,
        ge=0,
        le=2_147_483_647,
        strict=True,
    )
    alert_at_pct: int | None = Field(default=None, ge=1, le=100, strict=True)
    hard_limit: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def validate_patch(self) -> WorkspaceAiCostPolicyUpdate:
        if not self.model_fields_set:
            raise ValueError("Informe ao menos uma alteração no limite de tokens de IA.")
        if "alert_at_pct" in self.model_fields_set and self.alert_at_pct is None:
            raise ValueError("alertAtPct não pode ser nulo.")
        if "hard_limit" in self.model_fields_set and self.hard_limit is None:
            raise ValueError("hardLimit não pode ser nulo.")
        return self


AiTokenPackageRequestStatus = Literal["requested", "approved", "rejected", "revoked"]


class AiTokenPackageRead(CamelCaseSchema):
    id: UUID
    key: str
    name: str
    description: str | None
    token_quantity: int = Field(gt=0)
    commercial_price_cents: int | None = Field(default=None, ge=0)
    currency: str
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class AiTokenPackageUpsert(WriteSchema):
    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    token_quantity: int = Field(gt=0, le=9_000_000_000_000, strict=True)
    commercial_price_cents: int | None = Field(default=None, ge=0, le=2_147_483_647, strict=True)
    currency: str = Field(default="BRL", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=1_000_000, strict=True)


class WorkspaceAiTokenPackageRequestCreate(WriteSchema):
    package_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    request_note: str | None = Field(default=None, max_length=2000)


class WorkspaceAiTokenPackageRequestRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    package_id: UUID
    requested_by_user_id: UUID
    decided_by_user_id: UUID | None
    status: AiTokenPackageRequestStatus
    package_key_snapshot: str
    package_name_snapshot: str
    token_quantity_snapshot: int = Field(gt=0)
    commercial_price_cents_snapshot: int | None = Field(default=None, ge=0)
    currency_snapshot: str
    period_start: date
    period_end: date
    request_note: str | None
    decision_note: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value):
        return getattr(value, "value", value)


class WorkspaceAiTokenPackageRequestPageRead(CamelCaseSchema):
    items: list[WorkspaceAiTokenPackageRequestRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class AiTokenPackageDecision(WriteSchema):
    decision: Literal["approved", "rejected"]
    decision_note: str = Field(min_length=8, max_length=500)

    @field_validator("decision_note")
    @classmethod
    def strip_decision_note(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 8:
            raise ValueError("O motivo precisa ter ao menos 8 caracteres úteis.")
        return stripped


class AiTokenPackageRevoke(WriteSchema):
    decision_note: str = Field(min_length=8, max_length=500)

    @field_validator("decision_note")
    @classmethod
    def strip_decision_note(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 8:
            raise ValueError("O motivo precisa ter ao menos 8 caracteres úteis.")
        return stripped


class AiTokenPackageCatalogResponse(CamelCaseSchema):
    packages: list[AiTokenPackageRead] = Field(default_factory=list)
    requests: list[WorkspaceAiTokenPackageRequestRead] = Field(default_factory=list)
    support_release_required: Literal[True] = True
    self_activation_enabled: Literal[False] = False


__all__ = [
    "AiCostAlertLevel",
    "AiCostModelBreakdownRead",
    "AiCostTaskBreakdownRead",
    "AiQuotaState",
    "AiTokenModelBreakdownRead",
    "AiTokenTaskBreakdownRead",
    "AiCostRuntimeGatesRead",
    "AiTokenPackageCatalogResponse",
    "AiTokenPackageDecision",
    "AiTokenPackageRead",
    "AiTokenPackageRevoke",
    "AiTokenPackageRequestStatus",
    "AiTokenPackageUpsert",
    "WorkspaceAiTokenPackageRequestCreate",
    "WorkspaceAiTokenPackageRequestPageRead",
    "WorkspaceAiTokenPackageRequestRead",
    "WorkspaceAiCostPolicyUpdate",
    "WorkspaceAiCostSummaryRead",
    "WorkspaceAiTokenSummaryRead",
]
