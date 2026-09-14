from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.core.security import create_platform_websocket_ticket, verify_token
from app.db.post_commit import publish_committed_realtime_events
from app.schemas.support_ticket import SupportTicketCreate
from app.services.support_ticket_service import SupportTicketService
from app.websockets.endpoint import (
    PLATFORM_WEBSOCKET_SUBPROTOCOL,
    _extract_platform_ticket,
    authenticate_platform_websocket_ticket,
)
from app.websockets.manager import ws_manager
from app.websockets.platform_manager import (
    PlatformWebSocketIdentity,
    PlatformWebSocketManager,
    platform_ws_manager,
)


class FakePlatformWebSocket:
    def __init__(self) -> None:
        self.accepted_subprotocol: str | None = None
        self.closed_with: int | None = None
        self.messages: list[str] = []

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted_subprotocol = subprotocol

    async def send_text(self, payload: str) -> None:
        self.messages.append(payload)

    async def close(self, code: int) -> None:
        self.closed_with = code


def test_platform_ticket_is_read_only_from_dedicated_subprotocol():
    accepted = type(
        "Handshake",
        (),
        {
            "headers": {
                "sec-websocket-protocol": (
                    f"{PLATFORM_WEBSOCKET_SUBPROTOCOL}, ticket.platform-jwt"
                )
            }
        },
    )()
    workspace_protocol = type(
        "Handshake",
        (),
        {
            "headers": {
                "sec-websocket-protocol": "klm.realtime.v1, ticket.workspace-jwt"
            }
        },
    )()

    assert _extract_platform_ticket(accepted) == "platform-jwt"
    assert _extract_platform_ticket(workspace_protocol) is None


@pytest.mark.asyncio
async def test_platform_websocket_token_has_narrow_scope_and_is_one_time(
    monkeypatch,
):
    user_id = uuid4()
    session_id = uuid4()
    token = create_platform_websocket_ticket(
        user_id,
        session_id=session_id,
        auth_epoch=7,
        session_issued_at_ns=123456789,
    )
    payload = verify_token(token)

    assert payload is not None
    assert payload["token_type"] == "platform_websocket"
    assert payload["scope"] == "support:view"
    assert payload["sub"] == str(user_id)
    assert payload["session_id"] == str(session_id)
    assert payload["auth_epoch"] == 7

    monkeypatch.setattr(
        "app.websockets.endpoint.revalidate_platform_identity",
        AsyncMock(return_value=True),
    )
    consume = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr("app.websockets.endpoint.consume_token", consume)

    identity = await authenticate_platform_websocket_ticket(token)
    replay = await authenticate_platform_websocket_ticket(token)

    assert identity == PlatformWebSocketIdentity(
        user_id=user_id,
        session_id=session_id,
        auth_epoch=7,
        token_id=payload["jti"],
    )
    assert replay is None
    assert consume.await_count == 2


@pytest.mark.asyncio
async def test_ws_ticket_endpoint_requires_session_and_support_view_permission(
    support_factory,
):
    root, _ = await support_factory.user(is_platform_admin=True, name="Platform Root")
    root_context = support_factory.context(root)
    async with support_factory.client(root_context) as api:
        no_session = await api.post("/api/v1/platform/support/ws-ticket")
    assert no_session.status_code == 401

    root_context.session_id = uuid4()
    root_context.session_issued_at_ns = 987654321
    async with support_factory.client(root_context) as api:
        issued = await api.post("/api/v1/platform/support/ws-ticket")
    assert issued.status_code == 200
    payload = verify_token(issued.json()["ticket"])
    assert payload is not None
    assert payload["scope"] == "support:view"
    assert payload["session_id"] == str(root_context.session_id)
    assert issued.json()["expiresIn"] == 30

    finance, _ = await support_factory.user(
        name="Finance Staff",
        platform_role_package="finance_billing",
    )
    finance_context = support_factory.context(finance)
    finance_context.session_id = uuid4()
    async with support_factory.client(finance_context) as api:
        denied = await api.post("/api/v1/platform/support/ws-ticket")
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_platform_signal_contains_only_reload_identifiers(monkeypatch):
    manager = PlatformWebSocketManager()
    captured: list[dict] = []

    async def capture(message):
        captured.append(message)

    monkeypatch.setattr(manager, "broadcast_local", capture)
    workspace_id = uuid4()
    ticket_id = uuid4()

    await manager.publish_support_signal(
        workspace_id=workspace_id,
        event_type="SUPPORT_TICKET_UPDATED",
        data={
            "ticketId": str(ticket_id),
            "version": 8,
            "subject": "Must not leave the canonical API",
            "description": "Sensitive customer content",
            "technicalContext": {"authorization": "Bearer secret"},
        },
    )

    assert captured == [
        {
            "event": "SUPPORT_TICKET_UPDATED",
            "data": {
                "ticketId": str(ticket_id),
                "version": 8,
                "workspaceId": str(workspace_id),
            },
        }
    ]


@pytest.mark.asyncio
async def test_platform_manager_revalidates_connections_before_broadcast(monkeypatch):
    manager = PlatformWebSocketManager()
    websocket = FakePlatformWebSocket()
    identity = PlatformWebSocketIdentity(
        user_id=uuid4(),
        session_id=uuid4(),
        auth_epoch=0,
        token_id="platform-session:1",
    )
    revalidate = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.websockets.platform_manager.revalidate_platform_identity",
        revalidate,
    )
    await manager.connect(
        websocket,
        identity,
        subprotocol=PLATFORM_WEBSOCKET_SUBPROTOCOL,
    )

    await manager.broadcast_local(
        {
            "event": "SUPPORT_MESSAGE_ADDED",
            "data": {"ticketId": str(uuid4()), "version": 2},
        }
    )
    assert websocket.accepted_subprotocol == PLATFORM_WEBSOCKET_SUBPROTOCOL
    assert json.loads(websocket.messages[0])["event"] == "SUPPORT_MESSAGE_ADDED"

    revalidate.return_value = False
    await manager.broadcast_local(
        {
            "event": "SUPPORT_TICKET_UPDATED",
            "data": {"ticketId": str(uuid4()), "version": 3},
        }
    )
    assert websocket.closed_with == 4401
    assert len(websocket.messages) == 1


@pytest.mark.asyncio
async def test_committed_support_event_fans_out_to_workspace_and_platform_channels(
    db_session,
    auth_context,
    support_ticket_payload,
    monkeypatch,
):
    workspace_broadcast = AsyncMock()
    platform_publish = AsyncMock()
    monkeypatch.setattr(ws_manager, "broadcast_board_update", workspace_broadcast)
    monkeypatch.setattr(
        platform_ws_manager,
        "publish_support_signal",
        platform_publish,
    )
    payload = SupportTicketCreate.model_validate(support_ticket_payload())
    ticket, _ = await SupportTicketService(db_session).create_ticket(
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        payload=payload,
        idempotency_key="websocket-signal-ticket",
    )

    await publish_committed_realtime_events(db_session)

    workspace_broadcast.assert_awaited_once_with(
        "SUPPORT_TICKET_CREATED",
        {"ticketId": str(ticket.id), "version": 1},
        workspace_id=auth_context.workspace_id,
    )
    platform_publish.assert_awaited_once_with(
        workspace_id=auth_context.workspace_id,
        event_type="SUPPORT_TICKET_CREATED",
        data={"ticketId": str(ticket.id), "version": 1},
    )
    assert UUID(
        platform_publish.await_args.kwargs["data"]["ticketId"]
    ) == ticket.id
