"""Loopback-only client for the pinned local ChatterMate Community runtime."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings


class ChatterMateClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ChatterMateClient:
    _shared_access_token: str | None = None
    _shared_identity: dict[str, Any] | None = None
    _shared_cache_key: str | None = None
    _shared_expires_at = 0.0
    _shared_login_tasks: dict[
        str,
        asyncio.Task[tuple[str, dict[str, Any]]],
    ] = {}
    _session_ttl_seconds = 15 * 60

    def __init__(self) -> None:
        parsed = urlparse(settings.KLM_CHATTERMATE_URL)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ChatterMateClientError("A ponte ChatterMate aceita somente a origem HTTP local.")
        self.base_url = settings.KLM_CHATTERMATE_URL.rstrip("/")
        self.username = settings.KLM_CHATTERMATE_ADMIN_EMAIL.strip()
        self.password = settings.KLM_CHATTERMATE_ADMIN_PASSWORD.strip()
        if not self.username or not self.password:
            raise ChatterMateClientError("Bootstrap local do ChatterMate ainda não foi carregado.")
        self._access_token: str | None = None
        self._identity: dict[str, Any] | None = None

    def _cache_key(self) -> str:
        digest = hashlib.sha256(self.password.encode("utf-8")).hexdigest()
        return f"{self.base_url}\x00{self.username}\x00{digest}"

    @classmethod
    def _clear_shared_session(cls, cache_key: str) -> None:
        if cls._shared_cache_key != cache_key:
            return
        cls._shared_access_token = None
        cls._shared_identity = None
        cls._shared_cache_key = None
        cls._shared_expires_at = 0.0

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        token: str | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                follow_redirects=False,
                timeout=httpx.Timeout(20.0, connect=3.0),
            ) as client:
                response = await client.request(
                    method,
                    path,
                    params=params,
                    json=json,
                    data=data,
                    headers=headers,
                )
        except httpx.HTTPError as error:
            raise ChatterMateClientError(f"ChatterMate local indisponível: {error}") from error
        if response.status_code not in expected:
            raise ChatterMateClientError(
                f"ChatterMate rejeitou {method} {path} (HTTP {response.status_code}): "
                f"{response.text[:500]}",
                status_code=response.status_code,
            )
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise ChatterMateClientError("ChatterMate retornou JSON inválido.") from error

    async def _perform_login(self) -> tuple[str, dict[str, Any]]:
        payload = await self._raw_request(
            "POST",
            "/api/v1/users/login",
            data={"username": self.username, "password": self.password},
        )
        if not isinstance(payload, dict):
            raise ChatterMateClientError("ChatterMate retornou um login inesperado.")
        token = str(payload.get("access_token") or "")
        user = payload.get("user")
        if not token or not isinstance(user, dict) or not user.get("organization_id"):
            raise ChatterMateClientError("ChatterMate não retornou identidade de organização.")
        return token, user

    async def identity(self) -> dict[str, Any]:
        if self._access_token and self._identity:
            return self._identity

        cache_key = self._cache_key()
        client_type = type(self)
        if (
            client_type._shared_cache_key == cache_key
            and client_type._shared_access_token
            and client_type._shared_identity
            and client_type._shared_expires_at > time.monotonic()
        ):
            self._access_token = client_type._shared_access_token
            self._identity = dict(client_type._shared_identity)
            return self._identity

        task = client_type._shared_login_tasks.get(cache_key)
        if task is None or task.get_loop() is not asyncio.get_running_loop():
            task = asyncio.create_task(self._perform_login())
            client_type._shared_login_tasks[cache_key] = task
        try:
            token, identity = await asyncio.shield(task)
        except BaseException:
            if task.done() and client_type._shared_login_tasks.get(cache_key) is task:
                client_type._shared_login_tasks.pop(cache_key, None)
            raise
        if client_type._shared_login_tasks.get(cache_key) is task:
            client_type._shared_login_tasks.pop(cache_key, None)

        client_type._shared_access_token = token
        client_type._shared_identity = dict(identity)
        client_type._shared_cache_key = cache_key
        client_type._shared_expires_at = time.monotonic() + client_type._session_ttl_seconds
        self._access_token = token
        self._identity = dict(identity)
        return self._identity

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        await self.identity()
        try:
            return await self._raw_request(
                method,
                path,
                params=params,
                json=json,
                token=self._access_token,
                expected=expected,
            )
        except ChatterMateClientError as error:
            if error.status_code not in {401, 403}:
                raise
            self._access_token = None
            self._identity = None
            self._clear_shared_session(self._cache_key())
            await self.identity()
            return await self._raw_request(
                method,
                path,
                params=params,
                json=json,
                token=self._access_token,
                expected=expected,
            )

    @staticmethod
    def _rows(payload: Any, *, key: str | None = None) -> list[dict[str, Any]]:
        value = payload.get(key) if key and isinstance(payload, dict) else payload
        if not isinstance(value, list):
            raise ChatterMateClientError("ChatterMate retornou uma coleção inesperada.")
        return [row for row in value if isinstance(row, dict)]

    async def list_agents(self) -> list[dict[str, Any]]:
        return self._rows(await self._request("GET", "/api/v1/agent/list"))

    async def create_agent(self, data: dict[str, Any]) -> dict[str, Any]:
        payload = await self._request("POST", "/api/v1/agent", json=data, expected=(201,))
        if not isinstance(payload, dict) or not payload.get("id"):
            raise ChatterMateClientError("ChatterMate não retornou o agente criado.")
        return payload

    async def update_agent(self, agent_id: str, data: dict[str, Any]) -> dict[str, Any]:
        payload = await self._request("PUT", f"/api/v1/agent/{agent_id}", json=data)
        if not isinstance(payload, dict) or not payload.get("id"):
            raise ChatterMateClientError("ChatterMate não retornou o agente atualizado.")
        return payload

    async def workflow_for_agent(self, agent_id: str) -> dict[str, Any] | None:
        payload = await self._request("GET", f"/api/v1/workflow/agent/{agent_id}")
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise ChatterMateClientError("ChatterMate retornou um workflow inesperado.")
        return payload

    async def create_workflow(self, data: dict[str, Any]) -> dict[str, Any]:
        payload = await self._request(
            "POST",
            "/api/v1/workflow",
            json=data,
            expected=(201,),
        )
        if not isinstance(payload, dict) or not payload.get("id"):
            raise ChatterMateClientError("ChatterMate não retornou o workflow criado.")
        return payload

    async def update_workflow(
        self,
        workflow_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        payload = await self._request(
            "PUT",
            f"/api/v1/workflow/{workflow_id}",
            json=data,
        )
        if not isinstance(payload, dict) or not payload.get("id"):
            raise ChatterMateClientError("ChatterMate não retornou o workflow atualizado.")
        return payload

    async def workflow_nodes(self, workflow_id: str) -> dict[str, Any]:
        payload = await self._request(
            "GET",
            f"/api/v1/workflow/{workflow_id}/nodes",
        )
        if not isinstance(payload, dict):
            raise ChatterMateClientError("ChatterMate não retornou o grafo do workflow.")
        if not isinstance(payload.get("nodes"), list) or not isinstance(
            payload.get("connections"), list
        ):
            raise ChatterMateClientError("ChatterMate retornou um grafo inválido.")
        return payload

    async def replace_workflow_nodes(
        self,
        workflow_id: str,
        *,
        nodes: list[dict[str, Any]],
        connections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = await self._request(
            "PUT",
            f"/api/v1/workflow/{workflow_id}/nodes",
            json={"nodes": nodes, "connections": connections},
        )
        if not isinstance(payload, dict):
            raise ChatterMateClientError("ChatterMate não confirmou o grafo substituído.")
        if not isinstance(payload.get("nodes"), list) or not isinstance(
            payload.get("connections"), list
        ):
            raise ChatterMateClientError("ChatterMate retornou um grafo inválido.")
        return payload

    async def analytics(self, time_range: str) -> dict[str, Any]:
        payload = await self._request("GET", "/api/v1/analytics", params={"time_range": time_range})
        return payload if isinstance(payload, dict) else {}

    async def agent_performance(self, time_range: str) -> dict[str, Any]:
        payload = await self._request(
            "GET", "/api/v1/analytics/agent-performance", params={"time_range": time_range}
        )
        return payload if isinstance(payload, dict) else {}

    async def people(self) -> dict[str, Any]:
        payload = await self._request("GET", "/api/v1/people", params={"page": 1, "page_size": 20})
        return payload if isinstance(payload, dict) else {}

    async def people_stats(self) -> dict[str, Any]:
        payload = await self._request("GET", "/api/v1/people/stats")
        return payload if isinstance(payload, dict) else {}

    async def mcp_tools(self) -> list[dict[str, Any]]:
        return self._rows(
            await self._request("GET", "/api/v1/mcp-tools", params={"enabled_only": "false"})
        )

    async def groups(self) -> list[dict[str, Any]]:
        return self._rows(await self._request("GET", "/api/v1/groups"))

    async def notifications(self) -> list[dict[str, Any]]:
        return self._rows(await self._request("GET", "/api/v1/notifications"))

    async def knowledge(self, organization_id: str) -> dict[str, Any]:
        payload = await self._request(
            "GET",
            f"/api/v1/knowledge/organization/{organization_id}",
            params={"page": 1, "page_size": 50},
        )
        return payload if isinstance(payload, dict) else {}

    async def knowledge_queue(self, organization_id: str) -> dict[str, Any]:
        payload = await self._request(
            "GET", f"/api/v1/knowledge/queue/organization/{organization_id}"
        )
        return payload if isinstance(payload, dict) else {}
