"""Durable post-commit orchestration for funnel moves."""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select

from app.db.session import WorkerSessionLocal
from app.db.tenant import set_workspace_context
from app.models.klm_operations import KlmAuditEvent
from app.services.conversation_ownership_service import ConversationOwnershipDenied
from app.services.klm_agent_service import KlmAgentService
from app.services.klm_operations_service import KlmOperationsService
from app.workers.celery_app import celery_app


async def _process(workspace_id: UUID, operation_id: str) -> None:
    async with WorkerSessionLocal() as db:
        # A task recebe o workspace e filtra por ele, mas sob FORCE RLS a leitura
        # sem contexto devolve zero: toda automação pós-movimento retornava em
        # silêncio no `intent is None`.
        await set_workspace_context(db, workspace_id)
        intent = await db.scalar(
            select(KlmAuditEvent).where(
                KlmAuditEvent.workspace_id == workspace_id,
                KlmAuditEvent.event_type == "funnel_move_automation_requested",
                KlmAuditEvent.correlation_id == operation_id,
            )
        )
        if intent is None:
            return
        state = intent.after_data or {}
        completed = await db.scalar(
            select(KlmAuditEvent.id).where(
                KlmAuditEvent.workspace_id == workspace_id,
                KlmAuditEvent.event_type == "funnel_move_automation_completed",
                KlmAuditEvent.correlation_id == operation_id,
            )
        )
        if completed:
            return
        matter_id = intent.entity_id
        actor_id = intent.actor_user_id
        if matter_id is None or actor_id is None:
            return
        agents = list(state.get("agentsTriggered") or [])
        stage = str(state.get("columnId") or "")
        run_ids = []
        approval_id = None
        try:
            for index, agent_key in enumerate(agents):
                execution = await KlmAgentService(db).execute(
                    workspace_id=workspace_id,
                    user_id=actor_id,
                    agent_key=agent_key,
                    matter_id=matter_id,
                    thread_id=None,
                    request_human=False,
                    instructions=(
                        f"Movimento auditado do Kanban: {stage}. Revise o contexto atual, "
                        "preserve correções humanas e prepare somente ações internas reversíveis."
                    ),
                    idempotency_key=f"funnel-move:{operation_id}:{index}"[:120],
                )
                run_ids.extend(str(run.id) for run in execution["runs"])
                approval_id = execution.get("approvalId") or approval_id
            await KlmOperationsService(db).record_audit(
                workspace_id=workspace_id,
                actor_user_id=actor_id,
                actor_type="system",
                actor_label="Orquestração do CRM",
                event_type="funnel_move_automation_completed",
                entity_type="matter",
                entity_id=matter_id,
                summary="Automação pós-movimento concluída pelo worker.",
                correlation_id=operation_id,
                after_data={
                    "agentRunIds": run_ids,
                    "approvalId": str(approval_id) if approval_id else None,
                },
            )
        except ConversationOwnershipDenied as denied:
            if denied.reason != "human_active":
                raise
            # S1: conversa em atendimento humano não é falha de automação, é pulo.
            await KlmOperationsService(db).record_audit(
                workspace_id=workspace_id,
                actor_user_id=actor_id,
                actor_type="system",
                actor_label="Orquestração do CRM",
                event_type="funnel_move_automation_skipped",
                entity_type="matter",
                entity_id=matter_id,
                summary="Automação pós-movimento pulada: conversa em atendimento humano.",
                correlation_id=operation_id,
                after_data={
                    "agentsTriggered": agents,
                    "agentRunIds": run_ids,
                    "reason": "human_active",
                    "externalActionsExecuted": 0,
                },
            )
        except Exception:
            await KlmOperationsService(db).record_audit(
                workspace_id=workspace_id,
                actor_user_id=actor_id,
                actor_type="system",
                actor_label="Orquestração do CRM",
                event_type="funnel_move_automation_failed",
                entity_type="matter",
                entity_id=matter_id,
                summary="Automação pós-movimento falhou no worker.",
                correlation_id=operation_id,
                after_data={"agentsTriggered": agents, "externalActionsExecuted": 0},
            )
        await db.commit()


@celery_app.task(name="funnel_automation_tasks.process")
def process_funnel_automation(workspace_id: str, operation_id: str) -> None:
    asyncio.run(_process(UUID(workspace_id), operation_id))
