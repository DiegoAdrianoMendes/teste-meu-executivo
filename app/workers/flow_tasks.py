"""
Flow Tasks — Celery tasks para execução de flows e mensagens agendadas
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import opaque_ref, safe_error_type
from app.db.tenant import set_workspace_context
from app.db.worker_discovery import set_worker_discovery_context
from app.models.client import Client
from app.models.contract_billing import ContractBillingProposal
from app.models.entitlement import EntitlementKey
from app.models.finance_dimensional import FinanceTaxProfile
from app.models.matter import Matter
from app.models.storage_object import StorageObject
from app.models.task import Task, TaskPriority
from app.schemas.task import TaskCreate
from app.services.agent_intelligence_adapters import register_intelligence_adapters
from app.services.agent_node_executor import AgentExecutionContext, AgentNodeOutput
from app.services.entitlement_service import EntitlementEnforcementService
from app.services.flow_node_registry import FLOW_EXTERNAL_NODE_TYPES, FLOW_NODE_SPEC_BY_TYPE
from app.services.flow_runtime_primitives import (
    advance_loop,
    apply_data_node,
    edge_route_key,
    evaluate_flow_condition,
    get_context_value,
    resolve_expression,
    select_router_route,
)
from app.services.flow_sequence_service import (
    hold_or_skip_pending_template,
    normalize_sequence_bindings,
    normalize_sequence_delay,
    resolve_sequence_message,
    resolve_sequence_template_parameters,
    sequence_tariff,
    sequence_template_status,
)
from app.services.flow_trace import record_flow_trace
from app.services.task_service import TaskService
from app.services.whatsapp.helpers import phone_number_lookup_variants
from app.workers.celery_app import celery_app
from app.workers.fanout import (
    FanoutMetrics,
    bounded_batch_size,
    unique_workspace_ids,
)


class ExternalDeliveryBlockedError(RuntimeError):
    """O gate do runtime bloqueou uma tentativa de entrega externa."""


class FlowStepDispatchError(RuntimeError):
    """A intenção persistida existe, mas não pôde ser publicada no broker."""


class ExternalIntentBusyError(RuntimeError):
    """Outra execução ainda possui o lease da tentativa externa."""


class ExternalIntentUncertainError(RuntimeError):
    """O efeito pode ter sido aceito e exige reconciliação humana."""


class ExternalIntentFencedError(RuntimeError):
    """A execução perdeu o direito de concluir o efeito externo."""


@dataclass(frozen=True)
class ExternalDeliveryReceipt:
    provider_reference: str | None
    provider_supports_idempotency: bool
    raw_result: Any = None


EXTERNAL_FLOW_NODE_TYPES = FLOW_EXTERNAL_NODE_TYPES
FLOW_STEP_DISPATCHES_KEY = "flowStepDispatches"
CONTINUATION_SUCCESSOR_TOKEN_KEY = "continuationSuccessorToken"
FLOW_EXTERNAL_INTENTS_KEY = "flowExternalIntents"
FLOW_ACTIVE_EXTERNAL_INTENT_KEY = "flowActiveExternalIntent"
FLOW_EXTERNAL_INTENT_SEQUENCE_KEY = "flowExternalIntentSequence"
FLOW_NODE_RETRIES_KEY = "flowNodeRetries"
FLOW_ERROR_HISTORY_KEY = "flowErrorHistory"
FLOW_EXTERNAL_ATTEMPT_LEASE = timedelta(minutes=5)
SCHEDULED_MESSAGE_LEASE = timedelta(minutes=5)
FLOW_RECOVERY_LEASE = timedelta(minutes=2)
FLOW_RECOVERY_BATCH_SIZE = 100
register_intelligence_adapters()


async def _load_context_entity(
    db, workspace_id, node_type: str, config: dict, context: dict
) -> dict:
    """Load only a workspace-scoped, UI-safe projection for context nodes."""
    key = str(config.get("id") or config.get("contextId") or "")
    if node_type == "load_client":
        obj = await db.scalar(
            select(Client).where(Client.workspace_id == workspace_id, Client.id == key)
        )
        return {
            "client": {"label": getattr(obj, "name", None), "status": "active"} if obj else None
        }
    if node_type == "load_matter":
        obj = await db.scalar(
            select(Matter).where(Matter.workspace_id == workspace_id, Matter.id == key)
        )
        return {
            "matter": {
                "label": getattr(obj, "case_number", None),
                "status": str(getattr(obj, "status", "")),
            }
            if obj
            else None
        }
    if node_type == "load_task":
        obj = await db.scalar(select(Task).where(Task.workspace_id == workspace_id, Task.id == key))
        return {
            "task": {
                "label": getattr(obj, "title", None),
                "status": str(getattr(obj, "status", "")),
            }
            if obj
            else None
        }
    if node_type == "load_contract":
        obj = await db.scalar(
            select(ContractBillingProposal).where(
                ContractBillingProposal.workspace_id == workspace_id,
                ContractBillingProposal.id == key,
            )
        )
        return {
            "contract": {
                "label": getattr(obj, "source_document_name", None),
                "status": str(getattr(obj, "status", "")),
            }
            if obj
            else None
        }
    if node_type == "load_documents":
        rows = (
            await db.scalars(
                select(StorageObject).where(StorageObject.workspace_id == workspace_id).limit(50)
            )
        ).all()
        return {
            "documents": [
                {
                    "label": getattr(row, "original_filename", None),
                    "state": str(getattr(row, "state", "")),
                }
                for row in rows
            ]
        }
    if node_type == "load_tax_profile":
        obj = await db.scalar(
            select(FinanceTaxProfile).where(FinanceTaxProfile.workspace_id == workspace_id)
        )
        return {
            "taxProfile": {"regime": obj.regime, "municipality": obj.municipality} if obj else None
        }
    # Finance loaders use the already-authorized dimensional context; no raw
    # financial rows or identifiers are copied into an agent context here.
    if node_type == "load_finance":
        return {"finance": {"available": True, "source": "finance_dimensional"}}
    return {}


# These are deliberately finite operational waits, not retry intervals.  A
# provider/tool may still finish after the timeout, but the runtime will no
# longer leave a customer request without an accountable human.  Budget is
# handled immediately: spending must be an explicit owner decision, never a
# silent poll until a new billing period happens to arrive.
AGENT_RUNTIME_WAIT_TIMEOUTS: dict[str, timedelta] = {
    "waiting_tool": timedelta(minutes=15),
    "waiting_external": timedelta(minutes=30),
    "paused_budget": timedelta(seconds=0),
}


def _next_budget_period_start(now: datetime) -> datetime:
    """Return the first UTC instant of the next calendar budget period."""
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


async def _watchdog_runtime_agent_wait(
    db,
    *,
    workspace_id: UUID,
    agent_run: Any,
    now: datetime,
) -> bool:
    """Turn an expired runtime wait into one real, finite human handoff.

    The status transition and the HumanDecision are in the caller's one
    transaction.  Thus a committed ``waiting_human`` state always has an
    assignee, escalation path, owner in-app watch and durable phone delivery
    state.  A failed route terminates the run instead of creating a polling
    or phantom-human loop.
    """
    from sqlalchemy import select

    from app.models.agent_run import AgentRunStatus
    from app.models.flow import FlowRun
    from app.models.klm_operations import KlmAuditEvent
    from app.services.agent_runtime_service import AgentRuntimeService
    from app.services.flow_service import FlowService

    source_status = (
        agent_run.status.value
        if isinstance(agent_run.status, AgentRunStatus)
        else str(agent_run.status)
    )
    if source_status not in AGENT_RUNTIME_WAIT_TIMEOUTS:
        return False
    wait_started_at = agent_run.updated_at or agent_run.started_at or agent_run.created_at
    if wait_started_at.tzinfo is None:
        wait_started_at = wait_started_at.replace(tzinfo=UTC)
    if now - wait_started_at < AGENT_RUNTIME_WAIT_TIMEOUTS[source_status]:
        return False

    output = dict(agent_run.output_payload or {})
    runtime_wait = dict(output.get("runtimeWait") or {})
    # The value is only written after this durable transition succeeds.  It
    # doubles as an operator-visible explanation; the unique decision key is
    # the actual idempotency boundary.
    runtime_wait.update(
        {
            "sourceStatus": source_status,
            "waitStartedAt": wait_started_at.isoformat(),
            "watchdogEscalatedAt": now.isoformat(),
            "requiresHumanDecision": True,
        }
    )
    if source_status == AgentRunStatus.paused_budget.value:
        runtime_wait.update(
            {
                "scheduledResumeAt": _next_budget_period_start(now).isoformat(),
                "resumePolicy": "human_decision_required",
            }
        )
    output["runtimeWait"] = runtime_wait
    reason = {
        "waiting_tool": "Ferramenta não confirmou conclusão no prazo; decisão humana necessária.",
        "waiting_external": "Confirmação externa não chegou no prazo; decisão humana necessária.",
        "paused_budget": (
            "Orçamento de IA atingido; o responsável deve autorizar retomada, "
            "alterar orçamento ou encerrar a execução."
        ),
    }[source_status]
    decision_key = f"agent-run:{agent_run.id}:runtime-wait:{source_status}"

    try:
        # Flow-owned runs need the Flow checkpoint as well as AgentRun state;
        # otherwise a human decision would not know which continuation to
        # resume.  The checkpoint uses the original node/step and therefore
        # cannot be confused with an unrelated generic handoff.
        flow_run_id = (
            (agent_run.input_payload or {}).get("flow_run_id")
            if isinstance(agent_run.input_payload, dict)
            else None
        )
        agent_run.status = AgentRunStatus.waiting_human
        agent_run.completed_at = None
        agent_run.error_message = reason
        agent_run.output_payload = output
        await db.flush()
        if flow_run_id:
            flow_run = await db.scalar(
                select(FlowRun)
                .where(FlowRun.id == UUID(str(flow_run_id)), FlowRun.workspace_id == workspace_id)
                .with_for_update()
            )
            if flow_run is None:
                raise ValueError("linked_flow_run_unavailable")
            node_id = str((agent_run.input_payload or {}).get("flow_node_id") or "agent_wait")
            raw_step = (agent_run.input_payload or {}).get("flow_step") or 1
            try:
                execution_step = max(1, int(raw_step))
            except (TypeError, ValueError):
                execution_step = 1
            # FlowService derives the same opaque key deterministically.  It
            # owns the FlowRun checkpoint and the decision links both records.
            await FlowService(db)._create_human_checkpoint(
                flow_run,
                node_id=node_id,
                execution_step=execution_step,
                reason=reason,
                config={"required_roles": ["workspace_owner"], "sla_seconds": 3600},
                purpose=f"agent_{source_status}_timeout",
                agent_run_id=agent_run.id,
            )
        else:
            await AgentRuntimeService(db).ensure_human_decision(
                agent_run,
                decision_key=decision_key,
                options={
                    "approve": True,
                    "reject": True,
                    "reason": reason,
                    "purpose": f"agent_{source_status}_timeout",
                    # Generic runs are safely returned to the normal durable
                    # queue only after a named human approves the recovery.
                    "resumeAgentRunOnApprove": True,
                    "sourceWaitStatus": source_status,
                },
                sla_minutes=60,
            )
        db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=None,
                actor_type="system",
                actor_label="Watchdog de execução de agente",
                event_type="agent_runtime_wait_handoff_created",
                entity_type="agent_run",
                entity_id=agent_run.id,
                correlation_id=f"agent-runtime-wait:{agent_run.id}:{source_status}",
                summary="Espera de agente expirou e recebeu chamada humana finita.",
                after_data={
                    "fromStatus": source_status,
                    "toStatus": AgentRunStatus.waiting_human.value,
                    "decisionKey": decision_key,
                    "scheduledResumeAt": runtime_wait.get("scheduledResumeAt"),
                },
            )
        )
        return True
    except Exception as error:
        # There is no permissible state in which an expired action is merely
        # shown as waiting after its call could not be persisted.  Terminate
        # it and leave a clear audit trail for the office owner/dashboard.
        agent_run.status = AgentRunStatus.failed
        agent_run.completed_at = now
        agent_run.error_message = (
            "Watchdog não pôde criar chamada humana canônica para espera de agente: "
            f"{safe_error_type(error)}"
        )
        db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=None,
                actor_type="system",
                actor_label="Watchdog de execução de agente",
                event_type="agent_runtime_wait_handoff_failed",
                entity_type="agent_run",
                entity_id=agent_run.id,
                correlation_id=f"agent-runtime-wait:{agent_run.id}:{source_status}",
                summary="Espera expirou sem rota humana válida; execução encerrada.",
                after_data={"status": "failed", "errorCode": safe_error_type(error)},
            )
        )
        return False


def _runtime_node_contract_error(
    node_type: str, context: dict[str, Any] | None = None
) -> str | None:
    """Return a fail-closed contract error before any node side effect."""
    spec = FLOW_NODE_SPEC_BY_TYPE.get(str(node_type))
    if spec is None:
        return f"Contrato de nó desconhecido: {node_type}"
    if spec.get("availability") == "unavailable":
        return spec.get("unavailable_reason", "Nó não disponível neste ambiente.")
    required_permission = spec.get("required_permission")
    manifest = context or {}
    root_manifest = manifest.get("isPlatformRoot") is True
    if required_permission and not root_manifest and required_permission not in set(
        manifest.get("permissions") or []
    ):
        return f"Permissão ausente para nó {node_type}: {required_permission}"
    if spec.get("side_effect") == "external":
        if spec.get("approval_policy") in {None, "none"}:
            return f"Nó externo sem política de aprovação: {node_type}"
        if not spec.get("human_fallback"):
            return f"Nó externo sem fallback humano: {node_type}"
        if spec.get("idempotency_policy") in {None, "not_applicable"}:
            return f"Nó externo sem idempotência: {node_type}"
    return None


SCHEDULED_WORKSPACE_BATCH_SIZE = 64
SCHEDULED_MESSAGE_TOTAL_BATCH_SIZE = 200


def _inject_fault(point: str, **details: Any) -> None:
    """Test hook for simulating process death at durable protocol boundaries."""


def _as_utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _flow_json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _flow_json_safe(value.model_dump())
    if is_dataclass(value):
        return _flow_json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _flow_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_flow_json_safe(item) for item in value]
    if isinstance(value, (UUID, Decimal, date, datetime)):
        return str(value)
    return value


def _lease_expired(value: datetime | str | None, *, now: datetime | None = None) -> bool:
    lease_expires_at = _as_utc(value)
    return lease_expires_at is None or lease_expires_at <= (now or datetime.now(UTC))


def _callable_declares_keyword(callback: Callable[..., Any], keyword: str) -> bool:
    try:
        return keyword in inspect.signature(callback).parameters
    except (TypeError, ValueError):
        return False


def _callable_accepts_keyword(callback: Callable[..., Any], keyword: str) -> bool:
    try:
        parameters = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        return False
    return keyword in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def _provider_reference(result: Any) -> str | None:
    if isinstance(result, ExternalDeliveryReceipt):
        return result.provider_reference
    for attribute in ("wamid", "token", "id"):
        value = getattr(result, attribute, None)
        if value:
            return str(value)
    if isinstance(result, dict):
        for key in ("wamid", "messageId", "message_id", "token", "id"):
            if result.get(key):
                return str(result[key])
        messages = result.get("messages")
        if isinstance(messages, list) and messages:
            first = messages[0]
            if isinstance(first, dict) and first.get("id"):
                return str(first["id"])
    return None


def _flow_external_intents(
    context: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    raw = (context or {}).get(FLOW_EXTERNAL_INTENTS_KEY)
    if not isinstance(raw, dict):
        return {}
    return {str(key): dict(record) for key, record in raw.items() if isinstance(record, dict)}


def _active_flow_external_intent(
    context: dict[str, Any] | None,
    *,
    node_id: str | None = None,
) -> tuple[str, dict[str, Any]] | None:
    active_key = str((context or {}).get(FLOW_ACTIVE_EXTERNAL_INTENT_KEY) or "")
    if not active_key:
        return None
    record = _flow_external_intents(context).get(active_key)
    if not record or record.get("consumedAt"):
        return None
    if node_id is not None and str(record.get("nodeId") or "") != str(node_id):
        return None
    return active_key, record


def _prepare_flow_external_intent(
    run,
    *,
    node_id: str,
    node_type: str,
    flow_step: int,
) -> tuple[str, dict[str, Any]]:
    existing = _active_flow_external_intent(run.context, node_id=node_id)
    if existing:
        return existing

    context = dict(run.context or {})
    if context.get(FLOW_ACTIVE_EXTERNAL_INTENT_KEY):
        raise ValueError("Há uma intenção externa ativa para outro nó do flow")

    sequence = int(context.get(FLOW_EXTERNAL_INTENT_SEQUENCE_KEY, 0) or 0) + 1
    intent_key = f"flow-effect:{uuid4()}"
    now = datetime.now(UTC)
    record = {
        "intentKey": intent_key,
        "idempotencyKey": intent_key,
        "revisionId": str(run.revision_id),
        "nodeId": node_id,
        "nodeType": node_type,
        "flowStep": flow_step,
        "sequence": sequence,
        "state": "prepared",
        "preparedAt": now.isoformat(),
        "attemptCount": 0,
        "reconciliationRequired": False,
    }
    intents = _flow_external_intents(context)
    intents[intent_key] = record
    context[FLOW_EXTERNAL_INTENTS_KEY] = intents
    context[FLOW_ACTIVE_EXTERNAL_INTENT_KEY] = intent_key
    context[FLOW_EXTERNAL_INTENT_SEQUENCE_KEY] = sequence
    run.context = context
    return intent_key, record


def _update_flow_external_intent(
    run,
    intent_key: str,
    **updates: Any,
) -> dict[str, Any]:
    context = dict(run.context or {})
    intents = _flow_external_intents(context)
    record = intents.get(intent_key)
    if not record:
        raise ExternalIntentFencedError("Intenção externa não encontrada")
    record.update(updates)
    intents[intent_key] = record
    context[FLOW_EXTERNAL_INTENTS_KEY] = intents
    run.context = context
    return record


def _consume_flow_external_intent(run, intent_key: str) -> None:
    context = dict(run.context or {})
    intents = _flow_external_intents(context)
    record = intents.get(intent_key)
    if not record or record.get("state") != "confirmed":
        raise ExternalIntentFencedError("Efeito externo não possui checkpoint confirmado")
    record["consumedAt"] = datetime.now(UTC).isoformat()
    intents[intent_key] = record
    context[FLOW_EXTERNAL_INTENTS_KEY] = intents
    if context.get(FLOW_ACTIVE_EXTERNAL_INTENT_KEY) == intent_key:
        context.pop(FLOW_ACTIVE_EXTERNAL_INTENT_KEY, None)
    context.pop("uncertainExternalAction", None)
    run.context = context


def _pause_flow_for_uncertain_external_intent(
    run,
    intent_key: str,
    *,
    reason: str,
) -> None:
    now = datetime.now(UTC)
    record = _update_flow_external_intent(
        run,
        intent_key,
        state="uncertain",
        uncertainAt=now.isoformat(),
        reconciliationRequired=True,
        uncertaintyReason=reason,
        attemptLeaseExpiresAt=None,
    )
    context = dict(run.context or {})
    context["uncertainExternalAction"] = {
        "intentKey": intent_key,
        "nodeId": record.get("nodeId"),
        "nodeType": record.get("nodeType"),
        "state": "uncertain",
        "reconciliationRequired": True,
        "detectedAt": now.isoformat(),
    }
    run.context = context
    # This helper only fences the uncertain external effect.  It must not put
    # the run in ``waiting_human`` by itself: the caller persists a canonical
    # HumanDecision (assignee, finite escalation route, SLA and owner alerts)
    # through ``_call_human_before_waiting`` before that state is allowed.
    run.status = "active"
    run.completed_at = None
    run.paused_at = now
    run.pause_reason = reason
    run.error_message = None


async def _call_human_before_waiting(
    db,
    run,
    *,
    node_id: str,
    execution_step: int,
    reason: str,
    config: dict[str, Any] | None = None,
    purpose: str = "review",
) -> Any:
    """Persist a finite human call before a FlowRun may enter waiting_human.

    If routing cannot identify and notify an accountable human plus the
    workspace owner, this helper raises and the enclosing node fails.  It
    therefore cannot create an invisible or infinite human wait.
    """
    from app.services.agent_platform_service import HumanRoutingService

    routing = dict(config or {})
    requested_user_id = routing.get("user_id") or routing.get("assigned_user_id")
    decision_key = f"{run.id}:{node_id}:{execution_step}:{purpose}"
    decision = await HumanRoutingService(db).create_decision(
        workspace_id=run.workspace_id,
        agent_run_id=None,
        flow_run_id=run.id,
        decision_key=decision_key,
        options={
            **dict(routing.get("options") or {}),
            "reason": reason,
            "purpose": purpose,
        },
        required_roles=(
            list(routing.get("required_roles") or [])
            if routing.get("required_roles") or routing.get("required_skills") or requested_user_id
            else ["workspace_owner"]
        ),
        required_skills=list(routing.get("required_skills") or []),
        requested_user_id=UUID(str(requested_user_id)) if requested_user_id else None,
        sla_minutes=max(1, min(int(routing.get("sla_seconds", 3600)) // 60, 10080)),
        max_hops=int(routing.get("max_hops", 4)),
        allow_active_flow_run=True,
    )
    context = dict(run.context or {})
    checkpoints = dict(context.get("humanCheckpoints") or {})
    checkpoints[node_id] = {
        "nodeType": purpose,
        "status": "pending",
        "decisionKey": decision_key,
        "decisionId": str(decision.id),
        "assignedUserId": str(decision.assigned_user_id),
        "ownerUserId": str(decision.owner_user_id),
        "dueAt": decision.due_at.isoformat() if decision.due_at else None,
        "routingPath": list(decision.routing_path),
        "reason": reason,
    }
    context["humanCheckpoints"] = checkpoints
    run.context = context
    run.status = "waiting_human"
    run.completed_at = None
    run.paused_at = datetime.now(UTC)
    run.pause_reason = reason
    return decision


def _require_external_delivery(action: str) -> None:
    if not external_delivery_allowed():
        raise ExternalDeliveryBlockedError(
            f"{action} bloqueado: entregas externas estão desabilitadas"
        )


def _run_async(coro):
    """Helper para rodar coroutines em tasks Celery síncronas."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _node_config(node: dict[str, Any]) -> dict[str, Any]:
    config = node.get("config")
    if isinstance(config, dict):
        return config
    data = node.get("data")
    if isinstance(data, dict):
        return data
    return {}


def _outgoing_flow_edges(
    edges: list[dict[str, Any]],
    node_id: str,
) -> list[dict[str, Any]]:
    return [edge for edge in edges if str(edge.get("source") or "") == node_id]


def _next_flow_target_for_route(
    edges: list[dict[str, Any]],
    node_id: str,
    route_key: str,
) -> str | None:
    outgoing = _outgoing_flow_edges(edges, node_id)
    matching = [edge for edge in outgoing if edge_route_key(edge) == route_key]
    if not matching and route_key != "fallback":
        matching = [edge for edge in outgoing if edge_route_key(edge) == "fallback"]
    selected = (matching or outgoing or [None])[0]
    return str(selected.get("target")) if selected and selected.get("target") else None


def _max_flow_steps(trigger_config: dict[str, Any] | None) -> int:
    raw = (trigger_config or {}).get("maxSteps", (trigger_config or {}).get("max_steps", 100))
    try:
        return max(1, min(int(raw), 200))
    except (TypeError, ValueError):
        return 100


def _flow_step_dispatches(context: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    raw = (context or {}).get(FLOW_STEP_DISPATCHES_KEY)
    if not isinstance(raw, dict):
        return {}
    return {str(token): dict(record) for token, record in raw.items() if isinstance(record, dict)}


def _next_pending_flow_step_dispatch(
    context: dict[str, Any] | None,
    token: str | None,
) -> tuple[str, dict[str, Any]] | None:
    dispatches = _flow_step_dispatches(context)
    current_token = str(token or "")
    visited: set[str] = set()
    while current_token and current_token not in visited:
        visited.add(current_token)
        record = dispatches.get(current_token)
        if not record:
            return None
        if not record.get("consumedAt"):
            return current_token, record
        current_token = str(record.get("successorToken") or "")
    return None


def _flow_step_dispatch_countdown(record: dict[str, Any]) -> int:
    raw_available_at = record.get("availableAt")
    if not raw_available_at:
        return 0
    try:
        available_at = datetime.fromisoformat(str(raw_available_at))
    except ValueError:
        return 0
    if available_at.tzinfo is None:
        available_at = available_at.replace(tzinfo=UTC)
    remaining = (available_at.astimezone(UTC) - datetime.now(UTC)).total_seconds()
    return max(0, math.ceil(remaining))


def _publish_flow_step_dispatch(
    *,
    flow_run_id: str,
    workspace_id: UUID,
    token: str,
    record: dict[str, Any],
) -> None:
    try:
        execute_flow_run_step.apply_async(
            args=[flow_run_id, str(workspace_id), token],
            countdown=_flow_step_dispatch_countdown(record),
        )
    except Exception as error:
        logger.warning(
            "flow_run.step_dispatch_publish_failed",
            run_id=flow_run_id,
            dispatch_token=token,
            error_type=safe_error_type(error),
        )
        raise FlowStepDispatchError("Falha ao publicar a continuação persistida do flow") from error


def _record_flow_step_dispatch(
    run,
    *,
    source: tuple[str, str],
    target_node_id: str,
    available_at: datetime,
) -> tuple[str, dict[str, Any]]:
    from app.services.flow_service import FLOW_AGENT_EXECUTIONS_KEY

    context = dict(run.context or {})
    dispatches = _flow_step_dispatches(context)
    source_kind, source_id = source

    if source_kind == "checkpoint":
        raw_executions = context.get(FLOW_AGENT_EXECUTIONS_KEY)
        executions = dict(raw_executions) if isinstance(raw_executions, dict) else {}
        raw_source_record = executions.get(source_id)
        source_record = dict(raw_source_record) if isinstance(raw_source_record, dict) else None
        successor_key = CONTINUATION_SUCCESSOR_TOKEN_KEY
    else:
        executions = None
        raw_source_record = dispatches.get(source_id)
        source_record = dict(raw_source_record) if isinstance(raw_source_record, dict) else None
        successor_key = "successorToken"

    if source_record is None:
        raise ValueError("Origem da continuação persistida não foi encontrada")

    existing_token = str(source_record.get(successor_key) or "")
    if existing_token:
        existing_record = dispatches.get(existing_token)
        if not existing_record or str(existing_record.get("nodeId") or "") != target_node_id:
            raise ValueError("Intenção persistida de continuação está inconsistente")
        return existing_token, existing_record

    token = f"flow-step-dispatch:{uuid4()}"
    record = {
        "nodeId": target_node_id,
        "state": "pending",
        "createdAt": datetime.now(UTC).isoformat(),
        "availableAt": available_at.astimezone(UTC).isoformat(),
        "sourceType": source_kind,
        "sourceId": source_id,
    }
    source_record[successor_key] = token
    dispatches[token] = record
    context[FLOW_STEP_DISPATCHES_KEY] = dispatches

    if source_kind == "checkpoint":
        assert executions is not None
        executions[source_id] = source_record
        context[FLOW_AGENT_EXECUTIONS_KEY] = executions
    else:
        dispatches[source_id] = source_record
        context[FLOW_STEP_DISPATCHES_KEY] = dispatches

    run.context = context
    return token, record


def _record_root_flow_step_dispatch(
    run,
    *,
    target_node_id: str,
    available_at: datetime,
    source_type: str,
) -> tuple[str, dict[str, Any]]:
    """Persist an initial/manual dispatch before the broker is contacted."""
    context = dict(run.context or {})
    dispatches = _flow_step_dispatches(context)

    for token, existing in dispatches.items():
        if (
            not existing.get("consumedAt")
            and existing.get("state", "pending") == "pending"
            and str(existing.get("nodeId") or "") == target_node_id
        ):
            existing = {
                **existing,
                "availableAt": available_at.astimezone(UTC).isoformat(),
                "sourceType": source_type,
            }
            dispatches[token] = existing
            context[FLOW_STEP_DISPATCHES_KEY] = dispatches
            run.context = context
            return token, existing

    token = f"flow-step-dispatch:{uuid4()}"
    record = {
        "nodeId": target_node_id,
        "state": "pending",
        "createdAt": datetime.now(UTC).isoformat(),
        "availableAt": available_at.astimezone(UTC).isoformat(),
        "sourceType": source_type,
        "sourceId": None,
    }
    dispatches[token] = record
    context[FLOW_STEP_DISPATCHES_KEY] = dispatches
    run.context = context
    return token, record


def _consume_error_dispatch_source(
    run,
    continuation_token: str | None,
) -> tuple[str, str] | None:
    """Fence the failed delivery before persisting its recovery successor."""
    if not continuation_token:
        return None

    from app.services.flow_service import FLOW_AGENT_EXECUTIONS_KEY

    now = datetime.now(UTC).isoformat()
    context = dict(run.context or {})
    dispatches = _flow_step_dispatches(context)
    dispatch = dispatches.get(continuation_token)
    if dispatch is not None:
        dispatch.update({"state": "consumed", "consumedAt": dispatch.get("consumedAt") or now})
        dispatches[continuation_token] = dispatch
        context[FLOW_STEP_DISPATCHES_KEY] = dispatches
        run.context = context
        return "dispatch", continuation_token

    raw_executions = context.get(FLOW_AGENT_EXECUTIONS_KEY)
    executions = dict(raw_executions) if isinstance(raw_executions, dict) else {}
    for agent_run_id, raw_checkpoint in executions.items():
        if not isinstance(raw_checkpoint, dict):
            continue
        checkpoint = dict(raw_checkpoint)
        if str(checkpoint.get("continuationToken") or "") != continuation_token:
            continue
        checkpoint["continuationConsumedAt"] = checkpoint.get("continuationConsumedAt") or now
        executions[str(agent_run_id)] = checkpoint
        context[FLOW_AGENT_EXECUTIONS_KEY] = executions
        run.context = context
        return "checkpoint", str(agent_run_id)
    return None


def _record_error_recovery_dispatch(
    run,
    *,
    source: tuple[str, str] | None,
    target_node_id: str,
    available_at: datetime,
    source_type: str,
) -> tuple[str, dict[str, Any]]:
    if source:
        return _record_flow_step_dispatch(
            run,
            source=source,
            target_node_id=target_node_id,
            available_at=available_at,
        )
    return _record_root_flow_step_dispatch(
        run,
        target_node_id=target_node_id,
        available_at=available_at,
        source_type=source_type,
    )


def _flow_error_step(run, *, node_id: str) -> int:
    context = dict(run.context or {})
    active_intent = _active_flow_external_intent(context, node_id=node_id)
    if active_intent:
        try:
            return max(1, int(active_intent[1].get("flowStep") or 1))
        except (TypeError, ValueError):
            return 1
    try:
        return max(1, int(context.get("flowStepCount", 0) or 0) + 1)
    except (TypeError, ValueError):
        return 1


def _remember_flow_error(
    run,
    *,
    node_id: str,
    node_type: str,
    error: Exception,
    strategy: str,
    requested_strategy: str,
    attempt: int,
    max_retries: int,
    status: str,
    occurred_at: datetime,
    automatic_retry_blocked: bool = False,
) -> dict[str, Any]:
    message = str(error).strip() or safe_error_type(error)
    entry = {
        "errorId": str(uuid4()),
        "nodeId": node_id,
        "nodeType": node_type,
        "errorType": safe_error_type(error),
        "message": message[:1000],
        "strategy": strategy,
        "requestedStrategy": requested_strategy,
        "attempt": attempt,
        "maxRetries": max_retries,
        "status": status,
        "occurredAt": occurred_at.isoformat(),
    }
    if automatic_retry_blocked:
        entry["automaticRetryBlocked"] = True

    context = dict(run.context or {})
    raw_history = context.get(FLOW_ERROR_HISTORY_KEY)
    history = (
        [dict(item) for item in raw_history if isinstance(item, dict)]
        if isinstance(raw_history, list)
        else []
    )
    history.append(entry)
    context[FLOW_ERROR_HISTORY_KEY] = history[-50:]
    context["lastFlowError"] = entry
    run.context = context
    return entry


def _mark_flow_node_recovered(run, *, node_id: str) -> None:
    context = dict(run.context or {})
    raw_retries = context.get(FLOW_NODE_RETRIES_KEY)
    retries = dict(raw_retries) if isinstance(raw_retries, dict) else {}
    retries.pop(node_id, None)
    if retries:
        context[FLOW_NODE_RETRIES_KEY] = retries
    else:
        context.pop(FLOW_NODE_RETRIES_KEY, None)

    last_error = context.get("lastFlowError")
    if isinstance(last_error, dict) and str(last_error.get("nodeId") or "") == node_id:
        recovered = {
            **last_error,
            "status": "recovered",
            "recoveredAt": datetime.now(UTC).isoformat(),
        }
        context["lastFlowError"] = recovered
        raw_history = context.get(FLOW_ERROR_HISTORY_KEY)
        if isinstance(raw_history, list):
            context[FLOW_ERROR_HISTORY_KEY] = [
                (
                    recovered
                    if isinstance(item, dict) and item.get("errorId") == recovered.get("errorId")
                    else item
                )
                for item in raw_history
            ][-50:]
    run.context = context


async def _lock_flow_run(db, run_id: UUID, workspace_id: UUID):
    from sqlalchemy import select

    from app.models.flow import FlowRun

    return await db.scalar(
        select(FlowRun)
        .where(
            FlowRun.id == run_id,
            FlowRun.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )


async def _claim_flow_external_attempt(
    db,
    run,
    *,
    intent_key: str,
    provider_supports_idempotency: bool,
) -> str:
    active = _active_flow_external_intent(
        run.context,
        node_id=str(run.current_node_id or ""),
    )
    if not active or active[0] != intent_key:
        raise ExternalIntentFencedError("Intenção externa não está mais ativa")

    record = active[1]
    state = str(record.get("state") or "")
    if state == "confirmed":
        raise ExternalIntentFencedError("Efeito externo já confirmado")
    if state == "uncertain":
        raise ExternalIntentUncertainError("Efeito externo aguarda reconciliação")
    if state == "dispatching":
        if not _lease_expired(record.get("attemptLeaseExpiresAt")):
            raise ExternalIntentBusyError("Tentativa externa ainda possui lease válido")
        prior_support = bool(record.get("providerSupportsIdempotency"))
        if not prior_support or not provider_supports_idempotency:
            reason = (
                "Entrega externa incerta após perda do checkpoint; "
                "reconciliação humana obrigatória."
            )
            _pause_flow_for_uncertain_external_intent(
                run,
                intent_key,
                reason=reason,
            )
            await _call_human_before_waiting(
                db,
                run,
                node_id=str(run.current_node_id or record.get("nodeId") or "external"),
                execution_step=int(record.get("flowStep", 0) or 0),
                reason=reason,
                purpose="external_reconciliation",
            )
            await db.commit()
            raise ExternalIntentUncertainError(reason)
    elif state != "prepared":
        raise ExternalIntentFencedError(f"Estado de intenção externa inválido: {state}")

    now = datetime.now(UTC)
    attempt_token = str(uuid4())
    _update_flow_external_intent(
        run,
        intent_key,
        state="dispatching",
        attemptToken=attempt_token,
        attemptedAt=now.isoformat(),
        attemptLeaseExpiresAt=(now + FLOW_EXTERNAL_ATTEMPT_LEASE).isoformat(),
        attemptCount=int(record.get("attemptCount", 0) or 0) + 1,
        providerSupportsIdempotency=provider_supports_idempotency,
        reconciliationRequired=False,
    )
    await db.commit()
    _inject_fault(
        "flow_external.after_attempt_checkpoint",
        intent_key=intent_key,
        attempt_token=attempt_token,
    )
    return attempt_token


async def _checkpoint_flow_external_success(
    db,
    *,
    run_id: UUID,
    workspace_id: UUID,
    node_id: str,
    intent_key: str,
    attempt_token: str,
    provider_reference: str | None,
    context_updates: dict[str, Any] | None = None,
) -> Any | None:
    run = await _lock_flow_run(db, run_id, workspace_id)
    if not run or run.status != "active" or str(run.current_node_id or "") != node_id:
        return None
    active = _active_flow_external_intent(run.context, node_id=node_id)
    if not active or active[0] != intent_key:
        return None
    record = active[1]
    if (
        record.get("state") != "dispatching"
        or str(record.get("attemptToken") or "") != attempt_token
    ):
        return None

    _update_flow_external_intent(
        run,
        intent_key,
        state="confirmed",
        confirmedAt=datetime.now(UTC).isoformat(),
        providerReference=provider_reference,
        attemptLeaseExpiresAt=None,
        reconciliationRequired=False,
    )
    if context_updates:
        run.context = {**(run.context or {}), **context_updates}
    await db.commit()
    _inject_fault(
        "flow_external.after_success_checkpoint",
        intent_key=intent_key,
        attempt_token=attempt_token,
    )
    return run


async def _record_flow_external_attempt_failure(
    db,
    *,
    run_id: UUID,
    workspace_id: UUID,
    node_id: str,
    intent_key: str,
    attempt_token: str,
    provider_supports_idempotency: bool,
    error: Exception,
) -> None:
    run = await _lock_flow_run(db, run_id, workspace_id)
    if not run or str(run.current_node_id or "") != node_id:
        return
    active = _active_flow_external_intent(run.context, node_id=node_id)
    if not active or active[0] != intent_key:
        return
    record = active[1]
    if str(record.get("attemptToken") or "") != attempt_token:
        return

    if not provider_supports_idempotency:
        reason = (
            "Entrega externa incerta após falha sem idempotência do provider; "
            "reconciliação humana obrigatória."
        )
        _pause_flow_for_uncertain_external_intent(
            run,
            intent_key,
            reason=reason,
        )
        await _call_human_before_waiting(
            db,
            run,
            node_id=node_id,
            execution_step=int(record.get("flowStep", 0) or 0),
            reason=reason,
            purpose="external_reconciliation",
        )
    else:
        _update_flow_external_intent(
            run,
            intent_key,
            state="failed",
            failedAt=datetime.now(UTC).isoformat(),
            failureType=safe_error_type(error),
            attemptLeaseExpiresAt=None,
        )
        run.status = "failed"
        run.completed_at = None
        run.error_message = str(error)
    await db.commit()


async def _dispatch_whatsapp_with_protocol(
    workspace_id: UUID,
    phone: str,
    message: str,
    db,
    *,
    idempotency_key: str,
    before_dispatch: Callable[[bool], Awaitable[None]],
) -> Any:
    kwargs: dict[str, Any] = {}
    if _callable_declares_keyword(_send_whatsapp, "before_dispatch"):
        kwargs["before_dispatch"] = before_dispatch
        if _callable_declares_keyword(_send_whatsapp, "idempotency_key"):
            kwargs["idempotency_key"] = idempotency_key
    else:
        await before_dispatch(False)
    return await _send_whatsapp(workspace_id, phone, message, db, **kwargs)


async def _dispatch_whatsapp_template_with_protocol(
    workspace_id: UUID,
    phone: str,
    db,
    *,
    template_id: UUID | None,
    template_name: str,
    template_language: str,
    bindings: list[dict[str, str]] | None,
    context: dict[str, Any],
    connection_id: UUID | None,
    before_dispatch: Callable[[bool], Awaitable[None]],
) -> ExternalDeliveryReceipt:
    """Dispatch a sequence stage through Meta's official template endpoint.

    The template and connection are resolved by both workspace and persisted
    identifiers.  This is intentionally separate from ``send_text``: an
    approved template remains eligible outside the 24-hour free-text window.
    """
    _require_external_delivery("Envio de template WhatsApp")
    from app.models.whatsapp_campaign import MetaTemplateStatus, WhatsAppMetaTemplate
    from app.services.whatsapp.errors import WhatsAppProviderError
    from app.services.whatsapp_service import WhatsAppService

    template = None
    if template_id:
        template = await db.scalar(
            select(WhatsAppMetaTemplate).where(
                WhatsAppMetaTemplate.id == template_id,
                WhatsAppMetaTemplate.workspace_id == workspace_id,
                WhatsAppMetaTemplate.removed_at_source_at.is_(None),
            )
        )
    else:
        template = await db.scalar(
            select(WhatsAppMetaTemplate).where(
                WhatsAppMetaTemplate.workspace_id == workspace_id,
                WhatsAppMetaTemplate.name == template_name,
                WhatsAppMetaTemplate.language == template_language,
                WhatsAppMetaTemplate.removed_at_source_at.is_(None),
            )
        )
    if not template or template.status != MetaTemplateStatus.approved:
        raise ValueError("Template da etapa não está aprovado no workspace")
    if template.name != template_name or template.language != template_language:
        raise ValueError("Snapshot de template não corresponde ao template persistido")

    # A template's owning connection is the fallback only when an explicit
    # connection was not snapshotted.  The resolver still enforces workspace
    # ownership and connected status for either path.
    resolved_connection_id = connection_id or template.whatsapp_connection_id
    svc = WhatsAppService(db)
    connection = await svc.message_service._resolve_outbound_connection(
        workspace_id=workspace_id,
        explicit_connection_id=resolved_connection_id,
        contact=None,
    )
    if connection.id != template.whatsapp_connection_id:
        raise ValueError("Template e conexão WhatsApp não pertencem ao mesmo vínculo")
    body_parameters = resolve_sequence_template_parameters(bindings, context)
    try:
        access_token = await svc.connection_service.resolve_meta_access_token(connection)
        await before_dispatch(False)
        result = await svc.meta_client.send_template_message(
            phone_number_id=connection.phone_number_id or "",
            access_token=access_token,
            to=phone,
            template_name=template_name,
            language_code=template_language,
            body_parameters=body_parameters,
        )
        return ExternalDeliveryReceipt(
            provider_reference=_provider_reference(result),
            provider_supports_idempotency=False,
            raw_result=result,
        )
    except WhatsAppProviderError as error:
        await svc.connection_service.mark_connection_error(
            connection, error.code, error.message
        )
        raise


def _flow_external_intent_recovery_due(
    context: dict[str, Any] | None,
    *,
    node_id: str,
) -> bool:
    active = _active_flow_external_intent(context, node_id=node_id)
    if not active:
        return False
    state = str(active[1].get("state") or "")
    if state in {"prepared", "confirmed"}:
        return True
    return state == "dispatching" and _lease_expired(active[1].get("attemptLeaseExpiresAt"))


def _recovery_lease_active(
    record: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    return not _lease_expired(record.get("recoveryLeaseExpiresAt"), now=now)


async def _claim_flow_step_recovery(
    db,
    *,
    run_id: UUID,
    workspace_id: UUID,
    dispatch_token: str,
    now: datetime,
) -> dict[str, Any] | None:
    run = await _lock_flow_run(db, run_id, workspace_id)
    if not run or run.status != "active":
        await db.rollback()
        return None
    dispatches = _flow_step_dispatches(run.context)
    record = dispatches.get(dispatch_token)
    if (
        not record
        or record.get("consumedAt")
        or record.get("state", "pending") != "pending"
        or str(record.get("nodeId") or "") != str(run.current_node_id or "")
        or _flow_step_dispatch_countdown(record) > 0
        or _recovery_lease_active(record, now=now)
    ):
        await db.rollback()
        return None
    lease_token = uuid4().hex
    record.update(
        {
            "recoveryLeaseToken": lease_token,
            "recoveryClaimedAt": now.isoformat(),
            "recoveryLeaseExpiresAt": (now + FLOW_RECOVERY_LEASE).isoformat(),
        }
    )
    dispatches[dispatch_token] = record
    context = dict(run.context or {})
    context[FLOW_STEP_DISPATCHES_KEY] = dispatches
    run.context = context
    await db.commit()
    return record


async def _claim_flow_external_recovery(
    db,
    *,
    run_id: UUID,
    workspace_id: UUID,
    now: datetime,
) -> str | None:
    run = await _lock_flow_run(db, run_id, workspace_id)
    if not run or run.status != "active":
        await db.rollback()
        return None
    active = _active_flow_external_intent(
        run.context,
        node_id=str(run.current_node_id or ""),
    )
    if (
        not active
        or not _flow_external_intent_recovery_due(
            run.context,
            node_id=str(run.current_node_id or ""),
        )
        or _recovery_lease_active(active[1], now=now)
    ):
        await db.rollback()
        return None
    lease_token = uuid4().hex
    _update_flow_external_intent(
        run,
        active[0],
        recoveryLeaseToken=lease_token,
        recoveryClaimedAt=now.isoformat(),
        recoveryLeaseExpiresAt=(now + FLOW_RECOVERY_LEASE).isoformat(),
    )
    await db.commit()
    return lease_token


async def _release_flow_recovery_lease(
    db,
    *,
    run_id: UUID,
    workspace_id: UUID,
    lease_token: str,
    dispatch_token: str | None = None,
) -> None:
    run = await _lock_flow_run(db, run_id, workspace_id)
    if not run:
        await db.rollback()
        return
    if dispatch_token:
        dispatches = _flow_step_dispatches(run.context)
        record = dispatches.get(dispatch_token)
        if not record or record.get("recoveryLeaseToken") != lease_token:
            await db.rollback()
            return
        record.pop("recoveryLeaseToken", None)
        record.pop("recoveryClaimedAt", None)
        record.pop("recoveryLeaseExpiresAt", None)
        dispatches[dispatch_token] = record
        context = dict(run.context or {})
        context[FLOW_STEP_DISPATCHES_KEY] = dispatches
        run.context = context
    else:
        active = _active_flow_external_intent(
            run.context,
            node_id=str(run.current_node_id or ""),
        )
        if not active or active[1].get("recoveryLeaseToken") != lease_token:
            await db.rollback()
            return
        _update_flow_external_intent(
            run,
            active[0],
            recoveryLeaseToken=None,
            recoveryClaimedAt=None,
            recoveryLeaseExpiresAt=None,
        )
    await db.commit()


@celery_app.task(name="flow_tasks.execute_flow_run_step", bind=True, max_retries=3)
def execute_flow_run_step(
    self,
    flow_run_id: str,
    workspace_id: str,
    continuation_token: str | None = None,
) -> None:
    """Executa o próximo step de um FlowRun."""
    try:
        _run_async(
            _async_execute_flow_run_step(
                flow_run_id,
                self,
                workspace_id=workspace_id,
                continuation_token=continuation_token,
            )
        )
    except FlowStepDispatchError as error:
        raise self.retry(
            exc=error,
            countdown=min(2 ** (self.request.retries + 1), 30),
        )


@celery_app.task(
    name="flow_tasks.settle_flow_agent_run",
    bind=True,
    max_retries=5,
)
def settle_flow_agent_run(
    self,
    agent_run_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    """Reconcilia um AgentRun terminal com o checkpoint do flow vinculado."""
    try:
        result = _run_async(
            _async_settle_flow_agent_run(
                agent_run_id,
                workspace_id=workspace_id,
            )
        )
    except Exception as error:
        raise self.retry(
            exc=error,
            countdown=min(2 ** (self.request.retries + 1), 30),
        )
    if result.outcome == "pending":
        raise self.retry(
            countdown=min(2 ** (self.request.retries + 1), 30),
        )
    return {
        "status": result.outcome,
        "flow_run_id": str(result.flow_run_id) if result.flow_run_id else None,
    }


async def _async_settle_flow_agent_run(
    agent_run_id: str,
    *,
    workspace_id: UUID | str,
    session_factory=None,
):
    from app.db.session import WorkerSessionLocal as AsyncSessionLocal
    from app.services.flow_service import FlowService

    resolved_workspace_id = UUID(str(workspace_id))
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        return await FlowService(db).settle_agent_run(
            UUID(agent_run_id),
            resolved_workspace_id,
        )


async def _async_execute_flow_run_step(
    flow_run_id: str,
    task=None,
    *,
    workspace_id: UUID | str,
    continuation_token: str | None = None,
    session_factory=None,
) -> None:
    from sqlalchemy import select

    from app.db.session import WorkerSessionLocal as AsyncSessionLocal
    from app.models.flow import (
        FlowRevision,
        FlowRun,
        ScheduledMessage,
        verified_flow_revision_snapshot,
    )
    from app.schemas.flow import validate_flow_graph

    session_maker = session_factory or AsyncSessionLocal
    resolved_run_id = UUID(flow_run_id)
    async with session_maker() as db:
        try:
            resolved_workspace_id = UUID(str(workspace_id))
            await set_workspace_context(db, resolved_workspace_id)

            result = await db.execute(
                select(FlowRun)
                .where(
                    FlowRun.id == resolved_run_id,
                    FlowRun.workspace_id == resolved_workspace_id,
                )
                .with_for_update()
            )
            run = result.scalar_one_or_none()
            if not run or run.status not in ("active",):
                return

            dispatch_source: tuple[str, str] | None = None
            if continuation_token:
                from app.services.flow_service import FLOW_AGENT_EXECUTIONS_KEY

                context = dict(run.context or {})
                dispatches = _flow_step_dispatches(context)
                dispatch_record = dispatches.get(continuation_token)
                if dispatch_record is not None:
                    if dispatch_record.get("consumedAt"):
                        active_intent = _active_flow_external_intent(
                            context,
                            node_id=str(run.current_node_id or ""),
                        )
                        if active_intent:
                            dispatch_source = ("dispatch", continuation_token)
                        else:
                            pending = _next_pending_flow_step_dispatch(
                                context,
                                str(dispatch_record.get("successorToken") or ""),
                            )
                            await db.commit()
                            if pending:
                                pending_token, pending_record = pending
                                _publish_flow_step_dispatch(
                                    flow_run_id=flow_run_id,
                                    workspace_id=resolved_workspace_id,
                                    token=pending_token,
                                    record=pending_record,
                                )
                            return
                    if str(dispatch_record.get("nodeId") or "") != str(run.current_node_id or ""):
                        logger.error(
                            "flow_run.step_dispatch_checkpoint_mismatch",
                            run_id=flow_run_id,
                            dispatch_token=continuation_token,
                        )
                        return
                    dispatch_record.update(
                        {
                            "state": "consumed",
                            "consumedAt": datetime.now(UTC).isoformat(),
                        }
                    )
                    dispatches[continuation_token] = dispatch_record
                    context[FLOW_STEP_DISPATCHES_KEY] = dispatches
                    run.context = context
                    dispatch_source = ("dispatch", continuation_token)

                if dispatch_record is None:
                    raw_executions = context.get(FLOW_AGENT_EXECUTIONS_KEY)
                    executions = dict(raw_executions) if isinstance(raw_executions, dict) else {}
                    matching = next(
                        (
                            (str(agent_run_id), dict(checkpoint))
                            for agent_run_id, checkpoint in executions.items()
                            if isinstance(checkpoint, dict)
                            and str(checkpoint.get("continuationToken") or "") == continuation_token
                        ),
                        None,
                    )
                    if not matching:
                        logger.warning(
                            "flow_run.agent_continuation_token_missing",
                            run_id=flow_run_id,
                        )
                        return
                    checkpoint_agent_run_id, checkpoint = matching
                    if checkpoint.get("continuationConsumedAt"):
                        active_intent = _active_flow_external_intent(
                            context,
                            node_id=str(run.current_node_id or ""),
                        )
                        if active_intent:
                            dispatch_source = (
                                "checkpoint",
                                checkpoint_agent_run_id,
                            )
                        else:
                            pending = _next_pending_flow_step_dispatch(
                                context,
                                str(checkpoint.get(CONTINUATION_SUCCESSOR_TOKEN_KEY) or ""),
                            )
                            await db.commit()
                            if pending:
                                pending_token, pending_record = pending
                                _publish_flow_step_dispatch(
                                    flow_run_id=flow_run_id,
                                    workspace_id=resolved_workspace_id,
                                    token=pending_token,
                                    record=pending_record,
                                )
                            return
                    if checkpoint.get("state") != "advanced" or str(
                        checkpoint.get("continuationNodeId") or ""
                    ) != str(run.current_node_id or ""):
                        logger.error(
                            "flow_run.agent_continuation_checkpoint_mismatch",
                            run_id=flow_run_id,
                            agent_run_id=checkpoint_agent_run_id,
                        )
                        return
                    checkpoint["continuationConsumedAt"] = datetime.now(UTC).isoformat()
                    executions[checkpoint_agent_run_id] = checkpoint
                    context[FLOW_AGENT_EXECUTIONS_KEY] = executions
                    run.context = context
                    dispatch_source = ("checkpoint", checkpoint_agent_run_id)

            revision = await db.scalar(
                select(FlowRevision).where(
                    FlowRevision.id == run.revision_id,
                    FlowRevision.flow_id == run.flow_id,
                )
            )
            if not revision:
                run.status = "failed"
                run.error_message = "Revisão do flow não encontrada"
                await db.commit()
                return
            snapshot = verified_flow_revision_snapshot(revision)
            if not snapshot.get("is_active"):
                run.status = "failed"
                run.error_message = "Flow inativo não pode ser executado"
                await db.commit()
                return
            if (run.context or {}).get("klmRuntime") == "local_simulation" or (
                snapshot.get("trigger_config") or {}
            ).get("localOnly"):
                run.status = "failed"
                run.completed_at = None
                run.error_message = "Execução local não pode ser processada pelo worker"
                run.context = {
                    **(run.context or {}),
                    "klmRuntime": "local_simulation",
                    "externalActionsExecuted": 0,
                }
                await db.commit()
                return

            snapshot_nodes = snapshot.get("nodes") or []
            snapshot_edges = snapshot.get("edges") or []
            validate_flow_graph(snapshot_nodes, snapshot_edges)
            max_steps = _max_flow_steps(snapshot.get("trigger_config"))
            nodes = {n["id"]: n for n in snapshot_nodes}
            edges = snapshot_edges
            current_node = nodes.get(run.current_node_id or "")
            if not current_node:
                run.status = "completed"
                run.completed_at = datetime.now(UTC)
                await db.commit()
                return

            node_type = current_node.get("type", "")
            config = _node_config(current_node)
            node_id = str(run.current_node_id or "")
            contract_error = _runtime_node_contract_error(str(node_type), dict(run.context or {}))
            if contract_error:
                run.status = "failed"
                run.error_message = contract_error
                await db.commit()
                return
            run_context = dict(run.context or {})
            step_count = int(run_context.get("flowStepCount", 0) or 0)
            external_intent = (
                _active_flow_external_intent(run_context, node_id=node_id)
                if node_type in EXTERNAL_FLOW_NODE_TYPES
                else None
            )
            execution_step = (
                int(external_intent[1].get("flowStep") or step_count or 1)
                if external_intent
                else step_count + 1
            )
            node_data = (
                current_node.get("data") if isinstance(current_node.get("data"), dict) else {}
            )
            node_label = str(node_data.get("label") or current_node.get("label") or node_id)

            should_check_external_gate = (
                not external_intent or external_intent[1].get("state") == "prepared"
            )
            if (
                node_type in EXTERNAL_FLOW_NODE_TYPES
                and should_check_external_gate
                and not external_delivery_allowed()
            ):
                now = datetime.now(UTC)
                await _call_human_before_waiting(
                    db,
                    run,
                    node_id=node_id,
                    execution_step=step_count + 1,
                    reason=f"Nó {node_type} aguardando aprovação do canal externo.",
                    config=config,
                    purpose="external_channel_approval",
                )
                run.context = {
                    **(run.context or {}),
                    "flowStepCount": step_count,
                    "externalActionsExecuted": 0,
                    "blockedExternalAction": {
                        "nodeId": run.current_node_id,
                        "nodeType": node_type,
                        "blockedAt": now.isoformat(),
                    },
                }
                await db.commit()
                return

            # M1: mesmo com o canal externo aberto, um nó de texto livre do
            # WhatsApp só pode disparar dentro da janela de 24h da Meta. Fora
            # dela o envio seria violação de política, então pausamos o run para
            # decisão humana (reengajar via template aprovado), espelhando o
            # bloqueio do gate de canal acima.
            if (
                node_type in {"send_message", "scheduled_message"}
                and should_check_external_gate
                and external_delivery_allowed()
            ):
                from app.models.whatsapp import (
                    WaMessageDirection,
                    WhatsAppContact,
                    WhatsAppMessage,
                )
                from app.services.meta_policy_gate import MetaPolicyGate

                window_phone = (run.context or {}).get("phone") or (run.context or {}).get(
                    "contact_phone", ""
                )
                last_inbound_at = None
                if window_phone:
                    last_inbound_at = await db.scalar(
                        select(WhatsAppMessage.timestamp)
                        .join(
                            WhatsAppContact,
                            WhatsAppMessage.contact_id == WhatsAppContact.id,
                        )
                        .where(
                            WhatsAppContact.workspace_id == run.workspace_id,
                            WhatsAppContact.phone.in_(phone_number_lookup_variants(window_phone)),
                            WhatsAppMessage.direction == WaMessageDirection.inbound,
                        )
                        .order_by(WhatsAppMessage.timestamp.desc())
                        .limit(1)
                    )
                window_decision = MetaPolicyGate.evaluate(
                    "whatsapp",
                    last_inbound_at=last_inbound_at,
                    now=datetime.now(UTC),
                    author_type="agent",
                    action_type="send_message",
                )
                # A sequence stage carrying an approved/pending official
                # template is not free text.  It must be queued so the
                # scheduled worker can hold pending templates or dispatch the
                # approved template outside the 24-hour conversation window.
                sequence_template_stage = (
                    node_type == "scheduled_message"
                    and bool(config.get("template_name"))
                    and str(config.get("template_status") or "").lower()
                    in {"approved", "pending"}
                )
                if not window_decision.allowed and not sequence_template_stage:
                    now = datetime.now(UTC)
                    wait_reason = (
                        f"Nó {node_type} aguardando reengajamento: janela de 24h "
                        "do WhatsApp fechada (envie template aprovado)."
                    )
                    await _call_human_before_waiting(
                        db,
                        run,
                        node_id=node_id,
                        execution_step=step_count + 1,
                        reason=wait_reason,
                        config=config,
                        purpose="whatsapp_reengagement",
                    )
                    run.context = {
                        **(run.context or {}),
                        "flowStepCount": step_count,
                        "externalActionsExecuted": 0,
                        "blockedExternalAction": {
                            "nodeId": run.current_node_id,
                            "nodeType": node_type,
                            "blockedAt": now.isoformat(),
                            "reasonCode": window_decision.reason_code,
                            "requires": window_decision.requires,
                        },
                    }
                    await db.commit()
                    return

            if external_intent and str(external_intent[1].get("revisionId") or "") != str(
                run.revision_id
            ):
                raise ValueError("Intenção externa vinculada a outra revisão do flow")

            if not external_intent:
                if step_count >= max_steps:
                    run.status = "failed"
                    run.error_message = f"Limite seguro de {max_steps} etapas atingido"
                    run.context = {
                        **run_context,
                        "flowStepCount": step_count,
                        "flowMaxSteps": max_steps,
                        "flowLoopGuardTriggered": True,
                    }
                    record_flow_trace(
                        run,
                        node_id=node_id,
                        node_type=str(node_type),
                        label=node_label,
                        step=execution_step,
                        status="failed",
                    )
                    await db.commit()
                    return
                run.context = {
                    **run_context,
                    "flowStepCount": step_count + 1,
                    "flowMaxSteps": max_steps,
                }

            if node_type in EXTERNAL_FLOW_NODE_TYPES:
                resumed_context = dict(run.context or {})
                resumed_context.pop("blockedExternalAction", None)
                run.context = resumed_context
                if not external_intent:
                    external_intent = _prepare_flow_external_intent(
                        run,
                        node_id=node_id,
                        node_type=node_type,
                        flow_step=step_count + 1,
                    )
                    await db.commit()
                    _inject_fault(
                        "flow_external.after_intent_commit",
                        intent_key=external_intent[0],
                        node_id=node_id,
                    )
                    run = await _lock_flow_run(
                        db,
                        resolved_run_id,
                        resolved_workspace_id,
                    )
                    if (
                        not run
                        or run.status != "active"
                        or str(run.current_node_id or "") != node_id
                    ):
                        return
                    external_intent = _active_flow_external_intent(
                        run.context,
                        node_id=node_id,
                    )
                    if not external_intent:
                        raise ExternalIntentFencedError(
                            "Intenção externa desapareceu após o commit"
                        )

                intent_state = str(external_intent[1].get("state") or "")
                if intent_state == "uncertain":
                    wait_reason = (
                        "Efeito externo incerto; reconciliação humana obrigatória "
                        "antes de continuar."
                    )
                    _pause_flow_for_uncertain_external_intent(
                        run,
                        external_intent[0],
                        reason=wait_reason,
                    )
                    await _call_human_before_waiting(
                        db,
                        run,
                        node_id=node_id,
                        execution_step=execution_step,
                        reason=wait_reason,
                        config=config,
                        purpose="external_reconciliation",
                    )
                    await db.commit()
                    return
                if intent_state == "dispatching" and not _lease_expired(
                    external_intent[1].get("attemptLeaseExpiresAt")
                ):
                    await db.commit()
                    return
                if intent_state == "dispatching" and not external_intent[1].get(
                    "providerSupportsIdempotency"
                ):
                    wait_reason = (
                        "Efeito externo incerto após expiração do lease; "
                        "reconciliação humana obrigatória."
                    )
                    _pause_flow_for_uncertain_external_intent(
                        run,
                        external_intent[0],
                        reason=wait_reason,
                    )
                    await _call_human_before_waiting(
                        db,
                        run,
                        node_id=node_id,
                        execution_step=execution_step,
                        reason=wait_reason,
                        config=config,
                        purpose="external_reconciliation",
                    )
                    await db.commit()
                    return
                if intent_state == "failed":
                    run.status = "failed"
                    run.error_message = "Tentativa externa falhou"
                    record_flow_trace(
                        run,
                        node_id=node_id,
                        node_type=str(node_type),
                        label=node_label,
                        step=execution_step,
                        status="failed",
                    )
                    await db.commit()
                    return

            logger.info(
                "flow_run.executing_node",
                run_id=flow_run_id,
                revision_id=str(revision.id),
                revision=revision.revision_number,
                node_type=node_type,
                node_id=run.current_node_id,
            )

            # ── Executar nó ──────────────────────────────────────────
            next_node_id: str | None = None
            should_continue = True
            agent_run_to_settle: UUID | None = None
            trace_decision: dict[str, Any] | None = None

            if node_type == "trigger":
                # Nó de trigger — avançar imediatamente para o próximo
                pass

            elif node_type in {
                "set_variable",
                "transform",
                "aggregate",
                "link_action",
                "landing_page",
                "error_handler",
                "merge",
                "debounce",
                "dedupe",
                "checkpoint",
                "retry",
                "timeout",
                "cancel",
                "load_client",
                "load_contract",
                "load_matter",
                "load_task",
                "load_documents",
                "load_finance",
                "load_tax_profile",
                "validate_data",
                "mask_data",
                "data_quality",
                "compare_versions",
                "rag_query",
            }:
                context = dict(run.context or {})
                if str(node_type).startswith("load_"):
                    context.update(
                        await _load_context_entity(
                            db, workspace_id, str(node_type), config, context
                        )
                    )
                    trace_decision = {"loaded": node_type, "workspaceScoped": True}
                else:
                    trace_decision = apply_data_node(node_type, config, context)
                run.context = context
                if node_type == "cancel":
                    run.status = "cancelled"
                    run.completed_at = datetime.now(UTC)
                    should_continue = False

            elif node_type == "form":
                context = dict(run.context or {})
                trace_decision = apply_data_node(node_type, config, context)
                run.context = context
                await _call_human_before_waiting(
                    db,
                    run,
                    node_id=node_id,
                    execution_step=execution_step,
                    reason="Formulário aguardando submissão válida do contato.",
                    config=config,
                    purpose="form_submission",
                )
                should_continue = False

            elif node_type == "create_meeting":
                # Never call the API route from a worker.  MeetingService is
                # the canonical tenant-scoped command: it owns conflicts,
                # participant invariants, entitlement and audit receipts.
                from app.schemas.control_plane import MeetingCreate
                from app.services.meeting_service import MeetingService

                payload = MeetingCreate.model_validate(
                    {key: value for key, value in config.items() if key != "idempotency_key"}
                )
                meeting = await MeetingService(db).create_for_workspace(
                    workspace_id,
                    payload,
                    actor_user_id=revision.created_by_user_id,
                    idempotency_key=(
                        str(config.get("idempotency_key"))
                        if config.get("idempotency_key") and revision.created_by_user_id
                        else f"flow-meeting:{run.id}:{node_id}:{execution_step}"
                        if revision.created_by_user_id
                        else None
                    ),
                    correlation_id=f"flow:{run.id}:{node_id}:{execution_step}",
                )
                context = dict(run.context or {})
                context["meeting"] = {"id": str(meeting.id), "title": meeting.title}
                run.context = context
                trace_decision = {"meetingId": str(meeting.id), "created": True}

            elif node_type == "update_client":
                # Flow runs retain the initiating human identity.  A system
                # run cannot silently mutate personal data on their behalf.
                if revision.created_by_user_id is None:
                    raise ValueError(
                        "Atualização de cliente exige um solicitante humano autenticado."
                    )
                from app.services.client_command_service import ClientCommandService

                client = await ClientCommandService(db).update(
                    workspace_id=workspace_id,
                    client_id=UUID(str(config["client_id"])),
                    changes=dict(config["changes"]),
                    actor_user_id=revision.created_by_user_id,
                    expected_updated_at=(
                        datetime.fromisoformat(
                            str(config["expected_updated_at"]).replace("Z", "+00:00")
                        )
                        if config.get("expected_updated_at")
                        else None
                    ),
                    idempotency_key=str(
                        config.get("idempotency_key")
                        or f"flow-client:{run.id}:{node_id}:{execution_step}"
                    ),
                    correlation_id=f"flow:{run.id}:{node_id}:{execution_step}",
                    source="flow_update_client",
                )
                context = dict(run.context or {})
                context["client"] = {"id": str(client.id), "name": client.name}
                run.context = context
                trace_decision = {"clientId": str(client.id), "updated": True}

            elif node_type == "update_contract":
                if revision.created_by_user_id is None:
                    raise ValueError(
                        "Atualização de contrato exige um solicitante humano autenticado."
                    )
                from app.services.contract_command_service import ContractCommandService

                proposal = await ContractCommandService(db).update(
                    workspace_id=workspace_id,
                    proposal_id=UUID(str(config["proposal_id"])),
                    changes=dict(config["changes"]),
                    actor_user_id=revision.created_by_user_id,
                    expected_updated_at=(
                        datetime.fromisoformat(
                            str(config["expected_updated_at"]).replace("Z", "+00:00")
                        )
                        if config.get("expected_updated_at")
                        else None
                    ),
                    idempotency_key=str(
                        config.get("idempotency_key")
                        or f"flow-contract:{run.id}:{node_id}:{execution_step}"
                    ),
                    correlation_id=f"flow:{run.id}:{node_id}:{execution_step}",
                    source="flow_update_contract",
                )
                context = dict(run.context or {})
                context["contract"] = {"id": str(proposal.id), "matterId": str(proposal.matter_id)}
                run.context = context
                trace_decision = {"proposalId": str(proposal.id), "updated": True}

            elif node_type == "register_event":
                # Append-only system event.  The deterministic correlation id
                # is the idempotency boundary for retried flow steps.
                from sqlalchemy import select as sa_select

                from app.models.klm_operations import KlmAuditEvent

                correlation_id = (
                    f"flow-register-event:{run.id}:{run.revision_id}:{node_id}:{execution_step}"
                )
                event = await db.scalar(
                    sa_select(KlmAuditEvent).where(
                        KlmAuditEvent.workspace_id == run.workspace_id,
                        KlmAuditEvent.correlation_id == correlation_id,
                    )
                )
                if event is None:
                    raw_id = config.get("entity_id")
                    event = KlmAuditEvent(
                        workspace_id=run.workspace_id,
                        actor_user_id=revision.created_by_user_id,
                        actor_type="human" if revision.created_by_user_id else "system",
                        actor_label="KLM Flows",
                        event_type=str(config["event_type"]),
                        entity_type=str(config.get("entity_type") or "flow_run"),
                        entity_id=UUID(str(raw_id)) if raw_id else run.id,
                        summary=str(resolve_expression(config["summary"], run.context or {})),
                        correlation_id=correlation_id,
                        after_data={
                            "flowRunId": str(run.id),
                            **dict(config.get("after_data") or {}),
                        },
                    )
                    db.add(event)
                    await db.flush()
                context = dict(run.context or {})
                context["registeredEvent"] = {"id": str(event.id), "eventType": event.event_type}
                run.context = context
                trace_decision = {"auditEventId": str(event.id), "registered": True}

            elif node_type == "generate_report":
                from app.services.reports_service import ReportsService

                report = await ReportsService(db).get_summary(run.workspace_id)
                context = dict(run.context or {})
                context["report"] = _flow_json_safe(report.model_dump(mode="json"))
                run.context = context
                trace_decision = {"reportGenerated": True, "kpiCount": len(report.kpis)}

            elif node_type == "request_export":
                from app.models.finance_dimensional import FinanceExportJob
                from app.models.matter import Matter
                from app.services.finance_export_service import record_finance_export_event
                from app.workers.finance_export_tasks import run_finance_export

                actor_user_id = revision.created_by_user_id
                if actor_user_id is None:
                    actor_user_id = await db.scalar(
                        select(Matter.responsible_human_id).where(
                            Matter.id == run.matter_id,
                            Matter.workspace_id == run.workspace_id,
                            Matter.deleted_at.is_(None),
                        )
                    )
                if actor_user_id is None:
                    authorized_actors = dict((run.context or {}).get("humanAuthorizedActors") or {})
                    authorized_actor_id = authorized_actors.get(node_id)
                    if authorized_actor_id:
                        try:
                            actor_user_id = UUID(str(authorized_actor_id))
                        except (TypeError, ValueError):
                            actor_user_id = None
                if actor_user_id is None:
                    # Do not create a job under a fabricated identity.  The
                    # checkpoint is durable and routed; a human must take
                    # ownership before an export can be requested.
                    await _call_human_before_waiting(
                        db,
                        run,
                        node_id=node_id,
                        execution_step=execution_step,
                        reason="Exportação financeira exige responsável humano identificado.",
                        config={**config, "required_roles": ["workspace_owner"]},
                        purpose="export_authorization",
                    )
                    should_continue = False
                    trace_decision = {"export": "waiting_human", "reason": "missing_actor"}
                else:
                    # The job is durable before dispatch.  The short countdown
                    # prevents the worker from observing an uncommitted job while
                    # retaining an honest failed state if the broker is absent.
                    job = FinanceExportJob(
                        workspace_id=run.workspace_id,
                        actor_user_id=actor_user_id,
                        format=str(config["format"]),
                        status="queued",
                        drill_context=dict(config.get("drill_context") or {}),
                        expires_at=datetime.now(UTC) + timedelta(hours=24),
                    )
                    db.add(job)
                    await db.flush()
                    record_finance_export_event(db, job, "finance_export_requested")
                    try:
                        run_finance_export.apply_async(
                            args=[str(job.id), str(run.workspace_id)], countdown=2, retry=False
                        )
                    except Exception:
                        job.status = "failed"
                        job.error_detail = "FINANCE_EXPORT_ENQUEUE_FAILED"
                        record_finance_export_event(
                            db, job, "finance_export_failed", error_code=job.error_detail
                        )
                    context = dict(run.context or {})
                    context["export"] = {
                        "id": str(job.id),
                        "status": str(job.status),
                        "format": job.format,
                    }
                    run.context = context
                    trace_decision = {"exportJobId": str(job.id), "status": str(job.status)}

            elif node_type == "generate_document":
                # DocumentService is the only private-storage boundary. It
                # deliberately has no "invent bytes" operation: generated
                # legal documents need a human-approved source/artifact.  A
                # durable human decision is therefore a real execution path,
                # not an unavailable node or an unbounded wait.
                from app.services.document_service import DocumentService

                DocumentService._require_configured_backend()
                await _call_human_before_waiting(
                    db,
                    run,
                    node_id=node_id,
                    execution_step=execution_step,
                    reason=str(
                        config.get("reason") or f"Validar e gerar documento: {config['title']}"
                    ),
                    config={
                        **config,
                        "required_roles": list(config.get("required_roles") or ["workspace_owner"]),
                    },
                    purpose="document_generation_review",
                )
                should_continue = False
                trace_decision = {"documentGeneration": "waiting_human", "storage": "private"}

            elif node_type in {"human_assign", "human_notify"}:
                # These nodes create a decision the recipient must acknowledge
                # or resolve.  Pausing is deliberate: allowing execution to
                # continue would leave a durable but unresolvable decision.
                await _call_human_before_waiting(
                    db,
                    run,
                    node_id=node_id,
                    execution_step=execution_step,
                    reason=config.get("reason") or node_label,
                    config=config,
                    purpose=node_type,
                )
                should_continue = False

            elif node_type == "human_schedule":
                # Route first, then create a canonical Meeting in the same
                # transaction.  The scheduled human still must resolve the
                # durable decision before this flow may continue.
                decision = await _call_human_before_waiting(
                    db,
                    run,
                    node_id=node_id,
                    execution_step=execution_step,
                    reason=config.get("reason") or node_label,
                    config=config,
                    purpose="human_schedule",
                )
                from app.models.meeting import MeetingParticipantRole
                from app.schemas.control_plane import MeetingCreate, MeetingParticipantWrite
                from app.services.meeting_service import MeetingService

                participants = [
                    MeetingParticipantWrite(
                        user_id=decision.assigned_user_id,
                        role=MeetingParticipantRole.organizer,
                    )
                ]
                if decision.owner_user_id != decision.assigned_user_id:
                    participants.append(
                        MeetingParticipantWrite(
                            user_id=decision.owner_user_id,
                            role=MeetingParticipantRole.attendee,
                        )
                    )
                meeting = await MeetingService(db).create_for_workspace(
                    run.workspace_id,
                    MeetingCreate(
                        matter_id=UUID(str(config["matter_id"]))
                        if config.get("matter_id")
                        else run.matter_id,
                        title=str(config["title"]),
                        starts_at=config["starts_at"],
                        ends_at=config.get("ends_at"),
                        location=config.get("location"),
                        meeting_url=config.get("meeting_url"),
                        notes=config.get("notes") or config.get("reason"),
                        participants=participants,
                    ),
                    actor_user_id=decision.assigned_user_id,
                    idempotency_key=(f"flow-human-schedule:{run.id}:{node_id}:{execution_step}"),
                    correlation_id=f"flow:{run.id}:{node_id}:{execution_step}",
                )
                context = dict(run.context or {})
                context.setdefault("humanSchedules", {})[node_id] = {
                    "meetingId": str(meeting.id),
                    "decisionId": str(decision.id),
                    "organizerUserId": str(decision.assigned_user_id),
                    "ownerUserId": str(decision.owner_user_id),
                    "startsAt": meeting.starts_at.isoformat(),
                    "status": meeting.status.value,
                }
                run.context = context
                trace_decision = {"meetingId": str(meeting.id), "decisionId": str(decision.id)}
                should_continue = False

            elif node_type == "human_takeover":
                # The conversation ownership service performs the actual
                # tenant-scoped handoff and its own canonical audit event.
                decision = await _call_human_before_waiting(
                    db,
                    run,
                    node_id=node_id,
                    execution_step=execution_step,
                    reason=config.get("reason") or node_label,
                    config=config,
                    purpose="human_takeover",
                )
                from app.services.conversation_ownership_service import human_takeover

                ownership = await human_takeover(
                    db,
                    workspace_id=run.workspace_id,
                    thread_id=UUID(str(config["thread_id"])),
                    human_user_id=decision.assigned_user_id,
                    actor_label="Roteamento automático do fluxo",
                    actor_type="system",
                    reason=f"flow_human_takeover:{run.id}:{node_id}",
                )
                context = dict(run.context or {})
                context.setdefault("humanTakeovers", {})[node_id] = {
                    "threadId": str(ownership.thread.id),
                    "decisionId": str(decision.id),
                    "assignedUserId": str(decision.assigned_user_id),
                    "ownerGeneration": ownership.thread.owner_generation,
                    "changed": ownership.changed,
                }
                run.context = context
                trace_decision = {
                    "threadId": str(ownership.thread.id),
                    "decisionId": str(decision.id),
                    "changed": ownership.changed,
                }
                should_continue = False

            elif node_type == "human_escalate":
                from app.models.agent_platform import HumanDecision
                from app.services.agent_platform_service import HumanRoutingService

                context = dict(run.context or {})
                target_node_id = str(config.get("target_node_id") or node_id)
                target = context.get("humanRouting", {}).get(target_node_id) or context.get(
                    "humanCheckpoints", {}
                ).get(target_node_id)
                if not target or not target.get("decisionId"):
                    raise ValueError("human_escalate requires a routed human decision")
                decision = await db.get(HumanDecision, UUID(str(target["decisionId"])))
                if decision is None or decision.workspace_id != run.workspace_id:
                    raise ValueError("human_escalate target is unavailable")
                if decision.flow_run_id != run.id:
                    raise ValueError("human_escalate target belongs to another flow run")
                if decision.status != "pending":
                    raise ValueError("human_escalate target is no longer pending")
                escalated = await HumanRoutingService(db).escalate(decision)
                checkpoint = {
                    "type": node_type,
                    "decisionId": str(decision.id),
                    "escalated": escalated,
                    "status": str(decision.status),
                    "assignedUserId": (
                        str(decision.assigned_user_id) if decision.assigned_user_id else None
                    ),
                    "priority": config.get("priority", "high"),
                }
                context.setdefault("humanRouting", {})[node_id] = checkpoint
                checkpoints = dict(context.get("humanCheckpoints") or {})
                checkpoints[target_node_id] = {
                    "nodeType": "human_escalate",
                    "status": "pending" if escalated else "expired",
                    "decisionKey": decision.decision_key,
                    "decisionId": str(decision.id),
                    "assignedUserId": (
                        str(decision.assigned_user_id) if decision.assigned_user_id else None
                    ),
                    "ownerUserId": str(decision.owner_user_id),
                    "dueAt": decision.due_at.isoformat() if decision.due_at else None,
                    "routingPath": list(decision.routing_path),
                }
                context["humanCheckpoints"] = checkpoints
                run.context = context
                if not escalated:
                    run.status = "failed"
                    run.pause_reason = "Escalonamento humano terminou sem responsável disponível."
                else:
                    run.status = "waiting_human"
                    run.completed_at = None
                    run.paused_at = datetime.now(UTC)
                    run.pause_reason = (
                        "Escalonamento humano aguardando decisão do novo responsável."
                    )
                should_continue = False

            elif node_type in {
                "human_request_input",
                "human_review",
                "human_approval",
                "human_wait",
            }:
                # Durable, fail-closed human gate. The continuation worker resumes
                # from current_node_id after the decision is consumed.
                context = dict(run.context or {})
                context.setdefault("humanCheckpoints", {})[node_id] = {
                    "nodeType": node_type,
                    "status": "pending",
                    "decisionKey": f"{flow_run_id}:{node_id}:{execution_step}",
                    "slaSeconds": int(config.get("sla_seconds", 86400)),
                    "priority": config.get("priority", "normal"),
                }
                run.context = context
                from app.services.agent_platform_service import HumanRoutingService

                requested_user_id = config.get("user_id") or config.get("assigned_user_id")
                decision = await HumanRoutingService(db).create_decision(
                    workspace_id=run.workspace_id,
                    agent_run_id=None,
                    flow_run_id=run.id,
                    decision_key=context["humanCheckpoints"][node_id]["decisionKey"],
                    options=dict(config.get("options", {})),
                    required_roles=(
                        list(config.get("required_roles") or [])
                        if (
                            config.get("required_roles")
                            or config.get("required_skills")
                            or requested_user_id
                        )
                        else ["workspace_owner"]
                    ),
                    required_skills=list(config.get("required_skills") or []),
                    requested_user_id=(UUID(str(requested_user_id)) if requested_user_id else None),
                    sla_minutes=max(1, int(config.get("sla_seconds", 86400)) // 60),
                    max_hops=int(config.get("max_hops", 4)),
                    allow_active_flow_run=True,
                )
                context["humanCheckpoints"][node_id].update(
                    {
                        "decisionId": str(decision.id),
                        "assignedUserId": str(decision.assigned_user_id),
                        "ownerUserId": str(decision.owner_user_id),
                        "dueAt": (decision.due_at.isoformat() if decision.due_at else None),
                        "routingPath": list(decision.routing_path),
                    }
                )
                run.context = context
                # Waiting begins only after assignment and durable in-app calls
                # to the assignee and workspace owner were flushed above.
                run.status = "waiting_human"
                run.paused_at = datetime.now(UTC)
                run.pause_reason = f"Nó {node_type} aguardando decisão humana."
                should_continue = False

            elif node_type == "human_resolve":
                context = dict(run.context or {})
                cp = context.get("humanCheckpoints", {}).get(
                    str(config.get("target_node_id", node_id)), {}
                )
                if not cp or cp.get("status") != "decided":
                    raise ValueError("human_resolve requires consumed decision")
                cp["resolvedAt"] = datetime.now(UTC).isoformat()
                context.setdefault("humanCheckpoints", {})[
                    str(config.get("target_node_id", node_id))
                ] = cp
                run.context = context

            elif node_type == "finance_read":
                from app.services.finance_dimensional_service import (
                    FinanceDimensionalService,
                )
                from app.services.finance_executive_service import FinanceExecutiveService

                operation = str(config.get("operation") or "analytics")
                dimensional = FinanceDimensionalService(db)
                if operation == "analytics":
                    result = await FinanceExecutiveService(db).analytics(run.workspace_id)
                elif operation == "drilldown":
                    result = await dimensional.drilldown(
                        run.workspace_id,
                        cost_center_id=(
                            UUID(str(config["cost_center_id"]))
                            if config.get("cost_center_id")
                            else None
                        ),
                        client_id=(
                            UUID(str(config["client_id"])) if config.get("client_id") else None
                        ),
                        contract_id=(
                            UUID(str(config["contract_id"])) if config.get("contract_id") else None
                        ),
                        matter_id=(
                            UUID(str(config["matter_id"])) if config.get("matter_id") else None
                        ),
                    )
                elif operation in {"dre_cash", "dre_accrual"}:
                    today = datetime.now(UTC).date()
                    result = await dimensional.dre(
                        run.workspace_id,
                        mode="cash" if operation == "dre_cash" else "accrual",
                        period_start=date(today.year, 1, 1),
                        period_end=today,
                    )
                elif operation == "forecast":
                    result = await dimensional.list_forecasts(run.workspace_id)
                elif operation == "valuation":
                    result = await dimensional.list_valuations(run.workspace_id)
                elif operation == "tax_reform":
                    result = await dimensional.simulate_tax_reform(
                        run.workspace_id, as_of=datetime.now(UTC).date()
                    )
                else:
                    raise ValueError(f"unsupported finance_read operation: {operation}")
                context = dict(run.context or {})
                context.setdefault("finance", {})[node_id] = {
                    "mode": "read",
                    "operation": operation,
                    "status": "completed",
                    "result": _flow_json_safe(result),
                    "observedAt": datetime.now(UTC).isoformat(),
                }
                run.context = context
                if operation == "tax_reform":
                    tax_review_config = {
                        **config,
                        "required_roles": list(config.get("required_roles") or ["finance_manager"]),
                        "required_skills": list(
                            config.get("required_skills") or ["contabilidade", "tributario"]
                        ),
                    }
                    await _call_human_before_waiting(
                        db,
                        run,
                        node_id=node_id,
                        execution_step=execution_step,
                        reason=(
                            "Simulação tributária exige validação do contador antes de orientar "
                            "o escritório."
                        ),
                        config=tax_review_config,
                        purpose="tax_accountant_review",
                    )
                    should_continue = False

            elif node_type == "finance_draft":
                from sqlalchemy import select

                from app.models.finance import (
                    FinanceEntry,
                    FinanceEntryStatus,
                    FinanceEntryType,
                )

                parameters = dict(config.get("parameters") or {})
                amount = Decimal(str(parameters.get("amount") or 0))
                if amount <= 0:
                    raise ValueError("finance_draft requires a positive amount")
                draft_key = f"flow-draft:{run.id}:{node_id}:{execution_step}"
                draft = await db.scalar(
                    select(FinanceEntry).where(
                        FinanceEntry.workspace_id == run.workspace_id,
                        FinanceEntry.external_ref == draft_key,
                    )
                )
                if draft is None:
                    entry_type = FinanceEntryType(str(parameters.get("entry_type") or "expense"))
                    draft = FinanceEntry(
                        workspace_id=run.workspace_id,
                        matter_id=run.matter_id,
                        title=str(parameters.get("title") or "Rascunho financeiro do fluxo"),
                        description=str(parameters.get("description") or ""),
                        entry_type=entry_type,
                        status=FinanceEntryStatus.pending,
                        amount=amount,
                        due_date=(
                            _as_utc(parameters.get("due_at")) if parameters.get("due_at") else None
                        ),
                        external_ref=draft_key,
                        entry_metadata={
                            "draft": True,
                            "source": "flow",
                            "flowRunId": str(run.id),
                            "nodeId": node_id,
                            "humanReviewRequired": True,
                        },
                    )
                    db.add(draft)
                    await db.flush()
                context = dict(run.context or {})
                context.setdefault("finance", {})[node_id] = {
                    "mode": "draft",
                    "operation": str(config.get("operation") or "finance_draft"),
                    "status": "pending_human_review",
                    "financeEntryId": str(draft.id),
                }
                run.context = context
                await _call_human_before_waiting(
                    db,
                    run,
                    node_id=node_id,
                    execution_step=execution_step,
                    reason="Rascunho financeiro exige revisão humana antes de qualquer efeito.",
                    config=config,
                    purpose="finance_review",
                )
                should_continue = False

            elif node_type == "router":
                context = dict(run.context or {})
                trace_decision = dict(select_router_route(config, context))
                route_key = str(trace_decision.get("routeKey") or "fallback")
                next_node_id = _next_flow_target_for_route(
                    edges,
                    node_id,
                    route_key,
                )

            elif node_type == "loop":
                context = dict(run.context or {})
                trace_decision = dict(advance_loop(node_id, config, context))
                run.context = context
                next_node_id = _next_flow_target_for_route(
                    edges,
                    node_id,
                    str(trace_decision.get("routeKey") or "done"),
                )

            elif node_type == "send_message":
                message_template = config.get("message", "")
                message = _interpolate(message_template, run.context or {})
                phone = (run.context or {}).get("phone") or (run.context or {}).get(
                    "contact_phone", ""
                )
                if not phone:
                    raise ValueError("Nó send_message sem telefone de destino")
                assert external_intent is not None
                intent_key, intent_record = external_intent
                if intent_record.get("state") != "confirmed":
                    attempt_token: str | None = None
                    provider_supports_idempotency = False

                    async def before_dispatch(supports_idempotency: bool) -> None:
                        nonlocal attempt_token, provider_supports_idempotency
                        provider_supports_idempotency = supports_idempotency
                        attempt_token = await _claim_flow_external_attempt(
                            db,
                            run,
                            intent_key=intent_key,
                            provider_supports_idempotency=supports_idempotency,
                        )

                    try:
                        receipt = await _dispatch_whatsapp_with_protocol(
                            run.workspace_id,
                            phone,
                            message,
                            db,
                            idempotency_key=str(intent_record["idempotencyKey"]),
                            before_dispatch=before_dispatch,
                        )
                    except (
                        ExternalIntentBusyError,
                        ExternalIntentUncertainError,
                        ExternalIntentFencedError,
                    ):
                        return
                    except Exception as error:
                        if attempt_token:
                            await _record_flow_external_attempt_failure(
                                db,
                                run_id=resolved_run_id,
                                workspace_id=resolved_workspace_id,
                                node_id=node_id,
                                intent_key=intent_key,
                                attempt_token=attempt_token,
                                provider_supports_idempotency=(provider_supports_idempotency),
                                error=error,
                            )
                            return
                        raise

                    assert attempt_token is not None
                    _inject_fault(
                        "flow_external.after_provider_accept",
                        intent_key=intent_key,
                        attempt_token=attempt_token,
                        node_type=node_type,
                    )
                    checkpointed_run = await _checkpoint_flow_external_success(
                        db,
                        run_id=resolved_run_id,
                        workspace_id=resolved_workspace_id,
                        node_id=node_id,
                        intent_key=intent_key,
                        attempt_token=attempt_token,
                        provider_reference=_provider_reference(receipt),
                    )
                    if not checkpointed_run:
                        return
                    run = await _lock_flow_run(
                        db,
                        resolved_run_id,
                        resolved_workspace_id,
                    )
                    if (
                        not run
                        or run.status != "active"
                        or str(run.current_node_id or "") != node_id
                    ):
                        return
                    external_intent = _active_flow_external_intent(
                        run.context,
                        node_id=node_id,
                    )
                    if not external_intent:
                        return

            elif node_type == "wait":
                # Aguardar N horas — agendar próxima execução
                delay_hours = float(config.get("delay_hours", 24))
                delay_seconds = int(delay_hours * 3600)
                # Salvar estado e agendar próxima execução
                next_edges = [e for e in edges if e.get("source") == run.current_node_id]
                if next_edges:
                    next_node_id = next_edges[0].get("target")
                    record_flow_trace(
                        run,
                        node_id=node_id,
                        node_type=str(node_type),
                        label=node_label,
                        step=execution_step,
                        status="completed",
                    )
                    run.current_node_id = next_node_id
                    persisted_dispatch = None
                    if dispatch_source and next_node_id:
                        persisted_dispatch = _record_flow_step_dispatch(
                            run,
                            source=dispatch_source,
                            target_node_id=next_node_id,
                            available_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
                        )
                    elif next_node_id:
                        persisted_dispatch = _record_root_flow_step_dispatch(
                            run,
                            target_node_id=next_node_id,
                            available_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
                            source_type="legacy_wait_continuation",
                        )
                    await db.commit()
                    dispatch_token, dispatch_record = persisted_dispatch
                    _publish_flow_step_dispatch(
                        flow_run_id=flow_run_id,
                        workspace_id=resolved_workspace_id,
                        token=dispatch_token,
                        record=dispatch_record,
                    )
                else:
                    record_flow_trace(
                        run,
                        node_id=node_id,
                        node_type=str(node_type),
                        label=node_label,
                        step=execution_step,
                        status="completed",
                    )
                    run.status = "completed"
                    run.completed_at = datetime.now(UTC)
                    await db.commit()
                return  # Não continua agora

            elif node_type == "condition":
                # Avaliar condição e escolher edge "Sim" ou "Não"
                field = config.get("field", "")
                operator = config.get("operator") or "eq"
                value = config.get("value", "")
                context = run.context or {}
                sentinel = object()
                actual = get_context_value(context, str(field), sentinel)
                condition_met = evaluate_flow_condition(
                    None if actual is sentinel else actual,
                    operator,
                    value,
                    field_exists=actual is not sentinel,
                )
                condition_label = "true" if condition_met else "false"
                trace_decision = {
                    "field": str(field),
                    "operator": str(operator),
                    "expected": value,
                    "actual": None if actual is sentinel else actual,
                    "branch": condition_label,
                }
                # Encontrar edge correto
                matching_edges = [
                    e
                    for e in edges
                    if e.get("source") == run.current_node_id
                    and edge_route_key(e) == condition_label
                ]
                if not matching_edges:
                    matching_edges = [e for e in edges if e.get("source") == run.current_node_id]
                if matching_edges:
                    next_node_id = matching_edges[0].get("target")
                else:
                    should_continue = False

            elif node_type in {"human_pause", "human_transfer"}:
                # Pausar e aguardar ação humana
                reason = config.get("reason") or (
                    f"Transferir para {config.get('team') or config.get('queue')}"
                    if config.get("team") or config.get("queue")
                    else "Revisão humana necessária"
                )
                await _call_human_before_waiting(
                    db,
                    run,
                    node_id=node_id,
                    execution_step=execution_step,
                    reason=reason,
                    config=config,
                    purpose=node_type,
                )
                should_continue = False

            elif node_type in {
                "summarize",
                "classify_intent",
                "extract_data",
                "evaluate_risk",
                "evaluate_confidence",
                "draft_content",
                "review_content",
                "generate_alternatives",
                "compare_scenarios",
                "multi_agent_consensus",
                "finance_ledger_query",
                "finance_cost_center_query",
                "finance_forecast",
                "finance_valuation",
                "finance_insight",
                "finance_anomaly",
                "finance_allocation_suggest",
                "finance_reconcile",
            }:
                from app.services.agent_node_executor import agent_node_executors
                from app.services.finance_agent_adapters import register_finance_adapters

                register_finance_adapters()
                executor = agent_node_executors.resolve(str(node_type))
                ctx = AgentExecutionContext(
                    workspace_id=run.workspace_id,
                    context_type=str((run.context or {}).get("contextType") or "matter"),
                    context_id=getattr(run, "context_id", None) or run.matter_id,
                    permissions=frozenset((run.context or {}).get("permissions") or []),
                    deployment_id=getattr(run, "deployment_id", None),
                    correlation_id=f"flow:{run.id}:{node_id}:{execution_step}",
                    db=db,
                )
                try:
                    executor_payload = dict(config)
                    if node_type in {
                        "summarize",
                        "classify_intent",
                        "extract_data",
                        "evaluate_risk",
                        "evaluate_confidence",
                        "draft_content",
                        "review_content",
                        "generate_alternatives",
                        "compare_scenarios",
                        "multi_agent_consensus",
                    }:
                        from app.ai.factory import get_model_gateway

                        executor_payload["_provider"] = get_model_gateway()
                    output = await executor(ctx, executor_payload)
                except Exception as error:
                    output = AgentNodeOutput(
                        status="waiting_tool",
                        data={"reason": str(error)[:200]},
                        requires_human=True,
                    )
                trace_decision = {"status": output.status, **output.data}
                if output.status in {"waiting_tool", "waiting_human", "waiting_data"}:
                    await _call_human_before_waiting(
                        db,
                        run,
                        node_id=node_id,
                        execution_step=execution_step,
                        reason=str(output.data.get("reason") or "Revisão financeira necessária."),
                        config=config,
                        purpose="finance_human_review"
                        if str(node_type).startswith("finance_")
                        else "agent_tool_wait",
                    )
                    should_continue = False

            elif node_type in {"agent", "llm"}:
                # Acionar agente IA
                from app.services.flow_service import (
                    FLOW_ACTIVE_AGENT_RUN_KEY,
                    FLOW_AGENT_EXECUTIONS_KEY,
                )

                agent_name = config.get("agent", "klm_sdr")
                agent_config = {
                    "stage_key": config.get("stage_key") or "entrada",
                    "prompt": config.get("prompt"),
                    "model": config.get("model"),
                    "context_mode": config.get("context_mode"),
                    "use_rag": config.get("use_rag"),
                    "multimodal_enabled": config.get("multimodal_enabled"),
                    "human_review_required": config.get("human_review_required"),
                }
                from app.workers.event_bus import _enqueue_agent_run

                flow_step = step_count + 1
                agent_node_id = str(run.current_node_id)
                agent_run = await _enqueue_agent_run(
                    workspace_id=resolved_workspace_id,
                    matter_id=run.matter_id,
                    agent_name=agent_name,
                    trigger_source=f"flow:{run.flow_id}",
                    input_payload={
                        "flow_run_id": str(run.id),
                        "flow_revision_id": str(run.revision_id),
                        "flow_revision": revision.revision_number,
                        "flow_node_id": agent_node_id,
                        "flow_step": flow_step,
                        "stage_key": agent_config["stage_key"],
                        "agent_config": agent_config,
                    },
                    dedupe_key=(
                        f"flow-agent:{run.id}:{run.revision_id}:{agent_node_id}:{flow_step}"
                    ),
                )
                if not agent_run:
                    run.status = "failed"
                    run.completed_at = None
                    run.error_message = "Nó agent não criou um AgentRun revisável"
                    run.context = {
                        **(run.context or {}),
                        "flowAgentDispatchFailure": {
                            "revisionId": str(run.revision_id),
                            "nodeId": agent_node_id,
                            "step": flow_step,
                            "failedAt": datetime.now(UTC).isoformat(),
                        },
                    }
                    should_continue = False
                else:
                    agent_run_id = str(agent_run.id)
                    agent_status = (
                        agent_run.status.value
                        if hasattr(agent_run.status, "value")
                        else str(agent_run.status)
                    )
                    now = datetime.now(UTC)
                    context = dict(run.context or {})
                    raw_executions = context.get(FLOW_AGENT_EXECUTIONS_KEY)
                    executions = dict(raw_executions) if isinstance(raw_executions, dict) else {}
                    previous = executions.get(agent_run_id)
                    checkpoint = dict(previous) if isinstance(previous, dict) else {}
                    checkpoint.update(
                        {
                            "flowRunId": str(run.id),
                            "revisionId": str(run.revision_id),
                            "revision": revision.revision_number,
                            "nodeId": agent_node_id,
                            "step": flow_step,
                            "agentRunId": agent_run_id,
                            "agentName": agent_run.agent_name,
                            "agentRunStatus": agent_status,
                            "state": agent_status,
                            "linkedAt": checkpoint.get("linkedAt") or now.isoformat(),
                            "updatedAt": now.isoformat(),
                        }
                    )
                    executions[agent_run_id] = checkpoint
                    context[FLOW_AGENT_EXECUTIONS_KEY] = executions
                    context[FLOW_ACTIVE_AGENT_RUN_KEY] = agent_run_id
                    run.context = context
                    # The agent is queued/running independently.  This is a
                    # polling state, not a human handoff; reserving
                    # waiting_human for a canonical HumanDecision prevents
                    # an invisible wait with no assignee, SLA or owner alert.
                    run.status = "active"
                    run.completed_at = None
                    run.paused_at = None
                    run.pause_reason = f"Aguardando conclusão do AgentRun {agent_run_id}."
                    run.error_message = None
                    agent_run_to_settle = agent_run.id
                should_continue = False

            elif node_type == "create_task":
                task_intent = f"{run.id}:{run.revision_id}:{node_id}:{execution_step}"
                task_key = (
                    f"flow-task:{run.id}:{hashlib.sha256(task_intent.encode()).hexdigest()[:32]}"
                )
                raw_priority = str(config.get("task_priority") or "media")
                try:
                    priority = TaskPriority(raw_priority)
                except ValueError:
                    priority = TaskPriority.media
                resolved_description = str(
                    resolve_expression(
                        config.get("task_description") or "",
                        run.context or {},
                    )
                )
                task = await TaskService(db).create_automatic(
                    run.matter_id,
                    TaskCreate(
                        title=str(
                            resolve_expression(
                                config.get("task_title") or "Tarefa do fluxo",
                                run.context or {},
                            )
                        )[:500],
                        description=resolved_description[:10000] or None,
                        priority=priority,
                        is_ai_generated=True,
                        idempotency_key=task_key,
                    ),
                    run.workspace_id,
                    source="flow.create_task",
                    actor_user_id=revision.created_by_user_id,
                    correlation_id=(f"flow:create_task:{run.id}:{node_id}:{execution_step}"),
                )
                trace_decision = {"taskId": str(task.id), "idempotent": True}

            elif node_type == "update_card":
                from sqlalchemy import select as sa_select

                from app.models.matter import (
                    HealthSubtheme,
                    KanbanColumnId,
                    Matter,
                    MicroStatus,
                    Priority,
                    Urgency,
                )
                from app.schemas.flow import FLOW_CARD_UPDATE_FIELDS

                matter = await db.scalar(
                    sa_select(Matter).where(
                        Matter.id == run.matter_id,
                        Matter.workspace_id == run.workspace_id,
                        Matter.deleted_at.is_(None),
                    )
                )
                if matter is None:
                    raise ValueError("Caso do flow run não encontrado")
                raw_updates = config.get("updates")
                if not isinstance(raw_updates, dict) or not raw_updates:
                    raise ValueError("Nó update_card sem campos configurados")
                forbidden = set(raw_updates) - FLOW_CARD_UPDATE_FIELDS
                if forbidden:
                    raise ValueError(f"Campos não permitidos no card: {sorted(forbidden)}")
                enum_fields = {
                    "column_id": KanbanColumnId,
                    "micro_status": MicroStatus,
                    "priority": Priority,
                    "urgency": Urgency,
                    "health_subtheme": HealthSubtheme,
                }
                applied: dict[str, Any] = {}
                for field, expression in raw_updates.items():
                    value = resolve_expression(expression, run.context or {})
                    enum_type = enum_fields.get(field)
                    if enum_type and value not in {None, ""}:
                        value = enum_type(str(value))
                    if field == "tags" and not isinstance(value, list):
                        value = [item.strip() for item in str(value).split(",") if item.strip()]
                    setattr(matter, field, value)
                    applied[field] = value.value if hasattr(value, "value") else value
                trace_decision = {"updatedFields": sorted(applied)}
                run.context = {**(run.context or {}), "lastCardUpdate": applied}

            elif node_type == "internal_note":
                from sqlalchemy import select as sa_select

                from app.models.klm_operations import (
                    MessageAuthorType,
                    MessageDeliveryStatus,
                    MessageDirection,
                    OmnichannelChannel,
                    OmnichannelMessage,
                    OmnichannelThread,
                )

                raw_thread_id = (run.context or {}).get("threadId") or (run.context or {}).get(
                    "thread_id"
                )
                if not raw_thread_id:
                    raise ValueError("Nota interna exige conversa vinculada ao flow")
                thread_id = UUID(str(raw_thread_id))
                thread = await db.scalar(
                    sa_select(OmnichannelThread).where(
                        OmnichannelThread.id == thread_id,
                        OmnichannelThread.workspace_id == run.workspace_id,
                        OmnichannelThread.matter_id == run.matter_id,
                    )
                )
                if thread is None:
                    raise ValueError("Conversa da nota interna não pertence ao caso")
                note_key = f"flow-note:{run.id}:{run.revision_id}:{node_id}:{execution_step}"
                note = await db.scalar(
                    sa_select(OmnichannelMessage).where(
                        OmnichannelMessage.workspace_id == run.workspace_id,
                        OmnichannelMessage.idempotency_key == note_key,
                    )
                )
                if note is None:
                    note = OmnichannelMessage(
                        workspace_id=run.workspace_id,
                        thread_id=thread.id,
                        matter_id=run.matter_id,
                        channel=OmnichannelChannel.internal,
                        direction=MessageDirection.internal,
                        author_type=MessageAuthorType.system,
                        author_label="KLM Flows",
                        body=str(
                            resolve_expression(
                                config.get("note") or config.get("message") or "",
                                run.context or {},
                            )
                        ),
                        audience="internal_only",
                        transport="local",
                        idempotency_key=note_key,
                        delivery_status=MessageDeliveryStatus.received,
                        message_metadata={
                            "visibility": "internal_only",
                            "customerVisible": False,
                            "source": "flow_internal_note",
                            "flowRunId": str(run.id),
                            "flowRevisionId": str(run.revision_id),
                            "nodeId": node_id,
                        },
                    )
                    db.add(note)
                    await db.flush()
                trace_decision = {
                    "internalNoteId": str(note.id),
                    "customerVisible": False,
                }

            elif node_type == "audit_event":
                from sqlalchemy import select as sa_select

                from app.models.klm_operations import KlmAuditEvent

                event_type = str(config.get("event_type") or "flow_checkpoint")
                correlation_id = f"flow-audit:{run.id}:{run.revision_id}:{node_id}:{execution_step}"
                event = await db.scalar(
                    sa_select(KlmAuditEvent).where(
                        KlmAuditEvent.workspace_id == run.workspace_id,
                        KlmAuditEvent.event_type == event_type,
                        KlmAuditEvent.correlation_id == correlation_id,
                    )
                )
                if event is None:
                    event = KlmAuditEvent(
                        workspace_id=run.workspace_id,
                        actor_user_id=None,
                        actor_type="system",
                        actor_label="KLM Flows",
                        event_type=event_type,
                        entity_type="flow_run",
                        entity_id=run.id,
                        summary=str(
                            resolve_expression(
                                config.get("summary") or node_label,
                                run.context or {},
                            )
                        ),
                        correlation_id=correlation_id,
                        after_data={
                            "flowId": str(run.flow_id),
                            "revisionId": str(run.revision_id),
                            "nodeId": node_id,
                            "step": execution_step,
                        },
                    )
                    db.add(event)
                    await db.flush()
                trace_decision = {
                    "auditEventId": str(event.id),
                    "eventType": event_type,
                }

            elif node_type in {"connector_action", "action"}:
                wait_reason = (
                    f"Conector {config.get('connector')} / {config.get('operation')} "
                    "aguarda credencial e aprovação de execução externa."
                )
                await _call_human_before_waiting(
                    db,
                    run,
                    node_id=node_id,
                    execution_step=execution_step,
                    reason=wait_reason,
                    config=config,
                    purpose="connector_approval",
                )
                run.context = {
                    **(run.context or {}),
                    "blockedConnectorAction": {
                        "nodeId": node_id,
                        "connector": config.get("connector"),
                        "operation": config.get("operation"),
                        "externalActionsExecuted": 0,
                    },
                }
                should_continue = False

            elif node_type == "end":
                run.status = "completed"
                run.completed_at = datetime.now(UTC)
                run.context = {
                    **(run.context or {}),
                    "flowEnded": {
                        "status": config.get("end_status") or "completed",
                        "reason": config.get("reason") or "Fluxo encerrado",
                    },
                }
                should_continue = False

            elif node_type in {"payment_request", "social_action"}:
                action_label = (
                    "solicitação de pagamento"
                    if node_type == "payment_request"
                    else f"ação social {config.get('social_action')}"
                )
                wait_reason = (
                    f"A {action_label} foi preparada, mas aguarda a conexão oficial "
                    "e a liberação explícita da execução externa."
                )
                await _call_human_before_waiting(
                    db,
                    run,
                    node_id=node_id,
                    execution_step=execution_step,
                    reason=wait_reason,
                    config=config,
                    purpose=f"{node_type}_approval",
                )
                run.context = {
                    **(run.context or {}),
                    "blockedExternalTool": {
                        "nodeId": node_id,
                        "nodeType": node_type,
                        "config": {
                            key: value
                            for key, value in config.items()
                            if key
                            not in {
                                "credential",
                                "secret",
                                "token",
                                "api_key",
                                "apiKey",
                            }
                        },
                        "externalActionsExecuted": 0,
                    },
                }
                should_continue = False

            elif node_type == "zapsign_request":
                await EntitlementEnforcementService(db).enforce_feature(
                    workspace_id=run.workspace_id,
                    entitlement=EntitlementKey.digital_signature,
                )
                _require_external_delivery("Solicitação de assinatura")
                provider = str(config.get("provider") or "zapsign")
                from sqlalchemy import select as sa_select

                from app.models.matter import Matter

                matter_result = await db.execute(
                    sa_select(Matter).where(
                        Matter.id == run.matter_id,
                        Matter.workspace_id == run.workspace_id,
                        Matter.deleted_at.is_(None),
                    )
                )
                matter = matter_result.scalar_one_or_none()
                if not matter:
                    raise ValueError("Caso do flow run não encontrado")
                assert external_intent is not None
                intent_key, intent_record = external_intent
                if intent_record.get("state") != "confirmed" and provider != "zapsign":
                    integration_notes = config.get("integration_notes")
                    task_intent = f"{run.id}:{run.revision_id}:{node_id}:{execution_step}"
                    task_key = (
                        f"flow-signature:{run.id}:"
                        f"{hashlib.sha256(task_intent.encode()).hexdigest()[:32]}"
                    )
                    task_record = await TaskService(db).create_automatic(
                        matter.id,
                        TaskCreate(
                            title=f"Concluir etapa de assinatura via {provider}"[:500],
                            description=(
                                str(integration_notes)[:10000]
                                if integration_notes
                                else "Revisar e concluir a assinatura no integrador configurado."
                            ),
                            priority=TaskPriority.alta,
                            is_ai_generated=True,
                            idempotency_key=task_key,
                        ),
                        run.workspace_id,
                        source="flow.signature_manual_fallback",
                        actor_user_id=revision.created_by_user_id,
                        preferred_owner_id=matter.responsible_human_id,
                        correlation_id=(f"flow:signature:{run.id}:{node_id}:{execution_step}"),
                    )
                    _inject_fault(
                        "flow_external.after_local_effect_flush",
                        intent_key=intent_key,
                        node_type=node_type,
                    )
                    run.context = {
                        **(run.context or {}),
                        "signature_provider": provider,
                        "signature_doc_type": config.get("doc_type"),
                    }
                    _update_flow_external_intent(
                        run,
                        intent_key,
                        state="confirmed",
                        confirmedAt=datetime.now(UTC).isoformat(),
                        providerReference=str(task_record.id),
                    )
                    external_intent = _active_flow_external_intent(
                        run.context,
                        node_id=node_id,
                    )
                elif intent_record.get("state") != "confirmed":
                    from app.schemas.zapsign import ZapSignSigner
                    from app.services.zapsign_service import get_zapsign_service

                    attempt_token: str | None = None
                    try:
                        zapsign = await get_zapsign_service(
                            workspace_id=run.workspace_id,
                            db=db,
                        )
                        ctx = run.context or {}
                        signers = [
                            ZapSignSigner(
                                name=ctx.get("nome") or ctx.get("client_name") or "Cliente",
                                phone_number=(ctx.get("phone") or "").replace("+55", ""),
                                auth_mode=config.get("auth_mode") or "assinaturaTela-tokenEmail",
                            )
                        ]
                        doc_name = config.get("doc_type", "Contrato")
                        attempt_token = await _claim_flow_external_attempt(
                            db,
                            run,
                            intent_key=intent_key,
                            provider_supports_idempotency=False,
                        )
                        doc = await zapsign.create_document(
                            name=f"{doc_name} — {matter.demand_type or ''}",
                            signers=signers,
                            external_id=str(matter.id),
                        )
                        _inject_fault(
                            "flow_external.after_provider_accept",
                            intent_key=intent_key,
                            attempt_token=attempt_token,
                            node_type=node_type,
                        )
                        matter.zapsign_doc_token = doc.token
                        matter.zapsign_sign_url = doc.sign_url
                        matter.zapsign_status = "pending"
                        matter.column_id = "assinatura_pendente"
                        checkpointed_run = await _checkpoint_flow_external_success(
                            db,
                            run_id=resolved_run_id,
                            workspace_id=resolved_workspace_id,
                            node_id=node_id,
                            intent_key=intent_key,
                            attempt_token=attempt_token,
                            provider_reference=str(doc.token),
                            context_updates={"sign_url": doc.sign_url or ""},
                        )
                        if not checkpointed_run:
                            return
                    except (
                        ExternalIntentBusyError,
                        ExternalIntentUncertainError,
                        ExternalIntentFencedError,
                    ):
                        return
                    except Exception as error:
                        logger.warning(
                            "flow.zapsign_failed",
                            run_id=flow_run_id,
                            error_type=safe_error_type(error),
                        )
                        if attempt_token:
                            await _record_flow_external_attempt_failure(
                                db,
                                run_id=resolved_run_id,
                                workspace_id=resolved_workspace_id,
                                node_id=node_id,
                                intent_key=intent_key,
                                attempt_token=attempt_token,
                                provider_supports_idempotency=False,
                                error=error,
                            )
                            return
                        raise

                    try:
                        from app.services.operational_alert_service import (
                            OperationalAlertService,
                        )

                        await OperationalAlertService(db).notify_contract_sent(matter.id)
                    except Exception as alert_error:
                        logger.warning(
                            "flow.zapsign_alert_failed",
                            run_id=flow_run_id,
                            error_type=safe_error_type(alert_error),
                        )
                        await db.rollback()

                    run = await _lock_flow_run(
                        db,
                        resolved_run_id,
                        resolved_workspace_id,
                    )
                    if (
                        not run
                        or run.status != "active"
                        or str(run.current_node_id or "") != node_id
                    ):
                        return
                    external_intent = _active_flow_external_intent(
                        run.context,
                        node_id=node_id,
                    )
                    if not external_intent:
                        return

            elif node_type == "scheduled_message":
                _require_external_delivery("Agendamento de mensagem")
                from sqlalchemy import select as sa_select

                from app.models.matter import Matter
                from app.services.whatsapp.helpers import normalize_phone_number

                matter_result = await db.execute(
                    sa_select(Matter).where(
                        Matter.id == run.matter_id,
                        Matter.workspace_id == run.workspace_id,
                        Matter.deleted_at.is_(None),
                    )
                )
                matter = matter_result.scalar_one_or_none()
                if not matter:
                    raise ValueError("Caso do flow run não encontrado")
                assert external_intent is not None
                intent_key, intent_record = external_intent

                sequence_status = sequence_template_status(config)
                sequence_bindings = normalize_sequence_bindings(config)
                delay_value, delay_unit = normalize_sequence_delay(config)
                tariff = sequence_tariff(config)
                message_template = str(config.get("message", ""))
                if sequence_bindings and config.get("sequence_stage"):
                    # Field-backed sequence values are deliberately resolved
                    # by the delivery worker, immediately before dispatch.
                    # Materialization must not snapshot a stale context (and
                    # must not fail when a later field is absent at start).
                    message = message_template
                elif sequence_bindings:
                    message = resolve_sequence_message(
                        message_template,
                        config,
                        run.context or {},
                    )
                else:
                    message = _interpolate(message_template, run.context or {})
                phone = (run.context or {}).get("phone") or (run.context or {}).get(
                    "contact_phone", ""
                )
                scheduled_at_str = config.get("scheduled_at") or config.get("scheduled_time")
                if not phone:
                    raise ValueError("Nó scheduled_message sem telefone de destino")
                phone = normalize_phone_number(str(phone))
                if phone is None:
                    raise ValueError(
                        "Nó scheduled_message com telefone inválido; use DDI, DDD e número."
                    )
                send_now = config.get("send_now") is True or (
                    delay_value == 0 and delay_unit == "hours"
                )
                if not scheduled_at_str and delay_value is None and not send_now:
                    raise ValueError("Nó scheduled_message sem horário ou delay")

                import re

                now = datetime.now(UTC)
                if send_now:
                    scheduled_at = now
                elif delay_value is not None:
                    delay_seconds = delay_value * (86400 if delay_unit == "days" else 3600)
                    scheduled_at = now + timedelta(seconds=delay_seconds)
                else:
                    time_match = re.fullmatch(
                        r"([01]?\d|2[0-3]):([0-5]\d)", str(scheduled_at_str)
                    )
                    if not time_match:
                        raise ValueError("Horário inválido no nó scheduled_message")
                    h, m = int(time_match.group(1)), int(time_match.group(2))
                    scheduled_at = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    if scheduled_at <= now:
                        scheduled_at += timedelta(days=1)
                if intent_record.get("state") != "confirmed":
                    scheduled_message_id = uuid4()
                    sm = ScheduledMessage(
                        id=scheduled_message_id,
                        matter_id=run.matter_id,
                        workspace_id=run.workspace_id,
                        contact_phone=phone,
                        message=message,
                        scheduled_at=scheduled_at,
                        flow_run_id=run.id,
                        stage_id=node_id,
                        template_id=(
                            UUID(str(config.get("template_id")))
                            if config.get("template_id")
                            else None
                        ),
                        whatsapp_connection_id=(
                            UUID(
                                str(
                                    config.get("whatsapp_connection_id")
                                    or config.get("connection_id")
                                )
                            )
                            if config.get("whatsapp_connection_id")
                            or config.get("connection_id")
                            else None
                        ),
                        template_name=(
                            str(config.get("template_name"))
                            if config.get("template_name")
                            else None
                        ),
                        template_language=(
                            str(config.get("template_language"))
                            if config.get("template_language")
                            else None
                        ),
                        template_status=sequence_status,
                        template_bindings=sequence_bindings,
                        delay_value=delay_value,
                        delay_unit=delay_unit,
                        send_now=send_now,
                        interrupt_on_reply=bool(config.get("interrupt_on_reply", True)),
                        tariff_status=tariff["status"],
                        tariff_amount=tariff["amount"],
                        status="pending",
                        idempotency_key=(f"scheduled-message:{scheduled_message_id}"),
                    )
                    db.add(sm)
                    await db.flush()
                    _inject_fault(
                        "flow_external.after_local_effect_flush",
                        intent_key=intent_key,
                        node_type=node_type,
                    )
                    _update_flow_external_intent(
                        run,
                        intent_key,
                        state="confirmed",
                        confirmedAt=datetime.now(UTC).isoformat(),
                        providerReference=str(scheduled_message_id),
                    )
                    external_intent = _active_flow_external_intent(
                        run.context,
                        node_id=node_id,
                    )

            # ── Avançar para próximo nó ──────────────────────────────
            trace_status = (
                "failed"
                if run.status == "failed"
                else "waiting_human"
                if run.status == "waiting_human"
                else "completed"
            )
            if trace_status == "completed":
                _mark_flow_node_recovered(run, node_id=node_id)
            record_flow_trace(
                run,
                node_id=node_id,
                node_type=str(node_type),
                label=node_label,
                step=execution_step,
                status=trace_status,
                decision=trace_decision,
            )
            if should_continue:
                persisted_status = await db.scalar(
                    select(FlowRun.status).where(FlowRun.id == run.id)
                )
                if persisted_status != "active":
                    await db.rollback()
                    return
                if node_type in EXTERNAL_FLOW_NODE_TYPES:
                    active_intent = _active_flow_external_intent(
                        run.context,
                        node_id=node_id,
                    )
                    if not active_intent or active_intent[1].get("state") != "confirmed":
                        await db.rollback()
                        return
                    _consume_flow_external_intent(run, active_intent[0])

            if should_continue and not next_node_id:
                # Buscar próximo nó pelo edge
                next_edges = [e for e in edges if e.get("source") == run.current_node_id]
                if next_edges:
                    next_node_id = next_edges[0].get("target")

            if should_continue and next_node_id:
                run.current_node_id = next_node_id
                if dispatch_source:
                    persisted_dispatch = _record_flow_step_dispatch(
                        run,
                        source=dispatch_source,
                        target_node_id=next_node_id,
                        available_at=datetime.now(UTC),
                    )
                else:
                    persisted_dispatch = _record_root_flow_step_dispatch(
                        run,
                        target_node_id=next_node_id,
                        available_at=datetime.now(UTC),
                        source_type="legacy_step_continuation",
                    )
                await db.commit()
                # Continuar imediatamente (exceto wait). A intenção precede o broker.
                dispatch_token, dispatch_record = persisted_dispatch
                _publish_flow_step_dispatch(
                    flow_run_id=flow_run_id,
                    workspace_id=resolved_workspace_id,
                    token=dispatch_token,
                    record=dispatch_record,
                )
            elif should_continue and not next_node_id:
                run.status = "completed"
                run.completed_at = datetime.now(UTC)
                await db.commit()
            else:
                await db.commit()
                if agent_run_to_settle:
                    from app.services.flow_service import FlowService

                    await FlowService(db).settle_agent_run(
                        agent_run_to_settle,
                        resolved_workspace_id,
                    )

        except FlowStepDispatchError:
            await db.rollback()
            raise
        except Exception as e:
            logger.error(
                "flow_run.step_failed",
                run_id=flow_run_id,
                error_type=safe_error_type(e),
            )
            error_dispatch: tuple[str, dict[str, Any]] | None = None
            try:
                await db.rollback()
                await set_workspace_context(db, resolved_workspace_id)
                result = await db.execute(
                    select(FlowRun)
                    .where(
                        FlowRun.id == resolved_run_id,
                        FlowRun.workspace_id == resolved_workspace_id,
                    )
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
                run = result.scalar_one_or_none()
                if run and run.status == "active":
                    revision = await db.scalar(
                        select(FlowRevision).where(
                            FlowRevision.id == run.revision_id,
                            FlowRevision.flow_id == run.flow_id,
                        )
                    )
                    snapshot: dict[str, Any] = {}
                    if revision:
                        try:
                            snapshot = verified_flow_revision_snapshot(revision)
                        except Exception:
                            snapshot = {}

                    node_id = str(run.current_node_id or "")
                    current_node = next(
                        (
                            node
                            for node in snapshot.get("nodes", [])
                            if isinstance(node, dict) and str(node.get("id") or "") == node_id
                        ),
                        None,
                    )
                    node_type = str((current_node or {}).get("type") or "unknown")
                    node_label = str(
                        ((current_node or {}).get("data") or {}).get("label")
                        if isinstance((current_node or {}).get("data"), dict)
                        else ""
                    ) or str((current_node or {}).get("label") or node_id or "Etapa")
                    config = _node_config(current_node or {})
                    requested_strategy = str(config.get("error_strategy") or "fail")
                    strategy = (
                        requested_strategy
                        if requested_strategy in {"fail", "retry", "route", "wait_human"}
                        else "fail"
                    )
                    try:
                        max_retries = max(0, min(int(config.get("max_retries", 3)), 10))
                    except (TypeError, ValueError):
                        max_retries = 3
                    try:
                        retry_delay = max(
                            0,
                            min(int(config.get("retry_delay_seconds", 60)), 86400),
                        )
                    except (TypeError, ValueError):
                        retry_delay = 60

                    context = dict(run.context or {})
                    raw_retries = context.get(FLOW_NODE_RETRIES_KEY)
                    retries = dict(raw_retries) if isinstance(raw_retries, dict) else {}
                    try:
                        retry_count = max(0, int(retries.get(node_id, 0) or 0))
                    except (TypeError, ValueError):
                        retry_count = 0
                    occurred_at = datetime.now(UTC)
                    error_step = _flow_error_step(run, node_id=node_id)
                    dispatch_source = _consume_error_dispatch_source(
                        run,
                        continuation_token,
                    )
                    automatic_retry_blocked = False
                    if strategy == "retry" and node_type in EXTERNAL_FLOW_NODE_TYPES:
                        strategy = "wait_human"
                        automatic_retry_blocked = True

                    handled = False
                    if strategy == "retry" and retry_count < max_retries:
                        retry_attempt = retry_count + 1
                        context = dict(run.context or {})
                        raw_retries = context.get(FLOW_NODE_RETRIES_KEY)
                        retries = dict(raw_retries) if isinstance(raw_retries, dict) else {}
                        retries[node_id] = retry_attempt
                        context[FLOW_NODE_RETRIES_KEY] = retries
                        run.context = context
                        retry_at = occurred_at + timedelta(seconds=retry_delay)
                        _remember_flow_error(
                            run,
                            node_id=node_id,
                            node_type=node_type,
                            error=e,
                            strategy="retry",
                            requested_strategy=requested_strategy,
                            attempt=retry_attempt,
                            max_retries=max_retries,
                            status="retry_scheduled",
                            occurred_at=occurred_at,
                        )
                        record_flow_trace(
                            run,
                            node_id=node_id,
                            node_type=node_type,
                            label=node_label,
                            step=error_step,
                            status="retrying",
                            decision={
                                "strategy": "retry",
                                "attempt": retry_attempt,
                                "maxRetries": max_retries,
                                "retryAt": retry_at.isoformat(),
                                "errorType": safe_error_type(e),
                            },
                        )
                        run.error_message = None
                        run.completed_at = None
                        run.paused_at = None
                        run.pause_reason = None
                        error_dispatch = _record_error_recovery_dispatch(
                            run,
                            source=dispatch_source,
                            target_node_id=node_id,
                            available_at=retry_at,
                            source_type="node_error_retry",
                        )
                        handled = True

                    if not handled and strategy == "route":
                        edges = snapshot.get("edges") or []
                        error_target = next(
                            (
                                str(edge.get("target"))
                                for edge in _outgoing_flow_edges(edges, node_id)
                                if edge_route_key(edge) == "error" and edge.get("target")
                            ),
                            None,
                        )
                        if error_target:
                            context = dict(run.context or {})
                            context["flowStepCount"] = max(
                                int(context.get("flowStepCount", 0) or 0),
                                error_step,
                            )
                            run.context = context
                            _remember_flow_error(
                                run,
                                node_id=node_id,
                                node_type=node_type,
                                error=e,
                                strategy="route",
                                requested_strategy=requested_strategy,
                                attempt=retry_count + 1,
                                max_retries=max_retries,
                                status="routed",
                                occurred_at=occurred_at,
                            )
                            record_flow_trace(
                                run,
                                node_id=node_id,
                                node_type=node_type,
                                label=node_label,
                                step=error_step,
                                status="routed",
                                decision={
                                    "strategy": "route",
                                    "errorRoute": error_target,
                                    "errorType": safe_error_type(e),
                                },
                            )
                            run.current_node_id = error_target
                            run.error_message = None
                            error_dispatch = _record_error_recovery_dispatch(
                                run,
                                source=dispatch_source,
                                target_node_id=error_target,
                                available_at=occurred_at,
                                source_type="node_error_route",
                            )
                            handled = True
                        else:
                            strategy = "fail"

                    if not handled and strategy == "wait_human":
                        context = dict(run.context or {})
                        context["flowStepCount"] = max(
                            int(context.get("flowStepCount", 0) or 0),
                            error_step,
                        )
                        run.context = context
                        _remember_flow_error(
                            run,
                            node_id=node_id,
                            node_type=node_type,
                            error=e,
                            strategy="wait_human",
                            requested_strategy=requested_strategy,
                            attempt=retry_count + 1,
                            max_retries=max_retries,
                            status="waiting_human",
                            occurred_at=occurred_at,
                            automatic_retry_blocked=automatic_retry_blocked,
                        )
                        record_flow_trace(
                            run,
                            node_id=node_id,
                            node_type=node_type,
                            label=node_label,
                            step=error_step,
                            status="waiting_human",
                            decision={
                                "strategy": "wait_human",
                                "errorType": safe_error_type(e),
                            },
                        )
                        wait_reason = f"Falha em {node_label}; revisão humana necessária."
                        await _call_human_before_waiting(
                            db,
                            run,
                            node_id=node_id,
                            execution_step=error_step,
                            reason=wait_reason,
                            config=config,
                            purpose="node_error_review",
                        )
                        run.error_message = str(e)[:1000]
                        handled = True

                    if not handled:
                        context = dict(run.context or {})
                        context["flowStepCount"] = max(
                            int(context.get("flowStepCount", 0) or 0),
                            error_step,
                        )
                        run.context = context
                        _remember_flow_error(
                            run,
                            node_id=node_id,
                            node_type=node_type,
                            error=e,
                            strategy="fail",
                            requested_strategy=requested_strategy,
                            attempt=retry_count + 1,
                            max_retries=max_retries,
                            status="failed",
                            occurred_at=occurred_at,
                        )
                        record_flow_trace(
                            run,
                            node_id=node_id,
                            node_type=node_type,
                            label=node_label,
                            step=error_step,
                            status="failed",
                            decision={
                                "strategy": "fail",
                                "errorType": safe_error_type(e),
                            },
                        )
                        run.status = "failed"
                        run.completed_at = None
                        run.error_message = str(e)[:1000]
                    await db.commit()
            except Exception as status_error:
                logger.error(
                    "flow_run.failure_status_update_failed",
                    run_id=flow_run_id,
                    error_type=safe_error_type(status_error),
                )
            if error_dispatch:
                dispatch_token, dispatch_record = error_dispatch
                _publish_flow_step_dispatch(
                    flow_run_id=flow_run_id,
                    workspace_id=resolved_workspace_id,
                    token=dispatch_token,
                    record=dispatch_record,
                )


@celery_app.task(name="flow_tasks.process_scheduled_messages")
def process_scheduled_messages() -> int:
    """
    Task periódica: a cada minuto, envia mensagens pendentes com scheduled_at <= now().
    Deve ser agendada via Celery Beat.
    """
    return _run_async(_async_process_scheduled_messages())


@celery_app.task(name="flow_tasks.process_human_escalations")
def process_human_escalations() -> dict[str, int]:
    """Escalate overdue calls and dispatch durable phone notifications."""
    return _run_async(_async_process_human_escalations())


async def _async_process_human_escalations() -> dict[str, int]:
    from sqlalchemy import select

    from app.db.session import WorkerSessionLocal
    from app.models.agent_platform import (
        HumanDecision,
        HumanDecisionStatus,
        HumanDeliveryStatus,
        HumanNotificationDelivery,
    )
    from app.models.klm_operations import KlmAuditEvent
    from app.models.notification import NotificationType
    from app.models.user import User
    from app.models.workspace import Workspace
    from app.services.agent_platform_service import HumanRoutingService
    from app.services.notification_service import NotificationService

    now = datetime.now(UTC)
    counts = {
        "escalated": 0,
        "orphanRecovered": 0,
        "orphanFailed": 0,
        "phoneDelivered": 0,
        "phoneFailed": 0,
    }
    async with WorkerSessionLocal() as discovery:
        # The decision and delivery tables are protected by FORCE RLS. Discover
        # tenant IDs from the canonical workspace catalog, then bind each
        # transaction before reading pending work. A cross-tenant scan here
        # would be invisible to the least-privilege worker role in production.
        workspace_ids = list(
            (await discovery.scalars(select(Workspace.id).order_by(Workspace.id))).all()
        )
    for workspace_id in workspace_ids:
        async with WorkerSessionLocal() as db:
            await set_workspace_context(db, workspace_id)
            decisions = list(
                (
                    await db.scalars(
                        select(HumanDecision)
                        .where(
                            HumanDecision.workspace_id == workspace_id,
                            HumanDecision.status == HumanDecisionStatus.pending,
                            HumanDecision.due_at <= now,
                        )
                        .with_for_update(skip_locked=True)
                        .limit(50)
                    )
                ).all()
            )
            router = HumanRoutingService(db)
            for decision in decisions:
                if await router.escalate(decision):
                    counts["escalated"] += 1
                if decision.status == HumanDecisionStatus.expired and decision.flow_run_id:
                    from app.models.flow import FlowRun

                    flow_run = await db.scalar(
                        select(FlowRun)
                        .where(
                            FlowRun.id == decision.flow_run_id,
                            FlowRun.workspace_id == workspace_id,
                        )
                        .with_for_update()
                    )
                    if flow_run is not None and flow_run.status == "waiting_human":
                        flow_run.status = "failed"
                        flow_run.pause_reason = (
                            "Intervenção humana expirou após a hierarquia finita ser notificada."
                        )
                # A watchdog-created Flow decision intentionally links the
                # FlowRun and the AgentRun.  Both must reach a terminal state
                # when the finite human route expires; ``elif`` used to leave
                # the AgentRun parked indefinitely in that legitimate case.
                if decision.status == HumanDecisionStatus.expired and decision.agent_run_id:
                    from app.models.agent_run import AgentRun, AgentRunStatus

                    agent_run = await db.scalar(
                        select(AgentRun)
                        .where(
                            AgentRun.id == decision.agent_run_id,
                            AgentRun.workspace_id == workspace_id,
                        )
                        .with_for_update()
                    )
                    if agent_run is not None and agent_run.status == AgentRunStatus.waiting_human:
                        agent_run.status = AgentRunStatus.failed
                        agent_run.error_message = (
                            "Intervenção humana expirou após a hierarquia finita ser notificada."
                        )
                        agent_run.completed_at = datetime.now(UTC)

            # A FlowRun may have been created by legacy/local code, or the
            # process may have crashed between persisting waiting_human and
            # its decision.  Never leave that state unattended.  A pending or
            # decided decision is authoritative; an expired/cancelled one is
            # terminal and must not be recreated into an escalation loop.
            from app.models.flow import FlowRevision, FlowRun, verified_flow_revision_snapshot

            orphaned_runs = list(
                (
                    await db.scalars(
                        select(FlowRun)
                        .where(
                            FlowRun.workspace_id == workspace_id,
                            FlowRun.status == "waiting_human",
                        )
                        .with_for_update(skip_locked=True)
                        .limit(50)
                    )
                ).all()
            )
            for flow_run in orphaned_runs:
                linked_statuses = set(
                    (
                        await db.scalars(
                            select(HumanDecision.status).where(
                                HumanDecision.workspace_id == workspace_id,
                                HumanDecision.flow_run_id == flow_run.id,
                            )
                        )
                    ).all()
                )
                if linked_statuses & {
                    HumanDecisionStatus.pending,
                    HumanDecisionStatus.decided,
                }:
                    continue
                if linked_statuses:
                    flow_run.status = "failed"
                    flow_run.error_message = (
                        "Handoff humano terminal não retomou o fluxo; "
                        "intervenção manual necessária."
                    )
                    flow_run.pause_reason = "Decisão humana anterior expirou ou foi cancelada."
                    db.add(
                        KlmAuditEvent(
                            workspace_id=workspace_id,
                            actor_user_id=None,
                            actor_type="system",
                            actor_label="Watchdog de handoff humano",
                            event_type="human_handoff_orphan_terminal",
                            entity_type="flow_run",
                            entity_id=flow_run.id,
                            correlation_id=f"human-handoff-watchdog:{flow_run.id}",
                            summary="Flow aguardava humano após decisão terminal e foi encerrado.",
                            after_data={
                                "status": "failed",
                                "decisionStatuses": sorted(str(s) for s in linked_statuses),
                            },
                        )
                    )
                    counts["orphanFailed"] += 1
                    continue

                try:
                    revision = await db.scalar(
                        select(FlowRevision).where(FlowRevision.id == flow_run.revision_id)
                    )
                    snapshot = verified_flow_revision_snapshot(revision) if revision else {}
                    node = next(
                        (
                            item
                            for item in (snapshot.get("nodes") or [])
                            if str(item.get("id")) == str(flow_run.current_node_id)
                        ),
                        None,
                    )
                    config = dict(node.get("config") or {}) if isinstance(node, dict) else {}
                    reason = flow_run.pause_reason or (
                        "Handoff humano órfão recuperado pelo watchdog."
                    )
                    step = int((flow_run.context or {}).get("flowStepCount", 0) or 0)
                    await _call_human_before_waiting(
                        db,
                        flow_run,
                        node_id=str(flow_run.current_node_id or "orphaned_handoff"),
                        execution_step=max(1, step),
                        reason=reason,
                        config=config,
                        purpose="orphaned_wait_recovery",
                    )
                    db.add(
                        KlmAuditEvent(
                            workspace_id=workspace_id,
                            actor_user_id=None,
                            actor_type="system",
                            actor_label="Watchdog de handoff humano",
                            event_type="human_handoff_orphan_recovered",
                            entity_type="flow_run",
                            entity_id=flow_run.id,
                            correlation_id=f"human-handoff-watchdog:{flow_run.id}",
                            summary="Flow órfão recebeu nova decisão humana canônica.",
                        )
                    )
                    counts["orphanRecovered"] += 1
                except Exception as error:
                    flow_run.status = "failed"
                    flow_run.error_message = (
                        f"Watchdog não pôde criar handoff humano canônico: {safe_error_type(error)}"
                    )
                    flow_run.pause_reason = (
                        "Handoff humano órfão sem rota válida; encerrado com falha."
                    )
                    db.add(
                        KlmAuditEvent(
                            workspace_id=workspace_id,
                            actor_user_id=None,
                            actor_type="system",
                            actor_label="Watchdog de handoff humano",
                            event_type="human_handoff_orphan_failed",
                            entity_type="flow_run",
                            entity_id=flow_run.id,
                            correlation_id=f"human-handoff-watchdog:{flow_run.id}",
                            summary="Flow órfão não possuía rota humana válida e foi encerrado.",
                            after_data={"status": "failed", "errorCode": safe_error_type(error)},
                        )
                    )
                    counts["orphanFailed"] += 1

            # Generic/legacy AgentRuns can ask for a human outside a Flow.
            # They need the same durable decision as a V3 Flow handoff.  Flow
            # owned runs are intentionally skipped: FlowService owns their
            # checkpoint and its orphan recovery above.  A terminal decision
            # is never recreated, which keeps escalation finite.
            from app.models.agent_run import AgentRun, AgentRunStatus
            from app.services.agent_runtime_service import AgentRuntimeService

            waiting_agent_runs = list(
                (
                    await db.scalars(
                        select(AgentRun)
                        .where(
                            AgentRun.workspace_id == workspace_id,
                            AgentRun.status == AgentRunStatus.waiting_human,
                        )
                        .with_for_update(skip_locked=True)
                        .limit(50)
                    )
                ).all()
            )
            runtime = AgentRuntimeService(db)
            for agent_run in waiting_agent_runs:
                if isinstance(agent_run.input_payload, dict) and agent_run.input_payload.get(
                    "flow_run_id"
                ):
                    continue
                linked_statuses = set(
                    (
                        await db.scalars(
                            select(HumanDecision.status).where(
                                HumanDecision.workspace_id == workspace_id,
                                HumanDecision.agent_run_id == agent_run.id,
                            )
                        )
                    ).all()
                )
                if HumanDecisionStatus.pending in linked_statuses:
                    continue
                if linked_statuses:
                    agent_run.status = AgentRunStatus.failed
                    agent_run.completed_at = datetime.now(UTC)
                    agent_run.error_message = (
                        "Decisão humana terminal não retomou a execução; "
                        "intervenção manual necessária."
                    )
                    db.add(
                        KlmAuditEvent(
                            workspace_id=workspace_id,
                            actor_user_id=None,
                            actor_type="system",
                            actor_label="Watchdog de handoff humano",
                            event_type="human_handoff_agent_terminal",
                            entity_type="agent_run",
                            entity_id=agent_run.id,
                            correlation_id=f"human-handoff-watchdog:{agent_run.id}",
                            summary=(
                                "AgentRun aguardava humano após decisão terminal e foi encerrado."
                            ),
                            after_data={
                                "status": "failed",
                                "decisionStatuses": sorted(str(value) for value in linked_statuses),
                            },
                        )
                    )
                    counts["orphanFailed"] += 1
                    continue
                try:
                    await runtime.ensure_human_decision(agent_run)
                    db.add(
                        KlmAuditEvent(
                            workspace_id=workspace_id,
                            actor_user_id=None,
                            actor_type="system",
                            actor_label="Watchdog de handoff humano",
                            event_type="human_handoff_agent_recovered",
                            entity_type="agent_run",
                            entity_id=agent_run.id,
                            correlation_id=f"human-handoff-watchdog:{agent_run.id}",
                            summary="AgentRun órfão recebeu decisão humana canônica.",
                        )
                    )
                    counts["orphanRecovered"] += 1
                except Exception as error:
                    agent_run.status = AgentRunStatus.failed
                    agent_run.completed_at = datetime.now(UTC)
                    agent_run.error_message = (
                        f"Watchdog não pôde criar handoff humano canônico: {safe_error_type(error)}"
                    )
                    db.add(
                        KlmAuditEvent(
                            workspace_id=workspace_id,
                            actor_user_id=None,
                            actor_type="system",
                            actor_label="Watchdog de handoff humano",
                            event_type="human_handoff_agent_orphan_failed",
                            entity_type="agent_run",
                            entity_id=agent_run.id,
                            correlation_id=f"human-handoff-watchdog:{agent_run.id}",
                            summary="AgentRun órfão sem rota humana válida foi encerrado.",
                            after_data={"status": "failed", "errorCode": safe_error_type(error)},
                        )
                    )
                    counts["orphanFailed"] += 1

            # ``waiting_tool`` and ``waiting_external`` are operational
            # pauses, not a license to wait forever.  ``paused_budget`` is
            # even stricter: spending resumes only through an accountable
            # human decision.  Locking the run plus the stable decision key
            # makes duplicate Beat executions harmless.
            stale_runtime_waits = list(
                (
                    await db.scalars(
                        select(AgentRun)
                        .where(
                            AgentRun.workspace_id == workspace_id,
                            AgentRun.status.in_(
                                [
                                    AgentRunStatus.waiting_tool,
                                    AgentRunStatus.waiting_external,
                                    AgentRunStatus.paused_budget,
                                ]
                            ),
                        )
                        .order_by(AgentRun.updated_at, AgentRun.id)
                        .with_for_update(skip_locked=True)
                        .limit(50)
                    )
                ).all()
            )
            for agent_run in stale_runtime_waits:
                await _watchdog_runtime_agent_wait(
                    db,
                    workspace_id=workspace_id,
                    agent_run=agent_run,
                    now=now,
                )
            await db.commit()
        async with WorkerSessionLocal() as db:
            await set_workspace_context(db, workspace_id)
            deliveries = list(
                (
                    await db.scalars(
                        select(HumanNotificationDelivery)
                        .where(
                            HumanNotificationDelivery.workspace_id == workspace_id,
                            HumanNotificationDelivery.channel == "phone",
                            HumanNotificationDelivery.status == HumanDeliveryStatus.pending,
                            HumanNotificationDelivery.attempts < 3,
                        )
                        .with_for_update(skip_locked=True)
                        .limit(50)
                    )
                ).all()
            )
            workspace = await db.scalar(select(Workspace).where(Workspace.id == workspace_id))
            for delivery in deliveries:
                recipient = await db.scalar(
                    select(User).where(User.id == delivery.recipient_user_id)
                )
                decision = await db.scalar(
                    select(HumanDecision).where(HumanDecision.id == delivery.decision_id)
                )
                phone = (
                    (workspace.owner_whatsapp_phone or (recipient.phone if recipient else None))
                    if delivery.recipient_kind == "owner" and workspace
                    else (recipient.phone if recipient else None)
                )
                previous_status = (
                    delivery.status.value
                    if hasattr(delivery.status, "value")
                    else str(delivery.status)
                )
                delivery.attempts += 1
                if not phone:
                    delivery.status = HumanDeliveryStatus.not_configured
                    delivery.last_error_code = "phone_not_configured"
                    counts["phoneFailed"] += 1
                    owner_id = decision.owner_user_id if decision else None
                    if owner_id:
                        await NotificationService(db).create(
                            owner_id,
                            "Aviso telefônico não configurado",
                            "A intervenção humana está no sistema, mas um aviso por telefone "
                            "não pôde ser enviado. Configure o telefone/WhatsApp necessário.",
                            NotificationType.warning,
                            workspace_id=workspace_id,
                        )
                    db.add(
                        KlmAuditEvent(
                            workspace_id=workspace_id,
                            actor_user_id=None,
                            actor_type="system",
                            actor_label="Roteamento humano",
                            event_type="human_phone_delivery_not_configured",
                            entity_type="human_notification_delivery",
                            entity_id=delivery.id,
                            correlation_id=f"human-decision:{delivery.decision_id}",
                            summary="Aviso telefônico não enviado: telefone não configurado.",
                            before_data={
                                "status": previous_status,
                                "attempts": delivery.attempts - 1,
                            },
                            after_data={
                                "status": "not_configured",
                                "attempts": delivery.attempts,
                                "errorCode": delivery.last_error_code,
                            },
                        )
                    )
                    continue
                try:
                    requested_function = (
                        ", ".join(
                            [
                                *(decision.required_roles or []),
                                *(decision.required_skills or []),
                            ]
                        )
                        if decision
                        else "responsável disponível"
                    )
                    receipt = await _send_whatsapp(
                        workspace_id,
                        phone,
                        "Meu Executivo: há uma intervenção humana pendente para "
                        f"{requested_function or 'responsável disponível'}. "
                        "O dono acompanha a chamada no sistema. "
                        "Acesse para decidir dentro do prazo.",
                        db,
                        idempotency_key=f"human-decision:{delivery.decision_id}:{delivery.event_key}:{delivery.recipient_user_id}",
                    )
                    if not receipt.provider_reference:
                        raise RuntimeError("provider receipt unavailable")
                    delivery.status = HumanDeliveryStatus.delivered
                    delivery.provider_receipt_id = receipt.provider_reference
                    delivery.delivered_at = datetime.now(UTC)
                    delivery.last_error_code = None
                    counts["phoneDelivered"] += 1
                    db.add(
                        KlmAuditEvent(
                            workspace_id=workspace_id,
                            actor_user_id=None,
                            actor_type="system",
                            actor_label="Roteamento humano",
                            event_type="human_phone_delivery_confirmed",
                            entity_type="human_notification_delivery",
                            entity_id=delivery.id,
                            correlation_id=f"human-decision:{delivery.decision_id}",
                            summary="Provedor confirmou o aviso telefônico da intervenção humana.",
                            before_data={
                                "status": previous_status,
                                "attempts": delivery.attempts - 1,
                            },
                            after_data={
                                "status": "delivered",
                                "attempts": delivery.attempts,
                                "providerReceiptPresent": True,
                            },
                        )
                    )
                except Exception as error:
                    delivery.status = (
                        HumanDeliveryStatus.failed
                        if delivery.attempts >= 3
                        else HumanDeliveryStatus.pending
                    )
                    delivery.last_error_code = safe_error_type(error)
                    counts["phoneFailed"] += 1
                    owner_id = decision.owner_user_id if decision else None
                    if owner_id:
                        await NotificationService(db).create(
                            owner_id,
                            "Falha no aviso telefônico",
                            "A intervenção humana continua visível no sistema. O provedor não "
                            "confirmou o aviso no telefone "
                            f"(tentativa {delivery.attempts}/3).",
                            NotificationType.urgent,
                            workspace_id=workspace_id,
                        )
                    db.add(
                        KlmAuditEvent(
                            workspace_id=workspace_id,
                            actor_user_id=None,
                            actor_type="system",
                            actor_label="Roteamento humano",
                            event_type="human_phone_delivery_failed",
                            entity_type="human_notification_delivery",
                            entity_id=delivery.id,
                            correlation_id=f"human-decision:{delivery.decision_id}",
                            summary=(
                                "Provedor não confirmou o aviso telefônico da intervenção humana."
                            ),
                            before_data={
                                "status": previous_status,
                                "attempts": delivery.attempts - 1,
                            },
                            after_data={
                                "status": (
                                    delivery.status.value
                                    if hasattr(delivery.status, "value")
                                    else str(delivery.status)
                                ),
                                "attempts": delivery.attempts,
                                "errorCode": delivery.last_error_code,
                            },
                        )
                    )
            await db.commit()
    return counts


@celery_app.task(name="flow_tasks.recover_step_dispatches")
def recover_step_dispatches() -> int:
    """Republish persisted flow-step intents left behind by a process crash."""
    return _run_async(_async_recover_step_dispatches())


def _flow_recovery_work_predicate(
    *,
    dialect_name: str,
    available_before: str,
):
    from sqlalchemy import cast, exists, func, or_, select
    from sqlalchemy.dialects.postgresql import JSONB

    from app.models.flow import FlowRun

    if dialect_name == "postgresql":
        dispatch_rows = func.jsonb_each(FlowRun.context[FLOW_STEP_DISPATCHES_KEY]).table_valued(
            "key", "value"
        )
        dispatch_record = cast(dispatch_rows.c.value, JSONB)
        has_pending_dispatch = exists(
            select(1)
            .select_from(dispatch_rows)
            .where(
                or_(
                    dispatch_record["state"].astext.is_(None),
                    dispatch_record["state"].astext == "pending",
                ),
                dispatch_record["consumedAt"].astext.is_(None),
                dispatch_record["nodeId"].astext == FlowRun.current_node_id,
                or_(
                    dispatch_record["availableAt"].astext.is_(None),
                    dispatch_record["availableAt"].astext <= available_before,
                ),
            )
        )
        # Use the JSONB comparator so SQLAlchemy binds the lookup key as text.
        # ``op("?")`` inferred the right-hand side as JSONB and PostgreSQL
        # rejected the generated ``jsonb ? jsonb`` expression at runtime.
        has_active_external_intent = cast(FlowRun.context, JSONB).has_key(  # noqa: W601
            FLOW_ACTIVE_EXTERNAL_INTENT_KEY
        )
    else:
        dispatch_rows = func.json_each(
            FlowRun.context,
            f"$.{FLOW_STEP_DISPATCHES_KEY}",
        ).table_valued("key", "value")
        dispatch_state = func.json_extract(dispatch_rows.c.value, "$.state")
        dispatch_consumed_at = func.json_extract(
            dispatch_rows.c.value,
            "$.consumedAt",
        )
        dispatch_node_id = func.json_extract(
            dispatch_rows.c.value,
            "$.nodeId",
        )
        dispatch_available_at = func.json_extract(
            dispatch_rows.c.value,
            "$.availableAt",
        )
        has_pending_dispatch = exists(
            select(1)
            .select_from(dispatch_rows)
            .where(
                or_(dispatch_state.is_(None), dispatch_state == "pending"),
                dispatch_consumed_at.is_(None),
                dispatch_node_id == FlowRun.current_node_id,
                or_(
                    dispatch_available_at.is_(None),
                    dispatch_available_at <= available_before,
                ),
            )
        )
        has_active_external_intent = func.json_extract(
            FlowRun.context,
            f"$.{FLOW_ACTIVE_EXTERNAL_INTENT_KEY}",
        ).is_not(None)
    return or_(has_pending_dispatch, has_active_external_intent)


async def _flow_recovery_candidates(
    *,
    session_factory=None,
    batch_size: int = FLOW_RECOVERY_BATCH_SIZE,
    now: datetime | None = None,
) -> list[tuple[UUID, UUID]]:
    from sqlalchemy import select

    from app.db.session import WorkerSessionLocal as AsyncSessionLocal
    from app.models.flow import FlowRun
    from app.models.workspace import Workspace

    limit = max(1, min(int(batch_size), 500))
    reference_now = now or datetime.now(UTC)
    available_before = reference_now.astimezone(UTC).isoformat()
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as discovery_db:
        # Sem isto, a leitura sob FORCE RLS devolve zero linhas e o beat despacha
        # nada: ver app/db/worker_discovery.py (S6).
        await set_worker_discovery_context(discovery_db, family="flow_step_recovery")
        has_recovery_work = _flow_recovery_work_predicate(
            dialect_name=discovery_db.bind.dialect.name,
            available_before=available_before,
        )
        return [
            (run_id, workspace_id)
            for run_id, workspace_id in (
                await discovery_db.execute(
                    select(FlowRun.id, FlowRun.workspace_id)
                    .join(Workspace, Workspace.id == FlowRun.workspace_id)
                    .where(
                        Workspace.deleted_at.is_(None),
                        Workspace.is_active.is_(True),
                        FlowRun.status == "active",
                        has_recovery_work,
                    )
                    .order_by(FlowRun.started_at, FlowRun.id)
                    .limit(limit)
                )
            ).all()
        ]


async def _async_recover_step_dispatches(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = FLOW_RECOVERY_BATCH_SIZE,
) -> int:
    from sqlalchemy import select

    from app.db.session import WorkerSessionLocal as AsyncSessionLocal
    from app.models.flow import FlowRun

    reference_now = now or datetime.now(UTC)
    limit = max(1, min(int(batch_size), 500))
    session_maker = session_factory or AsyncSessionLocal
    candidates = await _flow_recovery_candidates(
        session_factory=session_maker,
        batch_size=limit,
        now=reference_now,
    )
    metrics = FanoutMetrics("flow.step_recovery", limit)
    metrics.discovery_rows = len(candidates)
    metrics.discovered_workspaces = len(
        unique_workspace_ids(
            (workspace_id for _, workspace_id in candidates),
            limit=limit,
        )
    )

    recovered = 0
    published_workspace_ids: set[UUID] = set()
    for run_id, workspace_id in candidates:
        if recovered >= limit:
            break
        async with session_maker() as db:
            await set_workspace_context(db, workspace_id)
            run = await db.scalar(
                select(FlowRun).where(
                    FlowRun.id == run_id,
                    FlowRun.workspace_id == workspace_id,
                    FlowRun.status == "active",
                )
            )
            if not run:
                continue
            published_current = False
            for token, record in _flow_step_dispatches(run.context).items():
                if recovered >= limit:
                    break
                if (
                    record.get("consumedAt")
                    or record.get("state", "pending") != "pending"
                    or str(record.get("nodeId") or "") != str(run.current_node_id or "")
                    or _flow_step_dispatch_countdown(record) > 0
                ):
                    continue
                if _recovery_lease_active(record, now=reference_now):
                    metrics.skipped_leased += 1
                    continue
                claimed = await _claim_flow_step_recovery(
                    db,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    dispatch_token=token,
                    now=reference_now,
                )
                if not claimed:
                    continue
                lease_token = str(claimed["recoveryLeaseToken"])
                try:
                    _publish_flow_step_dispatch(
                        flow_run_id=str(run_id),
                        workspace_id=workspace_id,
                        token=token,
                        record=claimed,
                    )
                except FlowStepDispatchError:
                    metrics.publish_failures += 1
                    await _release_flow_recovery_lease(
                        db,
                        run_id=run_id,
                        workspace_id=workspace_id,
                        lease_token=lease_token,
                        dispatch_token=token,
                    )
                    continue
                recovered += 1
                published_workspace_ids.add(workspace_id)
                published_current = True
            if published_current or recovered >= limit:
                continue

            active = _active_flow_external_intent(
                run.context,
                node_id=str(run.current_node_id or ""),
            )
            if not active or not _flow_external_intent_recovery_due(
                run.context,
                node_id=str(run.current_node_id or ""),
            ):
                continue
            if _recovery_lease_active(active[1], now=reference_now):
                metrics.skipped_leased += 1
                continue
            lease_token = await _claim_flow_external_recovery(
                db,
                run_id=run_id,
                workspace_id=workspace_id,
                now=reference_now,
            )
            if not lease_token:
                continue
            try:
                execute_flow_run_step.apply_async(
                    args=[str(run_id), str(workspace_id), None],
                )
            except Exception as error:
                metrics.publish_failures += 1
                await _release_flow_recovery_lease(
                    db,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    lease_token=lease_token,
                )
                logger.warning(
                    "flow_run.external_intent_recovery_publish_failed",
                    run_id=str(run_id),
                    error_type=safe_error_type(error),
                )
                continue
            recovered += 1
            published_workspace_ids.add(workspace_id)

    metrics.enqueued_workspaces = len(published_workspace_ids)
    metrics.processed_items = recovered
    metrics.emit()
    logger.info("flow_run.step_dispatch_recovery", count=recovered)
    return recovered


async def _due_scheduled_message_workspace_ids(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = SCHEDULED_WORKSPACE_BATCH_SIZE,
) -> list[UUID]:
    from sqlalchemy import and_, func, or_, select

    from app.db.session import WorkerSessionLocal as AsyncSessionLocal
    from app.models.flow import FlowRun, ScheduledMessage
    from app.models.workspace import Workspace

    reference_now = now or datetime.now(UTC)
    limit = bounded_batch_size(batch_size)
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as discovery_db:
        # Sem isto, a leitura sob FORCE RLS devolve zero linhas e o beat despacha
        # nada: ver app/db/worker_discovery.py (S6).
        await set_worker_discovery_context(discovery_db, family="scheduled_messages")
        return list(
            (
                await discovery_db.scalars(
                    select(ScheduledMessage.workspace_id)
                    .outerjoin(FlowRun, FlowRun.id == ScheduledMessage.flow_run_id)
                    .join(Workspace, Workspace.id == ScheduledMessage.workspace_id)
                    .where(
                        Workspace.deleted_at.is_(None),
                        Workspace.is_active.is_(True),
                        or_(
                            ScheduledMessage.flow_run_id.is_(None),
                            FlowRun.id.is_(None),
                            FlowRun.status.notin_(["paused", "waiting_human"]),
                        ),
                        or_(
                            and_(
                    ScheduledMessage.status == "pending",
                    ScheduledMessage.scheduled_at <= reference_now,
                    or_(
                        ScheduledMessage.pending_retry_at.is_(None),
                        ScheduledMessage.pending_retry_at <= reference_now,
                    ),
                            ),
                            and_(
                                ScheduledMessage.status == "processing",
                                or_(
                                    ScheduledMessage.lease_expires_at.is_(None),
                                    ScheduledMessage.lease_expires_at <= reference_now,
                                ),
                            ),
                        ),
                    )
                    .group_by(ScheduledMessage.workspace_id)
                    .order_by(func.min(ScheduledMessage.scheduled_at))
                    .limit(limit)
                )
            ).all()
        )


async def _async_process_scheduled_messages(
    *,
    session_factory=None,
    now: datetime | None = None,
    workspace_batch_size: int = SCHEDULED_WORKSPACE_BATCH_SIZE,
    total_batch_size: int = SCHEDULED_MESSAGE_TOTAL_BATCH_SIZE,
) -> int:
    from app.db.session import WorkerSessionLocal as AsyncSessionLocal

    session_maker = session_factory or AsyncSessionLocal
    workspace_limit = bounded_batch_size(workspace_batch_size)
    total_limit = max(1, min(int(total_batch_size), 1000))
    workspace_ids = await _due_scheduled_message_workspace_ids(
        session_factory=session_maker,
        now=now,
        batch_size=workspace_limit,
    )
    metrics = FanoutMetrics("flow.scheduled_messages", total_limit)
    metrics.discovery_rows = len(workspace_ids)
    metrics.discovered_workspaces = len(workspace_ids)

    processed_count = 0
    processed_workspaces = 0
    for workspace_id in workspace_ids:
        remaining = total_limit - processed_count
        if remaining <= 0:
            break
        async with session_maker() as db:
            await set_workspace_context(db, workspace_id)
            processed = await _process_scheduled_messages_for_workspace(
                db,
                workspace_id,
                limit=min(50, remaining),
            )
            processed_count += processed
            if processed:
                processed_workspaces += 1
    metrics.enqueued_workspaces = processed_workspaces
    metrics.processed_items = processed_count
    metrics.emit()
    logger.info("scheduled_messages.processing", count=processed_count)
    return processed_count


async def _process_scheduled_messages_for_workspace(
    db,
    workspace_id: UUID,
    *,
    limit: int = 50,
) -> int:
    from sqlalchemy import and_, or_, select

    from app.models.flow import (
        FlowRevision,
        FlowRun,
        ScheduledMessage,
        verified_flow_revision_snapshot,
    )

    now = datetime.now(UTC)
    result = await db.execute(
        select(ScheduledMessage)
        .outerjoin(FlowRun, FlowRun.id == ScheduledMessage.flow_run_id)
        .where(
            ScheduledMessage.workspace_id == workspace_id,
            or_(
                ScheduledMessage.flow_run_id.is_(None),
                FlowRun.id.is_(None),
                FlowRun.status.notin_(["paused", "waiting_human"]),
            ),
            or_(
                and_(
                    ScheduledMessage.status == "pending",
                    ScheduledMessage.scheduled_at <= now,
                    or_(
                        ScheduledMessage.pending_retry_at.is_(None),
                        ScheduledMessage.pending_retry_at <= now,
                    ),
                ),
                and_(
                    ScheduledMessage.status == "processing",
                    or_(
                        ScheduledMessage.lease_expires_at.is_(None),
                        ScheduledMessage.lease_expires_at <= now,
                    ),
                ),
            ),
        )
        .order_by(ScheduledMessage.scheduled_at, ScheduledMessage.id)
        .with_for_update(skip_locked=True, of=ScheduledMessage)
        .limit(max(1, min(int(limit), 200)))
    )
    messages = list(result.scalars().all())

    for msg in messages:
        lease_token = await _claim_scheduled_message(
            db,
            msg,
            workspace_id=workspace_id,
            now=now,
        )
        if not lease_token:
            continue
        _inject_fault(
            "scheduled_message.after_lease_claim",
            message_id=str(msg.id),
            lease_token=lease_token,
        )

        # Standalone scheduled rows can also carry the sequence contract. They
        # have no FlowRun to load below, but must obey the same pending-template
        # hold/skip gate before any provider dispatch.
        if msg.template_status == "pending" and not msg.flow_run_id:
            from app.models.whatsapp_campaign import WhatsAppMetaTemplate

            template_lookup = (
                WhatsAppMetaTemplate.id == msg.template_id
                if msg.template_id
                else (
                    (WhatsAppMetaTemplate.name == msg.template_name)
                    & (WhatsAppMetaTemplate.language == (msg.template_language or "pt_BR"))
                )
            )
            template_status = await db.scalar(
                select(WhatsAppMetaTemplate.status).where(
                    WhatsAppMetaTemplate.workspace_id == workspace_id,
                    template_lookup,
                    WhatsAppMetaTemplate.removed_at_source_at.is_(None),
                )
            )
            current_template_status = str(
                getattr(template_status, "value", template_status) or ""
            ).lower()
            if current_template_status == "approved":
                msg.template_status = "approved"
                msg.pending_grace_until = None
                msg.pending_retry_at = None
                msg.status = "pending"
                msg.lease_token = None
                msg.lease_expires_at = None
            else:
                await hold_or_skip_pending_template(db, msg, None, now=now)
            await db.commit()
            continue

        run = None
        snapshot: dict[str, Any] | None = None
        if msg.flow_run_id:
            run = await db.scalar(
                select(FlowRun)
                .where(
                    FlowRun.id == msg.flow_run_id,
                    FlowRun.workspace_id == workspace_id,
                )
                .with_for_update()
            )
            if not run:
                await _fenced_scheduled_message_transition(
                    db,
                    message_id=msg.id,
                    workspace_id=workspace_id,
                    lease_token=lease_token,
                    status="failed",
                    error_message="Flow run não encontrado",
                )
                continue
            if (run.context or {}).get("klmRuntime") == "local_simulation":
                await _fenced_scheduled_message_transition(
                    db,
                    message_id=msg.id,
                    workspace_id=workspace_id,
                    lease_token=lease_token,
                    status="failed",
                    error_message="Execução local simulada não permite entregas",
                )
                continue
            if run.status == "cancelled":
                await _fenced_scheduled_message_transition(
                    db,
                    message_id=msg.id,
                    workspace_id=workspace_id,
                    lease_token=lease_token,
                    status="cancelled",
                )
                continue
            if run.status == "failed":
                await _fenced_scheduled_message_transition(
                    db,
                    message_id=msg.id,
                    workspace_id=workspace_id,
                    lease_token=lease_token,
                    status="failed",
                    error_message="Flow run falhou antes da entrega",
                )
                continue
            if run.status in {"paused", "waiting_human"}:
                await _fenced_scheduled_message_transition(
                    db,
                    message_id=msg.id,
                    workspace_id=workspace_id,
                    lease_token=lease_token,
                    status="pending",
                )
                continue

            # A sequence stage may be persisted before Meta approves its
            # template. Hold the due item for a short, explicit grace period;
            # after that, skip it with an unconditional audit event and a
            # human notification when the matter has an owner.
            if msg.template_status == "pending":
                from app.models.whatsapp_campaign import WhatsAppMetaTemplate

                template_lookup = (
                    WhatsAppMetaTemplate.id == msg.template_id
                    if msg.template_id
                    else (
                        (WhatsAppMetaTemplate.name == msg.template_name)
                        & (WhatsAppMetaTemplate.language == (msg.template_language or "pt_BR"))
                    )
                )
                template_status = await db.scalar(
                    select(WhatsAppMetaTemplate.status).where(
                        WhatsAppMetaTemplate.workspace_id == workspace_id,
                        template_lookup,
                        WhatsAppMetaTemplate.removed_at_source_at.is_(None),
                    )
                )
                current_template_status = str(
                    getattr(template_status, "value", template_status) or ""
                ).lower()
                if current_template_status == "approved":
                    msg.template_status = "approved"
                    msg.pending_grace_until = None
                    msg.pending_retry_at = None
                    msg.error_message = None
                    msg.status = "pending"
                    msg.lease_token = None
                    msg.lease_expires_at = None
                    await db.commit()
                    continue
                await hold_or_skip_pending_template(db, msg, run, now=now)
                await db.commit()
                continue

            revision = await db.scalar(
                select(FlowRevision).where(
                    FlowRevision.id == run.revision_id,
                    FlowRevision.flow_id == run.flow_id,
                )
            )
            if not revision:
                await _fenced_scheduled_message_transition(
                    db,
                    message_id=msg.id,
                    workspace_id=workspace_id,
                    lease_token=lease_token,
                    status="failed",
                    error_message="Revisão do flow não encontrada",
                )
                continue
            try:
                snapshot = verified_flow_revision_snapshot(revision)
            except ValueError as error:
                await _fenced_scheduled_message_transition(
                    db,
                    message_id=msg.id,
                    workspace_id=workspace_id,
                    lease_token=lease_token,
                    status="failed",
                    error_message=str(error),
                )
                continue
            if not snapshot.get("is_active"):
                await _fenced_scheduled_message_transition(
                    db,
                    message_id=msg.id,
                    workspace_id=workspace_id,
                    lease_token=lease_token,
                    status="failed",
                    error_message="Flow inativo não permite entregas",
                )
                continue

        # Re-check an approved snapshot immediately before dispatch.  A later
        # Meta sync may revoke it; never fall back to free text in that case.
        if msg.template_name and msg.template_status == "approved":
            from app.models.whatsapp_campaign import WhatsAppMetaTemplate

            template_lookup = (
                WhatsAppMetaTemplate.id == msg.template_id
                if msg.template_id
                else (
                    (WhatsAppMetaTemplate.name == msg.template_name)
                    & (
                        WhatsAppMetaTemplate.language
                        == (msg.template_language or "pt_BR")
                    )
                )
            )
            template_status = await db.scalar(
                select(WhatsAppMetaTemplate.status).where(
                    WhatsAppMetaTemplate.workspace_id == workspace_id,
                    template_lookup,
                    WhatsAppMetaTemplate.removed_at_source_at.is_(None),
                )
            )
            current_template_status = str(
                getattr(template_status, "value", template_status) or ""
            ).lower()
            if current_template_status != "approved":
                msg.template_status = "pending"
                await hold_or_skip_pending_template(db, msg, run, now=now)
                await db.commit()
                continue
        if run is not None and snapshot is not None and (
            snapshot.get("trigger_config") or {}
        ).get("localOnly"):
            await _fenced_scheduled_message_transition(
                db,
                message_id=msg.id,
                workspace_id=workspace_id,
                lease_token=lease_token,
                status="failed",
                error_message="Execução local simulada não permite entregas",
            )
            continue

        attempt_started = False
        provider_supports_idempotency = False

        async def before_dispatch(supports_idempotency: bool) -> None:
            nonlocal attempt_started, provider_supports_idempotency
            provider_supports_idempotency = supports_idempotency
            attempt_started = await _checkpoint_scheduled_message_attempt(
                db,
                message_id=msg.id,
                workspace_id=workspace_id,
                lease_token=lease_token,
                provider_supports_idempotency=supports_idempotency,
            )
            if not attempt_started:
                raise ExternalIntentFencedError("Lease da mensagem foi substituído")

        try:
            dispatch_context = dict(run.context or {}) if run is not None else {}
            if msg.template_bindings and run is not None:
                # Sequence bindings are field-backed, so resolve them again
                # at delivery time rather than trusting a stale start snapshot.
                from app.models.whatsapp import WhatsAppContact
                from app.services.whatsapp_picker_service import WhatsAppPickerService

                raw_contact_id = dispatch_context.get("contactId")
                contact_id = None
                if raw_contact_id:
                    try:
                        contact_id = UUID(str(raw_contact_id))
                    except (TypeError, ValueError):
                        contact_id = None
                if contact_id is None:
                    contact_id = await db.scalar(
                        select(WhatsAppContact.id).where(
                            WhatsAppContact.workspace_id == workspace_id,
                            WhatsAppContact.phone == msg.contact_phone,
                        )
                    )
                variable_read = await WhatsAppPickerService(db).variables(
                    workspace_id,
                    contact_id=contact_id,
                    matter_id=run.matter_id,
                )
                resolved_fields = {
                    field.field_key: field.resolved_value
                    for group in variable_read.groups
                    for field in group.values
                }
                for binding in msg.template_bindings:
                    field_key = str(binding.get("fieldKey") or binding.get("field_key") or "")
                    value = resolved_fields.get(field_key)
                    if value is None or not str(value).strip():
                        raise ValueError(f"Variável sem valor para fieldKey '{field_key}'")
                    target = dispatch_context
                    parts = field_key.split(".")
                    for part in parts[:-1]:
                        child = target.get(part)
                        if not isinstance(child, dict):
                            child = {}
                            target[part] = child
                        target = child
                    target[parts[-1]] = str(value)
                dispatch_context["sequenceResolvedFields"] = resolved_fields
            if msg.template_name and msg.template_status == "approved":
                receipt = await _dispatch_whatsapp_template_with_protocol(
                    workspace_id,
                    msg.contact_phone,
                    db,
                    template_id=msg.template_id,
                    template_name=msg.template_name,
                    template_language=msg.template_language or "pt_BR",
                    bindings=msg.template_bindings,
                    context=dispatch_context,
                    connection_id=msg.whatsapp_connection_id,
                    before_dispatch=before_dispatch,
                )
            else:
                receipt = await _dispatch_whatsapp_with_protocol(
                    workspace_id,
                    msg.contact_phone,
                    msg.message,
                    db,
                    idempotency_key=(msg.idempotency_key or f"scheduled-message:{msg.id}"),
                    before_dispatch=before_dispatch,
                )
            _inject_fault(
                "scheduled_message.after_provider_accept",
                message_id=str(msg.id),
                lease_token=lease_token,
            )
            await _checkpoint_scheduled_message_success(
                db,
                message_id=msg.id,
                workspace_id=workspace_id,
                lease_token=lease_token,
                provider_reference=_provider_reference(receipt),
            )
        except ExternalIntentFencedError:
            await db.rollback()
        except Exception as error:
            await _record_scheduled_message_failure(
                db,
                message_id=msg.id,
                workspace_id=workspace_id,
                lease_token=lease_token,
                attempt_started=attempt_started,
                provider_supports_idempotency=provider_supports_idempotency,
                error=error,
            )
            logger.warning(
                "scheduled_message.send_failed",
                msg_id=str(msg.id),
                error_type=safe_error_type(error),
            )

    return len(messages)


async def _claim_scheduled_message(
    db,
    msg,
    *,
    workspace_id: UUID,
    now: datetime,
) -> str | None:
    from sqlalchemy import and_, or_, update

    from app.models.flow import ScheduledMessage

    if msg.status == "processing":
        if not msg.lease_token:
            await _mark_legacy_scheduled_message_uncertain(
                db,
                message_id=msg.id,
                workspace_id=workspace_id,
                expected_status="processing",
            )
            return None
        if not _lease_expired(msg.lease_expires_at, now=now):
            return None
        if msg.delivery_attempted_at and not msg.provider_supports_idempotency:
            await db.execute(
                update(ScheduledMessage)
                .where(
                    ScheduledMessage.id == msg.id,
                    ScheduledMessage.workspace_id == workspace_id,
                    ScheduledMessage.status == "processing",
                    ScheduledMessage.lease_token == msg.lease_token,
                    ScheduledMessage.lease_expires_at <= now,
                )
                .values(
                    status="delivery_uncertain",
                    delivery_uncertain_at=now,
                    lease_token=None,
                    lease_expires_at=None,
                    error_message=(
                        "Entrega possivelmente aceita; reconciliação humana obrigatória."
                    ),
                )
                .execution_options(synchronize_session=False)
            )
            await db.commit()
            return None
        expected = and_(
            ScheduledMessage.status == "processing",
            ScheduledMessage.lease_token == msg.lease_token,
            ScheduledMessage.lease_expires_at <= now,
        )
    else:
        if msg.delivery_attempted_at and not msg.provider_supports_idempotency:
            await _mark_legacy_scheduled_message_uncertain(
                db,
                message_id=msg.id,
                workspace_id=workspace_id,
                expected_status="pending",
            )
            return None
        expected = and_(
            ScheduledMessage.status == "pending",
            ScheduledMessage.scheduled_at <= now,
            or_(
                ScheduledMessage.pending_retry_at.is_(None),
                ScheduledMessage.pending_retry_at <= now,
            ),
        )

    lease_token = str(uuid4())
    idempotency_key = msg.idempotency_key or f"scheduled-message:{msg.id}"
    claim = await db.execute(
        update(ScheduledMessage)
        .where(
            ScheduledMessage.id == msg.id,
            ScheduledMessage.workspace_id == workspace_id,
            expected,
        )
        .values(
            status="processing",
            idempotency_key=idempotency_key,
            lease_token=lease_token,
            lease_expires_at=now + SCHEDULED_MESSAGE_LEASE,
            error_message=None,
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if claim.rowcount != 1:
        return None
    msg.status = "processing"
    msg.idempotency_key = idempotency_key
    msg.lease_token = lease_token
    msg.lease_expires_at = now + SCHEDULED_MESSAGE_LEASE
    return lease_token


async def _mark_legacy_scheduled_message_uncertain(
    db,
    *,
    message_id: UUID,
    workspace_id: UUID,
    expected_status: str,
) -> None:
    from sqlalchemy import and_, update

    from app.models.flow import ScheduledMessage

    status_fence = ScheduledMessage.status == expected_status
    if expected_status == "processing":
        status_fence = and_(
            status_fence,
            ScheduledMessage.lease_token.is_(None),
        )
    await db.execute(
        update(ScheduledMessage)
        .where(
            ScheduledMessage.id == message_id,
            ScheduledMessage.workspace_id == workspace_id,
            status_fence,
        )
        .values(
            status="delivery_uncertain",
            delivery_uncertain_at=datetime.now(UTC),
            lease_token=None,
            lease_expires_at=None,
            error_message=("Estado legado sem fencing; reconciliação humana obrigatória."),
        )
    )
    await db.commit()


async def _fenced_scheduled_message_transition(
    db,
    *,
    message_id: UUID,
    workspace_id: UUID,
    lease_token: str,
    status: str,
    error_message: str | None = None,
) -> bool:
    from sqlalchemy import update

    from app.models.flow import ScheduledMessage

    transition = await db.execute(
        update(ScheduledMessage)
        .where(
            ScheduledMessage.id == message_id,
            ScheduledMessage.workspace_id == workspace_id,
            ScheduledMessage.status == "processing",
            ScheduledMessage.lease_token == lease_token,
        )
        .values(
            status=status,
            error_message=error_message,
            lease_token=None,
            lease_expires_at=None,
        )
    )
    await db.commit()
    return transition.rowcount == 1


async def _checkpoint_scheduled_message_attempt(
    db,
    *,
    message_id: UUID,
    workspace_id: UUID,
    lease_token: str,
    provider_supports_idempotency: bool,
) -> bool:
    from sqlalchemy import update

    from app.models.flow import ScheduledMessage

    now = datetime.now(UTC)
    attempt = await db.execute(
        update(ScheduledMessage)
        .where(
            ScheduledMessage.id == message_id,
            ScheduledMessage.workspace_id == workspace_id,
            ScheduledMessage.status == "processing",
            ScheduledMessage.lease_token == lease_token,
            ScheduledMessage.lease_expires_at > now,
        )
        .values(
            delivery_attempted_at=now,
            delivery_attempt_count=ScheduledMessage.delivery_attempt_count + 1,
            provider_supports_idempotency=provider_supports_idempotency,
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if attempt.rowcount != 1:
        return False
    _inject_fault(
        "scheduled_message.after_attempt_checkpoint",
        message_id=str(message_id),
        lease_token=lease_token,
    )
    return True


async def _checkpoint_scheduled_message_success(
    db,
    *,
    message_id: UUID,
    workspace_id: UUID,
    lease_token: str,
    provider_reference: str | None,
) -> bool:
    from sqlalchemy import update

    from app.models.flow import ScheduledMessage

    checkpoint = await db.execute(
        update(ScheduledMessage)
        .where(
            ScheduledMessage.id == message_id,
            ScheduledMessage.workspace_id == workspace_id,
            ScheduledMessage.status == "processing",
            ScheduledMessage.lease_token == lease_token,
            ScheduledMessage.delivery_attempted_at.is_not(None),
        )
        .values(
            status="sent",
            sent_at=datetime.now(UTC),
            error_message=None,
            provider_message_id=provider_reference,
            lease_token=None,
            lease_expires_at=None,
            delivery_uncertain_at=None,
        )
    )
    await db.commit()
    if checkpoint.rowcount != 1:
        # An inbound interrupt may win the race while the provider request is
        # in flight.  A provider receipt then proves that cancellation was not
        # conclusive, so preserve the delivery-uncertain state for human
        # reconciliation instead of silently claiming it was cancelled.
        if provider_reference:
            await db.execute(
                update(ScheduledMessage)
                .where(
                    ScheduledMessage.id == message_id,
                    ScheduledMessage.workspace_id == workspace_id,
                    ScheduledMessage.status == "cancelled",
                    ScheduledMessage.delivery_attempted_at.is_not(None),
                )
                .values(
                    status="delivery_uncertain",
                    provider_message_id=provider_reference,
                    delivery_uncertain_at=datetime.now(UTC),
                    error_message=(
                        "Resposta inbound interrompeu a etapa enquanto o provedor já "
                        "podia ter aceitado a entrega; reconciliação humana obrigatória."
                    ),
                )
            )
            await db.commit()
        return False
    _inject_fault(
        "scheduled_message.after_success_checkpoint",
        message_id=str(message_id),
        lease_token=lease_token,
    )
    return True


async def _record_scheduled_message_failure(
    db,
    *,
    message_id: UUID,
    workspace_id: UUID,
    lease_token: str,
    attempt_started: bool,
    provider_supports_idempotency: bool,
    error: Exception,
) -> None:
    from sqlalchemy import update

    from app.models.flow import ScheduledMessage

    now = datetime.now(UTC)
    if not attempt_started:
        values = {
            "status": "failed",
            "error_message": str(error),
            "sent_at": None,
            "lease_token": None,
            "lease_expires_at": None,
        }
    elif provider_supports_idempotency:
        values = {
            "status": "pending",
            "error_message": str(error),
            "sent_at": None,
            "lease_token": None,
            "lease_expires_at": None,
        }
    else:
        values = {
            "status": "delivery_uncertain",
            "error_message": (
                "Entrega possivelmente aceita; reconciliação humana obrigatória. "
                f"Falha: {safe_error_type(error)}."
            ),
            "sent_at": None,
            "delivery_uncertain_at": now,
            "lease_token": None,
            "lease_expires_at": None,
        }
    await db.execute(
        update(ScheduledMessage)
        .where(
            ScheduledMessage.id == message_id,
            ScheduledMessage.workspace_id == workspace_id,
            ScheduledMessage.status == "processing",
            ScheduledMessage.lease_token == lease_token,
        )
        .values(**values)
    )
    await db.commit()


# ── Helpers ────────────────────────────────────────────────────────────


def _interpolate(template: str, context: dict[str, Any]) -> str:
    """Substitui variáveis {nome}, {prazo}, etc no template."""
    for key, value in context.items():
        template = template.replace(f"{{{key}}}", str(value or ""))
    return template


def _evaluate_condition(
    value: Any,
    operator: str,
    target: Any = "",
    *,
    field_exists: bool | None = None,
) -> bool:
    operator = operator.lower().strip()
    exists = value is not None if field_exists is None else field_exists
    if operator == "exists":
        return exists
    if operator == "not_empty":
        if not exists or value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, list | tuple | dict | set):
            return bool(value)
        return True

    value_text = "" if value is None else str(value)
    target_text = "" if target is None else str(target)
    if operator == "eq":
        return value_text.lower() == target_text.lower()
    if operator == "contains":
        return target_text.lower() in value_text.lower()
    if operator in {"gt", "gte", "lt", "lte"}:
        try:
            numeric_value = float(value_text)
            numeric_target = float(target_text)
        except (TypeError, ValueError):
            return False
        if operator == "gt":
            return numeric_value > numeric_target
        if operator == "gte":
            return numeric_value >= numeric_target
        if operator == "lt":
            return numeric_value < numeric_target
        return numeric_value <= numeric_target
    return False


async def _send_whatsapp(
    workspace_id,
    phone: str,
    message: str,
    db,
    *,
    idempotency_key: str | None = None,
    before_dispatch: Callable[[bool], Awaitable[None]] | None = None,
) -> ExternalDeliveryReceipt:
    """Envia mensagem WhatsApp via WhatsAppService."""
    _require_external_delivery("Envio de WhatsApp")
    from app.services.whatsapp.errors import WhatsAppProviderError
    from app.services.whatsapp_service import WhatsAppService

    svc = WhatsAppService(db)
    message_service = svc.message_service
    connection = await message_service._resolve_outbound_connection(
        workspace_id=workspace_id,
        explicit_connection_id=None,
        contact=None,
    )
    try:
        strategy = message_service.strategy_factory.build(connection)
        _require_external_delivery("Envio de WhatsApp")
        accepts_idempotency_key = _callable_accepts_keyword(
            strategy.send_text,
            "idempotency_key",
        )
        supports_idempotency = (
            bool(getattr(strategy, "supports_idempotency", False)) and accepts_idempotency_key
        )
        if before_dispatch:
            await before_dispatch(supports_idempotency)
        kwargs = (
            {"idempotency_key": idempotency_key}
            if accepts_idempotency_key and idempotency_key
            else {}
        )
        result = await strategy.send_text(connection, phone, message, **kwargs)
        return ExternalDeliveryReceipt(
            provider_reference=_provider_reference(result),
            provider_supports_idempotency=supports_idempotency,
            raw_result=result,
        )
    except WhatsAppProviderError as e:
        await message_service.connection_service.mark_connection_error(
            connection,
            e.code,
            e.message,
        )
        logger.warning(
            "flow.whatsapp_send_failed",
            phone_ref=opaque_ref(phone, kind="phone"),
            error_type=safe_error_type(e),
            provider_code=e.code,
            provider_status_code=e.provider_status_code,
        )
        raise
    except Exception as e:
        logger.warning(
            "flow.whatsapp_send_failed",
            phone_ref=opaque_ref(phone, kind="phone"),
            error_type=safe_error_type(e),
        )
        raise
