"""Server-side client for the pinned local Rocket.Chat Community runtime."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings


class RocketChatClientError(RuntimeError):
    pass


class RocketChatClient:
    _shared_headers: dict[str, str] | None = None
    _shared_cache_key: str | None = None
    _shared_expires_at = 0.0
    _shared_login_task: asyncio.Task[dict[str, str]] | None = None
    _session_ttl_seconds = 15 * 60

    def __init__(self) -> None:
        parsed = urlparse(settings.KLM_ROCKETCHAT_URL)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise RocketChatClientError("A ponte Rocket.Chat aceita somente a origem HTTP local.")
        self.base_url = settings.KLM_ROCKETCHAT_URL.rstrip("/")
        self.username = settings.KLM_ROCKETCHAT_ADMIN_USERNAME.strip()
        self.password = settings.KLM_ROCKETCHAT_ADMIN_PASSWORD.strip()
        if not self.username or not self.password:
            raise RocketChatClientError("Bootstrap local do Rocket.Chat ainda não foi carregado.")
        self._headers: dict[str, str] | None = None

    def _cache_key(self) -> str:
        digest = hashlib.sha256(self.password.encode("utf-8")).hexdigest()
        return f"{self.base_url}\x00{self.username}\x00{digest}"

    @classmethod
    def _clear_shared_session(cls, cache_key: str) -> None:
        if cls._shared_cache_key != cache_key:
            return
        cls._shared_headers = None
        cls._shared_cache_key = None
        cls._shared_expires_at = 0.0

    async def _perform_login(self) -> dict[str, str]:
        payload = await self._request(
            "POST",
            "/api/v1/login",
            json={"user": self.username, "password": self.password},
            auth=False,
        )
        data = payload.get("data") or {}
        token = str(data.get("authToken") or "")
        user_id = str(data.get("userId") or "")
        if not token or not user_id:
            raise RocketChatClientError("Rocket.Chat não retornou credenciais de sessão.")
        return {"X-Auth-Token": token, "X-User-Id": user_id}

    async def _login(self) -> dict[str, str]:
        if self._headers:
            return self._headers

        cache_key = self._cache_key()
        client_type = type(self)
        if (
            client_type._shared_cache_key == cache_key
            and client_type._shared_headers
            and client_type._shared_expires_at > time.monotonic()
        ):
            self._headers = dict(client_type._shared_headers)
            return self._headers

        task = client_type._shared_login_task
        if task is None or task.done() or task.get_loop() is not asyncio.get_running_loop():
            task = asyncio.create_task(self._perform_login())
            client_type._shared_login_task = task
        try:
            headers = await task
        finally:
            if client_type._shared_login_task is task:
                client_type._shared_login_task = None

        client_type._shared_cache_key = cache_key
        client_type._shared_headers = dict(headers)
        client_type._shared_expires_at = time.monotonic() + client_type._session_ttl_seconds
        self._headers = dict(headers)
        return self._headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        auth: bool = True,
        expected: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        async def send(headers: dict[str, str]) -> httpx.Response:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                follow_redirects=False,
                timeout=httpx.Timeout(15.0, connect=3.0),
            ) as client:
                return await client.request(
                    method, path, params=params, json=json, headers=headers
                )

        headers = await self._login() if auth else {}
        try:
            response = await send(headers)
            if auth and response.status_code in {401, 403}:
                self._headers = None
                self._clear_shared_session(self._cache_key())
                response = await send(await self._login())
        except httpx.HTTPError as error:
            raise RocketChatClientError(f"Rocket.Chat local indisponível: {error}") from error
        if response.status_code not in expected:
            raise RocketChatClientError(
                f"Rocket.Chat rejeitou {method} {path} (HTTP {response.status_code}): "
                f"{response.text[:500]}"
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise RocketChatClientError("Rocket.Chat retornou JSON inválido.") from error
        if not isinstance(payload, dict):
            raise RocketChatClientError("Rocket.Chat retornou um contrato inesperado.")
        return payload

    async def info(self) -> dict[str, Any]:
        info = await self._request("GET", "/api/info", auth=False)
        if not any(info.get(key) for key in ("hashedWorkspaceUrl", "cloudWorkspaceId", "uniqueID")):
            identity = await self._request("GET", "/api/v1/settings/uniqueID")
            info["uniqueID"] = identity.get("value")
        return info

    async def identity(self) -> dict[str, Any]:
        """Prove that the configured local bridge session is authenticated."""

        payload = await self._request("GET", "/api/v1/me")
        if not payload.get("_id") or not payload.get("username"):
            raise RocketChatClientError("Rocket.Chat não retornou a identidade autenticada.")
        return payload

    async def channel(self, name: str) -> dict[str, Any]:
        payload = await self._request("GET", "/api/v1/channels.info", params={"roomName": name})
        room = payload.get("channel")
        if not isinstance(room, dict) or not room.get("_id"):
            raise RocketChatClientError(f"Canal Rocket.Chat ausente: {name}")
        return room

    async def ensure_private_group(self, name: str) -> dict[str, Any]:
        payload = await self._request(
            "GET",
            "/api/v1/groups.info",
            params={"roomName": name},
            expected=(200, 400),
        )
        room = payload.get("group") if payload.get("success") else None
        if isinstance(room, dict) and room.get("_id"):
            return room
        created = await self._request(
            "POST", "/api/v1/groups.create", json={"name": name, "readOnly": False}
        )
        room = created.get("group")
        if not isinstance(room, dict) or not room.get("_id"):
            raise RocketChatClientError(f"Rocket.Chat não criou o grupo {name}.")
        return room

    async def group_info(self, room_id: str) -> dict[str, Any]:
        payload = await self._request(
            "GET", "/api/v1/groups.info", params={"roomId": room_id}
        )
        room = payload.get("group")
        if not isinstance(room, dict) or not room.get("_id"):
            raise RocketChatClientError("Rocket.Chat não retornou a sala privada.")
        return room

    async def channel_info(self, room_id: str) -> dict[str, Any]:
        payload = await self._request(
            "GET", "/api/v1/channels.info", params={"roomId": room_id}
        )
        room = payload.get("channel")
        if not isinstance(room, dict) or not room.get("_id"):
            raise RocketChatClientError("Rocket.Chat não retornou o canal público.")
        return room

    async def room_info(self, room_id: str, room_type: str) -> dict[str, Any]:
        return (
            await self.channel_info(room_id)
            if room_type == "c"
            else await self.group_info(room_id)
        )

    async def group_members(self, room_id: str, maximum: int = 100) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            "/api/v1/groups.members",
            params={"roomId": room_id, "count": max(1, min(maximum, 100))},
        )
        rows = payload.get("members")
        if not isinstance(rows, list):
            raise RocketChatClientError("Rocket.Chat retornou membros inválidos.")
        return [row for row in rows if isinstance(row, dict)]

    async def channel_members(
        self, room_id: str, maximum: int = 100
    ) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            "/api/v1/channels.members",
            params={"roomId": room_id, "count": max(1, min(maximum, 100))},
        )
        rows = payload.get("members")
        if not isinstance(rows, list):
            raise RocketChatClientError("Rocket.Chat retornou membros de canal inválidos.")
        return [row for row in rows if isinstance(row, dict)]

    async def room_members(
        self, room_id: str, room_type: str, maximum: int = 100
    ) -> list[dict[str, Any]]:
        return (
            await self.channel_members(room_id, maximum)
            if room_type == "c"
            else await self.group_members(room_id, maximum)
        )

    async def group_history(self, room_id: str, maximum: int = 50) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            "/api/v1/groups.history",
            params={"roomId": room_id, "count": max(1, min(maximum, 100))},
        )
        rows = payload.get("messages")
        if not isinstance(rows, list):
            raise RocketChatClientError("Rocket.Chat retornou histórico inválido.")
        return [row for row in rows if isinstance(row, dict)]

    async def channel_history(
        self, room_id: str, maximum: int = 50
    ) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            "/api/v1/channels.history",
            params={"roomId": room_id, "count": max(1, min(maximum, 100))},
        )
        rows = payload.get("messages")
        if not isinstance(rows, list):
            raise RocketChatClientError("Rocket.Chat retornou histórico de canal inválido.")
        return [row for row in rows if isinstance(row, dict)]

    async def room_history(
        self, room_id: str, room_type: str, maximum: int = 50
    ) -> list[dict[str, Any]]:
        return (
            await self.channel_history(room_id, maximum)
            if room_type == "c"
            else await self.group_history(room_id, maximum)
        )

    async def discussions(self, room_id: str, maximum: int = 50) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            "/api/v1/rooms.getDiscussions",
            params={"roomId": room_id, "count": max(1, min(maximum, 100))},
        )
        rows = payload.get("discussions")
        if not isinstance(rows, list):
            raise RocketChatClientError("Rocket.Chat retornou discussões inválidas.")
        return [row for row in rows if isinstance(row, dict)]

    async def send_message(
        self, *, room_id: str, message_id: str, body: str
    ) -> tuple[dict[str, Any], bool]:
        existing = await self._request(
            "GET",
            "/api/v1/chat.getMessage",
            params={"msgId": message_id},
            expected=(200, 400),
        )
        message = existing.get("message") if existing.get("success") else None
        if isinstance(message, dict) and message.get("_id"):
            return message, False
        sent = await self._request(
            "POST",
            "/api/v1/chat.sendMessage",
            json={
                "message": {
                    "_id": message_id,
                    "rid": room_id,
                    "msg": body,
                }
            },
        )
        message = sent.get("message")
        if not isinstance(message, dict) or not message.get("_id"):
            raise RocketChatClientError("Rocket.Chat não retornou a mensagem criada.")
        return message, True
