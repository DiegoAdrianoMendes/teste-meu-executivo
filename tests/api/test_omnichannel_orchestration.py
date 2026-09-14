"""Contracts for local-only provider setup, social actions and live flow traces."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.models.omnichannel_v6 import (
    OmnichannelActionIntent,
    OmnichannelAutomationRun,
    OmnichannelAutomationRunEvent,
    OmnichannelChannelConnection,
)


pytestmark = pytest.mark.usefixtures("active_subscription")


def _connection(
    *,
    channel: str,
    provider: str,
    connection_key: str,
    display_name: str,
    transport_mode: str = "official_api",
    configuration: dict | None = None,
    is_default: bool = False,
) -> dict:
    return {
        "channel": channel,
        "provider": provider,
        "connectionKey": connection_key,
        "displayName": display_name,
        "transportMode": transport_mode,
        "configuration": configuration or {},
        "credentialReference": f"vault://local/{connection_key}",
        "isDefault": is_default,
    }


def _public_comment(
    *,
    external_event_id: str,
    body: str = "Preciso deste remédio",
    metadata: dict | None = None,
    payload: dict | None = None,
) -> dict:
    return {
        "provider": "meta_instagram",
        "connectionKey": "instagram:principal",
        "channel": "instagram",
        "eventType": "post_comment",
        "externalEventId": external_event_id,
        "externalThreadId": "ig-post-123:comment-thread-ana",
        "externalUserId": "ig-user-ana",
        "externalAccountId": "ig-account-klm",
        "displayName": "Ana Paula",
        "address": "@anapaula",
        "canonicalPhone": "+5511999990000",
        "direction": "inbound",
        "authorType": "client",
        "authorLabel": "Ana Paula",
        "body": body,
        "visibility": "public",
        "publicUrl": "https://www.instagram.com/p/fixture/comment/123",
        "actionability": "reply_required",
        "correlation": {"postId": "ig-post-123", "commentId": external_event_id},
        "metadata": metadata or {},
        "payload": payload or {},
    }


@pytest.mark.asyncio
async def test_whatsapp_coexistence_multi_number_and_local_gate(client, db_session):
    principal_payload = _connection(
        channel="whatsapp",
        provider="meta_cloud",
        connection_key="whatsapp:principal",
        display_name="WhatsApp principal",
        transport_mode="coexistence",
        configuration={
            "phoneNumberId": "phone-001",
            "wabaId": "waba-001",
            "businessAccountId": "business-001",
        },
        is_default=True,
    )
    principal_payload["costPolicy"] = {
        "dailyBudgetBrl": 500,
        "providerCost": "0.25",
    }
    principal = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json=principal_payload,
    )
    assert principal.status_code == 201, principal.text
    principal_body = principal.json()
    assert principal_body["transportMode"] == "coexistence"
    assert principal_body["capabilities"]["business_app_simultaneous"]["supported"] is True
    assert "costPolicy" not in principal_body
    stored_principal = await db_session.scalar(
        select(OmnichannelChannelConnection).where(
            OmnichannelChannelConnection.id == UUID(principal_body["id"])
        )
    )
    assert stored_principal is not None
    assert stored_principal.cost_policy == {}
    stored_principal.cost_policy = {
        "currency": "BRL",
        "dailyBudgetBrl": 75,
        "providerCost": "0.10",
    }
    await db_session.flush()

    validation = await client.post(
        f"/api/v1/klm/omnichannel/connections/{principal_body['id']}/validate-local"
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["readyForExternalSetup"] is True
    assert validation.json()["missingConfiguration"] == []
    assert validation.json()["externalGateOpen"] is False
    assert "costPolicy" not in validation.json()["connection"]

    secondary = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json=_connection(
            channel="whatsapp",
            provider="meta_cloud",
            connection_key="whatsapp:secundario",
            display_name="WhatsApp captação",
            transport_mode="cloud_api",
            configuration={
                "phoneNumberId": "phone-002",
                "wabaId": "waba-001",
                "businessAccountId": "business-001",
            },
            is_default=True,
        ),
    )
    assert secondary.status_code == 201, secondary.text

    rows = await client.get("/api/v1/klm/omnichannel/connections?channel=whatsapp")
    assert rows.status_code == 200, rows.text
    by_key = {item["connectionKey"]: item for item in rows.json()}
    assert by_key["whatsapp:principal"]["isDefault"] is False
    assert by_key["whatsapp:secundario"]["isDefault"] is True
    assert "costPolicy" not in by_key["whatsapp:principal"]
    assert stored_principal.cost_policy["dailyBudgetBrl"] == 75

    stale = await client.patch(
        f"/api/v1/klm/omnichannel/connections/{principal_body['id']}",
        json={"expectedVersion": 1, "action": "pause"},
    )
    assert stale.status_code == 409
    assert stale.headers["X-KLM-Current-Version"] == "2"


@pytest.mark.asyncio
async def test_connection_rejects_embedded_secrets_and_marks_tiktok_limits(client):
    secret = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json=_connection(
            channel="instagram",
            provider="meta_instagram",
            connection_key="instagram:unsafe",
            display_name="Instagram inseguro",
            configuration={"pageId": "page-1", "accessToken": "never-store-this"},
        ),
    )
    assert secret.status_code == 409
    assert "credentialReference" in secret.json()["detail"]

    tiktok = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json=_connection(
            channel="tiktok",
            provider="tiktok_business",
            connection_key="tiktok:principal",
            display_name="TikTok principal",
            configuration={"businessAccountId": "tt-business", "advertiserId": "tt-ads"},
        ),
    )
    assert tiktok.status_code == 201, tiktok.text
    tiktok_body = tiktok.json()
    assert tiktok_body["capabilities"]["lead_forms"]["supported"] is True
    assert tiktok_body["capabilities"]["comment_hide"]["supported"] is False
    assert tiktok_body["capabilities"]["comment_delete"]["supported"] is False

    validation = await client.post(
        f"/api/v1/klm/omnichannel/connections/{tiktok_body['id']}/validate-local"
    )
    assert validation.status_code == 200, validation.text
    assert {"comment_hide", "comment_delete"}.issubset(validation.json()["unsupportedCapabilities"])


@pytest.mark.asyncio
async def test_instagram_profile_lookup_is_distinct_from_new_follower_webhook(client):
    response = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json=_connection(
            channel="instagram",
            provider="meta_instagram",
            connection_key="instagram:follow-signals",
            display_name="Instagram sinais sociais",
            configuration={"pageId": "page-follow", "instagramAccountId": "ig-follow"},
        ),
    )

    assert response.status_code == 201, response.text
    capabilities = response.json()["capabilities"]
    assert capabilities["follower_check"]["supported"] is True
    assert capabilities["new_follower_webhook"]["supported"] is False
    assert "inferido como não seguidor" in capabilities["follower_check"]["note"]


@pytest.mark.asyncio
async def test_public_comment_actions_are_tenant_bound_idempotent_and_external_blocked(
    client, db_session
):
    connection = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json=_connection(
            channel="instagram",
            provider="meta_instagram",
            connection_key="instagram:principal",
            display_name="Instagram KLM",
            configuration={"pageId": "page-1", "instagramAccountId": "ig-klm"},
        ),
    )
    assert connection.status_code == 201, connection.text
    event = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json=_public_comment(external_event_id="ig-comment-001"),
    )
    assert event.status_code == 201, event.text
    event_body = event.json()
    action_payload = {
        "eventId": event_body["event"]["id"],
        "connectionId": connection.json()["id"],
        "actionType": "private_reply",
        "targetExternalId": "ig-comment-001",
        "body": "Olá, Ana. Vou entender sua negativa com você por aqui.",
        "idempotencyKey": "social-action-private-001",
    }
    first = await client.post(
        f"/api/v1/klm/omnichannel/threads/{event_body['thread']['id']}/actions",
        json=action_payload,
    )
    replay = await client.post(
        f"/api/v1/klm/omnichannel/threads/{event_body['thread']['id']}/actions",
        json=action_payload,
    )
    changed = await client.post(
        f"/api/v1/klm/omnichannel/threads/{event_body['thread']['id']}/actions",
        json={**action_payload, "body": "Conteúdo divergente."},
    )

    assert first.status_code == 201, first.text
    assert first.json()["state"] == "blocked_external"
    assert first.json()["providerReceipt"] == {"externalRequests": 0, "localOnly": True}
    assert replay.status_code == 201
    assert replay.headers["X-KLM-Deduplicated"] == "true"
    assert replay.json()["id"] == first.json()["id"]
    assert changed.status_code == 409
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelActionIntent)) == 1


@pytest.mark.asyncio
async def test_social_event_inbox_returns_canonical_thread_message_and_connection(client):
    connection = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json=_connection(
            channel="instagram",
            provider="meta_instagram",
            connection_key="instagram:principal",
            display_name="Instagram KLM",
            configuration={"pageId": "page-1", "instagramAccountId": "ig-klm"},
        ),
    )
    assert connection.status_code == 201, connection.text
    created = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json=_public_comment(external_event_id="ig-comment-inbox-001"),
    )
    assert created.status_code == 201, created.text

    inbox = await client.get(
        "/api/v1/klm/omnichannel/events?channel=instagram&visibility=public"
    )
    assert inbox.status_code == 200, inbox.text
    body = inbox.json()
    assert body["hasMore"] is False
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["event"]["externalEventId"] == "ig-comment-inbox-001"
    assert item["thread"]["id"] == created.json()["thread"]["id"]
    assert item["identity"]["displayName"] == "Ana Paula"
    assert item["message"]["body"] == "Preciso deste remédio"
    assert item["connection"]["displayName"] == "Instagram KLM"
    assert item["channel"] == "instagram"


@pytest.mark.asyncio
async def test_keyword_comment_flow_runs_live_trace_without_external_requests(client, db_session):
    event = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json=_public_comment(
            external_event_id="ig-comment-flow-001",
            body="Quero ajuda com este medicamento urgente",
        ),
    )
    assert event.status_code == 201, event.text
    event_body = event.json()
    rule = await client.post(
        "/api/v1/klm/omnichannel/automation-rules",
        json={
            "name": "Comentário medicamento urgente",
            "slug": "comentario-medicamento-urgente",
            "channel": "instagram",
            "triggerType": "keyword_comment",
            "status": "active",
            "coexistWithAi": True,
            "triggerConfig": {
                "keywords": ["medicamento", "urgente"],
                "sourceIds": ["ig-post-123"],
            },
            "conditions": [{"field": "visibility", "operator": "equals", "value": "public"}],
            "actions": [
                {
                    "key": "resposta-publica",
                    "type": "public_reply",
                    "body": "Enviamos uma mensagem para entender seu caso.",
                },
                {
                    "key": "resposta-privada",
                    "type": "private_reply",
                    "body": "Olá. Conte qual medicamento foi negado e quando ocorreu a negativa.",
                    "delaySeconds": 3,
                },
                {"key": "tarefa-sdr", "type": "create_task"},
            ],
        },
    )
    assert rule.status_code == 201, rule.text

    payload = {
        "eventId": event_body["event"]["id"],
        "idempotencyKey": "simulate-comment-flow-001",
    }
    run = await client.post(
        f"/api/v1/klm/omnichannel/automation-rules/{rule.json()['id']}/simulate",
        json=payload,
    )
    replay = await client.post(
        f"/api/v1/klm/omnichannel/automation-rules/{rule.json()['id']}/simulate",
        json=payload,
    )

    assert run.status_code == 200, run.text
    assert run.json()["state"] == "completed_local"
    assert run.json()["result"]["matched"] is True
    assert run.json()["result"]["externalRequests"] == 0
    assert run.json()["result"]["preparedActions"] == 3
    assert run.json()["result"]["coexistWithAi"] is True
    assert [item["state"] for item in run.json()["trace"]] == [
        "matched",
        "blocked_external",
        "blocked_external",
        "simulated_local",
    ]
    assert replay.status_code == 200
    assert replay.headers["X-KLM-Deduplicated"] == "true"
    assert replay.json()["id"] == run.json()["id"]
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelAutomationRun)) == 1
    assert (
        await db_session.scalar(select(func.count()).select_from(OmnichannelAutomationRunEvent))
        == 4
    )


@pytest.mark.asyncio
async def test_keyword_comment_respects_case_mode_and_exclusions(client):
    event = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json=_public_comment(
            external_event_id="ig-comment-case-001",
            body="medicamento negado para emprego",
        ),
    )
    assert event.status_code == 201, event.text
    rule = await client.post(
        "/api/v1/klm/omnichannel/automation-rules",
        json={
            "name": "Comentário com caixa e exclusão",
            "slug": "comentario-caixa-exclusao",
            "channel": "instagram",
            "triggerType": "keyword_comment",
            "status": "active",
            "triggerConfig": {
                "keywords": ["Medicamento"],
                "excludedKeywords": ["currículo"],
                "caseSensitive": True,
                "keywordMode": "contains",
                "match": "any",
            },
            "actions": [{"key": "nota", "type": "create_task"}],
        },
    )
    assert rule.status_code == 201, rule.text

    response = await client.post(
        f"/api/v1/klm/omnichannel/automation-rules/{rule.json()['id']}/simulate",
        json={
            "eventId": event.json()["event"]["id"],
            "idempotencyKey": "simulate-comment-case-001",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "skipped"
    assert response.json()["result"]["matched"] is False
    assert response.json()["trace"][0]["outputSnapshot"]["keyword"] is False


@pytest.mark.asyncio
async def test_follower_filter_never_treats_unknown_as_not_following(client):
    unknown = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json=_public_comment(external_event_id="ig-comment-follower-unknown"),
    )
    known = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json=_public_comment(
            external_event_id="ig-comment-follower-known",
            metadata={"socialProfile": {"followsBusiness": False}},
        ),
    )
    assert unknown.status_code == 201, unknown.text
    assert known.status_code == 201, known.text
    rule = await client.post(
        "/api/v1/klm/omnichannel/automation-rules",
        json={
            "name": "Somente quem ainda não segue",
            "slug": "comentario-nao-seguidor",
            "channel": "instagram",
            "triggerType": "keyword_comment",
            "status": "active",
            "triggerConfig": {"followerStatus": "not_follows"},
            "actions": [{"key": "nota", "type": "create_task"}],
        },
    )
    assert rule.status_code == 201, rule.text

    unknown_run = await client.post(
        f"/api/v1/klm/omnichannel/automation-rules/{rule.json()['id']}/simulate",
        json={
            "eventId": unknown.json()["event"]["id"],
            "idempotencyKey": "simulate-follower-unknown",
        },
    )
    known_run = await client.post(
        f"/api/v1/klm/omnichannel/automation-rules/{rule.json()['id']}/simulate",
        json={
            "eventId": known.json()["event"]["id"],
            "idempotencyKey": "simulate-follower-known",
        },
    )

    assert unknown_run.status_code == 200, unknown_run.text
    assert unknown_run.json()["result"]["matched"] is False
    assert unknown_run.json()["trace"][0]["outputSnapshot"]["followerStatus"] is False
    assert known_run.status_code == 200, known_run.text
    assert known_run.json()["result"]["matched"] is True


@pytest.mark.asyncio
async def test_connection_listing_is_workspace_scoped(client, db_session, auth_context):
    from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus

    foreign_workspace = Workspace(
        name="Workspace externo",
        slug=f"workspace-externo-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    db_session.add(
        OmnichannelChannelConnection(
            workspace_id=foreign_workspace.id,
            channel="whatsapp",
            provider="meta_cloud",
            connection_key="foreign:number",
            display_name="Conexão externa",
        )
    )
    await db_session.flush()

    own = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json=_connection(
            channel="whatsapp",
            provider="meta_cloud",
            connection_key="own:number",
            display_name="Conexão própria",
        ),
    )
    assert own.status_code == 201, own.text
    rows = await client.get("/api/v1/klm/omnichannel/connections")
    assert rows.status_code == 200, rows.text
    assert {item["workspaceId"] for item in rows.json()} == {str(auth_context.workspace_id)}


@pytest.mark.asyncio
async def test_thread_context_unifies_channels_and_copilot_returns_local_evidence(client):
    whatsapp_connection = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json=_connection(
            channel="whatsapp",
            provider="meta_cloud",
            connection_key="meta_cloud:principal",
            display_name="WhatsApp principal",
            transport_mode="coexistence",
            configuration={
                "phoneNumberId": "phone-001",
                "wabaId": "waba-001",
                "businessAccountId": "business-001",
            },
        ),
    )
    instagram_connection = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json=_connection(
            channel="instagram",
            provider="meta_instagram",
            connection_key="instagram:principal",
            display_name="Instagram principal",
            configuration={"pageId": "page-1", "instagramAccountId": "ig-klm"},
        ),
    )
    assert whatsapp_connection.status_code == 201, whatsapp_connection.text
    assert instagram_connection.status_code == 201, instagram_connection.text

    whatsapp_event = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json={
            "provider": "meta_cloud",
            "connectionKey": "meta_cloud:principal",
            "channel": "whatsapp",
            "eventType": "message.received",
            "externalEventId": "wamid-context-001",
            "externalThreadId": "wa-thread-context",
            "externalUserId": "wa-user-context",
            "displayName": "Ana Paula",
            "address": "+5511999990000",
            "canonicalPhone": "+5511999990000",
            "direction": "inbound",
            "authorType": "client",
            "authorLabel": "Ana Paula",
            "body": "Meu plano negou o medicamento. Como posso enviar a negativa?",
            "visibility": "client_private",
            "actionability": "urgent",
        },
    )
    instagram_event = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json=_public_comment(
            external_event_id="ig-context-comment-001",
            body="Sou a Ana do WhatsApp e preciso de ajuda com o medicamento",
        ),
    )
    assert whatsapp_event.status_code == 201, whatsapp_event.text
    assert instagram_event.status_code == 201, instagram_event.text
    assert instagram_event.json()["thread"]["id"] == whatsapp_event.json()["thread"]["id"]
    thread_id = whatsapp_event.json()["thread"]["id"]

    context = await client.get(f"/api/v1/klm/omnichannel/threads/{thread_id}/context")
    assert context.status_code == 200, context.text
    context_body = context.json()
    assert context_body["thread"]["id"] == thread_id
    assert context_body["identity"]["canonicalPhone"] == "+5511999990000"
    assert {row["channel"] for row in context_body["channels"]} == {
        "whatsapp",
        "instagram",
    }
    assert {row["connectionName"] for row in context_body["channels"]} == {
        "WhatsApp principal",
        "Instagram principal",
    }
    assert context_body["externalGateOpen"] is False
    assert len(context_body["timeline"]) >= 4

    copilot = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/copilot",
        json={
            "action": "suggest_reply",
            "tone": "acolhedor",
            "goal": "INSTRUÇÃO INTERNA QUE NÃO PODE CHEGAR AO CLIENTE",
        },
    )
    assert copilot.status_code == 200, copilot.text
    copilot_body = copilot.json()
    assert copilot_body["engine"] == "klm_local_rules_v1"
    assert copilot_body["externalRequests"] == 0
    assert copilot_body["evidenceMessageIds"]
    assert "negativa" in copilot_body["content"].casefold()
    assert "INSTRUÇÃO INTERNA" not in copilot_body["content"]
    assert "Meu plano negou o medicamento. Como posso enviar a negativa?" in copilot_body[
        "unansweredQuestions"
    ]
    assert len(copilot_body["unansweredQuestions"]) == 2
