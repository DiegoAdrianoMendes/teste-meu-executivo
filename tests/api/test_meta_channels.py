from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ai.base import AICompletionResult
from app.ai.factory import TaskComplexity
from app.models.client import Client
from app.models.matter import KanbanColumnId, Matter, MicroStatus, PracticeArea
from app.models.meta_channels import MetaApprovedMessage, MetaAutomationRule, MetaChannelCampaign, MetaChannelConnection, MetaChannelType


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
async def test_instagram_suite_supports_connections_threads_drawer_and_rules(client, db_session, auth_context):
    matter_client = Client(
        workspace_id=auth_context.workspace_id,
        name="Lead Instagram",
        email="lead-instagram@example.com",
        phone="+5511910101010",
    )
    db_session.add(matter_client)
    await db_session.flush()

    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=matter_client.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="DM sobre horas extras",
        column_id=KanbanColumnId.qualificacao,
        micro_status=MicroStatus.qualificado,
    )
    db_session.add(matter)
    await db_session.commit()

    connection_response = await client.post(
        "/api/v1/instagram/connections",
        json={
            "accountName": "Perfil Oficial",
            "username": "@klmadvogados",
            "externalAccountId": "ig-001",
            "instagramAccountId": "17890001",
            "scopes": ["instagram_basic", "instagram_manage_messages"],
            "status": "connected",
            "webhookRegistered": True,
        },
    )
    assert connection_response.status_code == 201, connection_response.text
    assert connection_response.json()["status"] == "pending"
    assert connection_response.json()["webhookRegistered"] is False
    connection_id = connection_response.json()["id"]

    approved_response = await client.post(
        "/api/v1/instagram/approved-messages",
        json={
            "messageKind": "direct_message",
            "title": "Boas-vindas IG",
            "body": "Olá! Recebemos sua DM e podemos seguir por aqui.",
            "status": "approved",
            "requiresFollowing": True,
        },
    )
    assert approved_response.status_code == 201, approved_response.text
    approved_id = approved_response.json()["id"]

    rule_response = await client.post(
        "/api/v1/instagram/automation-rules",
        json={
            "title": "Keyword comentário",
            "eventType": "keyword_comment",
            "keyword": "quero",
            "approvedMessageId": approved_id,
            "actionConfig": {
                "targetQueueStatus": "human",
                "targetColumnId": "qualificacao",
                "requiresFollowing": True,
            },
        },
    )
    assert rule_response.status_code == 201, rule_response.text

    conversation_response = await client.post(
        "/api/v1/instagram/conversations",
        json={
            "connectionId": connection_id,
            "matterId": str(matter.id),
            "externalThreadId": "ig-thread-001",
            "externalParticipantId": "ig-user-77",
            "participantName": "Maria Lead",
            "participantHandle": "@maria.lead",
            "eventType": "post_comment",
            "sourceTitle": "Post de captação",
            "sourceUrl": "https://instagram.com/p/demo",
            "initialMessage": "Tenho interesse",
            "isPublicThread": True,
        },
    )
    assert conversation_response.status_code == 201, conversation_response.text
    conversation_id = conversation_response.json()["id"]

    follow_response = await client.post(
        f"/api/v1/instagram/conversations/{conversation_id}/follow-status",
        json={"status": "following"},
    )
    assert follow_response.status_code == 200, follow_response.text
    assert follow_response.json()["followStatus"] == "following"

    note_response = await client.post(
        f"/api/v1/instagram/conversations/{conversation_id}/notes",
        json={"body": "Lead com intenção forte."},
    )
    assert note_response.status_code == 201, note_response.text

    drawer_response = await client.get(f"/api/v1/instagram/conversations/{conversation_id}/drawer")
    assert drawer_response.status_code == 200, drawer_response.text
    drawer_payload = drawer_response.json()
    assert drawer_payload["matter"]["id"] == str(matter.id)
    assert drawer_payload["conversation"]["sourceTitle"] == "Post de captação"
    assert drawer_payload["notes"][0]["body"] == "Lead com intenção forte."

    await client.post(
        f"/api/v1/instagram/conversations/{conversation_id}/move-stage",
        json={"columnId": "proposta_fechamento", "microStatus": "proposta_enviada"},
    )

    await client.put(
        f"/api/v1/instagram/conversations/{conversation_id}/matter",
        json={"nextAction": "Enviar proposta comercial ainda hoje"},
    )

    send_response = await client.post(
        f"/api/v1/instagram/conversations/{conversation_id}/messages",
        json={"body": "Vamos continuar por DM com um resumo objetivo.", "isPublicReply": False},
    )
    assert send_response.status_code == 200, send_response.text
    assert send_response.json()["messages"][-1]["body"] == "Vamos continuar por DM com um resumo objetivo."
    assert send_response.json()["messages"][-1]["status"] == "pending"
    assert send_response.json()["messages"][-1]["messageMetadata"]["externalActionsExecuted"] == 0

    comments_response = await client.get("/api/v1/instagram/comments")
    assert comments_response.status_code == 200
    assert comments_response.json()[0]["conversationId"] == conversation_id

    campaign_response = await client.post(
        "/api/v1/instagram/campaigns",
        json={
            "connectionId": connection_id,
            "approvedMessageId": approved_id,
            "title": "Reengajar leads do Instagram",
            "description": "Campanha channel-aware",
            "audienceFilter": {"estimatedRecipients": 12},
            "policySummary": {"eligibleRecipients": 8, "blockedRecipients": 4},
        },
    )
    assert campaign_response.status_code == 201, campaign_response.text

    connection_count = (await db_session.execute(select(MetaChannelConnection))).scalars().all()
    assert len(connection_count) == 1
    assert connection_count[0].channel_type == MetaChannelType.instagram
    assert (await db_session.execute(select(MetaApprovedMessage))).scalars().first() is not None
    assert (await db_session.execute(select(MetaAutomationRule))).scalars().first() is not None
    assert (await db_session.execute(select(MetaChannelCampaign))).scalars().first() is not None


@pytest.mark.asyncio
async def test_messenger_suite_exposes_page_specific_threads_and_campaigns(client, db_session, auth_context):
    connection_response = await client.post(
        "/api/v1/messenger/connections",
        json={
            "accountName": "Página Oficial",
            "username": "klmadvogados.page",
            "externalAccountId": "page-001",
            "pageId": "1450001",
            "scopes": ["pages_manage_metadata", "pages_messaging"],
            "status": "connected",
            "webhookRegistered": True,
        },
    )
    assert connection_response.status_code == 201, connection_response.text
    connection_id = connection_response.json()["id"]

    conversation_response = await client.post(
        "/api/v1/messenger/conversations",
        json={
            "connectionId": connection_id,
            "externalThreadId": "msg-thread-001",
            "externalParticipantId": "psid-44",
            "participantName": "Lead da Page",
            "participantHandle": "psid-44",
            "eventType": "page_comment",
            "sourceTitle": "Comentário em post da página",
            "initialMessage": "Tenho interesse em entender melhor.",
            "isPublicThread": True,
        },
    )
    assert conversation_response.status_code == 201, conversation_response.text
    conversation_id = conversation_response.json()["id"]

    approved_response = await client.post(
        "/api/v1/messenger/approved-messages",
        json={
            "messageKind": "entry_point",
            "title": "Entrada menu principal",
            "body": "Escolha a trilha ideal para seu caso.",
            "status": "approved",
        },
    )
    assert approved_response.status_code == 201, approved_response.text
    approved_id = approved_response.json()["id"]

    rule_response = await client.post(
        "/api/v1/messenger/automation-rules",
        json={
            "title": "Entrada via page comment",
            "eventType": "page_comment",
            "approvedMessageId": approved_id,
            "actionConfig": {"targetQueueStatus": "human", "targetColumnId": "entrada"},
        },
    )
    assert rule_response.status_code == 201, rule_response.text

    comments_response = await client.get("/api/v1/messenger/comments")
    assert comments_response.status_code == 200, comments_response.text
    assert comments_response.json()[0]["eventType"] == "page_comment"

    send_response = await client.post(
        f"/api/v1/messenger/conversations/{conversation_id}/messages",
        json={"body": "Respondendo pela Page com CTA oficial.", "isPublicReply": True},
    )
    assert send_response.status_code == 200, send_response.text
    assert send_response.json()["messages"][-1]["isPublicReply"] is True

    campaign_response = await client.post(
        "/api/v1/messenger/campaigns",
        json={
            "connectionId": connection_id,
            "approvedMessageId": approved_id,
            "title": "Reengajar page comments",
            "audienceFilter": {"estimatedRecipients": 6},
            "policySummary": {"eligibleRecipients": 6, "blockedRecipients": 0},
        },
    )
    assert campaign_response.status_code == 201, campaign_response.text

    campaigns_list = await client.get("/api/v1/messenger/campaigns")
    assert campaigns_list.status_code == 200, campaigns_list.text
    assert campaigns_list.json()[0]["title"] == "Reengajar page comments"


@pytest.mark.asyncio
async def test_instagram_copilot_returns_ai_suggestions(client, monkeypatch):
    class FakeProvider:
        async def complete(self, *args, **kwargs):
            return AICompletionResult(
                text='{"summary":"Lead veio de comentário com intenção comercial.","recommended_action":"Responder por DM e puxar para qualificação humana.","reply_suggestions":["Oi! Posso te chamar no direct para entender melhor seu caso?","Perfeito, vou te responder por DM com o próximo passo.","Consigo te orientar por aqui e já encaminhar para atendimento humano."],"suggested_public_reply":false}',
                model_used="google/gemma3@gemma-3-27b-it",
                provider="vertex_gemma",
                cost_usd=0.01,
            )

    monkeypatch.setattr("app.services.meta_ai_service.get_provider_for_task", lambda complexity: FakeProvider())

    connection_response = await client.post(
        "/api/v1/instagram/connections",
        json={
            "accountName": "Perfil IA",
            "username": "@klmadvogados.ai",
            "externalAccountId": "ig-ai-001",
            "instagramAccountId": "178955501",
            "scopes": ["instagram_basic", "instagram_manage_messages"],
            "status": "connected",
            "webhookRegistered": True,
        },
    )
    assert connection_response.status_code == 201, connection_response.text

    conversation_response = await client.post(
        "/api/v1/instagram/conversations",
        json={
            "connectionId": connection_response.json()["id"],
            "externalThreadId": "ig-ai-thread-001",
            "externalParticipantId": "ig-ai-user-001",
            "participantName": "Lead Copilot",
            "participantHandle": "@lead.copilot",
            "eventType": "direct_message",
            "sourceTitle": "DM orgânica",
            "initialMessage": "Oi, queria saber como funciona o atendimento.",
            "isPublicThread": False,
        },
    )
    assert conversation_response.status_code == 201, conversation_response.text
    conversation_id = conversation_response.json()["id"]

    copilot_response = await client.post(
        f"/api/v1/instagram/conversations/{conversation_id}/copilot",
        json={"tone": "commercial", "goal": "qualificar e encaminhar para humano"},
    )
    assert copilot_response.status_code == 200, copilot_response.text
    payload = copilot_response.json()
    assert payload["provider"] == "vertex_gemma"
    assert payload["modelUsed"] == "google/gemma3@gemma-3-27b-it"
    assert payload["recommendedAction"] == "Responder por DM e puxar para qualificação humana."
    assert len(payload["replySuggestions"]) == 3
    assert payload["suggestedPublicReply"] is False


@pytest.mark.asyncio
async def test_instagram_copilot_falls_back_when_medium_model_fails(client, monkeypatch):
    class FailingProvider:
        async def complete(self, *args, **kwargs):
            raise RuntimeError("model not available")

    class FallbackProvider:
        async def complete(self, *args, **kwargs):
            return AICompletionResult(
                text='{"summary":"Fallback aplicado com sucesso.","recommended_action":"Responder e seguir com triagem manual.","reply_suggestions":["Posso te explicar por aqui.","Se preferir, seguimos com o próximo passo agora.","Vou te orientar com uma resposta objetiva."],"suggested_public_reply":false}',
                model_used="google/gemma3@gemma-3-4b-it",
                provider="vertex_gemma",
                cost_usd=0.005,
            )

    def provider_for_task(complexity):
        if complexity == TaskComplexity.MEDIUM:
            return FailingProvider()
        return FallbackProvider()

    def model_for_task(complexity):
        if complexity == TaskComplexity.MEDIUM:
            return "google/gemma3@gemma-3-27b-it"
        if complexity == TaskComplexity.SIMPLE:
            return "google/gemma3@gemma-3-4b-it"
        return "gemini-2.5-flash-lite"

    monkeypatch.setattr("app.services.meta_ai_service.get_provider_for_task", provider_for_task)
    monkeypatch.setattr("app.services.meta_ai_service.get_model_for_task", model_for_task)

    connection_response = await client.post(
        "/api/v1/instagram/connections",
        json={
            "accountName": "Perfil Fallback",
            "username": "@klmadvogados.fallback",
            "externalAccountId": "ig-fallback-001",
            "instagramAccountId": "178955502",
            "scopes": ["instagram_basic", "instagram_manage_messages"],
            "status": "connected",
            "webhookRegistered": True,
        },
    )
    assert connection_response.status_code == 201, connection_response.text

    conversation_response = await client.post(
        "/api/v1/instagram/conversations",
        json={
            "connectionId": connection_response.json()["id"],
            "externalThreadId": "ig-fallback-thread-001",
            "externalParticipantId": "ig-fallback-user-001",
            "participantName": "Lead Fallback",
            "participantHandle": "@lead.fallback",
            "eventType": "direct_message",
            "sourceTitle": "DM fallback",
            "initialMessage": "Oi, preciso entender melhor o fluxo.",
            "isPublicThread": False,
        },
    )
    assert conversation_response.status_code == 201, conversation_response.text
    conversation_id = conversation_response.json()["id"]

    copilot_response = await client.post(
        f"/api/v1/instagram/conversations/{conversation_id}/copilot",
        json={"tone": "clear", "goal": "responder sem falhar quando o modelo médio estiver indisponível"},
    )
    assert copilot_response.status_code == 200, copilot_response.text
    payload = copilot_response.json()
    assert payload["modelUsed"] == "google/gemma3@gemma-3-4b-it"
    assert payload["provider"] == "vertex_gemma"
    assert payload["summary"] == "Fallback aplicado com sucesso."


@pytest.mark.asyncio
async def test_messenger_ai_automation_generates_outbound_reply_on_matching_inbound(client, monkeypatch):
    class FakeProvider:
        async def complete(self, *args, **kwargs):
            return AICompletionResult(
                text="Perfeito. Posso seguir com você por aqui e já encaminhar o próximo passo com o time humano.",
                model_used="google/gemma3@gemma-3-4b-it",
                provider="vertex_gemma",
                cost_usd=0.02,
            )

    monkeypatch.setattr("app.services.meta_ai_service.get_provider_for_task", lambda complexity: FakeProvider())
    monkeypatch.setattr(
        "app.services.meta_channels_service.external_delivery_allowed",
        lambda: False,
    )

    connection_response = await client.post(
        "/api/v1/messenger/connections",
        json={
            "accountName": "Página IA",
            "username": "klmadvogados.page.ai",
            "externalAccountId": "page-ai-001",
            "pageId": "1450999",
            "scopes": ["pages_manage_metadata", "pages_messaging"],
            "status": "connected",
            "webhookRegistered": True,
        },
    )
    assert connection_response.status_code == 201, connection_response.text

    approved_response = await client.post(
        "/api/v1/messenger/approved-messages",
        json={
            "messageKind": "direct_message",
            "title": "Resposta humana de referência",
            "body": "Recebemos sua mensagem e vamos seguir por aqui.",
            "status": "approved",
        },
    )
    assert approved_response.status_code == 201, approved_response.text

    rule_response = await client.post(
        "/api/v1/messenger/automation-rules",
        json={
            "title": "IA page comment",
            "eventType": "page_comment",
            "approvedMessageId": approved_response.json()["id"],
            "actionConfig": {
                "targetQueueStatus": "human",
                "targetColumnId": "entrada",
                "aiEnabled": True,
                "aiTone": "commercial",
                "aiGoal": "qualificar e puxar para atendimento humano",
                "publicReply": True,
            },
        },
    )
    assert rule_response.status_code == 201, rule_response.text

    conversation_response = await client.post(
        "/api/v1/messenger/conversations",
        json={
            "connectionId": connection_response.json()["id"],
            "externalThreadId": "page-ai-thread-001",
            "externalParticipantId": "psid-ai-001",
            "participantName": "Lead Automação IA",
            "participantHandle": "psid-ai-001",
            "eventType": "page_comment",
            "sourceTitle": "Comentário no post institucional",
            "initialMessage": "Tenho interesse e queria entender melhor.",
            "isPublicThread": True,
        },
    )
    assert conversation_response.status_code == 201, conversation_response.text
    payload = conversation_response.json()
    assert payload["queueStatus"] == "human"
    assert payload["humanControlState"] == "copilot"
    assert len(payload["messages"]) == 2
    outbound = payload["messages"][-1]
    assert outbound["direction"] == "outbound"
    assert outbound["status"] == "pending"
    assert outbound["authorName"] == "IA Messenger"
    assert outbound["isPublicReply"] is True
    assert outbound["messageMetadata"]["aiGenerated"] is True
    assert outbound["messageMetadata"]["provider"] == "vertex_gemma"
    assert outbound["messageMetadata"]["deliveryStatus"] == "local_draft"
    assert outbound["messageMetadata"]["externalActionStatus"] == "blocked"
    assert outbound["messageMetadata"]["externalGate"] == "closed"
    assert outbound["messageMetadata"]["blockedReason"] == "klm_external_gate_closed"
    assert outbound["messageMetadata"]["externalActionsExecuted"] == 0
    assert "encaminhar o próximo passo" in outbound["body"]
