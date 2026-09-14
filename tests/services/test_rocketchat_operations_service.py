"""Contracts for the reviewed Rocket.Chat operational projection."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.services.rocketchat_client import RocketChatClient
from app.services.rocketchat_operations_service import RocketChatOperationsService


@pytest.mark.asyncio
async def test_rocketchat_launch_target_uses_persisted_room_identity() -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    thread_id = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")
    service = object.__new__(RocketChatOperationsService)
    service._room_link = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            external_entity_id="room-case",
            link_metadata={"roomType": "p", "roomName": "caso-1"},
        )
    )
    service.client = SimpleNamespace(room_info=AsyncMock())

    target = await service.launch_target(workspace_id, thread_id)

    assert target == {"roomId": "room-case", "roomName": "caso-1", "roomType": "p"}
    service.client.room_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_rocketchat_context_is_thread_scoped_and_redacts_broad_payloads() -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    thread_id = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")
    service = object.__new__(RocketChatOperationsService)
    service.db = SimpleNamespace()
    service._room_link = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            external_entity_id="room-case", link_metadata={"roomType": "p"}
        )
    )
    service.client = SimpleNamespace(
        room_info=AsyncMock(
            return_value={"_id": "room-case", "name": "caso-1", "password": "hidden"}
        ),
        room_members=AsyncMock(return_value=[{"_id": "u1", "name": "Advogada", "token": "hidden"}]),
        room_history=AsyncMock(
            return_value=[
                {
                    "_id": "m1",
                    "msg": "Estratégia aprovada.",
                    "u": {"_id": "u1", "name": "Advogada", "authToken": "hidden"},
                    "attachments": [{"title": "parecer.pdf", "url": "/file"}],
                    "reactions": {":white_check_mark:": {"usernames": ["advogada"]}},
                    "privateKey": "hidden",
                }
            ]
        ),
        discussions=AsyncMock(
            return_value=[{"_id": "d1", "fname": "Tese liminar", "msgs": 3, "token": "hidden"}]
        ),
    )

    context = await service.context(workspace_id, thread_id)

    assert context["linked"] is True
    assert context["room"] == {"_id": "room-case", "name": "caso-1"}
    assert context["members"] == [{"_id": "u1", "name": "Advogada"}]
    assert context["messages"][0]["attachments"] == [{"title": "parecer.pdf"}]
    assert context["messages"][0]["attachmentCount"] == 1
    assert context["messages"][0]["reactions"] == [
        {"emoji": ":white_check_mark:", "count": 1, "usernames": ["advogada"]}
    ]
    assert context["messages"][0]["user"] == {"_id": "u1", "name": "Advogada"}
    assert context["discussions"] == [{"_id": "d1", "fname": "Tese liminar", "msgs": 3}]
    assert context["runtimeCompatibility"] == "6.13.1"
    assert context["auditedReference"] == "8.6.1"
    assert context["roomType"] == "p"


@pytest.mark.asyncio
async def test_rocketchat_client_uses_bounded_read_contracts() -> None:
    client = RocketChatClient.__new__(RocketChatClient)
    client._request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"group": {"_id": "r1", "name": "caso"}},
            {"members": [{"_id": "u1"}]},
            {"messages": [{"_id": "m1"}]},
            {"discussions": [{"_id": "d1"}]},
        ]
    )

    assert (await client.group_info("r1"))["_id"] == "r1"
    assert len(await client.group_members("r1", maximum=500)) == 1
    assert len(await client.group_history("r1", maximum=500)) == 1
    assert len(await client.discussions("r1", maximum=500)) == 1
    calls = client._request.await_args_list
    assert calls[1].kwargs["params"]["count"] == 100
    assert calls[2].kwargs["params"]["count"] == 100
    assert calls[3].kwargs["params"]["count"] == 100


@pytest.mark.asyncio
async def test_rocketchat_identity_uses_authenticated_me_contract(monkeypatch) -> None:
    client = RocketChatClient.__new__(RocketChatClient)

    async def fake_request(method: str, path: str, **_kwargs):
        assert (method, path) == ("GET", "/api/v1/me")
        return {"_id": "rocket-user", "username": "klm-admin"}

    monkeypatch.setattr(client, "_request", fake_request)

    assert (await client.identity())["username"] == "klm-admin"


@pytest.mark.asyncio
async def test_rocketchat_clients_share_one_concurrent_admin_login(monkeypatch) -> None:
    calls = 0

    async def fake_login(_client: RocketChatClient) -> dict[str, str]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"X-Auth-Token": "shared-token", "X-User-Id": "shared-user"}

    monkeypatch.setattr(RocketChatClient, "_shared_headers", None)
    monkeypatch.setattr(RocketChatClient, "_shared_cache_key", None)
    monkeypatch.setattr(RocketChatClient, "_shared_expires_at", 0.0)
    monkeypatch.setattr(RocketChatClient, "_shared_login_task", None)
    monkeypatch.setattr(RocketChatClient, "_perform_login", fake_login)

    clients = []
    for _ in range(3):
        client = RocketChatClient.__new__(RocketChatClient)
        client.base_url = "http://127.0.0.1:3200"
        client.username = "klm-admin"
        client.password = "local-password"
        client._headers = None
        clients.append(client)

    headers = await asyncio.gather(*(client._login() for client in clients))

    assert calls == 1
    assert headers == [
        {"X-Auth-Token": "shared-token", "X-User-Id": "shared-user"}
    ] * 3
