"""Contracts for the reviewed Chatwoot operational facade."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.models.client import Client
from app.models.klm_operations import (
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.oss_bridge import ExternalEntitySyncState, OSSBridgeProvider
from app.schemas.oss_bridge import ExternalEntityLinkUpsert
from app.services.chatwoot_client import ChatwootClient, ChatwootClientError
from app.services.chatwoot_operations_service import (
    ChatwootOperationsService,
    ChatwootRoutingUpdateError,
)
from app.services.oss_bridge_service import OSSBridgeService

PROJECTION = {
    "totalMessages": 3,
    "projectedMessages": 1,
    "receivedMessages": 1,
    "pendingMessages": 1,
    "failedMessages": 0,
    "lastSyncedAt": None,
    "externalChannelDeliveryEnabled": False,
}


@pytest.mark.asyncio
async def test_chatwoot_projection_distinguishes_pending_local_and_received_messages(
    db_session,
    auth_context,
) -> None:
    client = Client(workspace_id=auth_context.workspace_id, name="Cliente projeção")
    db_session.add(client)
    await db_session.flush()
    thread = OmnichannelThread(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        primary_channel=OmnichannelChannel.whatsapp,
    )
    db_session.add(thread)
    await db_session.flush()
    messages = [
        OmnichannelMessage(
            workspace_id=auth_context.workspace_id,
            thread_id=thread.id,
            channel=OmnichannelChannel.whatsapp,
            direction=MessageDirection.outbound,
            author_type=MessageAuthorType.human,
            author_label="Advogado",
            body="Projetada",
            idempotency_key="chatwoot-projection-1",
            delivery_status=MessageDeliveryStatus.sent,
            message_metadata={"chatwootMessageId": "101"},
        ),
        OmnichannelMessage(
            workspace_id=auth_context.workspace_id,
            thread_id=thread.id,
            channel=OmnichannelChannel.whatsapp,
            direction=MessageDirection.inbound,
            author_type=MessageAuthorType.client,
            author_label="Cliente",
            body="Recebida",
            idempotency_key="chatwoot-projection-2",
            delivery_status=MessageDeliveryStatus.delivered,
            message_metadata={
                "source": "chatwoot_webhook",
                "chatwootMessageId": "102",
            },
        ),
        OmnichannelMessage(
            workspace_id=auth_context.workspace_id,
            thread_id=thread.id,
            channel=OmnichannelChannel.whatsapp,
            direction=MessageDirection.outbound,
            author_type=MessageAuthorType.agent,
            author_label="SDR",
            body="Pendente local",
            idempotency_key="chatwoot-projection-3",
            delivery_status=MessageDeliveryStatus.local_draft,
        ),
        OmnichannelMessage(
            workspace_id=auth_context.workspace_id,
            thread_id=thread.id,
            channel=OmnichannelChannel.whatsapp,
            direction=MessageDirection.outbound,
            author_type=MessageAuthorType.agent,
            author_label="SDR",
            body="Falhou",
            idempotency_key="chatwoot-projection-4",
            delivery_status=MessageDeliveryStatus.failed,
        ),
    ]
    db_session.add_all(messages)
    await db_session.flush()
    links = OSSBridgeService(db_session)
    await links.upsert_link(
        auth_context.workspace_id,
        ExternalEntityLinkUpsert(
            provider=OSSBridgeProvider.chatwoot,
            klm_entity_type="omnichannel_thread",
            klm_entity_id=thread.id,
            external_account_id="100001",
            external_entity_type="conversation",
            external_entity_id="73",
            sync_state=ExternalEntitySyncState.synced,
        ),
    )
    service = object.__new__(ChatwootOperationsService)
    service.db = db_session
    service.client = SimpleNamespace(account_id="100001")
    service.links = links

    result = await service.projection(auth_context.workspace_id, thread.id)

    assert result == {
        "totalMessages": 4,
        "projectedMessages": 1,
        "receivedMessages": 1,
        "pendingMessages": 2,
        "failedMessages": 1,
        "lastSyncedAt": None,
        "externalChannelDeliveryEnabled": False,
    }


@pytest.mark.asyncio
async def test_chatwoot_collection_accepts_array_and_payload_contracts() -> None:
    client = object.__new__(ChatwootClient)
    client._request_json = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [{"id": 1, "name": "Admin"}],
            {"payload": [{"id": 2, "name": "Atendimento"}]},
        ]
    )

    assert await client._collection("/agents") == [{"id": 1, "name": "Admin"}]
    assert await client._collection("/inboxes") == [{"id": 2, "name": "Atendimento"}]


@pytest.mark.asyncio
async def test_chatwoot_context_is_scoped_to_linked_thread_and_redacts_broad_payloads() -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    thread_id = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")
    service = object.__new__(ChatwootOperationsService)
    service.db = SimpleNamespace()
    service.links = SimpleNamespace(
        find_link_by_klm_entity=AsyncMock(return_value=SimpleNamespace(external_entity_id="73"))
    )
    service.projection = AsyncMock(return_value=PROJECTION)
    service.client = SimpleNamespace(
        account_id="100001",
        list_inboxes=AsyncMock(
            return_value=[
                {
                    "id": 1,
                    "name": "Atendimento",
                    "channel_type": "Channel::Api",
                    "api_token": "must-not-leak",
                }
            ]
        ),
        list_teams=AsyncMock(return_value=[]),
        list_labels=AsyncMock(return_value=[{"id": 2, "title": "urgente"}]),
        list_agents=AsyncMock(
            return_value=[{"id": 3, "name": "Kleist", "access_token": "hidden"}]
        ),
        list_canned_responses=AsyncMock(
            return_value=[{"id": 4, "short_code": "docs", "content": "Envie os PDFs."}]
        ),
        conversation=AsyncMock(
            return_value={
                "id": 73,
                "status": "open",
                "priority": "urgent",
                "access_token": "must-not-leak",
            }
        ),
    )

    context = await service.context(workspace_id, thread_id)

    assert context["linked"] is True
    assert context["conversationId"] == "73"
    assert context["inboxes"] == [{"id": 1, "name": "Atendimento", "channel_type": "Channel::Api"}]
    assert context["agents"] == [{"id": 3, "name": "Kleist"}]
    assert "access_token" not in context["conversation"]


@pytest.mark.asyncio
async def test_chatwoot_routing_updates_only_reviewed_operations_and_writes_audit() -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    thread_id = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")
    actor_id = UUID("fbdcc4ce-63d3-450d-b85d-13ffb08ea82d")
    db = SimpleNamespace(
        add=MagicMock(),
        flush=AsyncMock(),
        scalar=AsyncMock(return_value="Usuário Teste"),
    )
    client = SimpleNamespace(
        account_id="100001",
        conversation=AsyncMock(
            side_effect=[
                {"id": 73, "status": "open", "priority": None, "labels": []},
                {"id": 73, "status": "pending", "priority": "high", "labels": ["urgente"]},
                {"id": 73, "status": "pending", "priority": "high", "labels": ["urgente"]},
            ]
        ),
        set_conversation_status=AsyncMock(return_value={}),
        set_conversation_priority=AsyncMock(return_value={}),
        assign_conversation=AsyncMock(return_value={}),
        set_conversation_labels=AsyncMock(return_value={}),
        list_inboxes=AsyncMock(return_value=[]),
        list_teams=AsyncMock(return_value=[]),
        list_labels=AsyncMock(return_value=[]),
        list_agents=AsyncMock(return_value=[]),
        list_canned_responses=AsyncMock(return_value=[]),
    )
    service = object.__new__(ChatwootOperationsService)
    service.db = db
    service.client = client
    service.links = SimpleNamespace(
        find_link_by_klm_entity=AsyncMock(return_value=SimpleNamespace(external_entity_id="73"))
    )
    service.projection = AsyncMock(return_value=PROJECTION)

    result = await service.update_routing(
        workspace_id=workspace_id,
        thread_id=thread_id,
        actor_user_id=actor_id,
        changes={
            "status": "pending",
            "priority": "high",
            "assignee_id": 3,
            "team_id": 5,
            "labels": ["urgente"],
        },
    )

    client.set_conversation_status.assert_awaited_once_with("73", "pending")
    client.set_conversation_priority.assert_awaited_once_with("73", "high")
    client.assign_conversation.assert_awaited_once_with("73", assignee_id=3, team_id=5)
    client.set_conversation_labels.assert_awaited_once_with("73", ["urgente"])
    db.add.assert_called_once()
    db.flush.assert_awaited_once()
    assert result["conversation"]["status"] == "pending"
    assert result["projection"]["pendingMessages"] == 1


@pytest.mark.asyncio
async def test_chatwoot_partial_routing_failure_is_compensated_and_committed_to_audit() -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    thread_id = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")
    actor_id = UUID("fbdcc4ce-63d3-450d-b85d-13ffb08ea82d")
    db = SimpleNamespace(
        add=MagicMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        scalar=AsyncMock(return_value="Usuário Teste"),
    )
    client = SimpleNamespace(
        account_id="100001",
        conversation=AsyncMock(
            side_effect=[
                {"id": 73, "status": "open", "priority": None, "labels": []},
                {"id": 73, "status": "open", "priority": None, "labels": []},
            ]
        ),
        set_conversation_status=AsyncMock(side_effect=[{}, {}]),
        set_conversation_priority=AsyncMock(
            side_effect=[ChatwootClientError("remote failed"), {}]
        ),
    )
    service = object.__new__(ChatwootOperationsService)
    service.db = db
    service.client = client
    service.links = SimpleNamespace(
        find_link_by_klm_entity=AsyncMock(
            return_value=SimpleNamespace(external_entity_id="73")
        )
    )

    with pytest.raises(ChatwootRoutingUpdateError, match="estado anterior foi restaurado"):
        await service.update_routing(
            workspace_id=workspace_id,
            thread_id=thread_id,
            actor_user_id=actor_id,
            changes={"status": "pending", "priority": "high"},
        )

    assert client.set_conversation_status.await_args_list[1].args == ("73", "open")
    assert client.set_conversation_priority.await_args_list[1].args == ("73", None)
    audit = db.add.call_args.args[0]
    assert audit.event_type == "chatwoot.routing_partial_failure"
    assert audit.after_data["appliedSteps"] == ["status"]
    assert audit.after_data["compensatedSteps"] == ["status", "priority"]
    assert audit.after_data["compensationFailures"] == []
    db.commit.assert_awaited_once()
