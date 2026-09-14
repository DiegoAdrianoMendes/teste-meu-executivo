from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.agent_run import AgentRun
from app.models.document import Document
from app.models.klm_operations import (
    ExternalActionIntent,
    ExternalActionIntentStatus,
    FollowUpStatus,
    FollowUpTask,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelMessage,
)
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.meeting import Meeting
from app.models.user import User
from app.services.knowledge_service import KnowledgeService
from tests.agent_deployment_helpers import seed_verified_agent_deployments


@pytest.fixture(autouse=True)
async def _explicit_agent_deployments(db_session, auth_context, monkeypatch):
    await seed_verified_agent_deployments(
        db_session,
        auth_context.workspace_id,
        {"crm", "juridico", "orquestrador", "peticao", "sdr", "secretaria"},
        monkeypatch,
    )


@pytest.mark.asyncio
async def test_health_denial_journey_stays_grounded_human_gated_and_offline(
    client,
    db_session,
    auth_context,
    active_subscription,
):
    intake = await client.post(
        "/api/v1/klm/omnichannel/email-ingest",
        json={
            "messageId": "journey-health-email-001",
            "senderEmail": "jornada.saude@example.com",
            "senderName": "Lead Jornada Saúde",
            "subject": "Negativa urgente de medicamento de alto custo",
            "body": "O plano negou o medicamento prescrito e o médico indicou urgência.",
            "attachments": [
                {
                    "name": "negativa-plano.pdf",
                    "mimeType": "application/pdf",
                    "contentText": "Negativa formal de cobertura emitida pelo plano de saúde.",
                    "documentType": "outro",
                },
                {
                    "name": "prescricao-medica.pdf",
                    "mimeType": "application/pdf",
                    "contentText": "Prescrição médica do medicamento de alto custo com urgência.",
                    "documentType": "outro",
                },
            ],
        },
    )
    assert intake.status_code == 201, intake.text
    intake_payload = intake.json()
    matter_id = intake_payload["matterId"]
    thread_id = intake_payload["thread"]["id"]
    assert len(intake_payload["documentIds"]) == 2
    assert len(intake_payload["knowledgeChunkIds"]) == 2

    actor = await db_session.get(User, auth_context.user_id)
    assert actor is not None
    actor.is_internal_staff = True
    actor.is_platform_admin = True
    actor.platform_role_package = "platform_owner"
    actor.platform_access_active = True
    await db_session.flush()

    areas = await client.get("/api/v1/klm/areas")
    assert areas.status_code == 200, areas.text
    health_area = next(
        area for area in areas.json() if area["areaKey"] == "saude_suplementar"
    )
    unlocked = await client.patch(
        f"/api/v1/klm/areas/{health_area['id']}/activation",
        json={"enabled": True, "forceOverPlan": False},
    )
    assert unlocked.status_code == 200, unlocked.text
    assert unlocked.json()["isActive"] is True

    team = await client.post(
        "/api/v1/klm/agents/simulations/health-intake",
        json={
            "matterId": matter_id,
            "threadId": thread_id,
            "idempotencyKey": "journey-health-team-001",
        },
    )
    assert team.status_code == 201, team.text
    team_payload = team.json()
    assert team_payload["runtime"] == "local_simulator"
    assert team_payload["externalActionsExecuted"] == 0
    assert len(team_payload["runs"]) == 6
    assert [run["agentName"] for run in team_payload["runs"]] == [
        "orquestrador",
        "sdr",
        "juridico",
        "crm",
        "secretaria",
        "orquestrador",
    ]
    assert team_payload["approvalId"] is None
    team_handoff = team_payload["runs"][-1]
    assert team_handoff["agentName"] == "orquestrador"
    assert team_handoff["status"] == "waiting_human"
    assert team_handoff["outputPayload"]["humanQueue"]["queue"] == "human"
    assert team_handoff["outputPayload"]["humanQueue"]["source"] == "orquestrador"

    documents_before_petition = int(
        await db_session.scalar(
            select(func.count(Document.id)).where(Document.matter_id == matter_id)
        )
        or 0
    )
    petition = await client.post(
        "/api/v1/klm/agents/runs",
        json={
            "agentKey": "peticao",
            "matterId": matter_id,
            "threadId": thread_id,
            "requestHuman": True,
            "instructions": (
                "Preparar escopo da petição e encaminhar ao Orquestrador "
                "para a cadeia de decisão humana."
            ),
            "idempotencyKey": "journey-health-petition-001",
        },
    )
    assert petition.status_code == 201, petition.text
    petition_payload = petition.json()
    assert [run["agentName"] for run in petition_payload["runs"]] == [
        "peticao",
        "orquestrador",
    ]
    assert "hermes" not in {
        run["agentName"] for run in team_payload["runs"] + petition_payload["runs"]
    }
    assert petition_payload["approvalId"] is None
    assert petition_payload["runs"][-1]["status"] == "waiting_human"
    assert (
        petition_payload["runs"][-1]["outputPayload"]["humanQueue"]["source"]
        == "orquestrador"
    )
    assert petition_payload["externalActionsExecuted"] == 0
    assert petition_payload["runs"][0]["outputPayload"]["guardrails"]["neverProtocols"] is True
    documents_after_petition = int(
        await db_session.scalar(
            select(func.count(Document.id)).where(Document.matter_id == matter_id)
        )
        or 0
    )
    assert documents_after_petition == documents_before_petition == 2

    meeting = await client.post(
        "/api/v1/meetings",
        headers={"Idempotency-Key": f"meeting-health-journey-{uuid4().hex}"},
        json={
            "matterId": matter_id,
            "title": "Primeira reunião online com o advogado",
            "startsAt": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "location": "Google Meet",
            "meetingUrl": "https://meet.google.com/klm-local-preview",
        },
    )
    assert meeting.status_code == 201, meeting.text
    assert meeting.json()["meetingUrl"].startswith("https://meet.google.com/")

    intent = await client.post(
        "/api/v1/klm/external-intents",
        json={
            "matterId": matter_id,
            "actionType": "send_contract_and_power_of_attorney",
            "provider": "zapsign",
            "idempotencyKey": "journey-health-contract-001",
            "payload": {"documentState": "local_draft"},
        },
    )
    assert intent.status_code == 201, intent.text
    assert intent.json()["status"] == "blocked_external"
    approved_intent = await client.post(
        f"/api/v1/klm/external-intents/{intent.json()['id']}/approve-local"
    )
    assert approved_intent.status_code == 200
    assert approved_intent.json()["status"] == "approved_local"
    assert "continua bloqueada" in approved_intent.json()["reason"]

    retrieved = await KnowledgeService(db_session).retrieve_relevant_chunks(
        workspace_id=intake_payload["thread"]["workspaceId"],
        matter_id=matter_id,
        query_text="negativa prescrição medicamento urgência",
        context_mode="full",
        use_rag=True,
        multimodal_enabled=True,
        limit=20,
    )
    assert retrieved
    retrieved_sources = {chunk.source_type for chunk in retrieved}
    assert "email_attachment" in retrieved_sources
    assert "agent_run" not in retrieved_sources
    assert retrieved[0].source_type == "email_attachment"
    all_sources = set(
        (
            await db_session.execute(
                select(KnowledgeChunk.source_type).where(
                    KnowledgeChunk.matter_id == matter_id
                )
            )
        ).scalars()
    )
    assert "agent_run" in all_sources

    assert int(
        await db_session.scalar(
            select(func.count(AgentRun.id)).where(AgentRun.matter_id == matter_id)
        )
        or 0
    ) == 8
    assert int(
        await db_session.scalar(
            select(func.count(Meeting.id)).where(Meeting.matter_id == matter_id)
        )
        or 0
    ) == 1
    follow_up = await db_session.scalar(
        select(FollowUpTask).where(FollowUpTask.matter_id == matter_id)
    )
    assert follow_up is not None
    assert follow_up.status == FollowUpStatus.scheduled
    persisted_intent = await db_session.get(ExternalActionIntent, intent.json()["id"])
    assert persisted_intent is not None
    assert persisted_intent.status == ExternalActionIntentStatus.approved_local

    messages = list(
        (
            await db_session.execute(
                select(OmnichannelMessage).where(
                    OmnichannelMessage.matter_id == matter_id
                )
            )
        ).scalars()
    )
    assert messages
    assert all(message.direction != MessageDirection.outbound for message in messages)
    assert all(
        message.delivery_status
        in {MessageDeliveryStatus.received, MessageDeliveryStatus.local_draft}
        for message in messages
    )
