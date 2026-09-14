from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.base import AICompletionResult
from app.models.agent_platform import HumanDecision, HumanNotificationDelivery
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.client import Client
from app.models.flow import (
    Flow,
    FlowRevision,
    FlowRun,
    ScheduledMessage,
    build_flow_revision_snapshot,
    flow_revision_snapshot_hash,
)
from app.models.human_override import HumanOverrideDecision
from app.models.matter import Matter
from app.models.whatsapp import (
    WaMessageDirection,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.whatsapp_campaign import MetaTemplateStatus, WhatsAppMetaTemplate
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.flow import FlowUpdate
from app.services.agent_runtime_service import AgentRuntimeService
from app.services.flow_node_registry import FLOW_NODE_SPEC_BY_TYPE
from app.services.flow_service import (
    FLOW_ACTIVE_AGENT_RUN_KEY,
    FLOW_AGENT_EXECUTIONS_KEY,
    FlowAgentSettlement,
    FlowService,
    InvalidFlowRunTransition,
)
from app.services.whatsapp.errors import WhatsAppProviderError
from app.workers import event_bus, flow_tasks
from tests.agent_deployment_helpers import seed_verified_agent_deployments


@pytest.fixture(autouse=True)
async def _explicit_agent_deployment(db_session, auth_context, monkeypatch):
    await seed_verified_agent_deployments(
        db_session, auth_context.workspace_id, {"klm_sdr"}, monkeypatch
    )


class InjectedWorkerCrash(BaseException):
    pass


def _inject_one_crash(monkeypatch, point: str) -> None:
    injected = False

    def crash_once(actual_point: str, **details):
        nonlocal injected
        if actual_point == point and not injected:
            injected = True
            raise InjectedWorkerCrash(point)

    monkeypatch.setattr(flow_tasks, "_inject_fault", crash_once)


class _FakeGateway:
    async def complete(self, messages, **kwargs):
        return AICompletionResult(
            text='{"decision":"ok"}', input_tokens=11, output_tokens=7, cost_usd=0.03
        )


class _FailingGateway:
    async def complete(self, messages, **kwargs):
        raise RuntimeError("gateway unavailable")


async def _expire_active_flow_intent(db: AsyncSession, run: FlowRun) -> str:
    await db.refresh(run)
    context = dict(run.context or {})
    intent_key = str(context[flow_tasks.FLOW_ACTIVE_EXTERNAL_INTENT_KEY])
    intents = flow_tasks._flow_external_intents(context)
    record = dict(intents[intent_key])
    record["attemptLeaseExpiresAt"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    intents[intent_key] = record
    context[flow_tasks.FLOW_EXTERNAL_INTENTS_KEY] = intents
    run.context = context
    await db.commit()
    return intent_key


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def _create_matter(db: AsyncSession, workspace_id, suffix: str) -> Matter:
    client = Client(
        workspace_id=workspace_id,
        name=f"Cliente {suffix}",
        cpf=uuid4().hex[:11],
        phone="+5511999999999",
    )
    db.add(client)
    await db.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        demand_type=f"Demanda {suffix}",
    )
    db.add(matter)
    await db.flush()
    return matter


async def _create_due_scheduled_message(
    db: AsyncSession,
    workspace_id,
    *,
    suffix: str,
    body: str = "Enviar",
) -> ScheduledMessage:
    matter = await _create_matter(db, workspace_id, suffix)
    message = ScheduledMessage(
        matter_id=matter.id,
        workspace_id=workspace_id,
        contact_phone="+5511999999999",
        message=body,
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        status="pending",
    )
    db.add(message)
    await db.commit()
    return message


@pytest.mark.asyncio
async def test_standalone_approved_template_dispatches_without_flow_snapshot(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    message = await _create_due_scheduled_message(
        db_session, auth_context.workspace_id, suffix="standalone-approved"
    )
    connection = WhatsAppConnection(workspace_id=auth_context.workspace_id)
    db_session.add(connection)
    await db_session.flush()
    template = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        name="follow_up",
        language="pt_BR",
        status=MetaTemplateStatus.approved,
    )
    db_session.add(template)
    await db_session.flush()
    message.template_id = template.id
    message.template_name = template.name
    message.template_language = template.language
    message.template_status = "approved"
    message.template_bindings = []
    await db_session.commit()
    calls = []

    async def dispatch(*args, **kwargs):
        calls.append(kwargs)
        await kwargs["before_dispatch"](False)
        return flow_tasks.ExternalDeliveryReceipt("wamid.standalone", False)

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(flow_tasks, "_dispatch_whatsapp_template_with_protocol", dispatch)
    await flow_tasks._async_process_scheduled_messages(
        session_factory=_session_factory(db_session)
    )
    await db_session.refresh(message)
    assert calls and message.status == "sent"


@pytest.mark.asyncio
async def test_standalone_revoked_template_is_held_not_sent(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    message = await _create_due_scheduled_message(
        db_session, auth_context.workspace_id, suffix="standalone-revoked"
    )
    connection = WhatsAppConnection(workspace_id=auth_context.workspace_id)
    db_session.add(connection)
    await db_session.flush()
    template = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        name="follow_up_revoked",
        language="pt_BR",
        status=MetaTemplateStatus.rejected,
    )
    db_session.add(template)
    await db_session.flush()
    message.template_id = template.id
    message.template_name = template.name
    message.template_language = template.language
    message.template_status = "approved"
    await db_session.commit()
    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        flow_tasks,
        "_dispatch_whatsapp_template_with_protocol",
        lambda *args, **kwargs: pytest.fail("revoked template must not dispatch"),
    )
    await flow_tasks._async_process_scheduled_messages(
        session_factory=_session_factory(db_session)
    )
    await db_session.refresh(message)
    assert message.status == "pending"
    assert message.pending_grace_until is not None


async def _seed_open_whatsapp_window(
    db: AsyncSession,
    workspace_id,
    phone: str,
) -> None:
    conn = WhatsAppConnection(workspace_id=workspace_id)
    db.add(conn)
    await db.flush()
    contact = WhatsAppContact(
        workspace_id=workspace_id,
        whatsapp_connection_id=conn.id,
        phone=phone,
    )
    db.add(contact)
    await db.flush()
    db.add(
        WhatsAppMessage(
            contact_id=contact.id,
            whatsapp_connection_id=conn.id,
            direction=WaMessageDirection.inbound,
            body="oi",
            timestamp=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    await db.flush()


async def _create_flow_run(
    db: AsyncSession,
    workspace_id,
    *,
    suffix: str,
    flow_active: bool = True,
    run_status: str = "active",
    node_type: str = "send_message",
    node_config: dict | None = None,
    trigger_config: dict | None = None,
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
    current_node_id: str = "node-1",
    seed_open_window: bool = True,
    window_phone: str = "+5511999999999",
    created_by_user_id=None,
) -> tuple[Flow, FlowRun, Matter]:
    matter = await _create_matter(db, workspace_id, suffix)
    if seed_open_window:
        # Estado realista de produção: o nó de texto livre segue um inbound
        # recente, então a janela de 24h da Meta (MetaPolicyGate) está aberta.
        await _seed_open_whatsapp_window(db, workspace_id, window_phone)
    node = {
        "id": "node-1",
        "type": node_type,
        "config": (
            node_config
            if node_config is not None
            else ({"message": "Olá {nome}"} if node_type == "send_message" else {})
        ),
    }
    flow = Flow(
        workspace_id=workspace_id,
        name=f"Flow {suffix}",
        trigger="manual",
        trigger_config=trigger_config or {},
        nodes=nodes if nodes is not None else [node],
        edges=edges if edges is not None else [],
        is_active=flow_active,
    )
    db.add(flow)
    await db.flush()
    revision_id = uuid4()
    snapshot = build_flow_revision_snapshot(
        flow,
        revision_id=revision_id,
        revision_number=1,
    )
    revision = FlowRevision(
        id=revision_id,
        flow_id=flow.id,
        revision_number=1,
        snapshot=snapshot,
        snapshot_hash=flow_revision_snapshot_hash(snapshot),
        source="test_fixture",
        created_by_user_id=created_by_user_id,
    )
    db.add(revision)
    await db.flush()
    flow.current_revision = 1
    flow.current_revision_id = revision.id
    await db.flush()
    authored_nodes = nodes if nodes is not None else [node]
    required_permissions = sorted(
        {
            str(permission)
            for authored_node in authored_nodes
            if isinstance(authored_node, dict)
            for permission in [
                (FLOW_NODE_SPEC_BY_TYPE.get(str(authored_node.get("type") or "")) or {}).get(
                    "required_permission"
                )
            ]
            if permission
        }
    )
    run = FlowRun(
        flow_id=flow.id,
        revision_id=revision.id,
        matter_id=matter.id,
        workspace_id=workspace_id,
        status=run_status,
        current_node_id=current_node_id,
        context={
            "nome": "Ana",
            "phone": "+5511999999999",
            "permissions": required_permissions,
            "authorizationSource": "test_authorized_fixture",
        },
    )
    db.add(run)
    await db.flush()
    return flow, run, matter


@pytest.mark.asyncio
async def test_worker_intelligence_gateway_persists_accounting_and_trace(
    db_session: AsyncSession, auth_context, monkeypatch
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="worker-intelligence",
        node_type="summarize",
        seed_open_window=False,
    )
    await db_session.commit()
    monkeypatch.setattr("app.ai.factory.get_model_gateway", lambda: _FakeGateway())
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    assert run.status == "completed", run.context.get("flowErrorHistory")
    assert run.context["trace"][-1]["status"] == "completed"
    agent_runs = (
        await db_session.scalars(
            select(AgentRun).where(AgentRun.workspace_id == auth_context.workspace_id)
        )
    ).all()
    assert agent_runs, "worker did not persist AgentRun"
    assert agent_runs[0].input_tokens == 11
    assert agent_runs[0].output_tokens == 7
    assert float(agent_runs[0].cost_usd) == 0.03


@pytest.mark.asyncio
@pytest.mark.parametrize("gateway", [None, _FailingGateway()])
async def test_worker_intelligence_failure_creates_human_decision_before_wait(
    db_session: AsyncSession, auth_context, monkeypatch, gateway
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix=f"worker-human-{uuid4().hex[:6]}",
        node_type="summarize",
        seed_open_window=False,
    )
    await db_session.commit()
    monkeypatch.setattr("app.ai.factory.get_model_gateway", lambda: gateway)
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    assert run.status == "waiting_human"
    decisions = (
        await db_session.scalars(select(HumanDecision).where(HumanDecision.flow_run_id == run.id))
    ).all()
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.assigned_user_id is not None
    assert decision.owner_user_id == auth_context.user_id
    deliveries = (
        await db_session.scalars(
            select(HumanNotificationDelivery).where(
                HumanNotificationDelivery.decision_id == decision.id
            )
        )
    ).all()
    assert {delivery.channel for delivery in deliveries} >= {"in_app", "phone"}


@pytest.mark.asyncio
async def test_human_handoff_is_idempotent_for_same_flow_checkpoint(
    db_session: AsyncSession, auth_context
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix=f"worker-human-dedupe-{uuid4().hex[:6]}",
        node_type="summarize",
        seed_open_window=False,
    )
    first = await flow_tasks._call_human_before_waiting(
        db_session,
        run,
        node_id="node-1",
        execution_step=1,
        reason="provider unavailable",
        purpose="agent_tool_wait",
    )
    second = await flow_tasks._call_human_before_waiting(
        db_session,
        run,
        node_id="node-1",
        execution_step=1,
        reason="provider unavailable",
        purpose="agent_tool_wait",
    )
    assert first.id == second.id
    decisions = (
        await db_session.scalars(select(HumanDecision).where(HumanDecision.flow_run_id == run.id))
    ).all()
    assert len(decisions) == 1


@pytest.mark.asyncio
async def test_flow_node_retry_is_durable_bounded_and_then_fails(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="node-retry-policy",
        node_type="set_variable",
        node_config={
            "variable_name": "proposal.amount",
            "value_expression": "not-a-number",
            "value_type": "number",
            "error_strategy": "retry",
            "max_retries": 2,
            "retry_delay_seconds": 0,
        },
    )
    await db_session.commit()
    published: list[list[str]] = []
    monkeypatch.setattr(
        flow_tasks.execute_flow_run_step,
        "apply_async",
        lambda *args, **kwargs: published.append(kwargs["args"]),
    )

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    first_token = published[0][2]
    assert run.status == "active"
    assert run.context[flow_tasks.FLOW_NODE_RETRIES_KEY]["node-1"] == 1
    assert run.context["trace"][0]["status"] == "retrying"

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        continuation_token=first_token,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    second_token = published[1][2]
    assert run.status == "active"
    assert run.context[flow_tasks.FLOW_NODE_RETRIES_KEY]["node-1"] == 2
    assert second_token != first_token

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        continuation_token=second_token,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    assert len(published) == 2
    assert run.status == "failed"
    assert run.context["lastFlowError"]["strategy"] == "fail"
    assert len(run.context[flow_tasks.FLOW_ERROR_HISTORY_KEY]) == 3
    assert all(
        record.get("consumedAt")
        for record in run.context[flow_tasks.FLOW_STEP_DISPATCHES_KEY].values()
    )


@pytest.mark.asyncio
async def test_flow_node_error_route_advances_to_native_handler(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    nodes = [
        {
            "id": "node-1",
            "type": "set_variable",
            "config": {
                "variable_name": "proposal.amount",
                "value_expression": "invalid",
                "value_type": "number",
                "error_strategy": "route",
            },
        },
        {
            "id": "error-1",
            "type": "error_handler",
            "config": {"clear_error": True},
        },
    ]
    edges = [
        {
            "id": "node-1-error-1",
            "source": "node-1",
            "target": "error-1",
            "route_key": "error",
        }
    ]
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="node-error-route",
        nodes=nodes,
        edges=edges,
    )
    await db_session.commit()
    published: list[list[str]] = []
    monkeypatch.setattr(
        flow_tasks.execute_flow_run_step,
        "apply_async",
        lambda *args, **kwargs: published.append(kwargs["args"]),
    )

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    token = published[0][2]
    assert run.status == "active"
    assert run.current_node_id == "error-1"
    assert run.context["lastFlowError"]["status"] == "routed"
    assert run.context["trace"][0]["decision"]["errorRoute"] == "error-1"

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        continuation_token=token,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    assert run.status == "completed"
    assert "lastFlowError" not in run.context
    assert run.context["lastFlowErrorHandled"]["nodeId"] == "node-1"


@pytest.mark.asyncio
async def test_flow_node_error_can_pause_for_human_review(
    db_session: AsyncSession,
    auth_context,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="node-wait-human",
        node_type="set_variable",
        node_config={
            "variable_name": "proposal.amount",
            "value_expression": "invalid",
            "value_type": "number",
            "error_strategy": "wait_human",
        },
    )
    await db_session.commit()

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    assert run.status == "waiting_human"
    assert run.paused_at is not None
    assert run.context["lastFlowError"]["strategy"] == "wait_human"
    assert run.context["trace"][0]["status"] == "waiting_human"


@pytest.mark.asyncio
async def test_external_node_never_auto_retries_an_ambiguous_failure(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="external-no-blind-retry",
        node_type="scheduled_message",
        node_config={
            "message": "Lembrete",
            "scheduled_at": "invalid",
            "error_strategy": "retry",
            "max_retries": 3,
        },
    )
    await db_session.commit()
    published: list[list[str]] = []
    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        flow_tasks.execute_flow_run_step,
        "apply_async",
        lambda *args, **kwargs: published.append(kwargs["args"]),
    )

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    assert published == []
    assert run.status == "waiting_human"
    assert run.context["lastFlowError"]["requestedStrategy"] == "retry"
    assert run.context["lastFlowError"]["automaticRetryBlocked"] is True


async def _create_linked_agent_flow_run(
    db: AsyncSession,
    workspace_id,
    *,
    suffix: str,
    agent_status: AgentRunStatus,
    agent_error: str | None = None,
    post_agent_nodes: list[dict] | None = None,
) -> tuple[Flow, FlowRun, Matter, AgentRun]:
    matter = await _create_matter(db, workspace_id, suffix)
    continuation_nodes = post_agent_nodes or [
        {"id": "next-1", "type": "human_pause", "config": {"reason": "Revisar"}}
    ]
    chained_node_ids = ["agent-1", *(str(node["id"]) for node in continuation_nodes)]
    flow = Flow(
        workspace_id=workspace_id,
        name=f"Flow agent {suffix}",
        trigger="manual",
        trigger_config={},
        nodes=[
            {
                "id": "agent-1",
                "type": "agent",
                "config": {"agent": "klm_sdr", "stage_key": "entrada"},
            },
            *continuation_nodes,
        ],
        edges=[
            {
                "id": f"{source}-{target}",
                "source": source,
                "target": target,
            }
            for source, target in zip(chained_node_ids, chained_node_ids[1:])
        ],
        is_active=True,
    )
    db.add(flow)
    await db.flush()
    revision_id = uuid4()
    snapshot = build_flow_revision_snapshot(
        flow,
        revision_id=revision_id,
        revision_number=1,
    )
    revision = FlowRevision(
        id=revision_id,
        flow_id=flow.id,
        revision_number=1,
        snapshot=snapshot,
        snapshot_hash=flow_revision_snapshot_hash(snapshot),
        source="test_fixture",
    )
    db.add(revision)
    await db.flush()
    flow.current_revision = 1
    flow.current_revision_id = revision.id
    run = FlowRun(
        flow_id=flow.id,
        revision_id=revision.id,
        matter_id=matter.id,
        workspace_id=workspace_id,
        status="waiting_human",
        current_node_id="agent-1",
        context={"flowStepCount": 1},
        paused_at=datetime.now(UTC),
    )
    db.add(run)
    await db.flush()
    agent_run = AgentRun(
        workspace_id=workspace_id,
        matter_id=matter.id,
        agent_name="klm_sdr",
        trigger_source=f"flow:{flow.id}",
        dedupe_key=f"flow-agent:{run.id}:{revision.id}:agent-1:1",
        status=agent_status,
        input_payload={
            "flow_run_id": str(run.id),
            "flow_revision_id": str(revision.id),
            "flow_revision": 1,
            "flow_node_id": "agent-1",
            "flow_step": 1,
            "stage_key": "entrada",
        },
        output_payload={"summary": "Concluído"}
        if agent_status == AgentRunStatus.completed
        else None,
        error_message=agent_error,
        attempt_count=1,
        completed_at=datetime.now(UTC)
        if agent_status
        in {
            AgentRunStatus.completed,
            AgentRunStatus.failed,
            AgentRunStatus.waiting_human,
            AgentRunStatus.cancelled,
        }
        else None,
    )
    db.add(agent_run)
    await db.flush()
    checkpoint = {
        "flowRunId": str(run.id),
        "revisionId": str(revision.id),
        "revision": 1,
        "nodeId": "agent-1",
        "step": 1,
        "agentRunId": str(agent_run.id),
        "agentName": "klm_sdr",
        "agentRunStatus": agent_status.value,
        "state": agent_status.value,
        "linkedAt": datetime.now(UTC).isoformat(),
    }
    run.context = {
        "flowStepCount": 1,
        FLOW_ACTIVE_AGENT_RUN_KEY: str(agent_run.id),
        FLOW_AGENT_EXECUTIONS_KEY: {str(agent_run.id): checkpoint},
    }
    await db.commit()
    return flow, run, matter, agent_run


@pytest.mark.parametrize(
    ("value", "operator", "target", "field_exists", "expected"),
    [
        (7, "gte", "7", True, True),
        (6.99, "gte", "7", True, False),
        (7, "lte", "7", True, True),
        (8, "lte", "7", True, False),
        (None, "exists", "", True, True),
        (None, "exists", "", False, False),
        ("  ", "not_empty", "", True, False),
        ([], "not_empty", "", True, False),
        (0, "not_empty", "", True, True),
        ("conteúdo", "not_empty", "", True, True),
    ],
)
def test_evaluate_condition_supports_hardened_operators(
    value,
    operator: str,
    target,
    field_exists: bool,
    expected: bool,
):
    assert (
        flow_tasks._evaluate_condition(
            value,
            operator,
            target,
            field_exists=field_exists,
        )
        is expected
    )


@pytest.mark.asyncio
async def test_send_whatsapp_fails_closed_before_constructing_provider(monkeypatch):
    constructed = False

    class ForbiddenWhatsAppService:
        def __init__(self, db):
            nonlocal constructed
            constructed = True

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: False)
    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService",
        ForbiddenWhatsAppService,
    )

    with pytest.raises(flow_tasks.ExternalDeliveryBlockedError):
        await flow_tasks._send_whatsapp(
            uuid4(),
            "+5511999999999",
            "Bloquear",
            object(),
        )

    assert constructed is False


@pytest.mark.asyncio
async def test_send_whatsapp_propagates_provider_failure(monkeypatch):
    connection = object()
    marked_errors: list[tuple[str, str]] = []
    provider_error = WhatsAppProviderError(
        code="WA-TEST",
        message="provider unavailable",
    )

    class FakeStrategy:
        async def send_text(self, selected_connection, phone, message):
            assert selected_connection is connection
            raise provider_error

    class FakeStrategyFactory:
        def build(self, selected_connection):
            assert selected_connection is connection
            return FakeStrategy()

    class FakeConnectionService:
        async def mark_connection_error(self, selected_connection, code, message):
            assert selected_connection is connection
            marked_errors.append((code, message))

    class FakeMessageService:
        strategy_factory = FakeStrategyFactory()
        connection_service = FakeConnectionService()

        async def _resolve_outbound_connection(self, **kwargs):
            return connection

    class FakeWhatsAppService:
        def __init__(self, db):
            self.message_service = FakeMessageService()

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService",
        FakeWhatsAppService,
    )

    with pytest.raises(WhatsAppProviderError) as caught:
        await flow_tasks._send_whatsapp(
            uuid4(),
            "+5511999999999",
            "Falhar",
            object(),
        )

    assert caught.value is provider_error
    assert marked_errors == [("WA-TEST", "provider unavailable")]


@pytest.mark.asyncio
@pytest.mark.parametrize("guarantees_exactly_once", [True, False])
async def test_send_whatsapp_separates_key_support_from_retry_guarantee(
    monkeypatch,
    guarantees_exactly_once: bool,
):
    connection = object()
    received_keys: list[str] = []
    support_checkpoints: list[bool] = []

    class FakeStrategy:
        supports_idempotency = guarantees_exactly_once

        async def send_text(
            self,
            selected_connection,
            phone,
            message,
            *,
            idempotency_key,
        ):
            assert selected_connection is connection
            received_keys.append(idempotency_key)
            return SimpleNamespace(wamid="wamid-keyed")

    class FakeMessageService:
        connection_service = SimpleNamespace()
        strategy_factory = SimpleNamespace(build=lambda selected: FakeStrategy())

        async def _resolve_outbound_connection(self, **kwargs):
            return connection

    class FakeWhatsAppService:
        def __init__(self, db):
            self.message_service = FakeMessageService()

    async def before_dispatch(supports_idempotency: bool):
        support_checkpoints.append(supports_idempotency)

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService",
        FakeWhatsAppService,
    )

    receipt = await flow_tasks._send_whatsapp(
        uuid4(),
        "+5511999999999",
        "Enviar uma vez",
        object(),
        idempotency_key="flow-effect:keyed",
        before_dispatch=before_dispatch,
    )

    assert support_checkpoints == [guarantees_exactly_once]
    assert received_keys == ["flow-effect:keyed"]
    assert receipt.provider_reference == "wamid-keyed"
    assert receipt.provider_supports_idempotency is guarantees_exactly_once


@pytest.mark.asyncio
async def test_blocked_scheduled_message_is_failed_and_never_marked_sent(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "agendada-bloqueada")
    message = ScheduledMessage(
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511999999999",
        message="Não enviar",
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        status="pending",
    )
    db_session.add(message)
    await db_session.commit()
    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: False)

    await flow_tasks._async_process_scheduled_messages(session_factory=_session_factory(db_session))

    await db_session.refresh(message)
    assert message.status == "failed"
    assert message.sent_at is None
    assert "bloqueado" in (message.error_message or "")


@pytest.mark.asyncio
async def test_provider_failure_never_marks_scheduled_message_sent(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "agendada-falha")
    message = ScheduledMessage(
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511999999999",
        message="Falhar",
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        status="pending",
    )
    db_session.add(message)
    await db_session.commit()

    async def fail_send(*args, **kwargs):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(flow_tasks, "_send_whatsapp", fail_send)

    await flow_tasks._async_process_scheduled_messages(session_factory=_session_factory(db_session))

    await db_session.refresh(message)
    assert message.status == "delivery_uncertain"
    assert message.sent_at is None
    assert message.delivery_uncertain_at is not None
    assert message.delivery_attempt_count == 1
    assert "reconciliação humana" in (message.error_message or "")


@pytest.mark.asyncio
async def test_scheduled_message_is_marked_sent_only_after_success(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    matter = await _create_matter(db_session, auth_context.workspace_id, "agendada-sucesso")
    message = ScheduledMessage(
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511999999999",
        message="Enviar",
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        status="pending",
    )
    db_session.add(message)
    await db_session.commit()
    sent: list[str] = []

    async def successful_send(workspace_id, phone, body, db):
        sent.append(body)

    monkeypatch.setattr(flow_tasks, "_send_whatsapp", successful_send)

    await flow_tasks._async_process_scheduled_messages(session_factory=_session_factory(db_session))

    await db_session.refresh(message)
    assert sent == ["Enviar"]
    assert message.status == "sent"
    assert message.sent_at is not None
    assert message.error_message is None


@pytest.mark.asyncio
async def test_inactive_flow_run_is_failed_without_executing_node(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="inativo-worker",
        flow_active=False,
    )
    await db_session.commit()
    sends: list[str] = []

    async def record_send(*args, **kwargs):
        sends.append("sent")

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert sends == []
    assert run.status == "failed"
    assert run.error_message == "Flow inativo não pode ser executado"


@pytest.mark.asyncio
async def test_closed_channel_pauses_active_run_before_external_node(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="gate-worker",
    )
    await db_session.commit()
    sends: list[str] = []

    async def record_send(*args, **kwargs):
        sends.append("sent")

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: False)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert sends == []
    assert run.status == "waiting_human"
    assert run.error_message is None
    assert "aguardando aprovação" in (run.pause_reason or "")
    assert run.context["externalActionsExecuted"] == 0
    assert run.context["flowStepCount"] == 0
    assert run.context["blockedExternalAction"]["nodeId"] == "node-1"
    assert run.context["blockedExternalAction"]["nodeType"] == "send_message"
    assert run.context["blockedExternalAction"]["blockedAt"]


@pytest.mark.asyncio
async def test_closed_24h_window_pauses_active_run_before_free_text_send(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    # Canal externo aberto, mas SEM inbound recente: a janela de 24h da Meta
    # está fechada, então o nó de texto livre não pode disparar (M1).
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="janela-fechada",
        seed_open_window=False,
    )
    await db_session.commit()
    sends: list[str] = []

    async def record_send(*args, **kwargs):
        sends.append("sent")

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert sends == []
    assert run.status == "waiting_human"
    assert run.error_message is None
    assert "janela de 24h" in (run.pause_reason or "")
    assert run.context["externalActionsExecuted"] == 0
    assert run.context["blockedExternalAction"]["nodeType"] == "send_message"
    assert run.context["blockedExternalAction"]["requires"]


@pytest.mark.asyncio
async def test_agent_node_persists_revision_pinned_checkpoint_with_stable_dedupe(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, matter = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="agent-checkpoint",
        node_type="agent",
    )
    await db_session.commit()
    agent_run = AgentRun(
        id=uuid4(),
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        agent_name="klm_sdr",
        trigger_source=f"flow:{run.flow_id}",
        status=AgentRunStatus.queued,
        input_payload={},
        attempt_count=1,
    )
    captured: list[dict] = []

    async def fake_enqueue(**kwargs):
        captured.append(kwargs)
        return agent_run

    async def no_settlement(self, agent_run_id, workspace_id):
        return FlowAgentSettlement(
            outcome="pending",
            flow_run_id=run.id,
        )

    monkeypatch.setattr(
        "app.workers.event_bus._enqueue_agent_run",
        fake_enqueue,
    )
    monkeypatch.setattr(FlowService, "settle_agent_run", no_settlement)
    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: False)

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert len(captured) == 1
    dispatch = captured[0]
    assert dispatch["dedupe_key"] == (f"flow-agent:{run.id}:{run.revision_id}:node-1:1")
    assert dispatch["input_payload"]["flow_run_id"] == str(run.id)
    assert dispatch["input_payload"]["flow_revision_id"] == str(run.revision_id)
    assert dispatch["input_payload"]["flow_node_id"] == "node-1"
    assert dispatch["input_payload"]["flow_step"] == 1
    # Enqueuing an agent is active machine work. A flow may only enter
    # waiting_human after a durable HumanDecision has actually been created.
    assert run.status == "active"
    assert run.current_node_id == "node-1"
    assert run.context[FLOW_ACTIVE_AGENT_RUN_KEY] == str(agent_run.id)
    checkpoint = run.context[FLOW_AGENT_EXECUTIONS_KEY][str(agent_run.id)]
    assert checkpoint["revisionId"] == str(run.revision_id)
    assert checkpoint["nodeId"] == "node-1"
    assert checkpoint["step"] == 1


@pytest.mark.asyncio
async def test_completed_agent_run_advances_flow_automatically(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _, agent_run = await _create_linked_agent_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="agent-success",
        agent_status=AgentRunStatus.completed,
    )
    queued: list[list[str]] = []
    monkeypatch.setattr(
        flow_tasks.execute_flow_run_step,
        "apply_async",
        lambda *args, **kwargs: queued.append(kwargs["args"]),
    )

    settlement = await flow_tasks._async_settle_flow_agent_run(
        str(agent_run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert settlement.outcome == "advanced"
    assert run.status == "active"
    assert run.current_node_id == "next-1"
    assert run.revision_id == UUID(
        run.context[FLOW_AGENT_EXECUTIONS_KEY][str(agent_run.id)]["revisionId"]
    )
    assert FLOW_ACTIVE_AGENT_RUN_KEY not in run.context
    assert run.context[FLOW_AGENT_EXECUTIONS_KEY][str(agent_run.id)]["state"] == "advanced"
    assert queued == [
        [
            str(run.id),
            str(auth_context.workspace_id),
            f"flow-agent-continuation:{agent_run.id}",
        ]
    ]


@pytest.mark.asyncio
async def test_failed_agent_run_remains_explicit_and_reviewable_without_advancing(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _, agent_run = await _create_linked_agent_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="agent-failure",
        agent_status=AgentRunStatus.failed,
        agent_error="modelo indisponível",
    )
    queued: list[list[str]] = []
    monkeypatch.setattr(
        flow_tasks.execute_flow_run_step,
        "apply_async",
        lambda *args, **kwargs: queued.append(kwargs["args"]),
    )

    settlement = await flow_tasks._async_settle_flow_agent_run(
        str(agent_run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    checkpoint = run.context[FLOW_AGENT_EXECUTIONS_KEY][str(agent_run.id)]
    assert settlement.outcome == "failed"
    assert run.status == "waiting_human"
    assert run.current_node_id == "agent-1"
    assert run.error_message == "modelo indisponível"
    assert "revisão necessária" in (run.pause_reason or "")
    assert run.context[FLOW_ACTIVE_AGENT_RUN_KEY] == str(agent_run.id)
    assert checkpoint["state"] == "failed"
    assert checkpoint["agentRunStatus"] == "failed"
    assert checkpoint["terminalAt"]
    decisions = list(
        (
            await db_session.scalars(
                select(HumanDecision).where(HumanDecision.flow_run_id == run.id)
            )
        ).all()
    )
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.agent_run_id == agent_run.id
    assert decision.assigned_user_id == auth_context.user_id
    assert decision.owner_user_id == auth_context.user_id
    assert decision.due_at is not None
    deliveries = list(
        (
            await db_session.scalars(
                select(HumanNotificationDelivery).where(
                    HumanNotificationDelivery.decision_id == decision.id
                )
            )
        ).all()
    )
    assert {delivery.channel for delivery in deliveries} == {"in_app", "phone"}
    assert queued == []


@pytest.mark.asyncio
async def test_waiting_agent_run_creates_canonical_human_call(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _, agent_run = await _create_linked_agent_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="agent-waiting-human",
        agent_status=AgentRunStatus.waiting_human,
        agent_error="Revisão jurídica necessária",
    )
    monkeypatch.setattr(
        flow_tasks.execute_flow_run_step,
        "apply_async",
        lambda *args, **kwargs: None,
    )

    settlement = await flow_tasks._async_settle_flow_agent_run(
        str(agent_run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert settlement.outcome == "waiting_human"
    assert run.status == "waiting_human"
    checkpoint = run.context["humanCheckpoints"]["agent-1"]
    decision = await db_session.get(HumanDecision, UUID(checkpoint["decisionId"]))
    assert decision is not None
    assert decision.agent_run_id == agent_run.id
    assert decision.assigned_user_id == auth_context.user_id
    assert decision.owner_user_id == auth_context.user_id
    assert decision.due_at is not None
    deliveries = list(
        (
            await db_session.scalars(
                select(HumanNotificationDelivery).where(
                    HumanNotificationDelivery.decision_id == decision.id
                )
            )
        ).all()
    )
    assert {delivery.channel for delivery in deliveries} == {"in_app", "phone"}


@pytest.mark.asyncio
async def test_running_agent_keeps_flow_active_without_human_call(
    db_session: AsyncSession,
    auth_context,
):
    _, run, _, agent_run = await _create_linked_agent_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="agent-polling",
        agent_status=AgentRunStatus.running,
    )

    settlement = await flow_tasks._async_settle_flow_agent_run(
        str(agent_run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert settlement.outcome == "pending"
    assert run.status == "active"
    assert run.current_node_id == "agent-1"
    assert run.context.get("humanCheckpoints") is None
    assert (
        await db_session.scalars(select(HumanDecision).where(HumanDecision.flow_run_id == run.id))
    ).all() == []


def test_pending_agent_settlement_reenqueues_polling(monkeypatch):
    class RetryObservedError(Exception):
        pass

    retry_kwargs = None

    def retry(**kwargs):
        nonlocal retry_kwargs
        retry_kwargs = kwargs
        raise RetryObservedError

    def pending_result(coro):
        coro.close()
        return FlowAgentSettlement(outcome="pending", flow_run_id=uuid4())

    monkeypatch.setattr(flow_tasks, "_run_async", pending_result)
    monkeypatch.setattr(flow_tasks.settle_flow_agent_run, "retry", retry)
    with pytest.raises(RetryObservedError):
        flow_tasks.settle_flow_agent_run._orig_run(  # type: ignore[attr-defined]
            str(uuid4()),
            str(uuid4()),
        )
    assert retry_kwargs == {"countdown": 2}


@pytest.mark.asyncio
async def test_manual_resume_rejects_nonterminal_agent_then_accepts_success(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _, agent_run = await _create_linked_agent_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="agent-premature-resume",
        agent_status=AgentRunStatus.running,
    )
    queued: list[list[str]] = []
    monkeypatch.setattr(
        "app.services.flow_service.automation_worker_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        flow_tasks.execute_flow_run_step,
        "apply_async",
        lambda *args, **kwargs: queued.append(kwargs["args"]),
    )
    service = FlowService(db_session)

    with pytest.raises(InvalidFlowRunTransition, match="status atual: running"):
        await service.resume_flow_run(run.id, auth_context.workspace_id)

    await db_session.refresh(run)
    assert run.status == "waiting_human"
    assert run.current_node_id == "agent-1"
    assert queued == []

    agent_run.status = AgentRunStatus.completed
    agent_run.completed_at = datetime.now(UTC)
    agent_run.output_payload = {"summary": "Concluído após retry"}
    await db_session.commit()

    resumed = await service.resume_flow_run(run.id, auth_context.workspace_id)

    assert resumed is not None
    assert resumed.status == "active"
    assert resumed.current_node_id == "next-1"
    assert queued == [
        [
            str(run.id),
            str(auth_context.workspace_id),
            f"flow-agent-continuation:{agent_run.id}",
        ]
    ]


@pytest.mark.asyncio
async def test_agent_completion_redelivery_advances_and_enqueues_exactly_once(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _, agent_run = await _create_linked_agent_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="agent-idempotent",
        agent_status=AgentRunStatus.completed,
    )
    queued: list[list[str]] = []
    monkeypatch.setattr(
        flow_tasks.execute_flow_run_step,
        "apply_async",
        lambda *args, **kwargs: queued.append(kwargs["args"]),
    )

    first = await flow_tasks._async_settle_flow_agent_run(
        str(agent_run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    second = await flow_tasks._async_settle_flow_agent_run(
        str(agent_run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    checkpoint = run.context[FLOW_AGENT_EXECUTIONS_KEY][str(agent_run.id)]
    assert first.outcome == "advanced"
    assert second.outcome == "already_settled"
    assert run.status == "active"
    assert run.current_node_id == "next-1"
    assert run.context["flowStepCount"] == 1
    assert checkpoint["state"] == "advanced"
    assert len(queued) == 1
    assert queued[0][:2] == [str(run.id), str(auth_context.workspace_id)]
    assert queued[0][2] == f"flow-agent-continuation:{agent_run.id}"


@pytest.mark.asyncio
async def test_agent_continuation_redelivery_recovers_crash_after_commit(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _, agent_run = await _create_linked_agent_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="agent-continuation-crash",
        agent_status=AgentRunStatus.completed,
        post_agent_nodes=[
            {"id": "next-1", "type": "trigger", "config": {}},
            {
                "id": "pause-1",
                "type": "human_pause",
                "config": {"reason": "Revisar após continuação"},
            },
        ],
    )
    initial_dispatches: list[list[str]] = []
    monkeypatch.setattr(
        flow_tasks.execute_flow_run_step,
        "apply_async",
        lambda *args, **kwargs: initial_dispatches.append(kwargs["args"]),
    )
    await flow_tasks._async_settle_flow_agent_run(
        str(agent_run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    continuation_token = initial_dispatches[0][2]

    class SimulatedWorkerCrash(BaseException):
        pass

    def crash_before_publish(*args, **kwargs):
        raise SimulatedWorkerCrash

    monkeypatch.setattr(
        flow_tasks.execute_flow_run_step,
        "apply_async",
        crash_before_publish,
    )

    with pytest.raises(SimulatedWorkerCrash):
        await flow_tasks._async_execute_flow_run_step(
            str(run.id),
            workspace_id=auth_context.workspace_id,
            continuation_token=continuation_token,
            session_factory=_session_factory(db_session),
        )

    await db_session.refresh(run)
    checkpoint = run.context[FLOW_AGENT_EXECUTIONS_KEY][str(agent_run.id)]
    successor_token = checkpoint[flow_tasks.CONTINUATION_SUCCESSOR_TOKEN_KEY]
    successor_record = run.context[flow_tasks.FLOW_STEP_DISPATCHES_KEY][successor_token]
    assert run.status == "active"
    assert run.current_node_id == "pause-1"
    assert checkpoint["continuationConsumedAt"]
    assert successor_record["nodeId"] == "pause-1"
    assert successor_record["state"] == "pending"

    recovered_dispatches: list[list[str]] = []
    monkeypatch.setattr(
        flow_tasks.execute_flow_run_step,
        "apply_async",
        lambda *args, **kwargs: recovered_dispatches.append(kwargs["args"]),
    )
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        continuation_token=continuation_token,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert recovered_dispatches == [[str(run.id), str(auth_context.workspace_id), successor_token]]
    assert run.status == "active"
    assert run.current_node_id == "pause-1"

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        continuation_token=successor_token,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    successor_record = run.context[flow_tasks.FLOW_STEP_DISPATCHES_KEY][successor_token]
    assert run.status == "waiting_human"
    assert run.current_node_id == "pause-1"
    assert successor_record["state"] == "consumed"
    assert successor_record["consumedAt"]


@pytest.mark.asyncio
async def test_recovery_republishes_root_dispatch_persisted_before_broker(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="root-dispatch-recovery",
        node_type="trigger",
    )
    token, record = flow_tasks._record_root_flow_step_dispatch(
        run,
        target_node_id="node-1",
        available_at=datetime.now(UTC),
        source_type="flow_start",
    )
    await db_session.commit()

    published: list[list[str]] = []
    monkeypatch.setattr(
        flow_tasks.execute_flow_run_step,
        "apply_async",
        lambda *args, **kwargs: published.append(kwargs["args"]),
    )

    first_recovery = await flow_tasks._async_recover_step_dispatches(
        session_factory=_session_factory(db_session),
    )
    second_recovery = await flow_tasks._async_recover_step_dispatches(
        session_factory=_session_factory(db_session),
    )

    assert record["state"] == "pending"
    assert first_recovery == 1
    assert second_recovery == 0
    assert published == [[str(run.id), str(auth_context.workspace_id), token]]

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        continuation_token=token,
        session_factory=_session_factory(db_session),
    )
    async with _session_factory(db_session)() as verification_db:
        persisted = await verification_db.get(FlowRun, run.id)
        assert persisted is not None
        assert persisted.status == "completed"
        dispatch = persisted.context[flow_tasks.FLOW_STEP_DISPATCHES_KEY][token]
        assert dispatch["state"] == "consumed"
        assert dispatch["consumedAt"]


@pytest.mark.asyncio
async def test_flow_pollers_with_zero_work_never_enter_idle_tenants(
    db_session: AsyncSession,
    monkeypatch,
):
    db_session.add_all(
        Workspace(
            name=f"Flow idle {index}",
            slug=f"flow-idle-{uuid4().hex}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        for index in range(12)
    )
    await db_session.commit()

    async def forbidden_context(*args, **kwargs):
        raise AssertionError("idle flow tenant was entered")

    monkeypatch.setattr(flow_tasks, "set_workspace_context", forbidden_context)
    factory = _session_factory(db_session)

    assert await flow_tasks._async_recover_step_dispatches(session_factory=factory) == 0
    assert await flow_tasks._async_process_scheduled_messages(session_factory=factory) == 0


@pytest.mark.asyncio
async def test_scheduled_message_total_batch_cap_and_repeated_runs_are_idempotent(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    messages = [
        await _create_due_scheduled_message(
            db_session,
            auth_context.workspace_id,
            suffix=f"fanout-cap-{index}",
            body=f"Mensagem {index}",
        )
        for index in range(3)
    ]
    deliveries: list[str] = []

    async def record_send(workspace_id, phone, body, db):
        deliveries.append(body)

    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)
    factory = _session_factory(db_session)

    first = await flow_tasks._async_process_scheduled_messages(
        session_factory=factory,
        total_batch_size=2,
    )
    second = await flow_tasks._async_process_scheduled_messages(
        session_factory=factory,
        total_batch_size=2,
    )
    third = await flow_tasks._async_process_scheduled_messages(
        session_factory=factory,
        total_batch_size=2,
    )

    for message in messages:
        await db_session.refresh(message)
    assert first == 2
    assert second == 1
    assert third == 0
    assert sorted(deliveries) == ["Mensagem 0", "Mensagem 1", "Mensagem 2"]
    assert all(message.status == "sent" for message in messages)


@pytest.mark.asyncio
async def test_chat_template_copy_can_be_activated_and_started_atomically(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    matter = await _create_matter(
        db_session,
        auth_context.workspace_id,
        "active-template-copy",
    )
    template = Flow(
        workspace_id=None,
        name="Template de follow-up",
        trigger="manual",
        trigger_config={},
        nodes=[{"id": "start", "type": "trigger", "config": {}}],
        edges=[],
        is_template=True,
        is_active=True,
    )
    db_session.add(template)
    await db_session.flush()
    revision_id = uuid4()
    snapshot = build_flow_revision_snapshot(
        template,
        revision_id=revision_id,
        revision_number=1,
    )
    revision = FlowRevision(
        id=revision_id,
        flow_id=template.id,
        revision_number=1,
        snapshot=snapshot,
        snapshot_hash=flow_revision_snapshot_hash(snapshot),
        source="test_fixture",
    )
    db_session.add(revision)
    template.current_revision = 1
    template.current_revision_id = revision.id
    await db_session.commit()

    service = FlowService(db_session)
    copied = await service.apply_template(
        template.id,
        auth_context.workspace_id,
        "Follow-up do atendimento",
        activate=True,
    )

    assert copied.is_active is True
    copied_revision = await db_session.get(FlowRevision, copied.current_revision_id)
    assert copied_revision is not None
    assert copied_revision.snapshot["is_active"] is True

    monkeypatch.setattr("app.services.flow_service.automation_worker_allowed", lambda: False)
    run = await service.start_flow_run(
        copied.id,
        matter.id,
        auth_context.workspace_id,
        context={
            "source": "whatsapp_inbox",
            "permissions": ["security:manage"],
            "isPlatformRoot": True,
        },
        actor_user_id=auth_context.user_id,
    )
    assert run.status == "completed"
    assert run.context["externalActionsExecuted"] == 0
    assert run.context["authorizationSource"] == "manual_actor"
    assert run.context["authorizationActorId"] == str(auth_context.user_id)
    assert "finance:view" in run.context["permissions"]
    assert "security:manage" not in run.context["permissions"]
    assert "isPlatformRoot" not in run.context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "corrected_payload"),
    [
        (HumanOverrideDecision.accepted, None),
        (HumanOverrideDecision.corrected, {"summary": "Saída corrigida"}),
    ],
)
async def test_successful_human_override_reconciles_linked_flow_after_commit(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
    decision: HumanOverrideDecision,
    corrected_payload: dict | None,
):
    _, run, _, agent_run = await _create_linked_agent_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix=f"override-{decision.value}",
        agent_status=AgentRunStatus.waiting_human,
    )
    settlement_tasks: list[list[str]] = []
    continuations: list[list[str]] = []

    async def no_side_effect(*args, **kwargs):
        return None

    monkeypatch.setattr(AgentRuntimeService, "_append_internal_message", no_side_effect)
    monkeypatch.setattr(AgentRuntimeService, "_broadcast_run_event", no_side_effect)
    monkeypatch.setattr(
        flow_tasks.settle_flow_agent_run,
        "apply_async",
        lambda *args, **kwargs: settlement_tasks.append(kwargs["args"]),
    )
    monkeypatch.setattr(
        flow_tasks.execute_flow_run_step,
        "apply_async",
        lambda *args, **kwargs: continuations.append(kwargs["args"]),
    )

    override = await AgentRuntimeService(db_session).create_override(
        run=agent_run,
        workspace_id=auth_context.workspace_id,
        decided_by_user_id=auth_context.user_id,
        decision=decision,
        notes="Revisado",
        corrected_payload=corrected_payload,
    )

    await db_session.refresh(run)
    await db_session.refresh(agent_run)
    assert override.decision == decision
    assert agent_run.status == AgentRunStatus.completed
    if corrected_payload:
        assert agent_run.output_payload == corrected_payload
    assert run.status == "active"
    assert run.current_node_id == "next-1"
    assert settlement_tasks == [[str(agent_run.id), str(auth_context.workspace_id)]]
    assert len(continuations) == 1
    assert continuations[0][:2] == [str(run.id), str(auth_context.workspace_id)]


@pytest.mark.asyncio
async def test_rejected_human_override_keeps_linked_flow_reviewable(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _, agent_run = await _create_linked_agent_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="override-rejected",
        agent_status=AgentRunStatus.waiting_human,
    )
    continuations: list[list[str]] = []

    async def no_side_effect(*args, **kwargs):
        return None

    monkeypatch.setattr(AgentRuntimeService, "_append_internal_message", no_side_effect)
    monkeypatch.setattr(AgentRuntimeService, "_broadcast_run_event", no_side_effect)
    monkeypatch.setattr(
        flow_tasks.settle_flow_agent_run,
        "apply_async",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        flow_tasks.execute_flow_run_step,
        "apply_async",
        lambda *args, **kwargs: continuations.append(kwargs["args"]),
    )

    await AgentRuntimeService(db_session).create_override(
        run=agent_run,
        workspace_id=auth_context.workspace_id,
        decided_by_user_id=auth_context.user_id,
        decision=HumanOverrideDecision.rejected,
        notes="Saída rejeitada",
    )

    await db_session.refresh(run)
    await db_session.refresh(agent_run)
    checkpoint = run.context[FLOW_AGENT_EXECUTIONS_KEY][str(agent_run.id)]
    assert agent_run.status == AgentRunStatus.failed
    assert run.status == "waiting_human"
    assert run.current_node_id == "agent-1"
    assert run.error_message == "Saída rejeitada"
    assert checkpoint["state"] == "failed"
    assert continuations == []


@pytest.mark.asyncio
async def test_flow_aware_agent_retry_rebinds_checkpoint_and_completes_once(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    flow, run, _, original = await _create_linked_agent_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="flow-aware-retry",
        agent_status=AgentRunStatus.failed,
        agent_error="Falha original",
    )
    agent_tasks: list[tuple[str, str]] = []
    continuations: list[list[str]] = []
    monkeypatch.setattr(event_bus, "model_api_execution_allowed", lambda: True)
    monkeypatch.setattr(
        event_bus,
        "_publish_agent_run",
        lambda *, run_id, workspace_id, task_id: agent_tasks.append(
            (str(run_id), str(workspace_id))
        ),
    )
    monkeypatch.setattr(
        flow_tasks.execute_flow_run_step,
        "apply_async",
        lambda *args, **kwargs: continuations.append(kwargs["args"]),
    )

    retried = await event_bus.retry_agent_run(
        run_id=original.id,
        workspace_id=auth_context.workspace_id,
        triggered_by_user_id=auth_context.user_id,
        session_factory=_session_factory(db_session),
    )
    retried_again = await event_bus.retry_agent_run(
        run_id=original.id,
        workspace_id=auth_context.workspace_id,
        triggered_by_user_id=auth_context.user_id,
        session_factory=_session_factory(db_session),
    )

    assert retried is not None
    assert retried_again is not None
    assert retried_again.id == retried.id
    assert retried.trigger_source == f"flow:{flow.id}"
    assert retried.dedupe_key == (f"flow-agent-retry:{run.id}:{run.revision_id}:agent-1:1:1")
    assert retried.input_payload["flow_run_id"] == str(run.id)
    assert retried.input_payload["flow_revision_id"] == str(run.revision_id)
    assert retried.input_payload["flow_node_id"] == "agent-1"
    assert retried.input_payload["retry_of_agent_run_id"] == str(original.id)
    assert agent_tasks == [(str(retried.id), str(auth_context.workspace_id))]

    await db_session.refresh(run)
    assert run.context[FLOW_ACTIVE_AGENT_RUN_KEY] == str(retried.id)
    assert run.context[FLOW_AGENT_EXECUTIONS_KEY][str(original.id)]["state"] == ("superseded")
    retry_checkpoint = run.context[FLOW_AGENT_EXECUTIONS_KEY][str(retried.id)]
    assert retry_checkpoint["retryOfAgentRunId"] == str(original.id)
    assert retry_checkpoint["state"] == "queued"

    persisted_retry = await db_session.get(AgentRun, retried.id)
    assert persisted_retry is not None
    persisted_retry.status = AgentRunStatus.completed
    persisted_retry.completed_at = datetime.now(UTC)
    persisted_retry.output_payload = {"summary": "Retry concluído"}
    await db_session.commit()

    settlement = await flow_tasks._async_settle_flow_agent_run(
        str(retried.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert settlement.outcome == "advanced"
    assert run.status == "active"
    assert run.current_node_id == "next-1"
    assert len(continuations) == 1
    assert continuations[0][2] == f"flow-agent-continuation:{retried.id}"


@pytest.mark.asyncio
async def test_worker_executes_run_revision_snapshot_after_flow_update(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    flow, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="snapshot-worker",
    )
    old_revision_id = run.revision_id
    await db_session.commit()

    updated = await FlowService(db_session).update_flow(
        flow.id,
        auth_context.workspace_id,
        FlowUpdate(
            expected_revision=1,
            nodes=[
                {
                    "id": "node-1",
                    "type": "send_message",
                    "config": {"message": "Mensagem da revisão nova"},
                }
            ],
        ),
        actor_user_id=auth_context.user_id,
    )
    assert updated is not None
    assert updated.current_revision == 2
    assert updated.current_revision_id != old_revision_id

    sent: list[str] = []

    async def record_send(workspace_id, phone, message, db):
        sent.append(message)

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert sent == ["Olá Ana"]
    assert run.revision_id == old_revision_id
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_flow_step_limit_stops_cycle_before_another_effect(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    flow, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="loop-guard",
        trigger_config={"maxSteps": 2},
    )
    run.context = {"phone": "+5511999999999", "flowStepCount": 2}
    await db_session.commit()
    sends: list[str] = []

    async def record_send(*args, **kwargs):
        sends.append("sent")

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert sends == []
    assert run.status == "failed"
    assert run.context["flowLoopGuardTriggered"] is True
    assert run.error_message == "Limite seguro de 2 etapas atingido"


@pytest.mark.asyncio
async def test_local_simulation_run_cannot_be_reanimated_by_worker(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="simulacao-worker",
    )
    run.context = {
        "klmRuntime": "local_simulation",
        "externalActionsExecuted": 0,
        "phone": "+5511999999999",
    }
    await db_session.commit()
    sends: list[str] = []

    async def record_send(*args, **kwargs):
        sends.append("sent")

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert sends == []
    assert run.status == "failed"
    assert run.error_message == "Execução local não pode ser processada pelo worker"
    assert run.context["externalActionsExecuted"] == 0


@pytest.mark.asyncio
async def test_ambiguous_send_failure_requires_human_reconciliation(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="falha-worker",
    )
    await db_session.commit()

    async def fail_send(*args, **kwargs):
        raise RuntimeError("delivery failed")

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", fail_send)

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert run.status == "waiting_human"
    assert run.completed_at is None
    assert run.current_node_id == "node-1"
    assert run.error_message is None
    assert "reconciliação humana" in (run.pause_reason or "")
    intent_key = run.context[flow_tasks.FLOW_ACTIVE_EXTERNAL_INTENT_KEY]
    intent = run.context[flow_tasks.FLOW_EXTERNAL_INTENTS_KEY][intent_key]
    assert intent["state"] == "uncertain"
    assert intent["reconciliationRequired"] is True
    decision = await db_session.scalar(
        select(HumanDecision).where(HumanDecision.flow_run_id == run.id)
    )
    assert decision is not None
    assert decision.assigned_user_id is not None
    assert decision.owner_user_id is not None
    assert decision.routing_path
    assert decision.due_at is not None
    deliveries = list(
        (
            await db_session.scalars(
                select(HumanNotificationDelivery).where(
                    HumanNotificationDelivery.decision_id == decision.id
                )
            )
        ).all()
    )
    assert any(item.recipient_kind == "owner" for item in deliveries)
    assert any(item.channel == "in_app" for item in deliveries)
    assert any(item.channel == "phone" for item in deliveries)


@pytest.mark.asyncio
async def test_scheduled_message_from_inactive_flow_never_sends(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, matter = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="agendada-inativa",
        flow_active=False,
        run_status="completed",
    )
    message = ScheduledMessage(
        flow_run_id=run.id,
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511999999999",
        message="Não enviar",
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        status="pending",
    )
    db_session.add(message)
    await db_session.commit()
    sends: list[str] = []

    async def record_send(*args, **kwargs):
        sends.append("sent")

    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)

    await flow_tasks._async_process_scheduled_messages(session_factory=_session_factory(db_session))

    await db_session.refresh(message)
    assert sends == []
    assert message.status == "failed"
    assert message.sent_at is None
    assert message.error_message == "Flow inativo não permite entregas"


@pytest.mark.asyncio
async def test_scheduled_message_waits_while_run_is_paused(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, matter = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="agendada-pausada",
        run_status="paused",
    )
    message = ScheduledMessage(
        flow_run_id=run.id,
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511999999999",
        message="Aguardar",
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        status="pending",
    )
    db_session.add(message)
    await db_session.commit()
    sends: list[str] = []

    async def record_send(*args, **kwargs):
        sends.append("sent")

    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)

    await flow_tasks._async_process_scheduled_messages(session_factory=_session_factory(db_session))

    await db_session.refresh(message)
    assert sends == []
    assert message.status == "pending"
    assert message.sent_at is None


@pytest.mark.asyncio
async def test_paused_flow_backlog_cannot_starve_due_standalone_message(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, paused_run, matter = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="paused-backlog",
        run_status="paused",
    )
    paused_messages = [
        ScheduledMessage(
            flow_run_id=paused_run.id,
            matter_id=matter.id,
            workspace_id=auth_context.workspace_id,
            contact_phone=f"+55119999{index:04d}",
            message=f"Aguardar {index}",
            scheduled_at=datetime.now(UTC) - timedelta(minutes=2),
            status="pending",
        )
        for index in range(50)
    ]
    db_session.add_all(paused_messages)
    await db_session.commit()
    due = await _create_due_scheduled_message(
        db_session,
        auth_context.workspace_id,
        suffix="after-paused-backlog",
        body="Mensagem elegível",
    )
    sends: list[str] = []

    async def record_send(workspace_id, phone, body, db):
        sends.append(body)

    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)

    await flow_tasks._async_process_scheduled_messages(session_factory=_session_factory(db_session))

    await db_session.refresh(due)
    for message in paused_messages:
        await db_session.refresh(message)
    assert sends == ["Mensagem elegível"]
    assert due.status == "sent"
    assert all(message.status == "pending" for message in paused_messages)


@pytest.mark.asyncio
async def test_corrupt_run_revision_fails_scheduled_message_without_stalling(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, matter = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="agendada-revisao-corrompida",
        run_status="completed",
    )
    message = ScheduledMessage(
        flow_run_id=run.id,
        matter_id=matter.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511999999999",
        message="Não enviar",
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        status="pending",
    )
    db_session.add(message)
    await db_session.execute(
        update(FlowRevision)
        .where(FlowRevision.id == run.revision_id)
        .values(snapshot_hash="0" * 64)
    )
    await db_session.commit()
    sends: list[str] = []

    async def record_send(*args, **kwargs):
        sends.append("sent")

    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)

    await flow_tasks._async_process_scheduled_messages(session_factory=_session_factory(db_session))

    await db_session.refresh(message)
    assert sends == []
    assert message.status == "failed"
    assert "integridade" in (message.error_message or "")


@pytest.mark.asyncio
async def test_flow_worker_rejects_run_from_another_workspace(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="cross-tenant-worker",
    )
    other_workspace = Workspace(
        name="Outro workspace FlowTask",
        slug=f"flow-task-other-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.commit()
    adopted: list = []
    real_set_workspace_context = flow_tasks.set_workspace_context

    async def record_workspace(db, workspace_id):
        adopted.append(workspace_id)
        await real_set_workspace_context(db, workspace_id)

    sends: list[str] = []

    async def record_send(*args, **kwargs):
        sends.append("sent")

    monkeypatch.setattr(flow_tasks, "set_workspace_context", record_workspace)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=other_workspace.id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert adopted == [other_workspace.id]
    assert sends == []
    assert run.status == "active"


@pytest.mark.asyncio
async def test_scheduled_worker_uses_one_tenant_session_per_workspace(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    other_workspace = Workspace(
        name="Segundo workspace ScheduledTask",
        slug=f"scheduled-task-other-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    matter_a = await _create_matter(db_session, auth_context.workspace_id, "scheduled-a")
    matter_b = await _create_matter(db_session, other_workspace.id, "scheduled-b")
    message_a = ScheduledMessage(
        matter_id=matter_a.id,
        workspace_id=auth_context.workspace_id,
        contact_phone="+5511999990001",
        message="Workspace A",
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        status="pending",
    )
    message_b = ScheduledMessage(
        matter_id=matter_b.id,
        workspace_id=other_workspace.id,
        contact_phone="+5511999990002",
        message="Workspace B",
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        status="pending",
    )
    db_session.add_all((message_a, message_b))
    await db_session.commit()

    adopted: list = []
    sent: list[tuple] = []
    real_set_workspace_context = flow_tasks.set_workspace_context

    async def record_workspace(db, workspace_id):
        adopted.append(workspace_id)
        await real_set_workspace_context(db, workspace_id)

    async def record_send(workspace_id, phone, message, db):
        sent.append((workspace_id, message))

    monkeypatch.setattr(flow_tasks, "set_workspace_context", record_workspace)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)

    await flow_tasks._async_process_scheduled_messages(session_factory=_session_factory(db_session))

    await db_session.refresh(message_a)
    await db_session.refresh(message_b)
    assert set(adopted) == {auth_context.workspace_id, other_workspace.id}
    assert set(sent) == {
        (auth_context.workspace_id, "Workspace A"),
        (other_workspace.id, "Workspace B"),
    }
    assert message_a.status == message_b.status == "sent"


@pytest.mark.asyncio
async def test_flow_crash_after_unkeyed_provider_accept_never_resends(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="crash-unkeyed-flow",
    )
    await db_session.commit()
    sends: list[str] = []

    async def unkeyed_send(workspace_id, phone, body, db):
        sends.append(body)

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", unkeyed_send)
    _inject_one_crash(monkeypatch, "flow_external.after_provider_accept")

    with pytest.raises(InjectedWorkerCrash):
        await flow_tasks._async_execute_flow_run_step(
            str(run.id),
            workspace_id=auth_context.workspace_id,
            session_factory=_session_factory(db_session),
        )

    await db_session.refresh(run)
    intent_key = run.context[flow_tasks.FLOW_ACTIVE_EXTERNAL_INTENT_KEY]
    intent = run.context[flow_tasks.FLOW_EXTERNAL_INTENTS_KEY][intent_key]
    assert sends == ["Olá Ana"]
    assert intent["state"] == "dispatching"
    assert intent["providerSupportsIdempotency"] is False

    await _expire_active_flow_intent(db_session, run)
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert sends == ["Olá Ana"]
    assert run.status == "waiting_human"
    intent = run.context[flow_tasks.FLOW_EXTERNAL_INTENTS_KEY][intent_key]
    assert intent["state"] == "uncertain"
    assert intent["reconciliationRequired"] is True


@pytest.mark.asyncio
async def test_flow_crash_after_success_checkpoint_skips_duplicate_and_advances(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="crash-after-flow-checkpoint",
    )
    await db_session.commit()
    sends: list[str] = []

    async def unkeyed_send(workspace_id, phone, body, db):
        sends.append(body)

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", unkeyed_send)
    _inject_one_crash(monkeypatch, "flow_external.after_success_checkpoint")

    with pytest.raises(InjectedWorkerCrash):
        await flow_tasks._async_execute_flow_run_step(
            str(run.id),
            workspace_id=auth_context.workspace_id,
            session_factory=_session_factory(db_session),
        )

    await db_session.refresh(run)
    intent_key = run.context[flow_tasks.FLOW_ACTIVE_EXTERNAL_INTENT_KEY]
    assert run.context[flow_tasks.FLOW_EXTERNAL_INTENTS_KEY][intent_key]["state"] == ("confirmed")

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert sends == ["Olá Ana"]
    assert run.status == "completed"
    assert flow_tasks.FLOW_ACTIVE_EXTERNAL_INTENT_KEY not in run.context


@pytest.mark.asyncio
async def test_flow_idempotent_adapter_reuses_key_after_crash(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="crash-keyed-flow",
    )
    await db_session.commit()
    invocations: list[str] = []
    accepted_keys: set[str] = set()

    async def idempotent_send(
        workspace_id,
        phone,
        body,
        db,
        *,
        idempotency_key,
        before_dispatch,
    ):
        await before_dispatch(True)
        invocations.append(idempotency_key)
        accepted_keys.add(idempotency_key)
        return flow_tasks.ExternalDeliveryReceipt("wamid-1", True)

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", idempotent_send)
    _inject_one_crash(monkeypatch, "flow_external.after_provider_accept")

    with pytest.raises(InjectedWorkerCrash):
        await flow_tasks._async_execute_flow_run_step(
            str(run.id),
            workspace_id=auth_context.workspace_id,
            session_factory=_session_factory(db_session),
        )

    intent_key = await _expire_active_flow_intent(db_session, run)
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert invocations == [intent_key, intent_key]
    assert accepted_keys == {intent_key}
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_zapsign_crash_preserves_matter_correlation_and_requires_reconciliation(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, matter = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="crash-zapsign",
        node_type="zapsign_request",
    )
    await db_session.commit()
    external_ids: list[str] = []

    class FakeZapSign:
        async def create_document(self, **kwargs):
            external_ids.append(kwargs["external_id"])
            return SimpleNamespace(token="doc-1", sign_url="https://sign.test/doc-1")

    async def fake_get_zapsign_service(**kwargs):
        return FakeZapSign()

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        "app.services.zapsign_service.get_zapsign_service",
        fake_get_zapsign_service,
    )
    _inject_one_crash(monkeypatch, "flow_external.after_provider_accept")

    with pytest.raises(InjectedWorkerCrash):
        await flow_tasks._async_execute_flow_run_step(
            str(run.id),
            workspace_id=auth_context.workspace_id,
            session_factory=_session_factory(db_session),
        )

    intent_key = await _expire_active_flow_intent(db_session, run)
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert external_ids == [str(matter.id)]
    assert run.status == "waiting_human"
    intent = run.context[flow_tasks.FLOW_EXTERNAL_INTENTS_KEY][intent_key]
    assert intent["idempotencyKey"] == intent_key
    assert intent["state"] == "uncertain"
    assert intent["providerSupportsIdempotency"] is False


@pytest.mark.asyncio
async def test_scheduled_node_crash_before_commit_creates_one_message(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="crash-scheduled-node",
        node_type="scheduled_message",
        node_config={"message": "Lembrete", "scheduled_at": "23:59"},
    )
    await db_session.commit()
    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    _inject_one_crash(monkeypatch, "flow_external.after_local_effect_flush")

    with pytest.raises(InjectedWorkerCrash):
        await flow_tasks._async_execute_flow_run_step(
            str(run.id),
            workspace_id=auth_context.workspace_id,
            session_factory=_session_factory(db_session),
        )

    assert list((await db_session.scalars(select(ScheduledMessage))).all()) == []

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    messages = list((await db_session.scalars(select(ScheduledMessage))).all())
    assert len(messages) == 1
    assert messages[0].idempotency_key == f"scheduled-message:{messages[0].id}"
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_sequence_send_now_ignores_stale_saved_clock_time(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="sequence-send-now",
        node_type="scheduled_message",
        node_config={
            "message": "Lembrete estático",
            "template_name": "static_follow_up",
            "template_status": "pending",
            "bindings": [],
            "send_now": True,
            # This value represents an old editor snapshot and must not win.
            "scheduled_at": "09:00",
        },
    )
    await db_session.commit()
    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    before = datetime.now(UTC)
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    after = datetime.now(UTC)
    message = await db_session.scalar(
        select(ScheduledMessage).where(ScheduledMessage.flow_run_id == run.id)
    )
    assert message is not None
    scheduled_at = message.scheduled_at
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=UTC)
    assert before - timedelta(seconds=1) <= scheduled_at <= after + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_scheduled_claim_crash_reclaims_before_attempt(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    message = await _create_due_scheduled_message(
        db_session,
        auth_context.workspace_id,
        suffix="claim-crash",
    )
    sends: list[str] = []

    async def record_send(workspace_id, phone, body, db):
        sends.append(body)

    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)
    _inject_one_crash(monkeypatch, "scheduled_message.after_lease_claim")

    with pytest.raises(InjectedWorkerCrash):
        await flow_tasks._async_process_scheduled_messages(
            session_factory=_session_factory(db_session)
        )

    await db_session.refresh(message)
    first_token = message.lease_token
    assert message.status == "processing"
    assert message.delivery_attempted_at is None
    message.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    await flow_tasks._async_process_scheduled_messages(session_factory=_session_factory(db_session))

    await db_session.refresh(message)
    assert sends == ["Enviar"]
    assert message.status == "sent"
    assert message.delivery_attempt_count == 1
    assert first_token is not None
    assert message.lease_token is None


@pytest.mark.asyncio
async def test_scheduled_crash_after_unkeyed_accept_becomes_uncertain(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    message = await _create_due_scheduled_message(
        db_session,
        auth_context.workspace_id,
        suffix="provider-crash",
    )
    sends: list[str] = []

    async def unkeyed_send(workspace_id, phone, body, db):
        sends.append(body)

    monkeypatch.setattr(flow_tasks, "_send_whatsapp", unkeyed_send)
    _inject_one_crash(monkeypatch, "scheduled_message.after_provider_accept")

    with pytest.raises(InjectedWorkerCrash):
        await flow_tasks._async_process_scheduled_messages(
            session_factory=_session_factory(db_session)
        )

    await db_session.refresh(message)
    assert message.status == "processing"
    assert message.delivery_attempted_at is not None
    assert message.provider_supports_idempotency is False
    message.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    await flow_tasks._async_process_scheduled_messages(session_factory=_session_factory(db_session))
    await flow_tasks._async_process_scheduled_messages(session_factory=_session_factory(db_session))

    await db_session.refresh(message)
    assert sends == ["Enviar"]
    assert message.status == "delivery_uncertain"
    assert message.delivery_uncertain_at is not None


@pytest.mark.asyncio
async def test_scheduled_idempotent_adapter_reuses_key_after_crash(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    message = await _create_due_scheduled_message(
        db_session,
        auth_context.workspace_id,
        suffix="provider-keyed-crash",
    )
    invocations: list[str] = []
    accepted_keys: set[str] = set()

    async def idempotent_send(
        workspace_id,
        phone,
        body,
        db,
        *,
        idempotency_key,
        before_dispatch,
    ):
        await before_dispatch(True)
        invocations.append(idempotency_key)
        accepted_keys.add(idempotency_key)
        return flow_tasks.ExternalDeliveryReceipt("wamid-scheduled", True)

    monkeypatch.setattr(flow_tasks, "_send_whatsapp", idempotent_send)
    _inject_one_crash(monkeypatch, "scheduled_message.after_provider_accept")

    with pytest.raises(InjectedWorkerCrash):
        await flow_tasks._async_process_scheduled_messages(
            session_factory=_session_factory(db_session)
        )

    await db_session.refresh(message)
    expected_key = f"scheduled-message:{message.id}"
    message.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    await flow_tasks._async_process_scheduled_messages(session_factory=_session_factory(db_session))

    await db_session.refresh(message)
    assert invocations == [expected_key, expected_key]
    assert accepted_keys == {expected_key}
    assert message.status == "sent"
    assert message.provider_message_id == "wamid-scheduled"
    assert message.delivery_attempt_count == 2


@pytest.mark.asyncio
async def test_scheduled_message_fencing_rejects_stale_lease_token(
    db_session: AsyncSession,
    auth_context,
):
    message = await _create_due_scheduled_message(
        db_session,
        auth_context.workspace_id,
        suffix="fencing",
    )
    now = datetime.now(UTC)
    first_token = await flow_tasks._claim_scheduled_message(
        db_session,
        message,
        workspace_id=auth_context.workspace_id,
        now=now,
    )
    assert first_token is not None

    message.lease_expires_at = now - timedelta(seconds=1)
    await db_session.commit()
    assert not await flow_tasks._checkpoint_scheduled_message_attempt(
        db_session,
        message_id=message.id,
        workspace_id=auth_context.workspace_id,
        lease_token=first_token,
        provider_supports_idempotency=False,
    )
    second_token = await flow_tasks._claim_scheduled_message(
        db_session,
        message,
        workspace_id=auth_context.workspace_id,
        now=now,
    )
    assert second_token is not None
    assert second_token != first_token

    assert not await flow_tasks._checkpoint_scheduled_message_attempt(
        db_session,
        message_id=message.id,
        workspace_id=auth_context.workspace_id,
        lease_token=first_token,
        provider_supports_idempotency=False,
    )
    assert not await flow_tasks._checkpoint_scheduled_message_success(
        db_session,
        message_id=message.id,
        workspace_id=auth_context.workspace_id,
        lease_token=first_token,
        provider_reference="stale",
    )

    await db_session.refresh(message)
    assert message.status == "processing"
    assert message.lease_token == second_token
    assert message.sent_at is None


@pytest.mark.asyncio
async def test_legacy_processing_without_lease_is_never_retried(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    message = await _create_due_scheduled_message(
        db_session,
        auth_context.workspace_id,
        suffix="legacy-processing",
    )
    message.status = "processing"
    message.lease_token = None
    message.lease_expires_at = None
    await db_session.commit()
    sends: list[str] = []

    async def record_send(*args, **kwargs):
        sends.append("sent")

    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)

    await flow_tasks._async_process_scheduled_messages(session_factory=_session_factory(db_session))

    await db_session.refresh(message)
    assert sends == []
    assert message.status == "delivery_uncertain"
    assert "fencing" in (message.error_message or "")


@pytest.mark.asyncio
async def test_flow_crash_after_intent_commit_retries_before_any_effect(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="crash-after-intent",
    )
    await db_session.commit()
    sends: list[str] = []

    async def record_send(workspace_id, phone, body, db):
        sends.append(body)

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)
    _inject_one_crash(monkeypatch, "flow_external.after_intent_commit")

    with pytest.raises(InjectedWorkerCrash):
        await flow_tasks._async_execute_flow_run_step(
            str(run.id),
            workspace_id=auth_context.workspace_id,
            session_factory=_session_factory(db_session),
        )

    await db_session.refresh(run)
    intent_key = run.context[flow_tasks.FLOW_ACTIVE_EXTERNAL_INTENT_KEY]
    assert sends == []
    assert run.context[flow_tasks.FLOW_EXTERNAL_INTENTS_KEY][intent_key]["state"] == ("prepared")

    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert sends == ["Olá Ana"]
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_flow_crash_after_attempt_checkpoint_never_guesses_no_effect(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    _, run, _ = await _create_flow_run(
        db_session,
        auth_context.workspace_id,
        suffix="crash-after-attempt",
    )
    await db_session.commit()
    sends: list[str] = []

    async def record_send(workspace_id, phone, body, db):
        sends.append(body)

    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)
    _inject_one_crash(monkeypatch, "flow_external.after_attempt_checkpoint")

    with pytest.raises(InjectedWorkerCrash):
        await flow_tasks._async_execute_flow_run_step(
            str(run.id),
            workspace_id=auth_context.workspace_id,
            session_factory=_session_factory(db_session),
        )

    intent_key = await _expire_active_flow_intent(db_session, run)
    await flow_tasks._async_execute_flow_run_step(
        str(run.id),
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert sends == []
    assert run.status == "waiting_human"
    assert run.context[flow_tasks.FLOW_EXTERNAL_INTENTS_KEY][intent_key]["state"] == ("uncertain")


@pytest.mark.asyncio
async def test_scheduled_crash_after_attempt_checkpoint_never_auto_retries(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    message = await _create_due_scheduled_message(
        db_session,
        auth_context.workspace_id,
        suffix="scheduled-attempt-crash",
    )
    sends: list[str] = []

    async def record_send(workspace_id, phone, body, db):
        sends.append(body)

    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)
    _inject_one_crash(monkeypatch, "scheduled_message.after_attempt_checkpoint")

    with pytest.raises(InjectedWorkerCrash):
        await flow_tasks._async_process_scheduled_messages(
            session_factory=_session_factory(db_session)
        )

    await db_session.refresh(message)
    assert sends == []
    assert message.delivery_attempted_at is not None
    message.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    await flow_tasks._async_process_scheduled_messages(session_factory=_session_factory(db_session))

    await db_session.refresh(message)
    assert sends == []
    assert message.status == "delivery_uncertain"


@pytest.mark.asyncio
async def test_scheduled_crash_after_success_checkpoint_is_terminal(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    message = await _create_due_scheduled_message(
        db_session,
        auth_context.workspace_id,
        suffix="scheduled-success-crash",
    )
    sends: list[str] = []

    async def record_send(workspace_id, phone, body, db):
        sends.append(body)

    monkeypatch.setattr(flow_tasks, "_send_whatsapp", record_send)
    _inject_one_crash(monkeypatch, "scheduled_message.after_success_checkpoint")

    with pytest.raises(InjectedWorkerCrash):
        await flow_tasks._async_process_scheduled_messages(
            session_factory=_session_factory(db_session)
        )

    await db_session.refresh(message)
    assert message.status == "sent"
    assert message.sent_at is not None

    await flow_tasks._async_process_scheduled_messages(session_factory=_session_factory(db_session))

    await db_session.refresh(message)
    assert sends == ["Enviar"]
    assert message.status == "sent"
