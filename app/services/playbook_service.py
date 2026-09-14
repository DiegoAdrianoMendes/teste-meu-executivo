"""Domain service for governed, executable and locally testable playbooks."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_cost_privacy import strip_internal_provider_cost_fields
from app.models.configuration import ConfigDocument, ConfigDocumentVersion, ConfigScope
from app.models.playbook import (
    PlaybookLifecycleState,
    PlaybookScopeBinding,
    PlaybookScopeDimension,
    PlaybookSimulationRun,
    PlaybookSimulationStatus,
    PlaybookVersionGovernance,
)
from app.models.storage_object import StorageObject, StorageObjectState
from app.schemas.playbook import (
    InternalPlaybookContent,
    InternalPlaybookDocumentPayload,
    PlaybookCreate,
    PlaybookDetailRead,
    PlaybookDiffEntryRead,
    PlaybookDiffRead,
    PlaybookDraftWrite,
    PlaybookMetricsRead,
    PlaybookOfficeTemplate,
    PlaybookOfficeTemplateKey,
    PlaybookScope,
    PlaybookSimulationRead,
    PlaybookSimulationWrite,
    PlaybookSummaryRead,
    PlaybookVersionRead,
)
from app.services.configuration_service import (
    ConfigConflictError,
    ConfigurationService,
    ConfigValidationError,
)
from app.services.playbook_defaults import default_playbook_content

PLAYBOOK_NAMESPACE_PREFIX = "sales.playbook."
DEFAULT_HEALTH_PLAYBOOK_NAMESPACE = f"{PLAYBOOK_NAMESPACE_PREFIX}baseline.health-denials"
HEALTH_BASELINE_REVISION_NONCE = "system-health-baseline-v3-studio-library"

_STATE_SEQUENCE = {
    PlaybookLifecycleState.draft: 1,
    PlaybookLifecycleState.in_review: 2,
    PlaybookLifecycleState.approved: 3,
    PlaybookLifecycleState.published: 4,
    PlaybookLifecycleState.superseded: 5,
}
_TRANSITIONS = {
    PlaybookLifecycleState.draft: PlaybookLifecycleState.in_review,
    PlaybookLifecycleState.in_review: PlaybookLifecycleState.approved,
    PlaybookLifecycleState.approved: PlaybookLifecycleState.published,
}
_SCOPE_FIELDS = {
    "areas": PlaybookScopeDimension.area,
    "channels": PlaybookScopeDimension.channel,
    "stages": PlaybookScopeDimension.stage,
    "agents": PlaybookScopeDimension.agent,
    "offers": PlaybookScopeDimension.offer,
}


class PlaybookNotFoundError(LookupError):
    """Raised when a tenant cannot see the requested playbook or version."""


class PlaybookConflictError(RuntimeError):
    """Raised for an ETag or lifecycle concurrency conflict."""


class PlaybookTransitionError(RuntimeError):
    """Raised when a governance transition is not currently permitted."""


class PlaybookValidationError(ValueError):
    """Raised when a playbook cannot satisfy an executable contract."""


class PlaybookOfficeTemplateMissingError(PlaybookValidationError):
    """Raised fail-closed when document generation needs a firm office template
    that has not been uploaded yet. Callers must never fall back to a generic
    template — the office template is the exclusive source for that document
    type, so this always blocks generation instead of silently degrading."""


@dataclass(frozen=True)
class _VersionGovernance:
    state: PlaybookLifecycleState
    events: dict[PlaybookLifecycleState, PlaybookVersionGovernance]


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _scope_value(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_.-]+", "_", _normalize(value)).strip("_")
    if not normalized:
        raise PlaybookValidationError("Playbook scope contains an invalid empty value.")
    return normalized[:160]


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", _normalize(value)).strip("-")
    return (normalized or "playbook")[:140]


def _normalize_etag(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:].strip()
    return normalized.strip('"')


def _stored_revision_nonce(payload: Any) -> str:
    """Read the revision marker without trusting a legacy payload's full schema."""

    if not isinstance(payload, Mapping):
        return ""
    value = payload.get("revisionNonce", payload.get("revision_nonce", ""))
    return str(value or "").strip()


def structural_coverage(payload: InternalPlaybookDocumentPayload) -> float:
    """Return coverage of the mandatory executable playbook blocks."""

    content = payload.content
    checks = [
        bool(
            content.identity_voice.positioning
            and content.identity_voice.tone
            and content.identity_voice.allowed_claims
            and content.identity_voice.forbidden_claims
        ),
        bool(
            content.audience_intent.personas
            and content.audience_intent.pains
            and content.audience_intent.eligibility_signals
            and content.audience_intent.exclusion_signals
        ),
        bool(content.discovery and all(question.confirmation for question in content.discovery)),
        bool(
            content.legal_triage.subthemes
            and content.legal_triage.required_documents
            and content.legal_triage.legal_consult_triggers
            and content.legal_triage.sufficiency_rule
        ),
        bool(
            content.qualification.score_rules
            and content.qualification.required_fields
            and content.qualification.next_action_rules
        ),
        bool(
            content.commercial.minimum_initial_fee_brl >= 4000
            and content.commercial.success_only_rule
            and content.commercial.obligation_to_do_fee_rule
            and content.commercial.maximum_initial_fee_brl is None
        ),
        bool(
            len(content.objections) >= 14
            and all(
                objection.diagnosis
                and objection.confirmation_question
                and objection.allowed_response
                and objection.proof
                and objection.escalation_criteria
                for objection in content.objections
            )
        ),
        bool(
            content.proposals.desktop_template
            and content.proposals.whatsapp_template
            and len(content.proposals.required_blocks) >= 5
            and content.proposals.analytics_events
        ),
        bool(
            content.brand_kit.brand_name
            and content.brand_kit.primary_color
            and content.brand_kit.secondary_color
            and content.brand_kit.heading_font
            and content.brand_kit.body_font
            and content.brand_kit.letterhead_header
            and content.brand_kit.letterhead_footer
        ),
        bool(
            len(content.document_templates) >= 7
            and {
                "proposal",
                "contract",
                "power_of_attorney",
                "petition",
            }.issubset(
                {
                    template.document_type
                    for template in content.document_templates
                    if template.active and template.status == "approved"
                }
            )
            and all(template.sections for template in content.document_templates if template.active)
        ),
        bool(
            len(content.faqs) >= 10
            and all(
                faq.question and faq.approved_answer and faq.keywords and faq.source_refs
                for faq in content.faqs
                if faq.active
            )
        ),
        bool(
            len(content.response_templates) >= 7
            and all(
                template.body and template.intent and template.agent_keys
                for template in content.response_templates
                if template.active
            )
        ),
        bool(
            len(content.document_variables) >= 25
            and all(
                variable.key and variable.source_path for variable in content.document_variables
            )
        ),
        bool(
            content.follow_up.lead_never_unanswered
            and len(content.follow_up.cadences) >= 6
            and all(steps for steps in content.follow_up.cadences.values())
        ),
        bool(
            content.handoff.normal_dispatcher == "orquestrador"
            and content.handoff.fallback_queue
            and content.handoff.required_summary_fields
            and content.handoff.destinations
        ),
        bool(len(content.post_sale) >= 5),
        bool(
            content.experiments
            and all(
                experiment.metric and experiment.stop_criteria for experiment in content.experiments
            )
        ),
        bool(
            len(content.agents) >= 16
            and all(
                agent.objective
                and agent.inputs
                and agent.tools
                and agent.permissions
                and agent.success_criteria
                and agent.prohibitions
                and agent.fallback
                for agent in content.agents
            )
            and any(
                agent.key == "secretaria" and agent.handoffs == ["hermes"]
                for agent in content.agents
            )
            and any(
                agent.key == "orquestrador" and "fila_humana" in agent.handoffs
                for agent in content.agents
            )
            and any(
                agent.key == "hermes" and agent.handoffs == []
                for agent in content.agents
            )
            and content.policy.hard_gates
            and content.policy.stage_modes
        ),
    ]
    return round(sum(checks) * 100 / len(checks), 2)


class PlaybookService:
    """Own lifecycle, resolution, simulation and attribution for workspace playbooks."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.config = ConfigurationService(db)

    async def _document(
        self,
        workspace_id: UUID,
        document_id: UUID,
        *,
        lock: bool = False,
    ) -> ConfigDocument:
        query = select(ConfigDocument).where(
            ConfigDocument.id == document_id,
            ConfigDocument.workspace_id == workspace_id,
            ConfigDocument.namespace.startswith(PLAYBOOK_NAMESPACE_PREFIX),
        )
        if lock:
            query = query.with_for_update()
        document = await self.db.scalar(query)
        if document is None:
            raise PlaybookNotFoundError("Playbook not found in this workspace.")
        return document

    async def _version(
        self,
        document: ConfigDocument,
        version: int,
    ) -> ConfigDocumentVersion:
        row = await self.db.scalar(
            select(ConfigDocumentVersion).where(
                ConfigDocumentVersion.document_id == document.id,
                ConfigDocumentVersion.version == version,
            )
        )
        if row is None:
            raise PlaybookNotFoundError("Playbook version not found.")
        return row

    async def _governance(
        self,
        document_id: UUID,
    ) -> dict[int, _VersionGovernance]:
        rows = list(
            (
                await self.db.scalars(
                    select(PlaybookVersionGovernance)
                    .where(PlaybookVersionGovernance.document_id == document_id)
                    .order_by(
                        PlaybookVersionGovernance.version,
                        PlaybookVersionGovernance.sequence,
                    )
                )
            ).all()
        )
        grouped: dict[int, dict[PlaybookLifecycleState, PlaybookVersionGovernance]] = defaultdict(
            dict
        )
        for row in rows:
            grouped[row.version][row.state] = row
        return {
            version: _VersionGovernance(
                state=max(events.values(), key=lambda event: event.sequence).state,
                events=events,
            )
            for version, events in grouped.items()
        }

    async def _current_state(
        self,
        document: ConfigDocument,
    ) -> PlaybookLifecycleState:
        event = await self.db.scalar(
            select(PlaybookVersionGovernance)
            .where(
                PlaybookVersionGovernance.document_id == document.id,
                PlaybookVersionGovernance.version == document.current_version,
            )
            .order_by(PlaybookVersionGovernance.sequence.desc())
            .limit(1)
        )
        if event is None:
            raise PlaybookValidationError("Playbook governance metadata is missing.")
        return event.state

    async def _add_event(
        self,
        *,
        document: ConfigDocument,
        version_row: ConfigDocumentVersion,
        state: PlaybookLifecycleState,
        reason: str,
        actor_user_id: UUID | None,
    ) -> PlaybookVersionGovernance:
        event = PlaybookVersionGovernance(
            workspace_id=document.workspace_id,
            document_id=document.id,
            document_version_id=version_row.id,
            version=version_row.version,
            sequence=_STATE_SEQUENCE[state],
            state=state,
            reason=reason.strip()[:500],
            actor_user_id=actor_user_id,
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def _add_bindings(
        self,
        document: ConfigDocument,
        version_row: ConfigDocumentVersion,
        payload: InternalPlaybookDocumentPayload,
    ) -> None:
        for field_name, dimension in _SCOPE_FIELDS.items():
            for raw_value in getattr(payload.scope, field_name):
                self.db.add(
                    PlaybookScopeBinding(
                        workspace_id=document.workspace_id,
                        document_id=document.id,
                        document_version_id=version_row.id,
                        version=version_row.version,
                        dimension=dimension,
                        value=_scope_value(raw_value),
                    )
                )
        await self.db.flush()

    async def _validate_storage_references(
        self,
        *,
        workspace_id: UUID,
        payload: InternalPlaybookDocumentPayload,
    ) -> None:
        references: set[tuple[UUID, int]] = set()
        brand = payload.content.brand_kit
        if brand.logo_object_id is not None and brand.logo_object_version is not None:
            references.add((brand.logo_object_id, brand.logo_object_version))
        for template in payload.content.document_templates:
            if template.source_object_id is not None and template.source_object_version is not None:
                references.add((template.source_object_id, template.source_object_version))
        for office_template in payload.content.office_templates:
            if (
                office_template.document_object_id is not None
                and office_template.document_object_version is not None
            ):
                references.add(
                    (office_template.document_object_id, office_template.document_object_version)
                )
        if not references:
            return

        rows = list(
            (
                await self.db.scalars(
                    select(StorageObject).where(
                        StorageObject.workspace_id == workspace_id,
                        tuple_(StorageObject.object_id, StorageObject.version).in_(references),
                        StorageObject.state == StorageObjectState.available,
                        StorageObject.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        available = {(row.object_id, row.version) for row in rows}
        missing = sorted(
            references - available,
            key=lambda reference: (str(reference[0]), reference[1]),
        )
        if missing:
            rendered = ", ".join(f"{object_id}@v{version}" for object_id, version in missing)
            raise PlaybookValidationError(
                "Playbook files must exist in this workspace, be available, cleanly "
                f"stored and version-pinned. Invalid references: {rendered}."
            )

    async def create(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        data: PlaybookCreate,
    ) -> PlaybookDetailRead:
        content = default_playbook_content(data.autonomy_mode)
        if data.clone_from_id is not None:
            source = await self._document(workspace_id, data.clone_from_id)
            source_payload = InternalPlaybookDocumentPayload.model_validate(source.payload)
            content = source_payload.content.model_copy(deep=True)
            content.policy.autonomy_mode = data.autonomy_mode
        unique = uuid4()
        payload = InternalPlaybookDocumentPayload(
            name=data.name.strip(),
            slug=f"{_slug(data.name)}-{unique.hex[:8]}",
            description=data.description.strip(),
            scope=data.scope,
            content=content,
            revision_nonce=unique.hex,
        )
        await self._validate_storage_references(
            workspace_id=workspace_id,
            payload=payload,
        )
        namespace = f"{PLAYBOOK_NAMESPACE_PREFIX}{unique.hex}"
        try:
            document = await self.config.write_tenant(
                workspace_id=workspace_id,
                scope=ConfigScope.workspace,
                namespace=namespace,
                payload=payload.model_dump(mode="json", by_alias=True),
                actor_user_id=actor_user_id,
                reason="Playbook draft created",
            )
        except (ConfigConflictError, ConfigValidationError) as exc:
            raise PlaybookValidationError(str(exc)) from exc
        version_row = await self._version(document, document.current_version)
        await self._add_bindings(document, version_row, payload)
        await self._add_event(
            document=document,
            version_row=version_row,
            state=PlaybookLifecycleState.draft,
            reason="Playbook draft created",
            actor_user_id=actor_user_id,
        )
        return await self.detail(workspace_id=workspace_id, document_id=document.id)

    async def ensure_health_baseline(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> PlaybookDetailRead:
        """Provision the governed wildcard fallback used by the local health operation."""

        baseline_payload = InternalPlaybookDocumentPayload(
            name="Baseline de negativas de plano de saúde",
            slug="baseline-negativas-plano-saude",
            description=(
                "Fallback governado para todos os canais, etapas, agentes e ofertas da "
                "operação de negativas de plano de saúde."
            ),
            scope=PlaybookScope(
                areas=["negativas_plano_saude"],
                channels=[],
                stages=[],
                agents=[],
                offers=[],
            ),
            content=default_playbook_content("ai_triage_human_meeting"),
            revision_nonce=HEALTH_BASELINE_REVISION_NONCE,
        )
        document = await self.db.scalar(
            select(ConfigDocument).where(
                ConfigDocument.workspace_id == workspace_id,
                ConfigDocument.namespace == DEFAULT_HEALTH_PLAYBOOK_NAMESPACE,
            )
        )
        if document is None:
            try:
                document = await self.config.write_tenant(
                    workspace_id=workspace_id,
                    scope=ConfigScope.workspace,
                    namespace=DEFAULT_HEALTH_PLAYBOOK_NAMESPACE,
                    payload=baseline_payload.model_dump(mode="json", by_alias=True),
                    actor_user_id=actor_user_id,
                    reason="System health playbook baseline created",
                )
            except (ConfigConflictError, ConfigValidationError) as exc:
                raise PlaybookValidationError(str(exc)) from exc
            version_row = await self._version(document, document.current_version)
            await self._add_bindings(document, version_row, baseline_payload)
            await self._add_event(
                document=document,
                version_row=version_row,
                state=PlaybookLifecycleState.draft,
                reason="System health playbook baseline created",
                actor_user_id=actor_user_id,
            )
        else:
            stored_revision_nonce = _stored_revision_nonce(document.payload)
            is_system_baseline = stored_revision_nonce.startswith("system-health-baseline-v")
            current_state = await self._current_state(document)
            if (
                is_system_baseline
                and stored_revision_nonce != HEALTH_BASELINE_REVISION_NONCE
                and current_state is PlaybookLifecycleState.published
            ):
                try:
                    document = await self.config.write_tenant(
                        workspace_id=workspace_id,
                        scope=ConfigScope.workspace,
                        namespace=DEFAULT_HEALTH_PLAYBOOK_NAMESPACE,
                        payload=baseline_payload.model_dump(mode="json", by_alias=True),
                        actor_user_id=actor_user_id,
                        expected_etag=document.etag,
                        reason="System health playbook baseline upgraded to Studio library",
                    )
                except ConfigConflictError as exc:
                    raise PlaybookConflictError(str(exc)) from exc
                except ConfigValidationError as exc:
                    raise PlaybookValidationError(str(exc)) from exc
                version_row = await self._version(document, document.current_version)
                await self._add_bindings(document, version_row, baseline_payload)
                await self._add_event(
                    document=document,
                    version_row=version_row,
                    state=PlaybookLifecycleState.draft,
                    reason=(
                        "Baseline atualizado com biblioteca de documentos, identidade "
                        "visual, FAQ, respostas aprovadas e variáveis versionadas."
                    ),
                    actor_user_id=actor_user_id,
                )
            else:
                # Tenant-authored and current system payloads are never normalized silently.
                # Their complete contract must validate or the operation fails closed.
                InternalPlaybookDocumentPayload.model_validate(document.payload)

        current = await self._current_state(document)
        if current is PlaybookLifecycleState.draft:
            await self.transition(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                document_id=document.id,
                target=PlaybookLifecycleState.in_review,
                reason="Baseline estrutural validado pelo bootstrap local.",
            )
            current = PlaybookLifecycleState.in_review
        if current is PlaybookLifecycleState.in_review:
            passing_simulations = await self.db.scalar(
                select(func.count(PlaybookSimulationRun.id)).where(
                    PlaybookSimulationRun.workspace_id == workspace_id,
                    PlaybookSimulationRun.document_id == document.id,
                    PlaybookSimulationRun.version == document.current_version,
                    PlaybookSimulationRun.status == PlaybookSimulationStatus.passed,
                )
            )
            if not passing_simulations:
                await self.simulate(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    document_id=document.id,
                    data=PlaybookSimulationWrite(
                        scenario="Bootstrap local de negativas de plano de saúde",
                        customer_message=(
                            "Meu plano negou um medicamento e preciso entender os próximos passos."
                        ),
                        context={"intent": "triagem", "source": "local_bootstrap"},
                        version=document.current_version,
                    ),
                )
            await self.transition(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                document_id=document.id,
                target=PlaybookLifecycleState.approved,
                reason="Simulação determinística local aprovada.",
            )
            current = PlaybookLifecycleState.approved
        if current is PlaybookLifecycleState.approved:
            await self.transition(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                document_id=document.id,
                target=PlaybookLifecycleState.published,
                reason="Baseline local publicado para resolução efetiva.",
            )
            current = PlaybookLifecycleState.published
        if current is not PlaybookLifecycleState.published:
            raise PlaybookTransitionError(
                f"Health baseline cannot be activated from {current.value}."
            )
        return await self.effective(
            workspace_id=workspace_id,
            selectors={
                PlaybookScopeDimension.area: "negativas_plano_saude",
                PlaybookScopeDimension.channel: None,
                PlaybookScopeDimension.stage: None,
                PlaybookScopeDimension.agent: None,
                PlaybookScopeDimension.offer: None,
            },
        )

    async def clone(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        document_id: UUID,
        name: str,
    ) -> PlaybookDetailRead:
        source = await self._document(workspace_id, document_id)
        payload = InternalPlaybookDocumentPayload.model_validate(source.payload)
        return await self.create(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            data=PlaybookCreate(
                name=name,
                description=payload.description,
                scope=payload.scope,
                autonomy_mode=payload.content.policy.autonomy_mode,
                clone_from_id=document_id,
            ),
        )

    async def save_draft(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        document_id: UUID,
        data: PlaybookDraftWrite,
        expected_etag: str | None,
    ) -> PlaybookDetailRead:
        document = await self._document(workspace_id, document_id, lock=True)
        if await self._current_state(document) is not PlaybookLifecycleState.draft:
            raise PlaybookTransitionError("Only a draft playbook can be edited.")
        body_etag = _normalize_etag(data.expected_etag)
        header_etag = _normalize_etag(expected_etag)
        if header_etag is None or body_etag != header_etag:
            raise PlaybookConflictError("If-Match must equal the draft ETag.")
        current_payload = InternalPlaybookDocumentPayload.model_validate(document.payload)
        content = InternalPlaybookContent.model_validate(
            data.content.model_dump(mode="json", by_alias=True)
        )
        content.policy.daily_budget_brl = current_payload.content.policy.daily_budget_brl
        payload = InternalPlaybookDocumentPayload(
            name=data.name.strip(),
            slug=current_payload.slug,
            description=data.description.strip(),
            scope=data.scope,
            content=content,
            revision_nonce=uuid4().hex,
        )
        await self._validate_storage_references(
            workspace_id=workspace_id,
            payload=payload,
        )
        previous_version = document.current_version
        previous_row = await self._version(document, previous_version)
        try:
            document = await self.config.write_tenant(
                workspace_id=workspace_id,
                scope=ConfigScope.workspace,
                namespace=document.namespace,
                payload=payload.model_dump(mode="json", by_alias=True),
                actor_user_id=actor_user_id,
                expected_etag=header_etag,
                reason=data.reason,
            )
        except ConfigConflictError as exc:
            raise PlaybookConflictError(str(exc)) from exc
        except ConfigValidationError as exc:
            raise PlaybookValidationError(str(exc)) from exc
        if document.current_version != previous_version:
            await self._add_event(
                document=document,
                version_row=previous_row,
                state=PlaybookLifecycleState.superseded,
                reason=f"Superseded by draft v{document.current_version}",
                actor_user_id=actor_user_id,
            )
            version_row = await self._version(document, document.current_version)
            await self._add_bindings(document, version_row, payload)
            await self._add_event(
                document=document,
                version_row=version_row,
                state=PlaybookLifecycleState.draft,
                reason=data.reason,
                actor_user_id=actor_user_id,
            )
        return await self.detail(workspace_id=workspace_id, document_id=document.id)

    async def transition(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        document_id: UUID,
        target: PlaybookLifecycleState,
        reason: str,
    ) -> PlaybookDetailRead:
        document = await self._document(workspace_id, document_id, lock=True)
        current = await self._current_state(document)
        if _TRANSITIONS.get(current) is not target:
            raise PlaybookTransitionError(
                f"Transition from {current.value} to {target.value} is not allowed."
            )
        version_row = await self._version(document, document.current_version)
        payload = InternalPlaybookDocumentPayload.model_validate(version_row.payload)
        coverage = structural_coverage(payload)
        if target is PlaybookLifecycleState.in_review and coverage < 95:
            raise PlaybookTransitionError(
                f"Structural coverage must be at least 95%; current coverage is {coverage:.0f}%."
            )
        if target is PlaybookLifecycleState.approved:
            passed = await self.db.scalar(
                select(func.count(PlaybookSimulationRun.id)).where(
                    PlaybookSimulationRun.workspace_id == workspace_id,
                    PlaybookSimulationRun.document_id == document.id,
                    PlaybookSimulationRun.version == document.current_version,
                    PlaybookSimulationRun.status == PlaybookSimulationStatus.passed,
                )
            )
            if not passed:
                raise PlaybookTransitionError(
                    "At least one passing local simulation is required before approval."
                )
        if target is PlaybookLifecycleState.published:
            await self._supersede_active_published(
                document=document,
                actor_user_id=actor_user_id,
                replacement_version=document.current_version,
            )
        await self._add_event(
            document=document,
            version_row=version_row,
            state=target,
            reason=reason,
            actor_user_id=actor_user_id,
        )
        return await self.detail(workspace_id=workspace_id, document_id=document.id)

    async def _supersede_active_published(
        self,
        *,
        document: ConfigDocument,
        actor_user_id: UUID,
        replacement_version: int,
    ) -> None:
        governance = await self._governance(document.id)
        for version, metadata in governance.items():
            if (
                version == replacement_version
                or metadata.state is not PlaybookLifecycleState.published
            ):
                continue
            version_row = await self._version(document, version)
            await self._add_event(
                document=document,
                version_row=version_row,
                state=PlaybookLifecycleState.superseded,
                reason=f"Superseded by published v{replacement_version}",
                actor_user_id=actor_user_id,
            )

    async def rollback(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        document_id: UUID,
        target_version: int,
        reason: str,
    ) -> PlaybookDetailRead:
        document = await self._document(workspace_id, document_id, lock=True)
        target_row = await self._version(document, target_version)
        target_payload = InternalPlaybookDocumentPayload.model_validate(target_row.payload)
        previous_version = document.current_version
        previous_state = await self._current_state(document)
        previous_row = await self._version(document, previous_version)
        restored = target_payload.model_copy(
            update={"revision_nonce": uuid4().hex},
            deep=True,
        )
        try:
            document = await self.config.write_tenant(
                workspace_id=workspace_id,
                scope=ConfigScope.workspace,
                namespace=document.namespace,
                payload=restored.model_dump(mode="json", by_alias=True),
                actor_user_id=actor_user_id,
                expected_etag=document.etag,
                reason=reason,
            )
        except ConfigConflictError as exc:
            raise PlaybookConflictError(str(exc)) from exc
        except ConfigValidationError as exc:
            raise PlaybookValidationError(str(exc)) from exc
        if previous_state is PlaybookLifecycleState.draft:
            await self._add_event(
                document=document,
                version_row=previous_row,
                state=PlaybookLifecycleState.superseded,
                reason=f"Superseded by restored draft v{document.current_version}",
                actor_user_id=actor_user_id,
            )
        version_row = await self._version(document, document.current_version)
        await self._add_bindings(document, version_row, restored)
        await self._add_event(
            document=document,
            version_row=version_row,
            state=PlaybookLifecycleState.draft,
            reason=reason,
            actor_user_id=actor_user_id,
        )
        return await self.detail(workspace_id=workspace_id, document_id=document.id)

    async def list_summaries(self, workspace_id: UUID) -> list[PlaybookSummaryRead]:
        documents = list(
            (
                await self.db.scalars(
                    select(ConfigDocument)
                    .where(
                        ConfigDocument.workspace_id == workspace_id,
                        ConfigDocument.namespace.startswith(PLAYBOOK_NAMESPACE_PREFIX),
                    )
                    .order_by(ConfigDocument.updated_at.desc(), ConfigDocument.id)
                )
            ).all()
        )
        results = []
        for document in documents:
            detail = await self._build_detail(document)
            results.append(PlaybookSummaryRead.model_validate(detail.model_dump()))
        return results

    async def detail(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
    ) -> PlaybookDetailRead:
        document = await self._document(workspace_id, document_id)
        return await self._build_detail(document)

    async def _build_detail(
        self,
        document: ConfigDocument,
        *,
        effective_version: int | None = None,
        effective_payload: InternalPlaybookDocumentPayload | None = None,
        effective_etag: str | None = None,
        state_override: PlaybookLifecycleState | None = None,
    ) -> PlaybookDetailRead:
        versions = list(
            (
                await self.db.scalars(
                    select(ConfigDocumentVersion)
                    .where(ConfigDocumentVersion.document_id == document.id)
                    .order_by(ConfigDocumentVersion.version.desc())
                )
            ).all()
        )
        governance = await self._governance(document.id)
        version_reads: list[PlaybookVersionRead] = []
        for row in versions:
            metadata = governance.get(row.version)
            if metadata is None:
                raise PlaybookValidationError(
                    f"Governance metadata is missing for version {row.version}."
                )
            events = metadata.events
            review = events.get(PlaybookLifecycleState.in_review)
            approval = events.get(PlaybookLifecycleState.approved)
            publication = events.get(PlaybookLifecycleState.published)
            latest = max(events.values(), key=lambda event: event.sequence)
            version_reads.append(
                PlaybookVersionRead(
                    id=row.id,
                    version=row.version,
                    state=metadata.state,
                    payload_hash=row.payload_hash,
                    reason=latest.reason or row.reason,
                    created_by_user_id=row.created_by_user_id,
                    reviewed_by_user_id=review.actor_user_id if review else None,
                    approved_by_user_id=approval.actor_user_id if approval else None,
                    published_by_user_id=publication.actor_user_id if publication else None,
                    created_at=row.created_at,
                    reviewed_at=review.created_at if review else None,
                    approved_at=approval.created_at if approval else None,
                    published_at=publication.created_at if publication else None,
                )
            )
        payload_version = effective_version or document.current_version
        payload = effective_payload or InternalPlaybookDocumentPayload.model_validate(
            document.payload
        )
        state = state_override or governance[document.current_version].state
        published_versions = [
            version
            for version, metadata in governance.items()
            if metadata.state is PlaybookLifecycleState.published
        ]
        simulations = list(
            (
                await self.db.scalars(
                    select(PlaybookSimulationRun).where(
                        PlaybookSimulationRun.workspace_id == document.workspace_id,
                        PlaybookSimulationRun.document_id == document.id,
                        PlaybookSimulationRun.version == payload_version,
                    )
                )
            ).all()
        )
        passed = sum(
            simulation.status is PlaybookSimulationStatus.passed for simulation in simulations
        )
        return PlaybookDetailRead(
            id=document.id,
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
            state=state,
            current_version=document.current_version,
            published_version=max(published_versions) if published_versions else None,
            etag=effective_etag or document.etag,
            scope=payload.scope,
            autonomy_mode=payload.content.policy.autonomy_mode,
            structural_coverage=structural_coverage(payload),
            simulation_pass_rate=round(100 * passed / len(simulations), 2) if simulations else 0,
            simulations=len(simulations),
            updated_at=document.updated_at,
            content=payload.content,
            versions=version_reads,
        )

    async def diff(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
        from_version: int,
        to_version: int,
    ) -> PlaybookDiffRead:
        document = await self._document(workspace_id, document_id)
        before = strip_internal_provider_cost_fields(
            (await self._version(document, from_version)).payload
        )
        after = strip_internal_provider_cost_fields(
            (await self._version(document, to_version)).payload
        )
        entries: list[PlaybookDiffEntryRead] = []
        self._collect_diff("$", before, after, entries)
        return PlaybookDiffRead(
            from_version=from_version,
            to_version=to_version,
            entries=entries,
        )

    @classmethod
    def _collect_diff(
        cls,
        path: str,
        before: Any,
        after: Any,
        entries: list[PlaybookDiffEntryRead],
    ) -> None:
        if isinstance(before, dict) and isinstance(after, dict):
            keys = sorted(set(before) | set(after))
            for key in keys:
                if key == "revisionNonce":
                    continue
                child_path = f"{path}.{key}"
                if key not in before:
                    entries.append(
                        PlaybookDiffEntryRead(
                            path=child_path,
                            before=None,
                            after=after[key],
                            change="added",
                        )
                    )
                elif key not in after:
                    entries.append(
                        PlaybookDiffEntryRead(
                            path=child_path,
                            before=before[key],
                            after=None,
                            change="removed",
                        )
                    )
                else:
                    cls._collect_diff(child_path, before[key], after[key], entries)
            return
        if before != after:
            entries.append(
                PlaybookDiffEntryRead(
                    path=path,
                    before=before,
                    after=after,
                    change="changed",
                )
            )

    async def simulate(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        document_id: UUID,
        data: PlaybookSimulationWrite,
    ) -> PlaybookSimulationRead:
        document = await self._document(workspace_id, document_id)
        version = data.version or document.current_version
        version_row = await self._version(document, version)
        payload = InternalPlaybookDocumentPayload.model_validate(version_row.payload)
        coverage = structural_coverage(payload)
        mode = payload.content.policy.autonomy_mode
        normalized_message = _normalize(f"{data.scenario} {data.customer_message}")
        normalized_gates = [_normalize(gate) for gate in payload.content.policy.hard_gates]
        matched_gates = [gate for gate in normalized_gates if gate in normalized_message]
        conversion_intent = _normalize(str(data.context.get("intent", ""))) in {
            "fechamento",
            "contratacao",
            "reuniao",
        }
        matched_faq = self._matching_faq(payload, normalized_message)
        human_required = (
            bool(matched_gates)
            or mode == "human_only"
            or (mode == "ai_triage_human_meeting" and conversion_intent)
            or bool(matched_faq and matched_faq.requires_human_review)
        )
        response = self._local_response(
            payload,
            normalized_message,
            faq=matched_faq,
        )
        next_action = (
            "Registrar o resumo no Orquestrador e encaminhar à Secretaria, "
            "que aciona o Hermes para aprovação humana."
            if human_required
            else (
                "Atualizar o Card com as evidências e executar somente a próxima "
                "ação reversível permitida."
            )
        )
        status = (
            PlaybookSimulationStatus.passed if coverage >= 95 else PlaybookSimulationStatus.failed
        )
        prompt_tokens = max(1, len(data.customer_message.split()) * 2 + 120)
        completion_tokens = max(1, len(response.split()) * 2)
        trace = [
            {
                "rule": "playbook.version_pin",
                "outcome": "applied",
                "reason": (
                    f"Execution pinned to v{version} and digest "
                    f"{version_row.payload_hash[:12]}."
                ),
            },
            {
                "rule": "context.tenant_case_boundary",
                "outcome": "applied",
                "reason": "Only Card/Case 360 and its linked omnichannel context are authorized.",
            },
            {
                "rule": "policy.autonomy_mode",
                "outcome": mode,
                "reason": "Stage-specific policy falls back to the versioned default mode.",
            },
            {
                "rule": "policy.hard_gates",
                "outcome": "human_required" if human_required else "clear",
                "reason": ", ".join(matched_gates)
                if matched_gates
                else "No irreversible or high-impact action is executed by the sandbox.",
            },
            {
                "rule": "sandbox.external_actions",
                "outcome": "blocked",
                "reason": "Local simulations execute zero external actions by database constraint.",
            },
        ]
        if matched_faq is not None:
            trace.append(
                {
                    "rule": "knowledge.faq",
                    "outcome": "matched",
                    "reason": (
                        f"Approved FAQ {matched_faq.id} matched the local scenario; "
                        "its answer and source references were version-pinned."
                    ),
                }
            )
        sources = [
            f"playbook:{document.id}:v{version}",
            f"digest:{version_row.payload_hash}",
            f"policy:{mode}",
            "context:card-case360",
            "context:omnichannel-thread",
        ]
        if matched_faq is not None:
            sources.append(f"faq:{matched_faq.id}")
            sources.extend(f"faq-source:{source}" for source in matched_faq.source_refs)
        run = PlaybookSimulationRun(
            workspace_id=workspace_id,
            document_id=document.id,
            document_version_id=version_row.id,
            version=version,
            content_digest=version_row.payload_hash,
            status=status,
            scenario=data.scenario,
            customer_message=data.customer_message,
            context=deepcopy(data.context),
            response=response,
            next_action=next_action,
            policy_trace=trace,
            sources=sources,
            autonomy_mode=mode,
            human_approval_required=human_required,
            external_actions_executed=0,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_brl=Decimal("0"),
            model_key="local-deterministic-v1",
            created_by_user_id=actor_user_id,
        )
        self.db.add(run)
        await self.db.flush()
        await self.db.refresh(run)
        return self._simulation_read(run)

    @staticmethod
    def _matching_faq(payload: InternalPlaybookDocumentPayload, normalized_message: str):
        message_tokens = set(normalized_message.split())
        matches: list[tuple[int, int, Any]] = []
        for faq in payload.content.faqs:
            if not faq.active:
                continue
            normalized_question = _normalize(faq.question)
            keyword_hits = sum(
                1 for keyword in faq.keywords if _normalize(keyword) in normalized_message
            )
            question_tokens = {token for token in normalized_question.split() if len(token) >= 4}
            token_hits = len(question_tokens & message_tokens)
            if keyword_hits or token_hits >= 2:
                matches.append((keyword_hits, token_hits, faq))
        if not matches:
            return None
        return max(matches, key=lambda item: (item[0], item[1]))[2]

    @staticmethod
    def _local_response(
        payload: InternalPlaybookDocumentPayload,
        normalized_message: str,
        *,
        faq=None,
    ) -> str:
        greeting = payload.content.identity_voice.greeting
        if faq is not None:
            response = f"{greeting} {faq.approved_answer}"
            return response[: payload.content.identity_voice.max_message_characters]
        if "medicamento" in normalized_message or "remedio" in normalized_message:
            body = (
                " Para organizar a análise, preciso da prescrição, do relatório médico, "
                "da negativa e do nome, dose, frequência e ciclo do medicamento. "
                "Se houver data médica para início, confirme também esse prazo."
            )
        elif "home care" in normalized_message:
            body = (
                " Para organizar a análise, preciso do relatório que descreve o Home Care, "
                "da prescrição, da negativa e da data prevista para alta ou início do cuidado."
            )
        elif "caro" in normalized_message or "valor" in normalized_message:
            body = (
                " Posso explicar o escopo e as condições autorizadas. A dificuldade principal "
                "é o valor inicial ou entender o que está incluído na atuação?"
            )
        else:
            body = (
                " Vamos confirmar o que foi negado, a prescrição, a urgência médica e os "
                "documentos disponíveis antes de definir o próximo passo."
            )
        response = (
            f"{greeting}{body} Não há garantia de resultado; "
            "cada caso depende da análise dos documentos."
        )
        return response[: payload.content.identity_voice.max_message_characters]

    async def simulations(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
    ) -> list[PlaybookSimulationRead]:
        document = await self._document(workspace_id, document_id)
        rows = list(
            (
                await self.db.scalars(
                    select(PlaybookSimulationRun)
                    .where(
                        PlaybookSimulationRun.workspace_id == workspace_id,
                        PlaybookSimulationRun.document_id == document.id,
                    )
                    .order_by(PlaybookSimulationRun.created_at.desc(), PlaybookSimulationRun.id)
                )
            ).all()
        )
        return [self._simulation_read(row) for row in rows]

    @staticmethod
    def _simulation_read(run: PlaybookSimulationRun) -> PlaybookSimulationRead:
        return PlaybookSimulationRead(
            id=run.id,
            playbook_id=run.document_id,
            version=run.version,
            status=run.status,
            scenario=run.scenario,
            customer_message=run.customer_message,
            response=run.response,
            next_action=run.next_action,
            policy_trace=run.policy_trace,
            sources=run.sources,
            autonomy_mode=run.autonomy_mode,
            human_approval_required=run.human_approval_required,
            external_actions_executed=run.external_actions_executed,
            prompt_tokens=run.prompt_tokens,
            completion_tokens=run.completion_tokens,
            created_at=run.created_at,
        )

    async def metrics(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
    ) -> PlaybookMetricsRead:
        document = await self._document(workspace_id, document_id)
        payload = InternalPlaybookDocumentPayload.model_validate(document.payload)
        runs = list(
            (
                await self.db.scalars(
                    select(PlaybookSimulationRun)
                    .where(
                        PlaybookSimulationRun.workspace_id == workspace_id,
                        PlaybookSimulationRun.document_id == document.id,
                        PlaybookSimulationRun.version == document.current_version,
                    )
                    .order_by(PlaybookSimulationRun.created_at.desc())
                )
            ).all()
        )
        count = len(runs)
        passed = sum(run.status is PlaybookSimulationStatus.passed for run in runs)
        handoffs = sum(run.human_approval_required for run in runs)
        autonomous = sum(
            run.status is PlaybookSimulationStatus.passed
            and not run.human_approval_required
            and run.autonomy_mode in {"ai_close_guarded", "ai_autonomous_supervised"}
            for run in runs
        )
        return PlaybookMetricsRead(
            simulations=count,
            pass_rate=round(100 * passed / count, 2) if count else 0,
            structural_coverage=structural_coverage(payload),
            handoff_rate=round(100 * handoffs / count, 2) if count else 0,
            autonomous_resolution_rate=round(100 * autonomous / count, 2) if count else 0,
            average_tokens=round(
                sum(run.prompt_tokens + run.completion_tokens for run in runs) / count,
                2,
            )
            if count
            else 0,
            objections_covered=len(payload.content.objections),
            agents_covered=len(payload.content.agents),
            last_evaluated_at=runs[0].created_at if runs else None,
        )

    async def effective(
        self,
        *,
        workspace_id: UUID,
        selectors: dict[PlaybookScopeDimension, str | None],
    ) -> PlaybookDetailRead:
        documents = list(
            (
                await self.db.scalars(
                    select(ConfigDocument).where(
                        ConfigDocument.workspace_id == workspace_id,
                        ConfigDocument.namespace.startswith(PLAYBOOK_NAMESPACE_PREFIX),
                    )
                )
            ).all()
        )
        bindings = list(
            (
                await self.db.scalars(
                    select(PlaybookScopeBinding).where(
                        PlaybookScopeBinding.workspace_id == workspace_id
                    )
                )
            ).all()
        )
        grouped: dict[
            tuple[UUID, int],
            dict[PlaybookScopeDimension, set[str]],
        ] = defaultdict(lambda: defaultdict(set))
        for binding in bindings:
            grouped[(binding.document_id, binding.version)][binding.dimension].add(binding.value)
        normalized_selectors = {
            dimension: _scope_value(value) if value else None
            for dimension, value in selectors.items()
        }
        candidates: list[
            tuple[int, int, float, str, ConfigDocument, int, ConfigDocumentVersion]
        ] = []
        for document in documents:
            governance = await self._governance(document.id)
            active = [
                version
                for version, metadata in governance.items()
                if metadata.state is PlaybookLifecycleState.published
            ]
            for version in active:
                values = grouped[(document.id, version)]
                exact = 0
                matched = True
                for dimension, selector in normalized_selectors.items():
                    if selector is None:
                        continue
                    configured = values.get(dimension, set())
                    if configured and selector not in configured:
                        matched = False
                        break
                    if selector in configured:
                        exact += 1
                if not matched:
                    continue
                breadth = sum(len(items) for items in values.values())
                publication = governance[version].events[PlaybookLifecycleState.published]
                version_row = await self._version(document, version)
                candidates.append(
                    (
                        exact,
                        -breadth,
                        publication.created_at.timestamp(),
                        str(document.id),
                        document,
                        version,
                        version_row,
                    )
                )
        if not candidates:
            raise PlaybookNotFoundError("No published playbook matches this scope.")
        _, _, _, _, document, version, version_row = max(
            candidates,
            key=lambda candidate: candidate[:4],
        )
        payload = InternalPlaybookDocumentPayload.model_validate(version_row.payload)
        return await self._build_detail(
            document,
            effective_version=version,
            effective_payload=payload,
            effective_etag=version_row.payload_hash,
            state_override=PlaybookLifecycleState.published,
        )

    async def resolve_office_template(
        self,
        *,
        workspace_id: UUID,
        key: PlaybookOfficeTemplateKey,
        selectors: dict[PlaybookScopeDimension, str | None] | None = None,
    ) -> PlaybookOfficeTemplate:
        """Fail-closed resolution of the firm's own letterhead template for
        `key`. Every document/contract generation flow MUST call this first
        and use the returned `document_object_id`/`document_object_version`
        as its base — never a generic built-in template. Raises
        `PlaybookOfficeTemplateMissingError` (never returns a placeholder)
        when no playbook is published for this scope, or the type is not
        configured, or the slot is still pending upload.
        """
        try:
            effective = await self.effective(
                workspace_id=workspace_id,
                selectors=selectors or {},
            )
        except PlaybookNotFoundError as exc:
            raise PlaybookOfficeTemplateMissingError(
                "Nenhum playbook publicado para este workspace/escopo. "
                "Envie o modelo timbrado no Playbook antes de gerar este documento."
            ) from exc

        by_key = {template.key: template for template in effective.content.office_templates}
        template = by_key.get(key)
        if template is None or template.document_object_id is None:
            label = template.label if template is not None else key
            raise PlaybookOfficeTemplateMissingError(
                f"Envie o modelo timbrado no Playbook para '{label}' antes de gerar este "
                "documento. A geração nunca usa um modelo genérico."
            )
        return template
