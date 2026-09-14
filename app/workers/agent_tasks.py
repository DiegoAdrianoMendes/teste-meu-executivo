"""
Celery tasks do runtime de agentes.
"""
import asyncio
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from celery import Task
from sqlalchemy import and_, or_, select

from app.core.klm_runtime import external_delivery_allowed, model_api_execution_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.session import WorkerSessionLocal as AsyncSessionLocal
from app.db.tenant import set_workspace_context
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.human_override import HumanOverride
from app.services.agent_runtime_service import AgentRuntimeService
from app.services.platform_service import PlatformService
from app.services.workspace_policy_service import WorkspacePolicyService
from app.workers.celery_app import celery_app
from app.workers.fanout import (
    FanoutMetrics,
    bounded_batch_size,
    unique_workspace_ids,
)

FLOW_RECONCILABLE_AGENT_STATUSES = frozenset(
    {
        AgentRunStatus.completed,
        AgentRunStatus.failed,
        AgentRunStatus.waiting_human,
        AgentRunStatus.cancelled,
    }
)
AGENT_DISPATCH_LEASE_TIMEOUT = timedelta(minutes=2)
AGENT_RUNNING_STALE_TIMEOUT = timedelta(minutes=7)
AGENT_RECOVERY_WORKSPACE_BATCH_SIZE = 64
AGENT_RECOVERY_RUN_BATCH_SIZE = 100
LEGACY_AGENT_TASK_CLASSES = {
    "klm_sdr": "simple",
    "klm_analyst": "complex",
    "klm_drafter": "complex",
    "administrativo_ops": "medium",
    "secretaria_hibrida": "simple",
    "onboarding_ops": "medium",
}


def _is_flow_agent_run(run: AgentRun) -> bool:
    return bool((run.input_payload or {}).get("flow_run_id"))


def _schedule_flow_agent_settlement(run: AgentRun, *, countdown: int = 1) -> None:
    if not _is_flow_agent_run(run):
        return
    try:
        from app.workers.flow_tasks import settle_flow_agent_run

        settle_flow_agent_run.apply_async(
            args=[str(run.id), str(run.workspace_id)],
            countdown=countdown,
        )
    except Exception as error:
        logger.error(
            "agent_task.flow_settlement_enqueue_failed",
            agent_run_id=str(run.id),
            error_type=safe_error_type(error),
        )


async def _settle_linked_flow_run(db, run: AgentRun) -> None:
    if not _is_flow_agent_run(run):
        return
    try:
        from app.services.flow_service import FlowService

        await FlowService(db).settle_agent_run(run.id, run.workspace_id)
    except Exception as error:
        logger.error(
            "agent_task.flow_settlement_failed",
            agent_run_id=str(run.id),
            error_type=safe_error_type(error),
        )
        _schedule_flow_agent_settlement(run, countdown=1)


def _merge_policy_overrides(base_policy: dict, payload: dict) -> tuple[dict, Optional[str]]:
    agent_config = payload.get("agent_config") if isinstance(payload.get("agent_config"), dict) else {}
    merged = dict(base_policy)
    for source_key, target_key in (
        ("model", "model"),
        ("context_mode", "context_mode"),
        ("use_rag", "use_rag"),
        ("multimodal_enabled", "multimodal_enabled"),
        ("human_review_required", "human_review_required"),
    ):
        value = agent_config.get(source_key)
        if value not in (None, ""):
            merged[target_key] = value
    custom_prompt = agent_config.get("prompt") or None
    return merged, custom_prompt


async def _draft_social_sdr_reply(
    db,
    *,
    workspace_id: UUID,
    thread_id: UUID,
    channel: str,
    body: str,
    run_id: UUID,
) -> str:
    """Registra a resposta de qualificação do SDR social como mensagem local,
    nunca via transporte real (mesmo gate que protege `SocialCommentsPage`).

    Passa pelo `MetaPolicyGate` (mesma porta usada pelos workers de envio real,
    ver `meta_policy_gate.py`): dentro da janela de 24h vira rascunho
    `local_draft` (client_private, `aiGenerated=true`) pronto para revisão
    humana na aba "Leads quentes"; fora da janela vira nota interna, sem
    fingir que uma resposta ao cliente foi preparada.
    """
    from datetime import UTC, datetime

    from app.core.klm_runtime import external_delivery_allowed  # noqa: F401 (nunca usado p/ social)
    from app.models.klm_operations import (
        MessageAuthorType,
        MessageDeliveryStatus,
        MessageDirection,
        OmnichannelChannel,
        OmnichannelThread,
    )
    from app.models.omnichannel_v6 import OmnichannelThreadBinding
    from app.schemas.klm_operations import OmnichannelMessageCreate
    from app.services.klm_operations_service import KlmOperationsService
    from app.services.meta_policy_gate import MetaPolicyGate

    thread = await db.scalar(
        select(OmnichannelThread).where(
            OmnichannelThread.workspace_id == workspace_id,
            OmnichannelThread.id == thread_id,
        )
    )
    if thread is None:
        return "thread_not_found"

    binding = await db.scalar(
        select(OmnichannelThreadBinding)
        .where(
            OmnichannelThreadBinding.workspace_id == workspace_id,
            OmnichannelThreadBinding.thread_id == thread_id,
            OmnichannelThreadBinding.binding_state == "active",
        )
        .order_by(OmnichannelThreadBinding.created_at.desc())
        .limit(1)
    )

    decision = MetaPolicyGate.evaluate(
        channel,
        last_inbound_at=thread.last_inbound_at,
        now=datetime.now(UTC),
        author_type="agent",
        action_type="send_message",
    )

    audience = "client_private" if decision.allowed else "internal"
    body_text = body if decision.allowed else (
        f"[Nota interna — janela do {channel} fechada, revisão manual necessária]\n{body}"
    )

    try:
        message, _created = await KlmOperationsService(db).create_message(
            workspace_id,
            thread_id,
            None,
            OmnichannelMessageCreate(
                endpoint_id=binding.endpoint_id if binding else None,
                binding_id=binding.id if binding else None,
                channel=OmnichannelChannel(channel),
                direction=MessageDirection.outbound,
                author_type=MessageAuthorType.agent,
                author_label="SDR Jurídico (IA)",
                body=body_text,
                audience=audience,
                content_type="text",
                transport="local",
                idempotency_key=f"social-sdr-draft:{run_id}",
                delivery_status=MessageDeliveryStatus.local_draft,
                message_metadata={
                    "source": "klm_sdr_social_qualification",
                    "aiGenerated": True,
                    "agentRunId": str(run_id),
                    "policyGate": {
                        "allowed": decision.allowed,
                        "reasonCode": decision.reason_code,
                        "reason": decision.reason,
                    },
                },
            ),
        )
    except (LookupError, ValueError) as exc:
        logger.error(
            "agent_tasks.social_sdr_draft_failed",
            thread_id=str(thread_id),
            error_type=safe_error_type(exc),
        )
        return "draft_failed"

    metadata = dict(thread.thread_metadata or {})
    social_lead = dict(metadata.get("socialLead") or {})
    social_lead["lastDraft"] = {
        "messageId": str(message.id),
        "body": body_text,
        "audience": audience,
        "createdAt": datetime.now(UTC).isoformat(),
        "policyGate": {
            "allowed": decision.allowed,
            "reasonCode": decision.reason_code,
        },
    }
    metadata["socialLead"] = social_lead
    thread.thread_metadata = metadata

    await db.commit()
    return "local_draft" if decision.allowed else "policy_gated_internal_note"


def _ignored_delivery(run_id: UUID | str, run_status: AgentRunStatus | str) -> dict:
    status_value = run_status.value if isinstance(run_status, AgentRunStatus) else str(run_status)
    return {
        "status": "ignored",
        "agent_run_id": str(run_id),
        "run_status": status_value,
    }


async def _lock_run_for_delivery(
    db,
    *,
    run_id: UUID,
    workspace_id: UUID,
) -> tuple[Optional[AgentRun], Optional[dict]]:
    run = await db.scalar(
        select(AgentRun)
        .where(
            AgentRun.id == run_id,
            AgentRun.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update(skip_locked=True)
    )
    if run:
        return run, None

    existing = await db.scalar(
        select(AgentRun).where(
            AgentRun.id == run_id,
            AgentRun.workspace_id == workspace_id,
        )
    )
    if existing:
        result = _ignored_delivery(existing.id, existing.status)
        await db.commit()
        return None, result
    await db.commit()
    raise RuntimeError(f"AgentRun {run_id} não encontrado.")


async def _lock_owned_running_run(
    db,
    *,
    run_id: UUID,
    workspace_id: UUID,
    celery_task_id: str,
) -> Optional[AgentRun]:
    run = await db.scalar(
        select(AgentRun)
        .where(
            AgentRun.id == run_id,
            AgentRun.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if not run:
        return None
    override_id = await db.scalar(
        select(HumanOverride.id)
        .where(HumanOverride.agent_run_id == run.id)
        .limit(1)
    )
    if (
        run.status != AgentRunStatus.running
        or run.celery_task_id != celery_task_id
        or override_id is not None
    ):
        return None
    return run


async def _finalize_owned_agent_run(
    db,
    runtime: AgentRuntimeService,
    *,
    run_id: UUID,
    workspace_id: UUID,
    celery_task_id: str,
    status: AgentRunStatus,
    output_payload: Optional[dict] = None,
    reason: Optional[str] = None,
) -> Optional[AgentRun]:
    run = await _lock_owned_running_run(
        db,
        run_id=run_id,
        workspace_id=workspace_id,
        celery_task_id=celery_task_id,
    )
    if not run:
        await db.rollback()
        logger.info(
            "agent_task.stale_result_discarded",
            agent_run_id=str(run_id),
            celery_task_id=celery_task_id,
        )
        return None

    if status == AgentRunStatus.completed:
        await runtime.mark_completed(run, output_payload=output_payload)
    elif status == AgentRunStatus.waiting_human:
        await runtime.mark_waiting_human(
            run,
            output_payload=output_payload,
            reason=reason,
        )
    elif status == AgentRunStatus.failed:
        await runtime.mark_failed(run, reason or "Falha não especificada na execução do agente")
    else:
        raise ValueError(f"Status terminal de AgentRun não suportado: {status.value}")
    await db.commit()
    return run


async def _recover_agent_runs_async(
    *,
    session_factory=None,
    now: Optional[datetime] = None,
    workspace_ids: Optional[Iterable[UUID]] = None,
    publisher: Optional[Callable[..., None]] = None,
) -> dict[str, int]:
    from app.models.workspace import Workspace

    session_maker = session_factory or AsyncSessionLocal
    reference_now = now or datetime.now(timezone.utc)
    dispatch_cutoff = reference_now - AGENT_DISPATCH_LEASE_TIMEOUT
    running_cutoff = reference_now - AGENT_RUNNING_STALE_TIMEOUT
    workspace_limit = bounded_batch_size(AGENT_RECOVERY_WORKSPACE_BATCH_SIZE)
    metrics = FanoutMetrics("agent_runs.recovery", AGENT_RECOVERY_RUN_BATCH_SIZE)

    recovery_predicate = or_(
        and_(
            AgentRun.status == AgentRunStatus.queued,
            or_(
                AgentRun.celery_task_id.is_(None),
                AgentRun.updated_at <= dispatch_cutoff,
            ),
        ),
        and_(
            AgentRun.status == AgentRunStatus.running,
            or_(
                AgentRun.started_at <= running_cutoff,
                and_(
                    AgentRun.started_at.is_(None),
                    AgentRun.updated_at <= running_cutoff,
                ),
            ),
        ),
    )

    if workspace_ids is None:
        async with session_maker() as discovery_db:
            workspace_ids = list(
                (
                    await discovery_db.scalars(
                        select(AgentRun.workspace_id)
                        .join(Workspace, Workspace.id == AgentRun.workspace_id)
                        .where(
                            Workspace.deleted_at.is_(None),
                            Workspace.is_active.is_(True),
                            recovery_predicate,
                        )
                        .group_by(AgentRun.workspace_id)
                        .order_by(AgentRun.workspace_id)
                        .limit(workspace_limit)
                    )
                ).all()
            )
    else:
        workspace_ids = unique_workspace_ids(workspace_ids, limit=workspace_limit)

    workspace_ids = list(workspace_ids)
    metrics.discovery_rows = len(workspace_ids)
    metrics.discovered_workspaces = len(workspace_ids)

    intents: list[tuple[UUID, UUID, str]] = []
    waiting_human = 0
    failed = 0
    for workspace_id in workspace_ids:
        remaining = AGENT_RECOVERY_RUN_BATCH_SIZE - len(intents) - waiting_human
        if remaining <= 0:
            break
        resolved_workspace_id = UUID(str(workspace_id))
        async with session_maker() as db:
            await set_workspace_context(db, resolved_workspace_id)
            candidates = list(
                (
                    await db.scalars(
                        select(AgentRun)
                        .where(
                            AgentRun.workspace_id == resolved_workspace_id,
                            recovery_predicate,
                        )
                        .order_by(AgentRun.created_at, AgentRun.id)
                        .with_for_update(skip_locked=True)
                        .limit(remaining)
                    )
                ).all()
            )
            runtime = AgentRuntimeService(db)
            uncertain_runs: list[AgentRun] = []
            for run in candidates:
                if run.status == AgentRunStatus.running:
                    output = dict(run.output_payload or {})
                    billing = dict(output.get("billingReconciliation") or {})
                    billing.update(
                        {
                            "status": "reconciliation_required",
                            "requestStarted": True,
                            "reconciliationRequired": True,
                            "reason": "worker_lost_after_model_execution_started",
                        }
                    )
                    checkpoint = dict(output.get("modelCheckpoint") or {})
                    checkpoint.update(
                        {
                            "status": "execution_uncertain",
                            "requestStarted": True,
                            "billingStatus": "reconciliation_required",
                        }
                    )
                    await runtime.mark_waiting_human(
                        run,
                        output_payload={
                            **output,
                            "summary": (
                                "O worker foi interrompido depois de iniciar a execução. "
                                "O resultado e a cobrança da API precisam ser reconciliados."
                            ),
                            "executionUncertain": True,
                            "automaticRetryBlocked": True,
                            "billingReconciliation": billing,
                            "modelCheckpoint": checkpoint,
                        },
                        reason=(
                            "Execução interrompida após o início da chamada ao modelo; "
                            "retry automático bloqueado para evitar custo ou efeito duplicado."
                        ),
                    )
                    uncertain_runs.append(run)
                    waiting_human += 1
                    continue
                elif not run.celery_task_id:
                    run.celery_task_id = str(uuid4())
                run.updated_at = reference_now
                intents.append((run.id, resolved_workspace_id, run.celery_task_id))
            await db.commit()
            for uncertain_run in uncertain_runs:
                _schedule_flow_agent_settlement(uncertain_run, countdown=0)
                await _settle_linked_flow_run(db, uncertain_run)

    if publisher is None:
        from app.workers.event_bus import _publish_agent_run

        publisher = _publish_agent_run

    published = 0
    publish_failed = 0
    published_workspace_ids: set[UUID] = set()
    for run_id, workspace_id, task_id in intents:
        try:
            publisher(run_id=run_id, workspace_id=workspace_id, task_id=task_id)
            published += 1
            published_workspace_ids.add(workspace_id)
        except Exception as error:
            publish_failed += 1
            logger.error(
                "agent_run.recovery_publish_failed",
                agent_run_id=str(run_id),
                error_type=safe_error_type(error),
            )

    result = {
        "published": published,
        "publish_failed": publish_failed,
        "waiting_human": waiting_human,
        "failed": failed,
    }
    metrics.enqueued_workspaces = len(published_workspace_ids)
    metrics.processed_items = published + waiting_human
    metrics.publish_failures = publish_failed
    metrics.emit()
    logger.info("agent_run.recovery_completed", **result)
    return result


async def _execute_agent_run_async(
    agent_run_id: str,
    celery_task_id: str,
    *,
    workspace_id: UUID | str,
    session_factory=None,
) -> dict:
    session_maker = session_factory or AsyncSessionLocal
    resolved_run_id = UUID(agent_run_id)
    async with session_maker() as db:
        resolved_workspace_id = UUID(str(workspace_id))
        await set_workspace_context(db, resolved_workspace_id)

        runtime = AgentRuntimeService(db)
        run, delivery_result = await _lock_run_for_delivery(
            db,
            run_id=resolved_run_id,
            workspace_id=resolved_workspace_id,
        )
        if delivery_result:
            logger.info(
                "agent_task.delivery_busy_ignored",
                agent_run_id=agent_run_id,
                run_status=delivery_result["run_status"],
            )
            return delivery_result
        assert run is not None
        if run.status != AgentRunStatus.queued:
            logger.info(
                "agent_task.duplicate_delivery_ignored",
                agent_run_id=agent_run_id,
                status=run.status.value,
            )
            if run.status in FLOW_RECONCILABLE_AGENT_STATUSES:
                await db.commit()
                _schedule_flow_agent_settlement(run, countdown=0)
                await _settle_linked_flow_run(db, run)
            return _ignored_delivery(run.id, run.status)
        if run.celery_task_id and run.celery_task_id != celery_task_id:
            logger.info(
                "agent_task.stale_delivery_ignored",
                agent_run_id=agent_run_id,
                expected_task_id=run.celery_task_id,
                received_task_id=celery_task_id,
            )
            await db.commit()
            return _ignored_delivery(run.id, run.status)
        if not run.celery_task_id:
            run.celery_task_id = celery_task_id
            run.updated_at = datetime.now(timezone.utc)
        if not model_api_execution_allowed():
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
            _schedule_flow_agent_settlement(run)
            await _settle_linked_flow_run(db, run)
            return {
                "status": "blocked",
                "agent_run_id": agent_run_id,
                "run_status": AgentRunStatus.waiting_human.value,
            }

        stage_key = (run.input_payload or {}).get("stage_key") or (
            run.trigger_source.split(":", 1)[1] if run.trigger_source.startswith("column:") else "entrada"
        )
        policy = await WorkspacePolicyService(db).resolve_stage_policy(run.workspace_id, stage_key)

        await runtime.mark_running(run, celery_task_id=celery_task_id)
        await db.commit()

        run = await _lock_owned_running_run(
            db,
            run_id=resolved_run_id,
            workspace_id=resolved_workspace_id,
            celery_task_id=celery_task_id,
        )
        if not run:
            await db.rollback()
            return _ignored_delivery(resolved_run_id, AgentRunStatus.running)

        payload = run.input_payload or {}
        effective_policy, custom_prompt = _merge_policy_overrides(policy, payload)
        thread_id = run.thread_id or f"{run.agent_name}-{run.matter_id}"

        try:
            outcome_status = AgentRunStatus.completed
            outcome_reason: Optional[str] = None
            dispatch_drafter_output: Optional[dict] = None
            from app.agents.stubs.klm_analyst import run_analyst
            from app.agents.stubs.klm_drafter import run_drafter, run_drafter_with_feedback
            from app.agents.stubs.klm_onboarding_ops import run_onboarding_ops
            from app.agents.stubs.klm_sdr import run_sdr, run_sdr_on_message, run_sdr_on_social_message

            if run.agent_name == "klm_sdr" and payload.get("thread_id") and payload.get("inbound_text"):
                result = await run_sdr_on_social_message(
                    matter_id=UUID(str(run.matter_id)),
                    thread_id=UUID(payload["thread_id"]),
                    inbound_text=payload["inbound_text"],
                    workspace_id=resolved_workspace_id,
                    channel=str(payload.get("channel") or "instagram"),
                )
                delivery_status = "not_requested"
                external_actions_executed = 0
                if result:
                    delivery_status = await _draft_social_sdr_reply(
                        db,
                        workspace_id=resolved_workspace_id,
                        thread_id=UUID(payload["thread_id"]),
                        channel=str(payload.get("channel") or "instagram"),
                        body=result,
                        run_id=run.id,
                    )
                output_payload = {
                    "summary": result or "Rodada SDR social processada sem resposta adicional.",
                    "reply_text": result,
                    "source": "social_message",
                    "delivery_status": delivery_status,
                    "externalActionsExecuted": external_actions_executed,
                }
            elif run.agent_name == "klm_sdr" and payload.get("contact_id") and payload.get("inbound_text"):
                from app.models.whatsapp import WaQueueStatus, WhatsAppContact

                contact_id = UUID(payload["contact_id"])
                contact = await db.get(WhatsAppContact, contact_id)
                result = None
                if contact is not None and contact.queue_status == WaQueueStatus.human:
                    # S1: humano na conversa, o SDR nem roda (sem modelo, sem envio).
                    logger.info(
                        "agent_task.sdr_blocked_by_human",
                        agent_run_id=str(run.id),
                        contact_id=str(contact_id),
                    )
                    delivery_status = "blocked_by_human"
                    external_actions_executed = 0
                else:
                    result = await run_sdr_on_message(
                        matter_id=UUID(str(run.matter_id)),
                        contact_id=contact_id,
                        inbound_text=payload["inbound_text"],
                        workspace_id=resolved_workspace_id,
                    )
                if result:
                    if contact is None:
                        contact = await db.get(WhatsAppContact, contact_id)
                    else:
                        # Checkpoint pós-modelo: a escalada da mesma mensagem pode ter
                        # posto o contato em atendimento humano enquanto o modelo rodava.
                        await db.refresh(contact, attribute_names=["queue_status"])
                    if contact is None:
                        delivery_status = "contact_not_found"
                        external_actions_executed = 0
                    elif contact.queue_status == WaQueueStatus.human:
                        logger.info(
                            "agent_task.sdr_blocked_by_human",
                            agent_run_id=str(run.id),
                            contact_id=str(contact_id),
                            stage="after_model",
                        )
                        delivery_status = "blocked_by_human"
                        external_actions_executed = 0
                    else:
                        # Antes de auto-enviar, o SDR WhatsApp passa pelo mesmo
                        # MetaPolicyGate do caminho social: fora da janela de 24h
                        # não envia texto livre (viraria violação de política da
                        # Meta); degrada para rascunho local, como o social.
                        from app.models.whatsapp import (
                            WaMessageDirection,
                            WhatsAppMessage,
                        )
                        from app.services.meta_policy_gate import MetaPolicyGate

                        last_inbound_at = await db.scalar(
                            select(WhatsAppMessage.timestamp)
                            .where(
                                WhatsAppMessage.contact_id == contact_id,
                                WhatsAppMessage.direction == WaMessageDirection.inbound,
                            )
                            .order_by(WhatsAppMessage.timestamp.desc())
                            .limit(1)
                        )
                        policy = MetaPolicyGate.evaluate(
                            "whatsapp",
                            last_inbound_at=last_inbound_at,
                            now=datetime.now(timezone.utc),
                            author_type="agent",
                            action_type="send_message",
                        )
                        if external_delivery_allowed() and policy.allowed:
                            from app.services.whatsapp_service import WhatsAppService

                            svc = WhatsAppService(db)
                            sent = await svc._send_text_direct(
                                phone=contact.phone,
                                body=result,
                                workspace_id=contact.workspace_id,
                                idempotency_key=f"agent-run-reply:{run.id}",
                            )
                            delivery_status = "sent" if sent else "failed_or_uncertain"
                            external_actions_executed = 1 if sent else 0
                        elif external_delivery_allowed() and not policy.allowed:
                            delivery_status = "blocked_by_policy_window"
                            external_actions_executed = 0
                        else:
                            delivery_status = "local_draft"
                            external_actions_executed = 0
                elif delivery_status != "blocked_by_human":
                    delivery_status = "not_requested"
                    external_actions_executed = 0
                output_payload = {
                    "summary": result or "Rodada SDR processada sem resposta adicional.",
                    "reply_text": result,
                    "source": "whatsapp_message",
                    "delivery_status": delivery_status,
                    "externalActionsExecuted": external_actions_executed,
                }
            elif run.agent_name == "klm_sdr":
                output_payload = await run_sdr(
                    UUID(str(run.matter_id)),
                    thread_id,
                    workspace_id=resolved_workspace_id,
                    policy_overrides=effective_policy,
                    custom_prompt=custom_prompt,
                )
                if output_payload.get("status") == AgentRunStatus.waiting_human.value:
                    # Fail-closed vindo do stub (ex.: parse_failed) — o run espera humano.
                    outcome_status = AgentRunStatus.waiting_human
                    outcome_reason = (
                        output_payload.get("message")
                        or "Qualificação SDR aguardando revisão humana."
                    )
                elif (
                    effective_policy.get("human_review_required")
                    or effective_policy.get("mode") == "human_in_the_loop"
                ):
                    outcome_status = AgentRunStatus.waiting_human
                    outcome_reason = "Configuração do workspace exige revisão humana nesta etapa."
            elif run.agent_name == "klm_analyst":
                output_payload = await run_analyst(
                    UUID(str(run.matter_id)),
                    thread_id,
                    workspace_id=resolved_workspace_id,
                    policy_overrides=effective_policy,
                    custom_prompt=custom_prompt,
                )
                if (
                    output_payload.get("status") == AgentRunStatus.waiting_human.value
                    or effective_policy.get("human_review_required")
                    or effective_policy.get("mode") in {"human_in_the_loop", "ai_with_human_approval"}
                ):
                    outcome_status = AgentRunStatus.waiting_human
                    outcome_reason = output_payload.get("message") or "Análise aguardando revisão humana."
                recommendation = (output_payload or {}).get("recommendation", "")
                if recommendation == "proceed":
                    dispatch_drafter_output = output_payload
            elif run.agent_name == "klm_drafter":
                if payload.get("feedback"):
                    output_payload = await run_drafter_with_feedback(
                        UUID(str(run.matter_id)),
                        thread_id,
                        payload["feedback"],
                        workspace_id=resolved_workspace_id,
                        policy_overrides=effective_policy,
                        custom_prompt=custom_prompt,
                    )
                else:
                    output_payload = await run_drafter(
                        UUID(str(run.matter_id)),
                        thread_id,
                        workspace_id=resolved_workspace_id,
                        policy_overrides=effective_policy,
                        custom_prompt=custom_prompt,
                    )
                # A intenção ZapSign só existe quando a minuta foi de fato
                # persistida como documento do caso; sem artefato real, sem intenção.
                draft_document_id = (output_payload or {}).get("draft_document_id")
                if (output_payload or {}).get("draft_persisted") is True and draft_document_id:
                    output_payload = {
                        **(output_payload or {}),
                        "external_action_intent": {
                            "provider": "zapsign",
                            "action": "create_document",
                            "status": "awaiting_human_approval",
                            "draft_document_id": str(draft_document_id),
                        },
                    }
                outcome_status = AgentRunStatus.waiting_human
                outcome_reason = (
                    (output_payload or {}).get("message")
                    or "Minuta aguardando revisão humana."
                )
            elif run.agent_name in {
                "administrativo_ops",
                "secretaria_hibrida",
                "financeiro_ops",
                "pos_venda_agent",
                "assistente_juridico",
            }:
                output_payload = await run_onboarding_ops(
                    UUID(str(run.matter_id)),
                    thread_id,
                    workspace_id=resolved_workspace_id,
                    stage_key=stage_key,
                    agent_name=run.agent_name,
                    policy_overrides=effective_policy,
                    custom_prompt=custom_prompt,
                )
                if output_payload.get("status") == AgentRunStatus.waiting_human.value:
                    # Fail-closed vindo do stub (ex.: parse_failed) — o run espera humano.
                    outcome_status = AgentRunStatus.waiting_human
                    outcome_reason = (
                        output_payload.get("message")
                        or "Etapa operacional aguardando revisão humana."
                    )
                elif (
                    effective_policy.get("human_review_required")
                    or effective_policy.get("mode")
                    in {"human_in_the_loop", "ai_with_human_approval"}
                ):
                    outcome_status = AgentRunStatus.waiting_human
                    outcome_reason = (
                        output_payload.get("recommended_action")
                        or "Etapa operacional aguardando revisão humana."
                    )
            else:
                raise RuntimeError(f"Agente '{run.agent_name}' não suportado.")

            finalized_run = await _finalize_owned_agent_run(
                db,
                runtime,
                run_id=resolved_run_id,
                workspace_id=resolved_workspace_id,
                celery_task_id=celery_task_id,
                status=outcome_status,
                output_payload=output_payload,
                reason=outcome_reason,
            )
            if not finalized_run:
                return _ignored_delivery(resolved_run_id, AgentRunStatus.running)
            run = finalized_run

            if dispatch_drafter_output is not None:
                from app.workers.event_bus import dispatch_drafter

                await dispatch_drafter(
                    UUID(str(run.matter_id)),
                    analyst_output=dispatch_drafter_output,
                    db=db,
                    workspace_id=resolved_workspace_id,
                )

            _schedule_flow_agent_settlement(run)

            # Log token usage to PlatformTokenUsage for cost analytics
            await db.refresh(run)
            model_metadata = dict((run.output_payload or {}).get("modelMetadata") or {})
            if (
                run.input_tokens or run.output_tokens
            ) and not bool(model_metadata.get("usageAccounted")):
                try:
                    await PlatformService.log_agent_run_tokens(
                        db,
                        run,
                        task_class=str(
                            model_metadata.get("taskClass")
                            or LEGACY_AGENT_TASK_CLASSES.get(run.agent_name, "medium")
                        ),
                        request_id=str(
                            model_metadata.get("requestId") or f"agent-run:{run.id}"
                        ),
                        operation=f"agent.{run.agent_name}.legacy_backfill",
                        cached_input_tokens=int(
                            model_metadata.get("cachedInputTokens") or 0
                        ),
                        provider_request_id=str(
                            model_metadata.get("providerRequestId") or ""
                        ),
                        price_catalog_version=str(
                            model_metadata.get("priceCatalogVersion") or ""
                        ),
                        route_catalog_version=str(
                            model_metadata.get("routeCatalogVersion") or ""
                        ),
                    )
                    await db.commit()
                except Exception as log_exc:
                    logger.warning(
                        "agent_task.token_log_failed",
                        agent_run_id=agent_run_id,
                        error_type=safe_error_type(log_exc),
                    )

            await _settle_linked_flow_run(db, run)
            return {"status": "ok", "agent_run_id": agent_run_id}
        except Exception as exc:
            logger.error(
                "agent_task.execution_failed",
                agent_run_id=agent_run_id,
                error_type=safe_error_type(exc),
            )
            await db.rollback()
            try:
                failed_run = await _finalize_owned_agent_run(
                    db,
                    runtime,
                    run_id=resolved_run_id,
                    workspace_id=resolved_workspace_id,
                    celery_task_id=celery_task_id,
                    status=AgentRunStatus.failed,
                    reason=str(exc),
                )
            except Exception as persistence_error:
                await db.rollback()
                logger.error(
                    "agent_task.failure_persistence_failed",
                    agent_run_id=agent_run_id,
                    error_type=safe_error_type(persistence_error),
                )
                failed_run = None
            if failed_run:
                _schedule_flow_agent_settlement(failed_run)
                await _settle_linked_flow_run(db, failed_run)
            raise


@celery_app.task(
    name="app.workers.execute_agent_run",
    bind=True,
    base=Task,
    max_retries=0,
    soft_time_limit=240,
    time_limit=300,
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_agent_run(
    self,
    agent_run_id: str,
    workspace_id: str,
) -> dict:
    return asyncio.run(
        _execute_agent_run_async(
            agent_run_id=agent_run_id,
            celery_task_id=self.request.id or "",
            workspace_id=workspace_id,
        )
    )


@celery_app.task(
    name="app.workers.recover_agent_runs",
    acks_late=False,
    soft_time_limit=50,
    time_limit=60,
)
def recover_agent_runs() -> dict[str, int]:
    return asyncio.run(_recover_agent_runs_async())
