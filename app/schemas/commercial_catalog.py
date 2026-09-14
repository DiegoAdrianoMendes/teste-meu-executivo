"""Strict contracts for the canonical commercial catalog and account terms."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.commercial_catalog import (
    CommercialAdjustmentSource,
    CommercialAdjustmentType,
    CommercialBillingInterval,
    CommercialContractLineStatus,
    CommercialContractStatus,
    CommercialOfferFamily,
    CommercialOfferKind,
    CommercialPortability,
    CommercialRecordStatus,
    EntitlementBonusMode,
    EntitlementBonusSource,
)
from app.models.entitlement import EntitlementKey
from app.schemas.base import CamelCaseSchema, WriteSchema

# Schema 2.0 is intentionally frozen. Future entitlement additions require a
# new commercial schema reader instead of reinterpreting historical contracts.
COMMERCIAL_ENTITLEMENT_KEYS_V2: tuple[EntitlementKey, ...] = (
    EntitlementKey.tokens,
    EntitlementKey.ai_executions,
    EntitlementKey.ai_conversations,
    EntitlementKey.campaign_recipients,
    EntitlementKey.audio_minutes,
    EntitlementKey.storage_bytes,
    EntitlementKey.files,
    EntitlementKey.users,
    EntitlementKey.workspaces,
    EntitlementKey.whatsapp_numbers,
    EntitlementKey.agents,
    EntitlementKey.legal_suite,
    EntitlementKey.legal_areas,
    EntitlementKey.legal_cases,
    EntitlementKey.djen_terms,
    EntitlementKey.legal_ai_generations,
    EntitlementKey.legal_documents,
    EntitlementKey.legal_automations,
    EntitlementKey.legal_templates,
    EntitlementKey.legal_signatures,
    EntitlementKey.protocol_folders,
    EntitlementKey.legal_bulk_operations,
    EntitlementKey.legal_audit,
    EntitlementKey.legal_integrations,
    EntitlementKey.automations,
    EntitlementKey.active_tasks,
    EntitlementKey.task_participants,
    EntitlementKey.task_reviews,
    EntitlementKey.review_rounds,
    EntitlementKey.task_reminder_rules,
    EntitlementKey.meeting_participants,
    EntitlementKey.digital_signature,
    EntitlementKey.calendar,
    EntitlementKey.email,
    EntitlementKey.webhooks,
    EntitlementKey.advanced_audit,
    EntitlementKey.success_manager,
    EntitlementKey.monitored_processes,
)

# Schema 3.0 adds the first canonical contact-stock boundary. The splice keeps
# the reader-visible grouping stable without changing the frozen V2 tuple.
COMMERCIAL_ENTITLEMENT_KEYS_V3: tuple[EntitlementKey, ...] = (
    *COMMERCIAL_ENTITLEMENT_KEYS_V2[:4],
    EntitlementKey.contacts,
    *COMMERCIAL_ENTITLEMENT_KEYS_V2[4:],
)


def _assert_entitlement_schemas_are_current() -> None:
    """Keep the frozen V2 reader and current V3 writer explicit.

    Historical 2.0 snapshots intentionally omit ``contacts``. Runtime
    entitlement additions must instead extend a new schema and update this
    current-writer assertion before application startup can succeed.
    """

    if EntitlementKey.contacts in COMMERCIAL_ENTITLEMENT_KEYS_V2:
        raise RuntimeError("Commercial schema 2.0 is frozen and cannot include contacts.")
    declared = set(COMMERCIAL_ENTITLEMENT_KEYS_V3)
    canonical = set(EntitlementKey)
    if declared != canonical or len(COMMERCIAL_ENTITLEMENT_KEYS_V3) != len(declared):
        missing = sorted(item.value for item in canonical - declared)
        extra = sorted(item.value for item in declared - canonical)
        raise RuntimeError(
            "Commercial schema 3.0 is not aligned with EntitlementKey; "
            f"missing={missing}, extra={extra}. Publish a new schema reader "
            "before enabling the new entitlement."
        )


_assert_entitlement_schemas_are_current()

# This is intentionally frozen alongside schema 2.0. Treating a quantitative
# quota as a feature grant would remove its finite boundary, while applying a
# quantity delta to a boolean feature would manufacture a meaningless quota.
COMMERCIAL_FEATURE_KEYS_V2: frozenset[EntitlementKey] = frozenset(
    {
        EntitlementKey.digital_signature,
        EntitlementKey.calendar,
        EntitlementKey.email,
        EntitlementKey.webhooks,
        EntitlementKey.advanced_audit,
        EntitlementKey.success_manager,
        EntitlementKey.legal_suite,
        EntitlementKey.legal_signatures,
        EntitlementKey.legal_bulk_operations,
        EntitlementKey.legal_audit,
        EntitlementKey.legal_integrations,
    }
)
COMMERCIAL_FEATURE_KEYS_V3 = COMMERCIAL_FEATURE_KEYS_V2


class CommercialEntitlementPeriod(StrEnum):
    monthly = "monthly"
    stock = "stock"
    concurrent = "concurrent"
    feature = "feature"


class CommercialEntitlementEnforcement(StrEnum):
    hard = "hard"
    grace = "grace"


class CommercialEntitlementComposition(StrEnum):
    boolean_any = "boolean_any"
    maximum = "maximum"
    sum = "sum"


class _StrictWrite(WriteSchema):
    model_config = ConfigDict(**WriteSchema.model_config, extra="forbid")

    @field_validator("reason", check_fields=False)
    @classmethod
    def normalize_audit_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 8:
            raise ValueError("reason exige ao menos 8 caracteres úteis.")
        return normalized


class _StrictRead(CamelCaseSchema):
    model_config = ConfigDict(**CamelCaseSchema.model_config, extra="forbid")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Use data e hora com fuso horário explícito.")
    return value.astimezone(UTC)


class CommercialEntitlementSetting(_StrictWrite):
    enabled: bool
    unit: str = Field(min_length=1, max_length=40)
    period: CommercialEntitlementPeriod
    limit_quantity: int | None = Field(default=None, ge=0)
    enforcement: CommercialEntitlementEnforcement = CommercialEntitlementEnforcement.hard
    grace_quantity: int | None = Field(default=None, ge=0)
    composition: CommercialEntitlementComposition

    @field_validator("unit")
    @classmethod
    def normalize_unit(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("unit não pode ficar vazio.")
        return normalized

    @model_validator(mode="after")
    def validate_shape(self) -> CommercialEntitlementSetting:
        if self.period is CommercialEntitlementPeriod.feature:
            if self.limit_quantity is not None or self.grace_quantity is not None:
                raise ValueError("Entitlement feature não aceita quantidade.")
            if self.composition is not CommercialEntitlementComposition.boolean_any:
                raise ValueError("Entitlement feature usa composição boolean_any.")
        if not self.enabled and (
            self.limit_quantity is not None or self.grace_quantity is not None
        ):
            raise ValueError("Entitlement desabilitado não mantém quota oculta.")
        if self.enforcement is CommercialEntitlementEnforcement.hard:
            if self.grace_quantity is not None:
                raise ValueError("Limite hard não aceita tolerância.")
        elif self.limit_quantity is None or self.grace_quantity is None:
            raise ValueError("Enforcement grace exige limite e tolerância finitos.")
        return self


class _CommercialOfferSnapshotBase(_StrictWrite):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)
    is_public: bool = True
    is_active: bool = True
    is_recommended: bool = False
    is_quote_only: bool = False
    available_intervals: list[CommercialBillingInterval] = Field(
        default_factory=lambda: [CommercialBillingInterval.monthly], min_length=1, max_length=2
    )
    sort_order: int = Field(default=0, ge=0)
    currency: Literal["BRL"] = "BRL"
    price_monthly_cents: int | None = Field(default=None, ge=0)
    price_annual_cents: int | None = Field(default=None, ge=0)
    cta: str = Field(min_length=1, max_length=120)
    entitlements: dict[EntitlementKey, CommercialEntitlementSetting]

    @field_validator("name", "cta")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("O texto não pode ficar vazio.")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("available_intervals")
    @classmethod
    def unique_intervals(
        cls, values: list[CommercialBillingInterval]
    ) -> list[CommercialBillingInterval]:
        if len(set(values)) != len(values):
            raise ValueError("availableIntervals não pode repetir intervalos.")
        return values

    @model_validator(mode="after")
    def validate_offer_shape(self) -> _CommercialOfferSnapshotBase:
        if self.is_quote_only:
            if self.price_monthly_cents is not None or self.price_annual_cents is not None:
                raise ValueError("Oferta sob consulta não publica preço de catálogo.")
        elif self.price_monthly_cents is None:
            raise ValueError("Oferta pública precificada exige preço mensal.")
        if CommercialBillingInterval.annual not in self.available_intervals:
            if self.price_annual_cents is not None:
                raise ValueError("Preço anual só existe quando o intervalo anual está disponível.")
        if CommercialBillingInterval.monthly not in self.available_intervals:
            raise ValueError("Toda nova oferta V1 precisa aceitar cobrança mensal.")
        return self

    def _validate_complete_policy(
        self,
        expected_keys: tuple[EntitlementKey, ...],
    ) -> None:
        configured = set(self.entitlements)
        expected = set(expected_keys)
        if configured != expected:
            missing = sorted(item.value for item in expected - configured)
            extra = sorted(str(item) for item in configured - expected)
            raise ValueError(
                f"A política deve declarar todos os entitlements; missing={missing}, extra={extra}."
            )
        finite_keys = (
            EntitlementKey.tokens,
            EntitlementKey.ai_executions,
            EntitlementKey.ai_conversations,
            EntitlementKey.campaign_recipients,
            EntitlementKey.audio_minutes,
            EntitlementKey.legal_ai_generations,
        )
        if EntitlementKey.contacts in expected:
            finite_keys = (*finite_keys, EntitlementKey.contacts)
        for key in finite_keys:
            setting = self.entitlements[key]
            if setting.enabled and setting.limit_quantity is None:
                raise ValueError(f"{key.value} exige quota finita.")


class CommercialOfferSnapshotV2Read(_CommercialOfferSnapshotBase):
    """Exact reader for immutable historical 2.0 offer revisions."""

    schema_version: Literal["2.0"] = "2.0"

    @model_validator(mode="after")
    def validate_complete_v2_policy(self) -> CommercialOfferSnapshotV2Read:
        self._validate_complete_policy(COMMERCIAL_ENTITLEMENT_KEYS_V2)
        return self


class CommercialOfferSnapshotWrite(_CommercialOfferSnapshotBase):
    """Current offer writer; new publications are always explicit schema 3.0."""

    schema_version: Literal["3.0"] = "3.0"

    @model_validator(mode="after")
    def validate_complete_v3_policy(self) -> CommercialOfferSnapshotWrite:
        self._validate_complete_policy(COMMERCIAL_ENTITLEMENT_KEYS_V3)
        return self


CommercialOfferSnapshotRead = Annotated[
    CommercialOfferSnapshotV2Read | CommercialOfferSnapshotWrite,
    Field(discriminator="schema_version"),
]


class CommercialOfferPublishRequest(_StrictWrite):
    sku: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    family: CommercialOfferFamily
    kind: CommercialOfferKind
    legacy_tier: str | None = Field(default=None, max_length=40)
    snapshot: CommercialOfferSnapshotWrite
    reason: str = Field(min_length=8, max_length=500)

    @field_validator("legacy_tier")
    @classmethod
    def normalize_legacy_tier(cls, value: str | None) -> str | None:
        return value.strip().lower() or None if value is not None else None


class CommercialOfferVersionRead(_StrictRead):
    id: UUID
    offer_id: UUID
    sku: str
    family: CommercialOfferFamily
    kind: CommercialOfferKind
    legacy_tier: str | None = None
    version: int
    snapshot: CommercialOfferSnapshotRead
    snapshot_hash: str
    publication_reason: str
    published_by_user_id: UUID | None = None
    published_at: datetime


class CommercialOfferPageRead(_StrictRead):
    items: list[CommercialOfferVersionRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class CommercialCatalogBootstrapRead(_StrictRead):
    items: list[CommercialOfferVersionRead]
    idempotent_replay: bool = False


class CommercialOfferCommandRead(_StrictRead):
    offer: CommercialOfferVersionRead
    idempotent_replay: bool = False


class CommercialFieldDiffRead(_StrictRead):
    path: str
    label: str
    change_type: Literal["added", "removed", "changed"]
    before: Any | None = None
    after: Any | None = None


class CommercialUnitEconomicsBlockerRead(_StrictRead):
    code: str
    message: str


class CommercialUnitEconomicsGateRead(_StrictRead):
    assessment_version: str
    status: Literal["not_required", "insufficient_data", "unprofitable", "ready"]
    eligible: bool
    requires_evidence: bool
    evidence_source: Literal["provider_confirmed_usage_plus_internal_rate_card"]
    evaluated_at: datetime
    window_days: int
    minimum_confirmed_calls: int
    minimum_distinct_days: int
    minimum_observed_tokens: int
    observed_calls: int | None = None
    observed_days: int | None = None
    observed_tokens: int | None = None
    blockers: list[CommercialUnitEconomicsBlockerRead] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class CommercialOfferDraftSaveRequest(_StrictWrite):
    expected_published_version: int = Field(ge=1)
    snapshot: CommercialOfferSnapshotWrite
    reason: str = Field(min_length=8, max_length=500)


class CommercialOfferDraftRead(_StrictRead):
    id: UUID
    offer_id: UUID
    sku: str
    draft_version: int
    base_published_version: int
    snapshot: CommercialOfferSnapshotRead
    snapshot_hash: str
    status: Literal["active", "discarded", "published"]
    reason: str
    updated_by_user_id: UUID | None = None
    updated_at: datetime


class CommercialOfferDraftCommandRead(_StrictRead):
    draft: CommercialOfferDraftRead
    idempotent_replay: bool = False


class CommercialOfferDraftPreviewRead(_StrictRead):
    draft: CommercialOfferDraftRead
    published: CommercialOfferVersionRead
    diff: list[CommercialFieldDiffRead]
    warnings: list[str] = Field(default_factory=list)
    unit_economics: CommercialUnitEconomicsGateRead
    eligible: bool


class CommercialOfferDraftPublishRequest(_StrictWrite):
    reason: str = Field(min_length=8, max_length=500)


class CommercialOfferRevisionHistoryItem(_StrictRead):
    offer: CommercialOfferVersionRead
    diff: list[CommercialFieldDiffRead]


class CommercialOfferRevisionHistoryRead(_StrictRead):
    sku: str
    items: list[CommercialOfferRevisionHistoryItem]


class CommercialOfferRestorePreviewRequest(_StrictWrite):
    target_version: int = Field(ge=1)
    expected_current_version: int = Field(ge=1)


class CommercialOfferRestoreRequest(CommercialOfferRestorePreviewRequest):
    reason: str = Field(min_length=8, max_length=500)


class CommercialOfferRestorePreviewRead(_StrictRead):
    sku: str
    current: CommercialOfferVersionRead
    target: CommercialOfferVersionRead
    diff: list[CommercialFieldDiffRead]
    warnings: list[str] = Field(default_factory=list)
    unit_economics: CommercialUnitEconomicsGateRead
    eligible: bool


class WorkspaceContractLineWrite(_StrictWrite):
    sku: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    policy_version_id: UUID | None = None
    quantity: int = Field(default=1, ge=1, le=100_000)
    contracted_price_cents: int | None = Field(default=None, ge=0)
    billing_interval: CommercialBillingInterval = CommercialBillingInterval.monthly
    entitlement_overrides: dict[EntitlementKey, CommercialEntitlementSetting] = Field(
        default_factory=dict
    )
    effective_from: datetime | None = None
    effective_until: datetime | None = None

    @field_validator("effective_from", "effective_until")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_window(self) -> WorkspaceContractLineWrite:
        now = datetime.now(UTC)
        if (
            self.effective_from is not None
            and self.effective_until is not None
            and self.effective_until <= self.effective_from
        ):
            raise ValueError("effectiveUntil deve ser posterior a effectiveFrom.")
        if self.effective_from is not None and self.effective_from > now:
            raise ValueError(
                "effectiveFrom futuro permanece indisponível até existir um scheduler comercial."
            )
        if self.effective_until is not None and self.effective_until <= now:
            raise ValueError("A linha comercial não pode nascer com vigência já encerrada.")
        return self


class WorkspaceContractPreviewRequest(_StrictWrite):
    workspace_id: UUID
    lines: list[WorkspaceContractLineWrite] = Field(min_length=1, max_length=2)


class WorkspaceContractCreateRequest(WorkspaceContractPreviewRequest):
    status: CommercialContractStatus = CommercialContractStatus.active
    reason: str = Field(min_length=8, max_length=500)


class WorkspaceContractRevisionPreviewRequest(_StrictWrite):
    expected_version: int = Field(ge=1)
    lines: list[WorkspaceContractLineWrite] = Field(min_length=1, max_length=2)


class WorkspaceContractRevisionRequest(_StrictWrite):
    lines: list[WorkspaceContractLineWrite] = Field(min_length=1, max_length=2)
    reason: str = Field(min_length=8, max_length=500)


class WorkspaceContractLineRead(_StrictRead):
    id: UUID | None = None
    offer_id: UUID
    policy_version_id: UUID
    sku: str
    offer_family: CommercialOfferFamily
    quantity: int
    list_price_cents: int
    contracted_price_cents: int
    billing_interval: CommercialBillingInterval
    entitlement_overrides: dict[EntitlementKey, CommercialEntitlementSetting]
    effective_from: datetime
    effective_until: datetime | None = None
    status: CommercialContractLineStatus
    version: int


class PricePreviewRead(_StrictRead):
    list_price_cents: int = Field(
        ge=0,
        description="Preço de lista mensal equivalente, inclusive para linhas anuais legadas.",
    )
    contracted_price_cents: int = Field(
        ge=0,
        description="Preço contratado mensal equivalente antes dos ajustes.",
    )
    recurring_price_cents: int = Field(
        ge=0,
        description="Recorrência mensal equivalente depois dos ajustes.",
    )
    invoice_credit_cents: int = Field(
        ge=0,
        description="Crédito aplicado à cobrança mensal em prévia.",
    )
    amount_due_cents: int = Field(
        ge=0,
        description="Valor mensal equivalente após créditos.",
    )


class CapabilitySourceRead(_StrictRead):
    type: Literal["plan", "legal", "bonus", "override"]
    id: UUID
    contribution: int | bool | None


class EffectiveCapabilityRead(_StrictRead):
    key: EntitlementKey
    contracted: bool
    enabled: bool
    permitted: bool
    limit: int | None = None
    grace_limit: int | None = None
    used: int | None = None
    remaining: int | None = None
    unit: str
    period: CommercialEntitlementPeriod
    enforcement: CommercialEntitlementEnforcement
    sources: list[CapabilitySourceRead]
    denial_reason: (
        Literal[
            "not_contracted",
            "permission_denied",
            "quota_exhausted",
            "suspended",
            "not_configured",
        ]
        | None
    ) = None
    upgrade_offer: str | None = None


class EffectiveCapabilityManifestRead(_StrictRead):
    workspace_id: UUID
    contract_id: UUID
    contract_version: int
    contract_status: CommercialContractStatus
    active_products: list[str]
    capabilities: dict[EntitlementKey, EffectiveCapabilityRead]
    generated_at: datetime


class WorkspaceContractPreviewRead(_StrictRead):
    workspace_id: UUID
    lines: list[WorkspaceContractLineRead]
    price: PricePreviewRead
    manifest: EffectiveCapabilityManifestRead
    warnings: list[str] = Field(default_factory=list)


class WorkspaceContractRevisionPreviewRead(_StrictRead):
    contract_id: UUID
    workspace_id: UUID
    current_version: int
    target_version: int
    current_lines: list[WorkspaceContractLineRead]
    lines: list[WorkspaceContractLineRead]
    current_price: PricePreviewRead
    price: PricePreviewRead
    manifest: EffectiveCapabilityManifestRead
    changed_families: list[CommercialOfferFamily]
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    eligible: bool


class WorkspaceContractRead(_StrictRead):
    id: UUID
    workspace_id: UUID
    workspace_label: str
    status: CommercialContractStatus
    currency: Literal["BRL"]
    version: int
    lines: list[WorkspaceContractLineRead]
    price: PricePreviewRead
    created_at: datetime
    updated_at: datetime
    idempotent_replay: bool = False


class WorkspaceContractPageRead(_StrictRead):
    items: list[WorkspaceContractRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class WorkspaceContractHistoryItem(_StrictRead):
    event_id: UUID
    event_type: str
    version: int
    reason: str
    actor_user_id: UUID | None = None
    occurred_at: datetime
    snapshot: WorkspaceContractRead
    diff: list[CommercialFieldDiffRead]


class WorkspaceContractHistoryRead(_StrictRead):
    contract_id: UUID
    current_version: int
    items: list[WorkspaceContractHistoryItem]


class WorkspaceContractRestorePreviewRequest(_StrictWrite):
    target_version: int = Field(ge=1)
    expected_current_version: int = Field(ge=1)


class WorkspaceContractRestoreRequest(WorkspaceContractRestorePreviewRequest):
    reason: str = Field(min_length=8, max_length=500)


class WorkspaceContractRestorePreviewRead(_StrictRead):
    contract_id: UUID
    current_version: int
    target_version: int
    target_snapshot: WorkspaceContractRead
    revision: WorkspaceContractRevisionPreviewRead
    diff: list[CommercialFieldDiffRead]
    preserves_adjustments: bool = True
    preserves_bonuses: bool = True


class CommercialAdjustmentCreateRequest(_StrictWrite):
    contract_line_id: UUID | None = None
    type: CommercialAdjustmentType
    value: int = Field(ge=0)
    starts_at: datetime
    ends_at: datetime | None = None
    remaining_cycles: int | None = Field(default=None, ge=0)
    stack_group: str = Field(min_length=1, max_length=80)
    stackable: bool = False
    portability: CommercialPortability = CommercialPortability.preserve
    reason: str = Field(min_length=8, max_length=500)
    source: CommercialAdjustmentSource = CommercialAdjustmentSource.manual

    @field_validator("starts_at", "ends_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @field_validator("stack_group")
    @classmethod
    def normalize_stack_group(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def validate_adjustment(self) -> CommercialAdjustmentCreateRequest:
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("endsAt deve ser posterior a startsAt.")
        if self.ends_at is not None and self.ends_at <= datetime.now(UTC):
            raise ValueError("endsAt deve representar uma vigência ainda não encerrada.")
        if self.type is CommercialAdjustmentType.percentage_discount and self.value > 10_000:
            raise ValueError("Desconto percentual usa basis points e não pode exceder 100%.")
        if self.type is CommercialAdjustmentType.courtesy_period and self.value != 1:
            raise ValueError(
                "Cortesia por intervalo usa o marcador 1; "
                "a vigência é definida por startsAt/endsAt."
            )
        if self.type is CommercialAdjustmentType.courtesy_period and self.ends_at is None:
            raise ValueError("Cortesia exige endsAt para não criar gratuidade permanente.")
        if self.remaining_cycles is not None:
            raise ValueError(
                "remainingCycles permanece indisponível até existir consumo "
                "idempotente por ciclo; use endsAt para limitar a vigência."
            )
        if self.portability is CommercialPortability.recalculate:
            raise ValueError(
                "portability=recalculate permanece indisponível até existir "
                "uma regra determinística de recálculo."
            )
        return self


class CommercialAdjustmentUpdateRequest(CommercialAdjustmentCreateRequest):
    """Full replacement of mutable adjustment terms.

    The caller must also send ``If-Match``.  Keeping this as a full semantic
    replacement makes the audit diff deterministic and avoids accidental
    retention of hidden fields in a partial patch.
    """


class CommercialAdjustmentRead(_StrictRead):
    id: UUID
    contract_id: UUID
    contract_line_id: UUID | None = None
    type: CommercialAdjustmentType
    value: int
    starts_at: datetime
    ends_at: datetime | None = None
    remaining_cycles: int | None = None
    stack_group: str
    stackable: bool
    portability: CommercialPortability
    reason: str
    source: CommercialAdjustmentSource
    status: CommercialRecordStatus
    version: int
    created_at: datetime


class CommercialAdjustmentCommandRead(_StrictRead):
    adjustment: CommercialAdjustmentRead
    idempotent_replay: bool = False


class CommercialRecordRevokeRequest(_StrictWrite):
    reason: str = Field(min_length=8, max_length=500)


class EntitlementBonusCreateRequest(_StrictWrite):
    entitlement: EntitlementKey
    mode: EntitlementBonusMode
    quantity: int | None = Field(default=None, ge=0)
    starts_at: datetime
    ends_at: datetime | None = None
    renews_each_period: bool = False
    portability: CommercialPortability = CommercialPortability.preserve
    reason: str = Field(min_length=8, max_length=500)
    source: EntitlementBonusSource = EntitlementBonusSource.manual

    @field_validator("starts_at", "ends_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_bonus(self) -> EntitlementBonusCreateRequest:
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("endsAt deve ser posterior a startsAt.")
        if self.ends_at is not None and self.ends_at <= datetime.now(UTC):
            raise ValueError("endsAt deve representar uma vigência ainda não encerrada.")
        if not self.renews_each_period and self.ends_at is None:
            raise ValueError(
                "Bônus não renovável exige endsAt até existir um ledger de consumo por período."
            )
        if self.mode is EntitlementBonusMode.quantity_delta and self.quantity is None:
            raise ValueError("Bônus quantitativo exige quantity.")
        if self.mode is EntitlementBonusMode.feature_grant and self.quantity is not None:
            raise ValueError("Feature grant não aceita quantity.")
        if self.entitlement is EntitlementKey.legal_suite:
            raise ValueError("legal_suite é produto contratado, não bônus.")
        if (
            self.mode is EntitlementBonusMode.feature_grant
            and self.entitlement not in COMMERCIAL_FEATURE_KEYS_V2
        ):
            raise ValueError("Feature grant só pode habilitar entitlement booleano.")
        if (
            self.mode is EntitlementBonusMode.quantity_delta
            and self.entitlement in COMMERCIAL_FEATURE_KEYS_V2
        ):
            raise ValueError("Bônus quantitativo não pode ser aplicado a entitlement booleano.")
        if self.portability is CommercialPortability.recalculate:
            raise ValueError(
                "portability=recalculate permanece indisponível até existir "
                "uma regra determinística de recálculo."
            )
        return self


class EntitlementBonusUpdateRequest(EntitlementBonusCreateRequest):
    """Full replacement of mutable bonus terms; version is supplied by ETag."""


class EntitlementBonusRead(_StrictRead):
    id: UUID
    workspace_id: UUID
    entitlement: EntitlementKey
    mode: EntitlementBonusMode
    quantity: int | None = None
    starts_at: datetime
    ends_at: datetime | None = None
    renews_each_period: bool
    portability: CommercialPortability
    reason: str
    source: EntitlementBonusSource
    status: CommercialRecordStatus
    version: int
    created_at: datetime


class EntitlementBonusCommandRead(_StrictRead):
    bonus: EntitlementBonusRead
    idempotent_replay: bool = False


__all__ = [
    "COMMERCIAL_ENTITLEMENT_KEYS_V2",
    "COMMERCIAL_ENTITLEMENT_KEYS_V3",
    "CapabilitySourceRead",
    "CommercialAdjustmentCommandRead",
    "CommercialAdjustmentCreateRequest",
    "CommercialAdjustmentUpdateRequest",
    "CommercialAdjustmentRead",
    "CommercialCatalogBootstrapRead",
    "CommercialEntitlementComposition",
    "CommercialEntitlementEnforcement",
    "CommercialEntitlementPeriod",
    "CommercialEntitlementSetting",
    "CommercialOfferPageRead",
    "CommercialOfferCommandRead",
    "CommercialFieldDiffRead",
    "CommercialUnitEconomicsBlockerRead",
    "CommercialUnitEconomicsGateRead",
    "CommercialOfferDraftSaveRequest",
    "CommercialOfferDraftRead",
    "CommercialOfferDraftCommandRead",
    "CommercialOfferDraftPreviewRead",
    "CommercialOfferDraftPublishRequest",
    "CommercialOfferPublishRequest",
    "CommercialOfferRevisionHistoryItem",
    "CommercialOfferRevisionHistoryRead",
    "CommercialOfferRestorePreviewRequest",
    "CommercialOfferRestoreRequest",
    "CommercialOfferRestorePreviewRead",
    "CommercialOfferSnapshotRead",
    "CommercialOfferSnapshotV2Read",
    "CommercialOfferSnapshotWrite",
    "CommercialOfferVersionRead",
    "CommercialRecordRevokeRequest",
    "EffectiveCapabilityManifestRead",
    "EffectiveCapabilityRead",
    "EntitlementBonusCreateRequest",
    "EntitlementBonusUpdateRequest",
    "EntitlementBonusCommandRead",
    "EntitlementBonusRead",
    "PricePreviewRead",
    "WorkspaceContractCreateRequest",
    "WorkspaceContractLineRead",
    "WorkspaceContractLineWrite",
    "WorkspaceContractPreviewRead",
    "WorkspaceContractPreviewRequest",
    "WorkspaceContractRevisionPreviewRead",
    "WorkspaceContractRevisionPreviewRequest",
    "WorkspaceContractRevisionRequest",
    "WorkspaceContractRead",
    "WorkspaceContractPageRead",
    "WorkspaceContractHistoryItem",
    "WorkspaceContractHistoryRead",
    "WorkspaceContractRestorePreviewRequest",
    "WorkspaceContractRestoreRequest",
    "WorkspaceContractRestorePreviewRead",
]
