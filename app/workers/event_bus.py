"""
EventBus — dispatcher assíncrono dos agentes via Celery.
"""
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import model_api_execution_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.session import WorkerSessionLocal as AsyncSessionLocal
from app.db.tenant import set_workspace_context
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.matter import Matter
from app.models.task import TaskPriority
from app.schemas.task import TaskCreate
from app.services.agent_runtime_service import (
    AgentDeploymentUnavailableError,
    AgentRuntimeService,
)
from app.services.task_service import TaskService
from app.services.workspace_policy_service import WorkspacePolicyService
from app.workers.agent_tasks import execute_agent_run

COLUMN_AGENT_MAP: Dict[str, str] = {
    "entrada": "klm_sdr",
    "proposta_fechamento": "closer_humano",
    "coleta_dados": "administrativo_ops",
}


def _publish_agent_run(*, run_id: UUID | str, workspace_id: UUID | str, task_id: str) -> None:
    execute_agent_run.apply_async(
        args=[str(run_id), str(workspace_id)],
        task_id=task_id,
    )


async def _enqueue_agent_run(
    *,
    workspace_id: UUID,
    matter_id: UUID,
    agent_name: str,
    trigger_source: str,
    input_payload: Optional[Dict[str, Any]] = None,
    triggered_by_user_id: Optional[UUID] = None,
    dedupe_key: Optional[str] = None,
    session_factory=None,
) -> Optional[AgentRun]:
    resolved_workspace_id = UUID(str(workspace_id))
    session_maker = session_factory or AsyncSessionLocal
    dispatch_task_id: Optional[str] = None
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        matter = await db.scalar(
            select(Matter).where(
                Matter.id == matter_id,
                Matter.workspace_id == resolved_workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if not matter:
            logger.error(
                "event_bus.matter_not_found",
                matter_id=str(matter_id),
                workspace_id=str(resolved_workspace_id),
                agent=agent_name,
            )
            return None

        payload = input_payload or {}
        stage_key = payload.get("stage_key") or (
            trigger_source.split(":", 1)[1] if trigger_source.startswith("column:") else "entrada"
        )
        policy = await WorkspacePolicyService(db).resolve_stage_policy(
            resolved_workspace_id,
            stage_key,
        )
        if policy.get("mode") == "human_only":
            task_fingerprint = hashlib.sha256(
                f"{stage_key}:{policy.get('agent_key')}".encode()
            ).hexdigest()[:24]
            await TaskService(db).create_automatic(
                matter.id,
                TaskCreate(
                    title=f"Atuação humana necessária em {stage_key}"[:500],
                    description=(
                        f"A política atual desta etapa exige atuação humana "
                        f"({policy.get('agent_key')})."
                    )[:10000],
                    priority=TaskPriority.alta,
                    is_ai_generated=False,
                    idempotency_key=f"human-only:{matter.id}:{task_fingerprint}",
                ),
                resolved_workspace_id,
                source="event_bus.human_only_stage",
                actor_user_id=triggered_by_user_id,
                preferred_owner_id=matter.responsible_human_id,
                correlation_id=f"event_bus:human_only:{matter.id}:{task_fingerprint}",
            )
            await db.commit()
            logger.info(
                "event_bus.human_only_stage",
                matter_id=str(matter_id),
                stage_key=stage_key,
                agent_name=policy.get("agent_key"),
            )
            return None

        runtime = AgentRuntimeService(db)
        resolved_agent_name = policy.get("agent_key", agent_name)
        try:
            run, created = await runtime.create_run_with_state(
                workspace_id=resolved_workspace_id,
                matter_id=matter.id,
                agent_name=resolved_agent_name,
                trigger_source=trigger_source,
                triggered_by_user_id=triggered_by_user_id,
                thread_id=f"{resolved_agent_name}-{matter.id}",
                input_payload={**payload, "stage_key": stage_key},
                dedupe_key=dedupe_key,
            )
        except AgentDeploymentUnavailableError:
            task_fingerprint = hashlib.sha256(
                f"missing-deployment:{resolved_agent_name}:{matter.id}".encode()
            ).hexdigest()[:24]
            await TaskService(db).create_automatic(
                matter.id,
                TaskCreate(
                    title=f"Atuação humana necessária em {stage_key}"[:500],
                    description=(
                        f"O agente {resolved_agent_name} não possui deployment ativo. "
                        "Assuma o atendimento e acompanhe a configuração do agente."
                    )[:10000],
                    priority=TaskPriority.alta,
                    is_ai_generated=False,
                    idempotency_key=(
                        f"missing-deployment:{matter.id}:{task_fingerprint}"
                    ),
                ),
                resolved_workspace_id,
                source="event_bus.missing_agent_deployment",
                actor_user_id=triggered_by_user_id,
                preferred_owner_id=matter.responsible_human_id,
                correlation_id=(
                    f"event_bus:missing-deployment:{matter.id}:{task_fingerprint}"
                ),
            )
            await db.commit()
            logger.warning(
                "event_bus.missing_agent_deployment_human_fallback",
                matter_id=str(matter.id),
                stage_key=stage_key,
                agent_name=resolved_agent_name,
            )
            return None
        if not created:
            if run.status == AgentRunStatus.queued:
                if not run.celery_task_id:
                    run.celery_task_id = str(uuid4())
                    run.updated_at = datetime.now(timezone.utc)
                dispatch_task_id = run.celery_task_id
            await db.commit()
        elif run.status == AgentRunStatus.queued and not model_api_execution_allowed():
            await runtime.mark_waiting_human(
                run,
                output_payload={
                    "summary": "Execução registrada, mas não iniciada no modo local.",
                    "localOnly": True,
                    "externalActionsExecuted": 0,
                },
                reason="Agentes permanecem desligados até a aprovação do Meu Executivo.",
            )
            await db.commit()
            logger.info(
                "event_bus.agent_blocked_locally",
                matter_id=str(matter_id),
                agent_name=agent_name,
                agent_run_id=str(run.id),
            )
            return run
        else:
            if run.status == AgentRunStatus.queued:
                run.celery_task_id = str(uuid4())
                run.updated_at = datetime.now(timezone.utc)
                dispatch_task_id = run.celery_task_id
            await db.commit()

    if dispatch_task_id:
        try:
            _publish_agent_run(
                run_id=run.id,
                workspace_id=resolved_workspace_id,
                task_id=dispatch_task_id,
            )
            logger.info(
                "event_bus.agent_enqueued",
                matter_id=str(matter_id),
                agent_name=agent_name,
                agent_run_id=str(run.id),
                trigger_source=trigger_source,
            )
        except Exception as exc:
            logger.error(
                "event_bus.enqueue_failed",
                matter_id=str(matter_id),
                agent_name=agent_name,
                agent_run_id=str(run.id),
                error_type=safe_error_type(exc),
            )
    return run


async def dispatch_new_lead(
    matter_id: UUID,
    triggered_by_user_id: Optional[UUID] = None,
    *,
    workspace_id: UUID,
    dedupe_key: str | None = None,
) -> Optional[AgentRun]:
    return await _enqueue_agent_run(
        workspace_id=workspace_id,
        matter_id=matter_id,
        agent_name="klm_sdr",
        trigger_source="column:entrada",
        triggered_by_user_id=triggered_by_user_id,
        dedupe_key=dedupe_key,
    )


async def dispatch_whatsapp_message(
    matter_id: UUID,
    contact_id: UUID,
    inbound_text: str,
    wamid: Optional[str] = None,
    *,
    workspace_id: UUID,
) -> Optional[AgentRun]:
    dedupe_key = f"whatsapp:{wamid}" if wamid else None
    return await _enqueue_agent_run(
        workspace_id=workspace_id,
        matter_id=matter_id,
        agent_name="klm_sdr",
        trigger_source="whatsapp:message",
        input_payload={
            "contact_id": str(contact_id),
            "inbound_text": inbound_text,
            "wamid": wamid,
        },
        dedupe_key=dedupe_key,
    )


async def dispatch_social_message(
    matter_id: UUID,
    thread_id: UUID,
    inbound_text: str,
    channel: str,
    *,
    workspace_id: UUID,
    dedupe_key: Optional[str] = None,
) -> Optional[AgentRun]:
    """Continua a qualificação SDR para uma DM social (instagram/messenger/facebook).

    Espelha `dispatch_whatsapp_message`, mas roteia pelo `thread_id` do v6
    omnichannel em vez de um `contact_id` de WhatsApp — o consumidor
    (`agent_tasks.py`) diferencia os dois payloads por essa chave e produz a
    resposta como mensagem local_draft/nota interna gated no trilho social,
    nunca via transporte real.
    """
    return await _enqueue_agent_run(
        workspace_id=workspace_id,
        matter_id=matter_id,
        agent_name="klm_sdr",
        trigger_source="social:message",
        input_payload={
            "thread_id": str(thread_id),
            "inbound_text": inbound_text,
            "channel": channel,
        },
        dedupe_key=dedupe_key,
    )


async def dispatch_card_moved(
    matter_id: UUID,
    column_id: str,
    triggered_by_user_id: Optional[UUID] = None,
    *,
    workspace_id: UUID,
) -> Optional[AgentRun]:
    agent_name = COLUMN_AGENT_MAP.get(column_id)
    if not agent_name:
        logger.debug("event_bus.no_handler_for_column", matter_id=str(matter_id), column_id=column_id)
        return None

    return await _enqueue_agent_run(
        workspace_id=workspace_id,
        matter_id=matter_id,
        agent_name=agent_name,
        trigger_source=f"column:{column_id}",
        triggered_by_user_id=triggered_by_user_id,
    )


async def dispatch_approval_resolved(
    matter_id: UUID,
    approval_id: UUID,
    status: str,
    reason: Optional[str] = None,
    *,
    workspace_id: UUID,
) -> Optional[AgentRun]:
    resolved_workspace_id = UUID(str(workspace_id))
    async with AsyncSessionLocal() as db:
        await set_workspace_context(db, resolved_workspace_id)
        matter = await db.scalar(
            select(Matter).where(
                Matter.id == matter_id,
                Matter.workspace_id == resolved_workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if not matter:
            return None

        latest_run_result = await db.execute(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == resolved_workspace_id,
                AgentRun.matter_id == matter_id,
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        latest_run = latest_run_result.scalar_one_or_none()
        agent_name = latest_run.agent_name if latest_run else COLUMN_AGENT_MAP.get(matter.column_id.value, "klm_sdr")

    return await _enqueue_agent_run(
        workspace_id=resolved_workspace_id,
        matter_id=matter_id,
        agent_name=agent_name,
        trigger_source=f"approval:{status}",
        input_payload={
            "approval_id": str(approval_id),
            "approval_status": status,
            "approval_reason": reason,
            "reprocess_requested": True,
        },
        dedupe_key=f"approval:{approval_id}:{status}",
    )


async def dispatch_analyst(
    matter_id: UUID,
    trigger_source: str = "sdr:handoff",
    db: AsyncSession = None,
    *,
    workspace_id: UUID,
) -> Optional[AgentRun]:
    """Aciona klm_analyst quando SDR conclui handoff para proposta_fechamento."""
    return await _enqueue_agent_run(
        workspace_id=workspace_id,
        matter_id=matter_id,
        agent_name="klm_analyst",
        trigger_source=trigger_source,
    )


async def dispatch_drafter(
    matter_id: UUID,
    analyst_output: dict = None,
    db: AsyncSession = None,
    *,
    workspace_id: UUID,
) -> Optional[AgentRun]:
    """Aciona klm_drafter quando analyst recomenda 'proceed'."""
    return await _enqueue_agent_run(
        workspace_id=workspace_id,
        matter_id=matter_id,
        agent_name="klm_drafter",
        trigger_source="analyst:proceed",
        input_payload={"analyst_output": analyst_output or {}},
    )


async def retry_agent_run(
    *,
    run_id: UUID,
    workspace_id: UUID,
    triggered_by_user_id: Optional[UUID] = None,
    session_factory=None,
) -> Optional[AgentRun]:
    resolved_workspace_id = UUID(str(workspace_id))
    session_maker = session_factory or AsyncSessionLocal
    is_flow_retry = False
    retry_created = True
    dispatch_task_id: Optional[str] = None
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        result = await db.execute(
            select(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.workspace_id == resolved_workspace_id,
            )
        )
        run = result.scalar_one_or_none()
        if not run:
            return None

        runtime = AgentRuntimeService(db)
        is_flow_retry = bool((run.input_payload or {}).get("flow_run_id"))
        if is_flow_retry:
            from app.services.flow_service import FlowService

            retried, retry_created = await FlowService(db).create_flow_agent_retry(
                run.id,
                resolved_workspace_id,
                triggered_by_user_id=triggered_by_user_id,
            )
        else:
            retried, retry_created = await runtime.create_run_with_state(
                workspace_id=resolved_workspace_id,
                matter_id=run.matter_id,
                agent_name=run.agent_name,
                trigger_source=f"{run.trigger_source}:retry",
                triggered_by_user_id=triggered_by_user_id,
                thread_id=run.thread_id,
                input_payload=run.input_payload,
            )
        if retried.status.value == "queued" and not model_api_execution_allowed():
            await runtime.mark_waiting_human(
                retried,
                output_payload={
                    "summary": "Retry registrado sem execução no modo local.",
                    "localOnly": True,
                    "externalActionsExecuted": 0,
                },
                reason="Agentes permanecem desligados até a aprovação do Meu Executivo.",
            )
            await db.commit()
            if is_flow_retry:
                from app.services.flow_service import FlowService

                await FlowService(db).settle_agent_run(
                    retried.id,
                    resolved_workspace_id,
            )
            return retried
        if retry_created and retried.status == AgentRunStatus.queued:
            retried.celery_task_id = retried.celery_task_id or str(uuid4())
            retried.updated_at = datetime.now(timezone.utc)
            dispatch_task_id = retried.celery_task_id
        await db.commit()

    try:
        if dispatch_task_id:
            _publish_agent_run(
                run_id=retried.id,
                workspace_id=resolved_workspace_id,
                task_id=dispatch_task_id,
            )
        logger.info(
            "event_bus.agent_retried",
            original_run_id=str(run_id),
            retried_run_id=str(retried.id),
            agent_name=retried.agent_name,
        )
    except Exception as exc:
        logger.error(
            "event_bus.retry_enqueue_failed",
            retried_run_id=str(retried.id),
            error_type=safe_error_type(exc),
        )
    return retried
