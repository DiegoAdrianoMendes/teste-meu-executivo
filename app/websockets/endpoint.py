"""Endpoint autenticado do canal em tempo real."""
from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.core.security import verify_token
from app.core.token_store import consume_token
from app.websockets.manager import ws_manager
from app.websockets.platform_manager import (
    PlatformWebSocketIdentity,
    platform_ws_manager,
    revalidate_platform_identity,
)
from app.websockets.security import WebSocketIdentity, revalidate_websocket_identity

router = APIRouter(tags=["WebSocket"])
WEBSOCKET_SUBPROTOCOL = "klm.realtime.v1"
PLATFORM_WEBSOCKET_SUBPROTOCOL = "klm.platform.support.v1"
WEBSOCKET_IDLE_CLOSE_CODE = 4408


async def authenticate_websocket_ticket(
    ticket: str | None,
    db: AsyncSession,
) -> WebSocketIdentity | None:
    payload = verify_token(ticket or "")
    if not payload or payload.get("token_type") != "websocket":
        return None
    try:
        identity = WebSocketIdentity(
            workspace_id=UUID(payload["workspace_id"]),
            user_id=UUID(payload["sub"]),
            membership_id=UUID(payload["membership_id"]),
            session_id=UUID(payload["session_id"]),
            auth_epoch=int(payload["auth_epoch"]),
            token_id=str(payload["jti"]),
        )
    except (KeyError, TypeError, ValueError):
        return None

    if not await revalidate_websocket_identity(identity, db):
        return None
    if not await consume_token(payload.get("jti"), payload.get("exp")):
        return None
    return identity


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    allowed = {item.rstrip("/") for item in settings.CORS_ORIGINS}
    return origin.rstrip("/") in allowed


def _extract_ticket(websocket: WebSocket) -> str | None:
    raw_protocols = websocket.headers.get("sec-websocket-protocol", "")
    protocols = [item.strip() for item in raw_protocols.split(",") if item.strip()]
    if WEBSOCKET_SUBPROTOCOL not in protocols:
        return None
    ticket_protocol = next((item for item in protocols if item.startswith("ticket.")), None)
    return ticket_protocol.removeprefix("ticket.") if ticket_protocol else None


def _extract_platform_ticket(websocket: WebSocket) -> str | None:
    raw_protocols = websocket.headers.get("sec-websocket-protocol", "")
    protocols = [item.strip() for item in raw_protocols.split(",") if item.strip()]
    if PLATFORM_WEBSOCKET_SUBPROTOCOL not in protocols:
        return None
    ticket_protocol = next((item for item in protocols if item.startswith("ticket.")), None)
    return ticket_protocol.removeprefix("ticket.") if ticket_protocol else None


async def authenticate_platform_websocket_ticket(
    ticket: str | None,
) -> PlatformWebSocketIdentity | None:
    payload = verify_token(ticket or "")
    if (
        not payload
        or payload.get("token_type") != "platform_websocket"
        or payload.get("scope") != "support:view"
    ):
        return None
    try:
        identity = PlatformWebSocketIdentity(
            user_id=UUID(payload["sub"]),
            session_id=UUID(payload["session_id"]),
            auth_epoch=int(payload["auth_epoch"]),
            token_id=str(payload["jti"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not await revalidate_platform_identity(identity):
        return None
    if not await consume_token(payload.get("jti"), payload.get("exp")):
        return None
    return identity


async def _receive_text_with_idle_timeout(websocket: WebSocket) -> str:
    return await asyncio.wait_for(
        websocket.receive_text(),
        timeout=settings.WEBSOCKET_IDLE_TIMEOUT_SECONDS,
    )


@router.websocket("/api/v1/ws")
async def websocket_endpoint(
    websocket: WebSocket,
):
    if not _origin_allowed(websocket):
        await websocket.close(code=4403)
        return
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        identity = await authenticate_websocket_ticket(_extract_ticket(websocket), db)
    if identity is None:
        await websocket.close(code=4401)
        return

    await ws_manager.connect(
        websocket,
        workspace_id=identity.workspace_id,
        user_id=identity.user_id,
        membership_id=identity.membership_id,
        session_id=identity.session_id,
        auth_epoch=identity.auth_epoch,
        token_id=identity.token_id,
        subprotocol=WEBSOCKET_SUBPROTOCOL,
    )
    try:
        await websocket.send_text(json.dumps({"event": "CONNECTED", "data": {"status": "ok"}}))
        while True:
            try:
                data = await _receive_text_with_idle_timeout(websocket)
            except TimeoutError:
                logger.info("ws_client_idle_timeout")
                await websocket.close(code=WEBSOCKET_IDLE_CLOSE_CODE)
                return
            if data == "ping":
                if not await ws_manager.validate_connection(websocket):
                    return
                await websocket.send_text(json.dumps({"event": "PONG"}))
    except WebSocketDisconnect:
        logger.debug("ws_client_disconnected_gracefully")
    except Exception as exc:
        logger.error("ws_error", error_type=safe_error_type(exc))
    finally:
        await ws_manager.disconnect(websocket)


@router.websocket("/api/v1/platform/ws")
async def platform_websocket_endpoint(websocket: WebSocket):
    if not _origin_allowed(websocket):
        await websocket.close(code=4403)
        return
    identity = await authenticate_platform_websocket_ticket(
        _extract_platform_ticket(websocket)
    )
    if identity is None:
        await websocket.close(code=4401)
        return

    await platform_ws_manager.connect(
        websocket,
        identity,
        subprotocol=PLATFORM_WEBSOCKET_SUBPROTOCOL,
    )
    try:
        await websocket.send_text(
            json.dumps({"event": "CONNECTED", "data": {"status": "ok"}})
        )
        while True:
            try:
                data = await _receive_text_with_idle_timeout(websocket)
            except TimeoutError:
                await websocket.close(code=WEBSOCKET_IDLE_CLOSE_CODE)
                return
            if data == "ping":
                if not await platform_ws_manager.validate(websocket):
                    return
                await websocket.send_text(json.dumps({"event": "PONG"}))
    except WebSocketDisconnect:
        logger.info("platform_support_ws_disconnected")
    except Exception as error:
        logger.error(
            "platform_support_ws_error",
            error_type=safe_error_type(error),
        )
    finally:
        await platform_ws_manager.disconnect(websocket)
