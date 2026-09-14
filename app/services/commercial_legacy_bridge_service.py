"""Compatibility bridge from the canonical commercial domain to legacy billing.

The multiproduct catalog is the source of truth.  The old ``Plan`` /
``Subscription`` and ``WorkspaceSubscription`` tables remain temporary read
projections because access policy and a few older reports still consume them.
Nothing in this module derives or writes canonical state from those tables.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.platform_rbac import has_platform_permission, is_platform_root
from app.db.platform_control import (
    _set_commercial_portability_rebind_context,
    _set_commercial_portability_restore_rebind_context,
    set_platform_control_context,
)
from app.db.tenant import get_workspace_context
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.commercial_catalog import (
    CommercialBillingInterval,
    CommercialContractLineStatus,
    CommercialContractProviderBinding,
    CommercialContractStatus,
    CommercialOffer,
    CommercialOfferFamily,
    WorkspaceCommercialContract,
)
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.subscription_access import SubscriptionAccessStatus
from app.models.subscription_commercial import (
    SubscriptionEvent,
    SubscriptionEventType,
    WorkspacePlanCode,
    WorkspaceSubscription,
    WorkspaceSubscriptionStatus,
)
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.commercial_catalog import (
    CommercialAdjustmentCreateRequest,
    CommercialAdjustmentRead,
    CommercialAdjustmentUpdateRequest,
    CommercialOfferVersionRead,
    CommercialRecordRevokeRequest,
    EntitlementBonusCreateRequest,
    EntitlementBonusRead,
    EntitlementBonusUpdateRequest,
    WorkspaceContractCreateRequest,
    WorkspaceContractLineWrite,
    WorkspaceContractRead,
    WorkspaceContractRestoreRequest,
    WorkspaceContractRevisionPreviewRequest,
    WorkspaceContractRevisionRequest,
)
from app.services.commercial_catalog_service import (
    CommercialCatalogConflictError,
    CommercialCatalogNotFoundError,
    CommercialCatalogService,
    CommercialCatalogValidationError,
    canonical_sku_for_legacy_plan_key,
)
from app.services.subscription_access_policy import SubscriptionAccessPolicy


class CommercialBridgeError(RuntimeError):
    """Base class for safe compatibility failures."""


class CanonicalOfferUnavailableError(CommercialBridgeError):
    pass


class CanonicalCheckoutUnavailableError(CommercialBridgeError):
    """The CTA is a demonstration until a real billing provider is configured."""


class CanonicalPlanDirectionError(CommercialBridgeError):
    pass


class ProviderManagedContractError(CommercialBridgeError):
    """Prevent the control plane from diverging from a linked billing provider."""


_PROVIDER_MANAGED_MESSAGE = (
    "A assinatura Core está vinculada a um provedor de cobrança. Altere plano ou "
    "estado pelo fluxo do provedor; a reconciliação assinada atualizará o contrato "
    "canônico."
)


_SKU_TO_PLAN_TIER: dict[str, PlanTier] = {
    "crm_start": PlanTier.starter,
    "crm_advanced": PlanTier.pro,
    "crm_evolution": PlanTier.business,
}
_SKU_TO_WORKSPACE_PLAN: dict[str, WorkspacePlanCode] = {
    "crm_start": WorkspacePlanCode.essencial,
    "crm_advanced": WorkspacePlanCode.escritorio,
    "crm_evolution": WorkspacePlanCode.performance,
}
_CANONICAL_RANK = {
    "crm_start": 10,
    "crm_advanced": 20,
    "crm_evolution": 30,
}

# Aliases are accepted only at this deprecated boundary.  Domain services and
# persisted contracts continue to use the stable SKU.
_EXTRA_BORDER_ALIASES: dict[str, str] = {
    "essencial": "crm_start",
    "escritorio": "crm_advanced",
    "performance": "crm_evolution",
}


def canonical_sku(value: str | PlanTier) -> str:
    raw = value.value if isinstance(value, PlanTier) else str(value)
    normalized = raw.strip().lower()
    resolved = canonical_sku_for_legacy_plan_key(normalized)
    if resolved is None:
        resolved = _EXTRA_BORDER_ALIASES.get(normalized)
    if resolved not in _SKU_TO_PLAN_TIER:
        raise CanonicalOfferUnavailableError(
            "A oferta solicitada não pertence ao catálogo Core público."
        )
    return resolved


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)


def _line_is_active_and_effective(line: Any, *, at: datetime | None = None) -> bool:
    instant = _utc(at)
    starts_at = _utc(line.effective_from)
    ends_at = _utc(line.effective_until) if line.effective_until is not None else None
    return (
        line.status is CommercialContractLineStatus.active
        and starts_at <= instant
        and (ends_at is None or ends_at > instant)
    )


def _effective_core_line(
    contract: WorkspaceContractRead,
    *,
    required: bool = False,
    at: datetime | None = None,
) -> Any | None:
    matches = [
        line
        for line in contract.lines
        if line.sku in _SKU_TO_PLAN_TIER and _line_is_active_and_effective(line, at=at)
    ]
    if len(matches) > 1:
        raise CommercialCatalogConflictError(
            "Contrato possui mais de uma linha Core ativa e vigente."
        )
    if required and not matches:
        raise CommercialCatalogConflictError(
            "Contrato não possui linha Core ativa e vigente."
        )
    return matches[0] if matches else None


def _effective_lines_for_family(
    contract: WorkspaceContractRead,
    family: CommercialOfferFamily,
    *,
    at: datetime | None = None,
) -> list[Any]:
    matches = [
        line
        for line in contract.lines
        if line.offer_family is family and _line_is_active_and_effective(line, at=at)
    ]
    if len(matches) > 1:
        raise CommercialCatalogConflictError(
            f"Contrato possui mais de uma linha {family.value} ativa e vigente."
        )
    return matches


@dataclass(frozen=True, slots=True)
class CommercialRevenueRecord:
    """Provider-agnostic row used by Blackbox billing and KPI projections."""

    id: UUID
    workspace_id: UUID
    workspace_name: str | None
    sku: str
    status: str
    mrr_cents: int
    recurring_price_cents: int
    trial_ends_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    courtesy: bool
    source: str
    contract_id: UUID | None = None
    contract_version: int | None = None
    legacy_subscription_id: UUID | None = None

    @property
    def is_active(self) -> bool:
        return self.status in {"trial", "ativa", "inadimplente"}


class CanonicalOfferQuery:
    """Read current immutable offer revisions without consulting legacy plans."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _bootstrap_local_only(self) -> None:
        count = int(await self.db.scalar(select(func.count(CommercialOffer.id))) or 0)
        if count:
            return
        if settings.is_production:
            raise CanonicalOfferUnavailableError(
                "Catálogo comercial canônico não foi publicado. O cadastro foi interrompido."
            )
        # Deterministic bootstrap is permitted in local/test environments only.
        # Production publication remains a root-only deployment gate.
        await CommercialCatalogService(self.db).bootstrap_default_catalog(
            actor_user_id=None,
            idempotency_key="local-commercial-catalog-v1",
        )

    async def list_public_core(self) -> list[CommercialOfferVersionRead]:
        await self._bootstrap_local_only()
        offers, _ = await CommercialCatalogService(self.db).list_offers(
            limit=100,
            offset=0,
            public_only=True,
        )
        return [item for item in offers if item.sku in _SKU_TO_PLAN_TIER]

    async def list_active_core(self) -> list[CommercialOfferVersionRead]:
        """Return active Core offers available to authorized internal sales.

        Public visibility is a separate, stricter boundary protected by the
        unit-economics gate. Internal contracts and trials may use a reviewed
        offer while its market publication remains blocked.
        """

        await self._bootstrap_local_only()
        offers, _ = await CommercialCatalogService(self.db).list_offers(
            limit=100,
            offset=0,
            public_only=False,
        )
        return [
            item
            for item in offers
            if item.sku in _SKU_TO_PLAN_TIER and item.snapshot.is_active
        ]

    async def get(self, value: str | PlanTier) -> CommercialOfferVersionRead:
        sku = canonical_sku(value)
        for offer in await self.list_active_core():
            if offer.sku == sku:
                return offer
        raise CanonicalOfferUnavailableError(
            f"Oferta {sku} não está ativa para operação interna."
        )


class LegacyCommercialProjection:
    """Project canonical catalog/contracts into compatibility tables only."""

    def __init__(self, db: AsyncSession, *, control_plane: bool = False) -> None:
        self.db = db
        self.control_plane = control_plane

    @asynccontextmanager
    async def _access_workspace_scope(self, workspace_id: UUID):
        """Scope tenant-only access tables without permanently binding a worker.

        A Blackbox command can process more than one workspace over the lifetime
        of its SQLAlchemy session.  ``set_workspace_context`` deliberately
        forbids that for tenant requests, so the already-authorized control
        plane uses a transaction-local PostgreSQL setting and clears it before
        returning.  The command boundary establishes the attributable platform
        actor before this scope is entered.
        """

        if not self.control_plane:
            yield
            return
        bound_workspace = get_workspace_context(self.db)
        if bound_workspace is not None and bound_workspace != workspace_id:
            raise CommercialBridgeError(
                "A projeção de plataforma não pode reutilizar uma sessão tenant vinculada."
            )
        if bound_workspace == workspace_id:
            yield
            return
        if self.db.get_bind().dialect.name != "postgresql":
            yield
            return

        actor_present = bool(
            await self.db.scalar(
                text(
                    "SELECT NULLIF("
                    "current_setting('app.current_platform_actor_id', true), ''"
                    ") IS NOT NULL"
                )
            )
        )
        if not actor_present:
            raise CommercialBridgeError(
                "A projeção de plataforma exige contexto Blackbox atribuível."
            )

        await self.db.execute(
            text("SELECT set_config('app.current_workspace_id', :workspace_id, true)"),
            {"workspace_id": str(workspace_id)},
        )
        try:
            yield
        except Exception:
            # A transação chamadora deve executar rollback. O GUC é local à
            # transação e desaparece nesse rollback; tentar novo SQL aqui
            # mascararia a exceção original se o PostgreSQL já estiver abortado.
            raise
        else:
            await self.db.execute(
                text("SELECT set_config('app.current_workspace_id', '', true)")
            )

    async def project_catalog(self) -> dict[str, Plan]:
        offers = await CanonicalOfferQuery(self.db).list_active_core()
        projected: dict[str, Plan] = {}
        for offer in offers:
            tier = _SKU_TO_PLAN_TIER[offer.sku]
            snapshot = offer.snapshot
            plan = await self.db.scalar(select(Plan).where(Plan.tier == tier).with_for_update())
            if plan is None:
                plan = Plan(tier=tier)
                self.db.add(plan)
            policy = snapshot.entitlements
            plan.name = snapshot.name
            plan.description = snapshot.description
            plan.price_monthly_cents = int(snapshot.price_monthly_cents or 0)
            # Annual is deliberately unavailable for new V1 sales.  Zero here
            # means "not offered" in the deprecated schema, never a free annual plan.
            plan.price_annually_cents = int(snapshot.price_annual_cents or 0)
            plan.max_users = policy[EntitlementKey.users].limit_quantity
            plan.max_matters = None
            plan.max_tokens_per_month = policy[EntitlementKey.tokens].limit_quantity
            plan.max_ai_agents = policy[EntitlementKey.agents].limit_quantity
            plan.features = {
                "canonicalManaged": True,
                "canonicalSku": offer.sku,
                "canonicalOfferVersionId": str(offer.id),
            }
            plan.is_active = snapshot.is_active
            plan.is_public = snapshot.is_public
            plan.sort_order = snapshot.sort_order
            await self.db.flush()

            rows = {
                item.entitlement: item
                for item in (
                    await self.db.scalars(
                        select(PlanEntitlement).where(PlanEntitlement.plan_id == plan.id)
                    )
                ).all()
            }
            for key, setting in policy.items():
                row = rows.get(key)
                if row is None:
                    row = PlanEntitlement(plan_id=plan.id, entitlement=key)
                    self.db.add(row)
                row.enabled = setting.enabled
                row.limit_quantity = setting.limit_quantity
                row.hard_limit = True
            projected[offer.sku] = plan
        await self.db.flush()
        return projected

    async def project_contract(
        self,
        contract: WorkspaceContractRead,
        *,
        actor_user_id: UUID | None,
        idempotency_key: str,
        trial_ends_at: datetime | None = None,
        effective_at: datetime | None = None,
    ) -> Subscription | None:
        # A projection is part of the command that produced it.  Reusing that
        # command's effective instant keeps the compatibility access event,
        # billing periods, and canonical mutation on one timeline.  This is
        # especially important for deterministic local billing commands and
        # controlled replays; stamping the wall clock here can make the next
        # legitimate command look retroactive.
        now = _utc(effective_at) if effective_at is not None else _utc()
        core_line = _effective_core_line(contract, at=now)
        if core_line is None:
            # Legal-only contracts intentionally do not manufacture a paid CRM
            # subscription. Retire any former Core read projection so a
            # cancelled historical line cannot continue granting CRM access.
            await self._retire_legacy_core_projection(
                contract,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                effective_at=now,
            )
            await self._project_access(
                contract,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                trial_ends_at=trial_ends_at,
                effective_at=effective_at,
            )
            return None
        plans = await self.project_catalog()
        plan = plans[core_line.sku]
        status_map = {
            CommercialContractStatus.trialing: SubscriptionStatus.trialing,
            CommercialContractStatus.active: SubscriptionStatus.active,
            CommercialContractStatus.past_due: SubscriptionStatus.past_due,
            CommercialContractStatus.paused: SubscriptionStatus.paused,
            CommercialContractStatus.cancelled: SubscriptionStatus.cancelled,
            CommercialContractStatus.draft: SubscriptionStatus.paused,
        }
        subscription_status = status_map[contract.status]
        effective_lines = [
            line
            for line in contract.lines
            if _line_is_active_and_effective(line, at=now)
        ]
        projection_interval = (
            BillingInterval.annually
            if effective_lines
            and all(
                line.billing_interval is CommercialBillingInterval.annual
                for line in effective_lines
            )
            else BillingInterval.monthly
        )
        subscription = await self.db.scalar(
            select(Subscription)
            .where(Subscription.workspace_id == contract.workspace_id)
            .with_for_update()
        )
        if subscription is None:
            subscription = Subscription(
                workspace_id=contract.workspace_id,
                plan_id=plan.id,
                status=subscription_status,
                interval=BillingInterval.monthly,
            )
            self.db.add(subscription)
        previous_interval = subscription.interval
        subscription.plan_id = plan.id
        subscription.status = subscription_status
        subscription.interval = projection_interval
        subscription.current_period_start = subscription.current_period_start or now
        subscription.trial_end = _utc(trial_ends_at) if trial_ends_at else None
        if subscription.status is SubscriptionStatus.trialing:
            subscription.current_period_end = subscription.trial_end
        elif (
            subscription.current_period_end is None
            or _utc(subscription.current_period_end) <= now
            or previous_interval is not subscription.interval
        ):
            subscription.current_period_start = now
            subscription.current_period_end = now + timedelta(
                days=365 if subscription.interval is BillingInterval.annually else 30
            )
        subscription.cancelled_at = (
            now if contract.status is CommercialContractStatus.cancelled else None
        )
        subscription.cancel_at_period_end = False
        if contract.status is CommercialContractStatus.trialing:
            subscription.amount_cents = 0
        elif projection_interval is BillingInterval.annually:
            # Apply adjustments on the native annual cycle. Multiplying rounded
            # canonical MRR by 12 produces a different invoice amount.
            subscription.amount_cents = (
                await CommercialCatalogService(self.db).recurring_cycle_price_cents(
                    contract.id,
                    billing_interval=CommercialBillingInterval.annual,
                    effective_at=now,
                )
            )
        else:
            subscription.amount_cents = contract.price.recurring_price_cents
        subscription.currency = "BRL"
        subscription.notes = (
            f"read_projection canonical_contract={contract.id} version={contract.version}"
        )
        await self.db.flush()

        await self._project_workspace_subscription(
            contract,
            core_sku=core_line.sku,
            core_billing_interval=core_line.billing_interval,
            projection_interval=projection_interval,
            trial_ends_at=trial_ends_at,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            effective_at=now,
        )
        await self._project_access(
            contract,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            trial_ends_at=trial_ends_at,
            effective_at=effective_at,
        )
        return subscription

    async def _project_access(
        self,
        contract: WorkspaceContractRead,
        *,
        actor_user_id: UUID | None,
        idempotency_key: str,
        trial_ends_at: datetime | None,
        effective_at: datetime | None,
    ) -> None:
        now = _utc(effective_at) if effective_at is not None else _utc()
        access_status = {
            CommercialContractStatus.trialing: SubscriptionAccessStatus.trial,
            CommercialContractStatus.active: SubscriptionAccessStatus.active,
            CommercialContractStatus.past_due: SubscriptionAccessStatus.grace,
            CommercialContractStatus.cancelled: SubscriptionAccessStatus.canceled,
            CommercialContractStatus.paused: SubscriptionAccessStatus.suspended,
            CommercialContractStatus.draft: SubscriptionAccessStatus.suspended,
        }[contract.status]
        async with self._access_workspace_scope(contract.workspace_id):
            await SubscriptionAccessPolicy(
                self.db,
                bind_workspace=not self.control_plane,
            ).transition(
                contract.workspace_id,
                access_status,
                idempotency_key=f"canonical-projection:{idempotency_key}",
                source="commercial.canonical_projection",
                reason="canonical_contract_projected",
                actor_user_id=actor_user_id,
                # Preserve ``None`` for ordinary immediate commands so an
                # idempotent replay has the same request fingerprint.  An
                # explicit historical/scheduled instant remains explicit.
                effective_at=(
                    _utc(effective_at) if effective_at is not None else None
                ),
                trial_ends_at=(
                    trial_ends_at if access_status is SubscriptionAccessStatus.trial else None
                ),
                grace_ends_at=(
                    now + timedelta(days=7)
                    if access_status is SubscriptionAccessStatus.grace
                    else None
                ),
            )

    async def _retire_legacy_core_projection(
        self,
        contract: WorkspaceContractRead,
        *,
        actor_user_id: UUID | None,
        idempotency_key: str,
        effective_at: datetime,
    ) -> None:
        """Prevent a removed Core line from surviving in compatibility reads."""

        now = _utc(effective_at)
        subscription = await self.db.scalar(
            select(Subscription)
            .where(Subscription.workspace_id == contract.workspace_id)
            .with_for_update()
        )
        if subscription is not None:
            subscription.status = (
                SubscriptionStatus.cancelled
                if contract.status is CommercialContractStatus.cancelled
                else SubscriptionStatus.paused
            )
            subscription.amount_cents = 0
            subscription.trial_end = None
            subscription.cancelled_at = (
                now if contract.status is CommercialContractStatus.cancelled else None
            )
            subscription.notes = (
                f"read_projection legal_only_contract={contract.id} version={contract.version}"
            )

        row = await self.db.scalar(
            select(WorkspaceSubscription)
            .where(WorkspaceSubscription.workspace_id == contract.workspace_id)
            .with_for_update()
        )
        if row is None:
            await self.db.flush()
            return
        before = self._workspace_subscription_snapshot(row)
        row.status = (
            WorkspaceSubscriptionStatus.cancelada
            if contract.status is CommercialContractStatus.cancelled
            else WorkspaceSubscriptionStatus.pausada
        )
        row.valor_mensal_cents = 0
        row.cortesia = False
        row.trial_ends_at = None
        row.cancelled_at = (
            now if contract.status is CommercialContractStatus.cancelled else None
        )
        row.origem = "canonical_projection"
        row.metadata_json = {
            "canonicalContractId": str(contract.id),
            "canonicalContractVersion": contract.version,
            "canonicalLegalOnly": True,
            "projectionOnly": True,
        }
        await self.db.flush()
        after = self._workspace_subscription_snapshot(row)
        if before != after:
            exists = await self.db.scalar(
                select(SubscriptionEvent.id).where(
                    SubscriptionEvent.subscription_id == row.id,
                    SubscriptionEvent.motivo == f"canonical_projection:{idempotency_key}",
                )
            )
            if exists is None:
                self.db.add(
                    SubscriptionEvent(
                        subscription_id=row.id,
                        workspace_id=row.workspace_id,
                        event_type=SubscriptionEventType.plano_alterado,
                        actor_user_id=actor_user_id,
                        actor_label="projeção do contrato canônico",
                        motivo=f"canonical_projection:{idempotency_key}",
                        before_json=before or {},
                        after_json=after,
                    )
                )
                await self.db.flush()

    async def _project_workspace_subscription(
        self,
        contract: WorkspaceContractRead,
        *,
        core_sku: str,
        core_billing_interval: CommercialBillingInterval,
        projection_interval: BillingInterval,
        trial_ends_at: datetime | None,
        actor_user_id: UUID | None,
        idempotency_key: str,
        effective_at: datetime,
    ) -> None:
        row = await self.db.scalar(
            select(WorkspaceSubscription)
            .where(WorkspaceSubscription.workspace_id == contract.workspace_id)
            .with_for_update()
        )
        before = self._workspace_subscription_snapshot(row) if row is not None else None
        status_map = {
            CommercialContractStatus.trialing: WorkspaceSubscriptionStatus.trial,
            CommercialContractStatus.active: WorkspaceSubscriptionStatus.ativa,
            CommercialContractStatus.past_due: WorkspaceSubscriptionStatus.inadimplente,
            CommercialContractStatus.paused: WorkspaceSubscriptionStatus.pausada,
            CommercialContractStatus.cancelled: WorkspaceSubscriptionStatus.cancelada,
            CommercialContractStatus.draft: WorkspaceSubscriptionStatus.pausada,
        }
        if row is None:
            row = WorkspaceSubscription(
                workspace_id=contract.workspace_id,
                plano=_SKU_TO_WORKSPACE_PLAN[core_sku],
            )
            self.db.add(row)
            await self.db.flush()
        previous_cycle = row.ciclo
        row.plano = _SKU_TO_WORKSPACE_PLAN[core_sku]
        row.status = status_map[contract.status]
        annual = projection_interval is BillingInterval.annually
        row.valor_mensal_cents = contract.price.recurring_price_cents
        row.ciclo = "anual" if annual else "mensal"
        row.desconto_pct = Decimal("0")
        row.cortesia = (
            contract.price.recurring_price_cents == 0
            and contract.status is not CommercialContractStatus.trialing
        )
        row.trial_ends_at = trial_ends_at
        now = _utc(effective_at)
        row.current_period_start = row.current_period_start or now
        if trial_ends_at is not None:
            row.current_period_end = trial_ends_at
        elif (
            row.current_period_end is None
            or _utc(row.current_period_end) <= now
            or previous_cycle != row.ciclo
        ):
            row.current_period_start = now
            row.current_period_end = now + timedelta(days=365 if annual else 30)
        row.cancelled_at = (
            now if contract.status is CommercialContractStatus.cancelled else None
        )
        row.origem = "canonical_projection"
        row.metadata_json = {
            "canonicalContractId": str(contract.id),
            "canonicalContractVersion": contract.version,
            "canonicalBillingInterval": core_billing_interval.value,
            "canonicalCoreBillingInterval": core_billing_interval.value,
            "projectionBillingInterval": projection_interval.value,
            "canonicalRecurringPriceCents": contract.price.recurring_price_cents,
            "projectionOnly": True,
        }
        await self.db.flush()
        after = self._workspace_subscription_snapshot(row)
        if before != after:
            exists = await self.db.scalar(
                select(SubscriptionEvent.id).where(
                    SubscriptionEvent.subscription_id == row.id,
                    SubscriptionEvent.motivo == f"canonical_projection:{idempotency_key}",
                )
            )
            if exists is None:
                self.db.add(
                    SubscriptionEvent(
                        subscription_id=row.id,
                        workspace_id=row.workspace_id,
                        event_type=(
                            SubscriptionEventType.criada
                            if before is None
                            else SubscriptionEventType.plano_alterado
                        ),
                        actor_user_id=actor_user_id,
                        actor_label="projeção do contrato canônico",
                        motivo=f"canonical_projection:{idempotency_key}",
                        before_json=before or {},
                        after_json=after,
                    )
                )
                await self.db.flush()

    @staticmethod
    def _workspace_subscription_snapshot(
        row: WorkspaceSubscription | None,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "plano": row.plano.value,
            "status": row.status.value,
            "valor_mensal_cents": row.valor_mensal_cents,
            "ciclo": row.ciclo,
            "trial_ends_at": row.trial_ends_at.isoformat() if row.trial_ends_at else None,
            "canonical_contract_id": (row.metadata_json or {}).get("canonicalContractId"),
            "canonical_contract_version": (row.metadata_json or {}).get("canonicalContractVersion"),
        }


class CanonicalContractCommand:
    """Create/revise canonical contracts, then refresh compatibility projections."""

    _CONTRACT_MANAGE = (("commercial_contracts", "manage"),)
    _ADJUSTMENT_MANAGE = (("commercial_adjustments", "manage"),)
    _BONUS_MANAGE = (("entitlement_bonuses", "manage"),)
    _ENSURE_CORE_PLAN = (
        ("commercial_contracts", "manage"),
        ("customers", "manage"),
        ("sales", "manage"),
        ("finance", "manage"),
    )

    def __init__(
        self,
        db: AsyncSession,
        *,
        control_plane: bool = False,
    ) -> None:
        self.db = db
        self.control_plane = control_plane
        self.catalog = CommercialCatalogService(db)
        self.projection = LegacyCommercialProjection(
            db,
            control_plane=control_plane,
        )

    async def _provider_managed_binding(
        self,
        contract_id: UUID,
    ) -> CommercialContractProviderBinding | None:
        return await self.db.scalar(
            select(CommercialContractProviderBinding).where(
                CommercialContractProviderBinding.contract_id == contract_id,
                CommercialContractProviderBinding.provider_subscription_id.is_not(None),
            )
        )

    async def _reject_provider_managed_change(
        self,
        contract_id: UUID,
        *,
        changed_families: list[CommercialOfferFamily] | None = None,
        status_changed: bool = False,
    ) -> None:
        touches_core = CommercialOfferFamily.crm in (changed_families or [])
        if not touches_core and not status_changed:
            return
        if await self._provider_managed_binding(contract_id) is not None:
            raise ProviderManagedContractError(_PROVIDER_MANAGED_MESSAGE)

    async def _authorize_control_plane(
        self,
        actor_user_id: UUID | None,
        *,
        allowed_permissions: tuple[tuple[str, str], ...],
    ) -> tuple[str, str] | None:
        """Authorize the concrete operation and bind exactly that RLS action.

        Callers cannot supply a permission namespace.  That used to let a
        broad ``sales``, ``customers`` or ``finance`` capability authorize
        unrelated adjustment/bonus writes.  Every public command now owns its
        allowlist and returns the permission it actually used so a narrowly
        scoped internal portability update can restore the original context.
        """

        if not self.control_plane:
            return None
        if actor_user_id is None:
            raise CommercialBridgeError(
                "Comando comercial de plataforma exige ator identificado."
            )
        actor = await self.db.get(User, actor_user_id)
        permission = next(
            (
                item
                for item in allowed_permissions
                if actor is not None and has_platform_permission(actor, *item)
            ),
            None,
        )
        if actor is None or permission is None:
            raise CommercialBridgeError(
                "Ator sem permissão para executar esta operação comercial."
            )
        module, action = permission
        await set_platform_control_context(
            self.db,
            actor_user_id=actor_user_id,
            module=module,
            action=action,
            is_platform_root=is_platform_root(actor),
        )
        return permission

    @asynccontextmanager
    async def _portability_rebind_context(
        self,
        *,
        actor_user_id: UUID | None,
        restore_permission: tuple[str, str] | None,
    ) -> AsyncIterator[None]:
        """Open the one internal action allowed to rebind/revoke exceptions.

        This context is reachable only after the outer contract operation was
        authorized.  It is not a user permission and must never be accepted as
        an input.  PostgreSQL policies restrict it to the automatic UPDATEs of
        adjustment/bonus portability during a contract revision.
        """

        if not self.control_plane:
            yield
            return
        if actor_user_id is None or restore_permission is None:
            raise CommercialBridgeError(
                "Rebind de portabilidade exige operação de plataforma autorizada."
            )
        actor = await self.db.get(User, actor_user_id)
        if actor is None:
            raise CommercialBridgeError("Ator comercial não encontrado.")
        await _set_commercial_portability_rebind_context(
            self.db,
            actor_user_id=actor_user_id,
            is_platform_root=is_platform_root(actor),
        )
        try:
            yield
        except Exception:
            # A Python/application exception does not abort PostgreSQL, so the
            # reserved context must not leak when an upstream caller catches
            # it and continues. A failed SQL statement may leave the session
            # or transaction unusable; in that case restoration itself fails
            # closed by rolling back the whole transaction.
            if self.db.is_active:
                try:
                    await set_platform_control_context(
                        self.db,
                        actor_user_id=actor_user_id,
                        module=restore_permission[0],
                        action=restore_permission[1],
                        is_platform_root=is_platform_root(actor),
                    )
                except Exception:
                    await self.db.rollback()
            else:
                await self.db.rollback()
            raise
        else:
            await set_platform_control_context(
                self.db,
                actor_user_id=actor_user_id,
                module=restore_permission[0],
                action=restore_permission[1],
                is_platform_root=is_platform_root(actor),
            )

    @asynccontextmanager
    async def _portability_restore_rebind_context(
        self,
        *,
        actor_user_id: UUID | None,
        restore_permission: tuple[str, str] | None,
    ) -> AsyncIterator[None]:
        """Open the narrower internal context used only by contract restore.

        A restore intentionally preserves the current line-bound commercial
        adjustments even when their normal portability would expire or require
        review. PostgreSQL restricts this context to rebinding the line foreign
        key and the corresponding version/timestamp metadata; it cannot revoke,
        recreate, or edit the adjustment's commercial terms.
        """

        if not self.control_plane:
            yield
            return
        if actor_user_id is None or restore_permission is None:
            raise CommercialBridgeError(
                "Rebind de restauração exige operação de plataforma autorizada."
            )
        actor = await self.db.get(User, actor_user_id)
        if actor is None:
            raise CommercialBridgeError("Ator comercial não encontrado.")
        await _set_commercial_portability_restore_rebind_context(
            self.db,
            actor_user_id=actor_user_id,
            is_platform_root=is_platform_root(actor),
        )
        try:
            yield
        except Exception:
            if self.db.is_active:
                try:
                    await set_platform_control_context(
                        self.db,
                        actor_user_id=actor_user_id,
                        module=restore_permission[0],
                        action=restore_permission[1],
                        is_platform_root=is_platform_root(actor),
                    )
                except Exception:
                    await self.db.rollback()
            else:
                await self.db.rollback()
            raise
        else:
            await set_platform_control_context(
                self.db,
                actor_user_id=actor_user_id,
                module=restore_permission[0],
                action=restore_permission[1],
                is_platform_root=is_platform_root(actor),
            )

    async def _legacy_trial_end(self, workspace_id: UUID) -> datetime | None:
        return await self.db.scalar(
            select(Subscription.trial_end).where(Subscription.workspace_id == workspace_id)
        )

    @staticmethod
    def _projection_key(
        *,
        operation: str,
        scope_key: str,
        resource_key: str,
        contract: WorkspaceContractRead,
        idempotency_key: str,
    ) -> str:
        """Use a projection namespace at least as strong as command receipts.

        Command receipts intentionally allow the same client key in different
        operations.  Access transitions and legacy subscription events use a
        single workspace namespace, so forwarding the raw key makes two valid
        commands collide.  Contract version plus a bounded digest keeps exact
        replays stable without exceeding legacy key limits.
        """

        scope_digest = sha256(scope_key.strip().encode("utf-8")).hexdigest()[:16]
        resource_digest = sha256(resource_key.strip().encode("utf-8")).hexdigest()[:16]
        receipt_digest = sha256(idempotency_key.strip().encode("utf-8")).hexdigest()[:24]
        return (
            f"{operation}:s{scope_digest}:r{resource_digest}:"
            f"c{str(contract.id)[:12]}:v{contract.version}:k{receipt_digest}"
        )

    async def _project_if_current(
        self,
        contract: WorkspaceContractRead,
        *,
        actor_user_id: UUID | None,
        idempotency_key: str,
        operation: str,
        scope_key: str,
        resource_key: str,
        effective_at: datetime | None = None,
        trial_ends_at: datetime | None = None,
        trial_ends_at_provided: bool = False,
    ) -> None:
        """Repair a projection only when an idempotent replay is still current."""

        current = await self.catalog.get_contract(contract.id)
        if current.version != contract.version:
            return
        await self.projection.project_contract(
            current,
            actor_user_id=actor_user_id,
            idempotency_key=self._projection_key(
                operation=operation,
                scope_key=scope_key,
                resource_key=resource_key,
                contract=current,
                idempotency_key=idempotency_key,
            ),
            trial_ends_at=(
                trial_ends_at
                if trial_ends_at_provided
                else await self._legacy_trial_end(current.workspace_id)
            ),
            effective_at=effective_at,
        )

    async def create_contract(
        self,
        *,
        payload: WorkspaceContractCreateRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
        allow_legacy_import: bool = False,
        project_legacy: bool = True,
        effective_at: datetime | None = None,
    ) -> tuple[WorkspaceContractRead, bool]:
        """Canonical write plus compatibility projection in one transaction."""

        await self._authorize_control_plane(
            actor_user_id,
            allowed_permissions=self._CONTRACT_MANAGE,
        )
        contract, replay = await self.catalog.create_contract(
            payload=payload,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            allow_legacy_import=allow_legacy_import,
            effective_at=effective_at,
        )
        if project_legacy:
            await self._project_if_current(
                contract,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                operation="create_contract",
                scope_key=str(payload.workspace_id),
                resource_key=str(contract.id),
                effective_at=effective_at,
            )
        return contract, replay

    async def revise_contract(
        self,
        *,
        contract_id: UUID,
        expected_version: int,
        payload: WorkspaceContractRevisionRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
        project_legacy: bool = True,
        effective_at: datetime | None = None,
    ) -> tuple[WorkspaceContractRead, bool]:
        """Version the aggregate and refresh both legacy read models atomically."""

        authorized_permission = await self._authorize_control_plane(
            actor_user_id,
            allowed_permissions=self._CONTRACT_MANAGE,
        )
        preview = await self.catalog.preview_contract_revision(
            contract_id,
            WorkspaceContractRevisionPreviewRequest(
                expected_version=expected_version,
                lines=payload.lines,
            ),
        )
        await self._reject_provider_managed_change(
            contract_id,
            changed_families=preview.changed_families,
        )
        contract, replay = await self.catalog.revise_contract(
            contract_id=contract_id,
            expected_version=expected_version,
            payload=payload,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            effective_at=effective_at,
            portability_context=lambda: self._portability_rebind_context(
                actor_user_id=actor_user_id,
                restore_permission=authorized_permission,
            ),
        )
        if project_legacy:
            await self._project_if_current(
                contract,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                operation="revise_contract",
                scope_key=str(contract_id),
                resource_key=str(contract.id),
                effective_at=effective_at,
            )
        return contract, replay

    async def restore_contract(
        self,
        *,
        contract_id: UUID,
        payload: WorkspaceContractRestoreRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
        project_legacy: bool = True,
        effective_at: datetime | None = None,
    ) -> tuple[WorkspaceContractRead, bool]:
        """Restore canonically, then repair compatibility projections."""

        authorized_permission = await self._authorize_control_plane(
            actor_user_id,
            allowed_permissions=self._CONTRACT_MANAGE,
        )
        preview = await self.catalog.preview_contract_restore(
            contract_id=contract_id,
            target_version=payload.target_version,
            expected_current_version=payload.expected_current_version,
        )
        current = await self.catalog.get_contract(contract_id)
        await self._reject_provider_managed_change(
            contract_id,
            changed_families=preview.revision.changed_families,
            status_changed=current.status != preview.target_snapshot.status,
        )
        contract, replay = await self.catalog.restore_contract(
            contract_id=contract_id,
            payload=payload,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            effective_at=effective_at,
            restore_portability_context=lambda: self._portability_restore_rebind_context(
                actor_user_id=actor_user_id,
                restore_permission=authorized_permission,
            ),
        )
        if project_legacy:
            await self._project_if_current(
                contract,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                operation="restore_contract",
                scope_key=str(contract_id),
                resource_key=str(contract.id),
                effective_at=effective_at,
            )
        return contract, replay

    async def add_adjustment(
        self,
        *,
        contract_id: UUID,
        payload: CommercialAdjustmentCreateRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
        project_legacy: bool = True,
    ) -> tuple[CommercialAdjustmentRead, bool]:
        """Persist a commercial adjustment and refresh legacy prices atomically."""

        await self._authorize_control_plane(
            actor_user_id,
            allowed_permissions=self._ADJUSTMENT_MANAGE,
        )
        adjustment, replay = await self.catalog.add_adjustment(
            contract_id=contract_id,
            payload=payload,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
        )
        if project_legacy:
            contract = await self.catalog.get_contract(adjustment.contract_id)
            await self._project_if_current(
                contract,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                operation="add_adjustment",
                scope_key=str(contract_id),
                resource_key=str(adjustment.id),
            )
        return adjustment, replay

    async def update_adjustment(
        self,
        *,
        adjustment_id: UUID,
        expected_version: int,
        payload: CommercialAdjustmentUpdateRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
        project_legacy: bool = True,
    ) -> tuple[CommercialAdjustmentRead, bool]:
        """Replace an adjustment and refresh legacy prices in the same unit."""

        await self._authorize_control_plane(
            actor_user_id,
            allowed_permissions=self._ADJUSTMENT_MANAGE,
        )
        adjustment, replay = await self.catalog.update_adjustment(
            adjustment_id=adjustment_id,
            expected_version=expected_version,
            payload=payload,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
        )
        if project_legacy:
            contract = await self.catalog.get_contract(adjustment.contract_id)
            await self._project_if_current(
                contract,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                operation="update_adjustment",
                scope_key=str(adjustment_id),
                resource_key=str(adjustment.id),
            )
        return adjustment, replay

    async def revoke_adjustment(
        self,
        *,
        adjustment_id: UUID,
        expected_version: int,
        payload: CommercialRecordRevokeRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
        project_legacy: bool = True,
    ) -> tuple[CommercialAdjustmentRead, bool]:
        """Revoke an adjustment and refresh legacy prices in the same unit."""

        await self._authorize_control_plane(
            actor_user_id,
            allowed_permissions=self._ADJUSTMENT_MANAGE,
        )
        adjustment, replay = await self.catalog.revoke_adjustment(
            adjustment_id=adjustment_id,
            expected_version=expected_version,
            payload=payload,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
        )
        if project_legacy:
            contract = await self.catalog.get_contract(adjustment.contract_id)
            await self._project_if_current(
                contract,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                operation="revoke_adjustment",
                scope_key=str(adjustment_id),
                resource_key=str(adjustment.id),
            )
        return adjustment, replay

    async def add_bonus(
        self,
        *,
        workspace_id: UUID,
        payload: EntitlementBonusCreateRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[EntitlementBonusRead, bool]:
        """Persist a capacity bonus only under its dedicated permission."""

        await self._authorize_control_plane(
            actor_user_id,
            allowed_permissions=self._BONUS_MANAGE,
        )
        return await self.catalog.add_bonus(
            workspace_id=workspace_id,
            payload=payload,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
        )

    async def update_bonus(
        self,
        *,
        bonus_id: UUID,
        expected_version: int,
        payload: EntitlementBonusUpdateRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[EntitlementBonusRead, bool]:
        """Replace a bonus only under its dedicated permission."""

        await self._authorize_control_plane(
            actor_user_id,
            allowed_permissions=self._BONUS_MANAGE,
        )
        return await self.catalog.update_bonus(
            bonus_id=bonus_id,
            expected_version=expected_version,
            payload=payload,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
        )

    async def revoke_bonus(
        self,
        *,
        bonus_id: UUID,
        expected_version: int,
        payload: CommercialRecordRevokeRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[EntitlementBonusRead, bool]:
        """Revoke a bonus only under its dedicated permission."""

        await self._authorize_control_plane(
            actor_user_id,
            allowed_permissions=self._BONUS_MANAGE,
        )
        return await self.catalog.revoke_bonus(
            bonus_id=bonus_id,
            expected_version=expected_version,
            payload=payload,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
        )

    async def reconcile_projection(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        idempotency_key: str,
        effective_at: datetime | None = None,
    ) -> WorkspaceContractRead:
        """Repair legacy reads from the existing canonical contract without rewriting it."""

        await self._authorize_control_plane(
            actor_user_id,
            allowed_permissions=self._CONTRACT_MANAGE,
        )
        contract = await self.catalog.get_workspace_contract(workspace_id)
        await self.projection.project_contract(
            contract,
            actor_user_id=actor_user_id,
            idempotency_key=self._projection_key(
                operation="reconcile_projection",
                scope_key=str(workspace_id),
                resource_key=str(contract.id),
                contract=contract,
                idempotency_key=idempotency_key,
            ),
            trial_ends_at=await self._legacy_trial_end(workspace_id),
            effective_at=effective_at,
        )
        return contract

    @staticmethod
    def _copy_effective_line(line: Any) -> WorkspaceContractLineWrite:
        return WorkspaceContractLineWrite(
            sku=line.sku,
            policy_version_id=line.policy_version_id,
            quantity=line.quantity,
            contracted_price_cents=line.contracted_price_cents,
            billing_interval=line.billing_interval,
            entitlement_overrides=line.entitlement_overrides,
            effective_from=line.effective_from,
            effective_until=line.effective_until,
        )

    async def ensure_core_plan(
        self,
        *,
        workspace_id: UUID,
        plan: str | PlanTier,
        actor_user_id: UUID | None,
        idempotency_key: str,
        reason: str,
        project_legacy: bool = True,
    ) -> WorkspaceContractRead:
        """Create or revise the single active Core line, preserving Legal and exceptions."""

        authorized_permission = await self._authorize_control_plane(
            actor_user_id,
            allowed_permissions=self._ENSURE_CORE_PLAN,
        )
        target_sku = (await CanonicalOfferQuery(self.db).get(plan)).sku
        contract_id = await self.db.scalar(
            select(WorkspaceCommercialContract.id).where(
                WorkspaceCommercialContract.workspace_id == workspace_id
            )
        )
        if contract_id is None:
            contract, _ = await self.catalog.create_contract(
                payload=WorkspaceContractCreateRequest(
                    workspace_id=workspace_id,
                    status=CommercialContractStatus.active,
                    reason=reason,
                    lines=[WorkspaceContractLineWrite(sku=target_sku)],
                ),
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
            )
            if project_legacy:
                await self._project_if_current(
                    contract,
                    actor_user_id=actor_user_id,
                    idempotency_key=idempotency_key,
                    operation="create_contract",
                    scope_key=str(workspace_id),
                    resource_key=str(contract.id),
                )
            return contract

        contract = await self.catalog.get_contract(contract_id)
        current_core = _effective_core_line(contract)
        if current_core is not None and current_core.sku == target_sku:
            if project_legacy:
                await self.projection.project_contract(
                    contract,
                    actor_user_id=actor_user_id,
                    idempotency_key=self._projection_key(
                        operation="ensure_core_plan_existing",
                        scope_key=str(workspace_id),
                        resource_key=str(contract.id),
                        contract=contract,
                        idempotency_key=idempotency_key,
                    ),
                    trial_ends_at=await self._legacy_trial_end(workspace_id),
                )
            return contract

        await self._reject_provider_managed_change(
            contract.id,
            changed_families=[CommercialOfferFamily.crm],
        )

        lines = [WorkspaceContractLineWrite(sku=target_sku)]
        lines.extend(
            self._copy_effective_line(line)
            for line in _effective_lines_for_family(contract, CommercialOfferFamily.legal)
        )
        revised, _ = await self.catalog.revise_contract(
            contract_id=contract.id,
            expected_version=contract.version,
            payload=WorkspaceContractRevisionRequest(lines=lines, reason=reason),
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            portability_context=lambda: self._portability_rebind_context(
                actor_user_id=actor_user_id,
                restore_permission=authorized_permission,
            ),
        )
        if project_legacy:
            await self._project_if_current(
                revised,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                operation="revise_contract",
                scope_key=str(contract.id),
                resource_key=str(revised.id),
            )
        return revised

    async def provision_trial(
        self,
        *,
        workspace_id: UUID,
        plan: str | PlanTier,
        actor_user_id: UUID | None,
        idempotency_key: str,
        trial_days: int = 14,
        project_legacy: bool = True,
        effective_at: datetime | None = None,
    ) -> WorkspaceContractRead:
        await self._authorize_control_plane(
            actor_user_id,
            allowed_permissions=self._CONTRACT_MANAGE,
        )
        if not 1 <= trial_days <= 90:
            raise CommercialCatalogValidationError("Trial deve ficar entre 1 e 90 dias.")
        sku = (await CanonicalOfferQuery(self.db).get(plan)).sku
        existing_id = await self.db.scalar(
            select(WorkspaceCommercialContract.id).where(
                WorkspaceCommercialContract.workspace_id == workspace_id
            )
        )
        if existing_id is None:
            projection_at = _utc(effective_at) if effective_at is not None else _utc()
            contract, _ = await self.create_contract(
                payload=WorkspaceContractCreateRequest(
                    workspace_id=workspace_id,
                    status=CommercialContractStatus.trialing,
                    reason="Trial iniciado pelo fluxo de cadastro.",
                    lines=[WorkspaceContractLineWrite(sku=sku)],
                ),
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                project_legacy=False,
                effective_at=projection_at,
            )
        else:
            contract = await self.catalog.get_contract(existing_id)
            if contract.status is not CommercialContractStatus.trialing:
                raise CommercialCatalogConflictError(
                    "Conta já possui contrato comercial e não pode iniciar um novo trial."
                )
            active_core = _effective_core_line(contract)
            if active_core is None or active_core.sku != sku:
                raise CommercialCatalogConflictError(
                    "A chave idempotente não corresponde ao trial comercial existente."
                )
        if project_legacy:
            projection_at = _utc(effective_at) if effective_at is not None else _utc()
            await self.projection.project_contract(
                contract,
                actor_user_id=actor_user_id,
                idempotency_key=self._projection_key(
                    operation="provision_trial",
                    scope_key=str(workspace_id),
                    resource_key=str(contract.id),
                    contract=contract,
                    idempotency_key=idempotency_key,
                ),
                trial_ends_at=projection_at + timedelta(days=trial_days),
                effective_at=projection_at,
            )
        return contract

    async def change_core_plan(
        self,
        *,
        workspace_id: UUID,
        plan: str | PlanTier,
        direction: str,
        actor_user_id: UUID | None,
        idempotency_key: str,
        reason: str,
        project_legacy: bool = True,
        effective_at: datetime | None = None,
    ) -> WorkspaceContractRead:
        await self._authorize_control_plane(
            actor_user_id,
            allowed_permissions=self._CONTRACT_MANAGE,
        )
        if direction not in {"upgrade", "downgrade"}:
            raise CanonicalPlanDirectionError("Direção comercial inválida.")
        target_sku = (await CanonicalOfferQuery(self.db).get(plan)).sku
        contract = await self.catalog.get_workspace_contract(workspace_id)
        current_core = _effective_core_line(contract, required=True)
        if current_core.sku == target_sku:
            raise CommercialCatalogConflictError("A conta já utiliza essa oferta Core.")
        is_upgrade = _CANONICAL_RANK[target_sku] > _CANONICAL_RANK[current_core.sku]
        if (direction == "upgrade") != is_upgrade:
            raise CanonicalPlanDirectionError(
                f"{target_sku} não é um {direction} válido a partir de {current_core.sku}."
            )
        lines = [WorkspaceContractLineWrite(sku=target_sku)]
        lines.extend(
            self._copy_effective_line(line)
            for line in _effective_lines_for_family(contract, CommercialOfferFamily.legal)
        )
        preview = await self.catalog.preview_contract_revision(
            contract.id,
            WorkspaceContractRevisionPreviewRequest(
                expected_version=contract.version,
                lines=lines,
            ),
        )
        if not preview.eligible:
            raise CommercialCatalogConflictError(" ".join(preview.blockers or preview.warnings))
        revised, _ = await self.revise_contract(
            contract_id=contract.id,
            expected_version=contract.version,
            payload=WorkspaceContractRevisionRequest(lines=lines, reason=reason),
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            project_legacy=project_legacy,
            effective_at=effective_at,
        )
        return revised

    async def checkout(self, *_args: Any, **_kwargs: Any) -> None:
        raise CanonicalCheckoutUnavailableError(
            "Contratação automática ainda não está configurada. Use “Quero uma demonstração”."
        )

    async def change_status(
        self,
        *,
        workspace_id: UUID,
        target_status: CommercialContractStatus,
        actor_user_id: UUID | None,
        idempotency_key: str,
        reason: str,
        project_legacy: bool = True,
        trial_ends_at: datetime | None = None,
        effective_at: datetime | None = None,
    ) -> WorkspaceContractRead:
        """Apply a versioned internal lifecycle transition.

        This is intentionally provider-agnostic.  It changes the internal
        commercial state only; it never claims that an invoice, refund or
        provider subscription was changed.
        """

        await self._authorize_control_plane(
            actor_user_id,
            allowed_permissions=self._CONTRACT_MANAGE,
        )
        command_at = _utc(effective_at) if effective_at is not None else _utc()
        normalized_trial_end = _utc(trial_ends_at) if trial_ends_at is not None else None
        scope = str(workspace_id)
        request_payload = {
            "workspace_id": scope,
            "target_status": target_status.value,
            "reason": reason.strip(),
            # Idempotency represents caller input.  The generated command
            # timestamp belongs to the persisted response envelope; hashing a
            # fresh wall clock here would turn a valid retry into a conflict.
            "effective_at": (
                command_at.isoformat() if effective_at is not None else None
            ),
            "trial_ends_at": (
                normalized_trial_end.isoformat() if normalized_trial_end is not None else None
            ),
        }
        request_hash = self.catalog._hash(request_payload)
        await self.catalog._lock_command(
            operation="change_contract_status",
            scope_key=scope,
            aggregate_key=scope,
        )
        # A receipt created before a provider binding must not be replayed
        # after an external billing provider becomes authoritative. Besides returning stale state,
        # the legacy projection below could otherwise overwrite the provider
        # reconciliation with an old lifecycle status.
        provider_contract_id = await self.db.scalar(
            select(WorkspaceCommercialContract.id).where(
                WorkspaceCommercialContract.workspace_id == workspace_id
            )
        )
        if provider_contract_id is not None:
            await self._reject_provider_managed_change(
                provider_contract_id,
                status_changed=True,
            )
        receipt = await self.catalog._existing_receipt(
            operation="change_contract_status",
            scope_key=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt is not None:
            stored_payload = receipt.response_payload
            if isinstance(stored_payload, dict) and "contract" in stored_payload:
                replay = WorkspaceContractRead.model_validate(stored_payload["contract"])
                stored_effective_at = _utc(
                    datetime.fromisoformat(
                        str(stored_payload["effectiveAt"]).replace("Z", "+00:00")
                    )
                )
                raw_trial_end = stored_payload.get("trialEndsAt")
                stored_trial_end = (
                    _utc(datetime.fromisoformat(str(raw_trial_end).replace("Z", "+00:00")))
                    if raw_trial_end is not None
                    else None
                )
            else:
                # Compatibility for receipts written before the temporal
                # envelope existed. Never stamp a replay with the new wall clock.
                replay = WorkspaceContractRead.model_validate(stored_payload)
                stored_effective_at = _utc(receipt.created_at)
                stored_trial_end = None
            if project_legacy:
                await self._project_if_current(
                    replay,
                    actor_user_id=actor_user_id,
                    idempotency_key=idempotency_key,
                    operation="change_contract_status",
                    scope_key=scope,
                    resource_key=str(replay.id),
                    effective_at=stored_effective_at,
                    trial_ends_at=stored_trial_end,
                    trial_ends_at_provided=True,
                )
            return replay

        contract = await self.db.scalar(
            select(WorkspaceCommercialContract)
            .where(WorkspaceCommercialContract.workspace_id == workspace_id)
            .with_for_update()
        )
        if contract is None:
            raise CommercialCatalogNotFoundError("Workspace sem contrato comercial canônico.")
        allowed = {
            CommercialContractStatus.draft: {
                CommercialContractStatus.trialing,
                CommercialContractStatus.active,
                CommercialContractStatus.cancelled,
            },
            CommercialContractStatus.trialing: {
                CommercialContractStatus.active,
                CommercialContractStatus.past_due,
                CommercialContractStatus.paused,
                CommercialContractStatus.cancelled,
            },
            CommercialContractStatus.active: {
                CommercialContractStatus.past_due,
                CommercialContractStatus.paused,
                CommercialContractStatus.cancelled,
            },
            CommercialContractStatus.past_due: {
                CommercialContractStatus.active,
                CommercialContractStatus.paused,
                CommercialContractStatus.cancelled,
            },
            CommercialContractStatus.paused: {
                CommercialContractStatus.active,
                CommercialContractStatus.cancelled,
            },
            CommercialContractStatus.cancelled: set(),
        }
        if contract.status is target_status:
            raise CommercialCatalogConflictError(
                f"Contrato já está no estado {target_status.value}."
            )
        if target_status not in allowed[contract.status]:
            raise CommercialCatalogConflictError(
                f"Transição {contract.status.value} → {target_status.value} não permitida."
            )
        before = await self.catalog.get_contract(contract.id)
        contract.status = target_status
        contract.version += 1
        contract.updated_by_user_id = actor_user_id
        await self.db.flush()
        # ``updated_at`` is a server-side on-update value.  Refresh explicitly
        # before building the async read model so SQLAlchemy never attempts a
        # lazy IO outside greenlet context (notably with SQLite in API tests).
        await self.db.refresh(contract)
        after = await self.catalog.get_contract(contract.id)
        self.catalog._event(
            event_type="contract_status_changed",
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            resource_type="workspace_commercial_contract",
            resource_id=contract.id,
            before=before.model_dump(mode="json"),
            after=after.model_dump(mode="json"),
            reason=reason,
            correlation_id=idempotency_key,
        )
        await self.catalog._record_receipt(
            operation="change_contract_status",
            scope_key=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload={
                "contract": after.model_dump(mode="json"),
                "effectiveAt": command_at.isoformat(),
                "trialEndsAt": (
                    normalized_trial_end.isoformat()
                    if normalized_trial_end is not None
                    else None
                ),
            },
            actor_user_id=actor_user_id,
        )
        if project_legacy:
            await self._project_if_current(
                after,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                operation="change_contract_status",
                scope_key=scope,
                resource_key=str(after.id),
                trial_ends_at=normalized_trial_end,
                trial_ends_at_provided=True,
                effective_at=command_at,
            )
        return after


class CanonicalRevenueReadModel:
    """Combine canonical contracts with unreconciled legacy rows exactly once."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_records(self) -> list[CommercialRevenueRecord]:
        service = CommercialCatalogService(self.db)
        contract_ids = list(
            (
                await self.db.scalars(
                    select(WorkspaceCommercialContract.id).order_by(
                        WorkspaceCommercialContract.created_at.asc(),
                        WorkspaceCommercialContract.id.asc(),
                    )
                )
            ).all()
        )
        records: list[CommercialRevenueRecord] = []
        canonical_workspace_ids: set[UUID] = set()
        for contract_id in contract_ids:
            contract = await service.get_contract(contract_id)
            canonical_workspace_ids.add(contract.workspace_id)
            core = _effective_core_line(contract)
            product_sku = core.sku if core is not None else "legal_360"
            legacy_projection = await self.db.scalar(
                select(WorkspaceSubscription).where(
                    WorkspaceSubscription.workspace_id == contract.workspace_id
                )
            )
            status = {
                CommercialContractStatus.draft: "pausada",
                CommercialContractStatus.trialing: "trial",
                CommercialContractStatus.active: "ativa",
                CommercialContractStatus.past_due: "inadimplente",
                CommercialContractStatus.paused: "pausada",
                CommercialContractStatus.cancelled: "cancelada",
            }[contract.status]
            mrr = contract.price.recurring_price_cents if status in {"ativa", "inadimplente"} else 0
            records.append(
                CommercialRevenueRecord(
                    id=contract.id,
                    workspace_id=contract.workspace_id,
                    workspace_name=contract.workspace_label,
                    sku=product_sku,
                    status=status,
                    mrr_cents=mrr,
                    recurring_price_cents=contract.price.recurring_price_cents,
                    trial_ends_at=legacy_projection.trial_ends_at if legacy_projection else None,
                    cancelled_at=legacy_projection.cancelled_at if legacy_projection else None,
                    created_at=contract.created_at,
                    updated_at=contract.updated_at,
                    courtesy=contract.price.recurring_price_cents == 0 and status != "trial",
                    source="canonical_contract",
                    contract_id=contract.id,
                    contract_version=contract.version,
                    legacy_subscription_id=legacy_projection.id if legacy_projection else None,
                )
            )

        legacy_query = select(WorkspaceSubscription, Workspace.name).join(
            Workspace, Workspace.id == WorkspaceSubscription.workspace_id
        )
        if canonical_workspace_ids:
            legacy_query = legacy_query.where(
                WorkspaceSubscription.workspace_id.not_in(canonical_workspace_ids)
            )
        for subscription, workspace_name in (await self.db.execute(legacy_query)).all():
            records.append(
                CommercialRevenueRecord(
                    id=subscription.id,
                    workspace_id=subscription.workspace_id,
                    workspace_name=workspace_name,
                    sku={
                        WorkspacePlanCode.essencial: "crm_start",
                        WorkspacePlanCode.escritorio: "crm_advanced",
                        WorkspacePlanCode.performance: "crm_evolution",
                        WorkspacePlanCode.custom: "legacy_custom",
                    }[subscription.plano],
                    status=subscription.status.value,
                    mrr_cents=subscription.mrr_cents,
                    recurring_price_cents=subscription.valor_mensal_cents,
                    trial_ends_at=subscription.trial_ends_at,
                    cancelled_at=subscription.cancelled_at,
                    created_at=subscription.created_at,
                    updated_at=subscription.updated_at,
                    courtesy=subscription.cortesia,
                    source="legacy_unreconciled",
                    legacy_subscription_id=subscription.id,
                )
            )
        return records

    async def get(self, workspace_id: UUID) -> CommercialRevenueRecord:
        for record in await self.list_records():
            if record.workspace_id == workspace_id:
                return record
        raise CommercialCatalogNotFoundError("Workspace sem contrato ou assinatura comercial.")
