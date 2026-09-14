"""Transactional, provider-free SaaS billing lifecycle for local simulation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any
from uuid import UUID
from weakref import WeakValueDictionary

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.tenant import set_workspace_context
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.commercial_catalog import (
    CommercialContractStatus,
    WorkspaceCommercialContract,
)
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.klm_operations import ExternalActionIntent, ExternalActionIntentStatus
from app.models.subscription_access import SubscriptionAccessStatus
from app.models.workspace import Workspace
from app.schemas.local_billing import (
    LocalBillingCancellationMode,
    LocalBillingCommandResult,
    LocalBillingIntentRead,
    LocalBillingPortalRead,
    LocalBillingQuoteRead,
    LocalBillingSubscriptionRead,
    LocalPlanCatalogItemRead,
    LocalPlanCatalogUpdate,
    LocalPlanEntitlementRead,
)
from app.schemas.subscription_access import SubscriptionAccessDecision
from app.services.commercial_catalog_service import CommercialCatalogError
from app.services.commercial_legacy_bridge_service import (
    CanonicalContractCommand,
    CanonicalPlanDirectionError,
    CommercialBridgeError,
)
from app.services.entitlement_service import (
    FEATURE_ENTITLEMENTS,
    EntitlementNotConfiguredError,
    EntitlementResolver,
)
from app.services.subscription_access_policy import (
    SubscriptionAccessIdempotencyConflictError,
    SubscriptionAccessInvalidTransitionError,
    SubscriptionAccessNotFoundError,
    SubscriptionAccessPolicy,
    SubscriptionAccessPolicyError,
    SubscriptionAccessTimeConflictError,
)

LOCAL_BILLING_PROVIDER = "local_billing_simulator"
LOCAL_BILLING_ACTION_PREFIX = "local_billing."
LOCAL_BILLING_EFFECTIVE_AT_FUTURE_TOLERANCE = timedelta(seconds=30)
_RESPONSE_SNAPSHOT_KEY = "_localBillingResponseSnapshot"
_FIRST_SUBSCRIPTION_LOCKS: WeakValueDictionary[UUID, asyncio.Lock] = WeakValueDictionary()
_FIRST_SUBSCRIPTION_LOCKS_GUARD = Lock()


class LocalBillingError(RuntimeError):
    """Base error for local billing operations."""


class LocalBillingNotFoundError(LocalBillingError):
    pass


class LocalBillingConflictError(LocalBillingError):
    pass


class LocalBillingDisabledError(LocalBillingError):
    pass


class LocalBillingStateError(LocalBillingConflictError):
    pass


class LocalBillingDirectionError(LocalBillingConflictError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value is not None else None


def _period_end(start: datetime, interval: BillingInterval) -> datetime:
    days = 365 if interval == BillingInterval.annually else 30
    return start + timedelta(days=days)


def _price(plan: Plan, interval: BillingInterval) -> int:
    if interval == BillingInterval.annually:
        return plan.price_annually_cents
    return plan.price_monthly_cents


def _first_subscription_lock(workspace_id: UUID) -> asyncio.Lock:
    with _FIRST_SUBSCRIPTION_LOCKS_GUARD:
        lock = _FIRST_SUBSCRIPTION_LOCKS.get(workspace_id)
        if lock is None:
            lock = asyncio.Lock()
            _FIRST_SUBSCRIPTION_LOCKS[workspace_id] = lock
        return lock


_DEFAULT_PLANS: Mapping[PlanTier, dict[str, Any]] = {
    PlanTier.free: {
        "name": "Free",
        "description": "Local evaluation plan with a bounded trial footprint.",
        "monthly": 0,
        "annually": 0,
        "max_matters": 50,
        "sort_order": 0,
        "entitlements": {
            EntitlementKey.tokens: 100_000,
            EntitlementKey.ai_executions: 100,
            EntitlementKey.ai_conversations: 50,
            EntitlementKey.audio_minutes: 10,
            EntitlementKey.storage_bytes: 1_073_741_824,
            EntitlementKey.files: 100,
            EntitlementKey.users: 1,
            EntitlementKey.whatsapp_numbers: 1,
            EntitlementKey.agents: 1,
            EntitlementKey.legal_areas: 1,
            EntitlementKey.automations: 2,
            # Módulo processual contratável à parte (add-on, não core) — 0
            # em todos os planos base do catálogo local; ligado por override
            # de conta ou plano dedicado.
            EntitlementKey.monitored_processes: 0,
            EntitlementKey.digital_signature: False,
            EntitlementKey.calendar: False,
            EntitlementKey.email: False,
            EntitlementKey.webhooks: False,
            EntitlementKey.advanced_audit: False,
            EntitlementKey.success_manager: False,
        },
    },
    PlanTier.starter: {
        "name": "Starter",
        "description": "Local starter catalog item for small legal teams.",
        "monthly": 29_700,
        "annually": 285_120,
        "max_matters": 200,
        "sort_order": 1,
        "entitlements": {
            EntitlementKey.tokens: 500_000,
            EntitlementKey.ai_executions: 500,
            EntitlementKey.ai_conversations: 200,
            EntitlementKey.audio_minutes: 20,
            EntitlementKey.storage_bytes: 5_368_709_120,
            EntitlementKey.files: 1_000,
            EntitlementKey.users: 3,
            EntitlementKey.whatsapp_numbers: 1,
            EntitlementKey.agents: 2,
            EntitlementKey.legal_areas: 1,
            EntitlementKey.automations: 10,
            EntitlementKey.monitored_processes: 0,
            EntitlementKey.digital_signature: False,
            EntitlementKey.calendar: False,
            EntitlementKey.email: False,
            EntitlementKey.webhooks: False,
            EntitlementKey.advanced_audit: False,
            EntitlementKey.success_manager: False,
        },
    },
    PlanTier.pro: {
        "name": "Pro",
        "description": "Local professional catalog item for growing legal operations.",
        "monthly": 69_700,
        "annually": 669_120,
        "max_matters": None,
        "sort_order": 2,
        "entitlements": {
            EntitlementKey.tokens: 2_000_000,
            EntitlementKey.ai_executions: 2_000,
            EntitlementKey.ai_conversations: 1_000,
            EntitlementKey.audio_minutes: 40,
            EntitlementKey.storage_bytes: 26_843_545_600,
            EntitlementKey.files: 5_000,
            EntitlementKey.users: 10,
            EntitlementKey.whatsapp_numbers: 3,
            EntitlementKey.agents: 5,
            EntitlementKey.legal_areas: 4,
            EntitlementKey.automations: 50,
            EntitlementKey.monitored_processes: 0,
            EntitlementKey.digital_signature: True,
            EntitlementKey.calendar: True,
            EntitlementKey.email: True,
            EntitlementKey.webhooks: False,
            EntitlementKey.advanced_audit: True,
            EntitlementKey.success_manager: False,
        },
    },
    PlanTier.business: {
        "name": "Business",
        "description": "Local business catalog item with expanded team quotas.",
        "monthly": 149_700,
        "annually": 1_437_120,
        "max_matters": None,
        "sort_order": 3,
        "entitlements": {
            EntitlementKey.tokens: 10_000_000,
            EntitlementKey.ai_executions: 10_000,
            EntitlementKey.ai_conversations: 5_000,
            EntitlementKey.audio_minutes: 90,
            EntitlementKey.storage_bytes: 107_374_182_400,
            EntitlementKey.files: 20_000,
            EntitlementKey.users: 25,
            EntitlementKey.whatsapp_numbers: 10,
            EntitlementKey.agents: 15,
            EntitlementKey.legal_areas: None,
            EntitlementKey.automations: 200,
            EntitlementKey.monitored_processes: 0,
            EntitlementKey.digital_signature: True,
            EntitlementKey.calendar: True,
            EntitlementKey.email: True,
            EntitlementKey.webhooks: True,
            EntitlementKey.advanced_audit: True,
            EntitlementKey.success_manager: True,
        },
    },
    PlanTier.enterprise: {
        "name": "Enterprise",
        "description": "Editable local enterprise catalog item.",
        "monthly": 0,
        "annually": 0,
        "max_matters": None,
        "sort_order": 4,
        "entitlements": {
            EntitlementKey.tokens: None,
            EntitlementKey.ai_executions: None,
            EntitlementKey.ai_conversations: None,
            EntitlementKey.audio_minutes: None,
            EntitlementKey.storage_bytes: None,
            EntitlementKey.files: None,
            EntitlementKey.users: None,
            EntitlementKey.whatsapp_numbers: None,
            EntitlementKey.agents: None,
            EntitlementKey.legal_areas: None,
            EntitlementKey.automations: None,
            EntitlementKey.monitored_processes: 0,
            EntitlementKey.digital_signature: True,
            EntitlementKey.calendar: True,
            EntitlementKey.email: True,
            EntitlementKey.webhooks: True,
            EntitlementKey.advanced_audit: True,
            EntitlementKey.success_manager: True,
        },
    },
}


def _complete_legacy_entitlement_defaults() -> None:
    """Keep legacy plans readable without silently granting new capabilities.

    The canonical multiproduct catalog owns new sales. Legacy plans still need
    one explicit row for every typed entitlement so the resolver can fail
    closed deterministically during the expand/contract migration. Missing
    feature gates become ``False``; missing quantitative limits become ``0``.
    """

    for plan_definition in _DEFAULT_PLANS.values():
        entitlements = plan_definition["entitlements"]
        for key in EntitlementKey:
            entitlements.setdefault(key, False if key in FEATURE_ENTITLEMENTS else 0)


_complete_legacy_entitlement_defaults()

_DEFAULT_COMPATIBILITY_FEATURES: Mapping[PlanTier, dict[str, Any]] = {
    PlanTier.free: {
        "digitalSignature": False,
        "calendar": False,
        "email": False,
        "webhooks": False,
        "advancedAudit": False,
        "successManager": False,
        "supportTier": "community",
        "integrations": [],
        "maxWorkspaces": 1,
        "maxAudioMinutesPerMonth": 10,
        "maxFilesPerConversation": 5,
        "maxOperationalRecipients": 1,
        "maxCampaignRecipientsPerMonth": None,
    },
    PlanTier.starter: {
        "digitalSignature": False,
        "calendar": False,
        "email": False,
        "webhooks": False,
        "advancedAudit": False,
        "successManager": False,
        "supportTier": "standard",
        "integrations": ["whatsapp", "transcricao", "mensagens_agendadas"],
        "maxWorkspaces": 1,
        "maxAudioMinutesPerMonth": 20,
        "maxFilesPerConversation": 10,
        "maxOperationalRecipients": 2,
        "maxCampaignRecipientsPerMonth": None,
    },
    PlanTier.pro: {
        "digitalSignature": True,
        "calendar": True,
        "email": True,
        "webhooks": False,
        "advancedAudit": True,
        "successManager": False,
        "supportTier": "priority",
        "integrations": ["whatsapp", "zapsign", "email", "calendar", "advbox"],
        "maxWorkspaces": 3,
        "maxAudioMinutesPerMonth": 40,
        "maxFilesPerConversation": None,
        "maxOperationalRecipients": 6,
        "maxCampaignRecipientsPerMonth": None,
    },
    PlanTier.business: {
        "digitalSignature": True,
        "calendar": True,
        "email": True,
        "webhooks": True,
        "advancedAudit": True,
        "successManager": True,
        "supportTier": "exclusive",
        "integrations": [
            "whatsapp",
            "zapsign",
            "email",
            "calendar",
            "advbox",
            "webhooks",
        ],
        "maxWorkspaces": 4,
        "maxAudioMinutesPerMonth": 90,
        "maxFilesPerConversation": None,
        "maxOperationalRecipients": None,
        "maxCampaignRecipientsPerMonth": None,
    },
    PlanTier.enterprise: {
        "digitalSignature": True,
        "calendar": True,
        "email": True,
        "webhooks": True,
        "advancedAudit": True,
        "successManager": True,
        "supportTier": "exclusive",
        "integrations": [
            "whatsapp",
            "zapsign",
            "email",
            "calendar",
            "advbox",
            "webhooks",
        ],
        "maxWorkspaces": None,
        "maxAudioMinutesPerMonth": None,
        "maxFilesPerConversation": None,
        "maxOperationalRecipients": None,
        "maxCampaignRecipientsPerMonth": None,
    },
}

_LEGACY_FEATURE_KEYS: Mapping[EntitlementKey, tuple[str, ...]] = {
    EntitlementKey.ai_executions: (
        "maxAiExecutionsPerMonth",
        "max_ai_executions_per_month",
    ),
    EntitlementKey.ai_conversations: (
        "maxAiConversationsPerMonth",
        "max_ai_conversations_per_month",
    ),
    EntitlementKey.audio_minutes: (
        "maxAudioMinutesPerMonth",
        "max_audio_minutes_per_month",
    ),
    EntitlementKey.storage_bytes: ("maxStorageBytes", "max_storage_bytes"),
    EntitlementKey.files: ("maxFilesPerMonth", "max_files_per_month"),
    EntitlementKey.whatsapp_numbers: (
        "maxConnectedWhatsapps",
        "max_connected_whatsapps",
    ),
    EntitlementKey.legal_areas: ("maxLegalAreas", "max_legal_areas"),
    EntitlementKey.automations: ("automationLimit", "automation_limit"),
}

_LEGACY_FEATURE_FLAG_KEYS: Mapping[EntitlementKey, tuple[str, ...]] = {
    EntitlementKey.digital_signature: ("digitalSignature", "digital_signature"),
    EntitlementKey.calendar: ("calendar",),
    EntitlementKey.email: ("email",),
    EntitlementKey.webhooks: ("webhooks",),
    EntitlementKey.advanced_audit: ("advancedAudit", "advanced_audit"),
    EntitlementKey.success_manager: ("successManager", "success_manager"),
}

class LocalBillingService:
    """Owns local billing state and leaves transaction boundaries to the caller."""

    PLAN_ALIASES: Mapping[str, PlanTier] = {
        "basic": PlanTier.starter,
        "starter": PlanTier.starter,
        "start": PlanTier.starter,
        "crm_start": PlanTier.starter,
        "pro": PlanTier.pro,
        "advanced": PlanTier.pro,
        "crm_advanced": PlanTier.pro,
        "scale": PlanTier.business,
        "business": PlanTier.business,
        "evolution": PlanTier.business,
        "crm_evolution": PlanTier.business,
        "free": PlanTier.free,
        "enterprise": PlanTier.enterprise,
    }

    def __init__(
        self,
        db: AsyncSession,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.clock = clock or (lambda: datetime.now(UTC))
        self.access_policy = SubscriptionAccessPolicy(db, clock=self.clock)
        self.entitlement_resolver = EntitlementResolver(db)

    def _now(self) -> datetime:
        return _utc(self.clock())

    def _requested_effective_at(
        self,
        effective_at: datetime | None,
        *,
        server_now: datetime | None = None,
    ) -> datetime | None:
        if effective_at is None:
            return None
        trusted_now = _utc(server_now) if server_now is not None else self._now()
        requested = _utc(effective_at)
        if requested > trusted_now + LOCAL_BILLING_EFFECTIVE_AT_FUTURE_TOLERANCE:
            raise ValueError(
                "effective_at cannot be more than 30 seconds in the future."
            )
        return requested

    @staticmethod
    def _semantic_effective_at(
        requested_effective_at: datetime | None,
        *,
        server_now: datetime,
    ) -> datetime:
        if requested_effective_at is None:
            return _utc(server_now)
        return min(_utc(requested_effective_at), _utc(server_now))

    def _effective_now(self, effective_at: datetime | None) -> datetime:
        server_now = self._now()
        requested = self._requested_effective_at(
            effective_at,
            server_now=server_now,
        )
        return self._semantic_effective_at(requested, server_now=server_now)

    @staticmethod
    def _assert_mutation_enabled() -> None:
        if settings.is_production:
            raise LocalBillingDisabledError(
                "Local billing mutations are disabled in production."
            )

    @classmethod
    def normalize_plan_tier(cls, value: PlanTier | str) -> PlanTier:
        if isinstance(value, PlanTier):
            return value
        normalized = value.strip().lower()
        alias = cls.PLAN_ALIASES.get(normalized)
        if alias is not None:
            return alias
        try:
            return PlanTier(normalized)
        except ValueError as error:
            raise LocalBillingNotFoundError(f"Unknown local plan: {value}.") from error

    async def ensure_catalog(self) -> list[Plan]:
        """Seed only missing catalog rows and typed entitlements."""

        # Once the canonical catalog exists, the legacy table is a one-way
        # compatibility projection.  It must never reseed old prices over the
        # immutable commercial revisions.
        from app.models.commercial_catalog import CommercialOffer
        from app.services.commercial_legacy_bridge_service import (
            LegacyCommercialProjection,
        )

        canonical_count = int(
            await self.db.scalar(select(func.count(CommercialOffer.id))) or 0
        )
        if canonical_count:
            projected = await LegacyCommercialProjection(self.db).project_catalog()
            return sorted(
                projected.values(),
                key=lambda item: (item.sort_order, item.tier.value),
            )

        if settings.is_production:
            return list(
                (
                    await self.db.scalars(
                        select(Plan).order_by(Plan.sort_order.asc(), Plan.tier.asc())
                    )
                ).all()
            )

        plans: list[Plan] = []
        for tier, default in _DEFAULT_PLANS.items():
            plan = await self.db.scalar(select(Plan).where(Plan.tier == tier))
            created = plan is None
            if plan is None:
                plan = Plan(
                    name=default["name"],
                    tier=tier,
                    description=default["description"],
                    price_monthly_cents=default["monthly"],
                    price_annually_cents=default["annually"],
                    max_users=default["entitlements"][EntitlementKey.users],
                    max_matters=default["max_matters"],
                    max_tokens_per_month=default["entitlements"][EntitlementKey.tokens],
                    max_ai_agents=default["entitlements"][EntitlementKey.agents],
                    features=self._legacy_features(tier, default["entitlements"]),
                    is_active=True,
                    is_public=tier != PlanTier.enterprise,
                    sort_order=default["sort_order"],
                )
                try:
                    async with self.db.begin_nested():
                        self.db.add(plan)
                        await self.db.flush()
                except IntegrityError:
                    plan = await self.db.scalar(select(Plan).where(Plan.tier == tier))
                    if plan is None:
                        raise
            entitlement_defaults = (
                default["entitlements"]
                if created
                else self._existing_entitlement_defaults(plan, default["entitlements"])
            )
            compatibility = self._legacy_features(tier, entitlement_defaults)
            features = dict(plan.features or {})
            for key, value in compatibility.items():
                features.setdefault(key, value)
            if features != (plan.features or {}):
                plan.features = features
            await self._ensure_entitlements(plan, entitlement_defaults)
            plans.append(plan)
        return plans

    async def list_catalog(self, *, public_only: bool = False) -> list[LocalPlanCatalogItemRead]:
        query = select(Plan).order_by(Plan.sort_order.asc(), Plan.tier.asc())
        if public_only:
            query = query.where(Plan.is_public.is_(True), Plan.is_active.is_(True))
        plans = list((await self.db.scalars(query)).all())
        entitlement_rows = list(
            (
                await self.db.scalars(
                    select(PlanEntitlement).where(
                        PlanEntitlement.plan_id.in_([plan.id for plan in plans])
                    )
                )
            ).all()
        ) if plans else []
        grouped: dict[UUID, list[PlanEntitlement]] = {}
        for row in entitlement_rows:
            grouped.setdefault(row.plan_id, []).append(row)
        return [self._catalog_item(plan, grouped.get(plan.id, [])) for plan in plans]

    async def update_catalog_item(
        self,
        tier: PlanTier | str,
        payload: LocalPlanCatalogUpdate,
    ) -> LocalPlanCatalogItemRead:
        self._assert_mutation_enabled()
        from app.models.commercial_catalog import CommercialOffer

        if await self.db.scalar(select(CommercialOffer.id).limit(1)) is not None:
            raise LocalBillingConflictError(
                "O catálogo canônico está ativo. Publique uma nova versão pelo Estúdio "
                "de Planos; a projeção legada é somente leitura."
            )
        normalized_tier = self.normalize_plan_tier(tier)
        await self.ensure_catalog()
        plan = await self.db.scalar(
            select(Plan).where(Plan.tier == normalized_tier).with_for_update()
        )
        if plan is None:
            raise LocalBillingNotFoundError(f"Local plan {normalized_tier.value} not found.")

        fields = payload.model_fields_set
        scalar_updates = {
            "name": payload.name,
            "description": payload.description,
            "price_monthly_cents": payload.price_monthly_cents,
            "price_annually_cents": payload.price_annually_cents,
            "max_matters": payload.max_matters,
            "is_active": payload.is_active,
            "is_public": payload.is_public,
            "sort_order": payload.sort_order,
        }
        for field_name, value in scalar_updates.items():
            if field_name in fields:
                if field_name != "description" and field_name != "max_matters" and value is None:
                    raise ValueError(f"{field_name} cannot be null.")
                setattr(plan, field_name, value)

        if payload.entitlements is not None:
            seen: set[EntitlementKey] = set()
            for item in payload.entitlements:
                if item.entitlement in seen:
                    raise ValueError(
                        f"Duplicate entitlement: {item.entitlement.value}."
                    )
                seen.add(item.entitlement)
                await self._upsert_entitlement(
                    plan,
                    item.entitlement,
                    enabled=item.enabled,
                    limit_quantity=item.limit_quantity,
                    hard_limit=item.hard_limit,
                )
        await self.db.flush()
        rows = list(
            (
                await self.db.scalars(
                    select(PlanEntitlement).where(PlanEntitlement.plan_id == plan.id)
                )
            ).all()
        )
        return self._catalog_item(plan, rows)

    async def quote(
        self,
        plan_tier: PlanTier | str,
        interval: BillingInterval,
    ) -> LocalBillingQuoteRead:
        plan = await self._plan(plan_tier, require_public=True)
        rows = list(
            (
                await self.db.scalars(
                    select(PlanEntitlement).where(PlanEntitlement.plan_id == plan.id)
                )
            ).all()
        )
        return LocalBillingQuoteRead(
            plan=self._catalog_item(plan, rows),
            interval=interval,
            amount_cents=_price(plan, interval),
        )

    async def provision_trial(
        self,
        *,
        workspace_id: UUID,
        plan_tier: PlanTier | str,
        idempotency_key: str,
        actor_user_id: UUID | None,
        trial_days: int = 14,
        effective_at: datetime | None = None,
    ) -> LocalBillingCommandResult:
        self._assert_mutation_enabled()
        if not 1 <= trial_days <= 90:
            raise ValueError("trial_days must be between 1 and 90.")
        request_now = self._now()
        requested_effective_at = self._requested_effective_at(
            effective_at,
            server_now=request_now,
        )
        provisional_now = self._semantic_effective_at(
            requested_effective_at,
            server_now=request_now,
        )
        try:
            await CanonicalContractCommand(self.db).provision_trial(
                workspace_id=workspace_id,
                plan=plan_tier,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                trial_days=trial_days,
                project_legacy=False,
                effective_at=provisional_now,
            )
        except (CommercialBridgeError, CommercialCatalogError) as error:
            raise LocalBillingConflictError(str(error)) from error
        tier = self.normalize_plan_tier(plan_tier)
        payload = {
            "planTier": tier.value,
            "trialDays": trial_days,
            "effectiveAt": _iso(effective_at),
        }
        intent, replayed = await self._record_intent(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="trial",
            idempotency_key=idempotency_key,
            payload=payload,
        )
        if replayed:
            await self._locked_temporal_state(
                workspace_id,
                requested_effective_at=requested_effective_at,
            )
            return await self._command_result("trial", intent, True)

        await self.ensure_catalog()
        # The trial command already created or resolved the authoritative
        # canonical contract above.  The legacy row is now only a projection;
        # market visibility is governed by the canonical public-offer gate and
        # must not make an authorized internal trial impossible.
        plan = await self._plan(tier, require_public=False)
        provisional_trial_end = provisional_now + timedelta(days=trial_days)
        subscription, created = await self._get_or_create_subscription(
            workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.trialing,
            interval=BillingInterval.monthly,
            current_period_start=provisional_now,
            current_period_end=provisional_trial_end,
            trial_end=provisional_trial_end,
            amount_cents=0,
            currency="BRL",
        )
        server_now = self._now()
        now = self._semantic_effective_at(
            requested_effective_at,
            server_now=server_now,
        )
        trial_end = now + timedelta(days=trial_days)
        if not created:
            decision = await self.access_policy.reconcile(
                workspace_id,
                evaluated_at=server_now,
            )
            if decision.state not in {
                SubscriptionAccessStatus.trial,
                SubscriptionAccessStatus.canceled,
                SubscriptionAccessStatus.suspended,
            }:
                raise LocalBillingStateError(
                    "A trial cannot start from "
                    f"{decision.state.value if decision.state else 'missing'}."
                )
        subscription.plan_id = plan.id
        subscription.status = SubscriptionStatus.trialing
        subscription.interval = BillingInterval.monthly
        subscription.current_period_start = now
        subscription.current_period_end = trial_end
        subscription.trial_end = trial_end
        subscription.cancelled_at = None
        subscription.amount_cents = 0
        subscription.cancel_at_period_end = False

        await self._apply_local_billing_event(
            subscription=subscription,
            subscription_status=SubscriptionStatus.trialing,
            access_status=SubscriptionAccessStatus.trial,
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
            reason="local_trial_started",
            effective_at=now,
            trial_ends_at=trial_end,
        )
        await self.db.flush()
        return await self._command_result("trial", intent, False)

    async def checkout(
        self,
        *,
        workspace_id: UUID,
        plan_tier: PlanTier | str,
        interval: BillingInterval,
        idempotency_key: str,
        actor_user_id: UUID,
        effective_at: datetime | None = None,
    ) -> LocalBillingCommandResult:
        self._assert_mutation_enabled()
        from app.models.commercial_catalog import CommercialOffer

        if await self.db.scalar(select(CommercialOffer.id).limit(1)) is not None:
            raise LocalBillingDisabledError(
                "Contratação automática não está configurada. Use “Quero uma "
                "demonstração”; nenhum contrato ou cobrança foi criado."
            )
        request_now = self._now()
        requested_effective_at = self._requested_effective_at(
            effective_at,
            server_now=request_now,
        )
        provisional_now = self._semantic_effective_at(
            requested_effective_at,
            server_now=request_now,
        )
        tier = self.normalize_plan_tier(plan_tier)
        intent, replayed = await self._record_intent(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="checkout",
            idempotency_key=idempotency_key,
            payload={
                "planTier": tier.value,
                "interval": interval.value,
                "effectiveAt": _iso(effective_at),
            },
        )
        if replayed:
            await self._locked_temporal_state(
                workspace_id,
                requested_effective_at=requested_effective_at,
            )
            return await self._command_result("checkout", intent, True)

        await self.ensure_catalog()
        plan = await self._plan(tier, require_public=True)
        subscription, created = await self._get_or_create_subscription(
            workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=interval,
            current_period_start=provisional_now,
            current_period_end=_period_end(provisional_now, interval),
            trial_end=None,
            amount_cents=_price(plan, interval),
            currency="BRL",
        )
        server_now = self._now()
        now = self._semantic_effective_at(
            requested_effective_at,
            server_now=server_now,
        )
        if not created:
            await self.access_policy.reconcile(
                workspace_id,
                evaluated_at=server_now,
            )
        subscription.plan_id = plan.id
        subscription.interval = interval
        subscription.current_period_start = now
        subscription.current_period_end = _period_end(now, interval)
        subscription.trial_end = None
        subscription.cancelled_at = None
        subscription.cancel_at_period_end = False
        subscription.amount_cents = _price(plan, interval)
        subscription.currency = "BRL"
        await self._apply_local_billing_event(
            subscription=subscription,
            subscription_status=SubscriptionStatus.active,
            access_status=SubscriptionAccessStatus.active,
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
            reason="local_checkout_completed",
            effective_at=now,
        )
        await self.db.flush()
        return await self._command_result("checkout", intent, False)

    async def change_plan(
        self,
        *,
        workspace_id: UUID,
        plan_tier: PlanTier | str,
        direction: str,
        interval: BillingInterval | None,
        idempotency_key: str,
        actor_user_id: UUID,
        effective_at: datetime | None = None,
    ) -> LocalBillingCommandResult:
        self._assert_mutation_enabled()
        if direction not in {"upgrade", "downgrade"}:
            raise ValueError("direction must be upgrade or downgrade.")
        canonical_contract = await self.db.scalar(
            select(WorkspaceCommercialContract.id).where(
                WorkspaceCommercialContract.workspace_id == workspace_id
            )
        )
        request_now = self._now()
        requested_effective_at = self._requested_effective_at(
            effective_at,
            server_now=request_now,
        )
        projection_at = self._semantic_effective_at(
            requested_effective_at,
            server_now=request_now,
        )
        tier = self.normalize_plan_tier(plan_tier)
        intent, replayed = await self._record_intent(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=direction,
            idempotency_key=idempotency_key,
            payload={
                "planTier": tier.value,
                "interval": interval.value if interval else None,
                "effectiveAt": _iso(effective_at),
            },
        )
        if canonical_contract is not None:
            if replayed:
                return await self._command_result(direction, intent, True)
            if interval is BillingInterval.annually:
                raise LocalBillingConflictError(
                    "Novas alterações anuais permanecem indisponíveis no catálogo canônico."
                )
            try:
                await CanonicalContractCommand(self.db).change_core_plan(
                    workspace_id=workspace_id,
                    plan=tier,
                    direction=direction,
                    actor_user_id=actor_user_id,
                    idempotency_key=idempotency_key,
                    reason=f"Alteração {direction} solicitada pelo workspace.",
                    project_legacy=True,
                    effective_at=projection_at,
                )
            except CanonicalPlanDirectionError as error:
                raise LocalBillingDirectionError(str(error)) from error
            except (CommercialBridgeError, CommercialCatalogError) as error:
                raise LocalBillingConflictError(str(error)) from error
            await self.db.flush()
            return await self._command_result(direction, intent, False)

        subscription, decision, now, _ = await self._locked_temporal_state(
            workspace_id,
            requested_effective_at=requested_effective_at,
        )
        if replayed:
            return await self._command_result(direction, intent, True)
        if self._is_canceled(subscription, decision):
            return await self._command_result(direction, intent, False)
        if subscription.cancel_at_period_end:
            raise LocalBillingStateError(
                "Plan changes are blocked while cancellation at period end is pending."
            )
        current_plan = await self.db.get(Plan, subscription.plan_id)
        await self.ensure_catalog()
        # When a canonical contract exists the offer may intentionally remain
        # internal until unit-economics evidence passes.  The already
        # authorized lifecycle command may still refresh its legacy
        # projection without advertising the offer publicly.
        target_plan = await self._plan(
            tier,
            require_public=canonical_contract is None,
        )
        if current_plan is None:
            raise LocalBillingNotFoundError("Current local plan was not found.")
        if target_plan.id == current_plan.id:
            raise LocalBillingDirectionError("The subscription already uses this plan.")
        current_rank = (
            current_plan.sort_order,
            current_plan.price_monthly_cents,
            current_plan.price_annually_cents,
        )
        target_rank = (
            target_plan.sort_order,
            target_plan.price_monthly_cents,
            target_plan.price_annually_cents,
        )
        if target_rank == current_rank:
            raise LocalBillingDirectionError(
                "The current and target plans have the same catalog rank."
            )
        is_upgrade = target_rank > current_rank
        if (direction == "upgrade") != is_upgrade:
            raise LocalBillingDirectionError(
                f"Plan {target_plan.tier.value} is not a valid {direction} from "
                f"{current_plan.tier.value}."
            )
        if decision.state not in {
            SubscriptionAccessStatus.trial,
            SubscriptionAccessStatus.active,
        }:
            raise LocalBillingStateError("Plan changes require a trial or active subscription.")

        subscription.plan_id = target_plan.id
        subscription.interval = interval or subscription.interval
        if decision.state == SubscriptionAccessStatus.active:
            subscription.current_period_start = now
            subscription.current_period_end = _period_end(now, subscription.interval)
            subscription.amount_cents = _price(target_plan, subscription.interval)
        else:
            subscription.amount_cents = 0
        await self._transition(
            workspace_id,
            decision.state,
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
            reason=f"local_plan_{direction}",
            effective_at=now,
            trial_ends_at=decision.trial_ends_at,
            grace_ends_at=decision.grace_ends_at,
        )
        await self.db.flush()
        return await self._command_result(direction, intent, False)

    async def start_dunning(
        self,
        *,
        workspace_id: UUID,
        grace_days: int,
        idempotency_key: str,
        actor_user_id: UUID,
        effective_at: datetime | None = None,
    ) -> LocalBillingCommandResult:
        self._assert_mutation_enabled()
        if not 1 <= grace_days <= 30:
            raise ValueError("grace_days must be between 1 and 30.")
        requested_effective_at = self._requested_effective_at(effective_at)
        intent, replayed = await self._record_intent(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="dunning",
            idempotency_key=idempotency_key,
            payload={"graceDays": grace_days, "effectiveAt": _iso(effective_at)},
        )
        subscription, decision, now, _ = await self._locked_temporal_state(
            workspace_id,
            requested_effective_at=requested_effective_at,
        )
        if replayed:
            return await self._command_result("dunning", intent, True)
        if self._is_canceled(subscription, decision):
            return await self._command_result("dunning", intent, False)
        await self._transition_canonical_contract_if_present(
            workspace_id=workspace_id,
            target_status=CommercialContractStatus.past_due,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            reason="local_dunning_started",
            effective_at=now,
        )
        await self._apply_local_billing_event(
            subscription=subscription,
            subscription_status=SubscriptionStatus.past_due,
            access_status=SubscriptionAccessStatus.grace,
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
            reason="local_dunning_started",
            effective_at=now,
            grace_ends_at=now + timedelta(days=grace_days),
        )
        await self.db.flush()
        return await self._command_result("dunning", intent, False)

    async def suspend(
        self,
        *,
        workspace_id: UUID,
        idempotency_key: str,
        actor_user_id: UUID,
        reason: str | None = None,
        effective_at: datetime | None = None,
    ) -> LocalBillingCommandResult:
        return await self._status_command(
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
            action="suspension",
            to_state=SubscriptionAccessStatus.suspended,
            subscription_status=SubscriptionStatus.unpaid,
            reason=reason or "local_subscription_suspended",
            effective_at=effective_at,
        )

    async def reactivate(
        self,
        *,
        workspace_id: UUID,
        idempotency_key: str,
        actor_user_id: UUID,
        reason: str | None = None,
        effective_at: datetime | None = None,
    ) -> LocalBillingCommandResult:
        self._assert_mutation_enabled()
        requested_effective_at = self._requested_effective_at(effective_at)
        normalized_reason = (reason or "local_subscription_reactivated").strip()
        if not normalized_reason:
            raise ValueError("reason must not be empty.")
        intent, replayed = await self._record_intent(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="reactivation",
            idempotency_key=idempotency_key,
            payload={
                "toState": SubscriptionAccessStatus.active.value,
                "reason": normalized_reason,
                "effectiveAt": _iso(effective_at),
            },
        )
        subscription, _, now, _ = await self._locked_temporal_state(
            workspace_id,
            requested_effective_at=requested_effective_at,
        )
        if replayed:
            return await self._command_result("reactivation", intent, True)
        await self._transition_canonical_contract_if_present(
            workspace_id=workspace_id,
            target_status=CommercialContractStatus.active,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            reason=normalized_reason,
            effective_at=now,
        )
        subscription.cancelled_at = None
        subscription.cancel_at_period_end = False
        subscription.trial_end = None
        subscription.current_period_start = now
        subscription.current_period_end = _period_end(now, subscription.interval)
        plan = await self.db.get(Plan, subscription.plan_id)
        if plan is not None:
            subscription.amount_cents = _price(plan, subscription.interval)
        await self._apply_local_billing_event(
            subscription=subscription,
            subscription_status=SubscriptionStatus.active,
            access_status=SubscriptionAccessStatus.active,
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
            reason=normalized_reason,
            effective_at=now,
        )
        await self.db.flush()
        return await self._command_result("reactivation", intent, False)

    async def cancel(
        self,
        *,
        workspace_id: UUID,
        idempotency_key: str,
        actor_user_id: UUID,
        mode: LocalBillingCancellationMode = LocalBillingCancellationMode.period_end,
        reason: str | None = None,
        effective_at: datetime | None = None,
    ) -> LocalBillingCommandResult:
        self._assert_mutation_enabled()
        requested_effective_at = self._requested_effective_at(effective_at)
        normalized_mode = LocalBillingCancellationMode(mode)
        normalized_reason = (reason or "").strip()
        if normalized_mode == LocalBillingCancellationMode.immediate and not normalized_reason:
            raise ValueError("Immediate cancellation requires a non-empty reason.")
        intent_reason = normalized_reason or "local_subscription_cancellation_scheduled"
        intent, replayed = await self._record_intent(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="cancellation",
            idempotency_key=idempotency_key,
            payload={
                "mode": normalized_mode.value,
                "reason": normalized_reason or None,
                "effectiveAt": _iso(effective_at),
            },
        )
        subscription, decision, now, server_now = await self._locked_temporal_state(
            workspace_id,
            requested_effective_at=requested_effective_at,
        )
        if replayed:
            return await self._command_result("cancellation", intent, True)
        if self._is_canceled(subscription, decision):
            return await self._command_result("cancellation", intent, False)
        period_end = (
            _utc(subscription.current_period_end)
            if subscription.current_period_end is not None
            else None
        )
        schedule_at_period_end = (
            normalized_mode == LocalBillingCancellationMode.period_end
            and subscription.status == SubscriptionStatus.active
            and subscription.amount_cents > 0
            and period_end is not None
            and period_end > server_now
        )
        if schedule_at_period_end:
            if await self._canonical_contract_exists(workspace_id):
                raise LocalBillingDisabledError(
                    "O agendamento de cancelamento do contrato canônico ainda não "
                    "possui um fluxo interno versionado. Nenhuma alteração foi aplicada."
                )
            if decision.state is None:
                raise LocalBillingStateError("The subscription access state is missing.")
            subscription.cancel_at_period_end = True
            subscription.cancelled_at = None
            await self._apply_local_billing_event(
                subscription=subscription,
                subscription_status=subscription.status,
                access_status=decision.state,
                idempotency_key=idempotency_key,
                actor_user_id=actor_user_id,
                reason=intent_reason,
                effective_at=now,
                trial_ends_at=decision.trial_ends_at,
                grace_ends_at=decision.grace_ends_at,
            )
        else:
            cancellation_effective_at = (
                server_now
                if normalized_mode == LocalBillingCancellationMode.period_end
                and period_end is not None
                and period_end <= server_now
                else now
            )
            subscription.cancel_at_period_end = False
            subscription.cancelled_at = cancellation_effective_at
            await self._transition_canonical_contract_if_present(
                workspace_id=workspace_id,
                target_status=CommercialContractStatus.cancelled,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                reason=normalized_reason or "local_subscription_canceled",
                effective_at=cancellation_effective_at,
            )
            await self._apply_local_billing_event(
                subscription=subscription,
                subscription_status=SubscriptionStatus.cancelled,
                access_status=SubscriptionAccessStatus.canceled,
                idempotency_key=idempotency_key,
                actor_user_id=actor_user_id,
                reason=normalized_reason or "local_subscription_canceled",
                effective_at=cancellation_effective_at,
            )
        await self.db.flush()
        return await self._command_result("cancellation", intent, False)

    async def create_export_intent(
        self,
        *,
        workspace_id: UUID,
        idempotency_key: str,
        actor_user_id: UUID,
        export_format: str,
        scopes: list[str],
        reason: str | None = None,
        effective_at: datetime | None = None,
    ) -> LocalBillingCommandResult:
        self._assert_mutation_enabled()
        self._effective_now(effective_at)
        normalized_scopes = sorted({scope.strip() for scope in scopes if scope.strip()})
        if not normalized_scopes:
            raise ValueError("At least one export scope is required.")
        intent, replayed = await self._record_intent(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="data_export",
            idempotency_key=idempotency_key,
            payload={
                "format": export_format,
                "scopes": normalized_scopes,
                "effectiveAt": _iso(effective_at),
                "executionState": "intent_only",
                "externalEffects": 0,
            },
            status=ExternalActionIntentStatus.awaiting_approval,
            reason=reason or "Local export intent recorded; no export was executed.",
        )
        return await self._command_result("data_export", intent, replayed)

    async def create_closure_intent(
        self,
        *,
        workspace_id: UUID,
        idempotency_key: str,
        actor_user_id: UUID,
        confirm_closure: bool,
        reason: str,
        effective_at: datetime | None = None,
    ) -> LocalBillingCommandResult:
        self._assert_mutation_enabled()
        self._effective_now(effective_at)
        if not confirm_closure:
            raise ValueError("confirm_closure must be true.")
        intent, replayed = await self._record_intent(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="account_closure",
            idempotency_key=idempotency_key,
            payload={
                "confirmed": True,
                "effectiveAt": _iso(effective_at),
                "executionState": "intent_only",
                "externalEffects": 0,
            },
            status=ExternalActionIntentStatus.awaiting_approval,
            reason=reason,
        )
        return await self._command_result("account_closure", intent, replayed)

    async def get_portal(self, workspace_id: UUID) -> LocalBillingPortalRead:
        await set_workspace_context(self.db, workspace_id)
        decision = await self.access_policy.decide(workspace_id)
        subscription = await self._subscription(workspace_id, include_plan=True)

        entitlements: list[LocalPlanEntitlementRead] = []
        if subscription is not None:
            for key in EntitlementKey:
                try:
                    resolved = await self.entitlement_resolver.resolve(workspace_id, key)
                except EntitlementNotConfiguredError:
                    entitlements.append(
                        LocalPlanEntitlementRead(
                            entitlement=key,
                            enabled=False,
                            limit_quantity=None,
                            hard_limit=True,
                            source="not_configured",
                            configured=False,
                        )
                    )
                else:
                    entitlements.append(
                        LocalPlanEntitlementRead(
                            entitlement=key,
                            enabled=resolved.enabled,
                            limit_quantity=resolved.limit_quantity,
                            hard_limit=resolved.hard_limit,
                            source=resolved.source.value,
                        )
                    )

        intents = await self.list_intents(workspace_id)
        subscription_read = (
            self._subscription_read(subscription)
            if subscription is not None and subscription.plan is not None
            else None
        )
        return LocalBillingPortalRead(
            workspace_id=workspace_id,
            subscription=subscription_read,
            access=decision,
            entitlements=entitlements,
            recent_intents=intents,
        )

    async def list_intents(
        self,
        workspace_id: UUID,
        *,
        limit: int = 50,
    ) -> list[LocalBillingIntentRead]:
        await set_workspace_context(self.db, workspace_id)
        rows = list(
            (
                await self.db.scalars(
                    select(ExternalActionIntent)
                    .where(
                        ExternalActionIntent.workspace_id == workspace_id,
                        ExternalActionIntent.action_type.startswith(
                            LOCAL_BILLING_ACTION_PREFIX
                        ),
                    )
                    .order_by(ExternalActionIntent.created_at.desc())
                    .limit(max(1, min(limit, 200)))
                )
            ).all()
        )
        return [self._intent_read(row) for row in rows]

    async def _locked_temporal_state(
        self,
        workspace_id: UUID,
        *,
        requested_effective_at: datetime | None,
    ) -> tuple[Subscription, SubscriptionAccessDecision, datetime, datetime]:
        """Reconcile server deadlines before applying the command's semantic time."""

        subscription = await self._required_subscription(workspace_id, for_update=True)
        server_now = self._now()
        requested_effective_at = self._requested_effective_at(
            requested_effective_at,
            server_now=server_now,
        )
        decision = await self.access_policy.reconcile(
            workspace_id,
            evaluated_at=server_now,
        )
        semantic_effective_at = self._semantic_effective_at(
            requested_effective_at,
            server_now=server_now,
        )
        return subscription, decision, semantic_effective_at, server_now

    @staticmethod
    def _is_canceled(
        subscription: Subscription,
        decision: SubscriptionAccessDecision,
    ) -> bool:
        return (
            subscription.status == SubscriptionStatus.cancelled
            or decision.state == SubscriptionAccessStatus.canceled
        )

    async def _status_command(
        self,
        *,
        workspace_id: UUID,
        idempotency_key: str,
        actor_user_id: UUID,
        action: str,
        to_state: SubscriptionAccessStatus,
        subscription_status: SubscriptionStatus,
        reason: str,
        effective_at: datetime | None,
    ) -> LocalBillingCommandResult:
        self._assert_mutation_enabled()
        requested_effective_at = self._requested_effective_at(effective_at)
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("reason must not be empty.")
        intent, replayed = await self._record_intent(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=action,
            idempotency_key=idempotency_key,
            payload={
                "toState": to_state.value,
                "reason": normalized_reason,
                "effectiveAt": _iso(effective_at),
            },
        )
        subscription, decision, now, _ = await self._locked_temporal_state(
            workspace_id,
            requested_effective_at=requested_effective_at,
        )
        if replayed:
            return await self._command_result(action, intent, True)
        if self._is_canceled(subscription, decision):
            return await self._command_result(action, intent, False)
        canonical_status = {
            SubscriptionAccessStatus.suspended: CommercialContractStatus.paused,
            SubscriptionAccessStatus.active: CommercialContractStatus.active,
            SubscriptionAccessStatus.grace: CommercialContractStatus.past_due,
            SubscriptionAccessStatus.canceled: CommercialContractStatus.cancelled,
        }.get(to_state)
        if canonical_status is not None:
            await self._transition_canonical_contract_if_present(
                workspace_id=workspace_id,
                target_status=canonical_status,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                reason=normalized_reason,
                effective_at=now,
            )
        await self._apply_local_billing_event(
            subscription=subscription,
            subscription_status=subscription_status,
            access_status=to_state,
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
            reason=normalized_reason,
            effective_at=now,
        )
        await self.db.flush()
        return await self._command_result(action, intent, False)

    async def _canonical_contract_exists(self, workspace_id: UUID) -> bool:
        return (
            await self.db.scalar(
                select(WorkspaceCommercialContract.id).where(
                    WorkspaceCommercialContract.workspace_id == workspace_id
                )
            )
            is not None
        )

    async def _transition_canonical_contract_if_present(
        self,
        *,
        workspace_id: UUID,
        target_status: CommercialContractStatus,
        actor_user_id: UUID | None,
        idempotency_key: str,
        reason: str,
        effective_at: datetime,
        trial_ends_at: datetime | None = None,
    ) -> bool:
        """Keep the canonical aggregate authoritative during legacy commands.

        All writes stay in the caller transaction.  ``project_legacy=False``
        prevents the compatibility bridge from racing the legacy command that
        follows immediately below.
        """

        if not await self._canonical_contract_exists(workspace_id):
            return False
        try:
            await CanonicalContractCommand(self.db).change_status(
                workspace_id=workspace_id,
                target_status=target_status,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                reason=reason,
                project_legacy=False,
                effective_at=effective_at,
                trial_ends_at=trial_ends_at,
            )
        except (CommercialBridgeError, CommercialCatalogError) as error:
            raise LocalBillingConflictError(str(error)) from error
        return True

    async def _apply_local_billing_event(
        self,
        *,
        subscription: Subscription,
        subscription_status: SubscriptionStatus,
        access_status: SubscriptionAccessStatus,
        idempotency_key: str,
        actor_user_id: UUID | None,
        reason: str,
        effective_at: datetime,
        trial_ends_at: datetime | None = None,
        grace_ends_at: datetime | None = None,
    ) -> None:
        """Update billing source and access projection in the caller's transaction."""

        subscription.status = subscription_status
        await self._transition(
            subscription.workspace_id,
            access_status,
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
            reason=reason,
            effective_at=effective_at,
            trial_ends_at=trial_ends_at,
            grace_ends_at=grace_ends_at,
        )

    async def _transition(
        self,
        workspace_id: UUID,
        to_state: SubscriptionAccessStatus,
        *,
        idempotency_key: str,
        actor_user_id: UUID | None,
        reason: str,
        effective_at: datetime,
        trial_ends_at: datetime | None = None,
        grace_ends_at: datetime | None = None,
    ) -> None:
        try:
            await self.access_policy.transition(
                workspace_id,
                to_state,
                idempotency_key=f"local-billing:{self._key(idempotency_key)}",
                source="local.billing",
                reason=reason,
                actor_user_id=actor_user_id,
                effective_at=effective_at,
                trial_ends_at=trial_ends_at,
                grace_ends_at=grace_ends_at,
            )
        except (
            SubscriptionAccessIdempotencyConflictError,
            SubscriptionAccessInvalidTransitionError,
            SubscriptionAccessNotFoundError,
            SubscriptionAccessTimeConflictError,
            ValueError,
        ) as error:
            raise LocalBillingStateError(str(error)) from error
        except SubscriptionAccessPolicyError as error:
            raise LocalBillingError(str(error)) from error

    async def _command_result(
        self,
        action: str,
        intent: ExternalActionIntent,
        replayed: bool,
    ) -> LocalBillingCommandResult:
        snapshot = (intent.payload or {}).get(_RESPONSE_SNAPSHOT_KEY)
        if replayed and isinstance(snapshot, dict):
            try:
                original = LocalBillingCommandResult.model_validate(snapshot)
            except ValueError as error:
                raise LocalBillingConflictError(
                    "The stored local billing replay snapshot is invalid."
                ) from error
            return original.model_copy(update={"replayed": True})

        result = LocalBillingCommandResult(
            action=action,
            replayed=replayed,
            intent=self._intent_read(intent),
            portal=await self.get_portal(intent.workspace_id),
        )
        intent.payload = {
            **self._intent_request_payload(intent),
            _RESPONSE_SNAPSHOT_KEY: result.model_dump(mode="json", by_alias=True),
        }
        await self.db.flush()
        return result

    async def _record_intent(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        action: str,
        idempotency_key: str,
        payload: dict[str, Any],
        status: ExternalActionIntentStatus = ExternalActionIntentStatus.approved_local,
        reason: str = "Local billing simulation completed with zero external effects.",
    ) -> tuple[ExternalActionIntent, bool]:
        await set_workspace_context(self.db, workspace_id)
        key = f"local-billing:{self._key(idempotency_key)}"
        action_type = f"{LOCAL_BILLING_ACTION_PREFIX}{action}"
        existing = await self.db.scalar(
            select(ExternalActionIntent).where(
                ExternalActionIntent.workspace_id == workspace_id,
                ExternalActionIntent.idempotency_key == key,
            )
        )
        if existing is not None:
            self._validate_intent_replay(
                existing,
                action_type=action_type,
                actor_user_id=actor_user_id,
                payload=payload,
                status=status,
                reason=reason,
            )
            return existing, True

        intent = ExternalActionIntent(
            workspace_id=workspace_id,
            requested_by_user_id=actor_user_id,
            action_type=action_type,
            provider=LOCAL_BILLING_PROVIDER,
            idempotency_key=key,
            status=status,
            payload=payload,
            reason=reason,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(intent)
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(ExternalActionIntent).where(
                    ExternalActionIntent.workspace_id == workspace_id,
                    ExternalActionIntent.idempotency_key == key,
                )
            )
            if existing is None:
                raise
            self._validate_intent_replay(
                existing,
                action_type=action_type,
                actor_user_id=actor_user_id,
                payload=payload,
                status=status,
                reason=reason,
            )
            return existing, True
        return intent, False

    @staticmethod
    def _intent_request_payload(intent: ExternalActionIntent) -> dict[str, Any]:
        return {
            key: value
            for key, value in (intent.payload or {}).items()
            if key != _RESPONSE_SNAPSHOT_KEY
        }

    @classmethod
    def _intent_read(cls, intent: ExternalActionIntent) -> LocalBillingIntentRead:
        return LocalBillingIntentRead.model_validate(intent).model_copy(
            update={"payload": cls._intent_request_payload(intent)}
        )

    @classmethod
    def _validate_intent_replay(
        cls,
        intent: ExternalActionIntent,
        *,
        action_type: str,
        actor_user_id: UUID | None,
        payload: dict[str, Any],
        status: ExternalActionIntentStatus,
        reason: str,
    ) -> None:
        if (
            intent.action_type != action_type
            or intent.requested_by_user_id != actor_user_id
            or cls._intent_request_payload(intent) != payload
            or intent.status != status
            or intent.provider != LOCAL_BILLING_PROVIDER
            or intent.reason != reason
        ):
            raise LocalBillingConflictError(
                "The idempotency key is already bound to another local billing request."
            )

    @staticmethod
    def _key(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("idempotency_key must not be empty.")
        if len(normalized) > 160:
            raise ValueError("idempotency_key must be at most 160 characters.")
        return normalized

    async def _plan(
        self,
        tier: PlanTier | str,
        *,
        require_public: bool,
    ) -> Plan:
        normalized = self.normalize_plan_tier(tier)
        conditions = [Plan.tier == normalized, Plan.is_active.is_(True)]
        if require_public:
            conditions.append(Plan.is_public.is_(True))
        plan = await self.db.scalar(select(Plan).where(*conditions))
        if plan is None:
            raise LocalBillingNotFoundError(
                f"Local plan {normalized.value} is unavailable."
            )
        return plan

    async def _subscription(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
        include_plan: bool = False,
    ) -> Subscription | None:
        query = select(Subscription).where(Subscription.workspace_id == workspace_id)
        if include_plan:
            query = query.options(selectinload(Subscription.plan))
        if for_update:
            query = query.with_for_update()
        return await self.db.scalar(query)

    async def _get_or_create_subscription(
        self,
        workspace_id: UUID,
        **initial_values: Any,
    ) -> tuple[Subscription, bool]:
        """Serialize the absent-row case and recover cleanly from a unique race."""

        async with _first_subscription_lock(workspace_id):
            await set_workspace_context(self.db, workspace_id)
            workspace = await self.db.scalar(
                select(Workspace.id)
                .where(Workspace.id == workspace_id)
                .with_for_update()
            )
            if workspace is None:
                raise LocalBillingNotFoundError("The billing workspace was not found.")

            existing = await self._subscription(workspace_id, for_update=True)
            if existing is not None:
                return existing, False

            subscription = Subscription(
                workspace_id=workspace_id,
                **initial_values,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(subscription)
                    await self.db.flush()
            except IntegrityError:
                existing = await self._subscription(workspace_id, for_update=True)
                if existing is None:
                    raise
                return existing, False
            return subscription, True

    async def _required_subscription(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> Subscription:
        subscription = await self._subscription(workspace_id, for_update=for_update)
        if subscription is None:
            raise LocalBillingNotFoundError("The workspace has no local subscription.")
        return subscription

    async def _ensure_entitlements(
        self,
        plan: Plan,
        defaults: Mapping[EntitlementKey, int | bool | None],
    ) -> None:
        existing = {
            row.entitlement
            for row in (
                await self.db.scalars(
                    select(PlanEntitlement).where(PlanEntitlement.plan_id == plan.id)
                )
            ).all()
        }
        for key, limit in defaults.items():
            if key in existing:
                continue
            is_feature = key in FEATURE_ENTITLEMENTS
            if isinstance(limit, bool) and not is_feature:
                raise ValueError(f"Boolean limit is invalid for {key.value}.")
            row = PlanEntitlement(
                plan_id=plan.id,
                entitlement=key,
                enabled=bool(limit) if is_feature else True,
                limit_quantity=None if is_feature else limit,
                hard_limit=True,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(row)
                    await self.db.flush()
            except IntegrityError:
                persisted = await self.db.scalar(
                    select(PlanEntitlement).where(
                        PlanEntitlement.plan_id == plan.id,
                        PlanEntitlement.entitlement == key,
                    )
                )
                if persisted is None:
                    raise

    async def _upsert_entitlement(
        self,
        plan: Plan,
        key: EntitlementKey,
        *,
        enabled: bool,
        limit_quantity: int | None,
        hard_limit: bool,
    ) -> None:
        if key in FEATURE_ENTITLEMENTS and limit_quantity is not None:
            raise ValueError(f"Feature entitlement {key.value} cannot have a quantity limit.")
        row = await self.db.scalar(
            select(PlanEntitlement)
            .where(
                PlanEntitlement.plan_id == plan.id,
                PlanEntitlement.entitlement == key,
            )
            .with_for_update()
        )
        if row is None:
            row = PlanEntitlement(plan_id=plan.id, entitlement=key)
            self.db.add(row)
        row.enabled = enabled
        row.limit_quantity = limit_quantity
        row.hard_limit = hard_limit
        self._sync_legacy_entitlement(plan, key, enabled, limit_quantity)

    @staticmethod
    def _sync_legacy_entitlement(
        plan: Plan,
        key: EntitlementKey,
        enabled: bool,
        limit: int | None,
    ) -> None:
        if key == EntitlementKey.users:
            plan.max_users = limit
        elif key == EntitlementKey.tokens:
            plan.max_tokens_per_month = limit
        elif key == EntitlementKey.agents:
            plan.max_ai_agents = limit
        feature_keys = _LEGACY_FEATURE_KEYS.get(key, ())
        if feature_keys:
            features = dict(plan.features or {})
            for feature_key in feature_keys:
                features[feature_key] = limit
            plan.features = features
        flag_keys = _LEGACY_FEATURE_FLAG_KEYS.get(key, ())
        if flag_keys:
            features = dict(plan.features or {})
            for feature_key in flag_keys:
                features[feature_key] = enabled
            plan.features = features

    @staticmethod
    def _legacy_features(
        tier: PlanTier,
        entitlements: Mapping[EntitlementKey, int | bool | None],
    ) -> dict[str, Any]:
        features = dict(_DEFAULT_COMPATIBILITY_FEATURES[tier])
        for key, feature_keys in _LEGACY_FEATURE_KEYS.items():
            for feature_key in feature_keys:
                features[feature_key] = entitlements[key]
        for key, feature_keys in _LEGACY_FEATURE_FLAG_KEYS.items():
            for feature_key in feature_keys:
                features[feature_key] = bool(entitlements[key])
        return features

    @staticmethod
    def _existing_entitlement_defaults(
        plan: Plan,
        fallbacks: Mapping[EntitlementKey, int | bool | None],
    ) -> dict[EntitlementKey, int | bool | None]:
        features = plan.features or {}
        values = dict(fallbacks)
        values[EntitlementKey.users] = plan.max_users
        values[EntitlementKey.tokens] = plan.max_tokens_per_month
        values[EntitlementKey.agents] = plan.max_ai_agents
        for key, feature_keys in _LEGACY_FEATURE_KEYS.items():
            for feature_key in feature_keys:
                if feature_key in features:
                    values[key] = features[feature_key]
                    break
        for key, feature_keys in _LEGACY_FEATURE_FLAG_KEYS.items():
            for feature_key in feature_keys:
                if feature_key in features:
                    values[key] = bool(features[feature_key])
                    break
        return values

    @staticmethod
    def _catalog_item(
        plan: Plan,
        entitlements: list[PlanEntitlement],
    ) -> LocalPlanCatalogItemRead:
        ordered = sorted(entitlements, key=lambda row: row.entitlement.value)
        return LocalPlanCatalogItemRead(
            id=plan.id,
            name=plan.name,
            tier=plan.tier,
            description=plan.description,
            price_monthly_cents=plan.price_monthly_cents,
            price_annually_cents=plan.price_annually_cents,
            max_matters=plan.max_matters,
            is_active=plan.is_active,
            is_public=plan.is_public,
            sort_order=plan.sort_order,
            entitlements=[
                LocalPlanEntitlementRead(
                    entitlement=row.entitlement,
                    enabled=row.enabled,
                    limit_quantity=row.limit_quantity,
                    hard_limit=row.hard_limit,
                )
                for row in ordered
            ],
        )

    @staticmethod
    def _subscription_read(subscription: Subscription) -> LocalBillingSubscriptionRead:
        return LocalBillingSubscriptionRead(
            id=subscription.id,
            workspace_id=subscription.workspace_id,
            plan_id=subscription.plan_id,
            plan_tier=subscription.plan.tier,
            plan_name=subscription.plan.name,
            status=subscription.status,
            interval=subscription.interval,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            trial_end=subscription.trial_end,
            cancelled_at=subscription.cancelled_at,
            cancel_at_period_end=subscription.cancel_at_period_end,
            amount_cents=subscription.amount_cents,
            currency=subscription.currency,
        )


__all__ = [
    "LOCAL_BILLING_EFFECTIVE_AT_FUTURE_TOLERANCE",
    "LOCAL_BILLING_PROVIDER",
    "LocalBillingConflictError",
    "LocalBillingDisabledError",
    "LocalBillingDirectionError",
    "LocalBillingError",
    "LocalBillingNotFoundError",
    "LocalBillingService",
    "LocalBillingStateError",
]
