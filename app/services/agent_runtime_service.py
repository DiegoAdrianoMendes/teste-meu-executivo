"""
Service: AgentRuntimeService
Persistência, auditoria e governança das execuções de agentes.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import logger
from app.models.agent_platform import AgentDeployment, DeploymentStatus
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.client import Client
from app.models.contract_billing import ContractBillingProposal
from app.models.conversation import Conversation
from app.models.entitlement import EntitlementKey
from app.models.finance_dimensional import FinanceCostCenter
from app.models.human_override import HumanOverride, HumanOverrideDecision
from app.models.matter import Matter
from app.models.task import Task
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.services.agent_platform_service import validate_deployment_ready, verify_deployment
from app.services.entitlement_service import (
    EntitlementEnforcementService,
    entitlement_idempotency_key,
)
from app.services.knowledge_service import KnowledgeService


class AgentDeploymentUnavailableError(ValueError):
    """The requested workspace agent has no active executable deployment."""


def _next_month_period_start() -> datetime:
    """UTC boundary shown to the budget owner; approval is still required."""
    now = datetime.now(UTC)
    if now.month == 12:
        return now.replace(
            year=now.year + 1,
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    return now.replace(
        month=now.month + 1,
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


class AgentRunOverrideConflict(HTTPException):
    def __init__(self, run_id: UUID, existing: HumanOverride):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "agent_run_override_conflict",
                "message": "A primeira decisão terminal deste AgentRun é imutável.",
                "agentRunId": str(run_id),
                "existingOverrideId": str(existing.id),
                "existingDecision": existing.decision.value,
            },
        )


class AgentRunOverrideStateConflict(HTTPException):
    def __init__(self, run_id: UUID, current_status: AgentRunStatus):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "agent_run_not_waiting_human",
                "message": "Somente um AgentRun aguardando revisão humana pode ser decidido.",
                "agentRunId": str(run_id),
                "currentStatus": current_status.value,
            },
        )


class AgentRuntimeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_run(self, run_id: UUID, workspace_id: UUID) -> AgentRun | None:
        result = await self.db.execute(
            select(AgentRun)
            .options(selectinload(AgentRun.overrides))
            .where(AgentRun.id == run_id, AgentRun.workspace_id == workspace_id)
        )
        return result.scalar_one_or_none()

    async def list_runs_for_matter(self, matter_id: UUID, workspace_id: UUID) -> list[AgentRun]:
        result = await self.db.execute(
            select(AgentRun)
            .options(selectinload(AgentRun.overrides))
            .where(AgentRun.matter_id == matter_id, AgentRun.workspace_id == workspace_id)
            .order_by(desc(AgentRun.created_at))
        )
        return list(result.scalars().unique().all())

    async def create_run(
        self,
        *,
        workspace_id: UUID,
        matter_id: UUID,
        agent_name: str,
        trigger_source: str,
        triggered_by_user_id: UUID | None = None,
        thread_id: str | None = None,
        input_payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> AgentRun:
        run, _ = await self.create_run_with_state(
            workspace_id=workspace_id,
            matter_id=matter_id,
            agent_name=agent_name,
            trigger_source=trigger_source,
            triggered_by_user_id=triggered_by_user_id,
            thread_id=thread_id,
            input_payload=input_payload,
            dedupe_key=dedupe_key,
        )
        return run

    async def create_context_run(
        self,
        *,
        workspace_id: UUID,
        context_type: str,
        context_id: UUID | None,
        agent_name: str,
        trigger_source: str,
        triggered_by_user_id: UUID | None = None,
        input_payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> AgentRun:
        allowed = {
            "matter",
            "client",
            "contract",
            "conversation",
            "task",
            "finance",
            "workspace",
            "platform",
        }
        if context_type not in allowed:
            raise ValueError("context_type inválido")
        if context_type not in {"workspace", "platform"} and context_id is None:
            raise ValueError("context_id é obrigatório para este contexto")

        await self._require_context_in_workspace(
            workspace_id=workspace_id,
            context_type=context_type,
            context_id=context_id,
        )
        deployment = await self.db.scalar(
            select(AgentDeployment)
            .where(
                AgentDeployment.workspace_id == workspace_id,
                AgentDeployment.agent_key == agent_name,
                AgentDeployment.status == DeploymentStatus.active,
            )
            .order_by(desc(AgentDeployment.version))
        )
        if deployment is None:
            raise AgentDeploymentUnavailableError(
                "deployment ativo não encontrado para este agente no workspace"
            )
        # A persisted active row is not sufficient: manual runs must pass the
        # same authoritative definition/tool/entitlement/human/budget gates as
        # publication.  Fail closed before creating any AgentRun.
        gates = await verify_deployment(self.db, deployment)
        validate_deployment_ready(deployment.config or {}, gates)

        if dedupe_key:
            existing = await self.db.scalar(
                select(AgentRun).where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.dedupe_key == dedupe_key,
                )
            )
            if existing is not None:
                if (
                    existing.context_type != context_type
                    or existing.context_id != context_id
                    or existing.agent_name != agent_name
                    or existing.trigger_source != trigger_source
                ):
                    raise ValueError(
                        "Chave de idempotência já utilizada por outra execução neste workspace"
                    )
                return existing
        status = AgentRunStatus.queued
        limit = (deployment.budget_policy or {}).get("monthlyTokenLimit")
        if isinstance(limit, int) and limit > 0:
            period_start = datetime.now(UTC).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            used = await self.db.scalar(
                select(
                    func.coalesce(func.sum(AgentRun.input_tokens + AgentRun.output_tokens), 0)
                ).where(
                    AgentRun.deployment_id == deployment.id,
                    AgentRun.created_at >= period_start,
                )
            )
            if int(used or 0) >= limit:
                status = AgentRunStatus.paused_budget
        run = AgentRun(
            workspace_id=workspace_id,
            matter_id=context_id if context_type == "matter" else None,
            context_type=context_type,
            context_id=context_id,
            deployment_id=deployment.id,
            triggered_by_user_id=triggered_by_user_id,
            agent_name=agent_name,
            trigger_source=trigger_source,
            dedupe_key=dedupe_key,
            status=status,
            input_payload=input_payload,
            attempt_count=1,
            celery_task_id=str(uuid4()) if status == AgentRunStatus.queued else None,
        )
        if status == AgentRunStatus.paused_budget:
            # A budget pause is an explicit human decision, not a dormant
            # status.  Preserve its source in the safe output projection but
            # create the canonical call before committing waiting_human.
            run.status = AgentRunStatus.waiting_human
            run.error_message = "Orçamento do deployment atingido; revisão humana necessária."
            run.output_payload = {
                "runtimeWait": {
                    "sourceStatus": AgentRunStatus.paused_budget.value,
                    "requiresHumanDecision": True,
                    "resumePolicy": "human_decision_required",
                    "scheduledResumeAt": _next_month_period_start().isoformat(),
                }
            }
        self.db.add(run)
        await self.db.flush()
        # Contextual/manual runs consume the same metered entitlements as
        # matter-triggered runs. Reservations are idempotent on the run id and
        # committed with the run so a failed quota check cannot leave a ghost.
        if run.status == AgentRunStatus.queued:
            enforcer = EntitlementEnforcementService(self.db)
            execution_reservation = await enforcer.reserve_metered(
                workspace_id=workspace_id,
                entitlement=EntitlementKey.ai_executions,
                quantity=1,
                idempotency_key=entitlement_idempotency_key("agent-run-execution", run.id),
            )
            await enforcer.commit_metered(
                workspace_id=workspace_id, reservation=execution_reservation
            )
            conversation_reservation = await enforcer.reserve_metered(
                workspace_id=workspace_id,
                entitlement=EntitlementKey.ai_conversations,
                quantity=1,
                idempotency_key=entitlement_idempotency_key(
                    "ai-conversation",
                    input_payload.get("thread_id") if input_payload else f"context:{run.id}",
                ),
            )
            await enforcer.commit_metered(
                workspace_id=workspace_id, reservation=conversation_reservation
            )
            run.input_payload = {
                **(run.input_payload or {}),
                "entitlementUsage": {
                    "executionReservationId": (
                        str(execution_reservation.id) if execution_reservation else None
                    ),
                    "conversationReservationId": (
                        str(conversation_reservation.id) if conversation_reservation else None
                    ),
                },
            }
        if status == AgentRunStatus.paused_budget:
            await self.ensure_human_decision(
                run,
                decision_key=f"agent-run:{run.id}:runtime-wait:paused_budget",
                options={
                    "approve": True,
                    "reject": True,
                    "purpose": "agent_paused_budget_timeout",
                    "reason": "Orçamento do deployment atingido; decisão humana necessária.",
                    "resumeAgentRunOnApprove": True,
                    "sourceWaitStatus": AgentRunStatus.paused_budget.value,
                },
            )
        if run.status == AgentRunStatus.queued:
            from app.db.post_commit import queue_agent_run_wakeup

            queue_agent_run_wakeup(
                self.db,
                workspace_id=workspace_id,
                agent_run_id=run.id,
                task_id=run.celery_task_id,
            )
        # The caller owns the final commit.  Manual contextual runs are a
        # platform mutation and the API records their canonical audit event in
        # this same transaction.  Committing here used to make it possible to
        # persist and dispatch a run even if that audit write later failed.
        # ``queue_agent_run_wakeup`` remains post-commit, so no worker can
        # observe this run until the enclosing command succeeds.
        return run

    async def _require_context_in_workspace(
        self,
        *,
        workspace_id: UUID,
        context_type: str,
        context_id: UUID | None,
    ) -> None:
        """Fail closed before a run can reference data owned by another tenant."""
        if context_type in {"workspace", "platform"}:
            if context_id is not None:
                raise ValueError("context_id deve ser omitido para contexto global")
            return

        query = None
        if context_type == "matter":
            query = select(Matter.id).where(
                Matter.id == context_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        elif context_type == "client":
            query = select(Client.id).where(
                Client.id == context_id,
                Client.workspace_id == workspace_id,
                Client.deleted_at.is_(None),
            )
        elif context_type == "contract":
            query = select(ContractBillingProposal.id).where(
                ContractBillingProposal.id == context_id,
                ContractBillingProposal.workspace_id == workspace_id,
            )
        elif context_type == "conversation":
            query = (
                select(Conversation.id)
                .join(Matter, Matter.id == Conversation.matter_id)
                .where(
                    Conversation.id == context_id,
                    Matter.workspace_id == workspace_id,
                    Matter.deleted_at.is_(None),
                )
            )
        elif context_type == "task":
            query = (
                select(Task.id)
                .join(Matter, Matter.id == Task.matter_id)
                .where(
                    Task.id == context_id,
                    Matter.workspace_id == workspace_id,
                    Matter.deleted_at.is_(None),
                )
            )
        elif context_type == "finance":
            query = select(FinanceCostCenter.id).where(
                FinanceCostCenter.id == context_id,
                FinanceCostCenter.workspace_id == workspace_id,
            )

        if query is None or await self.db.scalar(query) is None:
            raise ValueError("Contexto não encontrado no workspace")

    async def create_run_with_state(
        self,
        *,
        workspace_id: UUID,
        matter_id: UUID,
        agent_name: str,
        trigger_source: str,
        triggered_by_user_id: UUID | None = None,
        thread_id: str | None = None,
        input_payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> tuple[AgentRun, bool]:
        matter_exists = await self.db.scalar(
            select(Matter.id).where(
                Matter.id == matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if matter_exists is None:
            raise ValueError("Caso não encontrado no workspace")

        if dedupe_key:
            existing = await self.db.execute(
                select(AgentRun)
                .options(selectinload(AgentRun.overrides))
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.dedupe_key == dedupe_key,
                )
            )
            existing_run = existing.scalar_one_or_none()
            if existing_run:
                if (
                    existing_run.matter_id != matter_id
                    or existing_run.agent_name != agent_name
                    or existing_run.trigger_source != trigger_source
                ):
                    raise ValueError(
                        "Chave de idempotência já utilizada por outra execução neste workspace"
                    )
                return existing_run, False

        enforcer = EntitlementEnforcementService(self.db)
        await enforcer.lock_workspace(workspace_id)
        locked_matter_id = await self.db.scalar(
            select(Matter.id)
            .where(
                Matter.id == matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if locked_matter_id is None:
            raise ValueError("Caso não encontrado no workspace")

        if dedupe_key:
            existing_run = await self.db.scalar(
                select(AgentRun)
                .options(selectinload(AgentRun.overrides))
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.dedupe_key == dedupe_key,
                )
            )
            if existing_run:
                if (
                    existing_run.matter_id != matter_id
                    or existing_run.agent_name != agent_name
                    or existing_run.trigger_source != trigger_source
                ):
                    raise ValueError(
                        "Chave de idempotência já utilizada por outra execução neste workspace"
                    )
                return existing_run, False

        active = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.matter_id == matter_id,
                AgentRun.agent_name == agent_name,
                AgentRun.trigger_source == trigger_source,
                AgentRun.status.in_([AgentRunStatus.queued, AgentRunStatus.running]),
            )
            .order_by(desc(AgentRun.created_at))
        )
        active_run = active.scalar_one_or_none()
        if active_run and not dedupe_key:
            return active_run, False

        deployment = await self.db.scalar(
            select(AgentDeployment)
            .where(
                AgentDeployment.workspace_id == workspace_id,
                AgentDeployment.agent_key == agent_name,
                AgentDeployment.status == DeploymentStatus.active,
            )
            .order_by(desc(AgentDeployment.version))
        )
        if deployment is None:
            raise AgentDeploymentUnavailableError(
                "deployment ativo não encontrado para este agente no workspace"
            )
        gates = await verify_deployment(self.db, deployment)
        validate_deployment_ready(deployment.config or {}, gates)
        if deployment is not None:
            limit = (deployment.budget_policy or {}).get("monthlyTokenLimit")
            if isinstance(limit, int) and limit > 0:
                now = datetime.now(UTC)
                period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                used = await self.db.scalar(
                    select(
                        func.coalesce(func.sum(AgentRun.input_tokens + AgentRun.output_tokens), 0)
                    ).where(
                        AgentRun.deployment_id == deployment.id,
                        AgentRun.created_at >= period_start,
                    )
                )
                if int(used or 0) >= limit:
                    raise ValueError("deployment monthly token budget exhausted")
        run = AgentRun(
            workspace_id=workspace_id,
            matter_id=matter_id,
            triggered_by_user_id=triggered_by_user_id,
            agent_name=agent_name,
            trigger_source=trigger_source,
            thread_id=thread_id,
            dedupe_key=dedupe_key,
            status=AgentRunStatus.queued,
            input_payload=input_payload,
            attempt_count=1,
            deployment_id=deployment.id if deployment else None,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(run)
                await self.db.flush()
                execution_reservation = await enforcer.reserve_metered(
                    workspace_id=workspace_id,
                    entitlement=EntitlementKey.ai_executions,
                    quantity=1,
                    idempotency_key=entitlement_idempotency_key(
                        "agent-run-execution",
                        run.id,
                    ),
                )
                await enforcer.commit_metered(
                    workspace_id=workspace_id,
                    reservation=execution_reservation,
                )
                conversation_scope = thread_id or f"matter:{matter_id}"
                conversation_reservation = await enforcer.reserve_metered(
                    workspace_id=workspace_id,
                    entitlement=EntitlementKey.ai_conversations,
                    quantity=1,
                    idempotency_key=entitlement_idempotency_key(
                        "ai-conversation",
                        conversation_scope,
                    ),
                )
                await enforcer.commit_metered(
                    workspace_id=workspace_id,
                    reservation=conversation_reservation,
                )
                if execution_reservation is not None or conversation_reservation is not None:
                    run.input_payload = {
                        **(run.input_payload or {}),
                        "entitlementUsage": {
                            "executionReservationId": (
                                str(execution_reservation.id)
                                if execution_reservation is not None
                                else None
                            ),
                            "conversationReservationId": (
                                str(conversation_reservation.id)
                                if conversation_reservation is not None
                                else None
                            ),
                        },
                    }
                await self._append_timeline(
                    matter_id=matter_id,
                    title=f"Execução enfileirada: {agent_name}",
                    description=f"Fonte: {trigger_source}",
                    event_data={
                        "agent_run_id": str(run.id),
                        "status": run.status.value,
                        "trigger_source": trigger_source,
                    },
                )
                await self.db.flush()
        except IntegrityError:
            if dedupe_key:
                existing_run = await self.db.scalar(
                    select(AgentRun)
                    .options(selectinload(AgentRun.overrides))
                    .where(
                        AgentRun.workspace_id == workspace_id,
                        AgentRun.dedupe_key == dedupe_key,
                    )
                )
                if existing_run:
                    if (
                        existing_run.matter_id != matter_id
                        or existing_run.agent_name != agent_name
                        or existing_run.trigger_source != trigger_source
                    ):
                        raise ValueError(
                            "Chave de idempotência já utilizada por outra execução neste workspace"
                        )
                    return existing_run, False
            raise
        return run, True

    async def mark_running(self, run: AgentRun, celery_task_id: str | None = None) -> AgentRun:
        run.status = AgentRunStatus.running
        run.celery_task_id = celery_task_id
        run.started_at = datetime.now(UTC)
        run.error_message = None
        await self.db.flush()
        await self._broadcast_run_event("AGENT_RUN_STARTED", run)
        return run

    async def mark_completed(
        self, run: AgentRun, output_payload: dict[str, Any] | None = None
    ) -> AgentRun:
        run.status = AgentRunStatus.completed
        run.completed_at = datetime.now(UTC)
        run.output_payload = output_payload
        run.error_message = None
        await self.db.flush()
        await KnowledgeService(self.db).index_agent_run(
            run_id=run.id,
            workspace_id=run.workspace_id,
            matter_id=run.matter_id,
            agent_name=run.agent_name,
            trigger_source=run.trigger_source,
            output_payload=output_payload,
        )

        await self._append_timeline(
            matter_id=run.matter_id,
            title=f"{run.agent_name} concluído",
            description=self._build_summary(run, output_payload),
            event_data={
                "agent_run_id": str(run.id),
                "status": run.status.value,
                "agent_name": run.agent_name,
            },
        )
        await self._append_internal_message(
            run.workspace_id, run.matter_id, self._build_summary(run, output_payload)
        )
        await self._broadcast_run_event("AGENT_RUN_COMPLETED", run)
        return run

    async def mark_waiting_human(
        self,
        run: AgentRun,
        output_payload: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> AgentRun:
        # A generic AgentRun used to stop here with only a status and a case
        # timeline entry.  That is not a handoff: nobody is assigned, the
        # owner is not alerted and the watchdog has nothing to escalate.  A
        # Flow-owned run is the sole exception because FlowService creates the
        # richer checkpoint/decision after it settles the run in this same
        # unit of work.
        run.status = AgentRunStatus.waiting_human
        run.completed_at = datetime.now(UTC)
        run.output_payload = output_payload
        run.error_message = reason
        await self.db.flush()
        if not self._is_flow_owned_run(run):
            await self.ensure_human_decision(run)
        await KnowledgeService(self.db).index_agent_run(
            run_id=run.id,
            workspace_id=run.workspace_id,
            matter_id=run.matter_id,
            agent_name=run.agent_name,
            trigger_source=run.trigger_source,
            output_payload=output_payload,
        )

        matter = await self.db.scalar(
            select(Matter)
            .where(
                Matter.id == run.matter_id,
                Matter.workspace_id == run.workspace_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if matter:
            matter.needs_approval = True
            matter.updated_at = datetime.now(UTC)

        await self._append_timeline(
            matter_id=run.matter_id,
            title=f"{run.agent_name} aguardando revisão humana",
            description=reason or self._build_summary(run, output_payload),
            event_data={
                "agent_run_id": str(run.id),
                "status": run.status.value,
                "agent_name": run.agent_name,
            },
        )
        await self._broadcast_run_event("AGENT_RUN_WAITING_HUMAN", run)
        return run

    @staticmethod
    def _is_flow_owned_run(run: AgentRun) -> bool:
        return bool(isinstance(run.input_payload, dict) and run.input_payload.get("flow_run_id"))

    @staticmethod
    def human_routing_requirements(run: AgentRun) -> tuple[list[str], list[str]]:
        """Return a safe, catalog-backed first responsibility for legacy runs.

        Legacy agents do not yet carry a V3 node routing contract.  Their
        names are system-controlled, so this mapping is deliberately kept
        local and never consumes model output, user text or identifiers.  The
        routing service then proves availability and ascends through the
        actual team hierarchy to the workspace owner.
        """
        agent_key = (run.agent_name or "").strip().casefold()
        functional_packages = (
            (("accounting", "contador", "contabil", "fiscal", "tax", "tribut"), "accounting"),
            (
                ("finance", "financeiro", "billing", "cobranca", "dunning", "reconciliation"),
                "finance",
            ),
            (("sdr", "intake", "triagem", "qualificacao"), "intake_sdr"),
            (("sales", "comercial", "business_development", "vendas"), "business_development"),
            (
                ("onboarding", "pos_venda", "customer_success", "client_success", "sucesso"),
                "client_success",
            ),
            (
                ("secretaria", "secretary", "agenda", "reception", "recepcao", "atendimento"),
                "secretary",
            ),
            (
                ("integration", "integracao", "technology", "tecnologia", "technical", "support"),
                "it_integrations",
            ),
            (("security", "seguranca", "audit", "auditor", "compliance"), "auditor"),
        )
        for tokens, access_package in functional_packages:
            if any(token in agent_key for token in tokens):
                return [access_package], []
        if any(
            token in agent_key for token in ("legal", "jurid", "drafter", "analyst", "petition")
        ):
            return ["legal_manager"], []
        return ["workspace_admin"], []

    async def ensure_human_decision(
        self,
        run: AgentRun,
        *,
        decision_key: str | None = None,
        options: dict[str, Any] | None = None,
        sla_minutes: int = 60,
    ):
        """Create the one durable call that makes a waiting run actionable.

        The caller owns the transaction.  Thus a committed ``waiting_human``
        generic run always has its HumanDecision, in-app owner watch and
        phone-delivery attempt/state together; an exception rolls all of them
        back instead of creating a phantom wait.
        """
        if run.status != AgentRunStatus.waiting_human:
            raise ValueError("human decision requires an AgentRun waiting for a human")
        if self._is_flow_owned_run(run):
            return None
        from app.services.agent_platform_service import HumanRoutingService

        required_roles, required_skills = self.human_routing_requirements(run)
        return await HumanRoutingService(self.db).create_decision(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            # A watchdog owns a different, durable reason for the same run.
            # Its opaque key is stable across retries, which makes the call
            # idempotent without merging an old human decision into a new
            # operational problem.
            decision_key=decision_key or f"agent-run:{run.id}:human-review",
            options=options or {"approve": True, "reject": True},
            required_roles=required_roles,
            required_skills=required_skills,
            sla_minutes=sla_minutes,
            max_hops=4,
        )

    async def mark_failed(self, run: AgentRun, error_message: str) -> AgentRun:
        run.status = AgentRunStatus.failed
        run.completed_at = datetime.now(UTC)
        run.error_message = error_message
        await self.db.flush()
        await KnowledgeService(self.db).index_agent_run(
            run_id=run.id,
            workspace_id=run.workspace_id,
            matter_id=run.matter_id,
            agent_name=run.agent_name,
            trigger_source=run.trigger_source,
            output_payload={"status": "failed", "error": error_message},
        )

        await self._append_timeline(
            matter_id=run.matter_id,
            title=f"{run.agent_name} falhou",
            description=error_message[:500],
            event_data={
                "agent_run_id": str(run.id),
                "status": run.status.value,
                "agent_name": run.agent_name,
            },
        )
        await self._broadcast_run_event("AGENT_RUN_FAILED", run)
        return run

    async def create_override(
        self,
        *,
        run: AgentRun,
        workspace_id: UUID,
        decided_by_user_id: UUID,
        decision: HumanOverrideDecision,
        notes: str | None = None,
        corrected_payload: dict[str, Any] | None = None,
    ) -> HumanOverride:
        """Persist the first decision; only an exact decision/notes/payload retry is idempotent."""
        if run.workspace_id != workspace_id:
            raise ValueError("AgentRun não pertence ao workspace informado")

        locked_run = await self.db.scalar(
            select(AgentRun)
            .where(AgentRun.id == run.id, AgentRun.workspace_id == workspace_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if not locked_run:
            raise ValueError("AgentRun não pertence ao workspace informado")

        existing = await self._first_override(locked_run.id)
        if existing:
            return await self._resolve_existing_override(
                existing=existing,
                run_id=locked_run.id,
                decision=decision,
                notes=notes,
                corrected_payload=corrected_payload,
            )

        if locked_run.status != AgentRunStatus.waiting_human:
            conflict = AgentRunOverrideStateConflict(locked_run.id, locked_run.status)
            await self.db.rollback()
            raise conflict

        override = HumanOverride(
            workspace_id=workspace_id,
            matter_id=locked_run.matter_id,
            agent_run_id=locked_run.id,
            decided_by_user_id=decided_by_user_id,
            decision=decision,
            notes=notes,
            before_payload=locked_run.output_payload,
            after_payload=corrected_payload,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(override)
                await self.db.flush()
        except IntegrityError:
            existing = await self._first_override(locked_run.id)
            if not existing:
                raise
            return await self._resolve_existing_override(
                existing=existing,
                run_id=locked_run.id,
                decision=decision,
                notes=notes,
                corrected_payload=corrected_payload,
            )

        now = datetime.now(UTC)
        if decision == HumanOverrideDecision.accepted:
            locked_run.status = AgentRunStatus.completed
            locked_run.error_message = None
        elif decision == HumanOverrideDecision.corrected:
            locked_run.status = AgentRunStatus.completed
            locked_run.error_message = None
            if corrected_payload is not None:
                locked_run.output_payload = corrected_payload
        else:
            locked_run.status = AgentRunStatus.failed
            locked_run.error_message = notes or "Saída do agente rejeitada por override humano"
        locked_run.completed_at = now
        await self.db.flush()

        matter = await self.db.scalar(
            select(Matter)
            .where(
                Matter.id == locked_run.matter_id,
                Matter.workspace_id == workspace_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if matter:
            other_waiting_run_id = await self.db.scalar(
                select(AgentRun.id)
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.matter_id == locked_run.matter_id,
                    AgentRun.status == AgentRunStatus.waiting_human,
                )
                .limit(1)
            )
            matter.needs_approval = other_waiting_run_id is not None
            matter.updated_at = now

        await self.db.flush()

        await self._append_timeline(
            matter_id=locked_run.matter_id,
            title=f"Override humano em {locked_run.agent_name}",
            description=notes or f"Decisão: {decision.value}",
            event_data={
                "agent_run_id": str(locked_run.id),
                "human_override_id": str(override.id),
                "decision": decision.value,
            },
        )
        await self._append_internal_message(
            workspace_id,
            locked_run.matter_id,
            f"Override humano em {locked_run.agent_name}: {decision.value}. {notes or ''}".strip(),
        )
        await self._broadcast_run_event("AGENT_RUN_OVERRIDDEN", locked_run)

        flow_linked = bool((locked_run.input_payload or {}).get("flow_run_id"))
        await self.db.commit()
        if flow_linked:
            try:
                from app.workers.flow_tasks import settle_flow_agent_run

                settle_flow_agent_run.apply_async(
                    args=[str(locked_run.id), str(workspace_id)],
                    countdown=1,
                )
            except Exception as error:
                logger.error(
                    "agent_override.flow_settlement_enqueue_failed",
                    agent_run_id=str(locked_run.id),
                    error_type=type(error).__name__,
                )

        if flow_linked:
            try:
                from app.services.flow_service import FlowService

                await FlowService(self.db).settle_agent_run(locked_run.id, workspace_id)
            except Exception as error:
                logger.error(
                    "agent_override.flow_settlement_failed",
                    agent_run_id=str(locked_run.id),
                    error_type=type(error).__name__,
                )
        return override

    async def _first_override(self, run_id: UUID) -> HumanOverride | None:
        return await self.db.scalar(
            select(HumanOverride)
            .where(HumanOverride.agent_run_id == run_id)
            .order_by(HumanOverride.created_at, HumanOverride.id)
            .limit(1)
        )

    async def _resolve_existing_override(
        self,
        *,
        existing: HumanOverride,
        run_id: UUID,
        decision: HumanOverrideDecision,
        notes: str | None,
        corrected_payload: dict[str, Any] | None,
    ) -> HumanOverride:
        if (
            existing.decision == decision
            and existing.notes == notes
            and existing.after_payload == corrected_payload
        ):
            await self.db.commit()
            return existing
        conflict = AgentRunOverrideConflict(run_id, existing)
        await self.db.rollback()
        raise conflict

    def _build_summary(self, run: AgentRun, output_payload: dict[str, Any] | None) -> str:
        if output_payload:
            summary = (
                output_payload.get("summary")
                or output_payload.get("message")
                or output_payload.get("conversation_summary")
            )
            if summary:
                return summary
            if run.agent_name == "klm_sdr":
                classification = output_payload.get("classification")
                score = output_payload.get("qualification_score") or output_payload.get(
                    "qualification_score"
                )
                if classification:
                    return f"Lead qualificado como {classification}."
                if score is not None:
                    return f"SDR concluiu a rodada com score {score}."
        return f"Execução {run.agent_name} finalizada com status {run.status.value}."

    async def _append_timeline(
        self,
        *,
        matter_id: UUID | None,
        title: str,
        description: str | None,
        event_data: dict[str, Any] | None = None,
    ) -> None:
        # A timeline pertence ao Caso 360. Execuções de workspace/plataforma
        # continuam auditadas no runtime, mas não podem inventar um caso para
        # acomodar um evento operacional.
        if matter_id is None:
            return
        self.db.add(
            TimelineEvent(
                matter_id=matter_id,
                event_type=TimelineEventType.agent_action,
                title=title,
                description=description,
                event_data=event_data,
            )
        )

    async def _append_internal_message(
        self, workspace_id: UUID, matter_id: UUID, message: str
    ) -> None:
        conversation = Conversation(
            matter_id=matter_id,
            message=message,
            is_internal=True,
        )
        self.db.add(conversation)
        await self.db.flush()
        await KnowledgeService(self.db).index_internal_conversation(
            workspace_id=workspace_id,
            matter_id=matter_id,
            conversation_id=conversation.id,
            message=message,
        )

    async def _broadcast_run_event(self, event_name: str, run: AgentRun) -> None:
        from app.db.post_commit import queue_realtime_event

        queue_realtime_event(
            self.db,
            workspace_id=run.workspace_id,
            event_type=event_name,
            data={
                "matter_id": str(run.matter_id),
                "agent_run_id": str(run.id),
                "agent_name": run.agent_name,
                "status": run.status.value,
            },
        )
