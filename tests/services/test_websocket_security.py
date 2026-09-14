import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio

from app.core import token_store
from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_session_token_id,
    create_websocket_ticket,
    parse_session_token_id,
    verify_token,
)
from app.core.token_store import revoke_user_tokens
from app.models.identity import UserSession
from app.models.user import User
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.websockets.endpoint import (
    WEBSOCKET_IDLE_CLOSE_CODE,
    _extract_ticket,
    _origin_allowed,
    _receive_text_with_idle_timeout,
    authenticate_websocket_ticket,
)
from app.websockets.manager import WebSocketManager
from app.websockets.security import revalidate_websocket_identity


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.closed_with: int | None = None
        self.messages: list[str] = []

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = True

    async def send_text(self, payload: str) -> None:
        self.messages.append(payload)

    async def close(self, code: int) -> None:
        self.closed_with = code


@pytest_asyncio.fixture
async def websocket_session(db_session, auth_context) -> UserSession:
    session = UserSession(
        id=uuid4(),
        user_id=auth_context.user_id,
        refresh_jti_digest=f"digest-{uuid4().hex}",
        device_label="Browser de teste",
        device_type="desktop",
        browser="Chrome",
        operating_system="Linux",
        last_workspace_id=auth_context.workspace_id,
        created_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(session)
    await db_session.flush()
    return session


def _ticket(auth_context, session: UserSession, **overrides) -> str:
    return create_websocket_ticket(
        auth_context.user_id,
        workspace_id=auth_context.workspace_id,
        membership_id=auth_context.membership_id,
        session_id=session.id,
        auth_epoch=auth_context.auth_epoch,
        **overrides,
    )


def test_websocket_ticket_is_read_from_subprotocol_not_url():
    websocket = type(
        "Handshake",
        (),
        {"headers": {"sec-websocket-protocol": "klm.realtime.v1, ticket.jwt-value"}},
    )()
    assert _extract_ticket(websocket) == "jwt-value"


def test_websocket_origin_uses_the_explicit_local_allowlist(monkeypatch):
    monkeypatch.setattr(
        settings,
        "CORS_ORIGINS",
        ["http://127.0.0.1:4173", "http://127.0.0.1:4174"],
    )
    allowed = type("Handshake", (), {"headers": {"origin": "http://127.0.0.1:4174"}})()
    denied = type("Handshake", (), {"headers": {"origin": "http://127.0.0.1:9999"}})()

    assert _origin_allowed(allowed) is True
    assert _origin_allowed(denied) is False


@pytest.mark.asyncio
async def test_websocket_idle_receive_times_out(monkeypatch):
    class IdleWebSocket:
        async def receive_text(self):
            await asyncio.sleep(1)
            return "late"

    monkeypatch.setattr(settings, "WEBSOCKET_IDLE_TIMEOUT_SECONDS", 0.001)

    with pytest.raises(TimeoutError):
        await _receive_text_with_idle_timeout(IdleWebSocket())

    assert WEBSOCKET_IDLE_CLOSE_CODE == 4408


@pytest.mark.asyncio
async def test_workspace_redis_listener_is_singleton_across_repeated_startup(monkeypatch):
    """Repeated lifespan startup must not leak an orphaned PubSub listener."""
    manager = WebSocketManager()
    started = 0
    release = asyncio.Event()

    async def listener():
        nonlocal started
        started += 1
        await release.wait()

    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(manager, "_redis_listener_loop", listener)
    await manager.start_redis_listener()
    await manager.start_redis_listener()
    await asyncio.sleep(0)

    assert started == 1
    assert manager._listener_task is not None

    # stop uses cancellation, so the listener must not need an external Redis
    # response to release its connection/task during shutdown.
    await manager.stop_redis_listener()
    assert manager._listener_task is None


@pytest.mark.asyncio
async def test_websocket_manager_broadcasts_only_inside_workspace(monkeypatch):
    manager = WebSocketManager()
    workspace_a = uuid4()
    workspace_b = uuid4()
    socket_a = FakeWebSocket()
    socket_b = FakeWebSocket()

    async def active(_identity):
        return True

    monkeypatch.setattr(manager, "_connection_is_active", active)
    await manager.connect(
        socket_a,
        workspace_id=workspace_a,
        user_id=uuid4(),
        membership_id=uuid4(),
        session_id=uuid4(),
        auth_epoch=0,
        token_id="session-a",
    )
    await manager.connect(
        socket_b,
        workspace_id=workspace_b,
        user_id=uuid4(),
        membership_id=uuid4(),
        session_id=uuid4(),
        auth_epoch=0,
        token_id="session-b",
    )
    await manager.broadcast_local(workspace_a, {"event": "CARD_UPDATED", "data": {"id": "a"}})

    assert socket_a.accepted is True
    assert socket_b.accepted is True
    assert len(socket_a.messages) == 1
    assert socket_b.messages == []
    assert manager.connection_count(workspace_a) == 1
    assert manager.connection_count(workspace_b) == 1


@pytest.mark.asyncio
async def test_websocket_ticket_requires_active_matching_membership(
    db_session,
    auth_context,
    websocket_session,
):
    ticket = _ticket(auth_context, websocket_session)
    identity = await authenticate_websocket_ticket(ticket, db_session)
    assert identity is not None
    assert identity.workspace_id == auth_context.workspace_id
    assert identity.user_id == auth_context.user_id
    assert identity.membership_id == auth_context.membership_id
    assert identity.token_id

    access_token = create_access_token(
        auth_context.user_id,
        workspace_id=auth_context.workspace_id,
        membership_id=auth_context.membership_id,
    )
    assert await authenticate_websocket_ticket(access_token, db_session) is None

    suspended_ticket = _ticket(auth_context, websocket_session)
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.status = MembershipStatus.suspended
    await db_session.flush()
    assert await authenticate_websocket_ticket(suspended_ticket, db_session) is None


@pytest.mark.asyncio
async def test_websocket_ticket_replay_is_rejected(
    db_session,
    auth_context,
    websocket_session,
):
    ticket = _ticket(auth_context, websocket_session)

    assert await authenticate_websocket_ticket(ticket, db_session) is not None
    assert await authenticate_websocket_ticket(ticket, db_session) is None


@pytest.mark.asyncio
async def test_websocket_reconnect_gets_a_distinct_one_time_ticket(
    db_session,
    auth_context,
    websocket_session,
):
    session_epoch = 123_456_789
    first = _ticket(
        auth_context,
        websocket_session,
        session_issued_at_ns=session_epoch,
    )
    second = _ticket(
        auth_context,
        websocket_session,
        session_issued_at_ns=session_epoch,
    )
    first_payload = verify_token(first)
    second_payload = verify_token(second)

    assert first != second
    assert first_payload is not None
    assert second_payload is not None
    assert first_payload["jti"] != second_payload["jti"]
    assert parse_session_token_id(first_payload["jti"])[1] == session_epoch
    assert parse_session_token_id(second_payload["jti"])[1] == session_epoch
    assert await authenticate_websocket_ticket(first, db_session) is not None
    assert await authenticate_websocket_ticket(second, db_session) is not None
    assert await authenticate_websocket_ticket(first, db_session) is None
    assert await authenticate_websocket_ticket(second, db_session) is None


@pytest.mark.asyncio
async def test_websocket_ticket_is_rejected_after_user_session_revocation(
    db_session,
    auth_context,
    websocket_session,
):
    ticket = _ticket(auth_context, websocket_session)
    websocket_session.revoked_at = datetime.now(UTC)
    websocket_session.revocation_reason = "user_requested"
    await db_session.flush()

    assert await authenticate_websocket_ticket(ticket, db_session) is None


@pytest.mark.asyncio
async def test_connection_revalidation_uses_durable_session_marker_after_handshake(
    db_session,
    auth_context,
    websocket_session,
):
    ticket = _ticket(auth_context, websocket_session)
    identity = await authenticate_websocket_ticket(ticket, db_session)
    assert identity is not None
    assert await revalidate_websocket_identity(identity, db_session) is True

    await revoke_user_tokens(str(auth_context.user_id))

    assert await revalidate_websocket_identity(identity, db_session) is False


@pytest.mark.asyncio
async def test_connection_revalidation_rejects_stale_auth_epoch(
    db_session,
    auth_context,
    websocket_session,
):
    ticket = _ticket(auth_context, websocket_session)
    identity = await authenticate_websocket_ticket(ticket, db_session)
    assert identity is not None

    user = await db_session.get(User, auth_context.user_id)
    user.auth_epoch += 1
    await db_session.flush()

    assert await revalidate_websocket_identity(identity, db_session) is False


@pytest.mark.asyncio
async def test_broadcast_closes_connection_when_durable_membership_is_revoked(
    db_session,
    auth_context,
    monkeypatch,
    websocket_session,
):
    ticket = _ticket(auth_context, websocket_session)
    identity = await authenticate_websocket_ticket(ticket, db_session)
    assert identity is not None

    manager = WebSocketManager()
    socket = FakeWebSocket()
    await manager.connect(
        socket,
        workspace_id=identity.workspace_id,
        user_id=identity.user_id,
        membership_id=identity.membership_id,
        session_id=identity.session_id,
        auth_epoch=identity.auth_epoch,
        token_id=identity.token_id,
    )

    async def validate(connection):
        return await revalidate_websocket_identity(connection, db_session)

    monkeypatch.setattr(manager, "_connection_is_active", validate)
    membership = await db_session.get(WorkspaceMembership, identity.membership_id)
    membership.status = MembershipStatus.suspended
    await db_session.flush()

    await manager.broadcast_local(identity.workspace_id, {"event": "SHOULD_NOT_LEAK"})

    assert socket.messages == []
    assert socket.closed_with == 4401
    assert manager.total_connections == 0


@pytest.mark.asyncio
async def test_ping_validation_closes_connection_after_durable_websocket_revocation_without_pubsub(
    db_session,
    auth_context,
    monkeypatch,
    websocket_session,
):
    ticket = _ticket(auth_context, websocket_session)
    identity = await authenticate_websocket_ticket(ticket, db_session)
    assert identity is not None

    manager = WebSocketManager()
    socket = FakeWebSocket()
    await manager.connect(
        socket,
        workspace_id=identity.workspace_id,
        user_id=identity.user_id,
        membership_id=identity.membership_id,
        session_id=identity.session_id,
        auth_epoch=identity.auth_epoch,
        token_id=identity.token_id,
    )

    async def validate(connection):
        return await revalidate_websocket_identity(connection, db_session)

    monkeypatch.setattr(manager, "_connection_is_active", validate)
    websocket_session.revoked_at = datetime.now(UTC)
    websocket_session.revocation_reason = "user_requested"
    await db_session.flush()

    assert await manager.validate_connection(socket) is False
    assert socket.closed_with == 4401
    assert manager.total_connections == 0


@pytest.mark.asyncio
async def test_remote_worker_observes_persistent_redis_websocket_marker_without_pubsub(
    db_session,
    auth_context,
    monkeypatch,
    websocket_session,
):
    class FakeRedis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        async def eval(self, _script, _numkeys, key, marker, ttl):
            current = self.values.get(key, "0")
            if int(marker) > int(current):
                self.values[key] = marker
            assert ttl == ""
            return 1

        async def get(self, key):
            return self.values.get(key)

        async def aclose(self):
            return None

    redis = FakeRedis()

    async def from_url(*_args, **_kwargs):
        return redis

    async def no_publish(_envelope):
        return False

    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(token_store.aioredis, "from_url", from_url)
    revoking_worker = WebSocketManager()
    monkeypatch.setattr(revoking_worker, "_publish_to_redis", no_publish)
    remote_worker = WebSocketManager()
    socket = FakeWebSocket()
    await remote_worker.connect(
        socket,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        membership_id=auth_context.membership_id,
        session_id=websocket_session.id,
        auth_epoch=auth_context.auth_epoch,
        token_id=create_session_token_id(auth_context.user_id),
    )

    async def validate(connection):
        return await revalidate_websocket_identity(connection, db_session)

    monkeypatch.setattr(remote_worker, "_connection_is_active", validate)
    await revoking_worker.disconnect_user_sessions(auth_context.user_id)

    assert await remote_worker.validate_connection(socket) is False
    assert socket.closed_with == 4401
    assert any(key.startswith("auth:websocket-revoked-before:") for key in redis.values)


@pytest.mark.asyncio
async def test_disconnect_user_closes_only_target_connection():
    manager = WebSocketManager()
    workspace = uuid4()
    target_user = uuid4()
    other_user = uuid4()
    target_socket = FakeWebSocket()
    other_socket = FakeWebSocket()

    await manager.connect(
        target_socket,
        workspace_id=workspace,
        user_id=target_user,
        membership_id=uuid4(),
        session_id=uuid4(),
        auth_epoch=0,
        token_id="target-session",
    )
    await manager.connect(
        other_socket,
        workspace_id=workspace,
        user_id=other_user,
        membership_id=uuid4(),
        session_id=uuid4(),
        auth_epoch=0,
        token_id="other-session",
    )
    await manager.disconnect_user(workspace, target_user)

    assert target_socket.closed_with == 4403
    assert other_socket.closed_with is None
    assert manager.connection_count(workspace) == 1


@pytest.mark.asyncio
async def test_disconnect_session_closes_only_target_family():
    manager = WebSocketManager()
    workspace = uuid4()
    user_id = uuid4()
    target_session = uuid4()
    other_session = uuid4()
    target_socket = FakeWebSocket()
    other_socket = FakeWebSocket()

    await manager.connect(
        target_socket,
        workspace_id=workspace,
        user_id=user_id,
        membership_id=uuid4(),
        session_id=target_session,
        auth_epoch=0,
        token_id="target-family",
    )
    await manager.connect(
        other_socket,
        workspace_id=workspace,
        user_id=user_id,
        membership_id=uuid4(),
        session_id=other_session,
        auth_epoch=0,
        token_id="other-family",
    )

    await manager.disconnect_session(target_session)

    assert target_socket.closed_with == 4401
    assert other_socket.closed_with is None
    assert manager.total_connections == 1


@pytest.mark.asyncio
async def test_disconnect_user_sessions_closes_target_across_local_workspaces():
    manager = WebSocketManager()
    workspace_a = uuid4()
    workspace_b = uuid4()
    target_user = uuid4()
    target_socket_a = FakeWebSocket()
    target_socket_b = FakeWebSocket()
    other_socket = FakeWebSocket()

    await manager.connect(
        target_socket_a,
        workspace_id=workspace_a,
        user_id=target_user,
        membership_id=uuid4(),
        session_id=uuid4(),
        auth_epoch=0,
        token_id="target-a",
    )
    await manager.connect(
        target_socket_b,
        workspace_id=workspace_b,
        user_id=target_user,
        membership_id=uuid4(),
        session_id=uuid4(),
        auth_epoch=0,
        token_id="target-b",
    )
    await manager.connect(
        other_socket,
        workspace_id=workspace_b,
        user_id=uuid4(),
        membership_id=uuid4(),
        session_id=uuid4(),
        auth_epoch=0,
        token_id="other",
    )

    await manager.disconnect_user_sessions(target_user, code=4401)

    assert target_socket_a.closed_with == 4401
    assert target_socket_b.closed_with == 4401
    assert other_socket.closed_with is None
    assert manager.total_connections == 1


@pytest.mark.asyncio
async def test_disconnect_control_is_fanned_out_to_other_workers(monkeypatch):
    worker_a = WebSocketManager()
    worker_b = WebSocketManager()
    workspace = uuid4()
    target_user = uuid4()
    local_socket = FakeWebSocket()
    remote_socket = FakeWebSocket()
    other_socket = FakeWebSocket()
    published: list[dict] = []

    await worker_a.connect(
        local_socket,
        workspace_id=workspace,
        user_id=target_user,
        membership_id=uuid4(),
        session_id=uuid4(),
        auth_epoch=0,
        token_id="local",
    )
    await worker_b.connect(
        remote_socket,
        workspace_id=workspace,
        user_id=target_user,
        membership_id=uuid4(),
        session_id=uuid4(),
        auth_epoch=0,
        token_id="remote",
    )
    await worker_b.connect(
        other_socket,
        workspace_id=workspace,
        user_id=uuid4(),
        membership_id=uuid4(),
        session_id=uuid4(),
        auth_epoch=0,
        token_id="other",
    )
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")

    async def capture(envelope):
        published.append(envelope)
        return True

    monkeypatch.setattr(worker_a, "_publish_to_redis", capture)
    await worker_a.disconnect_user(workspace, target_user)
    await worker_b._handle_redis_envelope(published[0])

    assert local_socket.closed_with == 4403
    assert remote_socket.closed_with == 4403
    assert other_socket.closed_with is None
