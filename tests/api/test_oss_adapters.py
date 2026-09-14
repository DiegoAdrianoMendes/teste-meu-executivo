"""Status contract for local-only OSS adapters."""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.api.v1.endpoints import oss_adapters
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    OSSBridgeProvider,
)
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole
from app.services.bottlecrm_operations_service import BottleCRMOperationsService
from app.services.chattermate_flow_service import ChatterMateFlowService
from app.services.chattermate_operations_service import ChatterMateOperationsService
from app.services.chatwoot_operations_service import ChatwootOperationsService
from app.services.chatwoot_sync_service import ChatwootSyncService
from app.services.rocketchat_bridge_service import RocketChatBridgeService
from app.services.rocketchat_operations_service import RocketChatOperationsService


@pytest_asyncio.fixture
async def client(oss_adapter_client):
    yield oss_adapter_client


@pytest_asyncio.fixture
async def unauthenticated_client(oss_unauthenticated_adapter_client):
    yield oss_unauthenticated_adapter_client


@pytest.mark.asyncio
async def test_contextual_launch_resolves_linked_chatwoot_conversation(monkeypatch):
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    thread_id = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")
    monkeypatch.setattr(oss_adapters.settings, "KLM_CHATWOOT_ACCOUNT_ID", "100001")
    monkeypatch.setattr(oss_adapters, "set_workspace_context", AsyncMock())
    monkeypatch.setattr(ChatwootOperationsService, "__init__", lambda self, db: None)
    linked = AsyncMock(return_value="73")
    monkeypatch.setattr(ChatwootOperationsService, "conversation_id", linked)

    destination = await oss_adapters._resolve_contextual_destination(
        {
            "service": "chatwoot",
            "workspaceId": str(workspace_id),
            "context": {
                "capabilityId": "chatwoot.conversation-composer",
                "threadId": str(thread_id),
                "destinationPath": "/app/accounts/100001/dashboard",
            },
        },
        object(),
    )

    assert destination == "/app/accounts/100001/conversations/73"
    linked.assert_awaited_once_with(workspace_id, thread_id)


@pytest.mark.asyncio
async def test_contextual_launch_resolves_linked_rocketchat_room_tab(monkeypatch):
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    thread_id = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")
    monkeypatch.setattr(oss_adapters, "set_workspace_context", AsyncMock())
    monkeypatch.setattr(RocketChatOperationsService, "__init__", lambda self, db: None)
    target = AsyncMock(return_value={"roomId": "room-1", "roomName": "caso saude", "roomType": "p"})
    monkeypatch.setattr(RocketChatOperationsService, "launch_target", target)

    destination = await oss_adapters._resolve_contextual_destination(
        {
            "service": "rocketchat",
            "workspaceId": str(workspace_id),
            "context": {
                "capabilityId": "rocketchat.room-pinned-messages",
                "threadId": str(thread_id),
                "destinationPath": "/home",
            },
        },
        object(),
    )

    assert destination == "/group/caso%20saude/pinned-messages"
    target.assert_awaited_once_with(workspace_id, thread_id)


@pytest.mark.asyncio
async def test_contextual_launch_resolves_synced_chattermate_agent_tab(monkeypatch):
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    monkeypatch.setattr(oss_adapters, "set_workspace_context", AsyncMock())
    monkeypatch.setattr(ChatterMateOperationsService, "__init__", lambda self, db: None)
    agent = AsyncMock(return_value="agent/sdr")
    monkeypatch.setattr(ChatterMateOperationsService, "launch_agent_id", agent)

    destination = await oss_adapters._resolve_contextual_destination(
        {
            "service": "chattermate",
            "workspaceId": str(workspace_id),
            "context": {
                "capabilityId": "chattermate.visual-editor",
                "destinationPath": "/ai-agents?tab=workflow-builder",
            },
        },
        object(),
    )

    assert destination == "/ai-agents?agent=agent%2Fsdr&tab=workflow-builder"
    agent.assert_awaited_once_with(workspace_id)


@pytest.mark.asyncio
async def test_contextual_launch_resolves_exact_chattermate_flow_agent(monkeypatch):
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    flow_id = UUID("8df7bffe-776d-4d33-8810-1aa9eff9fe8a")
    monkeypatch.setattr(oss_adapters, "set_workspace_context", AsyncMock())
    monkeypatch.setattr(ChatterMateFlowService, "__init__", lambda self, db: None)
    agent = AsyncMock(return_value="flow-agent")
    monkeypatch.setattr(ChatterMateFlowService, "flow_agent_id", agent)

    destination = await oss_adapters._resolve_contextual_destination(
        {
            "service": "chattermate",
            "workspaceId": str(workspace_id),
            "context": {
                "capabilityId": "chattermate.visual-editor",
                "flowId": str(flow_id),
                "destinationPath": "/ai-agents?tab=workflow-builder",
            },
        },
        object(),
    )

    assert destination == "/ai-agents?agent=flow-agent&tab=workflow-builder"
    agent.assert_awaited_once_with(workspace_id, flow_id)


@pytest.mark.asyncio
async def test_oss_status_is_local_and_never_claims_external_actions(client):
    response = await client.get("/api/v1/oss-adapters/status")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["mode"] == "local_only"
    assert payload["externalActionsEnabled"] is False
    assert {service["key"] for service in payload["services"]} == {
        "chatwoot",
        "rocketchat",
        "chattermate",
        "bottlecrm",
    }
    assert all(service["sourceCommit"] for service in payload["services"])
    assert all(
        service["localUrl"] is None
        or service["localUrl"].startswith(("http://127.0.0.1", "http://localhost"))
        for service in payload["services"]
    )


@pytest.mark.asyncio
async def test_oss_status_requires_authentication(unauthenticated_client):
    response = await unauthenticated_client.get("/api/v1/oss-adapters/status")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_rocketchat_webhook_rejects_invalid_token(client, auth_context, monkeypatch):
    monkeypatch.setattr(oss_adapters.settings, "KLM_ROCKETCHAT_WEBHOOK_TOKEN", "expected")
    ingest = AsyncMock()
    monkeypatch.setattr(RocketChatBridgeService, "ingest_webhook", ingest)

    response = await client.post(
        f"/api/v1/oss-adapters/rocketchat/webhooks/{auth_context.workspace_id}",
        json={
            "token": "wrong",
            "message_id": "rocket-message-1",
            "channel_id": "room-1",
            "channel_name": "juridico-saude",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Token Rocket.Chat inválido."
    ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_rocketchat_webhook_sets_tenant_context_before_ingest(
    client,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(oss_adapters.settings, "KLM_ROCKETCHAT_WEBHOOK_TOKEN", "expected")
    set_context = AsyncMock()
    monkeypatch.setattr(oss_adapters, "set_workspace_context", set_context)
    captured = {}
    monkeypatch.setattr(RocketChatBridgeService, "__init__", lambda self, db: None)

    async def ingest(self, *, workspace_id, payload):
        captured.update(workspace_id=workspace_id, payload=payload)
        return {"status": "processed", "deduplicated": False}

    monkeypatch.setattr(RocketChatBridgeService, "ingest_webhook", ingest)
    response = await client.post(
        f"/api/v1/oss-adapters/rocketchat/webhooks/{auth_context.workspace_id}",
        json={
            "token": "expected",
            "message_id": "rocket-message-1",
            "channel_id": "room-1",
            "channel_name": "juridico-saude",
            "text": "Nota interna",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "processed"
    set_context.assert_awaited_once()
    assert captured["workspace_id"] == auth_context.workspace_id
    assert captured["payload"]["message_id"] == "rocket-message-1"


@pytest.mark.asyncio
async def test_oss_capability_registry_is_explicit_and_truthful(client):
    response = await client.get("/api/v1/oss-adapters/capabilities")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["scope"] == "pinned-community-feature-families"
    assert payload["summary"]["total"] == len(payload["capabilities"])
    assert payload["summary"]["unclassified"] == 0
    verified_native_ids = set(payload["qaVerification"]["verifiedNativeCapabilityIds"])
    assert payload["summary"]["nativeExperience"] == len(verified_native_ids)
    assert payload["summary"]["nativeExperienceVerified"] == len(verified_native_ids)
    assert payload["summary"]["nativeRoute"] == 135
    assert payload["summary"]["contractEligible"] == 121
    assert 0 <= payload["summary"]["contractVerified"] <= 121
    assert "operationallyVerified" not in payload["summary"]
    assert payload["summary"]["externalEffectVerified"] == 0
    assert payload["summary"]["gatedConfiguration"] == 12
    assert payload["qaVerification"]["manifestPath"] == (
        ".local/qa-evidence/oss-native/oss_native_verification.json"
    )
    assert payload["qaVerification"]["verificationFileCount"] > 800
    assert len(payload["qaVerification"]["runtimeSourceDigest"]) == 64
    assert payload["qaVerification"]["runtimeSourceFileCount"] > 800
    assert "operationalIdsVerified" not in payload["qaVerification"]
    assert {item["provider"] for item in payload["capabilities"]} == {
        "chatwoot",
        "rocketchat",
        "chattermate",
        "bottlecrm",
    }
    assert all(item["disposition"] != "discovered" for item in payload["capabilities"])
    assert all(item["upstreamEvidence"] for item in payload["capabilities"])
    for item in payload["capabilities"]:
        verified = item["id"] in verified_native_ids
        assert item["nativeExperience"] is verified, item["id"]
        assert item["nativeExperienceVerified"] is verified, item["id"]
        if not verified:
            assert item["currentExposure"] == item["sourceExposure"], item["id"]
            assert item["integrationLevel"] == item["sourceExposure"], item["id"]
            assert item["targetRoute"] == item["sourceTargetRoute"], item["id"]
    assert sum(bool(item["contractEligible"]) for item in payload["capabilities"]) == 121
    assert not any(item["externalEffectVerified"] for item in payload["capabilities"])
    public_domains = {
        "chatwoot": "communications",
        "rocketchat": "collaboration",
        "chattermate": "automation",
        "bottlecrm": "crm",
    }
    for item in payload["capabilities"]:
        endpoint = item["commandsEndpoint"]
        expected_enabled = item["provider"] == "chatwoot"
        assert endpoint["enabled"] is expected_enabled, item["id"]
        assert endpoint["state"] == (
            "dispatcher_available" if expected_enabled else "disabled_no_dispatcher"
        ), item["id"]
        assert endpoint["replacementPath"] == (
            "/api/v1/capabilities/domains/"
            f"{public_domains[item['provider']]}/{item['key']}/actions"
        )
        assert item["provider"] not in endpoint["replacementPath"]


@pytest.mark.asyncio
async def test_oss_capability_registry_requires_authentication(unauthenticated_client):
    response = await unauthenticated_client.get("/api/v1/oss-adapters/capabilities")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_oss_launch_accepts_only_contextual_internal_routes(client, monkeypatch):
    captured = {}

    def fake_issue(
        service,
        workspace_id,
        ttl_seconds=60,
        *,
        actor_user_id,
        membership_id,
        actor_role,
        context=None,
    ):
        captured.update(
            service=service,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            membership_id=membership_id,
            actor_role=actor_role,
            context=context,
        )
        return {
            "service": service,
            "launchUrl": "http://127.0.0.1:3300/__klm_session?ticket=local",
            "expiresAt": "2026-07-17T16:00:00Z",
            "embeddable": True,
            "context": {**(context or {}), "destinationPath": "/ai-agents"},
        }

    monkeypatch.setattr(
        "app.api.v1.endpoints.oss_adapters.issue_launch_ticket",
        fake_issue,
    )
    response = await client.post(
        "/api/v1/oss-adapters/services/chattermate/launch",
        json={
            "returnTo": "/app/flows",
            "capabilityId": "chattermate.visual-editor",
            "surface": "Flows",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["context"]["returnTo"] == "/app/flows"
    assert payload["context"]["destinationPath"] == "/ai-agents"
    assert payload["launchUrl"].startswith("http://127.0.0.1:3300/__klm_session")
    assert captured["service"] == "chattermate"
    assert captured["actor_role"] == "workspace_owner"
    assert captured["context"]["capabilityId"] == "chattermate.visual-editor"

    rejected = await client.post(
        "/api/v1/oss-adapters/services/chattermate/launch",
        json={"returnTo": "https://example.test", "capabilityId": "chattermate.visual-editor"},
    )
    assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_oss_launch_rejects_non_admin_even_with_integration_grant(
    client,
    auth_context,
    db_session,
    monkeypatch,
):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    assert membership is not None
    membership.role = WorkspaceRole.finance_manager
    membership.permission_grants = [
        {"module": "integrations", "action": "manage", "scope": "workspace"}
    ]
    auth_context.role = WorkspaceRole.finance_manager
    await db_session.flush()
    issued = False

    def fake_issue(*_args, **_kwargs):
        nonlocal issued
        issued = True
        raise AssertionError("Non-admin launch must fail before ticket issuance")

    monkeypatch.setattr(oss_adapters, "issue_launch_ticket", fake_issue)
    response = await client.post(
        "/api/v1/oss-adapters/services/chatwoot/launch",
        json={"returnTo": "/app/conversas"},
    )

    assert response.status_code == 403
    assert "owners e administradores" in response.json()["detail"]
    assert issued is False


@pytest.mark.asyncio
async def test_launch_binding_revalidates_exact_actor_role_and_provider_identity(
    auth_context,
    db_session,
):
    link = ExternalEntityLink(
        workspace_id=auth_context.workspace_id,
        provider=OSSBridgeProvider.chatwoot,
        klm_entity_type="workspace",
        klm_entity_id=auth_context.workspace_id,
        external_account_id="1",
        external_entity_type="account",
        external_entity_id="1",
        sync_state=ExternalEntitySyncState.synced,
    )
    db_session.add(link)
    await db_session.flush()
    claims = {
        "service": "chatwoot",
        "workspaceId": str(auth_context.workspace_id),
        "actorUserId": str(auth_context.user_id),
        "membershipId": str(auth_context.membership_id),
        "actorRole": "workspace_owner",
    }

    user, membership, binding = await oss_adapters._authorized_launch_binding(
        claims,
        db_session,
    )

    assert user.id == auth_context.user_id
    assert membership.id == auth_context.membership_id
    assert binding.id == link.id

    membership.role = WorkspaceRole.workspace_admin
    await db_session.flush()
    with pytest.raises(oss_adapters.OSSLaunchError, match="não é mais válido"):
        await oss_adapters._authorized_launch_binding(claims, db_session)


@pytest.mark.asyncio
async def test_chatwoot_context_and_routing_are_typed_and_thread_scoped(client, monkeypatch):
    thread_id = "3c20e4c7-d8f3-4336-93af-733d4a7b9069"
    context = {
        "linked": True,
        "threadId": thread_id,
        "conversationId": "73",
        "conversation": {"id": 73, "status": "open", "priority": "high"},
        "inboxes": [{"id": 1, "name": "Atendimento"}],
        "teams": [],
        "labels": [{"id": 2, "title": "urgente"}],
        "agents": [{"id": 3, "name": "Kleist"}],
        "cannedResponses": [{"id": 4, "short_code": "docs", "content": "Envie PDFs."}],
        "projection": {
            "totalMessages": 3,
            "projectedMessages": 1,
            "receivedMessages": 1,
            "pendingMessages": 1,
            "failedMessages": 0,
            "lastSyncedAt": None,
            "externalChannelDeliveryEnabled": False,
        },
        "sourceOfTruth": "klm",
    }
    read = AsyncMock(return_value=context)
    update = AsyncMock(return_value={**context, "conversation": {"id": 73, "status": "pending"}})
    monkeypatch.setattr(ChatwootOperationsService, "__init__", lambda self, db: None)
    monkeypatch.setattr(ChatwootOperationsService, "context", read)
    monkeypatch.setattr(ChatwootOperationsService, "update_routing", update)

    response = await client.get(f"/api/v1/oss-adapters/chatwoot/threads/{thread_id}/context")
    assert response.status_code == 200, response.text
    assert response.json()["conversationId"] == "73"
    assert response.json()["sourceOfTruth"] == "klm"

    response = await client.patch(
        f"/api/v1/oss-adapters/chatwoot/threads/{thread_id}/routing",
        json={"status": "pending", "priority": "high", "labels": ["urgente"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["conversation"]["status"] == "pending"
    assert update.await_args.kwargs["changes"] == {
        "status": "pending",
        "priority": "high",
        "labels": ["urgente"],
    }

    invalid = await client.patch(
        f"/api/v1/oss-adapters/chatwoot/threads/{thread_id}/routing",
        json={},
    )
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_chatwoot_single_thread_projection_is_exposed_with_truthful_delivery_gate(
    client,
    monkeypatch,
):
    thread_id = "3c20e4c7-d8f3-4336-93af-733d4a7b9069"
    sync = AsyncMock(
        return_value={
            "status": "synced",
            "threadId": thread_id,
            "conversationId": "73",
            "totalMessages": 4,
            "projectedMessages": 2,
            "receivedMessages": 1,
            "pendingMessages": 1,
            "failedMessages": 0,
            "lastSyncedAt": "2026-07-17T17:00:00Z",
            "externalChannelDeliveryEnabled": False,
            "projectedNow": 1,
            "contactCreated": False,
            "rateLimited": False,
            "sourceOfTruth": "klm",
        }
    )
    monkeypatch.setattr(ChatwootSyncService, "__init__", lambda self, db: None)
    monkeypatch.setattr(ChatwootSyncService, "sync_thread_by_id", sync)

    response = await client.post(f"/api/v1/oss-adapters/chatwoot/threads/{thread_id}/sync")

    assert response.status_code == 200, response.text
    assert response.json()["projectedNow"] == 1
    assert response.json()["externalChannelDeliveryEnabled"] is False
    assert sync.await_args.args[1] == UUID(thread_id)


@pytest.mark.asyncio
async def test_rocketchat_context_and_single_thread_sync_are_exposed(client, monkeypatch):
    thread_id = "3c20e4c7-d8f3-4336-93af-733d4a7b9069"
    read = AsyncMock(
        return_value={
            "linked": True,
            "threadId": thread_id,
            "roomId": "room-case",
            "room": {"_id": "room-case", "fname": "Caso Saúde"},
            "members": [{"_id": "u1", "name": "Advogada"}],
            "messages": [{"_id": "m1", "msg": "Estratégia aprovada."}],
            "discussions": [],
            "runtimeCompatibility": "6.13.1",
            "auditedReference": "8.6.1",
            "sourceOfTruth": "klm",
        }
    )
    sync = AsyncMock(
        return_value={
            "threadId": thread_id,
            "roomId": "room-case",
            "messagesSeen": 2,
            "messagesProjected": 1,
        }
    )
    monkeypatch.setattr(RocketChatOperationsService, "__init__", lambda self, db: None)
    monkeypatch.setattr(RocketChatOperationsService, "context", read)
    monkeypatch.setattr(RocketChatBridgeService, "__init__", lambda self, db: None)
    monkeypatch.setattr(RocketChatBridgeService, "sync_thread", sync)

    response = await client.get(f"/api/v1/oss-adapters/rocketchat/threads/{thread_id}/context")
    assert response.status_code == 200, response.text
    assert response.json()["roomId"] == "room-case"
    assert response.json()["runtimeCompatibility"] == "6.13.1"

    response = await client.post(f"/api/v1/oss-adapters/rocketchat/threads/{thread_id}/sync")
    assert response.status_code == 200, response.text
    assert response.json()["messagesProjected"] == 1
    sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_chattermate_context_and_agent_sync_are_typed(client, monkeypatch):
    context = {
        "linked": True,
        "organizationId": "org1",
        "agents": [{"id": "a1", "name": "KLM · Hermes", "is_active": True}],
        "workflows": [],
        "analytics": {"conversations": {"total": 0}},
        "performance": {"bot_agents": []},
        "people": {"items": [], "total": 0},
        "peopleStats": {"total_people": 0},
        "mcpTools": [],
        "groups": [],
        "notifications": [],
        "knowledge": {"knowledge": [], "pagination": {}, "queueItems": []},
        "timeRange": "7d",
        "sourceOfTruth": "klm",
        "externalExecutionEnabled": False,
    }
    read = AsyncMock(return_value=context)
    sync = AsyncMock(return_value={"created": 13, "updated": 0, "seen": 13})
    monkeypatch.setattr(ChatterMateOperationsService, "__init__", lambda self, db: None)
    monkeypatch.setattr(ChatterMateOperationsService, "context", read)
    monkeypatch.setattr(ChatterMateOperationsService, "sync_agents", sync)

    response = await client.get("/api/v1/oss-adapters/chattermate/context?time_range=7d")
    assert response.status_code == 200, response.text
    assert response.json()["agents"][0]["name"] == "KLM · Hermes"
    assert response.json()["externalExecutionEnabled"] is False

    response = await client.post("/api/v1/oss-adapters/chattermate/sync-agents")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "synced",
        "created": 13,
        "updated": 0,
        "adopted": 0,
        "conflicts": 0,
        "seen": 13,
    }


@pytest.mark.asyncio
async def test_chattermate_flow_context_and_sync_are_typed(client, monkeypatch):
    flow_id = "8df7bffe-776d-4d33-8810-1aa9eff9fe8a"
    read = AsyncMock(
        return_value={
            "linked": True,
            "flowId": flow_id,
            "revision": 4,
            "agentId": "agent-flow",
            "workflowId": "workflow-flow",
            "workflowStatus": "draft",
            "nodeCount": 4,
            "connectionCount": 3,
            "sourceDigest": "a" * 64,
            "digestMatches": True,
            "graphMatches": True,
            "sourceOfTruth": "klm",
            "externalExecutionEnabled": False,
        }
    )
    sync = AsyncMock(
        return_value={
            "status": "synced",
            "flowId": flow_id,
            "agentId": "agent-flow",
            "workflowId": "workflow-flow",
            "agentAction": "unchanged",
            "workflowAction": "unchanged",
            "graphAction": "unchanged",
            "nodeCount": 4,
            "connectionCount": 3,
            "revision": 4,
            "sourceDigest": "a" * 64,
            "sourceOfTruth": "klm",
            "externalExecutionEnabled": False,
        }
    )
    monkeypatch.setattr(ChatterMateFlowService, "__init__", lambda self, db: None)
    monkeypatch.setattr(ChatterMateFlowService, "context", read)
    monkeypatch.setattr(ChatterMateFlowService, "sync_flow", sync)

    response = await client.get(f"/api/v1/oss-adapters/chattermate/flows/{flow_id}/context")
    assert response.status_code == 200, response.text
    assert response.json()["graphMatches"] is True

    response = await client.post(f"/api/v1/oss-adapters/chattermate/flows/{flow_id}/sync")
    assert response.status_code == 200, response.text
    assert response.json()["externalExecutionEnabled"] is False
    assert response.json()["graphAction"] == "unchanged"


@pytest.mark.asyncio
async def test_chattermate_flow_bridge_requires_automation_permission(
    client,
    auth_context,
    db_session,
):
    membership = await db_session.scalar(
        select(WorkspaceMembership).where(WorkspaceMembership.id == auth_context.membership_id)
    )
    assert membership is not None
    original_role = membership.role
    membership.role = WorkspaceRole.viewer
    await db_session.flush()
    try:
        response = await client.get(
            "/api/v1/oss-adapters/chattermate/flows/8df7bffe-776d-4d33-8810-1aa9eff9fe8a/context"
        )
    finally:
        membership.role = original_role
        await db_session.flush()

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_bottlecrm_operational_contexts_are_typed_and_scoped(client, monkeypatch):
    matter_id = "3c20e4c7-d8f3-4336-93af-733d4a7b9069"
    projection = {
        "linked": True,
        "organizationId": "organization-1",
        "totalLinks": 4,
        "syncedLinks": 4,
        "failedLinks": 0,
        "staleLinks": 0,
        "byEntityType": {"lead": 1, "opportunity": 1, "case": 1, "task": 1},
        "lastSyncedAt": None,
        "sourceOfTruth": "klm",
        "externalExecutionEnabled": False,
    }
    funnel = AsyncMock(
        return_value={
            "projection": projection,
            "counts": {"leads": 8},
            "urgentCounts": {"overdue_tasks": 1},
            "pipeline": [{"id": "PROPOSAL", "label": "Proposta", "count": 2, "value": 10000}],
            "revenue": {"weighted_pipeline": 7500, "currency": "BRL"},
            "opportunityColumns": [],
            "leadColumns": [],
            "caseColumns": [],
            "goals": [],
            "agingConfig": [],
            "notificationsUnread": 0,
            "sensitiveDataHidden": False,
        }
    )
    matter = AsyncMock(
        return_value={
            "projection": projection,
            "matterId": matter_id,
            "records": [
                {
                    "id": "opportunity-1",
                    "entityType": "opportunity",
                    "klmEntityId": matter_id,
                    "klmEntityType": "matter",
                    "name": "Caso Saúde",
                    "stage": "PROPOSAL",
                    "currency": "BRL",
                    "syncState": "synced",
                }
            ],
            "sensitiveDataHidden": False,
        }
    )
    work = AsyncMock(
        return_value={
            "projection": projection,
            "taskColumns": [],
            "tasks": [],
            "boards": [],
            "notificationsUnread": 0,
            "sensitiveDataHidden": False,
        }
    )
    finance = AsyncMock(
        return_value={
            "projection": projection,
            "summary": {"total_invoiced": 10000},
            "statusCounts": {"Sent": 1},
            "overdue": {"count": 1, "amount": 2500},
            "recentActivity": {},
            "estimateSummary": {},
            "invoices": [],
            "estimates": [],
            "products": [],
            "aging": [],
            "revenueSeries": [],
            "notificationsUnread": 0,
            "sensitiveDataHidden": False,
        }
    )
    monkeypatch.setattr(BottleCRMOperationsService, "__init__", lambda self, db: None)
    monkeypatch.setattr(BottleCRMOperationsService, "funnel_context", funnel)
    monkeypatch.setattr(BottleCRMOperationsService, "matter_context", matter)
    monkeypatch.setattr(BottleCRMOperationsService, "work_context", work)
    monkeypatch.setattr(BottleCRMOperationsService, "finance_context", finance)

    funnel_response = await client.get("/api/v1/oss-adapters/bottlecrm/funnel/context")
    assert funnel_response.status_code == 200, funnel_response.text
    assert funnel_response.json()["pipeline"][0]["value"] == 10000

    matter_response = await client.get(
        f"/api/v1/oss-adapters/bottlecrm/matters/{matter_id}/context"
    )
    assert matter_response.status_code == 200, matter_response.text
    assert matter_response.json()["records"][0]["klmEntityId"] == matter_id

    work_response = await client.get("/api/v1/oss-adapters/bottlecrm/work/context")
    assert work_response.status_code == 200, work_response.text
    assert work_response.json()["projection"]["sourceOfTruth"] == "klm"

    finance_response = await client.get("/api/v1/oss-adapters/bottlecrm/finance/context")
    assert finance_response.status_code == 200, finance_response.text
    assert finance_response.json()["overdue"]["amount"] == 2500

    funnel.assert_awaited_once()
    matter.assert_awaited_once()
    work.assert_awaited_once()
    finance.assert_awaited_once()
