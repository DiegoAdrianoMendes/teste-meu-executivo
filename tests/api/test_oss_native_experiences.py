from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.client import Client
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.flow import Flow
from app.models.klm_operations import (
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.matter import KanbanColumnId, Matter, MicroStatus, PracticeArea
from app.models.task import Task, TaskContextType, TaskPriority, TaskStatus
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.bottlecrm_client import BottleCRMClient
from app.services.chattermate_client import ChatterMateClient
from app.services.chatwoot_client import ChatwootClient
from app.services.klm_agent_service import AGENT_DEFINITIONS
from app.services.oss_native_experience_service import OSSNativeExperienceService
from app.services.rocketchat_client import RocketChatClient

pytestmark = pytest.mark.usefixtures("active_subscription")


async def _seed_native_experience(db_session, workspace_id):
    person = Client(
        workspace_id=workspace_id,
        name="Cliente nativo",
        email="cliente.nativo@example.com",
        phone="+5561999999999",
    )
    db_session.add(person)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=person.id,
        practice_area=PracticeArea.saude_suplementar,
        demand_type="Medicamento de alto custo",
        health_object_name="Medicamento X",
        column_id=KanbanColumnId.proposta,
        micro_status=MicroStatus.proposta_enviada,
        lead_score=82,
        claim_value=120_000,
        next_action="Retomar proposta",
    )
    db_session.add(matter)
    await db_session.flush()
    thread = OmnichannelThread(
        workspace_id=workspace_id,
        matter_id=matter.id,
        client_id=person.id,
        primary_channel=OmnichannelChannel.whatsapp,
        subject="Caso Medicamento X",
    )
    db_session.add(thread)
    await db_session.flush()
    db_session.add_all(
        [
            OmnichannelMessage(
                workspace_id=workspace_id,
                thread_id=thread.id,
                matter_id=matter.id,
                channel=OmnichannelChannel.whatsapp,
                direction=MessageDirection.outbound,
                author_type=MessageAuthorType.agent,
                author_label="SDR Saude",
                body="Mensagem local pendente",
                idempotency_key=f"native-chat-{uuid4()}",
                delivery_status=MessageDeliveryStatus.local_draft,
            ),
            OmnichannelMessage(
                workspace_id=workspace_id,
                thread_id=thread.id,
                matter_id=matter.id,
                channel=OmnichannelChannel.internal,
                direction=MessageDirection.internal,
                author_type=MessageAuthorType.human,
                author_label="Advogada",
                body="Estrategia aprovada internamente.",
                idempotency_key=f"native-internal-{uuid4()}",
                delivery_status=MessageDeliveryStatus.local_draft,
                message_metadata={
                    "discussion": "Tese liminar",
                    "attachments": [{"title": "parecer.pdf", "type": "application/pdf"}],
                },
            ),
        ]
    )
    task = Task(
        workspace_id=workspace_id,
        context_type=TaskContextType.matter,
        matter_id=matter.id,
        title="Revisar documentos",
        status=TaskStatus.pending,
        priority=TaskPriority.alta,
        due_date=(datetime.now(UTC) - timedelta(days=1)).date().isoformat(),
    )
    finance = FinanceEntry(
        workspace_id=workspace_id,
        matter_id=matter.id,
        title="Honorarios iniciais",
        entry_type=FinanceEntryType.receivable,
        status=FinanceEntryStatus.overdue,
        amount=4000,
        due_date=datetime.now(UTC) - timedelta(days=10),
    )
    flow = Flow(
        workspace_id=workspace_id,
        name="Follow-up proposta",
        practice_area="saude_suplementar",
        trigger="manual",
        nodes=[{"id": "start", "type": "trigger"}, {"id": "send", "type": "message"}],
        edges=[{"id": "start-send", "source": "start", "target": "send"}],
        is_active=True,
    )
    run = AgentRun(
        workspace_id=workspace_id,
        matter_id=matter.id,
        agent_name="klm_sdr",
        trigger_source="native_experience_test",
        status=AgentRunStatus.completed,
        input_tokens=120,
        output_tokens=45,
    )
    knowledge = KnowledgeChunk(
        workspace_id=workspace_id,
        matter_id=matter.id,
        source_type="document",
        source_ref="parecer.pdf",
        title="Parecer do caso",
        content_text="Conteudo juridico tenant-scoped.",
        summary="Resumo do parecer.",
        content_hash="a" * 64,
        tags=["parecer"],
    )
    db_session.add_all([task, finance, flow, run, knowledge])
    await db_session.flush()
    return matter, thread, flow


def _sidecar_must_not_start(*_args, **_kwargs):
    raise AssertionError("A experiencia nativa tentou construir um sidecar.")


@pytest.mark.asyncio
async def test_all_native_experiences_work_with_every_sidecar_offline(
    client,
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    matter, thread, flow = await _seed_native_experience(
        db_session,
        auth_context.workspace_id,
    )
    for sidecar in (
        ChatwootClient,
        RocketChatClient,
        ChatterMateClient,
        BottleCRMClient,
    ):
        monkeypatch.setattr(sidecar, "__init__", _sidecar_must_not_start)

    chat = await client.get(f"/api/v1/communications/threads/{thread.id}")
    assert chat.status_code == 200, chat.text
    assert chat.json()["projection"]["pendingMessages"] == 2

    routed = await client.patch(
        f"/api/v1/communications/threads/{thread.id}/routing",
        json={"status": "pending", "priority": "urgent", "labels": ["urgente"]},
    )
    assert routed.status_code == 200, routed.text
    assert routed.json()["conversation"]["priority"] == "urgent"

    reconciled = await client.post(f"/api/v1/communications/threads/{thread.id}/reconcile")
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["externalChannelDeliveryEnabled"] is False

    collaboration = await client.get(f"/api/v1/collaboration/threads/{thread.id}")
    assert collaboration.status_code == 200, collaboration.text
    assert collaboration.json()["messages"][0]["body"] == "Estrategia aprovada internamente."
    assert collaboration.json()["roomId"].startswith("klm:room:")

    collaboration_reconcile = await client.post(
        f"/api/v1/collaboration/threads/{thread.id}/reconcile"
    )
    assert collaboration_reconcile.status_code == 200, collaboration_reconcile.text
    assert collaboration_reconcile.json()["externalNetworkExecuted"] is False

    agents = await client.get("/api/v1/automation/agents?time_range=7d")
    assert agents.status_code == 200, agents.text
    assert len(agents.json()["agents"]) == len(AGENT_DEFINITIONS)
    agent_ids = {item["id"] for item in agents.json()["agents"]}
    assert {
        "klm-agent:orquestrador",
        "klm-agent:hermes",
        "klm-agent:auditor",
        "klm-agent:crm",
    } <= agent_ids
    assert agents.json()["peopleStats"]["newLeads7D"] == 1
    assert agents.json()["people"]["items"][0]["name"] == "Cliente nativo"
    sdr_performance = next(
        row for row in agents.json()["performance"] if row["name"] == "SDR Jurídico"
    )
    assert sdr_performance["totalChats"] == 1
    assert sdr_performance["completedRuns"] == 1
    assert agents.json()["knowledge"]["items"][0]["name"] == "Parecer do caso"
    assert agents.json()["externalExecutionEnabled"] is False
    agent_reconcile = await client.post("/api/v1/automation/agents/reconcile")
    assert agent_reconcile.status_code == 200, agent_reconcile.text
    assert agent_reconcile.json()["seen"] == len(AGENT_DEFINITIONS)

    flow_context = await client.get(f"/api/v1/automation/flows/{flow.id}")
    assert flow_context.status_code == 200, flow_context.text
    assert flow_context.json()["graphMatches"] is True
    flow_reconcile = await client.post(f"/api/v1/automation/flows/{flow.id}/reconcile")
    assert flow_reconcile.status_code == 200, flow_reconcile.text
    assert flow_reconcile.json()["externalExecutionEnabled"] is False

    funnel = await client.get("/api/v1/crm/funnel")
    assert funnel.status_code == 200, funnel.text
    assert funnel.json()["counts"]["leads"] == 1
    assert funnel.json()["revenue"]["totalPipeline"] == 120_000
    matter_context = await client.get(f"/api/v1/crm/matters/{matter.id}")
    assert matter_context.status_code == 200, matter_context.text
    assert {row["entityType"] for row in matter_context.json()["records"]} >= {
        "contact",
        "lead",
        "opportunity",
        "case",
        "task",
    }
    work = await client.get("/api/v1/crm/work")
    assert work.status_code == 200, work.text
    assert len(work.json()["tasks"]) == 1
    finance = await client.get("/api/v1/crm/finance")
    assert finance.status_code == 200, finance.text
    assert finance.json()["overdue"]["amount"] == 4000


@pytest.mark.asyncio
async def test_native_experiences_never_cross_workspace_boundaries(
    db_session,
    auth_context,
) -> None:
    _, own_thread, _ = await _seed_native_experience(
        db_session,
        auth_context.workspace_id,
    )
    other_workspace = Workspace(
        name="Outro tenant",
        slug=f"outro-{uuid4().hex[:10]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    _, other_thread, _ = await _seed_native_experience(db_session, other_workspace.id)
    service = OSSNativeExperienceService(db_session)

    masked_agents = await service.agent_context(
        auth_context.workspace_id,
        "7d",
        include_sensitive=False,
    )
    assert masked_agents["sensitiveDataHidden"] is True
    assert masked_agents["people"]["items"][0]["name"] == "Pessoa restrita"

    with pytest.raises(LookupError, match="Thread"):
        await service.chat_context(auth_context.workspace_id, other_thread.id)
    with pytest.raises(LookupError, match="Thread"):
        await service.collaboration_context(other_workspace.id, own_thread.id)

    own_funnel = await service.crm_funnel_context(
        auth_context.workspace_id,
        include_sensitive=True,
    )
    other_funnel = await service.crm_funnel_context(
        other_workspace.id,
        include_sensitive=True,
    )
    assert own_funnel["counts"]["leads"] == 1
    assert other_funnel["counts"]["leads"] == 1
    assert (
        own_funnel["projection"]["organizationId"] != (other_funnel["projection"]["organizationId"])
    )
