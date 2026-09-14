"""Provider-agnostic commercial catalog and effective-capability resolver."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.advisory import acquire_transaction_advisory_lock
from app.db.tenant import get_workspace_context
from app.models.commercial_catalog import (
    CommercialAdjustment,
    CommercialAdjustmentType,
    CommercialBillingInterval,
    CommercialCatalogEvent,
    CommercialCommandReceipt,
    CommercialContractLineStatus,
    CommercialContractStatus,
    CommercialOffer,
    CommercialOfferFamily,
    CommercialOfferKind,
    CommercialOfferVersion,
    CommercialPortability,
    CommercialRecordStatus,
    EntitlementBonus,
    EntitlementBonusMode,
    WorkspaceCommercialContract,
    WorkspaceContractLine,
)
from app.models.entitlement import (
    EntitlementKey,
    OverrideMode,
    WorkspaceEntitlementOverride,
    WorkspaceMonthlyUsage,
)
from app.models.whatsapp import WhatsAppContact
from app.models.workspace import Workspace
from app.schemas.commercial_catalog import (
    COMMERCIAL_ENTITLEMENT_KEYS_V3,
    CapabilitySourceRead,
    CommercialAdjustmentCreateRequest,
    CommercialAdjustmentRead,
    CommercialAdjustmentUpdateRequest,
    CommercialEntitlementComposition,
    CommercialEntitlementEnforcement,
    CommercialEntitlementPeriod,
    CommercialEntitlementSetting,
    CommercialFieldDiffRead,
    CommercialOfferDraftPreviewRead,
    CommercialOfferDraftPublishRequest,
    CommercialOfferDraftRead,
    CommercialOfferDraftSaveRequest,
    CommercialOfferPublishRequest,
    CommercialOfferRestorePreviewRead,
    CommercialOfferRestoreRequest,
    CommercialOfferRevisionHistoryItem,
    CommercialOfferRevisionHistoryRead,
    CommercialOfferSnapshotRead,
    CommercialOfferSnapshotV2Read,
    CommercialOfferSnapshotWrite,
    CommercialOfferVersionRead,
    CommercialRecordRevokeRequest,
    EffectiveCapabilityManifestRead,
    EffectiveCapabilityRead,
    EntitlementBonusCreateRequest,
    EntitlementBonusRead,
    EntitlementBonusUpdateRequest,
    PricePreviewRead,
    WorkspaceContractCreateRequest,
    WorkspaceContractHistoryItem,
    WorkspaceContractHistoryRead,
    WorkspaceContractLineRead,
    WorkspaceContractLineWrite,
    WorkspaceContractPreviewRead,
    WorkspaceContractPreviewRequest,
    WorkspaceContractRead,
    WorkspaceContractRestorePreviewRead,
    WorkspaceContractRestoreRequest,
    WorkspaceContractRevisionPreviewRead,
    WorkspaceContractRevisionPreviewRequest,
    WorkspaceContractRevisionRequest,
)
from app.services.commercial_unit_economics_service import (
    CommercialUnitEconomicsService,
)
from app.services.entitlement_service import (
    ENTITLEMENT_CATALOG,
    FEATURE_ENTITLEMENTS,
    METERED_ENTITLEMENTS,
    QUANTITATIVE_ENTITLEMENTS,
)

CATALOG_AUTHORITY_LOCK_NAMESPACE = "commercial:catalog-authority"
CATALOG_AUTHORITY_LOCK_VALUE = "canonical-vs-plan-policy"


class CommercialCatalogError(RuntimeError):
    """Base class for safe commercial command failures."""


class CommercialCatalogNotFoundError(CommercialCatalogError):
    pass


class CommercialContractNotFoundError(CommercialCatalogNotFoundError):
    pass


class CommercialCatalogConflictError(CommercialCatalogError):
    pass


class CommercialCatalogValidationError(CommercialCatalogError):
    pass


class CommercialIdempotencyConflictError(CommercialCatalogConflictError):
    pass


@dataclass(frozen=True, slots=True)
class _ResolvedLine:
    line_id: UUID
    offer: CommercialOffer
    version: CommercialOfferVersion
    snapshot: CommercialOfferSnapshotRead
    input: WorkspaceContractLineWrite
    list_price_cents: int
    contracted_price_cents: int


@dataclass(slots=True)
class _ContractRevisionPlan:
    contract: WorkspaceCommercialContract
    current_lines: list[WorkspaceContractLineRead]
    target_lines: list[WorkspaceContractLineRead]
    resolved_changed: list[_ResolvedLine]
    changed_families: set[CommercialOfferFamily]
    replacement_line_ids: dict[UUID, UUID]
    adjustments: list[CommercialAdjustment]
    adjustment_ids_to_revoke: set[UUID]
    adjustment_ids_to_rebind: set[UUID]
    restore_adjustment_ids_to_rebind: set[UUID]
    bonus_ids_to_revoke: set[UUID]
    preview_bonuses: list[EntitlementBonus]
    warnings: list[str]
    blockers: list[str]


_SUM_KEYS = frozenset(
    {
        EntitlementKey.tokens,
        EntitlementKey.ai_executions,
        EntitlementKey.ai_conversations,
        EntitlementKey.audio_minutes,
        EntitlementKey.storage_bytes,
        EntitlementKey.files,
        EntitlementKey.monitored_processes,
        EntitlementKey.legal_ai_generations,
        EntitlementKey.legal_documents,
        EntitlementKey.djen_terms,
    }
)

_LEGAL_EXPLICIT_TERMS = frozenset(
    {
        EntitlementKey.users,
        EntitlementKey.legal_areas,
        EntitlementKey.storage_bytes,
        EntitlementKey.monitored_processes,
        EntitlementKey.legal_documents,
        EntitlementKey.protocol_folders,
    }
)

# A commercial number is only publishable when an operational boundary
# consumes it.  These legal-specific keys have a catalog definition, but no
# route/service currently checks or reserves their quota.  Keeping them
# disabled is deliberately honest: Legal 360 is sold through the aggregate
# ``legal_suite`` gate plus the shared, enforced capacities below.  When a
# consumer is implemented, move the key out of this set together with its
# enforcement test.
_UNENFORCED_LEGAL_CAPABILITIES = frozenset(
    {
        EntitlementKey.legal_cases,
        EntitlementKey.djen_terms,
        EntitlementKey.legal_ai_generations,
        EntitlementKey.legal_automations,
        EntitlementKey.legal_templates,
        EntitlementKey.legal_signatures,
        EntitlementKey.legal_bulk_operations,
        EntitlementKey.legal_audit,
        EntitlementKey.legal_integrations,
    }
)

# Compatibility aliases exist only at integration boundaries.  The SKU is the
# canonical commercial identity; legacy plan keys must never become a second
# editable catalog again.
LEGACY_PLAN_KEY_TO_CANONICAL_SKU: Mapping[str, str] = {
    "basic": "crm_start",
    "starter": "crm_start",
    "start": "crm_start",
    "crm_start": "crm_start",
    "pro": "crm_advanced",
    "advanced": "crm_advanced",
    "crm_advanced": "crm_advanced",
    "scale": "crm_evolution",
    "business": "crm_evolution",
    "evolution": "crm_evolution",
    "crm_evolution": "crm_evolution",
}

_DIFF_LABELS: Mapping[str, str] = {
    "name": "Nome",
    "description": "Descrição",
    "is_public": "Visibilidade pública",
    "is_active": "Oferta ativa",
    "is_recommended": "Oferta recomendada",
    "is_quote_only": "Preço sob consulta",
    "available_intervals": "Periodicidades",
    "sort_order": "Ordem comercial",
    "price_monthly_cents": "Preço mensal",
    "price_annual_cents": "Preço anual",
    "cta": "Chamada para ação",
    "status": "Status",
    "lines": "Produtos contratados",
    "contracted_price_cents": "Preço contratado",
    "entitlement_overrides": "Limites negociados",
}


def canonical_sku_for_legacy_plan_key(value: str) -> str | None:
    """Resolve a deprecated plan key without manufacturing a new offer."""

    return LEGACY_PLAN_KEY_TO_CANONICAL_SKU.get(value.strip().lower())


def _diff_label(path: str) -> str:
    tail = path.rsplit(".", 1)[-1].replace("[", "").replace("]", "")
    return _DIFF_LABELS.get(tail, tail.replace("_", " ").strip().capitalize())


def _semantic_diff(
    before: Any,
    after: Any,
    *,
    path: str = "",
) -> list[CommercialFieldDiffRead]:
    """Return a deterministic, secret-free field diff for commercial snapshots."""

    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        diffs: list[CommercialFieldDiffRead] = []
        for key in sorted(set(before) | set(after), key=str):
            child_path = f"{path}.{key}" if path else str(key)
            if key not in before:
                diffs.append(
                    CommercialFieldDiffRead(
                        path=child_path,
                        label=_diff_label(child_path),
                        change_type="added",
                        before=None,
                        after=after[key],
                    )
                )
            elif key not in after:
                diffs.append(
                    CommercialFieldDiffRead(
                        path=child_path,
                        label=_diff_label(child_path),
                        change_type="removed",
                        before=before[key],
                        after=None,
                    )
                )
            else:
                diffs.extend(_semantic_diff(before[key], after[key], path=child_path))
        return diffs
    # Lists are intentionally treated as one semantic field. Index-by-index
    # diffs are noisy and misleading when a contract line is reordered.
    change_type = "added" if before is None else "removed" if after is None else "changed"
    return [
        CommercialFieldDiffRead(
            path=path or "value",
            label=_diff_label(path or "value"),
            change_type=change_type,
            before=before,
            after=after,
        )
    ]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _active_at(start: datetime, end: datetime | None, instant: datetime) -> bool:
    normalized = _as_utc(instant)
    return _as_utc(start) <= normalized and (end is None or _as_utc(end) > normalized)


def _record_status_for_window(
    starts_at: datetime,
    ends_at: datetime | None,
    *,
    now: datetime | None = None,
) -> CommercialRecordStatus:
    instant = _as_utc(now or datetime.now(UTC))
    if ends_at is not None and _as_utc(ends_at) <= instant:
        return CommercialRecordStatus.expired
    if _as_utc(starts_at) <= instant:
        return CommercialRecordStatus.active
    return CommercialRecordStatus.scheduled


def _monthly_equivalent_cents(
    amount_cents: int,
    billing_interval: CommercialBillingInterval,
) -> int:
    """Normalize positive recurring amounts to the monthly reporting basis.

    Historical annual contracts remain pinned to their annual line amount, but
    every contract-level price preview represents MRR.  Integer half-up
    rounding avoids silently dropping the remainder of a legacy annual price.
    """

    if billing_interval is CommercialBillingInterval.annual:
        return (amount_cents + 6) // 12
    return amount_cents


def _period_for(key: EntitlementKey) -> CommercialEntitlementPeriod:
    if key in FEATURE_ENTITLEMENTS:
        return CommercialEntitlementPeriod.feature
    if key in METERED_ENTITLEMENTS:
        return CommercialEntitlementPeriod.monthly
    if key in {
        EntitlementKey.task_participants,
        EntitlementKey.task_reviews,
        EntitlementKey.review_rounds,
        EntitlementKey.task_reminder_rules,
        EntitlementKey.meeting_participants,
    }:
        return CommercialEntitlementPeriod.concurrent
    return CommercialEntitlementPeriod.stock


def _composition_for(key: EntitlementKey) -> CommercialEntitlementComposition:
    if key in FEATURE_ENTITLEMENTS:
        return CommercialEntitlementComposition.boolean_any
    if key in _SUM_KEYS:
        return CommercialEntitlementComposition.sum
    return CommercialEntitlementComposition.maximum


def _setting(
    key: EntitlementKey,
    *,
    enabled: bool,
    limit: int | None = None,
) -> CommercialEntitlementSetting:
    return CommercialEntitlementSetting(
        enabled=enabled,
        unit=ENTITLEMENT_CATALOG[key].unit.value,
        period=_period_for(key),
        limit_quantity=limit if enabled else None,
        enforcement=CommercialEntitlementEnforcement.hard,
        grace_quantity=None,
        composition=_composition_for(key),
    )


def _complete_policy(
    limits: Mapping[EntitlementKey, int],
    features: Iterable[EntitlementKey] = (),
) -> dict[EntitlementKey, CommercialEntitlementSetting]:
    enabled_features = set(features)
    return {
        key: _setting(
            key,
            enabled=key in limits or key in enabled_features,
            limit=limits.get(key),
        )
        for key in COMMERCIAL_ENTITLEMENT_KEYS_V3
    }


def _default_offer_requests() -> tuple[CommercialOfferPublishRequest, ...]:
    gib = 1024**3
    collaboration_start = {
        EntitlementKey.active_tasks: 500,
        EntitlementKey.task_participants: 3,
        EntitlementKey.task_reviews: 1,
        EntitlementKey.review_rounds: 10,
        EntitlementKey.task_reminder_rules: 2,
        EntitlementKey.meeting_participants: 5,
    }
    collaboration_advanced = {
        EntitlementKey.active_tasks: 5_000,
        EntitlementKey.task_participants: 10,
        EntitlementKey.task_reviews: 1,
        EntitlementKey.review_rounds: 25,
        EntitlementKey.task_reminder_rules: 5,
        EntitlementKey.meeting_participants: 25,
    }
    collaboration_evolution = {
        EntitlementKey.active_tasks: 20_000,
        EntitlementKey.task_participants: 25,
        EntitlementKey.task_reviews: 1,
        EntitlementKey.review_rounds: 100,
        EntitlementKey.task_reminder_rules: 10,
        EntitlementKey.meeting_participants: 100,
    }
    return (
        CommercialOfferPublishRequest(
            sku="crm_start",
            family=CommercialOfferFamily.crm,
            kind=CommercialOfferKind.base_plan,
            legacy_tier="starter",
            reason="Catálogo V1 inicializado pelo Super Admin.",
            snapshot=CommercialOfferSnapshotWrite(
                name="Start",
                description="Para começar com segurança.",
                is_public=False,
                price_monthly_cents=26_900,
                price_annual_cents=None,
                is_recommended=False,
                sort_order=10,
                cta="Quero uma demonstração",
                entitlements=_complete_policy(
                    {
                        EntitlementKey.users: 3,
                        EntitlementKey.workspaces: 1,
                        EntitlementKey.whatsapp_numbers: 1,
                        EntitlementKey.agents: 2,
                        EntitlementKey.automations: 10,
                        EntitlementKey.ai_conversations: 200,
                        EntitlementKey.campaign_recipients: 200,
                        EntitlementKey.contacts: 1_000,
                        EntitlementKey.ai_executions: 500,
                        EntitlementKey.tokens: 500_000,
                        EntitlementKey.audio_minutes: 20,
                        EntitlementKey.storage_bytes: 5 * gib,
                        EntitlementKey.files: 1_000,
                        **collaboration_start,
                    }
                ),
            ),
        ),
        CommercialOfferPublishRequest(
            sku="crm_advanced",
            family=CommercialOfferFamily.crm,
            kind=CommercialOfferKind.base_plan,
            legacy_tier="pro",
            reason="Catálogo V1 inicializado pelo Super Admin.",
            snapshot=CommercialOfferSnapshotWrite(
                name="Advanced",
                description="Crescimento estruturado.",
                is_public=False,
                price_monthly_cents=67_900,
                price_annual_cents=None,
                is_recommended=True,
                sort_order=20,
                cta="Quero uma demonstração",
                entitlements=_complete_policy(
                    {
                        EntitlementKey.users: 10,
                        EntitlementKey.workspaces: 3,
                        EntitlementKey.whatsapp_numbers: 3,
                        EntitlementKey.agents: 5,
                        EntitlementKey.automations: 50,
                        EntitlementKey.ai_conversations: 1_000,
                        EntitlementKey.campaign_recipients: 1_000,
                        EntitlementKey.contacts: 10_000,
                        EntitlementKey.ai_executions: 2_000,
                        EntitlementKey.tokens: 2_000_000,
                        EntitlementKey.audio_minutes: 60,
                        EntitlementKey.storage_bytes: 25 * gib,
                        EntitlementKey.files: 5_000,
                        **collaboration_advanced,
                    },
                    {
                        EntitlementKey.calendar,
                        EntitlementKey.email,
                        EntitlementKey.advanced_audit,
                    },
                ),
            ),
        ),
        CommercialOfferPublishRequest(
            sku="crm_evolution",
            family=CommercialOfferFamily.crm,
            kind=CommercialOfferKind.base_plan,
            legacy_tier="business",
            reason="Catálogo V1 inicializado pelo Super Admin.",
            snapshot=CommercialOfferSnapshotWrite(
                name="Evolution",
                description="Automação, workflows e todos os módulos Core.",
                is_public=False,
                price_monthly_cents=124_900,
                price_annual_cents=None,
                is_recommended=False,
                sort_order=30,
                cta="Quero uma demonstração",
                entitlements=_complete_policy(
                    {
                        EntitlementKey.users: 25,
                        EntitlementKey.workspaces: 4,
                        EntitlementKey.whatsapp_numbers: 10,
                        EntitlementKey.agents: 15,
                        EntitlementKey.automations: 200,
                        EntitlementKey.ai_conversations: 3_000,
                        EntitlementKey.campaign_recipients: 3_000,
                        EntitlementKey.contacts: 50_000,
                        EntitlementKey.ai_executions: 6_000,
                        EntitlementKey.tokens: 6_000_000,
                        EntitlementKey.audio_minutes: 120,
                        EntitlementKey.storage_bytes: 100 * gib,
                        EntitlementKey.files: 20_000,
                        **collaboration_evolution,
                    },
                    {
                        EntitlementKey.calendar,
                        EntitlementKey.email,
                        EntitlementKey.webhooks,
                        EntitlementKey.advanced_audit,
                        EntitlementKey.success_manager,
                    },
                ),
            ),
        ),
        CommercialOfferPublishRequest(
            sku="legal_360",
            family=CommercialOfferFamily.legal,
            kind=CommercialOfferKind.solution,
            legacy_tier=None,
            reason="Oferta jurídica combinável inicializada pelo Super Admin.",
            snapshot=CommercialOfferSnapshotWrite(
                name="Jurídico 360",
                description="Operação jurídica completa com limites definidos em proposta.",
                is_quote_only=True,
                is_recommended=False,
                sort_order=40,
                cta="Falar com especialista",
                entitlements=_complete_policy(
                    {
                        EntitlementKey.users: 1,
                        EntitlementKey.workspaces: 1,
                        EntitlementKey.storage_bytes: 5 * gib,
                        EntitlementKey.files: 1_000,
                        EntitlementKey.legal_areas: 1,
                        EntitlementKey.monitored_processes: 0,
                        **collaboration_start,
                    },
                    {EntitlementKey.legal_suite},
                ),
            ),
        ),
    )


class CommercialCatalogService:
    """Transactional command and query boundary for commercial state.

    Methods only ``flush``. The request/worker boundary owns commit/rollback so
    domain state, receipt and evidence remain atomic.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    def _require_manifest_workspace_context(self, workspace_id: UUID) -> None:
        """Require tenant RLS before reading per-workspace usage or overrides.

        Blackbox requests also carry an attributable platform-control context,
        but that context intentionally does not bypass the generic tenant RLS
        protecting ``workspace_entitlement_overrides`` and monthly usage.  A
        manifest is always a single-workspace operation, so the request or
        worker boundary must bind that exact workspace explicitly.  This
        service guard never manufactures authority from a caller-supplied UUID:
        missing or mismatched context fails closed instead of silently
        resolving an incomplete manifest.

        SQLite has no RLS and remains context-free for direct service tests;
        API endpoints bind the workspace explicitly on every dialect so their
        request-boundary invariant is testable without PostgreSQL.
        """

        if self.db.get_bind().dialect.name != "postgresql":
            return
        if get_workspace_context(self.db) != workspace_id:
            raise CommercialCatalogConflictError(
                "O contexto tenant da conta não foi vinculado antes de resolver "
                "o manifesto comercial."
            )

    @staticmethod
    def _hash(payload: Any) -> str:
        try:
            encoded = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise CommercialCatalogValidationError(
                "Payload comercial contém valor não canônico."
            ) from error
        return sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _snapshot_payload(snapshot: CommercialOfferSnapshotWrite) -> dict[str, Any]:
        return snapshot.model_dump(mode="json", by_alias=False)

    async def _existing_receipt(
        self,
        *,
        operation: str,
        scope_key: str,
        idempotency_key: str,
        request_hash: str,
    ) -> CommercialCommandReceipt | None:
        key = idempotency_key.strip()
        if not key or len(key) > 200 or not key.isprintable():
            raise CommercialCatalogValidationError(
                "Idempotency-Key deve ter entre 1 e 200 caracteres imprimíveis."
            )
        receipt = await self.db.scalar(
            select(CommercialCommandReceipt).where(
                CommercialCommandReceipt.operation == operation,
                CommercialCommandReceipt.scope_key == scope_key,
                CommercialCommandReceipt.idempotency_key == key,
            )
        )
        if receipt is not None and receipt.request_hash != request_hash:
            raise CommercialIdempotencyConflictError(
                "A chave de idempotência já foi usada com outro payload."
            )
        return receipt

    async def _lock_command(
        self,
        *,
        operation: str,
        scope_key: str,
        aggregate_key: str | None = None,
    ) -> None:
        """Serialize aggregate mutation and receipt lookup in PostgreSQL.

        ``operation`` remains part of the idempotency-receipt identity, but it
        must not be the lock namespace for account commercial state. Otherwise
        a contract revision, an adjustment and a bonus for the same workspace
        can interleave and each compute from a different snapshot.
        """

        await acquire_transaction_advisory_lock(
            self.db,
            namespace="commercial:workspace-aggregate",
            value=aggregate_key or scope_key,
        )
        await acquire_transaction_advisory_lock(
            self.db,
            namespace=f"commercial:receipt:{operation}",
            value=scope_key,
        )

    async def _lock_catalog_authority(self) -> None:
        """Fence the first canonical publication against legacy policy writers."""

        await acquire_transaction_advisory_lock(
            self.db,
            namespace=CATALOG_AUTHORITY_LOCK_NAMESPACE,
            value=CATALOG_AUTHORITY_LOCK_VALUE,
        )

    async def _record_receipt(
        self,
        *,
        operation: str,
        scope_key: str,
        idempotency_key: str,
        request_hash: str,
        response_payload: dict[str, Any],
        actor_user_id: UUID | None,
    ) -> None:
        self.db.add(
            CommercialCommandReceipt(
                operation=operation,
                scope_key=scope_key,
                idempotency_key=idempotency_key.strip(),
                request_hash=request_hash,
                response_payload=response_payload,
                actor_user_id=actor_user_id,
            )
        )
        await self.db.flush()

    def _event(
        self,
        *,
        event_type: str,
        actor_user_id: UUID | None,
        workspace_id: UUID | None,
        resource_type: str,
        resource_id: UUID | None,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        reason: str,
        correlation_id: str,
    ) -> None:
        self.db.add(
            CommercialCatalogEvent(
                event_type=event_type,
                actor_user_id=actor_user_id,
                workspace_id=workspace_id,
                resource_type=resource_type,
                resource_id=resource_id,
                before=before,
                after=after,
                reason=reason.strip(),
                correlation_id=correlation_id,
            )
        )

    async def bootstrap_default_catalog(
        self,
        *,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[list[CommercialOfferVersionRead], bool]:
        requests = _default_offer_requests()
        request_payload = [item.model_dump(mode="json") for item in requests]
        request_hash = self._hash(request_payload)
        await self._lock_catalog_authority()
        await self._lock_command(operation="bootstrap_catalog", scope_key="v1")
        receipt = await self._existing_receipt(
            operation="bootstrap_catalog",
            scope_key="v1",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt is not None:
            return (
                [
                    CommercialOfferVersionRead.model_validate(item)
                    for item in receipt.response_payload["items"]
                ],
                True,
            )

        published: list[CommercialOfferVersionRead] = []
        for request in requests:
            offer = await self.db.scalar(
                select(CommercialOffer).where(CommercialOffer.sku == request.sku)
            )
            if offer is None:
                offer = CommercialOffer(
                    sku=request.sku,
                    family=request.family,
                    kind=request.kind,
                    legacy_tier=request.legacy_tier,
                    created_by_user_id=actor_user_id,
                )
                self.db.add(offer)
                await self.db.flush()
            elif offer.family != request.family or offer.kind != request.kind:
                raise CommercialCatalogConflictError(
                    f"SKU {request.sku} já existe com outra família ou tipo."
                )

            latest = await self.db.scalar(
                select(CommercialOfferVersion)
                .where(CommercialOfferVersion.offer_id == offer.id)
                .order_by(CommercialOfferVersion.version.desc())
                .limit(1)
            )
            if latest is not None:
                # Bootstrap never silently republishes or reprices an existing SKU.
                published.append(self._offer_read(offer, latest))
                continue
            version = await self._insert_offer_version(
                offer=offer,
                snapshot=request.snapshot,
                reason=request.reason,
                actor_user_id=actor_user_id,
                version_number=1,
            )
            published.append(self._offer_read(offer, version))
            self._event(
                event_type="offer_published",
                actor_user_id=actor_user_id,
                workspace_id=None,
                resource_type="commercial_offer_version",
                resource_id=version.id,
                before=None,
                after=published[-1].model_dump(mode="json"),
                reason=request.reason,
                correlation_id=idempotency_key,
            )

        await self.db.flush()
        response = {"items": [item.model_dump(mode="json") for item in published]}
        await self._record_receipt(
            operation="bootstrap_catalog",
            scope_key="v1",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=response,
            actor_user_id=actor_user_id,
        )
        return published, False

    async def publish_offer(
        self,
        *,
        payload: CommercialOfferPublishRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[CommercialOfferVersionRead, bool]:
        request_hash = self._hash(payload.model_dump(mode="json"))
        await self._lock_catalog_authority()
        await self._lock_command(operation="publish_offer", scope_key=payload.sku)
        receipt = await self._existing_receipt(
            operation="publish_offer",
            scope_key=payload.sku,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt is not None:
            return CommercialOfferVersionRead.model_validate(receipt.response_payload), True

        # ``commercial_offers`` is an append-only identity table.  The
        # production runtime role intentionally has no UPDATE privilege, and
        # PostgreSQL requires that privilege even for ``SELECT ... FOR
        # UPDATE``.  ``_lock_command`` already holds the transaction-scoped
        # aggregate advisory lock for this SKU, so a row lock would be both
        # redundant and incompatible with the least-privilege runtime role.
        offer = await self._offer_by_sku(payload.sku)
        before: CommercialOfferVersionRead | None = None
        if offer is None:
            offer = CommercialOffer(
                sku=payload.sku,
                family=payload.family,
                kind=payload.kind,
                legacy_tier=payload.legacy_tier,
                created_by_user_id=actor_user_id,
            )
            self.db.add(offer)
            await self.db.flush()
            version_number = 1
        else:
            if offer.family != payload.family or offer.kind != payload.kind:
                raise CommercialCatalogConflictError(
                    "Família e tipo de um SKU publicado são imutáveis."
                )
            latest = await self.db.scalar(
                select(CommercialOfferVersion)
                .where(CommercialOfferVersion.offer_id == offer.id)
                .order_by(CommercialOfferVersion.version.desc())
                .limit(1)
            )
            if latest is None:
                version_number = 1
            else:
                before = self._offer_read(offer, latest)
                version_number = latest.version + 1
        version = await self._insert_offer_version(
            offer=offer,
            snapshot=payload.snapshot,
            reason=payload.reason,
            actor_user_id=actor_user_id,
            version_number=version_number,
        )
        response = self._offer_read(offer, version)
        self._event(
            event_type="offer_published",
            actor_user_id=actor_user_id,
            workspace_id=None,
            resource_type="commercial_offer_version",
            resource_id=version.id,
            before=before.model_dump(mode="json") if before is not None else None,
            after=response.model_dump(mode="json"),
            reason=payload.reason,
            correlation_id=idempotency_key,
        )
        await self.db.flush()
        await self._record_receipt(
            operation="publish_offer",
            scope_key=payload.sku,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=response.model_dump(mode="json"),
            actor_user_id=actor_user_id,
        )
        return response, False

    async def _offer_by_sku(self, sku: str) -> CommercialOffer | None:
        """Read an immutable offer identity under the aggregate advisory lock.

        Callers that mutate the catalog acquire ``_lock_command`` first.  Do
        not add ``FOR UPDATE`` here: the production runtime role deliberately
        cannot update this append-only table.
        """

        return await self.db.scalar(
            select(CommercialOffer).where(CommercialOffer.sku == sku)
        )

    async def _offer_and_latest(
        self,
        sku: str,
    ) -> tuple[CommercialOffer, CommercialOfferVersion]:
        offer = await self._offer_by_sku(sku)
        if offer is None:
            raise CommercialCatalogNotFoundError(f"Oferta {sku} não encontrada.")
        latest = await self.db.scalar(
            select(CommercialOfferVersion)
            .where(CommercialOfferVersion.offer_id == offer.id)
            .order_by(CommercialOfferVersion.version.desc())
            .limit(1)
        )
        if latest is None:
            raise CommercialCatalogNotFoundError(f"Oferta {sku} ainda não foi publicada.")
        return offer, latest

    async def _latest_draft_event(
        self,
        offer_id: UUID,
    ) -> CommercialCatalogEvent | None:
        events = list(
            (
                await self.db.scalars(
                    select(CommercialCatalogEvent).where(
                        CommercialCatalogEvent.resource_type
                        == "commercial_offer_draft",
                        CommercialCatalogEvent.resource_id == offer_id,
                        CommercialCatalogEvent.event_type.in_(
                            [
                                "offer_draft_saved",
                                "offer_draft_discarded",
                                "offer_draft_published",
                            ]
                        ),
                    )
                )
            ).all()
        )
        if not events:
            return None
        # ``occurred_at`` is a database timestamp and multiple events in one
        # transaction can share it. UUID order is random, so the versioned
        # draft's explicit ``updated_at`` is the deterministic chronology.
        return max(
            events,
            key=lambda event: (
                self._draft_from_event(event).updated_at,
                str(event.id),
            ),
        )

    @staticmethod
    def _draft_from_event(event: CommercialCatalogEvent) -> CommercialOfferDraftRead:
        if event.after is None:
            raise CommercialCatalogValidationError("Evento de rascunho sem snapshot.")
        return CommercialOfferDraftRead.model_validate(event.after)

    async def get_offer_draft(self, sku: str) -> CommercialOfferDraftRead:
        offer, _ = await self._offer_and_latest(sku)
        event = await self._latest_draft_event(offer.id)
        if event is None:
            raise CommercialCatalogNotFoundError("Oferta sem rascunho ativo.")
        draft = self._draft_from_event(event)
        if draft.status != "active":
            raise CommercialCatalogNotFoundError("Oferta sem rascunho ativo.")
        return draft

    async def save_offer_draft(
        self,
        *,
        sku: str,
        payload: CommercialOfferDraftSaveRequest,
        expected_draft_version: int | None,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[CommercialOfferDraftRead, bool]:
        request_payload = {
            **payload.model_dump(mode="json"),
            "expected_draft_version": expected_draft_version,
        }
        request_hash = self._hash(request_payload)
        await self._lock_command(operation="save_offer_draft", scope_key=sku)
        receipt = await self._existing_receipt(
            operation="save_offer_draft",
            scope_key=sku,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt is not None:
            return CommercialOfferDraftRead.model_validate(receipt.response_payload), True

        offer, latest = await self._offer_and_latest(sku)
        if latest.version != payload.expected_published_version:
            raise CommercialCatalogConflictError(
                "A oferta publicada mudou. Recarregue antes de salvar o rascunho."
            )
        self._validate_canonical_policy(payload.snapshot)
        if offer.family is CommercialOfferFamily.legal:
            self._validate_legal_boundary(payload.snapshot)

        previous_event = await self._latest_draft_event(offer.id)
        previous = self._draft_from_event(previous_event) if previous_event is not None else None
        active_previous = previous if previous is not None and previous.status == "active" else None
        if active_previous is not None:
            if expected_draft_version is None:
                raise CommercialCatalogConflictError(
                    "Envie If-Match com a versão atual do rascunho."
                )
            if expected_draft_version != active_previous.draft_version:
                raise CommercialCatalogConflictError(
                    "O rascunho mudou. Recarregue antes de salvar."
                )
            draft_id = active_previous.id
            draft_version = active_previous.draft_version + 1
        else:
            if expected_draft_version not in {None, 0}:
                raise CommercialCatalogConflictError(
                    "O rascunho informado não existe mais. Recarregue a oferta."
                )
            draft_id = uuid4()
            draft_version = 1

        raw = self._snapshot_payload(payload.snapshot)
        draft = CommercialOfferDraftRead(
            id=draft_id,
            offer_id=offer.id,
            sku=offer.sku,
            draft_version=draft_version,
            base_published_version=latest.version,
            snapshot=payload.snapshot,
            snapshot_hash=self._hash(raw),
            status="active",
            reason=payload.reason.strip(),
            updated_by_user_id=actor_user_id,
            updated_at=datetime.now(UTC),
        )
        self._event(
            event_type="offer_draft_saved",
            actor_user_id=actor_user_id,
            workspace_id=None,
            resource_type="commercial_offer_draft",
            resource_id=offer.id,
            before=active_previous.model_dump(mode="json") if active_previous else None,
            after=draft.model_dump(mode="json"),
            reason=payload.reason,
            correlation_id=idempotency_key,
        )
        await self.db.flush()
        await self._record_receipt(
            operation="save_offer_draft",
            scope_key=sku,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=draft.model_dump(mode="json"),
            actor_user_id=actor_user_id,
        )
        return draft, False

    async def discard_offer_draft(
        self,
        *,
        sku: str,
        expected_draft_version: int,
        reason: str,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[CommercialOfferDraftRead, bool]:
        request_payload = {
            "expected_draft_version": expected_draft_version,
            "reason": reason.strip(),
        }
        request_hash = self._hash(request_payload)
        await self._lock_command(operation="discard_offer_draft", scope_key=sku)
        receipt = await self._existing_receipt(
            operation="discard_offer_draft",
            scope_key=sku,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt is not None:
            return CommercialOfferDraftRead.model_validate(receipt.response_payload), True
        offer, _ = await self._offer_and_latest(sku)
        current = await self.get_offer_draft(sku)
        if current.draft_version != expected_draft_version:
            raise CommercialCatalogConflictError(
                "O rascunho mudou. Recarregue antes de descartá-lo."
            )
        discarded = current.model_copy(
            update={
                "status": "discarded",
                "reason": reason.strip(),
                "updated_by_user_id": actor_user_id,
                "updated_at": datetime.now(UTC),
            }
        )
        self._event(
            event_type="offer_draft_discarded",
            actor_user_id=actor_user_id,
            workspace_id=None,
            resource_type="commercial_offer_draft",
            resource_id=offer.id,
            before=current.model_dump(mode="json"),
            after=discarded.model_dump(mode="json"),
            reason=reason,
            correlation_id=idempotency_key,
        )
        await self.db.flush()
        await self._record_receipt(
            operation="discard_offer_draft",
            scope_key=sku,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=discarded.model_dump(mode="json"),
            actor_user_id=actor_user_id,
        )
        return discarded, False

    async def preview_offer_draft(self, sku: str) -> CommercialOfferDraftPreviewRead:
        offer, latest = await self._offer_and_latest(sku)
        draft = await self.get_offer_draft(sku)
        published = self._offer_read(offer, latest)
        stale = draft.base_published_version != published.version
        warnings = ["A oferta publicada mudou depois da criação do rascunho."] if stale else []
        unit_economics = await CommercialUnitEconomicsService(self.db).evaluate(
            self._upgrade_snapshot(draft.snapshot)
        )
        diff = _semantic_diff(
            published.snapshot.model_dump(mode="json"),
            draft.snapshot.model_dump(mode="json"),
        )
        return CommercialOfferDraftPreviewRead(
            draft=draft,
            published=published,
            diff=diff,
            warnings=warnings,
            unit_economics=unit_economics,
            eligible=bool(diff) and not stale and unit_economics.eligible,
        )

    async def publish_offer_draft(
        self,
        *,
        sku: str,
        expected_draft_version: int,
        payload: CommercialOfferDraftPublishRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[CommercialOfferVersionRead, bool]:
        request_payload = {
            **payload.model_dump(mode="json"),
            "expected_draft_version": expected_draft_version,
        }
        request_hash = self._hash(request_payload)
        await self._lock_command(operation="publish_offer_draft", scope_key=sku)
        receipt = await self._existing_receipt(
            operation="publish_offer_draft",
            scope_key=sku,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt is not None:
            return CommercialOfferVersionRead.model_validate(receipt.response_payload), True

        offer, latest = await self._offer_and_latest(sku)
        draft = await self.get_offer_draft(sku)
        if draft.draft_version != expected_draft_version:
            raise CommercialCatalogConflictError("O rascunho mudou. Recarregue antes de publicar.")
        if draft.base_published_version != latest.version:
            raise CommercialCatalogConflictError(
                "A oferta publicada mudou. Gere um novo rascunho sobre a revisão atual."
            )
        version = await self._insert_offer_version(
            offer=offer,
            snapshot=self._upgrade_snapshot(draft.snapshot),
            reason=payload.reason,
            actor_user_id=actor_user_id,
            version_number=latest.version + 1,
        )
        response = self._offer_read(offer, version)
        self._event(
            event_type="offer_published",
            actor_user_id=actor_user_id,
            workspace_id=None,
            resource_type="commercial_offer_version",
            resource_id=version.id,
            before=self._offer_read(offer, latest).model_dump(mode="json"),
            after=response.model_dump(mode="json"),
            reason=payload.reason,
            correlation_id=idempotency_key,
        )
        published_draft = draft.model_copy(
            update={
                "status": "published",
                "reason": payload.reason.strip(),
                "updated_by_user_id": actor_user_id,
                "updated_at": datetime.now(UTC),
            }
        )
        self._event(
            event_type="offer_draft_published",
            actor_user_id=actor_user_id,
            workspace_id=None,
            resource_type="commercial_offer_draft",
            resource_id=offer.id,
            before=draft.model_dump(mode="json"),
            after=published_draft.model_dump(mode="json"),
            reason=payload.reason,
            correlation_id=idempotency_key,
        )
        await self.db.flush()
        await self._record_receipt(
            operation="publish_offer_draft",
            scope_key=sku,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=response.model_dump(mode="json"),
            actor_user_id=actor_user_id,
        )
        return response, False

    async def offer_revision_history(self, sku: str) -> CommercialOfferRevisionHistoryRead:
        offer = await self.db.scalar(select(CommercialOffer).where(CommercialOffer.sku == sku))
        if offer is None:
            raise CommercialCatalogNotFoundError(f"Oferta {sku} não encontrada.")
        versions = list(
            (
                await self.db.scalars(
                    select(CommercialOfferVersion)
                    .where(CommercialOfferVersion.offer_id == offer.id)
                    .order_by(CommercialOfferVersion.version.desc())
                )
            ).all()
        )
        items: list[CommercialOfferRevisionHistoryItem] = []
        ascending = list(reversed(versions))
        previous: CommercialOfferVersionRead | None = None
        for version in ascending:
            current = self._offer_read(offer, version)
            items.append(
                CommercialOfferRevisionHistoryItem(
                    offer=current,
                    diff=_semantic_diff(
                        previous.snapshot.model_dump(mode="json") if previous else {},
                        current.snapshot.model_dump(mode="json"),
                    ),
                )
            )
            previous = current
        return CommercialOfferRevisionHistoryRead(sku=sku, items=list(reversed(items)))

    async def preview_offer_restore(
        self,
        *,
        sku: str,
        target_version: int,
        expected_current_version: int,
    ) -> CommercialOfferRestorePreviewRead:
        offer, latest = await self._offer_and_latest(sku)
        if latest.version != expected_current_version:
            raise CommercialCatalogConflictError(
                "A oferta mudou. Gere uma nova prévia de restauração."
            )
        target = await self.db.scalar(
            select(CommercialOfferVersion).where(
                CommercialOfferVersion.offer_id == offer.id,
                CommercialOfferVersion.version == target_version,
            )
        )
        if target is None:
            raise CommercialCatalogNotFoundError("Revisão alvo não encontrada.")
        current_read = self._offer_read(offer, latest)
        target_read = self._offer_read(offer, target)
        unit_economics = await CommercialUnitEconomicsService(self.db).evaluate(
            self._upgrade_snapshot(target_read.snapshot)
        )
        diff = _semantic_diff(
            current_read.snapshot.model_dump(mode="json"),
            target_read.snapshot.model_dump(mode="json"),
        )
        return CommercialOfferRestorePreviewRead(
            sku=sku,
            current=current_read,
            target=target_read,
            diff=diff,
            warnings=[
                "A restauração publica uma nova revisão; contratos existentes "
                "não são reprificados.",
                *(blocker.message for blocker in unit_economics.blockers),
            ],
            unit_economics=unit_economics,
            eligible=bool(diff) and unit_economics.eligible,
        )

    async def restore_offer(
        self,
        *,
        sku: str,
        payload: CommercialOfferRestoreRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[CommercialOfferVersionRead, bool]:
        request_hash = self._hash(payload.model_dump(mode="json"))
        await self._lock_command(operation="restore_offer", scope_key=sku)
        receipt = await self._existing_receipt(
            operation="restore_offer",
            scope_key=sku,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt is not None:
            return CommercialOfferVersionRead.model_validate(receipt.response_payload), True
        offer, latest = await self._offer_and_latest(sku)
        if latest.version != payload.expected_current_version:
            raise CommercialCatalogConflictError(
                "A oferta mudou. Gere uma nova prévia de restauração."
            )
        target = await self.db.scalar(
            select(CommercialOfferVersion).where(
                CommercialOfferVersion.offer_id == offer.id,
                CommercialOfferVersion.version == payload.target_version,
            )
        )
        if target is None:
            raise CommercialCatalogNotFoundError("Revisão alvo não encontrada.")
        if target.snapshot_hash == latest.snapshot_hash:
            raise CommercialCatalogValidationError("A revisão alvo já corresponde à publicada.")
        version = await self._insert_offer_version(
            offer=offer,
            snapshot=self._upgrade_snapshot(self._version_snapshot(target)),
            reason=payload.reason,
            actor_user_id=actor_user_id,
            version_number=latest.version + 1,
        )
        response = self._offer_read(offer, version)
        self._event(
            event_type="offer_restored",
            actor_user_id=actor_user_id,
            workspace_id=None,
            resource_type="commercial_offer_version",
            resource_id=version.id,
            before=self._offer_read(offer, latest).model_dump(mode="json"),
            after=response.model_dump(mode="json"),
            reason=payload.reason,
            correlation_id=idempotency_key,
        )
        await self.db.flush()
        await self._record_receipt(
            operation="restore_offer",
            scope_key=sku,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=response.model_dump(mode="json"),
            actor_user_id=actor_user_id,
        )
        return response, False

    async def _insert_offer_version(
        self,
        *,
        offer: CommercialOffer,
        snapshot: CommercialOfferSnapshotWrite,
        reason: str,
        actor_user_id: UUID | None,
        version_number: int,
    ) -> CommercialOfferVersion:
        self._validate_canonical_policy(snapshot)
        if offer.family is CommercialOfferFamily.legal:
            self._validate_legal_boundary(snapshot)
        unit_economics = await CommercialUnitEconomicsService(self.db).evaluate(snapshot)
        if not unit_economics.eligible:
            diagnostics = (
                "; ".join(blocker.message for blocker in unit_economics.blockers)
                or "A avaliação de unit economics não autorizou a publicação."
            )
            raise CommercialCatalogValidationError(
                "Oferta pública com IA bloqueada pelo gate de unit economics: " + diagnostics
            )
        raw = self._snapshot_payload(snapshot)
        version = CommercialOfferVersion(
            offer_id=offer.id,
            version=version_number,
            schema_version=snapshot.schema_version,
            name=snapshot.name,
            description=snapshot.description,
            is_public=snapshot.is_public,
            is_active=snapshot.is_active,
            is_recommended=snapshot.is_recommended,
            is_quote_only=snapshot.is_quote_only,
            available_intervals=[item.value for item in snapshot.available_intervals],
            sort_order=snapshot.sort_order,
            currency=snapshot.currency,
            price_monthly_cents=snapshot.price_monthly_cents,
            price_annual_cents=snapshot.price_annual_cents,
            cta=snapshot.cta,
            entitlement_policy=raw["entitlements"],
            snapshot_hash=self._hash(raw),
            publication_reason=reason.strip(),
            published_by_user_id=actor_user_id,
        )
        self.db.add(version)
        await self.db.flush()
        return version

    @staticmethod
    def _validate_canonical_policy(snapshot: CommercialOfferSnapshotWrite) -> None:
        """Reject commercial rewrites of technical entitlement dimensions."""

        for key, setting in snapshot.entitlements.items():
            expected = (
                ENTITLEMENT_CATALOG[key].unit.value,
                _period_for(key),
                _composition_for(key),
            )
            observed = (setting.unit, setting.period, setting.composition)
            if observed != expected:
                raise CommercialCatalogValidationError(
                    f"{key.value} possui unidade, período ou composição não canônicos."
                )
            if (
                key in QUANTITATIVE_ENTITLEMENTS
                and setting.enabled
                and setting.limit_quantity is None
            ):
                raise CommercialCatalogValidationError(
                    f"{key.value} exige quota finita e não negativa."
                )

    @staticmethod
    def _validate_legal_boundary(snapshot: CommercialOfferSnapshotWrite) -> None:
        enabled_without_consumer = sorted(
            key.value
            for key in _UNENFORCED_LEGAL_CAPABILITIES
            if snapshot.entitlements[key].enabled
        )
        if enabled_without_consumer:
            raise CommercialCatalogValidationError(
                "Jurídico 360 não pode publicar capacidades sem enforcement operacional: "
                + ", ".join(enabled_without_consumer)
            )

    @staticmethod
    def _version_snapshot(version: CommercialOfferVersion) -> CommercialOfferSnapshotRead:
        snapshot_type: type[CommercialOfferSnapshotV2Read] | type[CommercialOfferSnapshotWrite]
        if version.schema_version == "2.0":
            snapshot_type = CommercialOfferSnapshotV2Read
        elif version.schema_version == "3.0":
            snapshot_type = CommercialOfferSnapshotWrite
        else:
            raise CommercialCatalogValidationError(
                f"Schema comercial {version.schema_version!r} não possui leitor registrado."
            )
        try:
            return snapshot_type(
                schema_version=version.schema_version,
                name=version.name,
                description=version.description,
                is_public=version.is_public,
                is_active=version.is_active,
                is_recommended=version.is_recommended,
                is_quote_only=version.is_quote_only,
                available_intervals=version.available_intervals,
                sort_order=version.sort_order,
                currency=version.currency,
                price_monthly_cents=version.price_monthly_cents,
                price_annual_cents=version.price_annual_cents,
                cta=version.cta,
                entitlements=version.entitlement_policy,
            )
        except ValueError as error:
            raise CommercialCatalogValidationError(
                f"Snapshot comercial {version.id} não corresponde ao schema "
                f"{version.schema_version} persistido."
            ) from error

    @staticmethod
    def _upgrade_snapshot(
        snapshot: CommercialOfferSnapshotRead,
    ) -> CommercialOfferSnapshotWrite:
        """Project V2 into a new V3 revision without rewriting V2 history."""

        if isinstance(snapshot, CommercialOfferSnapshotWrite):
            return snapshot
        entitlements = dict(snapshot.entitlements)
        entitlements[EntitlementKey.contacts] = _setting(
            EntitlementKey.contacts,
            enabled=False,
        )
        payload = snapshot.model_dump(
            mode="python",
            exclude={"schema_version", "entitlements"},
        )
        return CommercialOfferSnapshotWrite(
            **payload,
            schema_version="3.0",
            entitlements=entitlements,
        )

    @classmethod
    def _offer_read(
        cls, offer: CommercialOffer, version: CommercialOfferVersion
    ) -> CommercialOfferVersionRead:
        return CommercialOfferVersionRead(
            id=version.id,
            offer_id=offer.id,
            sku=offer.sku,
            family=offer.family,
            kind=offer.kind,
            legacy_tier=offer.legacy_tier,
            version=version.version,
            snapshot=cls._version_snapshot(version),
            snapshot_hash=version.snapshot_hash,
            publication_reason=version.publication_reason,
            published_by_user_id=version.published_by_user_id,
            published_at=version.published_at,
        )

    async def list_offers(
        self, *, limit: int = 50, offset: int = 0, public_only: bool = False
    ) -> tuple[list[CommercialOfferVersionRead], int]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        latest = (
            select(
                CommercialOfferVersion.offer_id.label("offer_id"),
                func.max(CommercialOfferVersion.version).label("version"),
            )
            .group_by(CommercialOfferVersion.offer_id)
            .subquery()
        )
        conditions = []
        if public_only:
            conditions.extend(
                [
                    CommercialOfferVersion.is_public.is_(True),
                    CommercialOfferVersion.is_active.is_(True),
                ]
            )
        base = (
            select(CommercialOffer, CommercialOfferVersion)
            .join(latest, latest.c.offer_id == CommercialOffer.id)
            .join(
                CommercialOfferVersion,
                and_(
                    CommercialOfferVersion.offer_id == latest.c.offer_id,
                    CommercialOfferVersion.version == latest.c.version,
                ),
            )
            .where(*conditions)
        )
        if public_only:
            # Immutable revisions created before this gate can still carry an
            # old ``is_public`` bit. Revalidate the latest snapshot at the
            # public read boundary and suppress it until current evidence is
            # sufficient; a historical flag is not economic proof.
            candidate_rows = (
                await self.db.execute(
                    base.order_by(
                        CommercialOfferVersion.sort_order.asc(),
                        CommercialOffer.sku.asc(),
                    )
                )
            ).all()
            eligible: list[CommercialOfferVersionRead] = []
            evaluator = CommercialUnitEconomicsService(self.db)
            for offer, version in candidate_rows:
                item = self._offer_read(offer, version)
                if (await evaluator.evaluate(item.snapshot)).eligible:
                    eligible.append(item)
            return eligible[offset : offset + limit], len(eligible)

        total = int(await self.db.scalar(select(func.count()).select_from(base.subquery())) or 0)
        rows = (
            await self.db.execute(
                base.order_by(CommercialOfferVersion.sort_order.asc(), CommercialOffer.sku.asc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [self._offer_read(offer, version) for offer, version in rows], total

    async def preview_contract(
        self,
        payload: WorkspaceContractPreviewRequest,
        *,
        allow_legacy_import: bool = False,
    ) -> WorkspaceContractPreviewRead:
        workspace = await self.db.get(Workspace, payload.workspace_id)
        if workspace is None or workspace.deleted_at is not None:
            raise CommercialCatalogNotFoundError("Workspace não encontrado.")
        resolved = await self._resolve_line_inputs(payload.lines, legacy_import=allow_legacy_import)
        now = datetime.now(UTC)
        transient_lines = [self._line_read_from_resolved(item, now=now) for item in resolved]
        contract_id = uuid4()
        manifest = await self._build_manifest(
            workspace_id=payload.workspace_id,
            contract_id=contract_id,
            contract_version=1,
            contract_status=CommercialContractStatus.active,
            lines=transient_lines,
            generated_at=now,
        )
        list_price_cents = sum(
            _monthly_equivalent_cents(
                item.list_price_cents * item.input.quantity,
                item.input.billing_interval,
            )
            for item in resolved
        )
        contracted_price_cents = sum(
            _monthly_equivalent_cents(
                item.contracted_price_cents * item.input.quantity,
                item.input.billing_interval,
            )
            for item in resolved
        )
        price = PricePreviewRead(
            list_price_cents=list_price_cents,
            contracted_price_cents=contracted_price_cents,
            recurring_price_cents=contracted_price_cents,
            invoice_credit_cents=0,
            amount_due_cents=contracted_price_cents,
        )
        warnings = [
            "Contrato anual preservado como importação legada; novas vendas continuam mensais."
            for item in resolved
            if item.input.billing_interval is CommercialBillingInterval.annual
        ]
        return WorkspaceContractPreviewRead(
            workspace_id=payload.workspace_id,
            lines=transient_lines,
            price=price,
            manifest=manifest,
            warnings=warnings,
        )

    async def preview_contract_revision(
        self,
        contract_id: UUID,
        payload: WorkspaceContractRevisionPreviewRequest,
        *,
        preserve_exceptions: bool = False,
    ) -> WorkspaceContractRevisionPreviewRead:
        contract = await self.db.get(WorkspaceCommercialContract, contract_id)
        if contract is None:
            raise CommercialContractNotFoundError("Contrato comercial não encontrado.")
        if contract.version != payload.expected_version:
            raise CommercialCatalogConflictError(
                "A versão do contrato mudou. Gere uma nova prévia antes de confirmar."
            )
        plan = await self._prepare_contract_revision(
            contract,
            payload.lines,
            preserve_exceptions=preserve_exceptions,
        )
        current_price = await self._price_preview(contract, plan.current_lines)
        target_price = self._calculate_price(
            plan.target_lines,
            [
                item
                for item in plan.adjustments
                if item.id not in plan.adjustment_ids_to_revoke
                and _active_at(item.starts_at, item.ends_at, datetime.now(UTC))
                # Legacy bounded-cycle rows cannot be consumed safely until a
                # per-billing-cycle idempotency ledger exists. Do not turn an
                # unconsumed counter into a perpetual discount.
                and item.remaining_cycles is None
            ],
            line_id_remap=plan.replacement_line_ids,
        )
        manifest = await self._build_manifest(
            workspace_id=contract.workspace_id,
            contract_id=contract.id,
            contract_version=contract.version + 1,
            contract_status=contract.status,
            lines=plan.target_lines,
            generated_at=datetime.now(UTC),
            bonuses_override=plan.preview_bonuses,
        )
        return WorkspaceContractRevisionPreviewRead(
            contract_id=contract.id,
            workspace_id=contract.workspace_id,
            current_version=contract.version,
            target_version=contract.version + 1,
            current_lines=plan.current_lines,
            lines=plan.target_lines,
            current_price=current_price,
            price=target_price,
            manifest=manifest,
            changed_families=sorted(plan.changed_families, key=lambda item: item.value),
            warnings=plan.warnings,
            blockers=plan.blockers,
            eligible=bool(plan.changed_families) and not plan.blockers,
        )

    async def revise_contract(
        self,
        *,
        contract_id: UUID,
        expected_version: int,
        payload: WorkspaceContractRevisionRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
        operation: str = "revise_contract",
        event_type: str = "contract_revised",
        preserve_exceptions: bool = False,
        target_status: CommercialContractStatus | None = None,
        effective_at: datetime | None = None,
        portability_context: Callable[[], AbstractAsyncContextManager[None]] | None = None,
        restore_portability_context: (
            Callable[[], AbstractAsyncContextManager[None]] | None
        ) = None,
    ) -> tuple[WorkspaceContractRead, bool]:
        request_payload = {
            **payload.model_dump(mode="json"),
            "expected_version": expected_version,
            "effective_at": (
                _as_utc(effective_at).isoformat() if effective_at is not None else None
            ),
        }
        request_hash = self._hash(request_payload)
        scope = str(contract_id)
        workspace_id = await self.db.scalar(
            select(WorkspaceCommercialContract.workspace_id).where(
                WorkspaceCommercialContract.id == contract_id
            )
        )
        if workspace_id is None:
            raise CommercialContractNotFoundError("Contrato comercial não encontrado.")
        await self._lock_command(
            operation=operation,
            scope_key=scope,
            aggregate_key=str(workspace_id),
        )
        receipt = await self._existing_receipt(
            operation=operation,
            scope_key=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt is not None:
            replay = WorkspaceContractRead.model_validate(receipt.response_payload)
            return replay.model_copy(update={"idempotent_replay": True}), True

        contract = await self.db.scalar(
            select(WorkspaceCommercialContract)
            .where(WorkspaceCommercialContract.id == contract_id)
            .with_for_update()
        )
        if contract is None:
            raise CommercialContractNotFoundError("Contrato comercial não encontrado.")
        if contract.version != expected_version:
            raise CommercialCatalogConflictError(
                "A versão do contrato mudou. Gere uma nova prévia antes de confirmar."
            )
        plan = await self._prepare_contract_revision(
            contract,
            payload.lines,
            preserve_exceptions=preserve_exceptions,
        )
        status_changed = target_status is not None and contract.status != target_status
        if not plan.changed_families and not status_changed:
            raise CommercialCatalogValidationError("A revisão não altera o contrato vigente.")
        if plan.blockers:
            raise CommercialCatalogConflictError(" ".join(plan.blockers))
        if restore_portability_context is not None and not preserve_exceptions:
            raise CommercialCatalogValidationError(
                "Contexto de rebind de restauração só pode ser usado por restore_contract."
            )

        before = await self.get_contract(contract.id)
        now = _as_utc(effective_at) if effective_at is not None else datetime.now(UTC)
        current_models = list(
            (
                await self.db.scalars(
                    select(WorkspaceContractLine)
                    .where(
                        WorkspaceContractLine.contract_id == contract.id,
                        WorkspaceContractLine.status == CommercialContractLineStatus.active,
                    )
                    .with_for_update()
                )
            ).all()
        )
        for line in current_models:
            if line.offer_family not in plan.changed_families:
                continue
            current_start = _as_utc(line.effective_from)
            if now < current_start:
                raise CommercialCatalogConflictError(
                    "A data efetiva não pode anteceder a linha comercial vigente."
                )
            line.status = CommercialContractLineStatus.cancelled
            # The persisted interval is strictly open-ended. A deterministic
            # test clock (or two commands in the same instant) may replace a
            # line exactly when it began; retain a valid historical interval
            # without moving the replacement away from the command clock.
            line.effective_until = max(now, current_start + timedelta(microseconds=1))
            line.version += 1
            line.updated_by_user_id = actor_user_id
        # Flush cancellations before inserting a replacement for a family so
        # the database partial unique index remains the final arbiter.
        await self.db.flush()

        for item in plan.resolved_changed:
            self.db.add(
                WorkspaceContractLine(
                    id=item.line_id,
                    contract_id=contract.id,
                    offer_id=item.offer.id,
                    policy_version_id=item.version.id,
                    offer_family=item.offer.family,
                    quantity=item.input.quantity,
                    list_price_cents=item.list_price_cents,
                    contracted_price_cents=item.contracted_price_cents,
                    billing_interval=item.input.billing_interval,
                    entitlement_overrides={
                        key.value: setting.model_dump(mode="json")
                        for key, setting in item.input.entitlement_overrides.items()
                    },
                    effective_from=item.input.effective_from or now,
                    effective_until=item.input.effective_until,
                    status=CommercialContractLineStatus.active,
                    version=1,
                    created_by_user_id=actor_user_id,
                    updated_by_user_id=actor_user_id,
                )
            )
        await self.db.flush()

        exception_events: list[
            tuple[
                str,
                str,
                UUID,
                dict[str, Any],
                dict[str, Any],
            ]
        ] = []
        bonuses_to_revoke: list[EntitlementBonus] = []
        if plan.bonus_ids_to_revoke:
            # Load and lock under the already-authorized contract operation.
            # The internal portability scope below is intentionally UPDATE-only.
            bonuses_to_revoke = list(
                (
                    await self.db.scalars(
                        select(EntitlementBonus)
                        .where(EntitlementBonus.id.in_(plan.bonus_ids_to_revoke))
                        .with_for_update()
                    )
                ).all()
            )

        async def apply_portability_updates() -> None:
            for adjustment in plan.adjustments:
                adjustment_before = CommercialAdjustmentRead.model_validate(
                    adjustment
                ).model_dump(mode="json")
                if adjustment.id in plan.adjustment_ids_to_revoke:
                    adjustment.status = CommercialRecordStatus.revoked
                    adjustment.version += 1
                    event_name = "commercial_adjustment_auto_revoked"
                elif adjustment.id in (
                    plan.adjustment_ids_to_rebind
                    | plan.restore_adjustment_ids_to_rebind
                ):
                    if adjustment.contract_line_id not in plan.replacement_line_ids:
                        raise CommercialCatalogConflictError(
                            "Plano de portabilidade referencia ajuste sem linha substituta."
                        )
                    adjustment.contract_line_id = plan.replacement_line_ids[
                        adjustment.contract_line_id
                    ]
                    adjustment.version += 1
                    event_name = "commercial_adjustment_auto_rebound"
                else:
                    continue
                exception_events.append(
                    (
                        event_name,
                        "commercial_adjustment",
                        adjustment.id,
                        adjustment_before,
                        CommercialAdjustmentRead.model_validate(adjustment).model_dump(
                            mode="json"
                        ),
                    )
                )
            for bonus in bonuses_to_revoke:
                bonus_before = EntitlementBonusRead.model_validate(bonus).model_dump(
                    mode="json"
                )
                bonus.status = CommercialRecordStatus.revoked
                bonus.version += 1
                exception_events.append(
                    (
                        "entitlement_bonus_auto_revoked",
                        "entitlement_bonus",
                        bonus.id,
                        bonus_before,
                        EntitlementBonusRead.model_validate(bonus).model_dump(
                            mode="json"
                        ),
                    )
                )
            # Force automatic exception UPDATEs while the selected bounded
            # portability RLS context is still active.
            await self.db.flush()

        has_restore_rebind = bool(plan.restore_adjustment_ids_to_rebind)
        has_normal_portability_updates = bool(
            plan.adjustment_ids_to_revoke
            or plan.adjustment_ids_to_rebind
            or plan.bonus_ids_to_revoke
        )
        if has_restore_rebind and has_normal_portability_updates:
            raise CommercialCatalogConflictError(
                "Plano de revisão misturou portabilidade normal e restauração."
            )
        selected_portability_context = (
            restore_portability_context
            if has_restore_rebind
            else portability_context
            if has_normal_portability_updates
            else None
        )
        if selected_portability_context is None:
            await apply_portability_updates()
        else:
            async with selected_portability_context():
                await apply_portability_updates()

        contract.version += 1
        if target_status is not None:
            contract.status = target_status
        contract.updated_by_user_id = actor_user_id
        await self.db.flush()
        await self.db.refresh(contract)
        response = await self.get_contract(contract.id)
        for child_event, resource_type, resource_id, child_before, child_after in exception_events:
            self._event(
                event_type=child_event,
                actor_user_id=actor_user_id,
                workspace_id=contract.workspace_id,
                resource_type=resource_type,
                resource_id=resource_id,
                before=child_before,
                after=child_after,
                reason=payload.reason,
                correlation_id=idempotency_key,
            )
        self._event(
            event_type=event_type,
            actor_user_id=actor_user_id,
            workspace_id=contract.workspace_id,
            resource_type="workspace_commercial_contract",
            resource_id=contract.id,
            before=before.model_dump(mode="json"),
            after=response.model_dump(mode="json"),
            reason=payload.reason,
            correlation_id=idempotency_key,
        )
        await self.db.flush()
        await self._record_receipt(
            operation=operation,
            scope_key=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=response.model_dump(mode="json"),
            actor_user_id=actor_user_id,
        )
        return response, False

    async def _prepare_contract_revision(
        self,
        contract: WorkspaceCommercialContract,
        inputs: list[WorkspaceContractLineWrite],
        *,
        preserve_exceptions: bool = False,
    ) -> _ContractRevisionPlan:
        current_lines = await self._load_line_reads(contract.id)
        now = datetime.now(UTC)
        effective_lines = [
            item
            for item in current_lines
            if item.status is CommercialContractLineStatus.active
            and _active_at(item.effective_from, item.effective_until, now)
        ]
        active_families = [item.offer_family for item in effective_lines]
        if len(active_families) != len(set(active_families)):
            raise CommercialCatalogConflictError(
                "Contrato possui mais de uma linha ativa e vigente para a mesma família."
            )
        current_active = {item.offer_family: item for item in effective_lines}
        resolved = await self._resolve_line_inputs(
            inputs,
            legacy_import=False,
            preserved_annual_lines=current_active,
        )
        target_lines: list[WorkspaceContractLineRead] = []
        resolved_changed: list[_ResolvedLine] = []
        changed_families: set[CommercialOfferFamily] = set(current_active)
        replacement_line_ids: dict[UUID, UUID] = {}
        target_families = {item.offer.family for item in resolved}

        for item in resolved:
            current = current_active.get(item.offer.family)
            if current is not None and self._line_matches_resolved(current, item):
                target_lines.append(current)
                changed_families.discard(item.offer.family)
                continue
            changed_families.add(item.offer.family)
            target = self._line_read_from_resolved(item, now=now)
            target_lines.append(target)
            resolved_changed.append(item)
            if current is not None and current.id is not None and target.id is not None:
                replacement_line_ids[current.id] = target.id

        changed_families.update(set(current_active) - target_families)
        adjustments = list(
            (
                await self.db.scalars(
                    select(CommercialAdjustment).where(
                        CommercialAdjustment.contract_id == contract.id,
                        CommercialAdjustment.status.in_(
                            [CommercialRecordStatus.scheduled, CommercialRecordStatus.active]
                        ),
                    )
                )
            ).all()
        )
        current_family_by_line = {
            item.id: item.offer_family for item in current_active.values() if item.id is not None
        }
        adjustment_ids_to_revoke: set[UUID] = set()
        adjustment_ids_to_rebind: set[UUID] = set()
        restore_adjustment_ids_to_rebind: set[UUID] = set()
        warnings: list[str] = []
        blockers: list[str] = []
        for adjustment in adjustments:
            affected_family = current_family_by_line.get(adjustment.contract_line_id)
            affected = (
                bool(changed_families)
                if affected_family is None
                else (affected_family in changed_families)
            )
            if not affected:
                continue
            legacy_courtesy = (
                adjustment.type is CommercialAdjustmentType.courtesy_period
                and (
                    adjustment.value != 1
                    or adjustment.remaining_cycles is not None
                )
            )
            if preserve_exceptions:
                if (
                    adjustment.contract_line_id is not None
                    and adjustment.contract_line_id not in replacement_line_ids
                ):
                    blockers.append(
                        "Um desconto ligado a uma linha ausente na revisão alvo não pode "
                        "ser preservado automaticamente."
                    )
                else:
                    if adjustment.contract_line_id in replacement_line_ids:
                        restore_adjustment_ids_to_rebind.add(adjustment.id)
                    if legacy_courtesy:
                        warnings.append(
                            "Cortesia histórica baseada em ciclos será preservada pela "
                            "restauração explícita e continuará exigindo intervenção."
                        )
                    elif adjustment.portability is CommercialPortability.manual_review:
                        warnings.append(
                            "Ajuste com revisão manual será preservado sem nova aprovação "
                            "porque esta é uma restauração explícita."
                        )
                    elif adjustment.portability is CommercialPortability.recalculate:
                        warnings.append(
                            "Ajuste histórico com recálculo será preservado sem recalcular "
                            "porque a restauração mantém a exceção atual."
                        )
                    elif (
                        adjustment.portability
                        is CommercialPortability.expire_on_offer_change
                    ):
                        warnings.append(
                            "Ajuste que expiraria numa troca normal será preservado "
                            "pela restauração explícita."
                        )
                    else:
                        warnings.append(
                            "Descontos e condições negociadas serão preservados "
                            "na restauração."
                        )
            elif legacy_courtesy:
                blockers.append(
                    "Existe cortesia histórica baseada em ciclos; revogue-a ou converta-a "
                    "em intervalo explícito antes de trocar a oferta."
                )
            elif adjustment.portability.value == "manual_review":
                blockers.append(
                    "Existe ajuste comercial marcado para revisão manual; "
                    "ajuste ou revogue-o antes."
                )
            elif adjustment.portability.value == "recalculate":
                blockers.append(
                    "Existe ajuste comercial com recálculo sem regra determinística; "
                    "substitua sua portabilidade antes de trocar a oferta."
                )
            elif adjustment.portability.value == "expire_on_offer_change":
                adjustment_ids_to_revoke.add(adjustment.id)
                warnings.append("Um ajuste comercial será revogado pela troca de oferta.")
            elif (
                adjustment.contract_line_id is not None
                and adjustment.contract_line_id not in replacement_line_ids
            ):
                blockers.append(
                    "Um ajuste ligado à linha removida precisa ser revogado ou redirecionado."
                )
            elif adjustment.contract_line_id in replacement_line_ids:
                adjustment_ids_to_rebind.add(adjustment.id)

        bonuses = list(
            (
                await self.db.scalars(
                    select(EntitlementBonus).where(
                        EntitlementBonus.workspace_id == contract.workspace_id,
                        EntitlementBonus.status.in_(
                            [CommercialRecordStatus.scheduled, CommercialRecordStatus.active]
                        ),
                    )
                )
            ).all()
        )
        bonus_ids_to_revoke: set[UUID] = set()
        preview_bonuses: list[EntitlementBonus] = []
        for bonus in bonuses:
            if preserve_exceptions:
                if _active_at(bonus.starts_at, bonus.ends_at, now):
                    preview_bonuses.append(bonus)
                if changed_families:
                    if bonus.portability is CommercialPortability.manual_review:
                        warnings.append(
                            "Bônus com revisão manual será preservado sem nova aprovação "
                            "porque esta é uma restauração explícita."
                        )
                    elif bonus.portability is CommercialPortability.recalculate:
                        warnings.append(
                            "Bônus histórico com recálculo será preservado sem recalcular "
                            "porque a restauração mantém a exceção atual."
                        )
                    elif bonus.portability is CommercialPortability.expire_on_offer_change:
                        warnings.append(
                            "Bônus que expiraria numa troca normal será preservado "
                            "pela restauração explícita."
                        )
                    else:
                        warnings.append(
                            "Bônus individuais serão preservados na restauração."
                        )
            elif changed_families and bonus.portability.value == "manual_review":
                blockers.append(
                    "Existe bônus marcado para revisão manual; ajuste ou revogue-o antes."
                )
                if _active_at(bonus.starts_at, bonus.ends_at, now):
                    preview_bonuses.append(bonus)
            elif changed_families and bonus.portability.value == "recalculate":
                blockers.append(
                    "Existe bônus com recálculo sem regra determinística; "
                    "substitua sua portabilidade antes de trocar a oferta."
                )
                if _active_at(bonus.starts_at, bonus.ends_at, now):
                    preview_bonuses.append(bonus)
            elif changed_families and bonus.portability.value == "expire_on_offer_change":
                bonus_ids_to_revoke.add(bonus.id)
                warnings.append("Um bônus será revogado pela troca de oferta.")
            else:
                if _active_at(bonus.starts_at, bonus.ends_at, now):
                    preview_bonuses.append(bonus)

        return _ContractRevisionPlan(
            contract=contract,
            current_lines=current_lines,
            target_lines=target_lines,
            resolved_changed=resolved_changed,
            changed_families=changed_families,
            replacement_line_ids=replacement_line_ids,
            adjustments=adjustments,
            adjustment_ids_to_revoke=adjustment_ids_to_revoke,
            adjustment_ids_to_rebind=adjustment_ids_to_rebind,
            restore_adjustment_ids_to_rebind=restore_adjustment_ids_to_rebind,
            bonus_ids_to_revoke=bonus_ids_to_revoke,
            preview_bonuses=preview_bonuses,
            warnings=list(dict.fromkeys(warnings)),
            blockers=list(dict.fromkeys(blockers)),
        )

    @staticmethod
    def _line_matches_resolved(
        current: WorkspaceContractLineRead,
        target: _ResolvedLine,
    ) -> bool:
        requested_start = target.input.effective_from
        return (
            current.sku == target.offer.sku
            and current.policy_version_id == target.version.id
            and current.quantity == target.input.quantity
            and current.list_price_cents == target.list_price_cents
            and current.contracted_price_cents == target.contracted_price_cents
            and current.billing_interval is target.input.billing_interval
            and current.entitlement_overrides == target.input.entitlement_overrides
            and (requested_start is None or _as_utc(current.effective_from) == requested_start)
            and current.effective_until == target.input.effective_until
        )

    async def create_contract(
        self,
        *,
        payload: WorkspaceContractCreateRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
        allow_legacy_import: bool = False,
        effective_at: datetime | None = None,
    ) -> tuple[WorkspaceContractRead, bool]:
        request_hash = self._hash(
            {
                **payload.model_dump(mode="json"),
                "effective_at": (
                    _as_utc(effective_at).isoformat() if effective_at is not None else None
                ),
            }
        )
        scope = str(payload.workspace_id)
        operation = "import_legacy_contract" if allow_legacy_import else "create_contract"
        await self._lock_command(operation=operation, scope_key=scope)
        receipt = await self._existing_receipt(
            operation=operation,
            scope_key=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt is not None:
            replay = WorkspaceContractRead.model_validate(receipt.response_payload)
            return replay.model_copy(update={"idempotent_replay": True}), True

        workspace = await self.db.scalar(
            select(Workspace)
            .where(Workspace.id == payload.workspace_id, Workspace.deleted_at.is_(None))
            .with_for_update()
        )
        if workspace is None:
            raise CommercialCatalogNotFoundError("Workspace não encontrado.")
        existing = await self.db.scalar(
            select(WorkspaceCommercialContract.id).where(
                WorkspaceCommercialContract.workspace_id == payload.workspace_id
            )
        )
        if existing is not None:
            raise CommercialCatalogConflictError("O workspace já possui contrato comercial.")
        resolved = await self._resolve_line_inputs(payload.lines, legacy_import=allow_legacy_import)
        now = _as_utc(effective_at) if effective_at is not None else datetime.now(UTC)
        contract = WorkspaceCommercialContract(
            workspace_id=payload.workspace_id,
            status=payload.status,
            currency="BRL",
            version=1,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        self.db.add(contract)
        await self.db.flush()
        for item in resolved:
            line = WorkspaceContractLine(
                id=item.line_id,
                contract_id=contract.id,
                offer_id=item.offer.id,
                policy_version_id=item.version.id,
                offer_family=item.offer.family,
                quantity=item.input.quantity,
                list_price_cents=item.list_price_cents,
                contracted_price_cents=item.contracted_price_cents,
                billing_interval=item.input.billing_interval,
                entitlement_overrides={
                    key.value: setting.model_dump(mode="json")
                    for key, setting in item.input.entitlement_overrides.items()
                },
                effective_from=item.input.effective_from or now,
                effective_until=item.input.effective_until,
                status=CommercialContractLineStatus.active,
                version=1,
                created_by_user_id=actor_user_id,
                updated_by_user_id=actor_user_id,
            )
            self.db.add(line)
        await self.db.flush()
        response = await self.get_contract(contract.id)
        self._event(
            event_type="contract_created",
            actor_user_id=actor_user_id,
            workspace_id=payload.workspace_id,
            resource_type="workspace_commercial_contract",
            resource_id=contract.id,
            before=None,
            after=response.model_dump(mode="json"),
            reason=payload.reason,
            correlation_id=idempotency_key,
        )
        await self.db.flush()
        await self._record_receipt(
            operation=operation,
            scope_key=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=response.model_dump(mode="json"),
            actor_user_id=actor_user_id,
        )
        return response, False

    async def _resolve_line_inputs(
        self,
        inputs: list[WorkspaceContractLineWrite],
        *,
        legacy_import: bool,
        preserved_annual_lines: Mapping[
            CommercialOfferFamily, WorkspaceContractLineRead
        ]
        | None = None,
    ) -> list[_ResolvedLine]:
        families: set[CommercialOfferFamily] = set()
        resolved: list[_ResolvedLine] = []
        for item in inputs:
            offer = await self.db.scalar(
                select(CommercialOffer).where(CommercialOffer.sku == item.sku)
            )
            if offer is None:
                raise CommercialCatalogNotFoundError(f"Oferta {item.sku} não encontrada.")
            if offer.family not in {CommercialOfferFamily.crm, CommercialOfferFamily.legal}:
                raise CommercialCatalogValidationError("Oferta interna não pode ser contratada.")
            if offer.family in families:
                raise CommercialCatalogValidationError(
                    f"Só pode existir uma linha {offer.family.value} ativa por contrato."
                )
            families.add(offer.family)
            if item.billing_interval is CommercialBillingInterval.annual and not legacy_import:
                current_annual = (preserved_annual_lines or {}).get(offer.family)
                preserves_existing_annual = bool(
                    current_annual is not None
                    and current_annual.billing_interval is CommercialBillingInterval.annual
                    and current_annual.sku == item.sku
                    and item.policy_version_id == current_annual.policy_version_id
                    and item.contracted_price_cents == current_annual.contracted_price_cents
                    and item.quantity == current_annual.quantity
                    and item.entitlement_overrides == current_annual.entitlement_overrides
                    and (
                        item.effective_from is None
                        or item.effective_from == _as_utc(current_annual.effective_from)
                    )
                    and item.effective_until == current_annual.effective_until
                )
                if not preserves_existing_annual:
                    raise CommercialCatalogValidationError(
                        "Novas linhas anuais e alterações em linha anual legada estão "
                        "indisponíveis; preserve política, preço, intervalo e limites vigentes."
                    )
            version_query = select(CommercialOfferVersion).where(
                CommercialOfferVersion.offer_id == offer.id
            )
            if item.policy_version_id is not None:
                version_query = version_query.where(
                    CommercialOfferVersion.id == item.policy_version_id
                )
            else:
                version_query = version_query.order_by(CommercialOfferVersion.version.desc()).limit(
                    1
                )
            version = await self.db.scalar(version_query)
            if version is None:
                raise CommercialCatalogNotFoundError(
                    f"Versão de política da oferta {item.sku} não encontrada."
                )
            snapshot = self._version_snapshot(version)
            if not snapshot.is_active:
                raise CommercialCatalogValidationError(f"Oferta {item.sku} está inativa.")
            if snapshot.is_quote_only and (
                item.contracted_price_cents is None or item.contracted_price_cents <= 0
            ):
                raise CommercialCatalogValidationError(
                    f"Oferta {item.sku} sob consulta exige preço contratado estritamente positivo."
                )
            self._validate_overrides(snapshot, item.entitlement_overrides)
            if offer.family is CommercialOfferFamily.legal:
                missing = sorted(
                    key.value for key in _LEGAL_EXPLICIT_TERMS - set(item.entitlement_overrides)
                )
                if missing:
                    raise CommercialCatalogValidationError(
                        "Jurídico 360 exige limites explícitos na proposta: " + ", ".join(missing)
                    )
                effective = dict(snapshot.entitlements)
                effective.update(item.entitlement_overrides)
                if not effective[EntitlementKey.legal_suite].enabled:
                    raise CommercialCatalogValidationError("Jurídico 360 exige legal_suite=true.")
                for key in (EntitlementKey.users, EntitlementKey.legal_areas):
                    setting = effective[key]
                    if (
                        not setting.enabled
                        or not setting.limit_quantity
                        or setting.limit_quantity < 1
                    ):
                        raise CommercialCatalogValidationError(
                            f"Jurídico 360 exige {key.value} >= 1."
                        )
                storage = effective[EntitlementKey.storage_bytes]
                if not storage.enabled or not storage.limit_quantity:
                    raise CommercialCatalogValidationError(
                        "Jurídico 360 exige armazenamento positivo e explícito."
                    )
                monitored = effective[EntitlementKey.monitored_processes]
                if not monitored.enabled or monitored.limit_quantity is None:
                    raise CommercialCatalogValidationError(
                        "Jurídico 360 exige processos monitorados explícitos, inclusive zero."
                    )
                for key in (EntitlementKey.legal_documents, EntitlementKey.protocol_folders):
                    setting = effective[key]
                    if (
                        not setting.enabled
                        or setting.limit_quantity is None
                        or setting.limit_quantity < 1
                    ):
                        raise CommercialCatalogValidationError(
                            f"Jurídico 360 exige {key.value} >= 1 na proposta."
                        )

            if item.billing_interval is CommercialBillingInterval.annual:
                if item.contracted_price_cents is None:
                    raise CommercialCatalogValidationError(
                        "Importação anual legada exige preço contratado explícito."
                    )
                # The current catalog intentionally has no annual list price;
                # retain the observed legacy amount instead of inventing zero.
                list_price = item.contracted_price_cents
            elif snapshot.is_quote_only:
                # Quote-only offers were validated above before any pricing
                # branch, including preserved annual legacy lines.
                list_price = item.contracted_price_cents
            else:
                if snapshot.price_monthly_cents is None:
                    raise CommercialCatalogValidationError(
                        f"Oferta {item.sku} não possui preço mensal publicado."
                    )
                list_price = snapshot.price_monthly_cents
            contracted = (
                item.contracted_price_cents
                if item.contracted_price_cents is not None
                else list_price
            )
            resolved.append(
                _ResolvedLine(
                    line_id=uuid4(),
                    offer=offer,
                    version=version,
                    snapshot=snapshot,
                    input=item,
                    list_price_cents=list_price,
                    contracted_price_cents=contracted,
                )
            )
        return resolved

    @staticmethod
    def _validate_overrides(
        snapshot: CommercialOfferSnapshotRead,
        overrides: Mapping[EntitlementKey, CommercialEntitlementSetting],
    ) -> None:
        for key, override in overrides.items():
            if (
                key in QUANTITATIVE_ENTITLEMENTS
                and override.enabled
                and override.limit_quantity is None
            ):
                raise CommercialCatalogValidationError(
                    f"Override de {key.value} exige quota finita e não negativa."
                )
            if key in _UNENFORCED_LEGAL_CAPABILITIES and override.enabled:
                raise CommercialCatalogValidationError(
                    f"{key.value} não possui enforcement operacional e não pode ser contratado."
                )
            base = snapshot.entitlements.get(key) or _setting(key, enabled=False)
            if (
                override.unit != base.unit
                or override.period != base.period
                or override.composition != base.composition
            ):
                raise CommercialCatalogValidationError(
                    f"Override de {key.value} não pode alterar unidade, período ou composição."
                )

    @staticmethod
    def _line_read_from_resolved(
        item: _ResolvedLine, *, now: datetime
    ) -> WorkspaceContractLineRead:
        return WorkspaceContractLineRead(
            id=item.line_id,
            offer_id=item.offer.id,
            policy_version_id=item.version.id,
            sku=item.offer.sku,
            offer_family=item.offer.family,
            quantity=item.input.quantity,
            list_price_cents=item.list_price_cents,
            contracted_price_cents=item.contracted_price_cents,
            billing_interval=item.input.billing_interval,
            entitlement_overrides=item.input.entitlement_overrides,
            effective_from=item.input.effective_from or now,
            effective_until=item.input.effective_until,
            status=CommercialContractLineStatus.active,
            version=1,
        )

    async def get_contract(self, contract_id: UUID) -> WorkspaceContractRead:
        contract = await self.db.get(WorkspaceCommercialContract, contract_id)
        if contract is None:
            raise CommercialContractNotFoundError("Contrato comercial não encontrado.")
        workspace_label = await self.db.scalar(
            select(Workspace.name).where(Workspace.id == contract.workspace_id)
        )
        if workspace_label is None:
            raise CommercialCatalogValidationError(
                "Conta vinculada ao contrato não foi encontrada."
            )
        lines = await self._load_line_reads(contract.id)
        price = await self._price_preview(contract, lines)
        return WorkspaceContractRead(
            id=contract.id,
            workspace_id=contract.workspace_id,
            workspace_label=workspace_label,
            status=contract.status,
            currency="BRL",
            version=contract.version,
            lines=lines,
            price=price,
            created_at=contract.created_at,
            updated_at=contract.updated_at,
        )

    async def get_contract_workspace_id(self, contract_id: UUID) -> UUID:
        """Resolve the single workspace needed before a platform preview binds RLS."""

        workspace_id = await self.db.scalar(
            select(WorkspaceCommercialContract.workspace_id).where(
                WorkspaceCommercialContract.id == contract_id
            )
        )
        if workspace_id is None:
            raise CommercialContractNotFoundError("Contrato comercial não encontrado.")
        return workspace_id

    async def get_workspace_contract(self, workspace_id: UUID) -> WorkspaceContractRead:
        contract_id = await self.db.scalar(
            select(WorkspaceCommercialContract.id).where(
                WorkspaceCommercialContract.workspace_id == workspace_id
            )
        )
        if contract_id is None:
            raise CommercialContractNotFoundError("Workspace sem contrato comercial canônico.")
        return await self.get_contract(contract_id)

    async def list_contracts(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[WorkspaceContractRead], int]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        total = int(await self.db.scalar(select(func.count(WorkspaceCommercialContract.id))) or 0)
        ids = list(
            (
                await self.db.scalars(
                    select(WorkspaceCommercialContract.id)
                    .order_by(
                        WorkspaceCommercialContract.updated_at.desc(),
                        WorkspaceCommercialContract.id.desc(),
                    )
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        )
        return [await self.get_contract(item) for item in ids], total

    @staticmethod
    def _contract_restore_state(contract: WorkspaceContractRead) -> dict[str, Any]:
        return {
            "status": contract.status.value,
            "lines": sorted(
                [
                    {
                        "sku": line.sku,
                        "policy_version_id": str(line.policy_version_id),
                        "quantity": line.quantity,
                        "contracted_price_cents": line.contracted_price_cents,
                        "billing_interval": line.billing_interval.value,
                        "entitlement_overrides": {
                            key.value: value.model_dump(mode="json")
                            for key, value in line.entitlement_overrides.items()
                        },
                    }
                    for line in contract.lines
                    if line.status is CommercialContractLineStatus.active
                ],
                key=lambda item: item["sku"],
            ),
        }

    @staticmethod
    def _contract_lines_for_restore(
        contract: WorkspaceContractRead,
    ) -> list[WorkspaceContractLineWrite]:
        return [
            WorkspaceContractLineWrite(
                sku=line.sku,
                policy_version_id=line.policy_version_id,
                quantity=line.quantity,
                contracted_price_cents=line.contracted_price_cents,
                billing_interval=line.billing_interval,
                entitlement_overrides=line.entitlement_overrides,
            )
            for line in contract.lines
            if line.status is CommercialContractLineStatus.active
        ]

    async def contract_history(self, contract_id: UUID) -> WorkspaceContractHistoryRead:
        contract = await self.db.get(WorkspaceCommercialContract, contract_id)
        if contract is None:
            raise CommercialContractNotFoundError("Contrato comercial não encontrado.")
        events = list(
            (
                await self.db.scalars(
                    select(CommercialCatalogEvent)
                    .where(
                        CommercialCatalogEvent.resource_type == "workspace_commercial_contract",
                        CommercialCatalogEvent.resource_id == contract_id,
                        CommercialCatalogEvent.after.is_not(None),
                    )
                    .order_by(
                        CommercialCatalogEvent.occurred_at.desc(),
                        CommercialCatalogEvent.id.desc(),
                    )
                )
            ).all()
        )
        items: list[WorkspaceContractHistoryItem] = []
        for event in events:
            if event.after is None:
                continue
            try:
                snapshot = WorkspaceContractRead.model_validate(event.after)
            except ValueError:
                # Legacy partial evidence remains visible in the canonical audit
                # center, but cannot become a fabricated restoration target.
                continue
            before_state: dict[str, Any] = {}
            if event.before is not None:
                try:
                    before_state = self._contract_restore_state(
                        WorkspaceContractRead.model_validate(event.before)
                    )
                except ValueError:
                    before_state = {}
            items.append(
                WorkspaceContractHistoryItem(
                    event_id=event.id,
                    event_type=event.event_type,
                    version=snapshot.version,
                    reason=event.reason,
                    actor_user_id=event.actor_user_id,
                    occurred_at=event.occurred_at,
                    snapshot=snapshot,
                    diff=_semantic_diff(
                        before_state,
                        self._contract_restore_state(snapshot),
                    ),
                )
            )
        return WorkspaceContractHistoryRead(
            contract_id=contract_id,
            current_version=contract.version,
            items=items,
        )

    async def _contract_snapshot_at_version(
        self,
        contract_id: UUID,
        version: int,
    ) -> WorkspaceContractRead:
        events = list(
            (
                await self.db.scalars(
                    select(CommercialCatalogEvent)
                    .where(
                        CommercialCatalogEvent.resource_type == "workspace_commercial_contract",
                        CommercialCatalogEvent.resource_id == contract_id,
                        CommercialCatalogEvent.after.is_not(None),
                    )
                    .order_by(CommercialCatalogEvent.occurred_at.desc())
                )
            ).all()
        )
        for event in events:
            try:
                snapshot = WorkspaceContractRead.model_validate(event.after)
            except ValueError:
                continue
            if snapshot.version == version:
                return snapshot
        raise CommercialCatalogNotFoundError("Versão contratual restaurável não encontrada.")

    async def preview_contract_restore(
        self,
        *,
        contract_id: UUID,
        target_version: int,
        expected_current_version: int,
    ) -> WorkspaceContractRestorePreviewRead:
        current = await self.get_contract(contract_id)
        if current.version != expected_current_version:
            raise CommercialCatalogConflictError(
                "O contrato mudou. Gere uma nova prévia de restauração."
            )
        target = await self._contract_snapshot_at_version(contract_id, target_version)
        revision = await self.preview_contract_revision(
            contract_id,
            WorkspaceContractRevisionPreviewRequest(
                expected_version=expected_current_version,
                lines=self._contract_lines_for_restore(target),
            ),
            preserve_exceptions=True,
        )
        status_changed = current.status != target.status
        if status_changed and not revision.eligible and not revision.blockers:
            revision = revision.model_copy(update={"eligible": True})
        return WorkspaceContractRestorePreviewRead(
            contract_id=contract_id,
            current_version=current.version,
            target_version=target.version,
            target_snapshot=target,
            revision=revision,
            diff=_semantic_diff(
                self._contract_restore_state(current),
                self._contract_restore_state(target),
            ),
            preserves_adjustments=revision.eligible and not any(
                "desconto" in blocker.lower() or "ajuste" in blocker.lower()
                for blocker in revision.blockers
            ),
            preserves_bonuses=revision.eligible and not any(
                "bônus" in blocker.lower() for blocker in revision.blockers
            ),
        )

    async def restore_contract(
        self,
        *,
        contract_id: UUID,
        payload: WorkspaceContractRestoreRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
        effective_at: datetime | None = None,
        restore_portability_context: (
            Callable[[], AbstractAsyncContextManager[None]] | None
        ) = None,
    ) -> tuple[WorkspaceContractRead, bool]:
        target = await self._contract_snapshot_at_version(contract_id, payload.target_version)
        return await self.revise_contract(
            contract_id=contract_id,
            expected_version=payload.expected_current_version,
            payload=WorkspaceContractRevisionRequest(
                lines=self._contract_lines_for_restore(target),
                reason=payload.reason,
            ),
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            operation="restore_contract",
            event_type="contract_restored",
            preserve_exceptions=True,
            target_status=target.status,
            effective_at=effective_at,
            restore_portability_context=restore_portability_context,
        )

    async def _load_line_reads(self, contract_id: UUID) -> list[WorkspaceContractLineRead]:
        rows = (
            await self.db.execute(
                select(WorkspaceContractLine, CommercialOffer)
                .join(CommercialOffer, CommercialOffer.id == WorkspaceContractLine.offer_id)
                .where(WorkspaceContractLine.contract_id == contract_id)
                .order_by(WorkspaceContractLine.offer_family.asc())
            )
        ).all()
        return [
            WorkspaceContractLineRead(
                id=line.id,
                offer_id=line.offer_id,
                policy_version_id=line.policy_version_id,
                sku=offer.sku,
                offer_family=line.offer_family,
                quantity=line.quantity,
                list_price_cents=line.list_price_cents,
                contracted_price_cents=line.contracted_price_cents,
                billing_interval=line.billing_interval,
                entitlement_overrides=line.entitlement_overrides or {},
                effective_from=line.effective_from,
                effective_until=line.effective_until,
                status=line.status,
                version=line.version,
            )
            for line, offer in rows
        ]

    async def add_adjustment(
        self,
        *,
        contract_id: UUID,
        payload: CommercialAdjustmentCreateRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[CommercialAdjustmentRead, bool]:
        request_hash = self._hash(payload.model_dump(mode="json"))
        workspace_id = await self.db.scalar(
            select(WorkspaceCommercialContract.workspace_id).where(
                WorkspaceCommercialContract.id == contract_id
            )
        )
        if workspace_id is None:
            raise CommercialContractNotFoundError("Contrato comercial não encontrado.")
        await self._lock_command(
            operation="add_adjustment",
            scope_key=str(contract_id),
            aggregate_key=str(workspace_id),
        )
        receipt = await self._existing_receipt(
            operation="add_adjustment",
            scope_key=str(contract_id),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt is not None:
            return CommercialAdjustmentRead.model_validate(receipt.response_payload), True
        contract = await self.db.scalar(
            select(WorkspaceCommercialContract)
            .where(WorkspaceCommercialContract.id == contract_id)
            .with_for_update()
        )
        if contract is None:
            raise CommercialContractNotFoundError("Contrato comercial não encontrado.")
        if payload.contract_line_id is not None:
            now = datetime.now(UTC)
            line = await self.db.scalar(
                select(WorkspaceContractLine.id).where(
                    WorkspaceContractLine.id == payload.contract_line_id,
                    WorkspaceContractLine.contract_id == contract_id,
                    WorkspaceContractLine.status == CommercialContractLineStatus.active,
                    WorkspaceContractLine.effective_from <= now,
                    or_(
                        WorkspaceContractLine.effective_until.is_(None),
                        WorkspaceContractLine.effective_until > now,
                    ),
                )
            )
            if line is None:
                raise CommercialCatalogValidationError(
                    "Ajuste exige linha ativa e vigente do contrato."
                )
        overlap_conditions = [
            CommercialAdjustment.contract_id == contract_id,
            CommercialAdjustment.stack_group == payload.stack_group,
            CommercialAdjustment.status.in_(
                [CommercialRecordStatus.scheduled, CommercialRecordStatus.active]
            ),
            or_(
                CommercialAdjustment.ends_at.is_(None),
                CommercialAdjustment.ends_at > payload.starts_at,
            ),
        ]
        if payload.ends_at is not None:
            overlap_conditions.append(CommercialAdjustment.starts_at < payload.ends_at)
        overlapping = list(
            (await self.db.scalars(select(CommercialAdjustment).where(*overlap_conditions))).all()
        )
        if overlapping and (
            not payload.stackable or any(not item.stackable for item in overlapping)
        ):
            raise CommercialCatalogConflictError(
                "Já existe ajuste não acumulável no mesmo stackGroup e período."
            )
        interval_conditions = [
            CommercialAdjustment.contract_id == contract_id,
            CommercialAdjustment.status.in_(
                [CommercialRecordStatus.scheduled, CommercialRecordStatus.active]
            ),
            or_(
                CommercialAdjustment.ends_at.is_(None),
                CommercialAdjustment.ends_at > payload.starts_at,
            ),
        ]
        if payload.ends_at is not None:
            interval_conditions.append(CommercialAdjustment.starts_at < payload.ends_at)
        interval_overlaps = list(
            (await self.db.scalars(select(CommercialAdjustment).where(*interval_conditions))).all()
        )
        recurring_types = {
            CommercialAdjustmentType.negotiated_price,
            CommercialAdjustmentType.percentage_discount,
            CommercialAdjustmentType.fixed_discount,
            CommercialAdjustmentType.courtesy_period,
        }
        same_scope = [
            item
            for item in interval_overlaps
            if item.contract_line_id == payload.contract_line_id and item.type in recurring_types
        ]
        negotiated_conflict = (
            payload.type in recurring_types
            and any(item.type is CommercialAdjustmentType.negotiated_price for item in same_scope)
        ) or (payload.type is CommercialAdjustmentType.negotiated_price and bool(same_scope))
        if negotiated_conflict and (
            not payload.stackable or any(not item.stackable for item in same_scope)
        ):
            raise CommercialCatalogConflictError(
                "Preço negociado é exclusivo no mesmo escopo por padrão."
            )
        adjustment = CommercialAdjustment(
            contract_id=contract_id,
            contract_line_id=payload.contract_line_id,
            type=payload.type,
            value=payload.value,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            remaining_cycles=payload.remaining_cycles,
            stack_group=payload.stack_group,
            stackable=payload.stackable,
            portability=payload.portability,
            reason=payload.reason,
            source=payload.source,
            status=_record_status_for_window(payload.starts_at, payload.ends_at),
            version=1,
            created_by_user_id=actor_user_id,
        )
        self.db.add(adjustment)
        await self.db.flush()
        response = CommercialAdjustmentRead.model_validate(adjustment)
        self._event(
            event_type="commercial_adjustment_added",
            actor_user_id=actor_user_id,
            workspace_id=contract.workspace_id,
            resource_type="commercial_adjustment",
            resource_id=adjustment.id,
            before=None,
            after=response.model_dump(mode="json"),
            reason=payload.reason,
            correlation_id=idempotency_key,
        )
        await self.db.flush()
        await self._record_receipt(
            operation="add_adjustment",
            scope_key=str(contract_id),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=response.model_dump(mode="json"),
            actor_user_id=actor_user_id,
        )
        return response, False

    async def list_adjustments(self, contract_id: UUID) -> list[CommercialAdjustmentRead]:
        if await self.db.get(WorkspaceCommercialContract, contract_id) is None:
            raise CommercialContractNotFoundError("Contrato comercial não encontrado.")
        rows = list(
            (
                await self.db.scalars(
                    select(CommercialAdjustment)
                    .where(CommercialAdjustment.contract_id == contract_id)
                    .order_by(CommercialAdjustment.created_at.desc())
                )
            ).all()
        )
        return [CommercialAdjustmentRead.model_validate(item) for item in rows]

    async def update_adjustment(
        self,
        *,
        adjustment_id: UUID,
        expected_version: int,
        payload: CommercialAdjustmentUpdateRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[CommercialAdjustmentRead, bool]:
        request_hash = self._hash(
            {**payload.model_dump(mode="json"), "expected_version": expected_version}
        )
        scope = str(adjustment_id)
        aggregate = (
            await self.db.execute(
                select(
                    CommercialAdjustment.contract_id,
                    WorkspaceCommercialContract.workspace_id,
                )
                .join(
                    WorkspaceCommercialContract,
                    WorkspaceCommercialContract.id == CommercialAdjustment.contract_id,
                )
                .where(CommercialAdjustment.id == adjustment_id)
            )
        ).one_or_none()
        if aggregate is None:
            raise CommercialCatalogNotFoundError("Ajuste comercial não encontrado.")
        await self._lock_command(
            operation="update_adjustment",
            scope_key=scope,
            aggregate_key=str(aggregate.workspace_id),
        )
        receipt = await self._existing_receipt(
            operation="update_adjustment",
            scope_key=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt is not None:
            return CommercialAdjustmentRead.model_validate(receipt.response_payload), True
        adjustment = await self.db.scalar(
            select(CommercialAdjustment)
            .where(CommercialAdjustment.id == adjustment_id)
            .with_for_update()
        )
        if adjustment is None:
            raise CommercialCatalogNotFoundError("Ajuste comercial não encontrado.")
        if adjustment.version != expected_version:
            raise CommercialCatalogConflictError(
                "O ajuste mudou. Recarregue os dados antes de confirmar."
            )
        if adjustment.status in {CommercialRecordStatus.revoked, CommercialRecordStatus.expired}:
            raise CommercialCatalogConflictError("Ajuste encerrado não pode ser editado.")
        await self._validate_adjustment_replacement(
            contract_id=adjustment.contract_id,
            payload=payload,
            exclude_adjustment_id=adjustment.id,
        )
        before = CommercialAdjustmentRead.model_validate(adjustment)
        adjustment.contract_line_id = payload.contract_line_id
        adjustment.type = payload.type
        adjustment.value = payload.value
        adjustment.starts_at = payload.starts_at
        adjustment.ends_at = payload.ends_at
        adjustment.remaining_cycles = payload.remaining_cycles
        adjustment.stack_group = payload.stack_group
        adjustment.stackable = payload.stackable
        adjustment.portability = payload.portability
        adjustment.reason = payload.reason
        adjustment.source = payload.source
        adjustment.status = _record_status_for_window(payload.starts_at, payload.ends_at)
        adjustment.version += 1
        await self.db.flush()
        response = CommercialAdjustmentRead.model_validate(adjustment)
        contract = await self.db.get(WorkspaceCommercialContract, adjustment.contract_id)
        self._event(
            event_type="commercial_adjustment_updated",
            actor_user_id=actor_user_id,
            workspace_id=contract.workspace_id if contract is not None else None,
            resource_type="commercial_adjustment",
            resource_id=adjustment.id,
            before=before.model_dump(mode="json"),
            after=response.model_dump(mode="json"),
            reason=payload.reason,
            correlation_id=idempotency_key,
        )
        await self.db.flush()
        await self._record_receipt(
            operation="update_adjustment",
            scope_key=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=response.model_dump(mode="json"),
            actor_user_id=actor_user_id,
        )
        return response, False

    async def revoke_adjustment(
        self,
        *,
        adjustment_id: UUID,
        expected_version: int,
        payload: CommercialRecordRevokeRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[CommercialAdjustmentRead, bool]:
        request_hash = self._hash(
            {**payload.model_dump(mode="json"), "expected_version": expected_version}
        )
        scope = str(adjustment_id)
        aggregate = (
            await self.db.execute(
                select(
                    CommercialAdjustment.contract_id,
                    WorkspaceCommercialContract.workspace_id,
                )
                .join(
                    WorkspaceCommercialContract,
                    WorkspaceCommercialContract.id == CommercialAdjustment.contract_id,
                )
                .where(CommercialAdjustment.id == adjustment_id)
            )
        ).one_or_none()
        if aggregate is None:
            raise CommercialCatalogNotFoundError("Ajuste comercial não encontrado.")
        await self._lock_command(
            operation="revoke_adjustment",
            scope_key=scope,
            aggregate_key=str(aggregate.workspace_id),
        )
        receipt = await self._existing_receipt(
            operation="revoke_adjustment",
            scope_key=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt is not None:
            return CommercialAdjustmentRead.model_validate(receipt.response_payload), True
        adjustment = await self.db.scalar(
            select(CommercialAdjustment)
            .where(CommercialAdjustment.id == adjustment_id)
            .with_for_update()
        )
        if adjustment is None:
            raise CommercialCatalogNotFoundError("Ajuste comercial não encontrado.")
        if adjustment.version != expected_version:
            raise CommercialCatalogConflictError(
                "O ajuste mudou. Recarregue os dados antes de confirmar."
            )
        if adjustment.status is CommercialRecordStatus.revoked:
            raise CommercialCatalogConflictError("Ajuste já revogado.")
        before = CommercialAdjustmentRead.model_validate(adjustment)
        adjustment.status = CommercialRecordStatus.revoked
        adjustment.reason = payload.reason
        adjustment.version += 1
        await self.db.flush()
        response = CommercialAdjustmentRead.model_validate(adjustment)
        contract = await self.db.get(WorkspaceCommercialContract, adjustment.contract_id)
        self._event(
            event_type="commercial_adjustment_revoked",
            actor_user_id=actor_user_id,
            workspace_id=contract.workspace_id if contract is not None else None,
            resource_type="commercial_adjustment",
            resource_id=adjustment.id,
            before=before.model_dump(mode="json"),
            after=response.model_dump(mode="json"),
            reason=payload.reason,
            correlation_id=idempotency_key,
        )
        await self.db.flush()
        await self._record_receipt(
            operation="revoke_adjustment",
            scope_key=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=response.model_dump(mode="json"),
            actor_user_id=actor_user_id,
        )
        return response, False

    async def _validate_adjustment_replacement(
        self,
        *,
        contract_id: UUID,
        payload: CommercialAdjustmentUpdateRequest,
        exclude_adjustment_id: UUID,
    ) -> None:
        if payload.contract_line_id is not None:
            now = datetime.now(UTC)
            line = await self.db.scalar(
                select(WorkspaceContractLine.id).where(
                    WorkspaceContractLine.id == payload.contract_line_id,
                    WorkspaceContractLine.contract_id == contract_id,
                    WorkspaceContractLine.status == CommercialContractLineStatus.active,
                    WorkspaceContractLine.effective_from <= now,
                    or_(
                        WorkspaceContractLine.effective_until.is_(None),
                        WorkspaceContractLine.effective_until > now,
                    ),
                )
            )
            if line is None:
                raise CommercialCatalogValidationError(
                    "A linha alvo não pertence ao contrato ativo."
                )
        overlap_conditions = [
            CommercialAdjustment.contract_id == contract_id,
            CommercialAdjustment.id != exclude_adjustment_id,
            CommercialAdjustment.status.in_(
                [CommercialRecordStatus.scheduled, CommercialRecordStatus.active]
            ),
            or_(
                CommercialAdjustment.ends_at.is_(None),
                CommercialAdjustment.ends_at > payload.starts_at,
            ),
        ]
        if payload.ends_at is not None:
            overlap_conditions.append(CommercialAdjustment.starts_at < payload.ends_at)
        overlapping = list(
            (await self.db.scalars(select(CommercialAdjustment).where(*overlap_conditions))).all()
        )
        same_group = [item for item in overlapping if item.stack_group == payload.stack_group]
        if same_group and (not payload.stackable or any(not item.stackable for item in same_group)):
            raise CommercialCatalogConflictError(
                "Já existe ajuste não acumulável no mesmo stackGroup e período."
            )
        recurring_types = {
            CommercialAdjustmentType.negotiated_price,
            CommercialAdjustmentType.percentage_discount,
            CommercialAdjustmentType.fixed_discount,
            CommercialAdjustmentType.courtesy_period,
        }
        same_scope = [
            item
            for item in overlapping
            if item.contract_line_id == payload.contract_line_id and item.type in recurring_types
        ]
        negotiated_conflict = (
            payload.type in recurring_types
            and any(item.type is CommercialAdjustmentType.negotiated_price for item in same_scope)
        ) or (payload.type is CommercialAdjustmentType.negotiated_price and bool(same_scope))
        if negotiated_conflict and (
            not payload.stackable or any(not item.stackable for item in same_scope)
        ):
            raise CommercialCatalogConflictError(
                "Preço negociado é exclusivo no mesmo escopo por padrão."
            )

    async def add_bonus(
        self,
        *,
        workspace_id: UUID,
        payload: EntitlementBonusCreateRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[EntitlementBonusRead, bool]:
        self._validate_bonus_boundary(payload.entitlement)
        request_hash = self._hash(payload.model_dump(mode="json"))
        contract_id = await self.db.scalar(
            select(WorkspaceCommercialContract.id).where(
                WorkspaceCommercialContract.workspace_id == workspace_id
            )
        )
        if contract_id is None:
            raise CommercialContractNotFoundError(
                "Bônus exige contrato comercial canônico."
            )
        await self._lock_command(
            operation="add_bonus",
            scope_key=str(workspace_id),
            aggregate_key=str(workspace_id),
        )
        receipt = await self._existing_receipt(
            operation="add_bonus",
            scope_key=str(workspace_id),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt is not None:
            return EntitlementBonusRead.model_validate(receipt.response_payload), True
        workspace = await self.db.get(Workspace, workspace_id)
        if workspace is None or workspace.deleted_at is not None:
            raise CommercialCatalogNotFoundError("Workspace não encontrado.")
        bonus = EntitlementBonus(
            workspace_id=workspace_id,
            entitlement=payload.entitlement,
            mode=payload.mode,
            quantity=payload.quantity,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            renews_each_period=payload.renews_each_period,
            portability=payload.portability,
            reason=payload.reason,
            source=payload.source,
            status=_record_status_for_window(payload.starts_at, payload.ends_at),
            version=1,
            created_by_user_id=actor_user_id,
        )
        self.db.add(bonus)
        await self.db.flush()
        response = EntitlementBonusRead.model_validate(bonus)
        self._event(
            event_type="entitlement_bonus_added",
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            resource_type="entitlement_bonus",
            resource_id=bonus.id,
            before=None,
            after=response.model_dump(mode="json"),
            reason=payload.reason,
            correlation_id=idempotency_key,
        )
        await self.db.flush()
        await self._record_receipt(
            operation="add_bonus",
            scope_key=str(workspace_id),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=response.model_dump(mode="json"),
            actor_user_id=actor_user_id,
        )
        return response, False

    async def list_bonuses(self, workspace_id: UUID) -> list[EntitlementBonusRead]:
        rows = list(
            (
                await self.db.scalars(
                    select(EntitlementBonus)
                    .where(EntitlementBonus.workspace_id == workspace_id)
                    .order_by(EntitlementBonus.created_at.desc())
                )
            ).all()
        )
        return [EntitlementBonusRead.model_validate(item) for item in rows]

    async def update_bonus(
        self,
        *,
        bonus_id: UUID,
        expected_version: int,
        payload: EntitlementBonusUpdateRequest,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[EntitlementBonusRead, bool]:
        self._validate_bonus_boundary(payload.entitlement)
        request_hash = self._hash(
            {**payload.model_dump(mode="json"), "expected_version": expected_version}
        )
        scope = str(bonus_id)
        workspace_id = await self.db.scalar(
            select(EntitlementBonus.workspace_id).where(EntitlementBonus.id == bonus_id)
        )
        if workspace_id is None:
            raise CommercialCatalogNotFoundError("Bônus não encontrado.")
        await self._lock_command(
            operation="update_bonus",
            scope_key=scope,
            aggregate_key=str(workspace_id),
        )
        receipt = await self._existing_receipt(
            operation="update_bonus",
            scope_key=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt is not None:
            return EntitlementBonusRead.model_validate(receipt.response_payload), True
        bonus = await self.db.scalar(
            select(EntitlementBonus).where(EntitlementBonus.id == bonus_id).with_for_update()
        )
        if bonus is None:
            raise CommercialCatalogNotFoundError("Bônus não encontrado.")
        if bonus.version != expected_version:
            raise CommercialCatalogConflictError(
                "O bônus mudou. Recarregue os dados antes de confirmar."
            )
        if bonus.status in {CommercialRecordStatus.revoked, CommercialRecordStatus.expired}:
            raise CommercialCatalogConflictError("Bônus encerrado não pode ser editado.")
        before = EntitlementBonusRead.model_validate(bonus)
        bonus.entitlement = payload.entitlement
        bonus.mode = payload.mode
        bonus.quantity = payload.quantity
        bonus.starts_at = payload.starts_at
        bonus.ends_at = payload.ends_at
        bonus.renews_each_period = payload.renews_each_period
        bonus.portability = payload.portability
        bonus.reason = payload.reason
        bonus.source = payload.source
        bonus.status = _record_status_for_window(payload.starts_at, payload.ends_at)
        bonus.version += 1
        await self.db.flush()
        response = EntitlementBonusRead.model_validate(bonus)
        self._event(
            event_type="entitlement_bonus_updated",
            actor_user_id=actor_user_id,
            workspace_id=bonus.workspace_id,
            resource_type="entitlement_bonus",
            resource_id=bonus.id,
            before=before.model_dump(mode="json"),
            after=response.model_dump(mode="json"),
            reason=payload.reason,
            correlation_id=idempotency_key,
        )
        await self.db.flush()
        await self._record_receipt(
            operation="update_bonus",
            scope_key=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=response.model_dump(mode="json"),
            actor_user_id=actor_user_id,
        )
        return response, False

    @staticmethod
    def _validate_bonus_boundary(entitlement: EntitlementKey) -> None:
        if entitlement in _UNENFORCED_LEGAL_CAPABILITIES:
            raise CommercialCatalogValidationError(
                f"{entitlement.value} não possui enforcement operacional e não aceita bônus."
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
        request_hash = self._hash(
            {**payload.model_dump(mode="json"), "expected_version": expected_version}
        )
        scope = str(bonus_id)
        workspace_id = await self.db.scalar(
            select(EntitlementBonus.workspace_id).where(EntitlementBonus.id == bonus_id)
        )
        if workspace_id is None:
            raise CommercialCatalogNotFoundError("Bônus não encontrado.")
        await self._lock_command(
            operation="revoke_bonus",
            scope_key=scope,
            aggregate_key=str(workspace_id),
        )
        receipt = await self._existing_receipt(
            operation="revoke_bonus",
            scope_key=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt is not None:
            return EntitlementBonusRead.model_validate(receipt.response_payload), True
        bonus = await self.db.scalar(
            select(EntitlementBonus).where(EntitlementBonus.id == bonus_id).with_for_update()
        )
        if bonus is None:
            raise CommercialCatalogNotFoundError("Bônus não encontrado.")
        if bonus.version != expected_version:
            raise CommercialCatalogConflictError(
                "O bônus mudou. Recarregue os dados antes de confirmar."
            )
        if bonus.status is CommercialRecordStatus.revoked:
            raise CommercialCatalogConflictError("Bônus já revogado.")
        before = EntitlementBonusRead.model_validate(bonus)
        bonus.status = CommercialRecordStatus.revoked
        bonus.reason = payload.reason
        bonus.version += 1
        await self.db.flush()
        response = EntitlementBonusRead.model_validate(bonus)
        self._event(
            event_type="entitlement_bonus_revoked",
            actor_user_id=actor_user_id,
            workspace_id=bonus.workspace_id,
            resource_type="entitlement_bonus",
            resource_id=bonus.id,
            before=before.model_dump(mode="json"),
            after=response.model_dump(mode="json"),
            reason=payload.reason,
            correlation_id=idempotency_key,
        )
        await self.db.flush()
        await self._record_receipt(
            operation="revoke_bonus",
            scope_key=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=response.model_dump(mode="json"),
            actor_user_id=actor_user_id,
        )
        return response, False

    async def _price_preview(
        self,
        contract: WorkspaceCommercialContract,
        lines: list[WorkspaceContractLineRead],
    ) -> PricePreviewRead:
        now = datetime.now(UTC)
        adjustments = await self._active_adjustments(contract.id, now=now)
        return self._calculate_price(lines, adjustments, now=now)

    async def _active_adjustments(
        self,
        contract_id: UUID,
        *,
        now: datetime,
    ) -> list[CommercialAdjustment]:
        return list(
            (
                await self.db.scalars(
                    select(CommercialAdjustment).where(
                        CommercialAdjustment.contract_id == contract_id,
                        CommercialAdjustment.status.in_(
                            [CommercialRecordStatus.scheduled, CommercialRecordStatus.active]
                        ),
                        CommercialAdjustment.starts_at <= now,
                        CommercialAdjustment.remaining_cycles.is_(None),
                        or_(
                            CommercialAdjustment.ends_at.is_(None),
                            CommercialAdjustment.ends_at > now,
                        ),
                    )
                )
            ).all()
        )

    async def recurring_cycle_price_cents(
        self,
        contract_id: UUID,
        *,
        billing_interval: CommercialBillingInterval,
        effective_at: datetime,
    ) -> int:
        """Return the exact recurring amount in the contract's native cycle.

        Canonical price previews intentionally expose MRR.  Reconstructing an
        annual compatibility invoice as ``rounded_mrr * 12`` loses cents.  This
        method applies recurring adjustments once on the annual amounts, while
        contract-wide fixed/negotiated values remain monthly terms and are
        expanded to the annual cycle.
        """

        now = _as_utc(effective_at)
        lines = await self._load_line_reads(contract_id)
        active_lines = [
            item
            for item in lines
            if item.status is CommercialContractLineStatus.active
            and _active_at(item.effective_from, item.effective_until, now)
        ]
        if not active_lines or any(
            item.billing_interval is not billing_interval for item in active_lines
        ):
            raise CommercialCatalogValidationError(
                "Preço do ciclo exige linhas vigentes no mesmo intervalo."
            )
        adjustments = await self._active_adjustments(contract_id, now=now)
        adjustments.sort(key=lambda item: (item.starts_at, str(item.id)))
        multiplier = 12 if billing_interval is CommercialBillingInterval.annual else 1
        recurring_by_line = {
            item.id: item.contracted_price_cents * item.quantity for item in active_lines
        }

        global_negotiated: int | None = None
        for item in adjustments:
            if item.type is not CommercialAdjustmentType.negotiated_price:
                continue
            if item.contract_line_id is None:
                global_negotiated = item.value * multiplier
            elif item.contract_line_id in recurring_by_line:
                recurring_by_line[item.contract_line_id] = item.value

        for item in adjustments:
            if item.contract_line_id not in recurring_by_line:
                continue
            current = recurring_by_line[item.contract_line_id]
            if item.type is CommercialAdjustmentType.percentage_discount:
                recurring_by_line[item.contract_line_id] = (
                    current - current * item.value // 10_000
                )
            elif item.type is CommercialAdjustmentType.fixed_discount:
                recurring_by_line[item.contract_line_id] = max(current - item.value, 0)
            elif item.type is CommercialAdjustmentType.courtesy_period:
                recurring_by_line[item.contract_line_id] = 0

        recurring = (
            global_negotiated
            if global_negotiated is not None
            else sum(recurring_by_line.values())
        )
        for item in adjustments:
            if item.contract_line_id is not None:
                continue
            if item.type is CommercialAdjustmentType.percentage_discount:
                recurring -= recurring * item.value // 10_000
            elif item.type is CommercialAdjustmentType.fixed_discount:
                recurring = max(recurring - item.value * multiplier, 0)
            elif item.type is CommercialAdjustmentType.courtesy_period:
                recurring = 0
        return max(recurring, 0)

    async def _active_bonuses(
        self,
        workspace_id: UUID,
        *,
        now: datetime,
    ) -> list[EntitlementBonus]:
        return list(
            (
                await self.db.scalars(
                    select(EntitlementBonus).where(
                        EntitlementBonus.workspace_id == workspace_id,
                        EntitlementBonus.status.in_(
                            [CommercialRecordStatus.scheduled, CommercialRecordStatus.active]
                        ),
                        EntitlementBonus.starts_at <= now,
                        or_(EntitlementBonus.ends_at.is_(None), EntitlementBonus.ends_at > now),
                    )
                )
            ).all()
        )

    @staticmethod
    def _calculate_price(
        lines: list[WorkspaceContractLineRead],
        adjustments: list[CommercialAdjustment],
        *,
        now: datetime | None = None,
        line_id_remap: Mapping[UUID, UUID] | None = None,
    ) -> PricePreviewRead:
        now = now or datetime.now(UTC)
        line_id_remap = line_id_remap or {}
        active_lines = [
            item
            for item in lines
            if item.status is CommercialContractLineStatus.active
            and _active_at(item.effective_from, item.effective_until, now)
        ]
        list_total = sum(
            _monthly_equivalent_cents(
                item.list_price_cents * item.quantity,
                item.billing_interval,
            )
            for item in active_lines
        )
        contracted_by_line = {
            item.id: _monthly_equivalent_cents(
                item.contracted_price_cents * item.quantity,
                item.billing_interval,
            )
            for item in active_lines
        }
        contracted_total = sum(contracted_by_line.values())
        interval_by_line = {item.id: item.billing_interval for item in active_lines}
        adjustments.sort(key=lambda item: (item.starts_at, str(item.id)))
        global_negotiated: int | None = None
        for item in adjustments:
            target_line_id = (
                line_id_remap.get(item.contract_line_id, item.contract_line_id)
                if item.contract_line_id is not None
                else None
            )
            if item.type is not CommercialAdjustmentType.negotiated_price:
                continue
            if target_line_id is None:
                global_negotiated = item.value
            elif target_line_id in contracted_by_line:
                contracted_by_line[target_line_id] = _monthly_equivalent_cents(
                    item.value,
                    interval_by_line[target_line_id],
                )

        for item in adjustments:
            target_line_id = (
                line_id_remap.get(item.contract_line_id, item.contract_line_id)
                if item.contract_line_id is not None
                else None
            )
            if target_line_id is None or target_line_id not in contracted_by_line:
                continue
            current = contracted_by_line[target_line_id]
            if item.type is CommercialAdjustmentType.percentage_discount:
                contracted_by_line[target_line_id] = current - current * item.value // 10_000
            elif item.type is CommercialAdjustmentType.fixed_discount:
                contracted_by_line[target_line_id] = max(
                    current
                    - _monthly_equivalent_cents(
                        item.value,
                        interval_by_line[target_line_id],
                    ),
                    0,
                )
            elif item.type is CommercialAdjustmentType.courtesy_period:
                contracted_by_line[target_line_id] = 0

        recurring = (
            global_negotiated if global_negotiated is not None else sum(contracted_by_line.values())
        )
        for item in adjustments:
            if item.contract_line_id is not None:
                continue
            if item.type is CommercialAdjustmentType.percentage_discount:
                recurring -= recurring * item.value // 10_000
            elif item.type is CommercialAdjustmentType.fixed_discount:
                recurring = max(recurring - item.value, 0)
            elif item.type is CommercialAdjustmentType.courtesy_period:
                recurring = 0
        recurring = max(recurring, 0)
        credits = sum(
            item.value
            for item in adjustments
            if item.type is CommercialAdjustmentType.invoice_credit
        )
        return PricePreviewRead(
            list_price_cents=list_total,
            contracted_price_cents=contracted_total,
            recurring_price_cents=recurring,
            invoice_credit_cents=credits,
            amount_due_cents=max(recurring - credits, 0),
        )

    async def resolve_effective_manifest(
        self, workspace_id: UUID
    ) -> EffectiveCapabilityManifestRead:
        contract = await self.db.scalar(
            select(WorkspaceCommercialContract).where(
                WorkspaceCommercialContract.workspace_id == workspace_id
            )
        )
        if contract is None:
            raise CommercialContractNotFoundError("Workspace sem contrato comercial canônico.")
        lines = await self._load_line_reads(contract.id)
        return await self._build_manifest(
            workspace_id=workspace_id,
            contract_id=contract.id,
            contract_version=contract.version,
            contract_status=contract.status,
            lines=lines,
            generated_at=datetime.now(UTC),
        )

    async def resolve_effective_capability(
        self, workspace_id: UUID, entitlement: EntitlementKey | str
    ) -> EffectiveCapabilityRead:
        key = EntitlementKey(entitlement)
        return (await self.resolve_effective_manifest(workspace_id)).capabilities[key]

    async def _build_manifest(
        self,
        *,
        workspace_id: UUID,
        contract_id: UUID,
        contract_version: int,
        contract_status: CommercialContractStatus,
        lines: list[WorkspaceContractLineRead],
        generated_at: datetime,
        bonuses_override: list[EntitlementBonus] | None = None,
    ) -> EffectiveCapabilityManifestRead:
        self._require_manifest_workspace_context(workspace_id)
        active_lines = [
            item
            for item in lines
            if item.status is CommercialContractLineStatus.active
            and _active_at(item.effective_from, item.effective_until, generated_at)
        ]
        policies: list[tuple[WorkspaceContractLineRead, CommercialOfferSnapshotRead]] = []
        for line in active_lines:
            version = await self.db.get(CommercialOfferVersion, line.policy_version_id)
            if version is None or version.offer_id != line.offer_id:
                raise CommercialCatalogValidationError(
                    "Contrato referencia versão de política ausente ou incompatível."
                )
            snapshot = self._version_snapshot(version)
            merged = dict(snapshot.entitlements)
            merged.update(line.entitlement_overrides)
            snapshot = snapshot.model_copy(update={"entitlements": merged})
            policies.append((line, snapshot))

        bonuses = (
            bonuses_override
            if bonuses_override is not None
            else await self._active_bonuses(workspace_id, now=generated_at)
        )
        overrides = {
            item.entitlement: item
            for item in (
                await self.db.scalars(
                    select(WorkspaceEntitlementOverride).where(
                        WorkspaceEntitlementOverride.workspace_id == workspace_id
                    )
                )
            ).all()
        }
        month_start = generated_at.date().replace(day=1)
        usage = {
            item.entitlement: item.consumed_quantity + item.reserved_quantity
            for item in (
                await self.db.scalars(
                    select(WorkspaceMonthlyUsage).where(
                        WorkspaceMonthlyUsage.workspace_id == workspace_id,
                        WorkspaceMonthlyUsage.period_start == month_start,
                    )
                )
            ).all()
        }
        contact_count = await self.db.scalar(
            select(func.count(WhatsAppContact.id)).where(
                WhatsAppContact.workspace_id == workspace_id
            )
        )
        usage[EntitlementKey.contacts] = int(contact_count or 0)

        contract_operational = contract_status in {
            CommercialContractStatus.active,
            CommercialContractStatus.trialing,
        }
        capabilities: dict[EntitlementKey, EffectiveCapabilityRead] = {}
        for key in EntitlementKey:
            settings: list[tuple[WorkspaceContractLineRead, CommercialEntitlementSetting]] = [
                (line, snapshot.entitlements[key])
                for line, snapshot in policies
                if key in snapshot.entitlements
            ]
            enabled_settings = [(line, setting) for line, setting in settings if setting.enabled]
            contracted = bool(enabled_settings)
            enabled = contracted
            sources: list[CapabilitySourceRead] = []
            limits: list[int | None] = []
            grace_limits: list[int | None] = []
            reference = settings[0][1] if settings else _setting(key, enabled=False, limit=None)
            for line, setting in enabled_settings:
                contribution = (
                    True
                    if setting.period is CommercialEntitlementPeriod.feature
                    else setting.limit_quantity
                )
                sources.append(
                    CapabilitySourceRead(
                        type=(
                            "legal" if line.offer_family is CommercialOfferFamily.legal else "plan"
                        ),
                        id=line.id or line.offer_id,
                        contribution=contribution,
                    )
                )
                multiplier = (
                    line.quantity
                    if setting.composition is CommercialEntitlementComposition.sum
                    else 1
                )
                limits.append(
                    None if setting.limit_quantity is None else setting.limit_quantity * multiplier
                )
                grace_limits.append(
                    None if setting.grace_quantity is None else setting.grace_quantity * multiplier
                )
            if enabled_settings and reference.period is not CommercialEntitlementPeriod.feature:
                composition = enabled_settings[0][1].composition
                canonical_dimensions = (
                    enabled_settings[0][1].unit,
                    enabled_settings[0][1].period,
                    enabled_settings[0][1].composition,
                    enabled_settings[0][1].enforcement,
                )
                if any(
                    (
                        setting.unit,
                        setting.period,
                        setting.composition,
                        setting.enforcement,
                    )
                    != canonical_dimensions
                    for _, setting in enabled_settings
                ):
                    raise CommercialCatalogValidationError(
                        f"Unidade, período, composição ou enforcement divergem para {key.value}."
                    )
                reference = enabled_settings[0][1]
                if any(value is None for value in limits):
                    effective_limit = None
                elif composition is CommercialEntitlementComposition.sum:
                    effective_limit = sum(value or 0 for value in limits)
                else:
                    effective_limit = max((value or 0 for value in limits), default=0)
                finite_graces = [value for value in grace_limits if value is not None]
                if effective_limit is None or not finite_graces:
                    grace_limit = None
                elif composition is CommercialEntitlementComposition.sum:
                    grace_limit = effective_limit + sum(finite_graces)
                else:
                    # `maximum` chooses one line's executable boundary. Taking
                    # the base maximum and grace maximum independently can
                    # manufacture a capacity that no contracted line grants.
                    grace_limit = max(
                        (limit or 0) + (grace or 0)
                        for limit, grace in zip(
                            limits,
                            grace_limits,
                            strict=True,
                        )
                    )
            else:
                effective_limit = None
                grace_limit = None

            for bonus in (item for item in bonuses if item.entitlement == key):
                if bonus.mode is EntitlementBonusMode.feature_grant:
                    enabled = True
                    contribution: int | bool | None = True
                else:
                    # Quantity deltas augment an enabled contracted capacity;
                    # they do not silently create a missing product.
                    if not enabled:
                        continue
                    if effective_limit is not None:
                        bonus_quantity = bonus.quantity or 0
                        effective_limit += bonus_quantity
                        if grace_limit is not None:
                            # Grace is additive to the composed finite capacity.
                            # A capacity bonus therefore moves both the regular
                            # limit and the final executable boundary.
                            grace_limit += bonus_quantity
                    contribution = bonus.quantity
                sources.append(
                    CapabilitySourceRead(type="bonus", id=bonus.id, contribution=contribution)
                )

            denial_reason: str | None = None
            override = overrides.get(key)
            if override is not None:
                invalid_finite_override = (
                    key in QUANTITATIVE_ENTITLEMENTS
                    and override.mode in {OverrideMode.grant, OverrideMode.limit}
                    and (
                        override.quantity is None
                        or isinstance(override.quantity, bool)
                        or override.quantity < 0
                    )
                )
                if invalid_finite_override:
                    enabled = False
                    effective_limit = None
                    grace_limit = None
                    denial_reason = "not_configured"
                    contribution = None
                elif override.mode is OverrideMode.revoke:
                    enabled = False
                    effective_limit = 0
                    grace_limit = None
                    denial_reason = "permission_denied"
                    contribution = False
                elif override.mode is OverrideMode.limit:
                    enabled = True
                    effective_limit = override.quantity
                    grace_limit = None
                    contribution = override.quantity
                else:
                    enabled = True
                    if override.quantity is not None:
                        effective_limit = override.quantity
                        grace_limit = None
                    contribution = override.quantity if override.quantity is not None else True
                sources.append(
                    CapabilitySourceRead(type="override", id=override.id, contribution=contribution)
                )

            if (
                key in QUANTITATIVE_ENTITLEMENTS
                and enabled
                and (
                    effective_limit is None
                    or isinstance(effective_limit, bool)
                    or effective_limit < 0
                )
            ):
                # No implicit unlimited quantitative capacity. Old/corrupt
                # rows remain visible in sources but resolve as an explicit
                # denial instead of reopening an unbounded path.
                enabled = False
                effective_limit = None
                grace_limit = None
                denial_reason = "not_configured"

            used = usage.get(key)
            permitted = enabled and contract_operational
            blocking_limit = effective_limit
            if not contract_operational:
                denial_reason = "suspended"
            elif not enabled and denial_reason is None:
                denial_reason = "not_contracted"
            else:
                blocking_limit = (
                    grace_limit
                    if reference.enforcement is CommercialEntitlementEnforcement.grace
                    and grace_limit is not None
                    else effective_limit
                )
            if (
                permitted
                and blocking_limit is not None
                and used is not None
                and used >= blocking_limit
                and reference.period is not CommercialEntitlementPeriod.feature
            ):
                permitted = False
                denial_reason = "quota_exhausted"
            remaining = (
                None if used is None or effective_limit is None else max(effective_limit - used, 0)
            )
            capabilities[key] = EffectiveCapabilityRead(
                key=key,
                contracted=contracted,
                enabled=enabled,
                permitted=permitted,
                limit=effective_limit,
                grace_limit=grace_limit,
                used=used,
                remaining=remaining,
                unit=reference.unit,
                period=reference.period,
                enforcement=reference.enforcement,
                sources=sources,
                denial_reason=denial_reason,
                upgrade_offer=None,
            )

        return EffectiveCapabilityManifestRead(
            workspace_id=workspace_id,
            contract_id=contract_id,
            contract_version=contract_version,
            contract_status=contract_status,
            active_products=[line.sku for line in active_lines],
            capabilities=capabilities,
            generated_at=generated_at,
        )


__all__ = [
    "CommercialCatalogConflictError",
    "CommercialCatalogError",
    "CommercialCatalogNotFoundError",
    "CommercialCatalogService",
    "CommercialCatalogValidationError",
    "CommercialContractNotFoundError",
    "CommercialIdempotencyConflictError",
]
