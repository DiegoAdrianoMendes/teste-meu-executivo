"""Focused contracts for the outbound Chatwoot projection."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
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
from app.services import chatwoot_sync_service as chatwoot_sync_module
from app.services.chatwoot_sync_service import ChatwootSyncService
from app.services.oss_bridge_service import OSSBridgeService


def _service(db_session) -> ChatwootSyncService:
    service = ChatwootSyncService.__new__(ChatwootSyncService)
    service.db = db_session
    service.client = SimpleNamespace()
    service.links = OSSBridgeService(db_session)
    service.account_id = "1"
    return service


@pytest.mark.asyncio
async def test_sync_workspace_projects_only_customer_threads_and_commits_once(
    db_session,
    auth_context,
) -> None:
    customer = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Chatwoot",
        phone="+5561999999999",
    )
    db_session.add(customer)
    await db_session.flush()
    external_thread = OmnichannelThread(
        workspace_id=auth_context.workspace_id,
        client_id=customer.id,
        primary_channel=OmnichannelChannel.whatsapp,
        subject="Atendimento externo",
    )
    internal_thread = OmnichannelThread(
        workspace_id=auth_context.workspace_id,
        primary_channel=OmnichannelChannel.internal,
        subject="Equipe juridica",
    )
    orphan_thread = OmnichannelThread(
        workspace_id=auth_context.workspace_id,
        primary_channel=OmnichannelChannel.email,
        subject="Contato ainda nao identificado",
    )
    db_session.add_all([external_thread, internal_thread, orphan_thread])
    await db_session.flush()

    service = _service(db_session)
    service.client.account = AsyncMock(return_value={"id": 1})
    service.ensure_workspace_binding = AsyncMock(return_value=None)
    service.sync_thread = AsyncMock(
        return_value={"messages": 2, "contacts": 1, "pendingMessages": 0}
    )

    with patch.object(db_session, "commit", wraps=db_session.commit) as commit:
        result = await service.sync_workspace(auth_context.workspace_id)

    service.sync_thread.assert_awaited_once()
    assert service.sync_thread.await_args.args[1].id == external_thread.id
    assert result == {
        "threads": 1,
        "messages": 2,
        "contacts": 1,
        "pendingMessages": 0,
    }
    assert commit.await_count == 1


@pytest.mark.asyncio
async def test_sync_thread_keeps_local_transaction_open_until_workspace_commit(
    db_session,
    auth_context,
) -> None:
    customer = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Transacional",
        email="cliente@example.com",
    )
    db_session.add(customer)
    await db_session.flush()
    thread = OmnichannelThread(
        workspace_id=auth_context.workspace_id,
        client_id=customer.id,
        primary_channel=OmnichannelChannel.email,
        subject="Negativa de cobertura",
    )
    db_session.add(thread)
    await db_session.flush()
    message = OmnichannelMessage(
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        channel=OmnichannelChannel.email,
        direction=MessageDirection.outbound,
        author_type=MessageAuthorType.agent,
        author_label="Agente SDR",
        body="Recebemos seus documentos.",
        idempotency_key="chatwoot-sync-transaction",
        delivery_status=MessageDeliveryStatus.local_draft,
    )
    db_session.add(message)
    await db_session.flush()

    service = _service(db_session)
    service.client.create_contact = AsyncMock(return_value={"id": 41})
    service.client.create_conversation = AsyncMock(return_value={"id": 73})
    service.client.list_messages = AsyncMock(return_value=[])
    service.client.create_message = AsyncMock(return_value={"id": 105})

    with patch.object(db_session, "commit", wraps=db_session.commit) as commit:
        result = await service.sync_thread(auth_context.workspace_id, thread)

    assert result == {"messages": 1, "contacts": 1, "pendingMessages": 0}
    assert commit.await_count == 0
    assert message.external_message_id == "105"
    assert message.delivery_status == MessageDeliveryStatus.local_draft
    assert message.message_metadata["chatwootConversationId"] == "73"


@pytest.mark.asyncio
async def test_sync_thread_by_id_reports_local_projection_instead_of_delivery_sync(
    db_session,
    auth_context,
    monkeypatch,
    tmp_path,
) -> None:
    customer = Client(workspace_id=auth_context.workspace_id, name="Cliente Local")
    db_session.add(customer)
    await db_session.flush()
    thread = OmnichannelThread(
        workspace_id=auth_context.workspace_id,
        client_id=customer.id,
        primary_channel=OmnichannelChannel.whatsapp,
        subject="Projeção local",
    )
    db_session.add(thread)
    await db_session.flush()

    monkeypatch.setattr(chatwoot_sync_module, "_ROOT", tmp_path)
    monkeypatch.setattr(chatwoot_sync_module, "_PROCESS_SYNC_LOCK", asyncio.Lock())
    service = _service(db_session)
    service.client.account = AsyncMock(return_value={"id": 1})
    service.ensure_workspace_binding = AsyncMock(return_value=None)
    service.sync_thread = AsyncMock(
        return_value={"messages": 1, "contacts": 0, "pendingMessages": 0}
    )
    service.links = SimpleNamespace(
        find_link_by_klm_entity=AsyncMock(
            return_value=SimpleNamespace(external_entity_id="73")
        )
    )
    projection = AsyncMock(
        return_value={"status": "synced", "pendingMessages": 1, "projectedMessages": 1}
    )
    audit = AsyncMock(return_value=SimpleNamespace())

    with patch.object(
        chatwoot_sync_module.ChatwootOperationsService,
        "projection",
        projection,
    ), patch.object(
        chatwoot_sync_module.KlmOperationsService,
        "record_audit",
        audit,
    ):
        result = await service.sync_thread_by_id(
            auth_context.workspace_id,
            thread.id,
            auth_context.user_id,
        )

    assert result["status"] == "projected_local"
    assert result["externalDeliveryChanged"] is False
    assert result["sourceOfTruth"] == "klm"


@pytest.mark.asyncio
async def test_sync_workspace_serializes_concurrent_reconciliations(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(chatwoot_sync_module, "_ROOT", tmp_path)
    monkeypatch.setattr(chatwoot_sync_module, "_PROCESS_SYNC_LOCK", asyncio.Lock())
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    active = 0
    maximum_active = 0

    async def reconcile(_: UUID) -> dict[str, int]:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"threads": 0, "messages": 0, "contacts": 0, "pendingMessages": 0}

    first = object.__new__(ChatwootSyncService)
    second = object.__new__(ChatwootSyncService)
    first._sync_workspace_unlocked = AsyncMock(side_effect=reconcile)
    second._sync_workspace_unlocked = AsyncMock(side_effect=reconcile)

    results = await asyncio.gather(
        first.sync_workspace(workspace_id),
        second.sync_workspace(workspace_id),
    )

    assert maximum_active == 1
    assert all(result["threads"] == 0 for result in results)
