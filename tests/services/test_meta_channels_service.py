"""Testes do MetaChannelsService — elegibilidade server-side de campanhas
Messenger/Instagram e capability matrix honesta.

Origem: docs/PLANO_META_MAXIMO_2026-07-23.md seção 5 (Fase 4) item 3 e 5;
docs/AUDITORIA_META_CANAIS_2026-07-23.md achados Messenger (P1-1 broadcast sem
lastro, campanhas com compliance autodeclarado, capability matrix all-true).
"""
from __future__ import annotations

import pytest
pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
async def test_messenger_campaign_policy_summary_ignores_client_numbers_and_computes_from_inbound(
    client, db_session
):
    connection_response = await client.post(
        "/api/v1/messenger/connections",
        json={
            "accountName": "Página Elegibilidade",
            "username": "klmadvogados.elegibilidade",
            "externalAccountId": "page-elig-001",
            "pageId": "1450777",
            "scopes": ["pages_messaging"],
            "status": "connected",
            "webhookRegistered": True,
        },
    )
    assert connection_response.status_code == 201, connection_response.text
    connection_id = connection_response.json()["id"]

    # Conversa com inbound recente (dentro da janela de 24h) => elegível.
    eligible_conversation = await client.post(
        "/api/v1/messenger/conversations",
        json={
            "connectionId": connection_id,
            "externalThreadId": "elig-thread-001",
            "externalParticipantId": "psid-elig-1",
            "participantName": "Lead dentro da janela",
            "eventType": "direct_message",
            "initialMessage": "Acabei de mandar mensagem.",
        },
    )
    assert eligible_conversation.status_code == 201, eligible_conversation.text

    # Conversa sem nenhuma mensagem inbound => bloqueada (gatilho-first).
    blocked_conversation = await client.post(
        "/api/v1/messenger/conversations",
        json={
            "connectionId": connection_id,
            "externalThreadId": "blocked-thread-001",
            "externalParticipantId": "psid-blocked-1",
            "participantName": "Lead sem inbound",
            "eventType": "direct_message",
        },
    )
    assert blocked_conversation.status_code == 201, blocked_conversation.text

    campaign_response = await client.post(
        "/api/v1/messenger/campaigns",
        json={
            "connectionId": connection_id,
            "title": "Reengajamento com números forjados pelo cliente",
            "audienceFilter": {"estimatedRecipients": 999},
            # Números digitados à mão: devem ser ignorados pelo backend.
            "policySummary": {"eligibleRecipients": 999, "blockedRecipients": 0},
        },
    )
    assert campaign_response.status_code == 201, campaign_response.text
    payload = campaign_response.json()

    assert payload["eligibleRecipients"] == 1
    assert payload["blockedRecipients"] == 1
    assert payload["policySummary"]["eligibleRecipients"] == 1
    assert payload["policySummary"]["blockedRecipients"] == 1
    assert payload["policySummary"]["computedAt"] is not None
    assert payload["policySummary"]["windowHours"] == 24
    assert payload["status"] == "draft"


@pytest.mark.asyncio
async def test_messenger_campaign_cannot_transition_to_running_or_scheduled(client):
    connection_response = await client.post(
        "/api/v1/messenger/connections",
        json={
            "accountName": "Página Gate",
            "username": "klmadvogados.gate",
            "externalAccountId": "page-gate-001",
            "pageId": "1450888",
            "scopes": ["pages_messaging"],
            "status": "connected",
            "webhookRegistered": True,
        },
    )
    assert connection_response.status_code == 201, connection_response.text
    connection_id = connection_response.json()["id"]

    campaign_response = await client.post(
        "/api/v1/messenger/campaigns",
        json={
            "connectionId": connection_id,
            "title": "Campanha sem transporte",
            "audienceFilter": {"estimatedRecipients": 3},
        },
    )
    assert campaign_response.status_code == 201, campaign_response.text
    campaign_id = campaign_response.json()["id"]
    assert campaign_response.json()["status"] == "draft"

    running_response = await client.patch(
        f"/api/v1/messenger/campaigns/{campaign_id}",
        json={"status": "running"},
    )
    assert running_response.status_code == 409, running_response.text
    assert "transporte orgânico de broadcast" in running_response.json()["detail"]

    scheduled_response = await client.patch(
        f"/api/v1/messenger/campaigns/{campaign_id}",
        json={"status": "scheduled"},
    )
    assert scheduled_response.status_code == 409, scheduled_response.text
    assert "transporte orgânico de broadcast" in scheduled_response.json()["detail"]

    # Status que não prometem disparo automático continuam livres.
    paused_response = await client.patch(
        f"/api/v1/messenger/campaigns/{campaign_id}",
        json={"status": "paused"},
    )
    assert paused_response.status_code == 200, paused_response.text
    assert paused_response.json()["status"] == "paused"


@pytest.mark.asyncio
async def test_messenger_campaign_create_with_scheduled_at_is_blocked(client):
    connection_response = await client.post(
        "/api/v1/messenger/connections",
        json={
            "accountName": "Página Agendada",
            "username": "klmadvogados.agendada",
            "externalAccountId": "page-agendada-001",
            "pageId": "1450999",
            "scopes": ["pages_messaging"],
            "status": "connected",
            "webhookRegistered": True,
        },
    )
    assert connection_response.status_code == 201, connection_response.text
    connection_id = connection_response.json()["id"]

    campaign_response = await client.post(
        "/api/v1/messenger/campaigns",
        json={
            "connectionId": connection_id,
            "title": "Campanha agendada sem executor",
            "audienceFilter": {"estimatedRecipients": 3},
            "scheduledAt": "2026-08-01T12:00:00Z",
        },
    )
    assert campaign_response.status_code == 409, campaign_response.text
    assert "transporte orgânico de broadcast" in campaign_response.json()["detail"]


@pytest.mark.asyncio
async def test_new_messenger_connection_capability_matrix_is_unverified(client):
    connection_response = await client.post(
        "/api/v1/messenger/connections",
        json={
            "accountName": "Página Nova",
            "username": "klmadvogados.nova",
            "externalAccountId": "page-nova-001",
            "pageId": "1451000",
            "scopes": ["pages_messaging"],
        },
    )
    assert connection_response.status_code == 201, connection_response.text
    matrix = connection_response.json()["capabilityMatrix"]

    # Nenhuma chave foi removida (diretriz 8: re-rotular, nunca apagar).
    assert set(matrix.keys()) == {
        "pageMessaging",
        "pageComments",
        "menus",
        "icebreakers",
        "postbacks",
        "entryPoints",
        "linksInText",
    }
    for key, capability in matrix.items():
        if key == "linksInText":
            assert capability["supported"] is True
        else:
            assert capability["supported"] is False
            assert "não verificada" in capability["reason"]


@pytest.mark.asyncio
async def test_new_instagram_connection_capability_matrix_is_unverified(client):
    connection_response = await client.post(
        "/api/v1/instagram/connections",
        json={
            "accountName": "Perfil Novo",
            "username": "@klmadvogados.novo",
            "externalAccountId": "ig-nova-001",
            "instagramAccountId": "17899999",
        },
    )
    assert connection_response.status_code == 201, connection_response.text
    matrix = connection_response.json()["capabilityMatrix"]

    assert set(matrix.keys()) == {
        "directMessages",
        "comments",
        "storyReplies",
        "mentions",
        "followSignal",
        "entryPoints",
        "linksInText",
        "icebreakers",
    }
    assert matrix["linksInText"]["supported"] is True
    assert matrix["directMessages"]["supported"] is False
