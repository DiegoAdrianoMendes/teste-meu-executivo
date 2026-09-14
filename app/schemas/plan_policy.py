"""Strict API contracts for plan-policy editing, rollout, and resolution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.billing import SubscriptionStatus
from app.models.entitlement import EntitlementKey
from app.models.plan_policy import PlanPolicyRolloutMode
from app.models.workspace import WorkspaceSetupMode, WorkspaceStatus
from app.schemas.base import CamelCaseSchema, WriteSchema

FEATURE_ENTITLEMENTS = frozenset(
    {
        EntitlementKey.digital_signature,
        EntitlementKey.calendar,
        EntitlementKey.email,
        EntitlementKey.webhooks,
        EntitlementKey.advanced_audit,
        EntitlementKey.success_manager,
    }
)
QUANTITY_ENTITLEMENTS = frozenset(EntitlementKey) - FEATURE_ENTITLEMENTS


class _StrictWriteSchema(WriteSchema):
    model_config = ConfigDict(**WriteSchema.model_config, extra="forbid")


class _StrictReadSchema(CamelCaseSchema):
    model_config = ConfigDict(**CamelCaseSchema.model_config, extra="forbid")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Use data e hora com fuso horário explícito.")
    return value.astimezone(UTC)


def _clean_unique_strings(values: list[str], *, lowercase: bool) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if lowercase:
            item = item.lower()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


class PlanPolicyEntitlementSetting(_StrictWriteSchema):
    """One checkbox plus its optional numeric quota."""

    enabled: bool
    limit_quantity: int | None = Field(default=None, ge=0)
    hard_limit: bool = True


class PlanPolicyParameters(_StrictWriteSchema):
    max_matters: int | None = Field(default=None, ge=0)
    max_workspaces: int | None = Field(default=None, ge=0)
    max_files_per_conversation: int | None = Field(default=None, ge=0)
    max_operational_recipients: int | None = Field(default=None, ge=0)
    max_campaign_recipients_per_month: int | None = Field(default=None, ge=0)


class PlanPolicySnapshot(_StrictWriteSchema):
    """The complete commercial and enforcement state captured by a publication."""

    schema_version: Literal["1.0"] = "1.0"
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2_000)
    is_active: bool = True
    is_public: bool = True
    recommended: bool = False
    sort_order: int = Field(default=0, ge=0)
    currency: str = Field(default="BRL", min_length=3, max_length=3)
    price_monthly_cents: int = Field(default=0, ge=0)
    price_annually_cents: int = Field(default=0, ge=0)
    support_tier: str = Field(default="standard", min_length=1, max_length=40)
    integrations: list[str] = Field(default_factory=list, max_length=100)
    highlights: list[str] = Field(default_factory=list, max_length=100)
    parameters: PlanPolicyParameters = Field(default_factory=PlanPolicyParameters)
    entitlements: dict[EntitlementKey, PlanPolicyEntitlementSetting]

    @field_validator("name", "support_tier")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("O campo não pode ficar vazio.")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized.isalpha():
            raise ValueError("currency deve usar três letras.")
        return normalized

    @field_validator("integrations")
    @classmethod
    def normalize_integrations(cls, values: list[str]) -> list[str]:
        return _clean_unique_strings(values, lowercase=True)

    @field_validator("highlights")
    @classmethod
    def normalize_highlights(cls, values: list[str]) -> list[str]:
        normalized = _clean_unique_strings(values, lowercase=False)
        if any(len(item) > 240 for item in normalized):
            raise ValueError("Cada destaque deve ter no máximo 240 caracteres.")
        return normalized

    @model_validator(mode="after")
    def validate_complete_entitlement_snapshot(self) -> PlanPolicySnapshot:
        configured = set(self.entitlements)
        expected = set(EntitlementKey)
        if configured != expected:
            missing = sorted(item.value for item in expected - configured)
            extra = sorted(str(item) for item in configured - expected)
            raise ValueError(
                f"O snapshot deve conter todos os entitlements; missing={missing}, extra={extra}."
            )
        for key, setting in self.entitlements.items():
            if key in FEATURE_ENTITLEMENTS and setting.limit_quantity is not None:
                raise ValueError(f"{key.value} é checkbox e não aceita quantidade.")
            if not setting.enabled and setting.limit_quantity is not None:
                raise ValueError(f"{key.value} desabilitado não pode manter quantidade oculta.")
            if key is EntitlementKey.tokens and setting.enabled and setting.limit_quantity is None:
                raise ValueError("tokens habilitados exigem uma franquia mensal finita.")
        return self


class PlanPolicyDraftWrite(_StrictWriteSchema):
    snapshot: PlanPolicySnapshot


class PlanPolicyRolloutRule(_StrictWriteSchema):
    """A safe AND-composed cohort rule; no user-provided SQL or expressions."""

    workspace_ids: list[UUID] = Field(default_factory=list, max_length=1_000)
    exclude_workspace_ids: list[UUID] = Field(default_factory=list, max_length=1_000)
    workspace_statuses: list[WorkspaceStatus] = Field(default_factory=list)
    setup_modes: list[WorkspaceSetupMode] = Field(default_factory=list)
    subscription_statuses: list[SubscriptionStatus] = Field(default_factory=list)
    workspace_created_from: datetime | None = None
    workspace_created_before: datetime | None = None
    workspace_is_active: bool | None = None

    @field_validator("workspace_created_from", "workspace_created_before")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return _aware_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_rule(self) -> PlanPolicyRolloutRule:
        has_criterion = any(
            (
                self.workspace_ids,
                self.exclude_workspace_ids,
                self.workspace_statuses,
                self.setup_modes,
                self.subscription_statuses,
                self.workspace_created_from is not None,
                self.workspace_created_before is not None,
                self.workspace_is_active is not None,
            )
        )
        if not has_criterion:
            raise ValueError("A regra de rollout precisa de ao menos um critério.")
        if set(self.workspace_ids) & set(self.exclude_workspace_ids):
            raise ValueError("Um workspace não pode ser incluído e excluído na mesma regra.")
        if (
            self.workspace_created_from is not None
            and self.workspace_created_before is not None
            and self.workspace_created_from >= self.workspace_created_before
        ):
            raise ValueError("workspaceCreatedBefore deve ser posterior a workspaceCreatedFrom.")
        return self


class PlanPolicyRolloutRequest(_StrictWriteSchema):
    rollout_mode: PlanPolicyRolloutMode
    effective_at: datetime | None = None
    rule: PlanPolicyRolloutRule | None = None

    @field_validator("effective_at")
    @classmethod
    def normalize_effective_at(cls, value: datetime | None) -> datetime | None:
        return _aware_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_rollout_shape(self) -> PlanPolicyRolloutRequest:
        if self.rollout_mode is PlanPolicyRolloutMode.rule and self.rule is None:
            raise ValueError("rolloutMode=rule exige uma regra estruturada.")
        if self.rollout_mode is not PlanPolicyRolloutMode.rule and self.rule is not None:
            raise ValueError("A regra só é aceita quando rolloutMode=rule.")
        if self.rollout_mode is PlanPolicyRolloutMode.scheduled and self.effective_at is None:
            raise ValueError("rolloutMode=scheduled exige effectiveAt.")
        return self


class PlanPolicyPreviewRequest(PlanPolicyRolloutRequest):
    sample_limit: int = Field(default=25, ge=1, le=100)


class PlanPolicyPublishRequest(PlanPolicyRolloutRequest):
    snapshot: PlanPolicySnapshot | None = None
    draft_etag: str | None = Field(default=None, min_length=1, max_length=80)
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def validate_publication_source(self) -> PlanPolicyPublishRequest:
        if self.snapshot is None and self.draft_etag is None:
            raise ValueError("Informe snapshot ou draftEtag para publicar.")
        return self


class PlanPolicyVersionRead(_StrictReadSchema):
    id: UUID
    plan_id: UUID
    version: int
    schema_version: str
    snapshot: PlanPolicySnapshot
    snapshot_hash: str
    rollout_mode: PlanPolicyRolloutMode
    effective_at: datetime
    rollout_rule: PlanPolicyRolloutRule | None = None
    publication_reason: str
    published_by_user_id: UUID
    published_at: datetime


class PlanPolicyDraftRead(_StrictReadSchema):
    id: UUID
    plan_id: UUID
    revision: int
    etag: str
    snapshot: PlanPolicySnapshot
    updated_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class PlanPolicyEditorRead(_StrictReadSchema):
    plan_id: UUID
    plan_tier: str
    source: Literal["draft", "published", "legacy"]
    etag: str
    draft_revision: int | None = None
    published_version: int | None = None
    snapshot: PlanPolicySnapshot


class PlanPolicyPlanSummaryRead(_StrictReadSchema):
    plan_id: UUID
    plan_tier: str
    plan_name: str
    draft_revision: int | None = None
    draft_etag: str | None = None
    latest_version: int | None = None
    latest_effective_at: datetime | None = None


class PlanPolicyPlanPageRead(_StrictReadSchema):
    items: list[PlanPolicyPlanSummaryRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class PlanPolicyVersionPageRead(_StrictReadSchema):
    items: list[PlanPolicyVersionRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class PlanPolicyAccountPreviewRead(_StrictReadSchema):
    workspace_id: UUID
    name: str
    slug: str
    workspace_status: WorkspaceStatus
    subscription_status: SubscriptionStatus
    workspace_created_at: datetime
    current_policy_version: int | None = None


class PlanPolicyPreviewRead(_StrictReadSchema):
    plan_id: UUID
    rollout_mode: PlanPolicyRolloutMode
    effective_at: datetime
    rule: PlanPolicyRolloutRule | None = None
    affected_count: int
    applies_immediately: bool
    accounts: list[PlanPolicyAccountPreviewRead]


class PlanPolicyPublicationRead(_StrictReadSchema):
    policy: PlanPolicyVersionRead
    affected_count: int
    assignments_created: int
    idempotent_replay: bool = False


class ResolvedPlanEntitlementRead(_StrictReadSchema):
    key: EntitlementKey
    enabled: bool
    limit_quantity: int | None
    hard_limit: bool
    source: Literal["plan_policy_version", "legacy_plan_entitlement"]


class WorkspacePlanPolicyResolutionRead(_StrictReadSchema):
    workspace_id: UUID
    plan_id: UUID
    policy_version_id: UUID | None = None
    policy_version: int | None = None
    assignment_id: UUID | None = None
    snapshot_hash: str
    source: Literal["plan_policy_version", "legacy_plan_entitlement"]
    parameters: PlanPolicyParameters
    entitlements: dict[EntitlementKey, ResolvedPlanEntitlementRead]


class PlanPolicyAuditEventRead(_StrictReadSchema):
    id: UUID
    event_type: str
    plan_id: UUID
    policy_version_id: UUID | None = None
    workspace_id: UUID | None = None
    actor_user_id: UUID | None = None
    idempotency_key: str | None = None
    event_data: dict
    occurred_at: datetime


class PlanPolicyEntitlementCatalogItemRead(_StrictReadSchema):
    key: EntitlementKey
    control: Literal["checkbox", "checkbox_and_number"]
    supports_limit: bool
    requires_finite_limit: bool = False


class PlanPolicyMaterializationRead(_StrictReadSchema):
    plan_id: UUID
    evaluated_accounts: int
    assignments_created: int
    effective_at: datetime


__all__ = [
    "FEATURE_ENTITLEMENTS",
    "PlanPolicyAccountPreviewRead",
    "PlanPolicyAuditEventRead",
    "PlanPolicyDraftRead",
    "PlanPolicyDraftWrite",
    "PlanPolicyEditorRead",
    "PlanPolicyEntitlementCatalogItemRead",
    "PlanPolicyEntitlementSetting",
    "PlanPolicyMaterializationRead",
    "PlanPolicyParameters",
    "PlanPolicyPlanSummaryRead",
    "PlanPolicyPreviewRead",
    "PlanPolicyPreviewRequest",
    "PlanPolicyPublicationRead",
    "PlanPolicyPublishRequest",
    "PlanPolicyRolloutRequest",
    "PlanPolicyRolloutRule",
    "PlanPolicySnapshot",
    "PlanPolicyVersionRead",
    "QUANTITY_ENTITLEMENTS",
    "ResolvedPlanEntitlementRead",
    "WorkspacePlanPolicyResolutionRead",
]
