"""Transactional human approval workflow."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.klm_runtime import model_api_execution_allowed
from app.core.logging import logger
from app.db.advisory import acquire_transaction_advisory_lock
from app.db.post_commit import queue_agent_run_wakeup, queue_realtime_event
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.approval import (
    ApprovalDecision,
    ApprovalQueueItem,
    ApprovalStatus,
)
from app.models.klm_operations import LegalArtifactState, LegalArtifactVersion
from app.models.legal_artifact_verification import (
    CitationVerificationStatus,
    LegalCitationVerification,
    LegalReviewFinding,
    LegalReviewSeverity,
)
from app.models.matter import Matter, MicroStatus
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.schemas.approval import ApprovalCreate, ApprovalResolveRequest
from app.services.agent_runtime_service import AgentRuntimeService
from app.services.citation_verification_service import extract_citations
from app.services.entitlement_service import EntitlementEnforcementService

_REPROCESS_DECISIONS = frozenset(
    {ApprovalDecision.rejected, ApprovalDecision.changes_requested}
)
_FALLBACK_AGENT_BY_COLUMN = {
    "entrada": "klm_sdr",
    "proposta_fechamento": "closer_humano",
    "coleta_dados": "administrativo_ops",
}
_LEGAL_ARTIFACT_APPROVAL_PREFIX = "legal_artifact:"


def _enrich_with_client_name(items: list[ApprovalQueueItem]) -> list[ApprovalQueueItem]:
    """Attach the read-model client label expected by the existing API schema."""
    for item in items:
        if item.matter and item.matter.client:
            item.client_name = item.matter.client.name
        else:
            item.client_name = "—"
    return items


class ApprovalService:
    def __init__(self, db: AsyncSession, workspace_id: UUID):
        if workspace_id is None:
            raise ValueError("workspace_id is required")
        self.db = db
        self.workspace_id = workspace_id

    async def get_pending(self) -> list[ApprovalQueueItem]:
        result = await self.db.execute(
            select(ApprovalQueueItem)
            .options(selectinload(ApprovalQueueItem.matter).selectinload(Matter.client))
            .join(ApprovalQueueItem.matter)
            .where(
                ApprovalQueueItem.status == ApprovalStatus.pending,
                Matter.workspace_id == self.workspace_id,
                Matter.deleted_at.is_(None),
            )
            .order_by(ApprovalQueueItem.created_at.asc())
        )
        return _enrich_with_client_name(list(result.scalars().all()))

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 50,
    ) -> list[ApprovalQueueItem]:
        result = await self.db.execute(
            select(ApprovalQueueItem)
            .options(selectinload(ApprovalQueueItem.matter).selectinload(Matter.client))
            .join(ApprovalQueueItem.matter)
            .where(
                Matter.workspace_id == self.workspace_id,
                Matter.deleted_at.is_(None),
            )
            .order_by(ApprovalQueueItem.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return _enrich_with_client_name(list(result.scalars().all()))

    async def get_by_id(
        self,
        approval_id: UUID,
    ) -> ApprovalQueueItem | None:
        result = await self.db.execute(
            select(ApprovalQueueItem)
            .options(selectinload(ApprovalQueueItem.matter).selectinload(Matter.client))
            .join(ApprovalQueueItem.matter)
            .where(
                ApprovalQueueItem.id == approval_id,
                Matter.workspace_id == self.workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        item = result.scalar_one_or_none()
        if item:
            _enrich_with_client_name([item])
        return item

    async def create(
        self,
        data: ApprovalCreate,
    ) -> ApprovalQueueItem:
        """Create or replay one pending approval under the Matter aggregate lock."""
        intent_key = data.intent_key or f"type:{data.approval_type}"
        normalized = data.model_copy(update={"intent_key": intent_key})
        matter = await self._lock_matter(normalized.matter_id)
        existing = await self._get_pending_for_intent(
            matter_id=matter.id,
            approval_type=normalized.approval_type,
            intent_key=intent_key,
        )
        if existing is not None:
            self._assert_equivalent_create(existing, normalized)
            self._repair_pending_flag(matter)
            return await self._load_enriched(existing.id)

        item = ApprovalQueueItem(**normalized.model_dump())
        try:
            async with self.db.begin_nested():
                self.db.add(item)
                await self.db.flush()
        except IntegrityError:
            existing = await self._get_pending_for_intent(
                matter_id=matter.id,
                approval_type=normalized.approval_type,
                intent_key=intent_key,
            )
            if existing is None:
                raise
            self._assert_equivalent_create(existing, normalized)
            self._repair_pending_flag(matter)
            return await self._load_enriched(existing.id)

        self._repair_pending_flag(matter)
        self.db.add(
            TimelineEvent(
                matter_id=matter.id,
                agent_id=normalized.requested_by,
                event_type=TimelineEventType.approval_requested,
                title=f"Aprovação solicitada: {normalized.title}",
                event_data={
                    "approval_id": str(item.id),
                    "type": normalized.approval_type,
                    "intent_key": intent_key,
                },
            )
        )
        await self.db.flush()
        queue_realtime_event(
            self.db,
            workspace_id=matter.workspace_id,
            event_type="APPROVAL_REQUESTED",
            data={"approval_id": str(item.id), "matter_id": str(matter.id)},
        )

        logger.info(
            "approval_created",
            approval_id=str(item.id),
            matter_id=str(matter.id),
        )
        return await self._load_enriched(item.id)

    async def resolve(
        self,
        approval_id: UUID,
        resolution: ApprovalResolveRequest,
    ) -> ApprovalQueueItem:
        """Resolve exactly once and persist every resulting state in one transaction."""
        matter_id = await self.db.scalar(
            select(ApprovalQueueItem.matter_id)
            .join(Matter, Matter.id == ApprovalQueueItem.matter_id)
            .where(
                ApprovalQueueItem.id == approval_id,
                Matter.workspace_id == self.workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if matter_id is None:
            raise self._not_found()

        await acquire_transaction_advisory_lock(
            self.db,
            namespace="approval-matter",
            value=str(matter_id),
        )
        if resolution.status in _REPROCESS_DECISIONS:
            # AgentRuntimeService uses workspace -> matter lock order. Acquire the
            # workspace lock first here as well so approval reprocessing cannot deadlock it.
            await EntitlementEnforcementService(self.db).lock_workspace(
                self.workspace_id
            )
        matter = await self._select_locked_matter(matter_id)
        if matter is None:
            raise self._not_found()

        item = await self.db.scalar(
            select(ApprovalQueueItem)
            .where(
                ApprovalQueueItem.id == approval_id,
                ApprovalQueueItem.matter_id == matter.id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if item is None:
            raise self._not_found()
        if item.status != ApprovalStatus.pending:
            raise self._already_resolved(item)

        decided_at = datetime.now(UTC)
        decided_status = ApprovalStatus(resolution.status.value)
        if decided_status == ApprovalStatus.approved:
            await self._require_legal_artifact_protocolable(item)
        cas_result = await self.db.execute(
            update(ApprovalQueueItem)
            .where(
                ApprovalQueueItem.id == item.id,
                ApprovalQueueItem.matter_id == matter.id,
                ApprovalQueueItem.status == ApprovalStatus.pending,
            )
            .values(
                status=decided_status,
                resolution_reason=resolution.resolution_reason,
                resolved_by=resolution.resolved_by,
                resolved_at=decided_at,
            )
            .returning(ApprovalQueueItem.id)
            .execution_options(synchronize_session=False)
        )
        if cas_result.scalar_one_or_none() is None:
            await self.db.refresh(item)
            raise self._already_resolved(item)
        await self.db.refresh(item)

        has_pending = bool(
            await self.db.scalar(
                select(
                    exists().where(
                        ApprovalQueueItem.matter_id == matter.id,
                        ApprovalQueueItem.status == ApprovalStatus.pending,
                    )
                )
            )
        )
        matter.needs_approval = has_pending
        if decided_status == ApprovalStatus.approved:
            matter.micro_status = (
                MicroStatus.revisao_interna
                if has_pending
                else MicroStatus.checklist_final
            )
        elif decided_status == ApprovalStatus.rejected:
            matter.micro_status = MicroStatus.ajustes_solicitados
        else:
            matter.micro_status = MicroStatus.revisao_interna
        matter.updated_at = decided_at

        artifact_states = list(
            (
                await self.db.scalars(
                    select(LegalArtifactState)
                    .where(
                        LegalArtifactState.workspace_id == self.workspace_id,
                        LegalArtifactState.approval_item_id == item.id,
                    )
                    .with_for_update()
                )
            ).all()
        )
        for artifact_state in artifact_states:
            artifact_state.status = decided_status.value
            artifact_state.updated_at = decided_at

        self.db.add(
            TimelineEvent(
                matter_id=matter.id,
                agent_id=resolution.resolved_by,
                event_type=TimelineEventType.approval_resolved,
                title=f"Aprovação {decided_status.value}: {item.title[:60]}",
                description=resolution.resolution_reason,
                event_data={
                    "approval_id": str(item.id),
                    "status": decided_status.value,
                    "reason": resolution.resolution_reason,
                },
            )
        )

        reprocess_run = None
        if resolution.status in _REPROCESS_DECISIONS:
            reprocess_run = await self._ensure_reprocess_agent_run(
                item=item,
                matter=matter,
                decision=resolution.status,
                reason=resolution.resolution_reason,
                resolved_by=resolution.resolved_by,
            )

        await self.db.flush()
        queue_realtime_event(
            self.db,
            workspace_id=self.workspace_id,
            event_type="APPROVAL_RESOLVED",
            data={"approval_id": str(item.id), "status": decided_status.value},
        )
        if reprocess_run is not None and reprocess_run.status == AgentRunStatus.queued:
            queue_agent_run_wakeup(
                self.db,
                workspace_id=self.workspace_id,
                agent_run_id=reprocess_run.id,
                task_id=reprocess_run.celery_task_id,
            )

        logger.info(
            "approval_resolved",
            approval_id=str(item.id),
            status=decided_status.value,
        )
        return await self._load_enriched(item.id)

    async def _require_legal_artifact_protocolable(self, item: ApprovalQueueItem) -> None:
        """Fail-closed antes de aprovar uma versão jurídica (Onda 6, §9/§3
        item 20): citação não verificada ou achado crítico de revisão
        BLOQUEIAM 'aprovada' — a regra da casa é nunca protocolar precedente
        não conferido na fonte. Só se aplica a `legal_artifact:*`; qualquer
        outro tipo de aprovação segue o fluxo normal, sem overhead extra.
        """
        if not item.approval_type.startswith(_LEGAL_ARTIFACT_APPROVAL_PREFIX):
            return

        artifact_states = list(
            await self.db.scalars(
                select(LegalArtifactState).where(
                    LegalArtifactState.workspace_id == self.workspace_id,
                    LegalArtifactState.approval_item_id == item.id,
                )
            )
        )
        for artifact_state in artifact_states:
            artifact = await self.db.scalar(
                select(LegalArtifactVersion).where(
                    LegalArtifactVersion.id == artifact_state.artifact_version_id,
                    LegalArtifactVersion.workspace_id == self.workspace_id,
                )
            )
            if artifact is None:
                continue

            verified_texts = set(
                await self.db.scalars(
                    select(LegalCitationVerification.citation_text).where(
                        LegalCitationVerification.workspace_id == self.workspace_id,
                        LegalCitationVerification.artifact_version_id == artifact.id,
                        LegalCitationVerification.status
                        == CitationVerificationStatus.verificada.value,
                    )
                )
            )
            unverified = [
                candidate.text
                for candidate in extract_citations(artifact.content)
                if candidate.text not in verified_texts
            ]
            if unverified:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "legal_artifact_citation_unverified",
                        "message": (
                            "Aprovação bloqueada: citação(ões) sem conferência na fonte — "
                            + ", ".join(unverified[:5])
                        ),
                    },
                )

            has_critical_finding = bool(
                await self.db.scalar(
                    select(exists().where(
                        LegalReviewFinding.workspace_id == self.workspace_id,
                        LegalReviewFinding.artifact_version_id == artifact.id,
                        LegalReviewFinding.severity == LegalReviewSeverity.critica.value,
                    ))
                )
            )
            if has_critical_finding:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "legal_artifact_review_finding_open",
                        "message": (
                            "Aprovação bloqueada: revisão adversarial tem achado crítico aberto."
                        ),
                    },
                )

    async def _lock_matter(
        self,
        matter_id: UUID,
    ) -> Matter:
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="approval-matter",
            value=str(matter_id),
        )
        matter = await self._select_locked_matter(matter_id)
        if matter is None:
            raise self._not_found_matter()
        return matter

    async def _select_locked_matter(
        self,
        matter_id: UUID,
    ) -> Matter | None:
        return await self.db.scalar(
            select(Matter)
            .where(
                Matter.id == matter_id,
                Matter.workspace_id == self.workspace_id,
                Matter.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )

    async def _get_pending_for_intent(
        self,
        *,
        matter_id: UUID,
        approval_type: str,
        intent_key: str,
    ) -> ApprovalQueueItem | None:
        pending = list(
            (
                await self.db.scalars(
                    select(ApprovalQueueItem)
                    .where(
                        ApprovalQueueItem.matter_id == matter_id,
                        ApprovalQueueItem.approval_type == approval_type,
                        ApprovalQueueItem.intent_key == intent_key,
                        ApprovalQueueItem.status == ApprovalStatus.pending,
                    )
                    .order_by(ApprovalQueueItem.created_at, ApprovalQueueItem.id)
                    .with_for_update()
                )
            ).all()
        )
        if len(pending) > 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "approval_pending_intent_invariant_violation",
                    "message": (
                        "Há mais de uma aprovação pendente para o mesmo caso, "
                        "tipo e intenção."
                    ),
                    "matterId": str(matter_id),
                    "approvalType": approval_type,
                    "intentKey": intent_key,
                },
            )
        return pending[0] if pending else None

    @staticmethod
    def _assert_equivalent_create(
        existing: ApprovalQueueItem,
        data: ApprovalCreate,
    ) -> None:
        equivalent = (
            existing.matter_id == data.matter_id
            and existing.approval_type == data.approval_type
            and existing.intent_key == data.intent_key
            and existing.title == data.title
            and existing.description == data.description
            and existing.priority == data.priority
            and existing.requested_by == data.requested_by
            and existing.document_version_url == data.document_version_url
        )
        if equivalent:
            return
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "approval_pending_payload_conflict",
                "message": (
                    "Já existe uma aprovação pendente para este caso e tipo "
                    "e intenção com payload diferente."
                ),
                "approvalId": str(existing.id),
                "matterId": str(existing.matter_id),
                "approvalType": existing.approval_type,
                "intentKey": existing.intent_key,
            },
        )

    @staticmethod
    def _repair_pending_flag(matter: Matter) -> None:
        if not matter.needs_approval:
            matter.needs_approval = True
            matter.updated_at = datetime.now(UTC)

    async def _ensure_reprocess_agent_run(
        self,
        *,
        item: ApprovalQueueItem,
        matter: Matter,
        decision: ApprovalDecision,
        reason: str | None,
        resolved_by: UUID | None,
    ) -> AgentRun | None:
        latest_run = await self.db.scalar(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == matter.workspace_id,
                AgentRun.matter_id == matter.id,
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(1)
        )
        stage_key = str(getattr(matter.column_id, "value", matter.column_id))
        agent_name = (
            latest_run.agent_name
            if latest_run is not None
            else _FALLBACK_AGENT_BY_COLUMN.get(stage_key, "klm_sdr")
        )
        trigger_source = f"approval:{decision.value}"
        dedupe_key = f"approval:{item.id}:{decision.value}"
        input_payload = {
            "approval_id": str(item.id),
            "approval_status": decision.value,
            "approval_reason": reason,
            "reprocess_requested": True,
            "stage_key": stage_key,
            "model_api_execution_allowed_at_decision": model_api_execution_allowed(),
        }
        thread_id = f"{agent_name}-{matter.id}"
        try:
            run, created = await AgentRuntimeService(self.db).create_run_with_state(
                workspace_id=matter.workspace_id,
                matter_id=matter.id,
                agent_name=agent_name,
                trigger_source=trigger_source,
                triggered_by_user_id=resolved_by,
                thread_id=thread_id,
                input_payload=input_payload,
                dedupe_key=dedupe_key,
            )
        except ValueError as exc:
            if str(exc) != "deployment ativo não encontrado para este agente no workspace":
                raise
            logger.info(
                "approval_reprocess_agent_not_deployed",
                approval_id=str(item.id),
                workspace_id=str(matter.workspace_id),
                agent_name=agent_name,
                decision=decision.value,
            )
            return None
        if not created:
            expected_payload = {
                key: value
                for key, value in input_payload.items()
                if key != "model_api_execution_allowed_at_decision"
            }
            actual_payload = run.input_payload or {}
            payload_matches = all(
                actual_payload.get(key) == value
                for key, value in expected_payload.items()
            )
            if (
                run.matter_id != matter.id
                or run.agent_name != agent_name
                or run.trigger_source != trigger_source
                or run.triggered_by_user_id != resolved_by
                or run.thread_id != thread_id
                or not payload_matches
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "approval_reprocess_dedupe_conflict",
                        "message": (
                            "O reprocessamento desta decisão já existe com dados divergentes."
                        ),
                        "approvalId": str(item.id),
                        "agentRunId": str(run.id),
                    },
                )
        if run.status == AgentRunStatus.queued and not run.celery_task_id:
            run.celery_task_id = str(uuid4())
            run.updated_at = datetime.now(UTC)
        return run

    async def _load_enriched(self, approval_id: UUID) -> ApprovalQueueItem:
        item = await self.db.scalar(
            select(ApprovalQueueItem)
            .options(selectinload(ApprovalQueueItem.matter).selectinload(Matter.client))
            .join(ApprovalQueueItem.matter)
            .where(
                ApprovalQueueItem.id == approval_id,
                Matter.workspace_id == self.workspace_id,
                Matter.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if item is None:
            raise RuntimeError("Approval disappeared inside its transaction")
        return _enrich_with_client_name([item])[0]

    @staticmethod
    def _not_found() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aprovação não encontrada.",
        )

    @staticmethod
    def _not_found_matter() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Caso não encontrado.",
        )

    @staticmethod
    def _already_resolved(item: ApprovalQueueItem) -> HTTPException:
        current_status = getattr(item.status, "value", str(item.status))
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "approval_already_resolved",
                "message": "A aprovação já possui uma decisão terminal.",
                "approvalId": str(item.id),
                "currentStatus": current_status,
            },
        )
