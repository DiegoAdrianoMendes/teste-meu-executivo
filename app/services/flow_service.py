"""
Flow Service — Gerenciamento de fluxos de automação
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import automation_worker_allowed, external_delivery_allowed
from app.core.logging import logger
from app.core.rbac import UNRESTRICTED_PERMISSION_SCOPES, get_effective_permissions
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.entitlement import EntitlementKey
from app.models.flow import (
    Flow,
    FlowRevision,
    FlowRun,
    ScheduledMessage,
    build_flow_revision_snapshot,
    flow_revision_snapshot_hash,
    verified_flow_revision_snapshot,
)
from app.models.klm_operations import (
    KlmAuditEvent,
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.matter import Matter
from app.models.task import Task
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.flow import (
    FlowCreate,
    FlowUpdate,
    ensure_publishable_flow_graph,
    validate_flow_graph,
)
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.entitlement_service import EntitlementEnforcementService
from app.services.flow_runtime_primitives import (
    FlowRouteDecision,
    advance_loop,
    apply_data_node,
    edge_route_key,
    evaluate_flow_condition,
    get_context_value,
    select_router_route,
)
from app.services.flow_trace import transition_flow_trace

TERMINAL_FLOW_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})
RESUMABLE_FLOW_RUN_STATUSES = frozenset({"paused", "waiting_human"})
FLOW_AGENT_EXECUTIONS_KEY = "flowAgentExecutions"
FLOW_ACTIVE_AGENT_RUN_KEY = "flowActiveAgentRunId"
SETTLED_FLOW_AGENT_CHECKPOINT_STATES = frozenset({"advanced", "completed"})
FLOW_AUTHORIZATION_CONTEXT_KEYS = frozenset(
    {
        "permissions",
        "isPlatformRoot",
        "authorizationActorId",
        "authorizationSource",
    }
)


class TaskFlowApplicationError(ValueError):
    """A published flow cannot be safely attached to the requested task."""


class TaskFlowIdempotencyConflict(ValueError):  # noqa: N818
    """The workspace idempotency key was already used for another request."""

FlowAgentSettlementOutcome = Literal[
    "advanced",
    "completed",
    "failed",
    "waiting_human",
    "waiting_data",
    "waiting_tool",
    "waiting_external",
    "paused_budget",
    "pending",
    "already_settled",
    "not_linked",
    "flow_terminal",
]

FlowTraversalStatus = Literal[
    "completed",
    "waiting_human",
    "max_steps_reached",
    "invalid_graph",
]


class FlowConditionDecision(TypedDict):
    field: str
    operator: str
    expected: Any
    actual: Any
    branch: str


class FlowTraceEntry(TypedDict, total=False):
    step: int
    nodeId: str
    type: str
    label: str
    status: str
    occurredAt: str
    decision: dict[str, Any]


@dataclass(frozen=True)
class FlowTraversalResult:
    status: FlowTraversalStatus
    trace: list[FlowTraceEntry]
    current_node_id: str | None
    next_node_id: str | None
    steps_executed: int
    pause_reason: str | None = None


@dataclass(frozen=True)
class FlowAgentSettlement:
    outcome: FlowAgentSettlementOutcome
    flow_run_id: UUID | None = None
    should_enqueue: bool = False
    continuation_token: str | None = None


class ExternalDeliveryBlockedError(RuntimeError):
    """A política do runtime não permite criar trabalho de entrega externa."""


class InvalidFlowRunTransition(ValueError):  # noqa: N818 - public compatibility
    """A transição solicitada não é válida para o estado atual do run."""


class FlowRevisionConflictError(RuntimeError):
    """A atualização foi baseada em uma revisão que deixou de ser corrente."""

    def __init__(self, expected_revision: int, current_revision: int):
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            "Flow foi alterado por outro editor "
            f"(esperada: {expected_revision}, atual: {current_revision})"
        )


class FlowRevisionRequiredError(RuntimeError):
    """O cliente precisa informar a revisão que pretende substituir."""

    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__(
            "expected_revision é obrigatório para atualizar este flow "
            f"(revisão atual: {current_revision})"
        )


class FlowRevisionIntegrityError(RuntimeError):
    """O encadeamento persistido entre flow, revisão e run está inconsistente."""


def _flow_agent_executions(context: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    raw = (context or {}).get(FLOW_AGENT_EXECUTIONS_KEY)
    if not isinstance(raw, dict):
        return {}
    return {
        str(agent_run_id): dict(checkpoint)
        for agent_run_id, checkpoint in raw.items()
        if isinstance(checkpoint, dict)
    }


def _active_flow_agent_checkpoint(
    run: FlowRun,
) -> tuple[str, dict[str, Any]] | None:
    context = dict(run.context or {})
    raw_agent_run_id = context.get(FLOW_ACTIVE_AGENT_RUN_KEY)
    if raw_agent_run_id is None:
        return None
    agent_run_id = str(raw_agent_run_id)
    checkpoint = _flow_agent_executions(context).get(agent_run_id)
    if checkpoint is None:
        raise FlowRevisionIntegrityError(
            "Flow run aponta para um AgentRun sem checkpoint persistido"
        )
    return agent_run_id, checkpoint


def _flow_agent_checkpoint_is_settled(checkpoint: dict[str, Any]) -> bool:
    if checkpoint.get("state") == "superseded":
        return bool(checkpoint.get("supersededByAgentRunId"))
    return (
        bool(checkpoint.get("advancedAt"))
        and str(checkpoint.get("state") or "") in SETTLED_FLOW_AGENT_CHECKPOINT_STATES
    )


def _evaluate_flow_condition(
    value: Any,
    operator: str,
    target: Any = "",
    *,
    field_exists: bool | None = None,
) -> bool:
    return evaluate_flow_condition(
        value,
        operator,
        target,
        field_exists=field_exists,
    )


def _edge_branch(edge: dict[str, Any]) -> str | None:
    return edge_route_key(edge)


def _flow_node_config(node: dict[str, Any]) -> dict[str, Any]:
    config = node.get("config")
    if isinstance(config, dict):
        return config
    data = node.get("data")
    return data if isinstance(data, dict) else {}


def _initial_flow_node_id(nodes: list[dict[str, Any]]) -> str | None:
    trigger_id = next(
        (str(node["id"]) for node in nodes if node.get("type") == "trigger"),
        None,
    )
    if trigger_id:
        return trigger_id
    return str(nodes[0]["id"]) if nodes else None


def _max_flow_steps(trigger_config: dict[str, Any] | None) -> int:
    raw = (trigger_config or {}).get("maxSteps", (trigger_config or {}).get("max_steps", 100))
    try:
        return max(1, min(int(raw), 200))
    except (TypeError, ValueError):
        return 100


def _next_flow_node(
    node: dict[str, Any],
    edges: list[dict[str, Any]],
    context: dict[str, Any],
) -> tuple[str | None, FlowConditionDecision | FlowRouteDecision | None]:
    node_id = str(node["id"])
    outgoing = [edge for edge in edges if str(edge.get("source")) == node_id]
    decision: FlowConditionDecision | FlowRouteDecision | None = None

    node_type = str(node.get("type") or "")
    config = _flow_node_config(node)
    if node_type == "condition":
        field = str(config.get("field") or "")
        operator = str(config.get("operator") or "eq")
        expected = config.get("value", "")
        sentinel = object()
        actual = get_context_value(context, field, sentinel)
        matched = _evaluate_flow_condition(
            None if actual is sentinel else actual,
            operator,
            expected,
            field_exists=actual is not sentinel,
        )
        branch: Literal["true", "false"] = "true" if matched else "false"
        decision = {
            "field": field,
            "operator": operator,
            "expected": expected,
            "actual": None if actual is sentinel else actual,
            "branch": branch,
        }
        matching = [edge for edge in outgoing if _edge_branch(edge) == branch]
        next_edge = (matching or outgoing or [None])[0]
    elif node_type == "router":
        decision = select_router_route(config, context)
        route_key = str(decision["routeKey"])
        matching = [edge for edge in outgoing if _edge_branch(edge) == route_key]
        fallback = [edge for edge in outgoing if _edge_branch(edge) == "fallback"]
        next_edge = (matching or fallback or outgoing or [None])[0]
    elif node_type == "loop":
        decision = advance_loop(node_id, config, context)
        route_key = str(decision["routeKey"])
        matching = [edge for edge in outgoing if _edge_branch(edge) == route_key]
        next_edge = (matching or outgoing or [None])[0]
    else:
        next_edge = outgoing[0] if outgoing else None

    next_node_id = str(next_edge["target"]) if next_edge else None
    return next_node_id, decision


def _traverse_flow_graph(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    context: dict[str, Any],
    *,
    start_node_id: str | None,
    max_steps: int,
    initial_step_count: int = 0,
) -> FlowTraversalResult:
    """Traverse the graph without side effects, preserving deterministic edge order."""
    nodes_by_id = {str(node["id"]): node for node in nodes}
    current_id = start_node_id
    last_node_id: str | None = None
    trace: list[FlowTraceEntry] = []

    while current_id and initial_step_count + len(trace) < max_steps:
        node = nodes_by_id.get(current_id)
        if not node:
            return FlowTraversalResult(
                status="invalid_graph",
                trace=trace,
                current_node_id=current_id,
                next_node_id=None,
                steps_executed=initial_step_count + len(trace),
            )

        node_type = str(node.get("type") or "")
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        data_decision = apply_data_node(
            node_type,
            _flow_node_config(node),
            context,
        )
        next_node_id, decision = _next_flow_node(node, edges, context)
        entry: FlowTraceEntry = {
            "step": initial_step_count + len(trace) + 1,
            "nodeId": current_id,
            "type": node_type,
            "label": str(data.get("label") or node.get("label") or current_id),
            "status": "simulated",
            "occurredAt": datetime.now(UTC).isoformat(),
        }
        if decision or data_decision:
            entry["decision"] = {**(data_decision or {}), **(decision or {})}

        trace.append(entry)
        last_node_id = current_id
        if node_type in {"human_pause", "human_transfer"}:
            entry["status"] = "waiting_human"
            config = _flow_node_config(node)
            reason = config.get("reason") or (
                f"Transferir para {config.get('team') or config.get('queue')}"
                if config.get("team") or config.get("queue")
                else "Revisão humana necessária"
            )
            return FlowTraversalResult(
                status="waiting_human",
                trace=trace,
                current_node_id=current_id,
                next_node_id=next_node_id,
                steps_executed=initial_step_count + len(trace),
                pause_reason=str(reason),
            )
        if node_type == "end":
            entry["status"] = "completed"
            return FlowTraversalResult(
                status="completed",
                trace=trace,
                current_node_id=current_id,
                next_node_id=None,
                steps_executed=initial_step_count + len(trace),
            )
        current_id = next_node_id

    if current_id:
        return FlowTraversalResult(
            status="max_steps_reached",
            trace=trace,
            current_node_id=current_id,
            next_node_id=None,
            steps_executed=initial_step_count + len(trace),
        )
    return FlowTraversalResult(
        status="completed",
        trace=trace,
        current_node_id=last_node_id,
        next_node_id=None,
        steps_executed=initial_step_count + len(trace),
    )


def _matter_flow_context(matter: Matter, context: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "matter_id": str(matter.id),
        "lead_score": matter.lead_score,
        "doc_score": matter.doc_score,
        "practice_area": matter.practice_area.value,
        "health_subtheme": matter.health_subtheme.value if matter.health_subtheme else None,
        "health_object_name": matter.health_object_name,
        **(context or {}),
    }


def _stored_flow_trace(context: dict[str, Any]) -> list[FlowTraceEntry]:
    raw_trace = context.get("trace")
    if not isinstance(raw_trace, list):
        return []
    return [entry for entry in raw_trace if isinstance(entry, dict)]


def _stored_flow_step_count(context: dict[str, Any]) -> int:
    try:
        return max(0, int(context.get("flowStepCount", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _apply_local_traversal(
    run: FlowRun,
    traversal: FlowTraversalResult,
    *,
    context: dict[str, Any],
    max_steps: int,
    previous_trace: list[FlowTraceEntry] | None = None,
) -> None:
    runtime_context = {
        **context,
        "klmRuntime": "local_simulation",
        "externalActionsExecuted": 0,
        "trace": [*(previous_trace or []), *traversal.trace],
        "flowStepCount": traversal.steps_executed,
        "flowMaxSteps": max_steps,
    }
    runtime_context.pop("flowResumeNodeId", None)
    runtime_context.pop("flowLoopGuardTriggered", None)
    run.current_node_id = traversal.current_node_id
    run.completed_at = None
    run.paused_at = None
    run.pause_reason = None
    run.error_message = None

    if traversal.status == "completed":
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
    elif traversal.status == "waiting_human":
        # The caller must create the canonical HumanDecision before changing
        # the persisted run to waiting_human.  Keeping this traversal helper
        # side-effect-free avoids a local-runtime phantom wait.
        run.status = "active"
        runtime_context["flowResumeNodeId"] = traversal.next_node_id
    elif traversal.status == "max_steps_reached":
        run.status = "failed"
        run.error_message = f"Limite seguro de {max_steps} etapas atingido"
        runtime_context["flowLoopGuardTriggered"] = True
    else:
        run.status = "failed"
        run.error_message = "Grafo inválido durante execução local"

    run.context = runtime_context


class FlowService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _enforce_active_flow_capacity(self, workspace_id: UUID) -> None:
        async def load_current_quantity() -> int:
            value = await self.db.scalar(
                select(func.count(Flow.id)).where(
                    Flow.workspace_id == workspace_id,
                    Flow.is_template.is_(False),
                    Flow.is_active.is_(True),
                    Flow.deleted_at.is_(None),
                )
            )
            return int(value or 0)

        await EntitlementEnforcementService(self.db).enforce_stock(
            workspace_id=workspace_id,
            entitlement=EntitlementKey.automations,
            load_current_quantity=load_current_quantity,
        )

    async def _sync_thread_execution(self, run: FlowRun) -> None:
        raw_thread_id = (run.context or {}).get("threadId")
        if not raw_thread_id:
            return
        try:
            thread_id = UUID(str(raw_thread_id))
        except ValueError:
            return
        thread = await self.db.scalar(
            select(OmnichannelThread).where(
                OmnichannelThread.id == thread_id,
                OmnichannelThread.workspace_id == run.workspace_id,
            )
        )
        if thread is None:
            return
        metadata = dict(thread.thread_metadata or {})
        execution = dict(metadata.get("latestFlowExecution") or {})
        if execution.get("runId") != str(run.id):
            return
        now = datetime.now(UTC)
        execution.update(
            {
                "status": run.status,
                "currentNodeId": run.current_node_id,
                "pausedAt": run.paused_at.isoformat() if run.paused_at else None,
                "pauseReason": run.pause_reason,
                "completedAt": run.completed_at.isoformat() if run.completed_at else None,
                "updatedAt": now.isoformat(),
            }
        )
        thread.thread_metadata = {**metadata, "latestFlowExecution": execution}
        thread.updated_at = now

    def _new_revision(
        self,
        flow: Flow,
        *,
        revision_number: int,
        source: str,
        actor_user_id: UUID | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> FlowRevision:
        revision_id = uuid4()
        snapshot = build_flow_revision_snapshot(
            flow,
            revision_id=revision_id,
            revision_number=revision_number,
            overrides=overrides,
        )
        return FlowRevision(
            id=revision_id,
            flow_id=flow.id,
            revision_number=revision_number,
            snapshot=snapshot,
            snapshot_hash=flow_revision_snapshot_hash(snapshot),
            source=source,
            created_by_user_id=actor_user_id,
        )

    async def _revision_by_id(
        self,
        revision_id: UUID,
        *,
        flow_id: UUID,
    ) -> FlowRevision | None:
        return await self.db.scalar(
            select(FlowRevision).where(
                FlowRevision.id == revision_id,
                FlowRevision.flow_id == flow_id,
            )
        )

    async def _ensure_current_revision(
        self,
        flow: Flow,
        *,
        source: str = "legacy_lazy_backfill",
    ) -> tuple[FlowRevision, bool]:
        """Backfill direct/legacy Flow inserts before exposing or executing them."""
        if flow.current_revision_id:
            revision = await self._revision_by_id(
                flow.current_revision_id,
                flow_id=flow.id,
            )
            if not revision or revision.revision_number != flow.current_revision:
                raise FlowRevisionIntegrityError(
                    f"Flow {flow.id} aponta para uma revisão corrente inválida"
                )
            verified_flow_revision_snapshot(revision)
            return revision, False

        locked_flow = await self.db.scalar(select(Flow).where(Flow.id == flow.id).with_for_update())
        if not locked_flow:
            raise FlowRevisionIntegrityError(f"Flow {flow.id} desapareceu durante o backfill")
        if locked_flow.current_revision_id:
            revision = await self._revision_by_id(
                locked_flow.current_revision_id,
                flow_id=locked_flow.id,
            )
            if not revision or revision.revision_number != locked_flow.current_revision:
                raise FlowRevisionIntegrityError(
                    f"Flow {flow.id} aponta para uma revisão corrente inválida"
                )
            verified_flow_revision_snapshot(revision)
            return revision, False
        if locked_flow.current_revision not in (0, None):
            raise FlowRevisionIntegrityError(
                f"Flow {flow.id} possui versão sem revisão correspondente"
            )

        revision = self._new_revision(
            locked_flow,
            revision_number=1,
            source=source,
        )
        self.db.add(revision)
        await self.db.flush()
        locked_flow.current_revision = 1
        locked_flow.current_revision_id = revision.id
        await self.db.flush()
        logger.info(
            "flow.revision_backfilled",
            flow_id=str(flow.id),
            revision_id=str(revision.id),
        )
        return revision, True

    async def _current_revision_for_flow(
        self,
        flow: Flow,
        *,
        commit_backfill: bool = True,
    ) -> FlowRevision:
        revision, created = await self._ensure_current_revision(flow)
        if created and commit_backfill:
            await self.db.commit()
            await self.db.refresh(flow)
        return revision

    async def _revision_for_run(self, run: FlowRun) -> FlowRevision:
        revision = await self._revision_by_id(run.revision_id, flow_id=run.flow_id)
        if not revision:
            raise FlowRevisionIntegrityError(f"Flow run {run.id} não possui uma revisão válida")
        verified_flow_revision_snapshot(revision)
        return revision

    async def _agent_run_for_checkpoint(
        self,
        run: FlowRun,
        agent_run_id: str,
        checkpoint: dict[str, Any],
    ) -> AgentRun:
        try:
            resolved_agent_run_id = UUID(agent_run_id)
        except (TypeError, ValueError) as error:
            raise FlowRevisionIntegrityError(
                "Checkpoint do nó agent possui AgentRun inválido"
            ) from error

        expected_checkpoint = {
            "flowRunId": str(run.id),
            "revisionId": str(run.revision_id),
            "nodeId": str(checkpoint.get("nodeId") or ""),
            "agentRunId": str(resolved_agent_run_id),
        }
        for key, expected in expected_checkpoint.items():
            if str(checkpoint.get(key) or "") != expected:
                raise FlowRevisionIntegrityError(
                    f"Checkpoint do nó agent possui {key} inconsistente"
                )

        agent_run = await self.db.scalar(
            select(AgentRun)
            .where(
                AgentRun.id == resolved_agent_run_id,
                AgentRun.workspace_id == run.workspace_id,
                AgentRun.matter_id == run.matter_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if not agent_run:
            raise FlowRevisionIntegrityError(
                "AgentRun do checkpoint não foi encontrado neste workspace"
            )

        payload = dict(agent_run.input_payload or {})
        expected_payload = {
            "flow_run_id": str(run.id),
            "flow_revision_id": str(run.revision_id),
            "flow_node_id": str(checkpoint["nodeId"]),
            "flow_step": str(checkpoint.get("step") or ""),
        }
        for key, expected in expected_payload.items():
            if str(payload.get(key) or "") != expected:
                raise FlowRevisionIntegrityError(
                    f"AgentRun não corresponde ao checkpoint do flow ({key})"
                )
        if agent_run.trigger_source != f"flow:{run.flow_id}":
            raise FlowRevisionIntegrityError(
                "AgentRun não pertence ao flow indicado pelo checkpoint"
            )
        return agent_run

    async def _create_human_checkpoint(
        self,
        run: FlowRun,
        *,
        node_id: str,
        execution_step: int,
        reason: str,
        config: dict[str, Any] | None = None,
        purpose: str,
        agent_run_id: UUID | None = None,
    ) -> None:
        """Create the durable call before putting a flow in ``waiting_human``.

        FlowService is also used by the local simulation and by AgentRun
        settlement, so neither path may rely on the Celery-node helper to
        create a real assignee, SLA and owner notification.
        """
        from app.services.agent_platform_service import HumanRoutingService

        routing = dict(config or {})
        requested_user_id = routing.get("user_id") or routing.get("assigned_user_id")
        decision_key = f"{run.id}:{node_id}:{execution_step}:{purpose}"
        decision = await HumanRoutingService(self.db).create_decision(
            workspace_id=run.workspace_id,
            flow_run_id=run.id,
            agent_run_id=agent_run_id,
            decision_key=decision_key,
            options={
                **dict(routing.get("options") or {}),
                "reason": reason,
                "purpose": purpose,
            },
            required_roles=(
                list(routing.get("required_roles") or [])
                if (
                    routing.get("required_roles")
                    or routing.get("required_skills")
                    or requested_user_id
                )
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
            "agentRunId": str(agent_run_id) if agent_run_id else None,
        }
        context["humanCheckpoints"] = checkpoints
        run.context = context
        run.status = "waiting_human"
        run.completed_at = None
        run.paused_at = datetime.now(UTC)
        run.pause_reason = reason

    async def _apply_agent_run_settlement(
        self,
        run: FlowRun,
        agent_run: AgentRun,
        checkpoint: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> FlowAgentSettlement:
        agent_run_id = str(agent_run.id)
        if _flow_agent_checkpoint_is_settled(checkpoint):
            return FlowAgentSettlement(
                outcome="already_settled",
                flow_run_id=run.id,
            )
        if run.status in TERMINAL_FLOW_RUN_STATUSES:
            return FlowAgentSettlement(
                outcome="flow_terminal",
                flow_run_id=run.id,
            )

        context = dict(run.context or {})
        if str(context.get(FLOW_ACTIVE_AGENT_RUN_KEY) or "") != agent_run_id:
            raise FlowRevisionIntegrityError(
                "AgentRun não corresponde ao checkpoint ativo do flow run"
            )
        if str(checkpoint.get("revisionId") or "") != str(run.revision_id):
            raise FlowRevisionIntegrityError(
                "Checkpoint do nó agent não corresponde à revisão pinada"
            )
        node_id = str(checkpoint.get("nodeId") or "")
        if node_id != str(run.current_node_id or ""):
            raise FlowRevisionIntegrityError(
                "Checkpoint do nó agent não corresponde ao nó atual do flow run"
            )

        snapshot_nodes = snapshot.get("nodes") or []
        snapshot_edges = snapshot.get("edges") or []
        node = next(
            (item for item in snapshot_nodes if str(item.get("id")) == node_id),
            None,
        )
        if not node or node.get("type") != "agent":
            raise FlowRevisionIntegrityError(
                "Checkpoint do AgentRun não aponta para um nó agent da revisão pinada"
            )

        status = (
            agent_run.status.value
            if isinstance(agent_run.status, AgentRunStatus)
            else str(agent_run.status)
        )
        now = datetime.now(UTC)
        updated_checkpoint = {
            **checkpoint,
            "agentRunStatus": status,
            "updatedAt": now.isoformat(),
        }
        if agent_run.completed_at:
            updated_checkpoint["terminalAt"] = agent_run.completed_at.isoformat()

        executions = _flow_agent_executions(context)
        should_enqueue = False
        if status == AgentRunStatus.completed.value:
            next_node_id = _next_flow_node(node, snapshot_edges, context)[0]
            continuation_token = f"flow-agent-continuation:{agent_run_id}" if next_node_id else None
            updated_checkpoint.update(
                {
                    "state": "advanced" if next_node_id else "completed",
                    "advancedAt": now.isoformat(),
                    "continuationNodeId": next_node_id,
                    "continuationToken": continuation_token,
                }
            )
            context.pop(FLOW_ACTIVE_AGENT_RUN_KEY, None)
            run.paused_at = None
            run.pause_reason = None
            run.error_message = None
            if next_node_id:
                run.status = "active"
                run.current_node_id = next_node_id
                run.completed_at = None
                should_enqueue = True
                outcome: FlowAgentSettlementOutcome = "advanced"
            else:
                run.status = "completed"
                run.completed_at = now
                outcome = "completed"
        elif status in {AgentRunStatus.failed.value, AgentRunStatus.cancelled.value}:
            updated_checkpoint["state"] = status
            reason = f"AgentRun {agent_run_id} terminou com status {status}; revisão necessária."
            run.status = "active"
            run.completed_at = None
            run.paused_at = None
            run.pause_reason = None
            run.error_message = agent_run.error_message or (
                f"Execução do agente terminou com status {status}"
            )
            outcome = "failed"
        elif status == AgentRunStatus.waiting_human.value:
            updated_checkpoint["state"] = status
            reason = agent_run.error_message or (
                f"AgentRun {agent_run_id} aguarda revisão humana."
            )
            run.status = "active"
            run.completed_at = None
            run.paused_at = None
            run.pause_reason = None
            run.error_message = None
            outcome = "waiting_human"
        elif status in {
            AgentRunStatus.waiting_data.value,
            AgentRunStatus.paused_budget.value,
        }:
            # Missing input/budget is a durable human checkpoint, not a
            # generic polling state.  The marker prevents duplicate decisions
            # when the watchdog observes the same AgentRun more than once.
            updated_checkpoint["state"] = status
            run.status = "waiting_human"
            run.completed_at = None
            run.paused_at = now
            run.pause_reason = agent_run.error_message or (
                "Dados humanos necessários."
                if status == AgentRunStatus.waiting_data.value
                else "Orçamento de IA atingido; revisão necessária."
            )
            outcome = status
            if not checkpoint.get("humanCheckpointCreated"):
                await self._create_human_checkpoint(
                    run,
                    node_id=node_id,
                    execution_step=int(checkpoint.get("step") or 0),
                    reason=run.pause_reason,
                    config=_flow_node_config(node),
                    purpose=(
                        "agent_data_request"
                        if status == AgentRunStatus.waiting_data.value
                        else "agent_budget_review"
                    ),
                    agent_run_id=agent_run.id,
                )
                updated_checkpoint["humanCheckpointCreated"] = True
                # `_create_human_checkpoint` updates the run context itself;
                # preserve that durable checkpoint when writing the agent
                # execution projection below.
                created_context = dict(run.context or {})
                context["humanCheckpoints"] = dict(
                    created_context.get("humanCheckpoints") or context.get("humanCheckpoints") or {}
                )
        elif status in {
            AgentRunStatus.waiting_tool.value,
            AgentRunStatus.waiting_external.value,
        }:
            # Tool/provider waits remain durable runtime pauses.  They are not
            # silently converted into human work; the worker/watchdog owns
            # their retry/timeout path and can escalate explicitly later.
            updated_checkpoint["state"] = status
            run.status = "paused"
            run.completed_at = None
            run.paused_at = now
            run.pause_reason = agent_run.error_message or f"AgentRun aguardando {status}."
            outcome = status
        else:
            updated_checkpoint["state"] = status
            # Queued/running is an AgentRun poll, not a human handoff.  Do
            # not overload waiting_human or create a phantom human wait.
            run.status = "active"
            run.completed_at = None
            run.paused_at = None
            run.pause_reason = f"Aguardando conclusão do AgentRun {agent_run_id}."
            run.error_message = None
            outcome = "pending"

        executions[agent_run_id] = updated_checkpoint
        context[FLOW_AGENT_EXECUTIONS_KEY] = executions
        run.context = context
        if status in {AgentRunStatus.failed.value, AgentRunStatus.cancelled.value}:
            await self._create_human_checkpoint(
                run,
                node_id=node_id,
                execution_step=int(checkpoint.get("step") or 0),
                reason=reason,
                config=_flow_node_config(node),
                purpose="agent_failure_review",
                agent_run_id=agent_run.id,
            )
        elif status == AgentRunStatus.waiting_human.value:
            await self._create_human_checkpoint(
                run,
                node_id=node_id,
                execution_step=int(checkpoint.get("step") or 0),
                reason=reason,
                config=_flow_node_config(node),
                purpose="agent_review",
                agent_run_id=agent_run.id,
            )
        transition_flow_trace(
            run,
            node_id=node_id,
            status=(
                "completed"
                if outcome in {"advanced", "completed"}
                else "failed"
                if outcome == "failed"
                else "waiting_human"
                if outcome in {"waiting_human", "waiting_data", "paused_budget"}
                else "active"
            ),
            occurred_at=now,
        )
        return FlowAgentSettlement(
            outcome=outcome,
            flow_run_id=run.id,
            should_enqueue=should_enqueue,
            continuation_token=updated_checkpoint.get("continuationToken"),
        )

    async def _dispatch_agent_continuation(
        self,
        run: FlowRun,
        agent_run_id: UUID,
    ) -> bool:
        checkpoint = _flow_agent_executions(run.context).get(str(agent_run_id))
        if not checkpoint:
            return False
        continuation_token = str(checkpoint.get("continuationToken") or "")
        continuation_node_id = str(checkpoint.get("continuationNodeId") or "")
        if (
            not continuation_token
            or not continuation_node_id
            or checkpoint.get("continuationEnqueuedAt")
            or checkpoint.get("continuationConsumedAt")
        ):
            return False
        if run.status != "active" or str(run.current_node_id or "") != continuation_node_id:
            return False

        from app.workers.flow_tasks import execute_flow_run_step

        execute_flow_run_step.apply_async(
            args=[str(run.id), str(run.workspace_id), continuation_token],
            countdown=0,
        )

        locked_run = await self.db.scalar(
            select(FlowRun)
            .where(
                FlowRun.id == run.id,
                FlowRun.workspace_id == run.workspace_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if not locked_run:
            raise FlowRevisionIntegrityError(
                "FlowRun desapareceu ao registrar a continuação do nó agent"
            )
        context = dict(locked_run.context or {})
        executions = _flow_agent_executions(context)
        current_checkpoint = executions.get(str(agent_run_id))
        if not current_checkpoint:
            raise FlowRevisionIntegrityError(
                "Checkpoint do nó agent desapareceu após publicar a continuação"
            )
        if str(current_checkpoint.get("continuationToken") or "") != continuation_token:
            raise FlowRevisionIntegrityError(
                "Token de continuação do nó agent mudou durante a publicação"
            )
        if not current_checkpoint.get("continuationEnqueuedAt"):
            current_checkpoint["continuationEnqueuedAt"] = datetime.now(UTC).isoformat()
            executions[str(agent_run_id)] = current_checkpoint
            context[FLOW_AGENT_EXECUTIONS_KEY] = executions
            locked_run.context = context
            await self.db.commit()
            await self.db.refresh(locked_run)
        return True

    async def settle_agent_run(
        self,
        agent_run_id: UUID,
        workspace_id: UUID,
    ) -> FlowAgentSettlement:
        probe = await self.db.scalar(
            select(AgentRun).where(
                AgentRun.id == agent_run_id,
                AgentRun.workspace_id == workspace_id,
            )
        )
        if not probe:
            return FlowAgentSettlement(outcome="not_linked")
        raw_flow_run_id = (probe.input_payload or {}).get("flow_run_id")
        if not raw_flow_run_id:
            return FlowAgentSettlement(outcome="not_linked")
        try:
            flow_run_id = UUID(str(raw_flow_run_id))
        except ValueError as error:
            raise FlowRevisionIntegrityError(
                "AgentRun vinculado a flow possui flow_run_id inválido"
            ) from error

        run = await self.db.scalar(
            select(FlowRun)
            .where(
                FlowRun.id == flow_run_id,
                FlowRun.workspace_id == workspace_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if not run:
            raise FlowRevisionIntegrityError(
                "FlowRun vinculado ao AgentRun não foi encontrado neste workspace"
            )

        checkpoint = _flow_agent_executions(run.context).get(str(agent_run_id))
        if checkpoint is None:
            return FlowAgentSettlement(
                outcome="pending",
                flow_run_id=run.id,
            )
        if _flow_agent_checkpoint_is_settled(checkpoint):
            settlement = FlowAgentSettlement(
                outcome="already_settled",
                flow_run_id=run.id,
                should_enqueue=bool(
                    checkpoint.get("continuationToken")
                    and checkpoint.get("continuationNodeId")
                    and not checkpoint.get("continuationEnqueuedAt")
                    and not checkpoint.get("continuationConsumedAt")
                ),
                continuation_token=checkpoint.get("continuationToken"),
            )
            if settlement.should_enqueue:
                await self.db.commit()
                await self.db.refresh(run)
                await self._dispatch_agent_continuation(run, agent_run_id)
            return settlement

        revision = await self._revision_for_run(run)
        snapshot = verified_flow_revision_snapshot(revision)
        agent_run = await self._agent_run_for_checkpoint(
            run,
            str(agent_run_id),
            checkpoint,
        )
        settlement = await self._apply_agent_run_settlement(
            run,
            agent_run,
            checkpoint,
            snapshot,
        )
        if settlement.outcome in {"already_settled", "flow_terminal"}:
            return settlement

        await self._sync_thread_execution(run)
        await self.db.commit()
        await self.db.refresh(run)
        if settlement.should_enqueue:
            await self._dispatch_agent_continuation(run, agent_run_id)
        return settlement

    async def create_flow_agent_retry(
        self,
        original_agent_run_id: UUID,
        workspace_id: UUID,
        *,
        triggered_by_user_id: UUID | None = None,
    ) -> tuple[AgentRun, bool]:
        original_probe = await self.db.scalar(
            select(AgentRun).where(
                AgentRun.id == original_agent_run_id,
                AgentRun.workspace_id == workspace_id,
            )
        )
        if not original_probe:
            raise InvalidFlowRunTransition("AgentRun original não foi encontrado")
        raw_flow_run_id = (original_probe.input_payload or {}).get("flow_run_id")
        if not raw_flow_run_id:
            raise InvalidFlowRunTransition("AgentRun não está vinculado a um flow")
        try:
            flow_run_id = UUID(str(raw_flow_run_id))
        except ValueError as error:
            raise FlowRevisionIntegrityError(
                "AgentRun vinculado a flow possui flow_run_id inválido"
            ) from error

        run = await self.db.scalar(
            select(FlowRun)
            .where(
                FlowRun.id == flow_run_id,
                FlowRun.workspace_id == workspace_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if not run:
            raise FlowRevisionIntegrityError(
                "FlowRun vinculado ao retry não foi encontrado neste workspace"
            )
        executions = _flow_agent_executions(run.context)
        original_checkpoint = executions.get(str(original_agent_run_id))
        if not original_checkpoint:
            raise FlowRevisionIntegrityError("Checkpoint do AgentRun original não foi encontrado")

        superseded_by = original_checkpoint.get("supersededByAgentRunId")
        if superseded_by:
            try:
                superseded_by_id = UUID(str(superseded_by))
            except ValueError as error:
                raise FlowRevisionIntegrityError(
                    "Checkpoint de retry possui AgentRun sucessor inválido"
                ) from error
            existing_retry = await self.db.scalar(
                select(AgentRun).where(
                    AgentRun.id == superseded_by_id,
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.matter_id == run.matter_id,
                )
            )
            if not existing_retry:
                raise FlowRevisionIntegrityError("AgentRun sucessor do retry não foi encontrado")
            retry_checkpoint = executions.get(str(existing_retry.id))
            if not retry_checkpoint:
                raise FlowRevisionIntegrityError(
                    "Checkpoint do AgentRun sucessor não foi encontrado"
                )
            if str((run.context or {}).get(FLOW_ACTIVE_AGENT_RUN_KEY) or "") != str(
                existing_retry.id
            ):
                raise FlowRevisionIntegrityError(
                    "AgentRun sucessor não corresponde ao checkpoint ativo"
                )
            await self._agent_run_for_checkpoint(
                run,
                str(existing_retry.id),
                retry_checkpoint,
            )
            return existing_retry, False

        active_agent_run_id = str((run.context or {}).get(FLOW_ACTIVE_AGENT_RUN_KEY) or "")
        if active_agent_run_id != str(original_agent_run_id):
            raise InvalidFlowRunTransition("Somente o AgentRun ativo do nó pode ser reenfileirado")
        if run.status != "waiting_human":
            raise InvalidFlowRunTransition(
                f"Flow run em estado '{run.status}' não aceita retry do nó agent"
            )

        revision = await self._revision_for_run(run)
        snapshot = verified_flow_revision_snapshot(revision)
        original = await self._agent_run_for_checkpoint(
            run,
            str(original_agent_run_id),
            original_checkpoint,
        )
        original_status = (
            original.status.value
            if isinstance(original.status, AgentRunStatus)
            else str(original.status)
        )
        if original_status not in {
            AgentRunStatus.failed.value,
            AgentRunStatus.waiting_human.value,
            AgentRunStatus.cancelled.value,
        }:
            raise InvalidFlowRunTransition(
                f"AgentRun em estado '{original_status}' não pode ser reenfileirado"
            )
        node_id = str(original_checkpoint.get("nodeId") or "")
        node = next(
            (item for item in (snapshot.get("nodes") or []) if str(item.get("id")) == node_id),
            None,
        )
        if not node or node.get("type") != "agent":
            raise FlowRevisionIntegrityError(
                "Retry não corresponde a um nó agent da revisão pinada"
            )

        try:
            retry_attempt = int(original_checkpoint.get("retryAttempt", 0) or 0) + 1
        except (TypeError, ValueError) as error:
            raise FlowRevisionIntegrityError(
                "Checkpoint do nó agent possui retryAttempt inválido"
            ) from error
        input_payload = {
            **dict(original.input_payload or {}),
            "flow_run_id": str(run.id),
            "flow_revision_id": str(run.revision_id),
            "flow_revision": revision.revision_number,
            "flow_node_id": node_id,
            "flow_step": original_checkpoint.get("step"),
            "retry_of_agent_run_id": str(original.id),
            "flow_retry_attempt": retry_attempt,
        }
        dedupe_key = (
            f"flow-agent-retry:{run.id}:{run.revision_id}:"
            f"{node_id}:{original_checkpoint.get('step')}:{retry_attempt}"
        )
        from app.services.agent_runtime_service import AgentRuntimeService

        retried, created = await AgentRuntimeService(self.db).create_run_with_state(
            workspace_id=workspace_id,
            matter_id=run.matter_id,
            agent_name=original.agent_name,
            trigger_source=f"flow:{run.flow_id}",
            triggered_by_user_id=triggered_by_user_id,
            thread_id=original.thread_id,
            input_payload=input_payload,
            dedupe_key=dedupe_key,
        )

        now = datetime.now(UTC)
        original_checkpoint.update(
            {
                "state": "superseded",
                "supersededAt": now.isoformat(),
                "supersededByAgentRunId": str(retried.id),
                "updatedAt": now.isoformat(),
            }
        )
        retry_checkpoint = dict(original_checkpoint)
        for key in (
            "terminalAt",
            "advancedAt",
            "continuationNodeId",
            "continuationToken",
            "continuationEnqueuedAt",
            "continuationConsumedAt",
            "supersededAt",
            "supersededByAgentRunId",
        ):
            retry_checkpoint.pop(key, None)
        retry_checkpoint.update(
            {
                "agentRunId": str(retried.id),
                "agentName": retried.agent_name,
                "agentRunStatus": AgentRunStatus.queued.value,
                "state": AgentRunStatus.queued.value,
                "retryOfAgentRunId": str(original.id),
                "retryAttempt": retry_attempt,
                "linkedAt": now.isoformat(),
                "updatedAt": now.isoformat(),
            }
        )
        executions[str(original.id)] = original_checkpoint
        executions[str(retried.id)] = retry_checkpoint
        context = dict(run.context or {})
        context[FLOW_AGENT_EXECUTIONS_KEY] = executions
        context[FLOW_ACTIVE_AGENT_RUN_KEY] = str(retried.id)
        run.context = context
        # A retry is active work, not a human checkpoint.  Keeping the flow
        # active prevents the watchdog from fabricating a HumanDecision for a
        # human who was never called.
        run.status = "active"
        run.completed_at = None
        run.paused_at = None
        run.pause_reason = None
        run.error_message = None
        await self._sync_thread_execution(run)
        await self.db.flush()
        return retried, created

    # ── Flows ──────────────────────────────────────────────────────────

    async def list_flows(self, workspace_id: UUID) -> list[Flow]:
        result = await self.db.execute(
            select(Flow)
            .where(Flow.workspace_id == workspace_id, Flow.deleted_at.is_(None))
            .order_by(Flow.created_at.desc())
        )
        flows = list(result.scalars().all())
        created_revision = False
        for flow in flows:
            _, created = await self._ensure_current_revision(flow)
            created_revision = created_revision or created
        if created_revision:
            await self.db.commit()
            for flow in flows:
                await self.db.refresh(flow)
        return flows

    async def get_flow(
        self,
        flow_id: UUID,
        workspace_id: UUID,
        *,
        commit_backfill: bool = True,
    ) -> Flow | None:
        result = await self.db.execute(
            select(Flow).where(
                Flow.id == flow_id,
                Flow.workspace_id == workspace_id,
                Flow.deleted_at.is_(None),
            )
        )
        flow = result.scalar_one_or_none()
        if flow:
            await self._current_revision_for_flow(
                flow,
                commit_backfill=commit_backfill,
            )
        return flow

    async def get_template(
        self,
        template_id: UUID,
        workspace_id: UUID | None = None,
        *,
        commit_backfill: bool = True,
    ) -> Flow | None:
        scope = (
            Flow.workspace_id.is_(None)
            if workspace_id is None
            else or_(Flow.workspace_id.is_(None), Flow.workspace_id == workspace_id)
        )
        result = await self.db.execute(
            select(Flow).where(
                Flow.id == template_id,
                Flow.is_template.is_(True),
                Flow.deleted_at.is_(None),
                scope,
            )
        )
        template = result.scalar_one_or_none()
        if template:
            await self._current_revision_for_flow(
                template,
                commit_backfill=commit_backfill,
            )
        return template

    async def create_flow(
        self,
        workspace_id: UUID,
        data: FlowCreate,
        *,
        actor_user_id: UUID | None = None,
        commit: bool = True,
    ) -> Flow:
        validate_flow_graph(data.nodes, data.edges)
        if data.is_active:
            ensure_publishable_flow_graph(data.nodes, data.edges)
            await self._enforce_active_flow_capacity(workspace_id)
        flow = Flow(
            workspace_id=workspace_id,
            name=data.name,
            description=data.description,
            practice_area=data.practice_area,
            trigger=data.trigger,
            trigger_config=data.trigger_config,
            nodes=data.nodes,
            edges=data.edges,
            is_active=data.is_active,
            is_template=False,
        )
        self.db.add(flow)
        await self.db.flush()
        revision = self._new_revision(
            flow,
            revision_number=1,
            source="create",
            actor_user_id=actor_user_id,
        )
        self.db.add(revision)
        await self.db.flush()
        flow.current_revision = revision.revision_number
        flow.current_revision_id = revision.id
        if commit:
            await self.db.commit()
        else:
            await self.db.flush()
        await self.db.refresh(flow)
        logger.info(
            "flow.created",
            flow_id=str(flow.id),
            revision=flow.current_revision,
            revision_id=str(flow.current_revision_id),
        )
        return flow

    async def update_flow(
        self,
        flow_id: UUID,
        workspace_id: UUID,
        data: FlowUpdate,
        *,
        actor_user_id: UUID | None = None,
    ) -> Flow | None:
        flow = await self.get_flow(
            flow_id,
            workspace_id,
        )
        if not flow:
            return None

        expected_revision = data.expected_revision
        if expected_revision is None:
            if flow.current_revision != 1:
                raise FlowRevisionRequiredError(flow.current_revision)
            expected_revision = 1
            logger.warning(
                "flow.update_legacy_revision_inferred",
                flow_id=str(flow.id),
                expected_revision=expected_revision,
            )
        if expected_revision != flow.current_revision:
            raise FlowRevisionConflictError(
                expected_revision,
                flow.current_revision,
            )

        changes = data.model_dump(
            exclude_none=True,
            exclude={"expected_revision"},
        )
        resulting_nodes = changes.get("nodes", flow.nodes or [])
        resulting_edges = changes.get("edges", flow.edges or [])
        resulting_active = bool(changes.get("is_active", flow.is_active))
        if changes.get("is_active") is True and not flow.is_active:
            await self._enforce_active_flow_capacity(workspace_id)
        validate_flow_graph(resulting_nodes, resulting_edges)
        if resulting_active:
            ensure_publishable_flow_graph(resulting_nodes, resulting_edges)

        next_revision = expected_revision + 1
        updated_at = datetime.now(UTC)
        revision = self._new_revision(
            flow,
            revision_number=next_revision,
            source="update",
            actor_user_id=actor_user_id,
            overrides={**changes, "updated_at": updated_at},
        )
        self.db.add(revision)
        try:
            await self.db.flush()
            result = await self.db.execute(
                update(Flow)
                .where(
                    Flow.id == flow_id,
                    Flow.workspace_id == workspace_id,
                    Flow.deleted_at.is_(None),
                    Flow.current_revision == expected_revision,
                    Flow.current_revision_id == flow.current_revision_id,
                )
                .values(
                    **changes,
                    current_revision=next_revision,
                    current_revision_id=revision.id,
                    updated_at=updated_at,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                await self.db.rollback()
                current_revision = await self.db.scalar(
                    select(Flow.current_revision).where(Flow.id == flow_id)
                )
                raise FlowRevisionConflictError(
                    expected_revision,
                    int(current_revision or 0),
                )
            await self.db.commit()
        except IntegrityError as error:
            await self.db.rollback()
            current_revision = await self.db.scalar(
                select(Flow.current_revision).where(Flow.id == flow_id)
            )
            if current_revision != expected_revision:
                raise FlowRevisionConflictError(
                    expected_revision,
                    int(current_revision or 0),
                ) from error
            raise

        await self.db.refresh(flow)
        logger.info(
            "flow.updated",
            flow_id=str(flow.id),
            revision=flow.current_revision,
            revision_id=str(flow.current_revision_id),
            actor_user_id=str(actor_user_id) if actor_user_id else None,
        )
        return flow

    async def delete_flow(self, flow_id: UUID, workspace_id: UUID) -> bool:
        flow = await self.get_flow(flow_id, workspace_id)
        if not flow:
            return False
        flow.deleted_at = datetime.now(UTC)
        await self.db.commit()
        return True

    async def get_templates(self, practice_area: str | None = None) -> list[Flow]:
        """Retorna templates da biblioteca (is_template=True)."""
        query = select(Flow).where(
            Flow.is_template.is_(True),
            Flow.workspace_id.is_(None),
            Flow.deleted_at.is_(None),
        )
        if practice_area:
            query = query.where(Flow.practice_area == practice_area)
        query = query.order_by(Flow.practice_area, Flow.name)
        result = await self.db.execute(query)
        templates = list(result.scalars().all())
        created_revision = False
        for template in templates:
            _, created = await self._ensure_current_revision(template)
            created_revision = created_revision or created
        if created_revision:
            await self.db.commit()
            for template in templates:
                await self.db.refresh(template)
        return templates

    async def apply_template(
        self,
        template_id: UUID,
        workspace_id: UUID,
        custom_name: str | None = None,
        *,
        actor_user_id: UUID | None = None,
        activate: bool = False,
        commit: bool = True,
    ) -> Flow:
        """Copia um template para o workspace do usuário."""
        template = await self.get_template(
            template_id,
            workspace_id,
            commit_backfill=commit,
        )
        if not template or not template.is_template:
            raise ValueError("Template não encontrado")
        if activate:
            await self._enforce_active_flow_capacity(workspace_id)
        template_revision = await self._current_revision_for_flow(
            template,
            commit_backfill=commit,
        )
        snapshot = verified_flow_revision_snapshot(template_revision)
        template_nodes = snapshot.get("nodes") or []
        template_edges = snapshot.get("edges") or []
        validate_flow_graph(template_nodes, template_edges)
        if activate:
            ensure_publishable_flow_graph(template_nodes, template_edges)
        new_flow = Flow(
            workspace_id=workspace_id,
            name=custom_name or snapshot["name"],
            description=snapshot.get("description"),
            practice_area=snapshot["practice_area"],
            trigger=snapshot["trigger"],
            trigger_config=snapshot.get("trigger_config"),
            nodes=template_nodes,
            edges=template_edges,
            is_active=activate,
            is_template=False,
        )
        self.db.add(new_flow)
        await self.db.flush()
        revision = self._new_revision(
            new_flow,
            revision_number=1,
            source="template_apply",
            actor_user_id=actor_user_id,
        )
        self.db.add(revision)
        await self.db.flush()
        new_flow.current_revision = revision.revision_number
        new_flow.current_revision_id = revision.id
        await self.db.flush()
        if commit:
            await self.db.commit()
            await self.db.refresh(new_flow)
        return new_flow

    async def list_flow_revisions(
        self,
        flow_id: UUID,
        workspace_id: UUID,
    ) -> list[FlowRevision] | None:
        flow = await self.get_flow(flow_id, workspace_id)
        if not flow:
            return None
        revisions = await self.db.scalars(
            select(FlowRevision)
            .where(FlowRevision.flow_id == flow.id)
            .order_by(FlowRevision.revision_number.desc())
        )
        history = list(revisions.all())
        for revision in history:
            verified_flow_revision_snapshot(revision)
        return history

    async def get_flow_revision(
        self,
        flow_id: UUID,
        revision_number: int,
        workspace_id: UUID,
    ) -> FlowRevision | None:
        flow = await self.get_flow(flow_id, workspace_id)
        if not flow:
            return None
        revision = await self.db.scalar(
            select(FlowRevision).where(
                FlowRevision.flow_id == flow.id,
                FlowRevision.revision_number == revision_number,
            )
        )
        if revision:
            verified_flow_revision_snapshot(revision)
        return revision

    async def dry_run_flow(
        self,
        flow_id: UUID,
        matter_id: UUID,
        workspace_id: UUID,
        context: dict[str, Any] | None = None,
        *,
        max_steps: int = 100,
        revision_number: int | None = None,
    ) -> dict[str, Any]:
        """Traverse a graph without persisting a run or invoking any adapter."""
        flow = await self.get_flow(
            flow_id,
            workspace_id,
        )
        if not flow:
            raise ValueError("Flow não encontrado")
        if revision_number is None:
            revision = await self._current_revision_for_flow(flow)
        else:
            revision = await self.db.scalar(
                select(FlowRevision).where(
                    FlowRevision.flow_id == flow.id,
                    FlowRevision.revision_number == revision_number,
                )
            )
            if not revision:
                raise ValueError("Revisão do flow não encontrada")
        snapshot = verified_flow_revision_snapshot(revision)
        snapshot_nodes = snapshot.get("nodes") or []
        snapshot_edges = snapshot.get("edges") or []
        validate_flow_graph(snapshot_nodes, snapshot_edges)
        matter = await self.db.scalar(
            select(Matter).where(
                Matter.id == matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if not matter:
            raise ValueError("Caso não encontrado no workspace")

        run_context = _matter_flow_context(matter, context)
        traversal = _traverse_flow_graph(
            snapshot_nodes,
            snapshot_edges,
            run_context,
            start_node_id=_initial_flow_node_id(snapshot_nodes),
            max_steps=max_steps,
        )

        return {
            "flow_id": flow.id,
            "revision_id": revision.id,
            "revision": revision.revision_number,
            "version": revision.revision_number,
            "matter_id": matter.id,
            "status": traversal.status,
            "trace": traversal.trace,
            "max_steps": max_steps,
            "external_actions_executed": 0,
            "context": run_context,
        }

    # ── Flow Runs ───────────────────────────────────────────────────────

    async def _flow_authorization_context(
        self,
        *,
        workspace_id: UUID,
        revision: FlowRevision,
        actor_user_id: UUID | None,
    ) -> dict[str, Any]:
        """Snapshot the real workspace authority that starts this run.

        Manual runs use the authenticated actor. System-triggered runs use
        the human who published the immutable revision. Missing, inactive or
        revoked authority deliberately produces an empty permission set, so
        protected nodes fail closed in the worker.
        """
        authority_user_id = actor_user_id or revision.created_by_user_id
        if authority_user_id is None:
            return {
                "permissions": [],
                "authorizationActorId": None,
                "authorizationSource": "missing",
            }

        membership = await self.db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == authority_user_id,
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )
        if membership is None:
            return {
                "permissions": [],
                "authorizationActorId": str(authority_user_id),
                "authorizationSource": "inactive_or_missing_membership",
            }

        effective = get_effective_permissions(
            membership.role,
            membership.permission_grants,
            membership.permission_denials,
        )
        permission_keys = sorted(
            {
                f"{permission.module}:{permission.action}"
                for permission in effective
                if permission.scope in UNRESTRICTED_PERMISSION_SCOPES
            }
        )
        return {
            "permissions": permission_keys,
            "authorizationActorId": str(authority_user_id),
            "authorizationSource": "manual_actor" if actor_user_id else "revision_publisher",
        }

    async def start_flow_run(
        self,
        flow_id: UUID,
        matter_id: UUID,
        workspace_id: UUID,
        context: dict[str, Any] | None = None,
        *,
        thread_id: UUID | None = None,
        actor_user_id: UUID | None = None,
        task_id: UUID | None = None,
        application_idempotency_key_hash: str | None = None,
        commit: bool = True,
        publish: bool = True,
    ) -> FlowRun:
        if publish and not commit:
            raise ValueError("O dispatch do flow só pode ocorrer após o commit")
        flow = await self.get_flow(
            flow_id,
            workspace_id,
            commit_backfill=commit,
        )
        if not flow:
            raise ValueError("Flow não encontrado")
        revision = await self._current_revision_for_flow(flow, commit_backfill=commit)
        snapshot = verified_flow_revision_snapshot(revision)
        if not snapshot.get("is_active"):
            raise ValueError("Flow inativo não pode ser executado")
        snapshot_nodes = snapshot.get("nodes") or []
        snapshot_edges = snapshot.get("edges") or []
        validate_flow_graph(snapshot_nodes, snapshot_edges)

        matter = await self.db.scalar(
            select(Matter).where(
                Matter.id == matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if not matter:
            raise ValueError("Caso não encontrado no workspace")

        thread = None
        if thread_id:
            thread = await self.db.scalar(
                select(OmnichannelThread).where(
                    OmnichannelThread.id == thread_id,
                    OmnichannelThread.workspace_id == workspace_id,
                )
            )
            if not thread or thread.matter_id != matter_id:
                raise ValueError("Conversa não pertence ao caso informado")

        first_node_id = _initial_flow_node_id(snapshot_nodes)

        started_at = datetime.now(UTC)
        caller_context = {
            key: value
            for key, value in (context or {}).items()
            if key not in FLOW_AUTHORIZATION_CONTEXT_KEYS
        }
        authorization_context = await self._flow_authorization_context(
            workspace_id=workspace_id,
            revision=revision,
            actor_user_id=actor_user_id,
        )
        run_context = {
            **caller_context,
            **({"threadId": str(thread_id)} if thread_id else {}),
            **authorization_context,
        }
        run = FlowRun(
            flow_id=flow_id,
            revision_id=revision.id,
            matter_id=matter_id,
            workspace_id=workspace_id,
            task_id=task_id,
            application_idempotency_key_hash=application_idempotency_key_hash,
            status="active",
            current_node_id=first_node_id,
            context=run_context,
        )
        self.db.add(run)
        # HumanDecision keys are derived from the durable FlowRun id.  Flush
        # before any local traversal can reach a human node, otherwise the
        # key would be built with ``None`` and retries could collide.
        await self.db.flush()
        # Incrementar contador
        flow.run_count = (flow.run_count or 0) + 1

        local_simulation = not automation_worker_allowed() or bool(
            (snapshot.get("trigger_config") or {}).get("localOnly")
        )
        if local_simulation:
            max_steps = _max_flow_steps(snapshot.get("trigger_config"))
            simulation_context = _matter_flow_context(matter, run_context)
            traversal = _traverse_flow_graph(
                snapshot_nodes,
                snapshot_edges,
                simulation_context,
                start_node_id=first_node_id,
                max_steps=max_steps,
            )
            _apply_local_traversal(
                run,
                traversal,
                context=simulation_context,
                max_steps=max_steps,
            )
            if traversal.status == "waiting_human":
                node = next(
                    (
                        item
                        for item in snapshot_nodes
                        if str(item.get("id")) == str(traversal.current_node_id)
                    ),
                    None,
                )
                await self._create_human_checkpoint(
                    run,
                    node_id=str(traversal.current_node_id),
                    execution_step=traversal.steps_executed,
                    reason=traversal.pause_reason or "Revisão humana necessária",
                    config=_flow_node_config(node or {}),
                    purpose=str((node or {}).get("type") or "human_pause"),
                )
        else:
            from app.workers.flow_tasks import _record_root_flow_step_dispatch

            _record_root_flow_step_dispatch(
                run,
                target_node_id=first_node_id,
                available_at=started_at,
                source_type="flow_start",
            )
        await self.db.flush()

        if thread:
            execution_state = {
                "flowId": str(flow.id),
                "flowName": snapshot.get("name"),
                "flowRevisionId": str(revision.id),
                "flowRevision": revision.revision_number,
                "runId": str(run.id),
                "status": run.status,
                "startedAt": started_at.isoformat(),
                "mode": "local_simulation" if local_simulation else "worker",
                "externalActionsExecuted": 0 if local_simulation else None,
            }
            thread.thread_metadata = {
                **(thread.thread_metadata or {}),
                "latestFlowExecution": execution_state,
            }
            thread.updated_at = started_at
            if not local_simulation:
                log_body = (
                    f"Fluxo ativado: {snapshot.get('name')}. Execução encaminhada ao runtime."
                )
            elif run.status == "completed":
                log_body = (
                    f"Fluxo ativado: {snapshot.get('name')}. "
                    "Execução local concluída sem ações externas."
                )
            elif run.status == "waiting_human":
                log_body = (
                    f"Fluxo ativado: {snapshot.get('name')}. "
                    "Execução local pausada para revisão humana, sem ações externas."
                )
            else:
                log_body = (
                    f"Fluxo ativado: {snapshot.get('name')}. "
                    "Execução local interrompida pelo limite de segurança, sem ações externas."
                )
            self.db.add(
                OmnichannelMessage(
                    workspace_id=workspace_id,
                    thread_id=thread.id,
                    matter_id=matter_id,
                    author_user_id=None,
                    channel=OmnichannelChannel.internal,
                    direction=MessageDirection.internal,
                    author_type=MessageAuthorType.system,
                    author_label="KLM Flows",
                    body=log_body,
                    idempotency_key=f"flow-activation:{run.id}",
                    delivery_status=MessageDeliveryStatus.received,
                    occurred_at=started_at,
                    message_metadata={
                        "visibility": "internal_only",
                        "customerVisible": False,
                        "source": "flow_activation",
                        "flowId": str(flow.id),
                        "flowRunId": str(run.id),
                        "flowName": snapshot.get("name"),
                        "flowRevisionId": str(revision.id),
                        "flowRevision": revision.revision_number,
                        "status": run.status,
                        "externalActionsExecuted": 0 if local_simulation else None,
                    },
                )
            )
            actor_label = await resolve_human_actor_label(self.db, actor_user_id)
            self.db.add(
                KlmAuditEvent(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    actor_type="human",
                    actor_label=actor_label,
                    event_type="flow_activated_from_conversation",
                    entity_type="flow_run",
                    entity_id=run.id,
                    summary=(f"Flow '{snapshot.get('name')}' ativado a partir da conversa."),
                    correlation_id=f"flow-run:{run.id}",
                    after_data={
                        **execution_state,
                        "threadId": str(thread.id),
                        "matterId": str(matter_id),
                    },
                )
            )

        if commit:
            await self.db.commit()
            await self.db.refresh(run)
        else:
            await self.db.flush()

        if local_simulation:
            logger.info(
                "flow_run.simulated_locally",
                run_id=str(run.id),
                flow_id=str(flow_id),
                nodes=len(run.context.get("trace", [])),
            )
            return run

        if publish:
            self.publish_initial_flow_run(run, workspace_id)

        logger.info(
            "flow_run.started", run_id=str(run.id), flow_id=str(flow_id), matter_id=str(matter_id)
        )
        return run

    async def start_task_flow_run(
        self,
        *,
        task_id: UUID,
        flow_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        idempotency_key: str,
    ) -> FlowRun:
        """Attach one published flow revision to one matter task, idempotently."""
        key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        existing = await self.db.scalar(
            select(FlowRun).where(
                FlowRun.workspace_id == workspace_id,
                FlowRun.application_idempotency_key_hash == key_hash,
            )
        )
        if existing is not None:
            if existing.task_id != task_id or existing.flow_id != flow_id:
                raise TaskFlowIdempotencyConflict(
                    "Idempotency-Key já utilizada para outra aplicação de workflow."
                )
            return existing

        task = await self.db.scalar(
            select(Task).where(Task.id == task_id, Task.workspace_id == workspace_id)
        )
        if task is None:
            raise TaskFlowApplicationError("Tarefa não encontrada no workspace.")
        if task.matter_id is None:
            raise TaskFlowApplicationError(
                "Neste primeiro vertical, workflows exigem tarefa vinculada a um caso."
            )

        flow = await self.get_flow(flow_id, workspace_id, commit_backfill=True)
        if flow is None:
            raise TaskFlowApplicationError("Workflow não encontrado no workspace.")
        revision = await self._current_revision_for_flow(flow, commit_backfill=True)
        snapshot = verified_flow_revision_snapshot(revision)
        if any(
            isinstance(node, dict) and node.get("type") == "create_task"
            for node in (snapshot.get("nodes") or [])
        ):
            raise TaskFlowApplicationError(
                "A revisão atual cria tarefas e não pode ser aplicada a uma tarefa existente."
            )

        try:
            run = await self.start_flow_run(
                flow_id,
                task.matter_id,
                workspace_id,
                actor_user_id=actor_user_id,
                task_id=task_id,
                application_idempotency_key_hash=key_hash,
                commit=False,
                publish=False,
            )
            actor_label = await resolve_human_actor_label(self.db, actor_user_id)
            self.db.add(
                KlmAuditEvent(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    actor_type="human",
                    actor_label=actor_label,
                    event_type="task_workflow_applied",
                    entity_type="flow_run",
                    entity_id=run.id,
                    summary=f"Workflow '{snapshot.get('name') or flow.name}' aplicado à tarefa.",
                    correlation_id=f"task-flow-run:{run.id}",
                    after_data={
                        "taskId": str(task_id),
                        "matterId": str(task.matter_id),
                        "flowId": str(flow.id),
                        "flowRevisionId": str(revision.id),
                        "flowRevision": revision.revision_number,
                        "flowRunId": str(run.id),
                        "idempotencyKeyHash": key_hash,
                    },
                )
            )
            await self.db.commit()
            await self.db.refresh(run)
        except IntegrityError:
            await self.db.rollback()
            existing = await self.db.scalar(
                select(FlowRun).where(
                    FlowRun.workspace_id == workspace_id,
                    FlowRun.application_idempotency_key_hash == key_hash,
                )
            )
            if existing is None:
                raise
            if existing.task_id != task_id or existing.flow_id != flow_id:
                raise TaskFlowIdempotencyConflict(
                    "Idempotency-Key já utilizada para outra aplicação de workflow."
                )
            return existing

        self.publish_initial_flow_run(run, workspace_id)
        return run

    async def list_task_flow_runs(
        self,
        *,
        task_id: UUID,
        workspace_id: UUID,
    ) -> list[tuple[FlowRun, str]]:
        result = await self.db.execute(
            select(FlowRun, Flow.name)
            .join(Flow, Flow.id == FlowRun.flow_id)
            .where(
                FlowRun.workspace_id == workspace_id,
                FlowRun.task_id == task_id,
            )
            .order_by(FlowRun.started_at.desc())
        )
        return [(run, flow_name) for run, flow_name in result.all()]

    @staticmethod
    def publish_initial_flow_run(run: FlowRun, workspace_id: UUID) -> bool:
        """Publica somente intents raiz já commitados; recovery cobre falha do broker."""
        from app.workers.flow_tasks import FlowStepDispatchError, _publish_flow_step_dispatch

        raw_dispatches = (run.context or {}).get("flowStepDispatches")
        if not isinstance(raw_dispatches, dict):
            return False
        pending = [
            (str(token), dict(record))
            for token, record in raw_dispatches.items()
            if isinstance(record, dict)
            and record.get("sourceType") == "flow_start"
            and record.get("state", "pending") == "pending"
            and not record.get("consumedAt")
        ]
        if not pending:
            return False
        for dispatch_token, dispatch_record in pending:
            try:
                _publish_flow_step_dispatch(
                    flow_run_id=str(run.id),
                    workspace_id=workspace_id,
                    token=dispatch_token,
                    record=dispatch_record,
                )
            except FlowStepDispatchError:
                logger.warning(
                    "flow_run.initial_dispatch_deferred",
                    run_id=str(run.id),
                    dispatch_token=dispatch_token,
                )
                return False
        return True

    async def pause_flow_run(
        self, run_id: UUID, workspace_id: UUID, reason: str = ""
    ) -> FlowRun | None:
        result = await self.db.execute(
            select(FlowRun)
            .where(FlowRun.id == run_id, FlowRun.workspace_id == workspace_id)
            .with_for_update()
        )
        run = result.scalar_one_or_none()
        if not run:
            return None
        if run.status != "active":
            raise InvalidFlowRunTransition(
                f"Flow run em estado '{run.status}' não pode ser pausado"
            )
        if self._is_local_simulation(run):
            raise InvalidFlowRunTransition(
                "Execução local síncrona não pode ser pausada manualmente"
            )
        run.status = "paused"
        run.paused_at = datetime.now(UTC)
        run.pause_reason = reason
        await self._sync_thread_execution(run)
        await self.db.commit()
        await self.db.refresh(run)
        return run

    async def resume_flow_run(self, run_id: UUID, workspace_id: UUID) -> FlowRun | None:
        result = await self.db.execute(
            select(FlowRun)
            .where(FlowRun.id == run_id, FlowRun.workspace_id == workspace_id)
            .with_for_update()
        )
        run = result.scalar_one_or_none()
        if not run:
            return None
        if run.status not in RESUMABLE_FLOW_RUN_STATUSES:
            raise InvalidFlowRunTransition(
                f"Flow run em estado '{run.status}' não pode ser retomado"
            )
        uncertain_action = (run.context or {}).get("uncertainExternalAction")
        if isinstance(uncertain_action, dict) and uncertain_action.get("reconciliationRequired"):
            raise InvalidFlowRunTransition(
                "Efeito externo incerto exige reconciliação explícita antes da retomada"
            )

        revision = await self._revision_for_run(run)
        snapshot = verified_flow_revision_snapshot(revision)
        if not snapshot.get("is_active"):
            raise InvalidFlowRunTransition("Flow inativo não pode ser retomado")
        snapshot_nodes = snapshot.get("nodes") or []
        snapshot_edges = snapshot.get("edges") or []
        validate_flow_graph(snapshot_nodes, snapshot_edges)
        trigger_config = snapshot.get("trigger_config") or {}
        local_simulation = self._is_local_simulation(run) or bool(trigger_config.get("localOnly"))

        if local_simulation:
            if run.status != "waiting_human":
                raise InvalidFlowRunTransition(
                    "Execução local em pausa manual não pode ser retomada; "
                    "somente waiting_human possui continuação local"
                )
            run_context = dict(run.context or {})
            previous_trace = _stored_flow_trace(run_context)
            step_count = _stored_flow_step_count(run_context)
            max_steps = _max_flow_steps(trigger_config)
            if "flowResumeNodeId" in run_context:
                raw_resume_node_id = run_context.get("flowResumeNodeId")
                resume_node_id = str(raw_resume_node_id) if raw_resume_node_id is not None else None
            else:
                current_node = next(
                    (node for node in snapshot_nodes if str(node.get("id")) == run.current_node_id),
                    None,
                )
                resume_node_id = (
                    _next_flow_node(current_node, snapshot_edges, run_context)[0]
                    if current_node
                    else run.current_node_id
                )

            if resume_node_id is None:
                traversal = FlowTraversalResult(
                    status="completed",
                    trace=[],
                    current_node_id=run.current_node_id,
                    next_node_id=None,
                    steps_executed=step_count,
                )
            else:
                traversal = _traverse_flow_graph(
                    snapshot_nodes,
                    snapshot_edges,
                    run_context,
                    start_node_id=resume_node_id,
                    max_steps=max_steps,
                    initial_step_count=step_count,
                )
            _apply_local_traversal(
                run,
                traversal,
                context=run_context,
                max_steps=max_steps,
                previous_trace=previous_trace,
            )
            if traversal.status == "waiting_human":
                node = next(
                    (
                        item
                        for item in snapshot_nodes
                        if str(item.get("id")) == str(traversal.current_node_id)
                    ),
                    None,
                )
                await self._create_human_checkpoint(
                    run,
                    node_id=str(traversal.current_node_id),
                    execution_step=traversal.steps_executed,
                    reason=traversal.pause_reason or "Revisão humana necessária",
                    config=_flow_node_config(node or {}),
                    purpose=str((node or {}).get("type") or "human_pause"),
                )
            await self._sync_thread_execution(run)
            await self.db.commit()
            await self.db.refresh(run)
            return run

        active_agent_checkpoint = _active_flow_agent_checkpoint(run)
        if active_agent_checkpoint:
            checkpoint_agent_run_id, checkpoint = active_agent_checkpoint
            agent_run = await self._agent_run_for_checkpoint(
                run,
                checkpoint_agent_run_id,
                checkpoint,
            )
            agent_status = (
                agent_run.status.value
                if isinstance(agent_run.status, AgentRunStatus)
                else str(agent_run.status)
            )
            if agent_status != AgentRunStatus.completed.value:
                raise InvalidFlowRunTransition(
                    "Nó agent não pode ser retomado antes de um AgentRun "
                    f"terminal bem-sucedido (status atual: {agent_status})"
                )
            if not automation_worker_allowed():
                raise ExternalDeliveryBlockedError(
                    "Retomada bloqueada: runtime de automação permanece desabilitado"
                )

            settlement = await self._apply_agent_run_settlement(
                run,
                agent_run,
                checkpoint,
                snapshot,
            )
            await self._sync_thread_execution(run)
            await self.db.commit()
            await self.db.refresh(run)
            if settlement.should_enqueue:
                await self._dispatch_agent_continuation(run, agent_run.id)
            return run

        if not automation_worker_allowed():
            raise ExternalDeliveryBlockedError(
                "Retomada bloqueada: runtime de automação permanece desabilitado"
            )

        should_enqueue = True
        blocked_external = (run.context or {}).get("blockedExternalAction")
        if blocked_external:
            if not external_delivery_allowed():
                raise ExternalDeliveryBlockedError(
                    "Retomada bloqueada: o canal externo deste nó permanece desabilitado"
                )
            if str(blocked_external.get("nodeId") or "") != str(run.current_node_id or ""):
                raise FlowRevisionIntegrityError(
                    "Checkpoint externo não corresponde ao nó atual do flow run"
                )
        elif run.status == "waiting_human":
            current_node = next(
                (node for node in snapshot_nodes if str(node.get("id")) == run.current_node_id),
                None,
            )
            if current_node:
                transition_flow_trace(
                    run,
                    node_id=str(current_node.get("id") or run.current_node_id or ""),
                    status="completed",
                )
            next_node_id = (
                _next_flow_node(current_node, snapshot_edges, dict(run.context or {}))[0]
                if current_node
                else None
            )
            if next_node_id:
                run.current_node_id = next_node_id
            else:
                run.status = "completed"
                run.completed_at = datetime.now(UTC)
                should_enqueue = False

        if should_enqueue:
            run.status = "active"
        run.paused_at = None
        run.pause_reason = None
        pending_dispatch = None
        if should_enqueue:
            from app.workers.flow_tasks import _record_root_flow_step_dispatch

            pending_dispatch = _record_root_flow_step_dispatch(
                run,
                target_node_id=str(run.current_node_id),
                available_at=datetime.now(UTC),
                source_type="flow_resume",
            )
        await self._sync_thread_execution(run)
        await self.db.commit()
        await self.db.refresh(run)
        if pending_dispatch:
            from app.workers.flow_tasks import (
                FlowStepDispatchError,
                _publish_flow_step_dispatch,
            )

            dispatch_token, dispatch_record = pending_dispatch
            try:
                _publish_flow_step_dispatch(
                    flow_run_id=str(run.id),
                    workspace_id=workspace_id,
                    token=dispatch_token,
                    record=dispatch_record,
                )
            except FlowStepDispatchError:
                logger.warning(
                    "flow_run.resume_dispatch_deferred",
                    run_id=str(run.id),
                    dispatch_token=dispatch_token,
                )
        return run

    async def reconcile_uncertain_external_action(
        self,
        run_id: UUID,
        workspace_id: UUID,
        *,
        outcome: Literal["delivered", "not_delivered"],
        actor_user_id: UUID,
        provider_reference: str | None = None,
        note: str | None = None,
    ) -> FlowRun | None:
        result = await self.db.execute(
            select(FlowRun)
            .where(FlowRun.id == run_id, FlowRun.workspace_id == workspace_id)
            .with_for_update()
        )
        run = result.scalar_one_or_none()
        if not run:
            return None
        if run.status != "waiting_human":
            raise InvalidFlowRunTransition(
                f"Flow run em estado '{run.status}' não possui efeito externo para reconciliar"
            )

        from app.workers.flow_tasks import (
            FlowStepDispatchError,
            _active_flow_external_intent,
            _publish_flow_step_dispatch,
            _record_root_flow_step_dispatch,
            _update_flow_external_intent,
        )

        active_intent = _active_flow_external_intent(
            run.context,
            node_id=str(run.current_node_id or ""),
        )
        if not active_intent or active_intent[1].get("state") != "uncertain":
            raise InvalidFlowRunTransition("Flow run não possui uma intenção externa incerta ativa")
        intent_key, intent_record = active_intent
        uncertain_action = (run.context or {}).get("uncertainExternalAction")
        if (
            not isinstance(uncertain_action, dict)
            or str(uncertain_action.get("intentKey") or "") != intent_key
        ):
            raise FlowRevisionIntegrityError(
                "Checkpoint de incerteza não corresponde à intenção externa ativa"
            )
        if not automation_worker_allowed():
            raise ExternalDeliveryBlockedError(
                "Reconciliação bloqueada: runtime de automação permanece desabilitado"
            )
        if outcome == "not_delivered" and not external_delivery_allowed():
            raise ExternalDeliveryBlockedError(
                "Nova tentativa bloqueada: entregas externas permanecem desabilitadas"
            )

        now = datetime.now(UTC)
        common_updates = {
            "reconciliationRequired": False,
            "reconciledAt": now.isoformat(),
            "reconciledByUserId": str(actor_user_id),
            "reconciliationOutcome": outcome,
            "reconciliationNote": note,
            "attemptLeaseExpiresAt": None,
        }
        if outcome == "delivered":
            _update_flow_external_intent(
                run,
                intent_key,
                state="confirmed",
                confirmedAt=now.isoformat(),
                providerReference=(provider_reference or intent_record.get("providerReference")),
                **common_updates,
            )
        else:
            _update_flow_external_intent(
                run,
                intent_key,
                state="prepared",
                retryAuthorizedAt=now.isoformat(),
                retryAuthorizedByUserId=str(actor_user_id),
                attemptToken=None,
                **common_updates,
            )

        context = dict(run.context or {})
        context.pop("uncertainExternalAction", None)
        run.context = context
        run.status = "active"
        run.paused_at = None
        run.pause_reason = None
        run.error_message = None
        dispatch_token, dispatch_record = _record_root_flow_step_dispatch(
            run,
            target_node_id=str(run.current_node_id),
            available_at=now,
            source_type="external_action_reconciliation",
        )
        actor_label = await resolve_human_actor_label(self.db, actor_user_id)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type="flow_external_action_reconciled",
                entity_type="flow_run",
                entity_id=run.id,
                summary=(
                    "Efeito externo confirmado como entregue."
                    if outcome == "delivered"
                    else "Efeito externo confirmado como não entregue; nova tentativa autorizada."
                ),
                correlation_id=f"flow-run:{run.id}",
                before_data={
                    "intentKey": intent_key,
                    "state": intent_record.get("state"),
                    "uncertainAt": intent_record.get("uncertainAt"),
                },
                after_data={
                    "intentKey": intent_key,
                    "outcome": outcome,
                    "providerReference": provider_reference,
                    "note": note,
                },
            )
        )
        await self._sync_thread_execution(run)
        await self.db.commit()
        await self.db.refresh(run)

        try:
            _publish_flow_step_dispatch(
                flow_run_id=str(run.id),
                workspace_id=workspace_id,
                token=dispatch_token,
                record=dispatch_record,
            )
        except FlowStepDispatchError:
            logger.warning(
                "flow_run.reconciliation_dispatch_deferred",
                run_id=str(run.id),
                dispatch_token=dispatch_token,
            )
        return run

    async def cancel_flow_run(self, run_id: UUID, workspace_id: UUID) -> FlowRun | None:
        result = await self.db.execute(
            select(FlowRun)
            .where(FlowRun.id == run_id, FlowRun.workspace_id == workspace_id)
            .with_for_update()
        )
        run = result.scalar_one_or_none()
        if not run:
            return None
        if run.status in TERMINAL_FLOW_RUN_STATUSES:
            raise InvalidFlowRunTransition(
                f"Flow run em estado '{run.status}' não pode ser cancelado"
            )
        if run.status not in {"active", "paused", "waiting_human"}:
            raise InvalidFlowRunTransition(
                f"Flow run em estado '{run.status}' não pode ser cancelado"
            )

        context = run.context or {}
        active_intent_key = str(context.get("flowActiveExternalIntent") or "")
        raw_intents = context.get("flowExternalIntents")
        active_intent = (
            raw_intents.get(active_intent_key)
            if active_intent_key and isinstance(raw_intents, dict)
            else None
        )
        if (
            isinstance(active_intent, dict)
            and not active_intent.get("consumedAt")
            and active_intent.get("state") in {"dispatching", "confirmed", "uncertain"}
        ):
            raise InvalidFlowRunTransition(
                "Efeito externo já iniciado exige conclusão ou reconciliação antes do cancelamento"
            )

        run.status = "cancelled"
        run.completed_at = datetime.now(UTC)
        run.paused_at = None
        run.pause_reason = None
        await self._sync_thread_execution(run)
        await self.db.execute(
            update(ScheduledMessage)
            .where(
                ScheduledMessage.flow_run_id == run.id,
                ScheduledMessage.workspace_id == workspace_id,
                or_(
                    ScheduledMessage.status == "pending",
                    (
                        (ScheduledMessage.status == "processing")
                        & ScheduledMessage.delivery_attempted_at.is_(None)
                    ),
                ),
            )
            .values(status="cancelled")
        )
        await self.db.commit()
        await self.db.refresh(run)
        return run

    @staticmethod
    def _is_local_simulation(run: FlowRun) -> bool:
        return (run.context or {}).get("klmRuntime") == "local_simulation"

    # ── Scheduled Messages ──────────────────────────────────────────────

    async def schedule_message(
        self,
        matter_id: UUID | None,
        workspace_id: UUID,
        contact_phone: str,
        message: str,
        scheduled_at: datetime,
        flow_run_id: UUID | None = None,
    ) -> ScheduledMessage:
        from app.services.whatsapp.helpers import normalize_phone_number

        if not external_delivery_allowed():
            raise ExternalDeliveryBlockedError(
                "Agendamento bloqueado: entregas externas estão desabilitadas"
            )
        if not matter_id:
            raise ValueError("Caso é obrigatório para agendar uma mensagem")
        matter = await self.db.scalar(
            select(Matter).where(
                Matter.id == matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if not matter:
            raise ValueError("Caso não encontrado no workspace")
        if flow_run_id:
            flow_run = await self.db.scalar(
                select(FlowRun).where(
                    FlowRun.id == flow_run_id,
                    FlowRun.workspace_id == workspace_id,
                )
            )
            if not flow_run or flow_run.matter_id != matter_id:
                raise ValueError("Flow run não pertence ao caso informado")
            if flow_run.status in TERMINAL_FLOW_RUN_STATUSES:
                raise InvalidFlowRunTransition(
                    f"Flow run em estado '{flow_run.status}' não aceita agendamentos"
                )
            if self._is_local_simulation(flow_run):
                raise InvalidFlowRunTransition(
                    "Execução local simulada não aceita agendamentos externos"
                )
            revision = await self._revision_for_run(flow_run)
            snapshot = verified_flow_revision_snapshot(revision)
            if not snapshot.get("is_active"):
                raise InvalidFlowRunTransition("Flow inativo não aceita agendamentos externos")
            if (snapshot.get("trigger_config") or {}).get("localOnly"):
                raise InvalidFlowRunTransition(
                    "Execução local simulada não aceita agendamentos externos"
                )
        normalized_phone = normalize_phone_number(contact_phone)
        if normalized_phone is None:
            raise ValueError(
                "Telefone inválido para agendamento. Use DDI, DDD e número, como 5561999999999."
            )
        msg = ScheduledMessage(
            matter_id=matter_id,
            workspace_id=workspace_id,
            contact_phone=normalized_phone,
            message=message,
            scheduled_at=scheduled_at,
            flow_run_id=flow_run_id,
        )
        self.db.add(msg)
        await self.db.commit()
        await self.db.refresh(msg)
        return msg

    async def list_scheduled_messages(
        self, workspace_id: UUID, matter_id: UUID | None = None
    ) -> list[ScheduledMessage]:
        query = select(ScheduledMessage).where(ScheduledMessage.workspace_id == workspace_id)
        if matter_id:
            query = query.where(ScheduledMessage.matter_id == matter_id)
        query = query.order_by(ScheduledMessage.scheduled_at)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def reconcile_scheduled_message(
        self,
        msg_id: UUID,
        workspace_id: UUID,
        *,
        outcome: Literal["delivered", "not_delivered"],
        actor_user_id: UUID,
        provider_reference: str | None = None,
        retry_at: datetime | None = None,
        note: str | None = None,
    ) -> ScheduledMessage | None:
        result = await self.db.execute(
            select(ScheduledMessage)
            .where(
                ScheduledMessage.id == msg_id,
                ScheduledMessage.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        msg = result.scalar_one_or_none()
        if not msg:
            return None
        if msg.status != "delivery_uncertain":
            raise InvalidFlowRunTransition(
                f"Mensagem em estado '{msg.status}' não exige reconciliação"
            )
        if outcome == "not_delivered":
            if not automation_worker_allowed():
                raise ExternalDeliveryBlockedError(
                    "Nova tentativa bloqueada: runtime de automação permanece desabilitado"
                )
            if not external_delivery_allowed():
                raise ExternalDeliveryBlockedError(
                    "Nova tentativa bloqueada: entregas externas permanecem desabilitadas"
                )

        now = datetime.now(UTC)
        before_data = {
            "status": msg.status,
            "deliveryUncertainAt": (
                msg.delivery_uncertain_at.isoformat() if msg.delivery_uncertain_at else None
            ),
            "deliveryAttemptCount": msg.delivery_attempt_count,
        }
        if outcome == "delivered":
            msg.status = "sent"
            msg.sent_at = now
            msg.provider_message_id = provider_reference or msg.provider_message_id
        else:
            msg.status = "pending"
            resolved_retry_at = retry_at or now
            if resolved_retry_at.tzinfo is None:
                resolved_retry_at = resolved_retry_at.replace(tzinfo=UTC)
            else:
                resolved_retry_at = resolved_retry_at.astimezone(UTC)
            msg.scheduled_at = resolved_retry_at
            msg.sent_at = None
            msg.provider_message_id = None
            msg.delivery_attempted_at = None
            msg.provider_supports_idempotency = False

        msg.error_message = None
        msg.delivery_uncertain_at = None
        msg.lease_token = None
        msg.lease_expires_at = None
        actor_label = await resolve_human_actor_label(self.db, actor_user_id)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type="scheduled_message_reconciled",
                entity_type="scheduled_message",
                entity_id=msg.id,
                summary=(
                    "Mensagem reconciliada como entregue."
                    if outcome == "delivered"
                    else "Mensagem reconciliada como não entregue; nova tentativa autorizada."
                ),
                correlation_id=f"scheduled-message:{msg.id}",
                before_data=before_data,
                after_data={
                    "status": msg.status,
                    "outcome": outcome,
                    "providerReference": provider_reference,
                    "retryAt": msg.scheduled_at.isoformat(),
                    "note": note,
                },
            )
        )
        should_publish = outcome == "not_delivered" and msg.scheduled_at <= now
        await self.db.commit()
        await self.db.refresh(msg)

        if should_publish:
            from app.workers.flow_tasks import process_scheduled_messages

            try:
                process_scheduled_messages.delay()
            except Exception:
                logger.warning(
                    "scheduled_message.reconciliation_dispatch_deferred",
                    message_id=str(msg.id),
                )
        return msg

    async def cancel_scheduled_message(self, msg_id: UUID, workspace_id: UUID) -> bool:
        result = await self.db.execute(
            select(ScheduledMessage)
            .where(
                ScheduledMessage.id == msg_id,
                ScheduledMessage.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        msg = result.scalar_one_or_none()
        if not msg:
            return False
        if msg.status == "delivery_uncertain" or (
            msg.status == "processing" and msg.delivery_attempted_at is not None
        ):
            raise InvalidFlowRunTransition(
                "Entrega já iniciada ou incerta exige reconciliação antes do cancelamento"
            )
        if msg.status not in {"pending", "processing"}:
            return False
        msg.status = "cancelled"
        msg.lease_token = None
        msg.lease_expires_at = None
        await self.db.commit()
        return True
