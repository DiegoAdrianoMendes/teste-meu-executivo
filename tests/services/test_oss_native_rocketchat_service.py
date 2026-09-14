"""Focused contracts for the tenant-scoped native Rocket.Chat adapter."""

from __future__ import annotations

import json
import sys
from types import ModuleType
from uuid import UUID, uuid4

import pytest

from app.models.oss_bridge import REDACTED_SECRET, ExternalEntitySyncState, OSSBridgeProvider
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.oss_bridge import ExternalEntityLinkUpsert
from app.services.klm_operations_service import KlmOperationsService
from app.services.oss_bridge_service import OSSBridgeService, compute_payload_hash
from app.services.oss_capability_registry import oss_capability_registry
from app.services.oss_native_rocketchat_service import (
    _CAPABILITY_ACTIONS,
    _GATED_CAPABILITIES,
    RocketChatNativeService,
)
from app.services.rocketchat_bridge_service import (
    RocketChatBridgeService,
    RocketChatWorkspaceBindingError,
)
from app.services.rocketchat_operations_service import RocketChatOperationsService

ACCOUNT_ID = "rocket-native-test"
SECRET = "sk-this-value-must-never-escape"
CAPABILITY_KEYS = tuple(_CAPABILITY_ACTIONS)


class FakeRocketChatClient:
    def __init__(self, *, account_id: str = ACCOUNT_ID, forbid_network: bool = False):
        self.account_id = account_id
        self.forbid_network = forbid_network
        self.info_calls = 0
        self.requests: list[tuple[str, str, dict | None, dict | None, bool]] = []

    async def info(self):
        if self.forbid_network:
            raise AssertionError("gated capability attempted Rocket.Chat network access")
        self.info_calls += 1
        return {"uniqueID": self.account_id, "version": "6.13.1"}

    async def identity(self):
        return {
            "_id": "admin-user",
            "username": "klm-admin",
            "name": "KLM Admin",
            "status": "online",
            "authToken": SECRET,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        auth: bool = True,
        **_kwargs,
    ):
        if self.forbid_network:
            raise AssertionError("gated capability attempted Rocket.Chat network access")
        self.requests.append((method, path, params, json, auth))
        if path.endswith("/im.list"):
            return {
                "ims": [
                    {
                        "_id": "dm-1",
                        "t": "d",
                        "fname": "Conversa interna",
                        "authToken": SECRET,
                    }
                ],
                "success": True,
            }
        if path.endswith("/users.list"):
            return {
                "users": [
                    {
                        "_id": "user-1",
                        "username": "advogada",
                        "name": "Advogada",
                        "status": "online",
                        "roles": ["user"],
                        "password": SECRET,
                    }
                ],
                "success": True,
            }
        if path.endswith("/roles.list"):
            return {"roles": [{"_id": "admin", "name": "Admin"}], "success": True}
        if path.endswith("/permissions.listAll"):
            return {
                "permissions": [{"_id": "view-room-administration", "roles": ["admin"]}],
                "success": True,
            }
        if path.endswith("/rooms.adminRooms"):
            return {
                "rooms": [{"_id": "room-admin", "name": "juridico", "t": "c"}],
                "success": True,
            }
        if path.endswith("/integrations.list"):
            return {
                "integrations": [
                    {
                        "_id": "hook-1",
                        "name": "KLM",
                        "type": "webhook-incoming",
                        "enabled": True,
                        "token": SECRET,
                    }
                ],
                "success": True,
            }
        if path.endswith("/integrations.history"):
            return {
                "history": [{"_id": "history-1", "status": "success", "token": SECRET}],
                "success": True,
            }
        if path.endswith("/teams.listAll"):
            return {
                "teams": [{"_id": "team-1", "name": "Juridico", "type": 1}],
                "success": True,
            }
        if path.endswith("/api/apps/installed"):
            return {
                "apps": [
                    {
                        "id": "app-1",
                        "name": "KLM App",
                        "version": "1.0.0",
                        "status": "manually_enabled",
                        "privateKey": SECRET,
                    }
                ],
                "success": True,
            }
        if path.endswith("/emoji-custom.list"):
            return {
                "emojis": [{"_id": "emoji-1", "name": "aprovado", "token": SECRET}],
                "success": True,
            }
        if path.endswith("/oauth-apps.list"):
            return {
                "oauthApps": [
                    {
                        "_id": "oauth-1",
                        "name": "KLM OAuth",
                        "active": True,
                        "clientSecret": SECRET,
                    }
                ],
                "success": True,
            }
        if path.endswith("/users.getPersonalAccessTokens"):
            return {
                "tokens": [{"name": "automation", "lastTokenPart": "1234", "token": SECRET}],
                "success": True,
            }
        if path.endswith("/settings.public"):
            return {
                "settings": [
                    {
                        "_id": "Accounts_OAuth_Google",
                        "group": "OAuth",
                        "value": SECRET,
                        "clientSecret": SECRET,
                    }
                ],
                "success": True,
            }
        if path.endswith("/chat.update"):
            body = json or {}
            return {
                "message": {
                    "_id": body["msgId"],
                    "rid": body["roomId"],
                    "msg": body["text"],
                    "authToken": SECRET,
                },
                "success": True,
            }
        return {"success": True, "token": SECRET}

    async def room_history(self, room_id: str, room_type: str, maximum: int = 50):
        return [
            {
                "_id": "message-1",
                "rid": room_id,
                "msg": "@advogada decisao aprovada",
                "u": {"_id": "user-1", "username": "advogada", "token": SECRET},
                "reactions": {":white_check_mark:": {"usernames": ["advogada"]}},
            }
        ][:maximum]

    async def discussions(self, room_id: str, maximum: int = 50):
        return [{"_id": "discussion-1", "prid": room_id, "fname": "Tese"}][:maximum]


def _service(db_session, client: FakeRocketChatClient) -> RocketChatNativeService:
    service = RocketChatNativeService.__new__(RocketChatNativeService)
    service.db = db_session
    service.client = client
    service.links = OSSBridgeService(db_session)
    service.klm = KlmOperationsService(db_session)
    service.operations = RocketChatOperationsService(db_session, client)  # type: ignore[arg-type]
    bridge = RocketChatBridgeService.__new__(RocketChatBridgeService)
    bridge.db = db_session
    bridge.links = service.links
    bridge.operations = service.klm
    bridge.client = client
    service.bridge = bridge
    return service


async def _link(
    db_session,
    workspace_id: UUID,
    *,
    account_id: str = ACCOUNT_ID,
    entity_type: str = "account",
    entity_id: str | None = None,
    klm_entity_type: str = "workspace",
    klm_entity_id: UUID | None = None,
    metadata: dict | None = None,
):
    return await OSSBridgeService(db_session).upsert_link(
        workspace_id,
        ExternalEntityLinkUpsert(
            provider=OSSBridgeProvider.rocketchat,
            klm_entity_type=klm_entity_type,
            klm_entity_id=klm_entity_id or workspace_id,
            external_account_id=account_id,
            external_entity_type=entity_type,
            external_entity_id=entity_id or account_id,
            sync_state=ExternalEntitySyncState.synced,
            metadata=metadata or {"runtimeVersion": "6.13.1"},
        ),
    )


async def _workspace(db_session, label: str) -> Workspace:
    workspace = Workspace(
        name=f"Rocket Native {label}",
        slug=f"rocket-native-{label}-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


def test_native_service_covers_exactly_the_33_classified_rocket_capabilities():
    registry_keys = {
        item["key"]
        for item in oss_capability_registry()["capabilities"]
        if item["provider"] == "rocketchat"
    }

    assert len(registry_keys) == 33
    assert set(CAPABILITY_KEYS) == registry_keys
    assert all(_CAPABILITY_ACTIONS[key] for key in registry_keys)


@pytest.mark.asyncio
async def test_every_advertised_rocket_action_has_a_domain_validation_contract(
    db_session,
    auth_context,
) -> None:
    service = _service(db_session, FakeRocketChatClient(forbid_network=True))
    validated = 0

    for capability_key, commands in _CAPABILITY_ACTIONS.items():
        for command in commands:
            action = next(
                row for row in service._actions(capability_key) if row["command"] == command
            )
            payload = {
                required.split("|")[0]: (
                    True if required.split("|")[0] in {"confirmed", "confirmLocalOnly"}
                    else "local-value"
                )
                for required in action["required"]
            }
            result = await service.validate(
                auth_context.workspace_id,
                auth_context.user_id,
                capability_key,
                command,
                payload,
                mode="dry-run",
            )
            assert result["status"] == "validated"
            assert result["externalNetworkExecuted"] is False
            validated += 1

    assert validated == 77


@pytest.mark.asyncio
@pytest.mark.parametrize("capability_key", CAPABILITY_KEYS)
async def test_every_capability_has_non_empty_sanitized_content_and_actions(
    db_session,
    auth_context,
    capability_key: str,
):
    client = FakeRocketChatClient(forbid_network=capability_key in _GATED_CAPABILITIES)
    service = _service(db_session, client)
    if capability_key not in _GATED_CAPABILITIES:
        await _link(db_session, auth_context.workspace_id)

    result = await service.snapshot(auth_context.workspace_id, capability_key, {})

    assert result["capabilityKey"] == capability_key
    assert result["content"]
    assert result["actions"]
    assert all(action["command"] for action in result["actions"])
    assert result["runtimeRisk"] == {
        "runtimeVersion": "6.13.1",
        "auditedSourceVersion": "8.6.1",
        "compatibilityStatus": "unverified",
        "compatible": None,
        "warning": result["runtimeRisk"]["warning"],
    }
    assert SECRET not in json.dumps(result)
    if capability_key in _GATED_CAPABILITIES:
        assert result["content"]["networkExecuted"] is False
        assert client.info_calls == 0
    else:
        assert client.info_calls == 1


@pytest.mark.asyncio
async def test_same_rocket_server_binding_is_refused_for_another_workspace(
    db_session,
    auth_context,
):
    other_workspace = await _workspace(db_session, "owner")
    await _link(db_session, other_workspace.id)
    client = FakeRocketChatClient()
    service = _service(db_session, client)

    with pytest.raises(RocketChatWorkspaceBindingError, match="outro workspace"):
        await service.snapshot(auth_context.workspace_id, "admin-users", {})

    assert not client.requests


@pytest.mark.asyncio
async def test_linked_room_read_model_never_mixes_other_workspace_data(
    db_session,
    auth_context,
):
    other_workspace = await _workspace(db_session, "foreign")
    await _link(db_session, auth_context.workspace_id)
    await _link(db_session, other_workspace.id, account_id="rocket-foreign")
    await _link(
        db_session,
        auth_context.workspace_id,
        entity_type="room",
        entity_id="room-current",
        klm_entity_type="omnichannel_thread",
        klm_entity_id=uuid4(),
        metadata={"roomName": "caso-atual", "roomType": "p", "scope": "case"},
    )
    await _link(
        db_session,
        other_workspace.id,
        account_id="rocket-foreign",
        entity_type="room",
        entity_id="room-foreign",
        klm_entity_type="omnichannel_thread",
        klm_entity_id=uuid4(),
        metadata={"roomName": "caso-estranho", "roomType": "p", "scope": "case"},
    )
    service = _service(db_session, FakeRocketChatClient())

    result = await service.snapshot(
        auth_context.workspace_id,
        "channels-case-rooms",
        {},
    )

    serialized = json.dumps(result)
    assert "room-current" in serialized
    assert "room-foreign" not in serialized
    assert "caso-estranho" not in serialized


@pytest.mark.asyncio
async def test_message_edit_is_executed_once_and_replay_is_fenced(
    db_session,
    auth_context,
):
    await _link(db_session, auth_context.workspace_id)
    await _link(
        db_session,
        auth_context.workspace_id,
        entity_type="room",
        entity_id="room-edit",
        klm_entity_type="omnichannel_thread",
        klm_entity_id=uuid4(),
        metadata={"roomName": "caso-edit", "roomType": "p", "scope": "case"},
    )
    client = FakeRocketChatClient()
    service = _service(db_session, client)
    payload = {
        "roomId": "room-edit",
        "messageId": "message-edit",
        "text": "Texto juridico revisado.",
        "idempotencyKey": "edit-message-001",
    }

    first = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "message-lifecycle-export-emoji",
        "edit-message",
        payload,
    )
    replay = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "message-lifecycle-export-emoji",
        "edit-message",
        payload,
    )

    update_calls = [request for request in client.requests if request[1].endswith("/chat.update")]
    assert first["status"] == "executed"
    assert first["result"]["message"]["msg"] == "Texto juridico revisado."
    assert replay["status"] == "replayed"
    assert len(update_calls) == 1
    assert SECRET not in json.dumps(first)


@pytest.mark.asyncio
async def test_create_user_receipt_never_persists_or_fingerprints_the_password(
    db_session,
    auth_context,
):
    await _link(db_session, auth_context.workspace_id)
    client = FakeRocketChatClient()
    service = _service(db_session, client)
    first_password = "Transient-Rocket-First-42"
    second_password = "Transient-Rocket-Second-99"
    payload = {
        "email": "qa@klm.local",
        "name": "QA KLM",
        "username": "qa-klm",
        "password": first_password,
        "idempotencyKey": "create-user-transient-001",
    }

    first = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "admin-users",
        "create-user",
        payload,
    )
    replay = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "admin-users",
        "create-user",
        {**payload, "password": second_password},
    )

    receipt = await service.links.find_receipt(
        auth_context.workspace_id,
        OSSBridgeProvider.rocketchat,
        "native:admin-users:create-user-transient-001",
    )
    assert receipt is not None
    assert first["status"] == "executed"
    assert replay["status"] == "replayed"
    assert receipt.payload["payload"]["password"] == REDACTED_SECRET
    assert receipt.payload_hash == compute_payload_hash(receipt.payload)
    assert first_password not in json.dumps(receipt.payload)
    assert second_password not in json.dumps(receipt.payload)
    create_calls = [
        request for request in client.requests if request[1].endswith("/users.create")
    ]
    assert len(create_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("capability_key", sorted(_GATED_CAPABILITIES))
async def test_gated_dry_runs_never_touch_rocket_network(
    db_session,
    auth_context,
    capability_key: str,
):
    client = FakeRocketChatClient(forbid_network=True)
    service = _service(db_session, client)

    result = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        capability_key,
        "dry-run",
        {"plan": {"operation": "local-review-only", "clientSecret": SECRET}},
    )

    assert result["status"] == "dry_run"
    assert result["result"]["networkExecuted"] is False
    assert result["result"]["wouldExecuteUpstream"] is False
    assert SECRET not in json.dumps(result)
    assert client.info_calls == 0
    assert not client.requests


@pytest.mark.asyncio
async def test_gated_local_policy_uses_late_native_state_import_without_network(
    db_session,
    auth_context,
    monkeypatch,
):
    calls: list[dict] = []
    module = ModuleType("app.services.oss_native_state_service")

    class OSSNativeStateService:
        def __init__(self, db):
            assert db is db_session

        async def execute(
            self,
            workspace_id,
            actor_user_id,
            provider,
            capability_key,
            command,
            payload,
        ):
            calls.append(
                {
                    "workspaceId": workspace_id,
                    "actorUserId": actor_user_id,
                    "provider": provider,
                    "capabilityKey": capability_key,
                    "command": command,
                    "payload": payload,
                }
            )
            return {"saved": True, "privateKey": SECRET}

    module.OSSNativeStateService = OSSNativeStateService  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    client = FakeRocketChatClient(forbid_network=True)
    service = _service(db_session, client)

    result = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "e2ee",
        "save-local-policy",
        {
            "confirmLocalOnly": True,
            "idempotencyKey": "e2ee-local-policy-001",
            "retentionDays": 30,
        },
    )

    assert result["status"] == "executed"
    assert result["result"]["storedLocally"] is True
    assert result["result"]["networkExecuted"] is False
    assert calls == [
        {
            "workspaceId": auth_context.workspace_id,
            "actorUserId": auth_context.user_id,
            "provider": "rocketchat",
            "capabilityKey": "e2ee",
            "command": "save-local-policy",
            "payload": {"retentionDays": 30},
        }
    ]
    assert SECRET not in json.dumps(result)
    assert client.info_calls == 0
    assert not client.requests
