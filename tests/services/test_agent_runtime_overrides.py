from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.agent_platform import (
    HumanDecision,
    HumanDecisionStatus,
    HumanNotificationDelivery,
)
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.client import Client
from app.models.conversation import Conversation
from app.models.human_override import HumanOverride, HumanOverrideDecision
from app.models.matter import Matter, PracticeArea
from app.models.timeline_event import TimelineEvent
from app.services.agent_runtime_service import AgentRuntimeService
from app.services.knowledge_service import KnowledgeService

pytestmark = pytest.mark.usefixtures("active_subscription")


async def _waiting_run(db_session, auth_context, *, flow_linked: bool = False):
    client = Client(
        workspace_id=auth_context.workspace_id,
        name=f"Cliente Override {uuid4().hex[:8]}",
    )
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Revisão humana de saída",
        needs_approval=True,
    )
    db_session.add(matter)
    await db_session.flush()
    run = AgentRun(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        agent_name="klm_drafter",
        trigger_source="test:override",
        status=AgentRunStatus.waiting_human,
        input_payload={"flow_run_id": str(uuid4())} if flow_linked else {},
        output_payload={"draft": "original"},
        error_message="Revisão obrigatória",
        attempt_count=1,
    )
    db_session.add(run)
    await db_session.commit()
    return matter, run


def _isolate_override_side_effects(monkeypatch):
    monkeypatch.setattr(
        AgentRuntimeService,
        "_broadcast_run_event",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        KnowledgeService,
        "index_internal_conversation",
        AsyncMock(return_value=None),
    )


def _mock_flow_settlement(monkeypatch):
    from app.services.flow_service import FlowService
    from app.workers.flow_tasks import settle_flow_agent_run

    enqueue = MagicMock()
    settle = AsyncMock(return_value=None)
    monkeypatch.setattr(settle_flow_agent_run, "apply_async", enqueue)
    monkeypatch.setattr(FlowService, "settle_agent_run", settle)
    return enqueue, settle


async def _override_count(db_session, run_id):
    return await db_session.scalar(
        select(func.count()).select_from(HumanOverride).where(HumanOverride.agent_run_id == run_id)
    )


@pytest.mark.parametrize(
    ("agent_name", "expected_package"),
    [
        ("klm_sdr", "intake_sdr"),
        ("klm_onboarding_ops", "client_success"),
        ("pos_venda_agent", "client_success"),
        ("secretaria_atendimento", "secretary"),
        ("billing_reconciliation", "finance"),
        ("tributario_reforma", "accounting"),
        ("security_auditor", "auditor"),
        ("technical_support", "it_integrations"),
        ("klm_drafter", "legal_manager"),
    ],
)
def test_legacy_agents_route_to_the_matching_function_before_management(
    agent_name, expected_package
):
    run = AgentRun(agent_name=agent_name)

    required_roles, required_skills = AgentRuntimeService.human_routing_requirements(run)

    assert required_roles == [expected_package]
    assert required_skills == []


@pytest.mark.asyncio
async def test_generic_waiting_run_creates_one_routed_human_call_with_owner_watch(
    db_session, auth_context, monkeypatch
):
    """A legacy run cannot be committed as a silent waiting_human state."""
    matter, run = await _waiting_run(db_session, auth_context)
    run.status = AgentRunStatus.running
    run.completed_at = None
    await db_session.commit()
    _isolate_override_side_effects(monkeypatch)

    runtime = AgentRuntimeService(db_session)
    await runtime.mark_waiting_human(run, reason="Revisão jurídica necessária.")
    await db_session.commit()

    decisions = list(
        (
            await db_session.scalars(
                select(HumanDecision).where(HumanDecision.agent_run_id == run.id)
            )
        ).all()
    )
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.status == HumanDecisionStatus.pending
    assert decision.owner_user_id == auth_context.user_id
    assert decision.routing_path[-1] == str(auth_context.user_id)
    deliveries = list(
        (
            await db_session.scalars(
                select(HumanNotificationDelivery).where(
                    HumanNotificationDelivery.decision_id == decision.id
                )
            )
        ).all()
    )
    assert any(item.channel == "in_app" and item.recipient_kind == "owner" for item in deliveries)
    assert any(item.channel == "phone" and item.recipient_kind == "owner" for item in deliveries)

    # Replays reuse the pending decision rather than creating a second call.
    await runtime.mark_waiting_human(run, reason="Revisão jurídica necessária.")
    await db_session.commit()
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(HumanDecision)
            .where(HumanDecision.agent_run_id == run.id)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_identical_override_is_idempotent_and_settles_flow_only_once(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    matter, run = await _waiting_run(db_session, auth_context, flow_linked=True)
    _isolate_override_side_effects(monkeypatch)
    enqueue, settle = _mock_flow_settlement(monkeypatch)
    url = f"/api/v1/agent-runs/matters/{matter.id}/{run.id}/override"
    payload = {
        "decision": "corrected",
        "notes": "Correção final aprovada",
        "correctedPayload": {"draft": "corrigida"},
    }

    first = await client.post(url, json=payload)
    assert first.status_code == 200, first.text
    await db_session.refresh(run)
    first_completed_at = run.completed_at

    repeated = await client.post(url, json=payload)

    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["id"] == first.json()["id"]
    assert await _override_count(db_session, run.id) == 1
    await db_session.refresh(run)
    await db_session.refresh(matter)
    assert run.status == AgentRunStatus.completed
    assert run.output_payload == {"draft": "corrigida"}
    assert run.completed_at == first_completed_at
    assert matter.needs_approval is False
    assert await db_session.scalar(select(func.count()).select_from(TimelineEvent)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Conversation)) == 1
    enqueue.assert_called_once_with(
        args=[str(run.id), str(auth_context.workspace_id)],
        countdown=1,
    )
    settle.assert_awaited_once_with(run.id, auth_context.workspace_id)


@pytest.mark.parametrize(
    ("first_decision", "conflicting_decision", "expected_status"),
    [
        ("accepted", "rejected", AgentRunStatus.completed),
        ("rejected", "accepted", AgentRunStatus.failed),
    ],
)
@pytest.mark.asyncio
async def test_opposite_terminal_override_returns_409_and_preserves_first_decision(
    client,
    db_session,
    auth_context,
    monkeypatch,
    first_decision,
    conflicting_decision,
    expected_status,
):
    matter, run = await _waiting_run(db_session, auth_context, flow_linked=True)
    run_id = run.id
    _isolate_override_side_effects(monkeypatch)
    enqueue, settle = _mock_flow_settlement(monkeypatch)
    url = f"/api/v1/agent-runs/matters/{matter.id}/{run.id}/override"

    first = await client.post(
        url,
        json={"decision": first_decision, "notes": "Primeira decisão"},
    )
    assert first.status_code == 200, first.text
    await db_session.refresh(run)
    first_completed_at = run.completed_at

    conflict = await client.post(
        url,
        json={"decision": conflicting_decision, "notes": "Decisão posterior"},
    )

    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"] == {
        "code": "agent_run_override_conflict",
        "message": "A primeira decisão terminal deste AgentRun é imutável.",
        "agentRunId": str(run_id),
        "existingOverrideId": first.json()["id"],
        "existingDecision": first_decision,
    }
    assert await _override_count(db_session, run_id) == 1
    persisted_run = await db_session.get(AgentRun, run_id)
    assert persisted_run is not None
    assert persisted_run.status == expected_status
    assert persisted_run.completed_at == first_completed_at
    if first_decision == "rejected":
        assert persisted_run.error_message == "Primeira decisão"
    else:
        assert persisted_run.error_message is None
    enqueue.assert_called_once()
    settle.assert_awaited_once()


@pytest.mark.parametrize(
    "conflicting_payload",
    [
        {
            "decision": "corrected",
            "notes": "Notas alteradas",
            "correctedPayload": {"draft": "v1"},
        },
        {
            "decision": "corrected",
            "notes": "Notas originais",
            "correctedPayload": {"draft": "v2"},
        },
    ],
)
@pytest.mark.asyncio
async def test_same_decision_with_different_content_returns_409(
    client,
    db_session,
    auth_context,
    monkeypatch,
    conflicting_payload,
):
    matter, run = await _waiting_run(db_session, auth_context)
    run_id = run.id
    _isolate_override_side_effects(monkeypatch)
    url = f"/api/v1/agent-runs/matters/{matter.id}/{run.id}/override"
    first_payload = {
        "decision": "corrected",
        "notes": "Notas originais",
        "correctedPayload": {"draft": "v1"},
    }

    first = await client.post(url, json=first_payload)
    conflict = await client.post(url, json=conflicting_payload)

    assert first.status_code == 200, first.text
    assert conflict.status_code == 409, conflict.text
    assert await _override_count(db_session, run_id) == 1
    persisted_run = await db_session.get(AgentRun, run_id)
    assert persisted_run is not None
    assert persisted_run.output_payload == {"draft": "v1"}


@pytest.mark.parametrize(
    "run_status",
    [
        AgentRunStatus.queued,
        AgentRunStatus.running,
        AgentRunStatus.completed,
        AgentRunStatus.failed,
    ],
)
@pytest.mark.asyncio
async def test_override_rejects_run_not_effectively_waiting_human(
    client,
    db_session,
    auth_context,
    monkeypatch,
    run_status,
):
    matter, run = await _waiting_run(db_session, auth_context)
    run_id = run.id
    run.status = run_status
    await db_session.commit()
    _isolate_override_side_effects(monkeypatch)

    response = await client.post(
        f"/api/v1/agent-runs/matters/{matter.id}/{run_id}/override",
        json={"decision": "accepted", "notes": "Decisão inválida"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {
        "code": "agent_run_not_waiting_human",
        "message": "Somente um AgentRun aguardando revisão humana pode ser decidido.",
        "agentRunId": str(run_id),
        "currentStatus": run_status.value,
    }
    assert await _override_count(db_session, run_id) == 0


@pytest.mark.asyncio
async def test_matter_keeps_approval_flag_until_all_waiting_runs_are_reviewed(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    matter, first_run = await _waiting_run(db_session, auth_context)
    second_run = AgentRun(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        agent_name="klm_analyst",
        trigger_source="test:second-review",
        status=AgentRunStatus.waiting_human,
        output_payload={"analysis": "pending"},
        error_message="Revisão obrigatória",
        attempt_count=1,
    )
    db_session.add(second_run)
    await db_session.commit()
    _isolate_override_side_effects(monkeypatch)

    first_response = await client.post(
        f"/api/v1/agent-runs/matters/{matter.id}/{first_run.id}/override",
        json={"decision": "accepted", "notes": "Primeira revisão"},
    )
    assert first_response.status_code == 200, first_response.text
    await db_session.refresh(matter)
    await db_session.refresh(second_run)

    assert second_run.status == AgentRunStatus.waiting_human
    assert matter.needs_approval is True

    second_response = await client.post(
        f"/api/v1/agent-runs/matters/{matter.id}/{second_run.id}/override",
        json={"decision": "accepted", "notes": "Segunda revisão"},
    )
    assert second_response.status_code == 200, second_response.text
    await db_session.refresh(matter)

    assert matter.needs_approval is False


@pytest.mark.asyncio
async def test_database_rejects_a_second_override_for_same_run(db_session, auth_context):
    matter, run = await _waiting_run(db_session, auth_context)
    first = HumanOverride(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        agent_run_id=run.id,
        decided_by_user_id=auth_context.user_id,
        decision=HumanOverrideDecision.accepted,
    )
    second = HumanOverride(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        agent_run_id=run.id,
        decided_by_user_id=auth_context.user_id,
        decision=HumanOverrideDecision.rejected,
    )
    db_session.add_all([first, second])

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
