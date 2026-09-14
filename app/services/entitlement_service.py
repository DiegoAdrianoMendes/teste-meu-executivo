"""Typed entitlement resolution and atomic monthly usage reservations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# Register the canonical commercial contract tables for clean test/startup
# schema creation. The router/model registry integration remains centralized.
import app.models.commercial_catalog  # noqa: F401

# Register versioned-policy metadata before test/startup schema creation without
# coupling app.models.__init__ to this isolated delivery.
import app.models.plan_policy  # noqa: F401
from app.core.config import settings
from app.models.ai_token_package import (
    AiTokenPackageRequestStatus,
    WorkspaceAiTokenPackageRequest,
)
from app.models.billing import Subscription, TokenBudget
from app.models.entitlement import (
    EntitlementKey,
    EntitlementUsageReservation,
    OverrideMode,
    PlanEntitlement,
    UsageReservationStatus,
    WorkspaceEntitlementOverride,
    WorkspaceMonthlyUsage,
)
from app.models.workspace import Workspace


class EntitlementUnit(StrEnum):
    tokens = "tokens"
    executions = "executions"
    conversations = "conversations"
    recipients = "recipients"
    contacts = "contacts"
    minutes = "minutes"
    bytes = "bytes"
    files = "files"
    users = "users"
    workspaces = "workspaces"
    numbers = "numbers"
    agents = "agents"
    areas = "areas"
    cases = "cases"
    terms = "terms"
    generations = "generations"
    documents = "documents"
    templates = "templates"
    folders = "folders"
    operations = "operations"
    automations = "automations"
    tasks = "tasks"
    participants = "participants"
    reviews = "reviews"
    rounds = "rounds"
    reminders = "reminders"
    meetings = "meetings"
    feature = "feature"
    processes = "processes"


@dataclass(frozen=True, slots=True)
class EntitlementDefinition:
    key: EntitlementKey
    unit: EntitlementUnit
    legacy_plan_attribute: str | None = None
    legacy_feature_keys: tuple[str, ...] = ()
    legacy_feature_flag_keys: tuple[str, ...] = ()


ENTITLEMENT_CATALOG: Mapping[EntitlementKey, EntitlementDefinition] = MappingProxyType(
    {
        EntitlementKey.tokens: EntitlementDefinition(
            key=EntitlementKey.tokens,
            unit=EntitlementUnit.tokens,
            legacy_plan_attribute="max_tokens_per_month",
        ),
        EntitlementKey.ai_executions: EntitlementDefinition(
            key=EntitlementKey.ai_executions,
            unit=EntitlementUnit.executions,
            legacy_feature_keys=("maxAiExecutionsPerMonth", "max_ai_executions_per_month"),
        ),
        EntitlementKey.ai_conversations: EntitlementDefinition(
            key=EntitlementKey.ai_conversations,
            unit=EntitlementUnit.conversations,
            legacy_feature_keys=(
                "maxAiConversationsPerMonth",
                "max_ai_conversations_per_month",
            ),
        ),
        EntitlementKey.campaign_recipients: EntitlementDefinition(
            key=EntitlementKey.campaign_recipients,
            unit=EntitlementUnit.recipients,
            legacy_feature_keys=(
                "maxCampaignRecipientsPerMonth",
                "max_campaign_recipients_per_month",
            ),
        ),
        EntitlementKey.contacts: EntitlementDefinition(
            key=EntitlementKey.contacts,
            unit=EntitlementUnit.contacts,
            legacy_feature_keys=("maxContacts", "max_contacts"),
        ),
        EntitlementKey.audio_minutes: EntitlementDefinition(
            key=EntitlementKey.audio_minutes,
            unit=EntitlementUnit.minutes,
            legacy_feature_keys=(
                "maxAudioMinutesPerMonth",
                "max_audio_minutes_per_month",
            ),
        ),
        EntitlementKey.storage_bytes: EntitlementDefinition(
            key=EntitlementKey.storage_bytes,
            unit=EntitlementUnit.bytes,
            legacy_feature_keys=("maxStorageBytes", "max_storage_bytes"),
        ),
        EntitlementKey.files: EntitlementDefinition(
            key=EntitlementKey.files,
            unit=EntitlementUnit.files,
            legacy_feature_keys=("maxFilesPerMonth", "max_files_per_month"),
        ),
        EntitlementKey.users: EntitlementDefinition(
            key=EntitlementKey.users,
            unit=EntitlementUnit.users,
            legacy_plan_attribute="max_users",
        ),
        EntitlementKey.workspaces: EntitlementDefinition(
            key=EntitlementKey.workspaces,
            unit=EntitlementUnit.workspaces,
            legacy_feature_keys=("maxWorkspaces", "max_workspaces"),
        ),
        EntitlementKey.whatsapp_numbers: EntitlementDefinition(
            key=EntitlementKey.whatsapp_numbers,
            unit=EntitlementUnit.numbers,
            legacy_feature_keys=("maxConnectedWhatsapps", "max_connected_whatsapps"),
        ),
        EntitlementKey.agents: EntitlementDefinition(
            key=EntitlementKey.agents,
            unit=EntitlementUnit.agents,
            legacy_plan_attribute="max_ai_agents",
        ),
        EntitlementKey.legal_suite: EntitlementDefinition(
            key=EntitlementKey.legal_suite,
            unit=EntitlementUnit.feature,
        ),
        EntitlementKey.legal_areas: EntitlementDefinition(
            key=EntitlementKey.legal_areas,
            unit=EntitlementUnit.areas,
            legacy_feature_keys=("maxLegalAreas", "max_legal_areas"),
        ),
        EntitlementKey.legal_cases: EntitlementDefinition(
            key=EntitlementKey.legal_cases,
            unit=EntitlementUnit.cases,
        ),
        EntitlementKey.djen_terms: EntitlementDefinition(
            key=EntitlementKey.djen_terms,
            unit=EntitlementUnit.terms,
        ),
        EntitlementKey.legal_ai_generations: EntitlementDefinition(
            key=EntitlementKey.legal_ai_generations,
            unit=EntitlementUnit.generations,
        ),
        EntitlementKey.legal_documents: EntitlementDefinition(
            key=EntitlementKey.legal_documents,
            unit=EntitlementUnit.documents,
        ),
        EntitlementKey.legal_automations: EntitlementDefinition(
            key=EntitlementKey.legal_automations,
            unit=EntitlementUnit.automations,
        ),
        EntitlementKey.legal_templates: EntitlementDefinition(
            key=EntitlementKey.legal_templates,
            unit=EntitlementUnit.templates,
        ),
        EntitlementKey.legal_signatures: EntitlementDefinition(
            key=EntitlementKey.legal_signatures,
            unit=EntitlementUnit.feature,
        ),
        EntitlementKey.protocol_folders: EntitlementDefinition(
            key=EntitlementKey.protocol_folders,
            unit=EntitlementUnit.folders,
        ),
        EntitlementKey.legal_bulk_operations: EntitlementDefinition(
            key=EntitlementKey.legal_bulk_operations,
            unit=EntitlementUnit.feature,
        ),
        EntitlementKey.legal_audit: EntitlementDefinition(
            key=EntitlementKey.legal_audit,
            unit=EntitlementUnit.feature,
        ),
        EntitlementKey.legal_integrations: EntitlementDefinition(
            key=EntitlementKey.legal_integrations,
            unit=EntitlementUnit.feature,
        ),
        EntitlementKey.automations: EntitlementDefinition(
            key=EntitlementKey.automations,
            unit=EntitlementUnit.automations,
            legacy_feature_keys=("automationLimit", "automation_limit"),
        ),
        EntitlementKey.active_tasks: EntitlementDefinition(
            key=EntitlementKey.active_tasks,
            unit=EntitlementUnit.tasks,
        ),
        EntitlementKey.task_participants: EntitlementDefinition(
            key=EntitlementKey.task_participants,
            unit=EntitlementUnit.participants,
        ),
        EntitlementKey.task_reviews: EntitlementDefinition(
            key=EntitlementKey.task_reviews,
            unit=EntitlementUnit.reviews,
        ),
        EntitlementKey.review_rounds: EntitlementDefinition(
            key=EntitlementKey.review_rounds,
            unit=EntitlementUnit.rounds,
        ),
        EntitlementKey.task_reminder_rules: EntitlementDefinition(
            key=EntitlementKey.task_reminder_rules,
            unit=EntitlementUnit.reminders,
        ),
        EntitlementKey.meeting_participants: EntitlementDefinition(
            key=EntitlementKey.meeting_participants,
            unit=EntitlementUnit.participants,
        ),
        EntitlementKey.digital_signature: EntitlementDefinition(
            key=EntitlementKey.digital_signature,
            unit=EntitlementUnit.feature,
            legacy_feature_flag_keys=("digitalSignature", "digital_signature"),
        ),
        EntitlementKey.calendar: EntitlementDefinition(
            key=EntitlementKey.calendar,
            unit=EntitlementUnit.feature,
            legacy_feature_flag_keys=("calendar",),
        ),
        EntitlementKey.email: EntitlementDefinition(
            key=EntitlementKey.email,
            unit=EntitlementUnit.feature,
            legacy_feature_flag_keys=("email",),
        ),
        EntitlementKey.webhooks: EntitlementDefinition(
            key=EntitlementKey.webhooks,
            unit=EntitlementUnit.feature,
            legacy_feature_flag_keys=("webhooks",),
        ),
        EntitlementKey.advanced_audit: EntitlementDefinition(
            key=EntitlementKey.advanced_audit,
            unit=EntitlementUnit.feature,
            legacy_feature_flag_keys=("advancedAudit", "advanced_audit"),
        ),
        EntitlementKey.success_manager: EntitlementDefinition(
            key=EntitlementKey.success_manager,
            unit=EntitlementUnit.feature,
            legacy_feature_flag_keys=("successManager", "success_manager"),
        ),
        # Add-on contratável à parte — nunca tem legado (módulo nasceu depois
        # do catálogo legado de `Plan.features`): sem PlanEntitlement/override
        # configurado, `enforce_stock` levanta EntitlementNotConfiguredError e
        # a superfície do módulo recusa como "não contratado" (0/ausente).
        EntitlementKey.monitored_processes: EntitlementDefinition(
            key=EntitlementKey.monitored_processes,
            unit=EntitlementUnit.processes,
        ),
    }
)

STOCK_ENTITLEMENTS = frozenset(
    {
        EntitlementKey.storage_bytes,
        EntitlementKey.files,
        EntitlementKey.users,
        EntitlementKey.contacts,
        EntitlementKey.workspaces,
        EntitlementKey.whatsapp_numbers,
        EntitlementKey.agents,
        EntitlementKey.legal_areas,
        EntitlementKey.legal_cases,
        EntitlementKey.djen_terms,
        EntitlementKey.legal_documents,
        EntitlementKey.legal_automations,
        EntitlementKey.legal_templates,
        EntitlementKey.protocol_folders,
        EntitlementKey.automations,
        EntitlementKey.active_tasks,
        EntitlementKey.task_participants,
        EntitlementKey.task_reviews,
        EntitlementKey.review_rounds,
        EntitlementKey.task_reminder_rules,
        EntitlementKey.meeting_participants,
        EntitlementKey.monitored_processes,
    }
)
METERED_ENTITLEMENTS = frozenset(
    {
        EntitlementKey.tokens,
        EntitlementKey.ai_executions,
        EntitlementKey.ai_conversations,
        EntitlementKey.campaign_recipients,
        EntitlementKey.audio_minutes,
        EntitlementKey.legal_ai_generations,
    }
)
FEATURE_ENTITLEMENTS = frozenset(
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
QUANTITATIVE_ENTITLEMENTS = frozenset(STOCK_ENTITLEMENTS | METERED_ENTITLEMENTS)


def _assert_entitlement_contract_is_complete() -> None:
    """Refuse startup when a new entitlement lacks catalog/classification."""

    canonical = set(EntitlementKey)
    catalog = set(ENTITLEMENT_CATALOG)
    categories = (STOCK_ENTITLEMENTS, METERED_ENTITLEMENTS, FEATURE_ENTITLEMENTS)
    classified = set().union(*categories)
    overlaps = {
        item.value
        for index, left in enumerate(categories)
        for right in categories[index + 1 :]
        for item in left & right
    }
    if catalog != canonical or classified != canonical or overlaps:
        raise RuntimeError(
            "Entitlement contract is incomplete; "
            f"catalog_missing={sorted(item.value for item in canonical - catalog)}, "
            f"catalog_extra={sorted(item.value for item in catalog - canonical)}, "
            f"classification_missing={sorted(item.value for item in canonical - classified)}, "
            f"classification_extra={sorted(item.value for item in classified - canonical)}, "
            f"classification_overlaps={sorted(overlaps)}."
        )


_assert_entitlement_contract_is_complete()


class EntitlementLimitSource(StrEnum):
    commercial_contract = "commercial_contract"
    commercial_bonus = "commercial_bonus"
    plan_policy_version = "plan_policy_version"
    plan_entitlement = "plan_entitlement"
    token_package = "token_package"
    token_budget = "token_budget"
    legacy_plan = "legacy_plan"
    legacy_feature = "legacy_feature"
    workspace_override = "workspace_override"


@dataclass(frozen=True, slots=True)
class ResolvedEntitlement:
    key: EntitlementKey
    enabled: bool
    limit_quantity: int | None
    hard_limit: bool
    source: EntitlementLimitSource
    plan_id: UUID | None


@dataclass(frozen=True, slots=True)
class MonthlyUsageSnapshot:
    workspace_id: UUID
    entitlement: EntitlementKey
    period_start: date
    consumed_quantity: int
    reserved_quantity: int
    limit_quantity: int | None
    hard_limit: bool
    limit_source: str | None

    @property
    def total_quantity(self) -> int:
        return self.consumed_quantity + self.reserved_quantity

    @property
    def remaining_quantity(self) -> int | None:
        if self.limit_quantity is None:
            return None
        return max(self.limit_quantity - self.total_quantity, 0)

    @property
    def is_over_limit(self) -> bool:
        return self.limit_quantity is not None and self.total_quantity > self.limit_quantity


@dataclass(frozen=True, slots=True)
class CurrentStockSnapshot:
    workspace_id: UUID
    entitlement: EntitlementKey
    current_quantity: int
    requested_quantity: int
    limit_quantity: int | None
    hard_limit: bool
    limit_source: str | None
    legacy_unsubscribed_bypass: bool = False

    @property
    def projected_quantity(self) -> int:
        return self.current_quantity + self.requested_quantity


class EntitlementError(RuntimeError):
    """Base class for entitlement and usage failures."""


class EntitlementNotConfiguredError(EntitlementError):
    pass


class EntitlementDisabledError(EntitlementError):
    pass


class EntitlementIdempotencyConflictError(EntitlementError):
    pass


class UsageReservationNotFoundError(EntitlementError):
    pass


class UsageReservationStateError(EntitlementError):
    pass


class UsageInvariantError(EntitlementError):
    pass


class EntitlementWorkspaceNotFoundError(EntitlementError):
    pass


class CurrentStockLimitExceededError(EntitlementError):
    def __init__(
        self,
        *,
        entitlement: EntitlementKey,
        limit_quantity: int,
        current_quantity: int,
        requested_quantity: int,
    ) -> None:
        self.entitlement = entitlement
        self.limit_quantity = limit_quantity
        self.current_quantity = current_quantity
        self.requested_quantity = requested_quantity
        super().__init__(
            f"Limite corrente de {entitlement.value} excedido: "
            f"limite={limit_quantity}, atual={current_quantity}, "
            f"solicitado={requested_quantity}."
        )

    @property
    def usage_quantity(self) -> int:
        return self.current_quantity

    @property
    def remaining_quantity(self) -> int:
        return max(self.limit_quantity - self.usage_quantity, 0)


class MonthlyQuotaExceededError(EntitlementError):
    def __init__(
        self,
        *,
        entitlement: EntitlementKey,
        limit_quantity: int,
        consumed_quantity: int,
        reserved_quantity: int,
        requested_quantity: int,
    ) -> None:
        self.entitlement = entitlement
        self.limit_quantity = limit_quantity
        self.consumed_quantity = consumed_quantity
        self.reserved_quantity = reserved_quantity
        self.requested_quantity = requested_quantity
        super().__init__(
            f"Quota mensal de {entitlement.value} excedida: limite={limit_quantity}, "
            f"consumido={consumed_quantity}, reservado={reserved_quantity}, "
            f"solicitado={requested_quantity}."
        )

    @property
    def usage_quantity(self) -> int:
        return self.consumed_quantity + self.reserved_quantity

    @property
    def remaining_quantity(self) -> int:
        return max(self.limit_quantity - self.usage_quantity, 0)


class _ConditionalUsageUpdateRejectedError(RuntimeError):
    pass


class _ConcurrentReservationResolutionError(RuntimeError):
    pass


def utc_month_start(occurred_at: datetime | None = None) -> date:
    instant = occurred_at or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    else:
        instant = instant.astimezone(UTC)
    return date(instant.year, instant.month, 1)


def get_entitlement_definition(key: EntitlementKey | str) -> EntitlementDefinition:
    normalized = EntitlementKey(key)
    return ENTITLEMENT_CATALOG[normalized]


def entitlement_idempotency_key(namespace: str, *parts: object) -> str:
    """Build a stable reservation key without leaking long domain payloads."""
    normalized_namespace = namespace.strip().lower().replace(" ", "-") or "usage"
    raw = ":".join(str(part) for part in parts)
    digest = sha256(raw.encode("utf-8")).hexdigest()
    return f"{normalized_namespace[:80]}:{digest}"


class EntitlementResolver:
    """Resolve typed limits while retaining read compatibility with legacy plans."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _require_finite_quantitative_limit(
        resolved: ResolvedEntitlement,
    ) -> ResolvedEntitlement:
        """Reject historical quantitative grants that imply unlimited use."""

        quantity = resolved.limit_quantity
        if (
            resolved.enabled
            and resolved.key in QUANTITATIVE_ENTITLEMENTS
            and (
                quantity is None
                or isinstance(quantity, bool)
                or not isinstance(quantity, int)
                or quantity < 0
            )
        ):
            raise EntitlementNotConfiguredError(
                f"Entitlement quantitativo {resolved.key.value} exige limite finito "
                "e não negativo."
            )
        return resolved

    async def resolve(
        self,
        workspace_id: UUID,
        entitlement: EntitlementKey | str,
    ) -> ResolvedEntitlement:
        """Resolve the effective entitlement: override > plan > default.

        A platform-admin ``WorkspaceEntitlementOverride`` always wins over the
        plan/package resolution. ``revoke`` fails closed (disabled, zero
        quota) without even needing a resolvable plan. ``grant`` and ``limit``
        first attempt the normal plan resolution to inherit ``hard_limit``/
        ``plan_id`` context, but degrade gracefully — an override can enable
        an entitlement the plan never configured at all.
        """
        key = EntitlementKey(entitlement)

        # A canonical multiproduct contract, when present, is authoritative.
        # Workspaces not yet reconciled keep the legacy resolver below; this is
        # the expand/contract compatibility seam and deliberately fails closed
        # once a canonical contract exists.
        from app.schemas.commercial_catalog import CommercialEntitlementEnforcement
        from app.services.commercial_catalog_service import (
            CommercialCatalogService,
            CommercialContractNotFoundError,
        )

        try:
            capability = await CommercialCatalogService(self.db).resolve_effective_capability(
                workspace_id, key
            )
        except CommercialContractNotFoundError:
            capability = None
        if capability is not None:
            source_types = {item.type for item in capability.sources}
            source = (
                EntitlementLimitSource.workspace_override
                if "override" in source_types
                else EntitlementLimitSource.commercial_bonus
                if "bonus" in source_types
                else EntitlementLimitSource.commercial_contract
            )
            blocking_limit = (
                capability.grace_limit
                if capability.enforcement is CommercialEntitlementEnforcement.grace
                and capability.grace_limit is not None
                else capability.limit
            )
            operationally_enabled = capability.enabled and capability.denial_reason not in {
                "not_contracted",
                "permission_denied",
                "suspended",
                "not_configured",
            }
            canonical = ResolvedEntitlement(
                key=key,
                enabled=operationally_enabled,
                limit_quantity=blocking_limit,
                # V1 does not bill an unbounded overage. A configured grace
                # threshold is therefore the final hard enforcement boundary.
                hard_limit=True,
                source=source,
                plan_id=None,
            )
            # An account override is absolute. Legacy token packages/budgets
            # cannot silently alter it after the canonical resolver applied it.
            if source is EntitlementLimitSource.workspace_override:
                return self._require_finite_quantitative_limit(canonical)
            return self._require_finite_quantitative_limit(
                await self._with_token_overlays(workspace_id, canonical)
            )

        override = await self._get_override(workspace_id, key)

        if override is not None and override.mode == OverrideMode.revoke:
            return ResolvedEntitlement(
                key=key,
                enabled=False,
                limit_quantity=0,
                hard_limit=True,
                source=EntitlementLimitSource.workspace_override,
                plan_id=await self._safe_plan_id(workspace_id),
            )

        try:
            base = await self._resolve_plan_only(workspace_id, key)
        except EntitlementNotConfiguredError:
            if override is None:
                raise
            base = None

        if override is None:
            return self._require_finite_quantitative_limit(
                await self._with_token_overlays(workspace_id, base)
            )

        if (
            key in QUANTITATIVE_ENTITLEMENTS
            and override.mode in {OverrideMode.grant, OverrideMode.limit}
            and (
                override.quantity is None
                or isinstance(override.quantity, bool)
                or override.quantity < 0
            )
        ):
            raise EntitlementNotConfiguredError(
                f"Override quantitativo histórico de {key.value} não possui "
                "quantity finita e não negativa."
            )

        if override.mode == OverrideMode.grant:
            overridden = ResolvedEntitlement(
                key=key,
                enabled=True,
                limit_quantity=(
                    override.quantity
                    if override.quantity is not None
                    else (base.limit_quantity if base is not None else None)
                ),
                hard_limit=base.hard_limit if base is not None else True,
                source=EntitlementLimitSource.workspace_override,
                plan_id=base.plan_id if base is not None else None,
            )
        elif override.mode == OverrideMode.limit:
            overridden = ResolvedEntitlement(
                key=key,
                enabled=True,
                limit_quantity=override.quantity,
                hard_limit=base.hard_limit if base is not None else True,
                source=EntitlementLimitSource.workspace_override,
                plan_id=base.plan_id if base is not None else None,
            )
        else:  # pragma: no cover — defensive, DB enum already fails closed.
            raise EntitlementError(f"Modo de override desconhecido: {override.mode!r}")

        return self._require_finite_quantitative_limit(
            await self._with_token_overlays(workspace_id, overridden)
        )

    async def _get_override(
        self,
        workspace_id: UUID,
        key: EntitlementKey,
    ) -> WorkspaceEntitlementOverride | None:
        return await self.db.scalar(
            select(WorkspaceEntitlementOverride).where(
                WorkspaceEntitlementOverride.workspace_id == workspace_id,
                WorkspaceEntitlementOverride.entitlement == key,
            )
        )

    async def _safe_plan_id(self, workspace_id: UUID) -> UUID | None:
        return await self.db.scalar(
            select(Subscription.plan_id).where(Subscription.workspace_id == workspace_id)
        )

    async def _resolve_plan_only(
        self,
        workspace_id: UUID,
        key: EntitlementKey,
    ) -> ResolvedEntitlement:
        """Resolve strictly from the plan/package — no override, no overlays."""
        subscription = await self.db.scalar(
            select(Subscription)
            .options(selectinload(Subscription.plan))
            .where(Subscription.workspace_id == workspace_id)
        )
        if subscription is None or subscription.plan is None:
            raise EntitlementNotConfiguredError(
                f"Workspace {workspace_id} não possui plano para resolver {key.value}."
            )

        plan = subscription.plan
        # Local import avoids a module cycle: PlanPolicyService reuses this
        # module's entitlement catalog but never calls EntitlementResolver.
        from app.services.plan_policy_service import (
            PlanPolicyNotFoundError,
            PlanPolicyService,
            PlanPolicyValidationError,
        )

        try:
            versioned = await PlanPolicyService(self.db).resolve_versioned_workspace(
                workspace_id=workspace_id
            )
        except PlanPolicyNotFoundError:
            versioned = None
        except PlanPolicyValidationError as error:
            raise EntitlementNotConfiguredError(str(error)) from error
        if versioned is not None:
            policy_setting = versioned.entitlements[key]
            return ResolvedEntitlement(
                key=key,
                enabled=policy_setting.enabled,
                limit_quantity=policy_setting.limit_quantity,
                hard_limit=policy_setting.hard_limit,
                source=EntitlementLimitSource.plan_policy_version,
                plan_id=versioned.plan_id,
            )

        configured = await self.db.scalar(
            select(PlanEntitlement).where(
                PlanEntitlement.plan_id == plan.id,
                PlanEntitlement.entitlement == key,
            )
        )
        if configured is not None:
            return ResolvedEntitlement(
                key=key,
                enabled=configured.enabled,
                limit_quantity=configured.limit_quantity,
                hard_limit=configured.hard_limit,
                source=EntitlementLimitSource.plan_entitlement,
                plan_id=plan.id,
            )

        typed_count = int(
            await self.db.scalar(
                select(func.count(PlanEntitlement.id)).where(PlanEntitlement.plan_id == plan.id)
            )
            or 0
        )
        if typed_count:
            raise EntitlementNotConfiguredError(
                f"O plano tipado {plan.id} não configura o entitlement {key.value}."
            )

        definition = ENTITLEMENT_CATALOG[key]
        if definition.legacy_plan_attribute is not None:
            return ResolvedEntitlement(
                key=key,
                enabled=True,
                limit_quantity=self._coerce_limit(
                    getattr(plan, definition.legacy_plan_attribute), key
                ),
                hard_limit=True,
                source=EntitlementLimitSource.legacy_plan,
                plan_id=plan.id,
            )

        features = plan.features or {}
        for feature_key in definition.legacy_feature_flag_keys:
            if feature_key in features:
                value = features[feature_key]
                if not isinstance(value, bool):
                    raise EntitlementNotConfiguredError(
                        f"Flag inválida para {key.value}: {value!r}."
                    )
                return ResolvedEntitlement(
                    key=key,
                    enabled=value,
                    limit_quantity=None,
                    hard_limit=True,
                    source=EntitlementLimitSource.legacy_feature,
                    plan_id=plan.id,
                )
        for feature_key in definition.legacy_feature_keys:
            if feature_key in features:
                return ResolvedEntitlement(
                    key=key,
                    enabled=True,
                    limit_quantity=self._coerce_limit(features[feature_key], key),
                    hard_limit=True,
                    source=EntitlementLimitSource.legacy_feature,
                    plan_id=plan.id,
                )

        raise EntitlementNotConfiguredError(
            f"O plano {plan.id} não configura o entitlement {key.value}."
        )

    async def _with_token_overlays(
        self,
        workspace_id: UUID,
        resolved: ResolvedEntitlement,
    ) -> ResolvedEntitlement:
        with_packages = await self._with_active_token_packages(workspace_id, resolved)
        return await self._with_token_budget_overlay(workspace_id, with_packages)

    async def _with_active_token_packages(
        self,
        workspace_id: UUID,
        resolved: ResolvedEntitlement,
    ) -> ResolvedEntitlement:
        if (
            resolved.key != EntitlementKey.tokens
            or not resolved.enabled
            or resolved.limit_quantity is None
        ):
            return resolved
        today = datetime.now(UTC).date()
        extra_tokens = int(
            await self.db.scalar(
                select(
                    func.coalesce(
                        func.sum(WorkspaceAiTokenPackageRequest.token_quantity_snapshot),
                        0,
                    )
                ).where(
                    WorkspaceAiTokenPackageRequest.workspace_id == workspace_id,
                    WorkspaceAiTokenPackageRequest.status == AiTokenPackageRequestStatus.approved,
                    WorkspaceAiTokenPackageRequest.period_start <= today,
                    WorkspaceAiTokenPackageRequest.period_end > today,
                )
            )
            or 0
        )
        if extra_tokens <= 0:
            return resolved
        return ResolvedEntitlement(
            key=resolved.key,
            enabled=resolved.enabled,
            limit_quantity=resolved.limit_quantity + extra_tokens,
            hard_limit=resolved.hard_limit,
            source=EntitlementLimitSource.token_package,
            plan_id=resolved.plan_id,
        )

    async def _with_token_budget_overlay(
        self,
        workspace_id: UUID,
        resolved: ResolvedEntitlement,
    ) -> ResolvedEntitlement:
        if resolved.key != EntitlementKey.tokens or not resolved.enabled:
            return resolved
        token_budget = await self.db.scalar(
            select(TokenBudget).where(TokenBudget.workspace_id == workspace_id)
        )
        if token_budget is None:
            return resolved

        budget_limit = self._coerce_limit(token_budget.monthly_limit, resolved.key)
        if budget_limit is None:
            return resolved
        # A soft workspace budget is an alert threshold; it must not replace a
        # larger hard plan cap and accidentally make the soft threshold blocking.
        if resolved.hard_limit and not token_budget.hard_limit:
            return resolved
        if resolved.limit_quantity is None or budget_limit < resolved.limit_quantity:
            effective_limit = budget_limit
            source = EntitlementLimitSource.token_budget
        else:
            effective_limit = resolved.limit_quantity
            source = resolved.source
        return ResolvedEntitlement(
            key=resolved.key,
            enabled=resolved.enabled,
            limit_quantity=effective_limit,
            hard_limit=resolved.hard_limit or token_budget.hard_limit,
            source=source,
            plan_id=resolved.plan_id,
        )

    @staticmethod
    def _coerce_limit(value: object, key: EntitlementKey) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise EntitlementNotConfiguredError(
                f"Limite inválido para {key.value}: boolean não é quantidade."
            )
        try:
            normalized = int(value)
        except (TypeError, ValueError) as error:
            raise EntitlementNotConfiguredError(
                f"Limite inválido para {key.value}: {value!r}."
            ) from error
        if normalized < 0 or str(normalized) != str(value).strip():
            raise EntitlementNotConfiguredError(f"Limite inválido para {key.value}: {value!r}.")
        return normalized


CurrentQuantityLoader = Callable[[], Awaitable[int]]


class EntitlementEnforcementService:
    """Apply typed entitlements at domain write boundaries.

    Stock checks serialize on the workspace row and evaluate the live resource
    count. Metered checks delegate to durable monthly reservations. A local
    legacy workspace with no subscription bypasses enforcement; once a
    subscription exists, missing or disabled configuration fails closed.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.resolver = EntitlementResolver(db)
        self.monthly_usage = MonthlyUsageService(db)

    async def lock_workspace(self, workspace_id: UUID) -> bool:
        workspace = await self.db.scalar(
            select(Workspace.id)
            .where(
                Workspace.id == workspace_id,
                Workspace.deleted_at.is_(None),
            )
            # Serialize entitlement/quota writers without blocking the
            # ``FOR KEY SHARE`` acquired by tenant-child foreign keys.  A full
            # ``FOR UPDATE`` makes a dedicated storage-intent transaction wait
            # on this caller while the caller waits on that transaction.
            .with_for_update(key_share=True)
        )
        if workspace is None:
            raise EntitlementWorkspaceNotFoundError("Workspace não encontrado.")
        subscription_id = await self.db.scalar(
            select(Subscription.id).where(Subscription.workspace_id == workspace_id)
        )
        if subscription_id is not None:
            return True
        # Legal 360 standalone intentionally has no legacy CRM Subscription.
        # Its canonical contract still activates quota enforcement.
        from app.models.commercial_catalog import WorkspaceCommercialContract

        canonical_contract_id = await self.db.scalar(
            select(WorkspaceCommercialContract.id).where(
                WorkspaceCommercialContract.workspace_id == workspace_id
            )
        )
        if canonical_contract_id is not None:
            return True
        # The legacy no-contract bypass only exists for the isolated local
        # simulator. AWS/staging/production must deny an uncontracted account;
        # otherwise a missing reconciliation record becomes unlimited access.
        if (
            settings.is_local_only
            and settings.uses_local_infrastructure
            and not settings.is_production
        ):
            return False
        raise EntitlementNotConfiguredError(
            "Workspace sem assinatura legada nem contrato comercial canônico."
        )

    async def enforce_stock(
        self,
        *,
        workspace_id: UUID,
        entitlement: EntitlementKey | str,
        load_current_quantity: CurrentQuantityLoader,
        requested_quantity: int = 1,
    ) -> CurrentStockSnapshot:
        key = EntitlementKey(entitlement)
        if key not in STOCK_ENTITLEMENTS:
            raise ValueError(f"{key.value} não é um entitlement de estoque.")
        quantity = MonthlyUsageService._validate_quantity(requested_quantity)
        has_subscription = await self.lock_workspace(workspace_id)
        if not has_subscription:
            return CurrentStockSnapshot(
                workspace_id=workspace_id,
                entitlement=key,
                current_quantity=0,
                requested_quantity=quantity,
                limit_quantity=None,
                hard_limit=False,
                limit_source=None,
                legacy_unsubscribed_bypass=True,
            )

        resolved = await self.resolver.resolve(workspace_id, key)
        if not resolved.enabled:
            raise EntitlementDisabledError(
                f"Entitlement {key.value} desabilitado para este workspace."
            )
        current_quantity = await load_current_quantity()
        if (
            isinstance(current_quantity, bool)
            or not isinstance(current_quantity, int)
            or current_quantity < 0
        ):
            raise UsageInvariantError(
                f"Contagem corrente inválida para {key.value}: {current_quantity!r}."
            )
        if (
            resolved.hard_limit
            and resolved.limit_quantity is not None
            and current_quantity + quantity > resolved.limit_quantity
        ):
            raise CurrentStockLimitExceededError(
                entitlement=key,
                limit_quantity=resolved.limit_quantity,
                current_quantity=current_quantity,
                requested_quantity=quantity,
            )
        return CurrentStockSnapshot(
            workspace_id=workspace_id,
            entitlement=key,
            current_quantity=current_quantity,
            requested_quantity=quantity,
            limit_quantity=resolved.limit_quantity,
            hard_limit=resolved.hard_limit,
            limit_source=resolved.source.value,
        )

    async def enforce_feature(
        self,
        *,
        workspace_id: UUID,
        entitlement: EntitlementKey | str,
    ) -> ResolvedEntitlement | None:
        key = EntitlementKey(entitlement)
        if key not in FEATURE_ENTITLEMENTS:
            raise ValueError(f"{key.value} não é um entitlement de recurso.")
        has_subscription = await self.lock_workspace(workspace_id)
        if not has_subscription:
            return None
        resolved = await self.resolver.resolve(workspace_id, key)
        if not resolved.enabled:
            raise EntitlementDisabledError(
                f"Entitlement {key.value} desabilitado para este workspace."
            )
        return resolved

    async def reserve_metered(
        self,
        *,
        workspace_id: UUID,
        entitlement: EntitlementKey | str,
        quantity: int,
        idempotency_key: str,
        occurred_at: datetime | None = None,
    ) -> EntitlementUsageReservation | None:
        key = EntitlementKey(entitlement)
        if key not in METERED_ENTITLEMENTS:
            raise ValueError(f"{key.value} não é um entitlement de uso medido.")
        has_subscription = await self.lock_workspace(workspace_id)
        if not has_subscription:
            return None
        resolved = await self.resolver.resolve(workspace_id, key)
        if not resolved.enabled:
            raise EntitlementDisabledError(
                f"Entitlement {key.value} desabilitado para este workspace."
            )
        if key == EntitlementKey.tokens and resolved.limit_quantity is None:
            raise EntitlementNotConfiguredError(
                "A franquia de tokens de IA precisa ter um limite mensal finito."
            )
        return await self.monthly_usage.reserve(
            workspace_id=workspace_id,
            entitlement=key,
            quantity=quantity,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
        )

    async def commit_metered(
        self,
        *,
        workspace_id: UUID,
        reservation: EntitlementUsageReservation | None,
        actual_quantity: int | None = None,
    ) -> EntitlementUsageReservation | None:
        if reservation is None:
            return None
        return await self.monthly_usage.commit(
            workspace_id=workspace_id,
            reservation_id=reservation.id,
            actual_quantity=actual_quantity,
        )

    async def commit_metered_by_id(
        self,
        *,
        workspace_id: UUID,
        reservation_id: UUID,
        actual_quantity: int | None = None,
    ) -> EntitlementUsageReservation:
        return await self.monthly_usage.commit(
            workspace_id=workspace_id,
            reservation_id=reservation_id,
            actual_quantity=actual_quantity,
        )

    async def release_metered(
        self,
        *,
        workspace_id: UUID,
        reservation: EntitlementUsageReservation | None,
    ) -> EntitlementUsageReservation | None:
        if reservation is None:
            return None
        return await self.monthly_usage.release(
            workspace_id=workspace_id,
            reservation_id=reservation.id,
        )

    async def release_metered_by_id(
        self,
        *,
        workspace_id: UUID,
        reservation_id: UUID,
    ) -> EntitlementUsageReservation:
        return await self.monthly_usage.release(
            workspace_id=workspace_id,
            reservation_id=reservation_id,
        )

    async def get_metered_reservation(
        self,
        *,
        workspace_id: UUID,
        reservation_id: UUID,
    ) -> EntitlementUsageReservation:
        return await self.monthly_usage.get_reservation(
            workspace_id=workspace_id,
            reservation_id=reservation_id,
        )


class MonthlyUsageService:
    """Reserve quota before a write, then commit or release in the same transaction.

    The service flushes state but never commits the session. Callers keep the
    reservation transition and the protected domain write in one transaction.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.resolver = EntitlementResolver(db)

    async def reserve(
        self,
        *,
        workspace_id: UUID,
        entitlement: EntitlementKey | str,
        quantity: int,
        idempotency_key: str,
        occurred_at: datetime | None = None,
    ) -> EntitlementUsageReservation:
        key = EntitlementKey(entitlement)
        normalized_quantity = self._validate_quantity(quantity)
        normalized_idempotency_key = self._validate_idempotency_key(idempotency_key)
        period_start = utc_month_start(occurred_at)

        existing = await self._find_reservation(
            workspace_id,
            key,
            period_start,
            normalized_idempotency_key,
        )
        if existing is not None:
            self._validate_reservation_replay(existing, normalized_quantity)
            return existing

        resolved = await self.resolver.resolve(workspace_id, key)
        if not resolved.enabled:
            raise EntitlementDisabledError(
                f"Entitlement {key.value} desabilitado para o workspace {workspace_id}."
            )

        usage = await self._ensure_usage_bucket(
            workspace_id=workspace_id,
            entitlement=key,
            period_start=period_start,
            resolved=resolved,
        )
        reservation = EntitlementUsageReservation(
            workspace_id=workspace_id,
            usage_id=usage.id,
            entitlement=key,
            period_start=period_start,
            idempotency_key=normalized_idempotency_key,
            reserved_quantity=normalized_quantity,
            status=UsageReservationStatus.reserved,
        )

        try:
            async with self.db.begin_nested():
                statement = (
                    update(WorkspaceMonthlyUsage)
                    .where(WorkspaceMonthlyUsage.id == usage.id)
                    .values(
                        reserved_quantity=(
                            WorkspaceMonthlyUsage.reserved_quantity + normalized_quantity
                        ),
                        limit_quantity=resolved.limit_quantity,
                        hard_limit=resolved.hard_limit,
                        limit_source=resolved.source.value,
                    )
                    .execution_options(synchronize_session=False)
                )
                if resolved.hard_limit and resolved.limit_quantity is not None:
                    statement = statement.where(
                        WorkspaceMonthlyUsage.consumed_quantity
                        + WorkspaceMonthlyUsage.reserved_quantity
                        + normalized_quantity
                        <= resolved.limit_quantity
                    )
                result = await self.db.execute(statement)
                if result.rowcount != 1:
                    raise _ConditionalUsageUpdateRejectedError
                self.db.add(reservation)
                await self.db.flush()
        except IntegrityError:
            replay = await self._find_reservation(
                workspace_id,
                key,
                period_start,
                normalized_idempotency_key,
            )
            if replay is None:
                raise
            self._validate_reservation_replay(replay, normalized_quantity)
            return replay
        except _ConditionalUsageUpdateRejectedError:
            snapshot = await self.get_usage(
                workspace_id=workspace_id,
                entitlement=key,
                occurred_at=occurred_at,
            )
            if resolved.limit_quantity is None:
                raise UsageInvariantError("Bucket de uso não pôde ser reservado.")
            raise MonthlyQuotaExceededError(
                entitlement=key,
                limit_quantity=resolved.limit_quantity,
                consumed_quantity=snapshot.consumed_quantity,
                reserved_quantity=snapshot.reserved_quantity,
                requested_quantity=normalized_quantity,
            ) from None

        return reservation

    async def commit(
        self,
        *,
        workspace_id: UUID,
        reservation_id: UUID,
        actual_quantity: int | None = None,
    ) -> EntitlementUsageReservation:
        reservation = await self._get_reservation(workspace_id, reservation_id)
        quantity = (
            reservation.reserved_quantity
            if actual_quantity is None
            else self._validate_quantity(actual_quantity, allow_zero=True)
        )
        if reservation.status == UsageReservationStatus.committed:
            if reservation.committed_quantity != quantity:
                raise EntitlementIdempotencyConflictError(
                    "A reserva já foi confirmada com outra quantidade."
                )
            return reservation
        if reservation.status == UsageReservationStatus.released:
            raise UsageReservationStateError("Uma reserva liberada não pode ser confirmada.")

        usage = await self._get_usage_bucket(workspace_id, reservation.usage_id)
        if (
            usage.entitlement != reservation.entitlement
            or usage.period_start != reservation.period_start
        ):
            raise UsageInvariantError(
                "A reserva não corresponde ao recurso e período do bucket mensal."
            )
        try:
            async with self.db.begin_nested():
                resolution = await self.db.execute(
                    update(EntitlementUsageReservation)
                    .where(
                        EntitlementUsageReservation.id == reservation.id,
                        EntitlementUsageReservation.workspace_id == workspace_id,
                        EntitlementUsageReservation.status == UsageReservationStatus.reserved,
                    )
                    .values(
                        status=UsageReservationStatus.committed,
                        committed_quantity=quantity,
                        resolved_at=datetime.now(UTC),
                    )
                    .execution_options(synchronize_session=False)
                )
                if resolution.rowcount != 1:
                    raise _ConcurrentReservationResolutionError

                statement = (
                    update(WorkspaceMonthlyUsage)
                    .where(
                        WorkspaceMonthlyUsage.id == usage.id,
                        WorkspaceMonthlyUsage.workspace_id == workspace_id,
                        WorkspaceMonthlyUsage.entitlement == reservation.entitlement,
                        WorkspaceMonthlyUsage.period_start == reservation.period_start,
                        WorkspaceMonthlyUsage.reserved_quantity >= reservation.reserved_quantity,
                    )
                    .values(
                        reserved_quantity=(
                            WorkspaceMonthlyUsage.reserved_quantity - reservation.reserved_quantity
                        ),
                        consumed_quantity=WorkspaceMonthlyUsage.consumed_quantity + quantity,
                    )
                    .execution_options(synchronize_session=False)
                )
                if usage.hard_limit and usage.limit_quantity is not None:
                    statement = statement.where(
                        WorkspaceMonthlyUsage.consumed_quantity
                        + WorkspaceMonthlyUsage.reserved_quantity
                        - reservation.reserved_quantity
                        + quantity
                        <= usage.limit_quantity
                    )
                result = await self.db.execute(statement)
                if result.rowcount != 1:
                    raise _ConditionalUsageUpdateRejectedError
        except _ConcurrentReservationResolutionError:
            replay = await self._get_reservation(
                workspace_id,
                reservation_id,
                populate_existing=True,
            )
            if replay.status == UsageReservationStatus.committed:
                if replay.committed_quantity != quantity:
                    raise EntitlementIdempotencyConflictError(
                        "A reserva já foi confirmada com outra quantidade."
                    )
                return replay
            raise UsageReservationStateError(
                "A reserva foi resolvida por outra operação."
            ) from None
        except _ConditionalUsageUpdateRejectedError:
            snapshot = await self.get_usage(
                workspace_id=workspace_id,
                entitlement=reservation.entitlement,
                occurred_at=datetime.combine(reservation.period_start, datetime.min.time(), UTC),
            )
            if usage.hard_limit and usage.limit_quantity is not None:
                raise MonthlyQuotaExceededError(
                    entitlement=reservation.entitlement,
                    limit_quantity=usage.limit_quantity,
                    consumed_quantity=snapshot.consumed_quantity,
                    reserved_quantity=snapshot.reserved_quantity,
                    requested_quantity=quantity,
                ) from None
            raise UsageInvariantError(
                "O total reservado do bucket é menor que a reserva pendente."
            ) from None

        return await self._get_reservation(workspace_id, reservation_id, populate_existing=True)

    async def release(
        self,
        *,
        workspace_id: UUID,
        reservation_id: UUID,
    ) -> EntitlementUsageReservation:
        reservation = await self._get_reservation(workspace_id, reservation_id)
        if reservation.status == UsageReservationStatus.released:
            return reservation
        if reservation.status == UsageReservationStatus.committed:
            raise UsageReservationStateError("Uma reserva confirmada não pode ser liberada.")

        try:
            async with self.db.begin_nested():
                resolution = await self.db.execute(
                    update(EntitlementUsageReservation)
                    .where(
                        EntitlementUsageReservation.id == reservation.id,
                        EntitlementUsageReservation.workspace_id == workspace_id,
                        EntitlementUsageReservation.status == UsageReservationStatus.reserved,
                    )
                    .values(
                        status=UsageReservationStatus.released,
                        resolved_at=datetime.now(UTC),
                    )
                    .execution_options(synchronize_session=False)
                )
                if resolution.rowcount != 1:
                    raise _ConcurrentReservationResolutionError
                result = await self.db.execute(
                    update(WorkspaceMonthlyUsage)
                    .where(
                        WorkspaceMonthlyUsage.id == reservation.usage_id,
                        WorkspaceMonthlyUsage.workspace_id == workspace_id,
                        WorkspaceMonthlyUsage.entitlement == reservation.entitlement,
                        WorkspaceMonthlyUsage.period_start == reservation.period_start,
                        WorkspaceMonthlyUsage.reserved_quantity >= reservation.reserved_quantity,
                    )
                    .values(
                        reserved_quantity=(
                            WorkspaceMonthlyUsage.reserved_quantity - reservation.reserved_quantity
                        )
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != 1:
                    raise _ConditionalUsageUpdateRejectedError
        except _ConcurrentReservationResolutionError:
            replay = await self._get_reservation(
                workspace_id,
                reservation_id,
                populate_existing=True,
            )
            if replay.status == UsageReservationStatus.released:
                return replay
            raise UsageReservationStateError(
                "A reserva foi resolvida por outra operação."
            ) from None
        except _ConditionalUsageUpdateRejectedError:
            raise UsageInvariantError(
                "O total reservado do bucket é menor que a reserva pendente."
            ) from None

        return await self._get_reservation(workspace_id, reservation_id, populate_existing=True)

    async def get_usage(
        self,
        *,
        workspace_id: UUID,
        entitlement: EntitlementKey | str,
        occurred_at: datetime | None = None,
    ) -> MonthlyUsageSnapshot:
        key = EntitlementKey(entitlement)
        period_start = utc_month_start(occurred_at)
        usage = await self.db.scalar(
            select(WorkspaceMonthlyUsage)
            .where(
                WorkspaceMonthlyUsage.workspace_id == workspace_id,
                WorkspaceMonthlyUsage.entitlement == key,
                WorkspaceMonthlyUsage.period_start == period_start,
            )
            .execution_options(populate_existing=True)
        )
        if usage is None:
            resolved = await self.resolver.resolve(workspace_id, key)
            return MonthlyUsageSnapshot(
                workspace_id=workspace_id,
                entitlement=key,
                period_start=period_start,
                consumed_quantity=0,
                reserved_quantity=0,
                limit_quantity=resolved.limit_quantity,
                hard_limit=resolved.hard_limit,
                limit_source=resolved.source.value,
            )
        return self._snapshot(usage)

    async def get_reservation(
        self,
        *,
        workspace_id: UUID,
        reservation_id: UUID,
    ) -> EntitlementUsageReservation:
        return await self._get_reservation(
            workspace_id,
            reservation_id,
            populate_existing=True,
        )

    async def _ensure_usage_bucket(
        self,
        *,
        workspace_id: UUID,
        entitlement: EntitlementKey,
        period_start: date,
        resolved: ResolvedEntitlement,
    ) -> WorkspaceMonthlyUsage:
        values = {
            "id": uuid4(),
            "workspace_id": workspace_id,
            "entitlement": entitlement,
            "period_start": period_start,
            "consumed_quantity": 0,
            "reserved_quantity": 0,
            "limit_quantity": resolved.limit_quantity,
            "hard_limit": resolved.hard_limit,
            "limit_source": resolved.source.value,
        }
        dialect_name = self.db.get_bind().dialect.name
        if dialect_name == "postgresql":
            statement = postgresql_insert(WorkspaceMonthlyUsage).values(**values)
            statement = statement.on_conflict_do_nothing(
                index_elements=["workspace_id", "entitlement", "period_start"]
            )
            await self.db.execute(statement)
        elif dialect_name == "sqlite":
            statement = sqlite_insert(WorkspaceMonthlyUsage).values(**values)
            statement = statement.on_conflict_do_nothing(
                index_elements=["workspace_id", "entitlement", "period_start"]
            )
            await self.db.execute(statement)
        else:
            try:
                async with self.db.begin_nested():
                    self.db.add(WorkspaceMonthlyUsage(**values))
                    await self.db.flush()
            except IntegrityError:
                pass

        usage = await self.db.scalar(
            select(WorkspaceMonthlyUsage).where(
                WorkspaceMonthlyUsage.workspace_id == workspace_id,
                WorkspaceMonthlyUsage.entitlement == entitlement,
                WorkspaceMonthlyUsage.period_start == period_start,
            )
        )
        if usage is None:
            raise UsageInvariantError("Não foi possível criar o bucket mensal de uso.")
        return usage

    async def _find_reservation(
        self,
        workspace_id: UUID,
        entitlement: EntitlementKey,
        period_start: date,
        idempotency_key: str,
    ) -> EntitlementUsageReservation | None:
        return await self.db.scalar(
            select(EntitlementUsageReservation).where(
                EntitlementUsageReservation.workspace_id == workspace_id,
                EntitlementUsageReservation.entitlement == entitlement,
                EntitlementUsageReservation.period_start == period_start,
                EntitlementUsageReservation.idempotency_key == idempotency_key,
            )
        )

    async def _get_reservation(
        self,
        workspace_id: UUID,
        reservation_id: UUID,
        *,
        populate_existing: bool = False,
    ) -> EntitlementUsageReservation:
        statement = select(EntitlementUsageReservation).where(
            EntitlementUsageReservation.id == reservation_id,
            EntitlementUsageReservation.workspace_id == workspace_id,
        )
        if populate_existing:
            statement = statement.execution_options(populate_existing=True)
        reservation = await self.db.scalar(statement)
        if reservation is None:
            raise UsageReservationNotFoundError(
                f"Reserva {reservation_id} não encontrada no workspace {workspace_id}."
            )
        return reservation

    async def _get_usage_bucket(
        self,
        workspace_id: UUID,
        usage_id: UUID,
    ) -> WorkspaceMonthlyUsage:
        usage = await self.db.scalar(
            select(WorkspaceMonthlyUsage)
            .where(
                WorkspaceMonthlyUsage.id == usage_id,
                WorkspaceMonthlyUsage.workspace_id == workspace_id,
            )
            .execution_options(populate_existing=True)
        )
        if usage is None:
            raise UsageInvariantError(f"Bucket de uso {usage_id} não encontrado.")
        return usage

    @staticmethod
    def _validate_quantity(quantity: int, *, allow_zero: bool = False) -> int:
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise ValueError("quantity deve ser um inteiro.")
        minimum = 0 if allow_zero else 1
        if quantity < minimum:
            qualifier = "não negativa" if allow_zero else "positiva"
            raise ValueError(f"quantity deve ser {qualifier}.")
        return quantity

    @staticmethod
    def _validate_idempotency_key(value: str) -> str:
        normalized = value.strip() if isinstance(value, str) else ""
        if not normalized:
            raise ValueError("idempotency_key é obrigatória.")
        if len(normalized) > 200:
            raise ValueError("idempotency_key deve ter no máximo 200 caracteres.")
        return normalized

    @staticmethod
    def _validate_reservation_replay(
        reservation: EntitlementUsageReservation,
        quantity: int,
    ) -> None:
        if reservation.reserved_quantity != quantity:
            raise EntitlementIdempotencyConflictError(
                "A chave idempotente já foi usada com outra quantidade."
            )

    @staticmethod
    def _snapshot(usage: WorkspaceMonthlyUsage) -> MonthlyUsageSnapshot:
        return MonthlyUsageSnapshot(
            workspace_id=usage.workspace_id,
            entitlement=usage.entitlement,
            period_start=usage.period_start,
            consumed_quantity=usage.consumed_quantity,
            reserved_quantity=usage.reserved_quantity,
            limit_quantity=usage.limit_quantity,
            hard_limit=usage.hard_limit,
            limit_source=usage.limit_source,
        )
