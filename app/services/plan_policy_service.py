"""Transactional plan-policy publication, rollout, and entitlement resolution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.advisory import acquire_transaction_advisory_lock
from app.models.billing import Plan, Subscription
from app.models.commercial_catalog import CommercialOffer, WorkspaceCommercialContract
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.plan_policy import (
    PlanPolicyAuditEvent,
    PlanPolicyDraft,
    PlanPolicyIdempotencyRecord,
    PlanPolicyRolloutMode,
    PlanPolicyVersion,
    WorkspacePlanPolicyAssignment,
)
from app.models.workspace import Workspace
from app.schemas.plan_policy import (
    FEATURE_ENTITLEMENTS,
    PlanPolicyAccountPreviewRead,
    PlanPolicyAuditEventRead,
    PlanPolicyDraftWrite,
    PlanPolicyEditorRead,
    PlanPolicyEntitlementCatalogItemRead,
    PlanPolicyEntitlementSetting,
    PlanPolicyMaterializationRead,
    PlanPolicyParameters,
    PlanPolicyPlanSummaryRead,
    PlanPolicyPreviewRead,
    PlanPolicyPreviewRequest,
    PlanPolicyPublishRequest,
    PlanPolicyRolloutRule,
    PlanPolicySnapshot,
    ResolvedPlanEntitlementRead,
    WorkspacePlanPolicyResolutionRead,
)
from app.services.entitlement_service import ENTITLEMENT_CATALOG


class PlanPolicyError(RuntimeError):
    """Base class for versioned plan-policy failures."""


class PlanPolicyNotFoundError(PlanPolicyError):
    pass


class PlanPolicyConflictError(PlanPolicyError):
    pass


class PlanPolicyValidationError(PlanPolicyError):
    pass


@dataclass(frozen=True, slots=True)
class PlanPolicyPublicationResult:
    policy: PlanPolicyVersion
    affected_count: int
    assignments_created: int
    idempotent_replay: bool


Clock = Callable[[], datetime]
CATALOG_AUTHORITY_LOCK_NAMESPACE = "commercial:catalog-authority"
CATALOG_AUTHORITY_LOCK_VALUE = "canonical-vs-plan-policy"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _json_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _json_copy(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _normalize_etag(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:].strip()
    return normalized.strip('"')


class PlanPolicyService:
    """Owns policy history while leaving the legacy mutable catalog untouched."""

    def __init__(self, db: AsyncSession, *, clock: Clock | None = None):
        self.db = db
        self._clock = clock or (lambda: datetime.now(UTC))

    def now(self) -> datetime:
        return _utc(self._clock())

    async def _lock_catalog_authority(self) -> None:
        """Fence legacy writers against first canonical catalog publication."""

        await acquire_transaction_advisory_lock(
            self.db,
            namespace=CATALOG_AUTHORITY_LOCK_NAMESPACE,
            value=CATALOG_AUTHORITY_LOCK_VALUE,
        )

    async def _canonical_catalog_active(self) -> bool:
        return (
            await self.db.scalar(select(CommercialOffer.id).limit(1))
            is not None
        )

    async def _canonical_contract_id(self, workspace_id: UUID) -> UUID | None:
        return await self.db.scalar(
            select(WorkspaceCommercialContract.id).where(
                WorkspaceCommercialContract.workspace_id == workspace_id
            )
        )

    @staticmethod
    def _canonical_catalog_conflict(message: str) -> PlanPolicyConflictError:
        return PlanPolicyConflictError(
            f"O catálogo canônico está ativo. {message}"
        )

    @staticmethod
    def entitlement_catalog() -> list[PlanPolicyEntitlementCatalogItemRead]:
        return [
            PlanPolicyEntitlementCatalogItemRead(
                key=key,
                control=("checkbox" if key in FEATURE_ENTITLEMENTS else "checkbox_and_number"),
                supports_limit=key not in FEATURE_ENTITLEMENTS,
                requires_finite_limit=key is EntitlementKey.tokens,
            )
            for key in EntitlementKey
        ]

    async def list_plan_summaries(self) -> list[PlanPolicyPlanSummaryRead]:
        plans = list(
            (
                await self.db.scalars(select(Plan).order_by(Plan.sort_order.asc(), Plan.name.asc()))
            ).all()
        )
        if not plans:
            return []
        plan_ids = [plan.id for plan in plans]
        drafts = {
            draft.plan_id: draft
            for draft in (
                await self.db.scalars(
                    select(PlanPolicyDraft).where(PlanPolicyDraft.plan_id.in_(plan_ids))
                )
            ).all()
        }
        versions = list(
            (
                await self.db.scalars(
                    select(PlanPolicyVersion)
                    .where(PlanPolicyVersion.plan_id.in_(plan_ids))
                    .order_by(
                        PlanPolicyVersion.plan_id.asc(),
                        PlanPolicyVersion.version.desc(),
                    )
                )
            ).all()
        )
        latest: dict[UUID, PlanPolicyVersion] = {}
        for version in versions:
            latest.setdefault(version.plan_id, version)
        return [
            PlanPolicyPlanSummaryRead(
                plan_id=plan.id,
                plan_tier=plan.tier.value,
                plan_name=plan.name,
                draft_revision=(drafts[plan.id].revision if plan.id in drafts else None),
                draft_etag=(drafts[plan.id].etag if plan.id in drafts else None),
                latest_version=(latest[plan.id].version if plan.id in latest else None),
                latest_effective_at=(
                    _utc(latest[plan.id].effective_at) if plan.id in latest else None
                ),
            )
            for plan in plans
        ]

    async def list_plan_summaries_page(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[PlanPolicyPlanSummaryRead], int]:
        summaries = await self.list_plan_summaries()
        return summaries[offset:offset + limit], len(summaries)

    async def get_editor(self, plan_id: UUID) -> PlanPolicyEditorRead:
        plan = await self._get_plan(plan_id)
        draft = await self.db.scalar(
            select(PlanPolicyDraft).where(PlanPolicyDraft.plan_id == plan_id)
        )
        if draft is not None:
            snapshot = self._parse_snapshot(draft.snapshot)
            return PlanPolicyEditorRead(
                plan_id=plan.id,
                plan_tier=plan.tier.value,
                source="draft",
                etag=draft.etag,
                draft_revision=draft.revision,
                snapshot=snapshot,
            )

        latest = await self._latest_version(plan_id)
        if latest is not None:
            snapshot = self._parse_snapshot(latest.snapshot)
            return PlanPolicyEditorRead(
                plan_id=plan.id,
                plan_tier=plan.tier.value,
                source="published",
                etag=latest.snapshot_hash,
                published_version=latest.version,
                snapshot=snapshot,
            )

        snapshot = await self._legacy_snapshot(plan)
        return PlanPolicyEditorRead(
            plan_id=plan.id,
            plan_tier=plan.tier.value,
            source="legacy",
            etag=_json_hash(self._snapshot_payload(snapshot)),
            snapshot=snapshot,
        )

    async def save_draft(
        self,
        *,
        plan_id: UUID,
        payload: PlanPolicyDraftWrite,
        actor_user_id: UUID,
        expected_etag: str | None = None,
    ) -> PlanPolicyDraft:
        await self._lock_catalog_authority()
        if await self._canonical_catalog_active():
            raise self._canonical_catalog_conflict(
                "Crie rascunhos exclusivamente pelo novo Estúdio de Planos."
            )
        await self._get_plan(plan_id, lock=True)
        snapshot_payload = self._snapshot_payload(payload.snapshot)
        etag = _json_hash(snapshot_payload)
        draft = await self.db.scalar(
            select(PlanPolicyDraft).where(PlanPolicyDraft.plan_id == plan_id).with_for_update()
        )
        normalized_expected = _normalize_etag(expected_etag)
        if draft is None:
            if normalized_expected is not None:
                raise PlanPolicyConflictError(
                    "O rascunho ainda não existe; remova o If-Match e tente novamente."
                )
            draft = PlanPolicyDraft(
                plan_id=plan_id,
                revision=1,
                etag=etag,
                snapshot=snapshot_payload,
                updated_by_user_id=actor_user_id,
            )
            self.db.add(draft)
            previous_etag = None
        else:
            if draft.etag == etag:
                return draft
            if normalized_expected != draft.etag:
                raise PlanPolicyConflictError(
                    "O rascunho mudou desde que foi carregado; atualize o editor."
                )
            previous_etag = draft.etag
            draft.revision += 1
            draft.etag = etag
            draft.snapshot = snapshot_payload
            draft.updated_by_user_id = actor_user_id

        self._add_audit(
            event_type="draft_saved",
            plan_id=plan_id,
            actor_user_id=actor_user_id,
            event_data={
                "revision": draft.revision,
                "previousEtag": previous_etag,
                "etag": etag,
            },
        )
        await self.db.flush()
        await self.db.refresh(draft)
        return draft

    async def preview(
        self,
        *,
        plan_id: UUID,
        rollout: PlanPolicyPreviewRequest,
    ) -> PlanPolicyPreviewRead:
        await self._get_plan(plan_id)
        effective_at = _utc(rollout.effective_at or self.now())
        statement = self._target_account_statement(
            plan_id=plan_id,
            rollout_mode=rollout.rollout_mode,
            effective_at=effective_at,
            rule=rollout.rule,
        )
        affected_count = int(
            await self.db.scalar(
                select(func.count()).select_from(statement.order_by(None).subquery())
            )
            or 0
        )
        rows = (await self.db.execute(statement.limit(rollout.sample_limit))).all()
        accounts: list[PlanPolicyAccountPreviewRead] = []
        for workspace, subscription in rows:
            current_version = await self.db.scalar(
                select(func.max(PlanPolicyVersion.version))
                .join(
                    WorkspacePlanPolicyAssignment,
                    WorkspacePlanPolicyAssignment.policy_version_id == PlanPolicyVersion.id,
                )
                .where(
                    WorkspacePlanPolicyAssignment.workspace_id == workspace.id,
                    WorkspacePlanPolicyAssignment.plan_id == plan_id,
                )
            )
            accounts.append(
                PlanPolicyAccountPreviewRead(
                    workspace_id=workspace.id,
                    name=workspace.name,
                    slug=workspace.slug,
                    workspace_status=workspace.status,
                    subscription_status=subscription.status,
                    workspace_created_at=_utc(workspace.created_at),
                    current_policy_version=(
                        int(current_version) if current_version is not None else None
                    ),
                )
            )
        return PlanPolicyPreviewRead(
            plan_id=plan_id,
            rollout_mode=rollout.rollout_mode,
            effective_at=effective_at,
            rule=rollout.rule,
            affected_count=affected_count,
            applies_immediately=effective_at <= self.now(),
            accounts=accounts,
        )

    async def publish(
        self,
        *,
        plan_id: UUID,
        payload: PlanPolicyPublishRequest,
        actor_user_id: UUID,
        idempotency_key: str,
    ) -> PlanPolicyPublicationResult:
        await self._lock_catalog_authority()
        if await self._canonical_catalog_active():
            raise self._canonical_catalog_conflict(
                "Publique e restaure ofertas exclusivamente pelo novo Estúdio de Planos."
            )
        normalized_key = self._validate_idempotency_key(idempotency_key)
        request_payload = payload.model_dump(mode="json", by_alias=True, exclude_none=False)
        request_hash = _json_hash(request_payload)
        await self._get_plan(plan_id, lock=True)

        receipt = await self.db.scalar(
            select(PlanPolicyIdempotencyRecord)
            .where(
                PlanPolicyIdempotencyRecord.operation == "publish",
                PlanPolicyIdempotencyRecord.plan_id == plan_id,
                PlanPolicyIdempotencyRecord.idempotency_key == normalized_key,
            )
            .with_for_update()
        )
        if receipt is not None:
            if receipt.request_hash != request_hash:
                raise PlanPolicyConflictError(
                    "Idempotency-Key já foi usada para outra publicação deste plano."
                )
            version = await self.db.get(PlanPolicyVersion, receipt.policy_version_id)
            if version is None:
                raise PlanPolicyValidationError(
                    "O recibo idempotente aponta para uma versão inexistente."
                )
            return PlanPolicyPublicationResult(
                policy=version,
                affected_count=int(receipt.response_payload["affectedCount"]),
                assignments_created=int(receipt.response_payload["assignmentsCreated"]),
                idempotent_replay=True,
            )

        snapshot = await self._publication_snapshot(plan_id=plan_id, payload=payload)
        snapshot_payload = self._snapshot_payload(snapshot)
        snapshot_hash = _json_hash(snapshot_payload)
        now = self.now()
        effective_at = _utc(payload.effective_at or now)
        latest_number = int(
            await self.db.scalar(
                select(func.max(PlanPolicyVersion.version)).where(
                    PlanPolicyVersion.plan_id == plan_id
                )
            )
            or 0
        )
        rule_payload = (
            payload.rule.model_dump(mode="json", by_alias=True)
            if payload.rule is not None
            else None
        )
        version = PlanPolicyVersion(
            plan_id=plan_id,
            version=latest_number + 1,
            schema_version=snapshot.schema_version,
            snapshot=snapshot_payload,
            snapshot_hash=snapshot_hash,
            rollout_mode=payload.rollout_mode,
            effective_at=effective_at,
            rollout_rule=rule_payload,
            publication_reason=payload.reason.strip(),
            published_by_user_id=actor_user_id,
            published_at=now,
        )
        self.db.add(version)
        await self.db.flush()

        # Keep the provider-free billing catalog aligned only when the
        # publication is immediate and explicitly targets current and future
        # accounts. Targeted, new-only, and scheduled rollouts remain resolved
        # by the versioned policy without leaking their future scope into the
        # global catalog row.
        if (
            effective_at <= now
            and payload.rollout_mode is PlanPolicyRolloutMode.existing_and_new
        ):
            await self._synchronize_legacy_catalog(plan_id=plan_id, snapshot=snapshot)

        target_statement = self._target_account_statement(
            plan_id=plan_id,
            rollout_mode=payload.rollout_mode,
            effective_at=effective_at,
            rule=payload.rule,
        )
        affected_count = int(
            await self.db.scalar(
                select(func.count()).select_from(target_statement.order_by(None).subquery())
            )
            or 0
        )
        assignments_created = 0
        if effective_at <= now:
            target_rows = (await self.db.execute(target_statement)).all()
            assignments = [
                WorkspacePlanPolicyAssignment(
                    workspace_id=workspace.id,
                    plan_id=plan_id,
                    policy_version_id=version.id,
                    assignment_reason="publication",
                    assigned_by_user_id=actor_user_id,
                    assigned_at=now,
                )
                for workspace, _subscription in target_rows
            ]
            self.db.add_all(assignments)
            assignments_created = len(assignments)

        self._add_audit(
            event_type="policy_published",
            plan_id=plan_id,
            policy_version_id=version.id,
            actor_user_id=actor_user_id,
            idempotency_key=normalized_key,
            event_data={
                "version": version.version,
                "snapshotHash": snapshot_hash,
                "rolloutMode": payload.rollout_mode.value,
                "effectiveAt": effective_at.isoformat(),
                "rule": rule_payload,
                "affectedCount": affected_count,
                "assignmentsCreated": assignments_created,
            },
        )
        response_payload = {
            "policyVersionId": str(version.id),
            "affectedCount": affected_count,
            "assignmentsCreated": assignments_created,
        }
        self.db.add(
            PlanPolicyIdempotencyRecord(
                operation="publish",
                plan_id=plan_id,
                idempotency_key=normalized_key,
                request_hash=request_hash,
                policy_version_id=version.id,
                actor_user_id=actor_user_id,
                response_payload=response_payload,
                created_at=now,
            )
        )
        await self.db.flush()
        return PlanPolicyPublicationResult(
            policy=version,
            affected_count=affected_count,
            assignments_created=assignments_created,
            idempotent_replay=False,
        )

    async def _synchronize_legacy_catalog(
        self,
        *,
        plan_id: UUID,
        snapshot: PlanPolicySnapshot,
    ) -> None:
        """Mirror an immediately global policy into the local billing read model.

        The typed policy remains authoritative for runtime resolution. This
        mirror exists so the local plan catalog, quote and lifecycle screens do
        not show stale legacy values after a global publication.
        """
        if await self.db.scalar(select(CommercialOffer.id).limit(1)) is not None:
            raise PlanPolicyConflictError(
                "A projeção legada é somente leitura enquanto o catálogo canônico está ativo."
            )
        plan = await self._get_plan(plan_id, lock=True)
        plan.name = snapshot.name
        plan.description = snapshot.description
        plan.price_monthly_cents = snapshot.price_monthly_cents
        plan.price_annually_cents = snapshot.price_annually_cents
        plan.is_active = snapshot.is_active
        plan.is_public = snapshot.is_public
        plan.sort_order = snapshot.sort_order
        plan.max_matters = snapshot.parameters.max_matters

        # Reuse the existing compatibility mapping for legacy plan columns
        # while writing the complete typed entitlement set in the same tx.
        from app.services.local_billing_service import LocalBillingService

        for key, setting in snapshot.entitlements.items():
            row = await self.db.scalar(
                select(PlanEntitlement)
                .where(
                    PlanEntitlement.plan_id == plan.id,
                    PlanEntitlement.entitlement == key,
                )
                .with_for_update()
            )
            if row is None:
                row = PlanEntitlement(
                    plan_id=plan.id,
                    entitlement=key,
                )
                self.db.add(row)
            row.enabled = setting.enabled
            row.limit_quantity = setting.limit_quantity
            row.hard_limit = setting.hard_limit
            LocalBillingService._sync_legacy_entitlement(
                plan,
                key,
                setting.enabled,
                setting.limit_quantity,
            )
        await self.db.flush()

    async def list_versions(self, plan_id: UUID) -> list[PlanPolicyVersion]:
        await self._get_plan(plan_id)
        return list(
            (
                await self.db.scalars(
                    select(PlanPolicyVersion)
                    .where(PlanPolicyVersion.plan_id == plan_id)
                    .order_by(PlanPolicyVersion.version.desc())
                )
            ).all()
        )

    async def list_versions_page(
        self,
        plan_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[PlanPolicyVersion], int]:
        await self._get_plan(plan_id)
        total = int((await self.db.scalar(
            select(func.count(PlanPolicyVersion.id)).where(
                PlanPolicyVersion.plan_id == plan_id
            )
        )) or 0)
        rows = list((await self.db.scalars(
            select(PlanPolicyVersion)
            .where(PlanPolicyVersion.plan_id == plan_id)
            .order_by(PlanPolicyVersion.version.desc(), PlanPolicyVersion.id.desc())
            .limit(limit)
            .offset(offset)
        )).all())
        return rows, total

    async def get_version(self, plan_id: UUID, version: int) -> PlanPolicyVersion:
        policy = await self.db.scalar(
            select(PlanPolicyVersion).where(
                PlanPolicyVersion.plan_id == plan_id,
                PlanPolicyVersion.version == version,
            )
        )
        if policy is None:
            raise PlanPolicyNotFoundError("Versão da política de plano não encontrada.")
        return policy

    async def list_audit(
        self,
        *,
        plan_id: UUID,
        limit: int = 100,
    ) -> list[PlanPolicyAuditEventRead]:
        await self._get_plan(plan_id)
        events = list(
            (
                await self.db.scalars(
                    select(PlanPolicyAuditEvent)
                    .where(PlanPolicyAuditEvent.plan_id == plan_id)
                    .order_by(
                        PlanPolicyAuditEvent.occurred_at.desc(),
                        PlanPolicyAuditEvent.id.desc(),
                    )
                    .limit(limit)
                )
            ).all()
        )
        return [PlanPolicyAuditEventRead.model_validate(event) for event in events]

    async def resolve_workspace(
        self,
        *,
        workspace_id: UUID,
        effective_at: datetime | None = None,
        actor_user_id: UUID | None = None,
    ) -> WorkspacePlanPolicyResolutionRead:
        resolution, _created = await self._resolve_workspace(
            workspace_id=workspace_id,
            effective_at=_utc(effective_at or self.now()),
            actor_user_id=actor_user_id,
            assignment_reason="runtime_resolution",
            audit_assignment=True,
            fallback_to_legacy=True,
        )
        assert resolution is not None
        return resolution

    async def resolve_versioned_workspace(
        self,
        *,
        workspace_id: UUID,
        effective_at: datetime | None = None,
    ) -> WorkspacePlanPolicyResolutionRead | None:
        """Resolve only published policy state for the runtime integration.

        Returning ``None`` delegates to the existing PlanEntitlement/legacy path.
        This method deliberately does not invoke EntitlementResolver, preventing
        recursion when that resolver calls back into this service.
        """

        resolution, _created = await self._resolve_workspace(
            workspace_id=workspace_id,
            effective_at=_utc(effective_at or self.now()),
            actor_user_id=None,
            assignment_reason="entitlement_runtime",
            audit_assignment=False,
            fallback_to_legacy=False,
            lock_target=False,
            materialize_assignment=False,
        )
        return resolution

    async def resolve_entitlement(
        self,
        *,
        workspace_id: UUID,
        entitlement: EntitlementKey | str,
        effective_at: datetime | None = None,
    ) -> ResolvedPlanEntitlementRead:
        key = EntitlementKey(entitlement)
        resolution = await self.resolve_workspace(
            workspace_id=workspace_id,
            effective_at=effective_at,
        )
        return resolution.entitlements[key]

    async def materialize_due_assignments(
        self,
        *,
        plan_id: UUID,
        actor_user_id: UUID,
        effective_at: datetime | None = None,
        limit: int = 1_000,
    ) -> PlanPolicyMaterializationRead:
        await self._lock_catalog_authority()
        if await self._canonical_catalog_active():
            raise self._canonical_catalog_conflict(
                "Assignments legados não podem ser materializados."
            )
        await self._get_plan(plan_id, lock=True)
        reference_at = _utc(effective_at or self.now())
        workspace_ids = list(
            (
                await self.db.scalars(
                    select(Workspace.id)
                    .join(Subscription, Subscription.workspace_id == Workspace.id)
                    .where(
                        Subscription.plan_id == plan_id,
                        Workspace.deleted_at.is_(None),
                    )
                    .order_by(Workspace.created_at.asc(), Workspace.id.asc())
                    .limit(limit)
                )
            ).all()
        )
        created = 0
        for workspace_id in workspace_ids:
            _resolution, assignment_created = await self._resolve_workspace(
                workspace_id=workspace_id,
                effective_at=reference_at,
                actor_user_id=actor_user_id,
                assignment_reason="due_materialization",
                audit_assignment=False,
                fallback_to_legacy=False,
            )
            created += int(assignment_created)
        self._add_audit(
            event_type="assignments_materialized",
            plan_id=plan_id,
            actor_user_id=actor_user_id,
            event_data={
                "effectiveAt": reference_at.isoformat(),
                "evaluatedAccounts": len(workspace_ids),
                "assignmentsCreated": created,
            },
        )
        await self.db.flush()
        return PlanPolicyMaterializationRead(
            plan_id=plan_id,
            evaluated_accounts=len(workspace_ids),
            assignments_created=created,
            effective_at=reference_at,
        )

    async def _resolve_workspace(
        self,
        *,
        workspace_id: UUID,
        effective_at: datetime,
        actor_user_id: UUID | None,
        assignment_reason: str,
        audit_assignment: bool,
        fallback_to_legacy: bool,
        lock_target: bool = True,
        materialize_assignment: bool = True,
    ) -> tuple[WorkspacePlanPolicyResolutionRead | None, bool]:
        if materialize_assignment:
            await self._lock_catalog_authority()
        canonical_contract_id = await self._canonical_contract_id(workspace_id)
        if canonical_contract_id is not None:
            # A conta reconciliada é resolvida exclusivamente pelo manifesto
            # canônico. O leitor versionado sinaliza isso com ``None`` para o
            # chamador; a API legada explícita falha em vez de fabricar um
            # assignment paralelo ou retornar uma política obsoleta.
            if fallback_to_legacy:
                raise self._canonical_catalog_conflict(
                    "A conta já possui contrato comercial; use o manifesto efetivo."
                )
            return None, False

        # Enquanto houver contas ainda não reconciliadas, elas continuam
        # legíveis pelo caminho antigo. Após a ativação do catálogo, porém,
        # esse caminho é estritamente read-only e nunca cria novo assignment.
        if await self._canonical_catalog_active():
            materialize_assignment = False

        statement = (
            select(Workspace, Subscription)
            .join(Subscription, Subscription.workspace_id == Workspace.id)
            .where(
                Workspace.id == workspace_id,
                Workspace.deleted_at.is_(None),
            )
        )
        # Runtime entitlement reads can happen while billing already owns the
        # subscription row. Avoid acquiring workspace+subscription locks in a
        # second order here; command paths materialize assignments explicitly.
        if lock_target:
            statement = statement.with_for_update()
        row = (await self.db.execute(statement)).first()
        if row is None:
            raise PlanPolicyNotFoundError(
                "Workspace com assinatura não encontrado para resolver a política."
            )
        workspace, subscription = row
        plan = await self._get_plan(subscription.plan_id)

        current_row = (
            await self.db.execute(
                select(WorkspacePlanPolicyAssignment, PlanPolicyVersion)
                .join(
                    PlanPolicyVersion,
                    PlanPolicyVersion.id == WorkspacePlanPolicyAssignment.policy_version_id,
                )
                .where(
                    WorkspacePlanPolicyAssignment.workspace_id == workspace.id,
                    WorkspacePlanPolicyAssignment.plan_id == plan.id,
                    PlanPolicyVersion.effective_at <= effective_at,
                )
                .order_by(
                    PlanPolicyVersion.version.desc(),
                    WorkspacePlanPolicyAssignment.assigned_at.desc(),
                )
            )
        ).first()
        current_assignment = current_row[0] if current_row is not None else None
        selected = current_row[1] if current_row is not None else None

        candidates = list(
            (
                await self.db.scalars(
                    select(PlanPolicyVersion)
                    .where(
                        PlanPolicyVersion.plan_id == plan.id,
                        PlanPolicyVersion.effective_at <= effective_at,
                    )
                    .order_by(PlanPolicyVersion.version.desc())
                )
            ).all()
        )
        for candidate in candidates:
            if selected is not None and candidate.version <= selected.version:
                break
            if self._workspace_matches_policy(
                workspace=workspace,
                subscription=subscription,
                policy=candidate,
            ):
                selected = candidate
                current_assignment = None
                break

        assignment_created = False
        if selected is not None and current_assignment is None and materialize_assignment:
            existing = await self.db.scalar(
                select(WorkspacePlanPolicyAssignment).where(
                    WorkspacePlanPolicyAssignment.workspace_id == workspace.id,
                    WorkspacePlanPolicyAssignment.policy_version_id == selected.id,
                )
            )
            if existing is None:
                existing = WorkspacePlanPolicyAssignment(
                    workspace_id=workspace.id,
                    plan_id=plan.id,
                    policy_version_id=selected.id,
                    assignment_reason=assignment_reason,
                    assigned_by_user_id=actor_user_id,
                    assigned_at=self.now(),
                )
                self.db.add(existing)
                await self.db.flush()
                assignment_created = True
                if audit_assignment:
                    self._add_audit(
                        event_type="workspace_assigned",
                        plan_id=plan.id,
                        policy_version_id=selected.id,
                        workspace_id=workspace.id,
                        actor_user_id=actor_user_id,
                        event_data={
                            "version": selected.version,
                            "assignmentReason": assignment_reason,
                            "effectiveAt": effective_at.isoformat(),
                        },
                    )
            current_assignment = existing

        if selected is None and not fallback_to_legacy:
            await self.db.flush()
            return None, assignment_created

        if selected is not None:
            snapshot = self._parse_snapshot(selected.snapshot)
            source = "plan_policy_version"
            snapshot_hash = selected.snapshot_hash
        else:
            snapshot = await self._legacy_snapshot(plan)
            source = "legacy_plan_entitlement"
            snapshot_hash = _json_hash(self._snapshot_payload(snapshot))

        entitlements = {
            key: ResolvedPlanEntitlementRead(
                key=key,
                enabled=setting.enabled,
                limit_quantity=setting.limit_quantity,
                hard_limit=setting.hard_limit,
                source=source,
            )
            for key, setting in snapshot.entitlements.items()
        }
        await self.db.flush()
        return (
            WorkspacePlanPolicyResolutionRead(
                workspace_id=workspace.id,
                plan_id=plan.id,
                policy_version_id=selected.id if selected is not None else None,
                policy_version=selected.version if selected is not None else None,
                assignment_id=(current_assignment.id if current_assignment is not None else None),
                snapshot_hash=snapshot_hash,
                source=source,
                parameters=snapshot.parameters,
                entitlements=entitlements,
            ),
            assignment_created,
        )

    def _workspace_matches_policy(
        self,
        *,
        workspace: Workspace,
        subscription: Subscription,
        policy: PlanPolicyVersion,
    ) -> bool:
        if policy.rollout_mode in {
            PlanPolicyRolloutMode.existing_and_new,
            PlanPolicyRolloutMode.scheduled,
        }:
            return True
        if policy.rollout_mode is PlanPolicyRolloutMode.new_only:
            return _utc(workspace.created_at) >= _utc(policy.effective_at)
        if policy.rollout_mode is not PlanPolicyRolloutMode.rule:
            return False
        if policy.rollout_rule is None:
            raise PlanPolicyValidationError("Versão rule sem rolloutRule persistida.")
        try:
            rule = PlanPolicyRolloutRule.model_validate(policy.rollout_rule)
        except ValidationError as error:
            raise PlanPolicyValidationError("A regra persistida da política é inválida.") from error
        if rule.workspace_ids and workspace.id not in rule.workspace_ids:
            return False
        if workspace.id in rule.exclude_workspace_ids:
            return False
        if rule.workspace_statuses and workspace.status not in rule.workspace_statuses:
            return False
        if rule.setup_modes and workspace.setup_mode not in rule.setup_modes:
            return False
        if rule.subscription_statuses and subscription.status not in rule.subscription_statuses:
            return False
        workspace_created_at = _utc(workspace.created_at)
        if (
            rule.workspace_created_from is not None
            and workspace_created_at < rule.workspace_created_from
        ):
            return False
        if (
            rule.workspace_created_before is not None
            and workspace_created_at >= rule.workspace_created_before
        ):
            return False
        if (
            rule.workspace_is_active is not None
            and workspace.is_active is not rule.workspace_is_active
        ):
            return False
        return True

    def _target_account_statement(
        self,
        *,
        plan_id: UUID,
        rollout_mode: PlanPolicyRolloutMode,
        effective_at: datetime,
        rule: PlanPolicyRolloutRule | None,
    ):
        statement = (
            select(Workspace, Subscription)
            .join(Subscription, Subscription.workspace_id == Workspace.id)
            .where(
                Subscription.plan_id == plan_id,
                Workspace.deleted_at.is_(None),
            )
        )
        if rollout_mode is PlanPolicyRolloutMode.new_only:
            statement = statement.where(Workspace.created_at >= effective_at)
        elif rollout_mode is PlanPolicyRolloutMode.rule:
            if rule is None:
                raise PlanPolicyValidationError("Rollout rule exige critérios.")
            if rule.workspace_ids:
                statement = statement.where(Workspace.id.in_(rule.workspace_ids))
            if rule.exclude_workspace_ids:
                statement = statement.where(Workspace.id.not_in(rule.exclude_workspace_ids))
            if rule.workspace_statuses:
                statement = statement.where(Workspace.status.in_(rule.workspace_statuses))
            if rule.setup_modes:
                statement = statement.where(Workspace.setup_mode.in_(rule.setup_modes))
            if rule.subscription_statuses:
                statement = statement.where(Subscription.status.in_(rule.subscription_statuses))
            if rule.workspace_created_from is not None:
                statement = statement.where(Workspace.created_at >= rule.workspace_created_from)
            if rule.workspace_created_before is not None:
                statement = statement.where(Workspace.created_at < rule.workspace_created_before)
            if rule.workspace_is_active is not None:
                statement = statement.where(Workspace.is_active.is_(rule.workspace_is_active))
        return statement.order_by(Workspace.created_at.asc(), Workspace.id.asc())

    async def _publication_snapshot(
        self,
        *,
        plan_id: UUID,
        payload: PlanPolicyPublishRequest,
    ) -> PlanPolicySnapshot:
        if payload.snapshot is not None:
            return payload.snapshot
        draft = await self.db.scalar(
            select(PlanPolicyDraft).where(PlanPolicyDraft.plan_id == plan_id).with_for_update()
        )
        if draft is None:
            raise PlanPolicyNotFoundError("Não existe rascunho para publicar.")
        if _normalize_etag(payload.draft_etag) != draft.etag:
            raise PlanPolicyConflictError("draftEtag não corresponde ao rascunho atual.")
        return self._parse_snapshot(draft.snapshot)

    async def _legacy_snapshot(self, plan: Plan) -> PlanPolicySnapshot:
        rows = list(
            (
                await self.db.scalars(
                    select(PlanEntitlement).where(PlanEntitlement.plan_id == plan.id)
                )
            ).all()
        )
        typed = {row.entitlement: row for row in rows}
        features: Mapping[str, Any] = plan.features or {}
        entitlements: dict[EntitlementKey, PlanPolicyEntitlementSetting] = {}
        for key, definition in ENTITLEMENT_CATALOG.items():
            configured = typed.get(key)
            if configured is not None:
                entitlements[key] = PlanPolicyEntitlementSetting(
                    enabled=configured.enabled,
                    limit_quantity=configured.limit_quantity,
                    hard_limit=configured.hard_limit,
                )
                continue
            if definition.legacy_plan_attribute is not None:
                entitlements[key] = PlanPolicyEntitlementSetting(
                    enabled=True,
                    limit_quantity=getattr(plan, definition.legacy_plan_attribute),
                    hard_limit=True,
                )
                continue
            flag_found = False
            for feature_key in definition.legacy_feature_flag_keys:
                if feature_key in features:
                    entitlements[key] = PlanPolicyEntitlementSetting(
                        enabled=bool(features[feature_key]),
                        limit_quantity=None,
                        hard_limit=True,
                    )
                    flag_found = True
                    break
            if flag_found:
                continue
            limit_found = False
            for feature_key in definition.legacy_feature_keys:
                if feature_key in features:
                    raw_limit = features[feature_key]
                    entitlements[key] = PlanPolicyEntitlementSetting(
                        enabled=True,
                        limit_quantity=raw_limit,
                        hard_limit=True,
                    )
                    limit_found = True
                    break
            if limit_found:
                continue
            entitlements[key] = PlanPolicyEntitlementSetting(
                enabled=False,
                limit_quantity=None,
                hard_limit=True,
            )

        parameters = PlanPolicyParameters(
            max_matters=plan.max_matters,
            max_workspaces=features.get("maxWorkspaces"),
            max_files_per_conversation=features.get("maxFilesPerConversation"),
            max_operational_recipients=features.get("maxOperationalRecipients"),
            max_campaign_recipients_per_month=features.get(
                "maxCampaignRecipientsPerMonth"
            ),
        )
        try:
            return PlanPolicySnapshot(
                name=plan.name,
                description=plan.description,
                is_active=plan.is_active,
                is_public=plan.is_public,
                recommended=bool(features.get("recommended", False)),
                sort_order=plan.sort_order,
                currency="BRL",
                price_monthly_cents=plan.price_monthly_cents,
                price_annually_cents=plan.price_annually_cents,
                support_tier=str(features.get("supportTier", "standard")),
                integrations=list(features.get("integrations") or []),
                highlights=list(features.get("highlights") or []),
                parameters=parameters,
                entitlements=entitlements,
            )
        except (TypeError, ValidationError, ValueError) as error:
            raise PlanPolicyValidationError(
                "O catálogo legado do plano não forma um snapshot integral válido."
            ) from error

    async def _get_plan(self, plan_id: UUID, *, lock: bool = False) -> Plan:
        statement = select(Plan).where(Plan.id == plan_id)
        if lock:
            statement = statement.with_for_update()
        plan = await self.db.scalar(statement)
        if plan is None:
            raise PlanPolicyNotFoundError("Plano não encontrado.")
        return plan

    async def _latest_version(self, plan_id: UUID) -> PlanPolicyVersion | None:
        return await self.db.scalar(
            select(PlanPolicyVersion)
            .where(PlanPolicyVersion.plan_id == plan_id)
            .order_by(PlanPolicyVersion.version.desc())
            .limit(1)
        )

    @staticmethod
    def _snapshot_payload(snapshot: PlanPolicySnapshot) -> dict[str, Any]:
        return _json_copy(snapshot.model_dump(mode="json", by_alias=True))

    @staticmethod
    def _parse_snapshot(value: Mapping[str, Any]) -> PlanPolicySnapshot:
        try:
            return PlanPolicySnapshot.model_validate(value)
        except ValidationError as error:
            raise PlanPolicyValidationError(
                "O snapshot persistido da política de plano é inválido."
            ) from error

    @staticmethod
    def _validate_idempotency_key(value: str) -> str:
        normalized = value.strip()
        if not 8 <= len(normalized) <= 200 or any(
            not character.isprintable() for character in normalized
        ):
            raise PlanPolicyValidationError(
                "Idempotency-Key deve conter entre 8 e 200 caracteres imprimíveis."
            )
        return normalized

    def _add_audit(
        self,
        *,
        event_type: str,
        plan_id: UUID,
        event_data: Mapping[str, Any],
        policy_version_id: UUID | None = None,
        workspace_id: UUID | None = None,
        actor_user_id: UUID | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        self.db.add(
            PlanPolicyAuditEvent(
                event_type=event_type,
                plan_id=plan_id,
                policy_version_id=policy_version_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                event_data=_json_copy(dict(event_data)),
                occurred_at=self.now(),
            )
        )


__all__ = [
    "PlanPolicyConflictError",
    "PlanPolicyError",
    "PlanPolicyNotFoundError",
    "PlanPolicyPublicationResult",
    "PlanPolicyService",
    "PlanPolicyValidationError",
]
