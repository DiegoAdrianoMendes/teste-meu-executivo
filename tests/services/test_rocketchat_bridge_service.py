"""Focused recovery and identity contracts for the Rocket.Chat bridge."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.klm_operations import KlmAuditEvent, OmnichannelMessage
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    OSSBridgeProvider,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.oss_bridge import ExternalEntityLinkUpsert
from app.services.klm_operations_service import KlmOperationsService
from app.services.oss_bridge_service import OSSBridgeService
from app.services.rocketchat_bridge_service import (
    RocketChatBridgeService,
    RocketChatWorkspaceBindingError,
)
from app.services.rocketchat_client import RocketChatClient


def _service(db_session) -> RocketChatBridgeService:
    service = RocketChatBridgeService.__new__(RocketChatBridgeService)
    service.db = db_session
    service.links = OSSBridgeService(db_session)
    service.operations = KlmOperationsService(db_session)
    service.client = SimpleNamespace()
    return service


async def _link(
    service: RocketChatBridgeService,
    workspace_id,
    *,
    account_id: str,
    entity_type: str,
    entity_id: str,
    klm_entity_type: str = "workspace",
    klm_entity_id=None,
) -> ExternalEntityLink:
    return await service.links.upsert_link(
        workspace_id,
        ExternalEntityLinkUpsert(
            provider=OSSBridgeProvider.rocketchat,
            klm_entity_type=klm_entity_type,
            klm_entity_id=klm_entity_id or workspace_id,
            external_account_id=account_id,
            external_entity_type=entity_type,
            external_entity_id=entity_id,
            sync_state=ExternalEntitySyncState.synced,
            metadata={"runtimeVersion": "8.6.1"},
        ),
    )


@pytest.mark.asyncio
async def test_binding_marks_superseded_runtime_links_stale(db_session, auth_context):
    service = _service(db_session)
    old_account = await _link(
        service,
        auth_context.workspace_id,
        account_id="rocket-old",
        entity_type="account",
        entity_id="rocket-old",
    )
    old_room = await _link(
        service,
        auth_context.workspace_id,
        account_id="rocket-old",
        entity_type="room",
        entity_id="room-old",
        klm_entity_type="omnichannel_thread",
        klm_entity_id=uuid4(),
    )

    await service._bind_workspace(auth_context.workspace_id, "rocket-current")
    await db_session.flush()

    assert old_account.sync_state == ExternalEntitySyncState.stale
    assert old_room.sync_state == ExternalEntitySyncState.stale
    assert old_account.link_metadata["supersededByRuntimeVersion"] == "6.13.1"
    current = await service.links.find_link_by_external_entity(
        auth_context.workspace_id,
        OSSBridgeProvider.rocketchat,
        "rocket-current",
        "account",
        "rocket-current",
    )
    assert current is not None
    assert current.sync_state == ExternalEntitySyncState.synced


@pytest.mark.asyncio
async def test_same_rocket_server_cannot_bind_two_workspaces(db_session, auth_context):
    service = _service(db_session)
    await service._bind_workspace(auth_context.workspace_id, "rocket-shared")
    other_workspace = Workspace(
        name="Workspace Rocket externo",
        slug=f"rocket-externo-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()

    with pytest.raises(RocketChatWorkspaceBindingError, match="outro workspace"):
        await service._bind_workspace(other_workspace.id, "rocket-shared")

    count = await db_session.scalar(
        select(func.count())
        .select_from(ExternalEntityLink)
        .where(
            ExternalEntityLink.provider == OSSBridgeProvider.rocketchat,
            ExternalEntityLink.external_account_id == "rocket-shared",
            ExternalEntityLink.klm_entity_type == "workspace",
            ExternalEntityLink.external_entity_type == "account",
            ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
        )
    )
    assert count == 1


@pytest.mark.asyncio
async def test_channel_binding_repairs_a_missing_local_thread(db_session, auth_context):
    service = _service(db_session)
    link = await _link(
        service,
        auth_context.workspace_id,
        account_id="rocket-current",
        entity_type="room",
        entity_id="room-comercial",
        klm_entity_type="omnichannel_thread",
        klm_entity_id=uuid4(),
    )

    thread = await service._ensure_channel_thread(
        auth_context.workspace_id,
        "rocket-current",
        "room-comercial",
        "comercial",
    )
    await db_session.flush()

    assert link.klm_entity_id == thread.id
    assert link.sync_state == ExternalEntitySyncState.synced
    assert thread.subject == "Equipe · Comercial"
    assert thread.owner_agent_key == "orquestrador"
    assert thread.thread_metadata["coordinationAgentKey"] == "orquestrador"
    assert thread.thread_metadata["ownerBridgeAgentKey"] == "hermes"
    count = await db_session.scalar(
        select(func.count())
        .select_from(ExternalEntityLink)
        .where(
            ExternalEntityLink.workspace_id == auth_context.workspace_id,
            ExternalEntityLink.provider == OSSBridgeProvider.rocketchat,
            ExternalEntityLink.external_account_id == "rocket-current",
            ExternalEntityLink.external_entity_type == "room",
            ExternalEntityLink.external_entity_id == "room-comercial",
        )
    )
    assert count == 1


@pytest.mark.asyncio
async def test_send_message_reports_existing_and_recreated_projections():
    client = RocketChatClient.__new__(RocketChatClient)
    client._request = AsyncMock(
        side_effect=[
            {"success": True, "message": {"_id": "klm-existing"}},
            {"success": False},
            {"success": True, "message": {"_id": "klm-recreated"}},
        ]
    )

    existing, existing_created = await client.send_message(
        room_id="room-1", message_id="klm-existing", body="Nota existente"
    )
    recreated, recreated_created = await client.send_message(
        room_id="room-1", message_id="klm-recreated", body="Nota recriada"
    )

    assert existing["_id"] == "klm-existing"
    assert existing_created is False
    assert recreated["_id"] == "klm-recreated"
    assert recreated_created is True


@pytest.mark.asyncio
async def test_webhook_replay_is_idempotent_and_edit_updates_without_duplicate(
    db_session,
    auth_context,
):
    service = _service(db_session)
    await _link(
        service,
        auth_context.workspace_id,
        account_id="rocket-current",
        entity_type="account",
        entity_id="rocket-current",
    )
    service.client.info = AsyncMock(return_value={"uniqueID": "rocket-current"})
    original = {
        "message_id": "rocket-message-1",
        "channel_id": "room-juridico",
        "channel_name": "juridico-saude",
        "user_name": "Advogada KLM",
        "text": "Primeira versão",
        "timestamp": "2026-07-17T12:00:00Z",
        "token": "redacted-by-bridge",
    }

    first = await service.ingest_webhook(
        workspace_id=auth_context.workspace_id,
        payload=original,
    )
    replay = await service.ingest_webhook(
        workspace_id=auth_context.workspace_id,
        payload=original,
    )
    edited = await service.ingest_webhook(
        workspace_id=auth_context.workspace_id,
        payload={
            **original,
            "text": "Versão corrigida",
            "timestamp": "2026-07-17T12:05:00Z",
        },
    )

    messages = list(
        (
            await db_session.execute(
                select(OmnichannelMessage).where(
                    OmnichannelMessage.workspace_id == auth_context.workspace_id,
                    OmnichannelMessage.external_message_id == "rocket-message-1",
                )
            )
        ).scalars()
    )
    assert first["messageUpdated"] is False
    assert replay == {"status": "processed", "deduplicated": True}
    assert edited["messageUpdated"] is True
    assert len(messages) == 1
    assert messages[0].body == "Versão corrigida"
    assert messages[0].author_type.value == "human"
    assert messages[0].author_user_id is None
    assert messages[0].message_metadata["rocketEditCount"] == 1
    assert messages[0].message_metadata["rocketLastRevision"]
    audit_count = await db_session.scalar(
        select(func.count())
        .select_from(KlmAuditEvent)
        .where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.event_type == "rocketchat.message_updated",
            KlmAuditEvent.entity_id == messages[0].id,
        )
    )
    assert audit_count == 1
    recorded_audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.event_type == "omnichannel_message_recorded",
            KlmAuditEvent.entity_id == messages[0].id,
        )
    )
    assert recorded_audit.actor_type == "system"
    assert recorded_audit.actor_label == "Rocket.Chat bridge"
    assert recorded_audit.actor_user_id is None
