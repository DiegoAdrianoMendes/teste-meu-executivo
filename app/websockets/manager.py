"""Distribuição de eventos em tempo real isolada por workspace."""
from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import WebSocket

from app.core.config import settings
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.core.redis_client import get_shared_redis_client
from app.core.token_store import revoke_websocket_sessions
from app.websockets.security import WebSocketIdentity, revalidate_websocket_identity

REDIS_CHANNEL = "klm:board:events"
CONTROL_DISCONNECT_USER = "disconnect_user"
CONTROL_DISCONNECT_USER_SESSIONS = "disconnect_user_sessions"
CONTROL_DISCONNECT_SESSION = "disconnect_session"


class WebSocketManager:
    """Mantém conexões autenticadas e nunca transmite entre workspaces."""

    def __init__(self) -> None:
        self._connections: dict[WebSocket, WebSocketIdentity] = {}
        self._listener_task: asyncio.Task | None = None

    @property
    def total_connections(self) -> int:
        return len(self._connections)

    def connection_count(self, workspace_id: UUID) -> int:
        return sum(
            1 for connection in self._connections.values()
            if connection.workspace_id == workspace_id
        )

    async def connect(
        self,
        websocket: WebSocket,
        *,
        workspace_id: UUID,
        user_id: UUID,
        membership_id: UUID,
        session_id: UUID,
        auth_epoch: int,
        token_id: str,
        subprotocol: str | None = None,
    ) -> None:
        await websocket.accept(subprotocol=subprotocol)
        self._connections[websocket] = WebSocketIdentity(
            workspace_id=workspace_id,
            user_id=user_id,
            membership_id=membership_id,
            session_id=session_id,
            auth_epoch=auth_epoch,
            token_id=token_id,
        )
        logger.debug(
            "ws_client_connected",
            workspace_id=str(workspace_id),
            user_id=str(user_id),
            workspace_connections=self.connection_count(workspace_id),
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        connection = self._connections.pop(websocket, None)
        if connection:
            logger.debug(
                "ws_client_disconnected",
                workspace_id=str(connection.workspace_id),
                user_id=str(connection.user_id),
                workspace_connections=self.connection_count(connection.workspace_id),
            )

    async def _connection_is_active(self, connection: WebSocketIdentity) -> bool:
        from app.db.session import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as db:
                return await revalidate_websocket_identity(connection, db)
        except Exception as exc:
            logger.error("ws_identity_revalidation_failed", error_type=safe_error_type(exc))
            return False

    async def validate_connection(self, websocket: WebSocket, *, code: int = 4401) -> bool:
        connection = self._connections.get(websocket)
        if connection is None:
            return False
        if await self._connection_is_active(connection):
            return True
        self._connections.pop(websocket, None)
        try:
            await websocket.close(code=code)
        except Exception:
            pass
        return False

    async def disconnect_user(
        self,
        workspace_id: UUID,
        user_id: UUID,
        *,
        code: int = 4403,
    ) -> None:
        await self._disconnect_user_local(workspace_id, user_id, code=code)
        await self._publish_control(
            {
                "kind": CONTROL_DISCONNECT_USER,
                "workspace_id": str(workspace_id),
                "user_id": str(user_id),
                "code": code,
            }
        )

    async def _disconnect_user_local(
        self,
        workspace_id: UUID,
        user_id: UUID,
        *,
        code: int,
    ) -> None:
        matches = [
            websocket
            for websocket, connection in self._connections.items()
            if connection.workspace_id == workspace_id and connection.user_id == user_id
        ]
        for websocket in matches:
            self._connections.pop(websocket, None)
            try:
                await websocket.close(code=code)
            except Exception:
                pass

    async def disconnect_user_sessions(
        self,
        user_id: UUID,
        *,
        code: int = 4401,
    ) -> None:
        """Fecha todas as conexões locais de um usuário, em qualquer workspace."""
        await self._disconnect_user_sessions_local(user_id, code=code)
        await revoke_websocket_sessions(str(user_id))
        await self._publish_control(
            {
                "kind": CONTROL_DISCONNECT_USER_SESSIONS,
                "user_id": str(user_id),
                "code": code,
            }
        )

    async def disconnect_session(
        self,
        session_id: UUID,
        *,
        code: int = 4401,
    ) -> None:
        await self._disconnect_session_local(session_id, code=code)
        await self._publish_control(
            {
                "kind": CONTROL_DISCONNECT_SESSION,
                "session_id": str(session_id),
                "code": code,
            }
        )

    async def disconnect_sessions(
        self,
        session_ids: list[UUID],
        *,
        code: int = 4401,
    ) -> None:
        for session_id in session_ids:
            await self.disconnect_session(session_id, code=code)

    async def _disconnect_session_local(
        self,
        session_id: UUID,
        *,
        code: int,
    ) -> None:
        matches = [
            websocket
            for websocket, connection in self._connections.items()
            if connection.session_id == session_id
        ]
        for websocket in matches:
            self._connections.pop(websocket, None)
            try:
                await websocket.close(code=code)
            except Exception:
                pass

    async def _disconnect_user_sessions_local(
        self,
        user_id: UUID,
        *,
        code: int,
    ) -> None:
        matches = [
            websocket
            for websocket, connection in self._connections.items()
            if connection.user_id == user_id
        ]
        for websocket in matches:
            self._connections.pop(websocket, None)
            try:
                await websocket.close(code=code)
            except Exception:
                pass

    async def broadcast_local(
        self,
        workspace_id: UUID,
        message: dict[str, Any],
    ) -> None:
        payload = json.dumps(message, default=str)
        dead_connections: list[WebSocket] = []
        for websocket, connection in list(self._connections.items()):
            if connection.workspace_id != workspace_id:
                continue
            if not await self.validate_connection(websocket):
                continue
            try:
                await websocket.send_text(payload)
            except Exception:
                dead_connections.append(websocket)
        for websocket in dead_connections:
            self._connections.pop(websocket, None)

    async def broadcast_board_update(
        self,
        event_type: str,
        data: dict[str, Any],
        *,
        workspace_id: UUID,
    ) -> None:
        message = {"event": event_type, "data": data}
        envelope = {"kind": "event", "workspace_id": str(workspace_id), **message}
        published = await self._publish_to_redis(envelope)
        if not published:
            await self.broadcast_local(workspace_id, message)

    async def _publish_to_redis(self, envelope: dict[str, Any]) -> bool:
        if settings.uses_in_memory_redis:
            return False
        try:
            client = await get_shared_redis_client(
                settings.REDIS_URL,
                decode_responses=True,
                factory=aioredis.from_url,
            )
            subscribers = await client.publish(
                REDIS_CHANNEL,
                json.dumps(envelope, default=str),
            )
            return subscribers > 0
        except Exception as exc:
            logger.warning("redis_publish_failed", error_type=safe_error_type(exc))
            return False

    async def _publish_control(self, envelope: dict[str, Any]) -> None:
        """Best-effort fan-out; local disconnect already happened first."""
        if settings.uses_in_memory_redis:
            return
        await self._publish_to_redis(envelope)

    async def _handle_redis_envelope(self, envelope: dict[str, Any]) -> None:
        """Dispatch one validated event/control message without republishing it."""
        kind = envelope.get("kind", "event")
        try:
            if kind == CONTROL_DISCONNECT_USER:
                await self._disconnect_user_local(
                    UUID(str(envelope["workspace_id"])),
                    UUID(str(envelope["user_id"])),
                    code=int(envelope.get("code", 4403)),
                )
                return
            if kind == CONTROL_DISCONNECT_USER_SESSIONS:
                await self._disconnect_user_sessions_local(
                    UUID(str(envelope["user_id"])),
                    code=int(envelope.get("code", 4401)),
                )
                return
            if kind == CONTROL_DISCONNECT_SESSION:
                await self._disconnect_session_local(
                    UUID(str(envelope["session_id"])),
                    code=int(envelope.get("code", 4401)),
                )
                return
            if kind != "event":
                raise ValueError("unknown websocket envelope kind")
            workspace_id = UUID(str(envelope["workspace_id"]))
            message = {
                key: value
                for key, value in envelope.items()
                if key not in {"kind", "workspace_id"}
            }
        except (KeyError, TypeError, ValueError):
            logger.warning("ws_invalid_redis_envelope")
            return
        await self.broadcast_local(workspace_id, message)

    async def start_redis_listener(self) -> None:
        if settings.uses_in_memory_redis:
            logger.debug("ws_local_broadcast_only")
            return
        # Lifespan start can be invoked more than once by a reload/test
        # harness.  A second PubSub listener consumes another connection and
        # overwrites the only task reference, leaving the first listener
        # impossible to stop.  Keep exactly one retained listener per process
        # just as PlatformWebSocketManager already does.
        if self._listener_task is not None and not self._listener_task.done():
            return
        self._listener_task = None
        self._listener_task = asyncio.create_task(self._redis_listener_loop())
        logger.info("ws_redis_listener_started")

    async def stop_redis_listener(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

    async def _redis_listener_loop(self) -> None:
        retry_delay = 1.0
        while True:
            client = None
            pubsub = None
            try:
                client = await get_shared_redis_client(
                    settings.REDIS_URL,
                    decode_responses=True,
                    factory=aioredis.from_url,
                    socket_timeout=None,
                )
                pubsub = client.pubsub()
                await pubsub.subscribe(REDIS_CHANNEL)
                retry_delay = 1.0
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        envelope = json.loads(message["data"])
                        if not isinstance(envelope, dict):
                            raise TypeError("websocket envelope must be an object")
                    except (json.JSONDecodeError, TypeError):
                        logger.warning("ws_invalid_redis_envelope")
                        continue
                    await self._handle_redis_envelope(envelope)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "redis_listener_error",
                    error_type=safe_error_type(exc),
                    retry_seconds=retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)
            finally:
                if pubsub is not None:
                    await pubsub.aclose()


ws_manager = WebSocketManager()
