"""Canal separado de sinais do SAC para a equipe interna do SaaS."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import WebSocket
from sqlalchemy import select

from app.core.config import settings
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.core.platform_rbac import has_platform_permission
from app.core.redis_client import get_shared_redis_client
from app.core.token_store import is_websocket_session_revoked
from app.models.identity import UserSession
from app.models.user import User

PLATFORM_SUPPORT_REDIS_CHANNEL = "klm:platform:support:events"


@dataclass(frozen=True)
class PlatformWebSocketIdentity:
    user_id: UUID
    session_id: UUID
    auth_epoch: int
    token_id: str


async def revalidate_platform_identity(
    identity: PlatformWebSocketIdentity,
) -> bool:
    if await is_websocket_session_revoked(identity.token_id):
        return False
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User)
            .join(
                UserSession,
                (UserSession.id == identity.session_id)
                & (UserSession.user_id == identity.user_id),
            )
            .where(
                User.id == identity.user_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
                User.email_verified_at.is_not(None),
                User.auth_epoch == identity.auth_epoch,
                User.platform_access_active.is_(True),
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > datetime.now(UTC),
            )
        )
        user = result.scalar_one_or_none()
        return user is not None and has_platform_permission(user, "support", "view")


class PlatformWebSocketManager:
    """Fan-out mínimo; o painel sempre recarrega o estado canônico por HTTP."""

    def __init__(self) -> None:
        self._connections: dict[WebSocket, PlatformWebSocketIdentity] = {}
        self._listener_task: asyncio.Task | None = None

    async def connect(
        self,
        websocket: WebSocket,
        identity: PlatformWebSocketIdentity,
        *,
        subprotocol: str,
    ) -> None:
        await websocket.accept(subprotocol=subprotocol)
        self._connections[websocket] = identity

    async def disconnect(self, websocket: WebSocket) -> None:
        self._connections.pop(websocket, None)

    async def validate(self, websocket: WebSocket) -> bool:
        identity = self._connections.get(websocket)
        if identity is None:
            return False
        if await revalidate_platform_identity(identity):
            return True
        self._connections.pop(websocket, None)
        try:
            await websocket.close(code=4401)
        except Exception:
            pass
        return False

    async def broadcast_local(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, default=str)
        dead: list[WebSocket] = []
        for websocket in list(self._connections):
            if not await self.validate(websocket):
                continue
            try:
                await websocket.send_text(payload)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            self._connections.pop(websocket, None)

    async def publish_support_signal(
        self,
        *,
        workspace_id: UUID,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        safe_data = {
            "ticketId": str(data.get("ticketId") or ""),
            "version": int(data.get("version") or 0),
            "workspaceId": str(workspace_id),
        }
        message = {"event": event_type, "data": safe_data}
        if settings.uses_in_memory_redis:
            await self.broadcast_local(message)
            return
        try:
            client = await get_shared_redis_client(
                settings.REDIS_URL,
                decode_responses=True,
                factory=aioredis.from_url,
            )
            subscribers = await client.publish(
                PLATFORM_SUPPORT_REDIS_CHANNEL,
                json.dumps(message),
            )
            if subscribers == 0:
                await self.broadcast_local(message)
        except Exception as error:
            logger.warning(
                "platform_support_realtime_publish_failed",
                error_type=safe_error_type(error),
            )
            await self.broadcast_local(message)

    async def start_redis_listener(self) -> None:
        if settings.uses_in_memory_redis or self._listener_task is not None:
            return
        self._listener_task = asyncio.create_task(self._redis_listener_loop())

    async def stop_redis_listener(self) -> None:
        if self._listener_task is None:
            return
        self._listener_task.cancel()
        try:
            await self._listener_task
        except asyncio.CancelledError:
            pass
        self._listener_task = None

    async def _redis_listener_loop(self) -> None:
        retry_delay = 1.0
        while True:
            pubsub = None
            try:
                client = await get_shared_redis_client(
                    settings.REDIS_URL,
                    decode_responses=True,
                    factory=aioredis.from_url,
                    socket_timeout=None,
                )
                pubsub = client.pubsub()
                await pubsub.subscribe(PLATFORM_SUPPORT_REDIS_CHANNEL)
                retry_delay = 1.0
                async for item in pubsub.listen():
                    if item["type"] != "message":
                        continue
                    message = json.loads(item["data"])
                    if not isinstance(message, dict):
                        raise TypeError("platform support signal must be an object")
                    await self.broadcast_local(message)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "platform_support_realtime_listener_failed",
                    error_type=safe_error_type(error),
                    retry_seconds=retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)
            finally:
                if pubsub is not None:
                    await pubsub.aclose()


platform_ws_manager = PlatformWebSocketManager()
