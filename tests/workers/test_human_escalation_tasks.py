from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agent_platform import (
    HumanDecision,
    HumanDecisionStatus,
    HumanDeliveryStatus,
    HumanNotificationDelivery,
)
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.flow import FlowRun
from app.models.notification import Notification
from app.models.user import User
from app.workers import flow_tasks


@pytest.mark.asyncio
async def test_terminal_human_escalation_calls_owner_and_fails_waiting_flow(
    db_session: AsyncSession, auth_context, monkeypatch
):
    """The final hierarchy hop is terminal: no phantom wait or extra call remains."""
    flow_run = FlowRun(
        flow_id=uuid4(),
        revision_id=uuid4(),
        matter_id=uuid4(),
        workspace_id=auth_context.workspace_id,
        status="waiting_human",
        context={
            "humanCheckpoints": {"checkpoint-1": {"decisionKey": "terminal-worker"}}
        },
    )
    db_session.add(flow_run)
    await db_session.flush()
    decision = HumanDecision(
        workspace_id=auth_context.workspace_id,
        flow_run_id=flow_run.id,
        decision_key="terminal-worker",
        status=HumanDecisionStatus.pending,
        options={"approve": True},
        assigned_user_id=auth_context.user_id,
        owner_user_id=auth_context.user_id,
        routing_path=[str(auth_context.user_id)],
        max_hops=1,
        escalation_level=0,
        due_at=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(decision)
    await db_session.commit()

    # The worker imports this factory at call time. Reuse the isolated test DB
    # and bind the tenant context as a no-op (SQLite has no SET LOCAL support).
    session_factory = async_sessionmaker(
        db_session.bind, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr("app.db.session.WorkerSessionLocal", session_factory)

    async def bind_workspace(_db, _workspace_id):
        return None

    monkeypatch.setattr(flow_tasks, "set_workspace_context", bind_workspace)
    result = await flow_tasks._async_process_human_escalations()

    assert result == {
        "escalated": 0,
        "orphanRecovered": 0,
        "orphanFailed": 0,
        "phoneDelivered": 0,
        "phoneFailed": 0,
    }
    await db_session.refresh(decision)
    await db_session.refresh(flow_run)
    assert decision.status == HumanDecisionStatus.expired
    assert flow_run.status == "failed"
    assert "hierarquia finita" in (flow_run.pause_reason or "")
    deliveries = list(
        (
            await db_session.scalars(
                select(HumanNotificationDelivery).where(
                    HumanNotificationDelivery.decision_id == decision.id
                )
            )
        ).all()
    )
    assert {(delivery.channel, delivery.status) for delivery in deliveries} == {
        ("in_app", HumanDeliveryStatus.delivered),
        ("phone", HumanDeliveryStatus.not_configured),
    }
    assert {delivery.event_key for delivery in deliveries} == {"terminal_owner_1"}


@pytest.mark.asyncio
async def test_terminal_human_escalation_fails_waiting_agent_run(
    db_session: AsyncSession, auth_context, monkeypatch
):
    """A direct AgentRun cannot remain parked after the final human hop expires."""
    agent_run = AgentRun(
        workspace_id=auth_context.workspace_id,
        matter_id=uuid4(),
        triggered_by_user_id=auth_context.user_id,
        agent_name="finance-director",
        trigger_source="human-routing-test",
        status=AgentRunStatus.waiting_human,
    )
    db_session.add(agent_run)
    await db_session.flush()
    decision = HumanDecision(
        workspace_id=auth_context.workspace_id,
        agent_run_id=agent_run.id,
        decision_key="terminal-agent-worker",
        status=HumanDecisionStatus.pending,
        options={"approve": True},
        assigned_user_id=auth_context.user_id,
        owner_user_id=auth_context.user_id,
        routing_path=[str(auth_context.user_id)],
        max_hops=1,
        escalation_level=0,
        due_at=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(decision)
    await db_session.commit()

    session_factory = async_sessionmaker(
        db_session.bind, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr("app.db.session.WorkerSessionLocal", session_factory)

    async def bind_workspace(_db, _workspace_id):
        return None

    monkeypatch.setattr(flow_tasks, "set_workspace_context", bind_workspace)
    result = await flow_tasks._async_process_human_escalations()

    assert result == {
        "escalated": 0,
        "orphanRecovered": 0,
        "orphanFailed": 0,
        "phoneDelivered": 0,
        "phoneFailed": 0,
    }
    await db_session.refresh(decision)
    await db_session.refresh(agent_run)
    assert decision.status == HumanDecisionStatus.expired
    assert agent_run.status == AgentRunStatus.failed
    assert agent_run.completed_at is not None
    assert "hierarquia finita" in (agent_run.error_message or "")
    deliveries = list(
        (
            await db_session.scalars(
                select(HumanNotificationDelivery).where(
                    HumanNotificationDelivery.decision_id == decision.id
                )
            )
        ).all()
    )
    assert {(delivery.channel, delivery.status) for delivery in deliveries} == {
        ("in_app", HumanDeliveryStatus.delivered),
        ("phone", HumanDeliveryStatus.not_configured),
    }


@pytest.mark.asyncio
async def test_stale_tool_wait_creates_one_routed_human_call_and_owner_watch(
    db_session: AsyncSession, auth_context, monkeypatch
):
    """A tool pause cannot become an unattended polling state."""
    agent_run = AgentRun(
        workspace_id=auth_context.workspace_id,
        matter_id=uuid4(),
        triggered_by_user_id=auth_context.user_id,
        agent_name="technical-support",
        trigger_source="runtime-wait-test",
        status=AgentRunStatus.waiting_tool,
        error_message="connector timed out",
        updated_at=datetime.now(UTC) - timedelta(minutes=16),
    )
    db_session.add(agent_run)
    await db_session.commit()

    session_factory = async_sessionmaker(
        db_session.bind, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr("app.db.session.WorkerSessionLocal", session_factory)

    async def bind_workspace(_db, _workspace_id):
        return None

    monkeypatch.setattr(flow_tasks, "set_workspace_context", bind_workspace)
    await flow_tasks._async_process_human_escalations()
    await db_session.refresh(agent_run)
    assert agent_run.status == AgentRunStatus.waiting_human
    assert agent_run.output_payload["runtimeWait"]["sourceStatus"] == "waiting_tool"

    decisions = list(
        (
            await db_session.scalars(
                select(HumanDecision).where(HumanDecision.agent_run_id == agent_run.id)
            )
        ).all()
    )
    assert len(decisions) == 1
    assert decisions[0].decision_key == f"agent-run:{agent_run.id}:runtime-wait:waiting_tool"
    assert decisions[0].options["resumeAgentRunOnApprove"] is True
    deliveries = list(
        (
            await db_session.scalars(
                select(HumanNotificationDelivery).where(
                    HumanNotificationDelivery.decision_id == decisions[0].id
                )
            )
        ).all()
    )
    # In the default fixture the correctly resolved technical route ends at
    # the same workspace owner, so one "assigned" call is also the owner
    # watch; both system and phone channels are durable.
    assert {item.event_key for item in deliveries} == {"assigned"}
    assert {item.channel for item in deliveries} == {"in_app", "phone"}

    # A second Beat tick sees the pending canonical decision and must neither
    # duplicate a call nor turn it into a failure.
    await flow_tasks._async_process_human_escalations()
    decisions_after_second_tick = list(
        (
            await db_session.scalars(
                select(HumanDecision).where(HumanDecision.agent_run_id == agent_run.id)
            )
        ).all()
    )
    assert len(decisions_after_second_tick) == 1


@pytest.mark.asyncio
async def test_budget_pause_requires_owner_decision_and_records_next_period(
    db_session: AsyncSession, auth_context, monkeypatch
):
    """Budget exhaustion alerts the owner immediately instead of sleeping forever."""
    agent_run = AgentRun(
        workspace_id=auth_context.workspace_id,
        matter_id=uuid4(),
        triggered_by_user_id=auth_context.user_id,
        agent_name="finance-director",
        trigger_source="budget-wait-test",
        status=AgentRunStatus.paused_budget,
        updated_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(agent_run)
    await db_session.commit()
    session_factory = async_sessionmaker(
        db_session.bind, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr("app.db.session.WorkerSessionLocal", session_factory)

    async def bind_workspace(_db, _workspace_id):
        return None

    monkeypatch.setattr(flow_tasks, "set_workspace_context", bind_workspace)
    await flow_tasks._async_process_human_escalations()
    await db_session.refresh(agent_run)
    assert agent_run.status == AgentRunStatus.waiting_human
    wait = agent_run.output_payload["runtimeWait"]
    assert wait["resumePolicy"] == "human_decision_required"
    assert datetime.fromisoformat(wait["scheduledResumeAt"]).day == 1
    decision = await db_session.scalar(
        select(HumanDecision).where(HumanDecision.agent_run_id == agent_run.id)
    )
    assert decision is not None
    assert decision.options["sourceWaitStatus"] == "paused_budget"


@pytest.mark.asyncio
async def test_human_phone_delivery_requires_provider_receipt(
    db_session: AsyncSession, auth_context, monkeypatch
):
    """A phone alert becomes delivered only after the provider supplies a receipt."""
    owner = await db_session.get(User, auth_context.user_id)
    assert owner is not None
    owner.phone = "+5511999999999"
    decision = HumanDecision(
        workspace_id=auth_context.workspace_id,
        decision_key="phone-receipt",
        status=HumanDecisionStatus.pending,
        options={"approve": True},
        assigned_user_id=auth_context.user_id,
        owner_user_id=auth_context.user_id,
        routing_path=[str(auth_context.user_id)],
        max_hops=1,
    )
    db_session.add(decision)
    await db_session.flush()
    delivery = HumanNotificationDelivery(
        workspace_id=auth_context.workspace_id,
        decision_id=decision.id,
        recipient_user_id=auth_context.user_id,
        recipient_kind="owner",
        channel="phone",
        event_key="receipt-test",
        status=HumanDeliveryStatus.pending,
    )
    db_session.add(delivery)
    await db_session.commit()

    session_factory = async_sessionmaker(
        db_session.bind, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr("app.db.session.WorkerSessionLocal", session_factory)

    async def bind_workspace(_db, _workspace_id):
        return None

    async def send_with_receipt(*_args, **_kwargs):
        return SimpleNamespace(provider_reference="wamid.human-receipt")

    monkeypatch.setattr(flow_tasks, "set_workspace_context", bind_workspace)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", send_with_receipt)

    result = await flow_tasks._async_process_human_escalations()

    await db_session.refresh(delivery)
    assert result["phoneDelivered"] == 1
    assert result["phoneFailed"] == 0
    assert delivery.status == HumanDeliveryStatus.delivered
    assert delivery.provider_receipt_id == "wamid.human-receipt"
    assert delivery.attempts == 1
    assert delivery.delivered_at is not None


@pytest.mark.asyncio
async def test_human_phone_delivery_retries_then_fails_with_owner_alerts(
    db_session: AsyncSession, auth_context, monkeypatch
):
    """Retries are finite and a failed delivery remains visible to the office owner."""
    owner = await db_session.get(User, auth_context.user_id)
    assert owner is not None
    owner.phone = "+5511988888888"
    decision = HumanDecision(
        workspace_id=auth_context.workspace_id,
        decision_key="phone-failure",
        status=HumanDecisionStatus.pending,
        options={"approve": True},
        assigned_user_id=auth_context.user_id,
        owner_user_id=auth_context.user_id,
        routing_path=[str(auth_context.user_id)],
        max_hops=1,
    )
    db_session.add(decision)
    await db_session.flush()
    delivery = HumanNotificationDelivery(
        workspace_id=auth_context.workspace_id,
        decision_id=decision.id,
        recipient_user_id=auth_context.user_id,
        recipient_kind="owner",
        channel="phone",
        event_key="failure-test",
        status=HumanDeliveryStatus.pending,
    )
    db_session.add(delivery)
    await db_session.commit()

    session_factory = async_sessionmaker(
        db_session.bind, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr("app.db.session.WorkerSessionLocal", session_factory)

    async def bind_workspace(_db, _workspace_id):
        return None

    async def failed_send(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(flow_tasks, "set_workspace_context", bind_workspace)
    monkeypatch.setattr(flow_tasks, "_send_whatsapp", failed_send)

    for attempt in range(1, 4):
        result = await flow_tasks._async_process_human_escalations()
        await db_session.refresh(delivery)
        assert result["phoneDelivered"] == 0
        assert result["phoneFailed"] == 1
        assert delivery.attempts == attempt
        assert delivery.status == (
            HumanDeliveryStatus.failed if attempt == 3 else HumanDeliveryStatus.pending
        )

    owner_alerts = list(
        (
            await db_session.scalars(
                select(Notification).where(
                    Notification.workspace_id == auth_context.workspace_id,
                    Notification.user_id == auth_context.user_id,
                    Notification.title == "Falha no aviso telefônico",
                )
            )
        ).all()
    )
    assert len(owner_alerts) == 3


@pytest.mark.asyncio
async def test_watchdog_recovers_orphaned_wait_once_with_canonical_call(
    db_session: AsyncSession, auth_context, monkeypatch
):
    flow_run = FlowRun(
        flow_id=uuid4(),
        revision_id=uuid4(),
        matter_id=uuid4(),
        workspace_id=auth_context.workspace_id,
        status="waiting_human",
        current_node_id="legacy-human-node",
        pause_reason="Processo interrompido antes do handoff.",
        context={"flowStepCount": 2},
    )
    db_session.add(flow_run)
    await db_session.commit()

    session_factory = async_sessionmaker(
        db_session.bind, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr("app.db.session.WorkerSessionLocal", session_factory)

    async def bind_workspace(_db, _workspace_id):
        return None

    monkeypatch.setattr(flow_tasks, "set_workspace_context", bind_workspace)
    first = await flow_tasks._async_process_human_escalations()

    await db_session.refresh(flow_run)
    decisions = list(
        (
            await db_session.scalars(
                select(HumanDecision).where(HumanDecision.flow_run_id == flow_run.id)
            )
        ).all()
    )
    assert first["orphanRecovered"] == 1
    assert flow_run.status == "waiting_human"
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.status == HumanDecisionStatus.pending
    assert decision.assigned_user_id == auth_context.user_id
    assert decision.owner_user_id == auth_context.user_id
    assert flow_run.context["humanCheckpoints"]["legacy-human-node"]["decisionId"] == str(
        decision.id
    )
    assert (
        await db_session.scalars(
            select(HumanNotificationDelivery).where(
                HumanNotificationDelivery.decision_id == decision.id
            )
        )
    ).all()

    second = await flow_tasks._async_process_human_escalations()
    assert second["orphanRecovered"] == 0
    assert len(
        list(
            (
                await db_session.scalars(
                    select(HumanDecision).where(HumanDecision.flow_run_id == flow_run.id)
                )
            ).all()
        )
    ) == 1


@pytest.mark.asyncio
async def test_watchdog_recovers_generic_agent_wait_once_with_owner_delivery(
    db_session: AsyncSession, auth_context, monkeypatch
):
    """Legacy AgentRuns must not remain waiting without a routed decision."""
    agent_run = AgentRun(
        workspace_id=auth_context.workspace_id,
        matter_id=uuid4(),
        triggered_by_user_id=auth_context.user_id,
        agent_name="klm_drafter",
        trigger_source="legacy-agent-wait",
        status=AgentRunStatus.waiting_human,
        input_payload={},
    )
    db_session.add(agent_run)
    await db_session.commit()

    session_factory = async_sessionmaker(
        db_session.bind, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr("app.db.session.WorkerSessionLocal", session_factory)

    async def bind_workspace(_db, _workspace_id):
        return None

    monkeypatch.setattr(flow_tasks, "set_workspace_context", bind_workspace)
    first = await flow_tasks._async_process_human_escalations()

    decisions = list(
        (
            await db_session.scalars(
                select(HumanDecision).where(HumanDecision.agent_run_id == agent_run.id)
            )
        ).all()
    )
    assert first["orphanRecovered"] == 1
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.status == HumanDecisionStatus.pending
    assert decision.owner_user_id == auth_context.user_id
    assert (
        await db_session.scalars(
            select(HumanNotificationDelivery).where(
                HumanNotificationDelivery.decision_id == decision.id
            )
        )
    ).all()

    second = await flow_tasks._async_process_human_escalations()
    assert second["orphanRecovered"] == 0
    assert len(
        list(
            (
                await db_session.scalars(
                    select(HumanDecision).where(HumanDecision.agent_run_id == agent_run.id)
                )
            ).all()
        )
    ) == 1
