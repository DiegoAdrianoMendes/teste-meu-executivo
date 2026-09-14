"""Minimal, server-side Chatwoot REST client for the local KLM projection."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings


class ChatwootClientError(RuntimeError):
    """Raised when the pinned local Chatwoot API rejects a bridge operation."""


class ChatwootRateLimitError(ChatwootClientError):
    """The upstream per-conversation flood guard stopped a historical batch."""


def _require_loopback(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ChatwootClientError("A ponte Chatwoot aceita somente a origem HTTP local.")
    return value.rstrip("/")


class ChatwootClient:
    def __init__(self) -> None:
        self.base_url = _require_loopback(settings.KLM_CHATWOOT_URL)
        self.account_id = settings.KLM_CHATWOOT_ACCOUNT_ID.strip()
        self.inbox_id = settings.KLM_CHATWOOT_INBOX_ID.strip()
        self.api_token = settings.KLM_CHATWOOT_API_ACCESS_TOKEN.strip()
        if not self.account_id or not self.inbox_id or not self.api_token:
            raise ChatwootClientError("Bootstrap local do Chatwoot ainda não foi carregado.")

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> dict[str, Any] | list[Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers={"api_access_token": self.api_token},
                follow_redirects=False,
                timeout=httpx.Timeout(15.0, connect=3.0),
            ) as client:
                response = await client.request(method, path, params=params, json=json)
        except httpx.HTTPError as error:
            raise ChatwootClientError(f"Chatwoot local indisponível: {error}") from error
        if response.status_code not in expected:
            detail = response.text[:500]
            if response.status_code == 422 and "Too many messages" in detail:
                raise ChatwootRateLimitError(
                    "Limite antiloop do Chatwoot alcançado; a reconciliação "
                    "continuará no próximo lote."
                )
            raise ChatwootClientError(
                f"Chatwoot rejeitou {method} {path} (HTTP {response.status_code}): {detail}"
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise ChatwootClientError("Chatwoot retornou JSON inválido.") from error
        if not isinstance(payload, (dict, list)):
            raise ChatwootClientError("Chatwoot retornou um contrato inesperado.")
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        payload = await self._request_json(
            method,
            path,
            params=params,
            json=json,
            expected=expected,
        )
        if not isinstance(payload, dict):
            raise ChatwootClientError("Chatwoot retornou um objeto inesperado.")
        return payload

    async def _collection(self, path: str, *, key: str = "payload") -> list[dict[str, Any]]:
        payload = await self._request_json("GET", path)
        rows = payload if isinstance(payload, list) else payload.get(key)
        if not isinstance(rows, list):
            raise ChatwootClientError(f"Chatwoot retornou uma coleção inválida em {path}.")
        return [row for row in rows if isinstance(row, dict)]

    async def account(self) -> dict[str, Any]:
        return await self._request("GET", f"/api/v1/accounts/{self.account_id}")

    async def list_inboxes(self) -> list[dict[str, Any]]:
        return await self._collection(f"/api/v1/accounts/{self.account_id}/inboxes")

    async def list_teams(self) -> list[dict[str, Any]]:
        return await self._collection(f"/api/v1/accounts/{self.account_id}/teams")

    async def list_labels(self) -> list[dict[str, Any]]:
        return await self._collection(f"/api/v1/accounts/{self.account_id}/labels")

    async def list_agents(self) -> list[dict[str, Any]]:
        return await self._collection(f"/api/v1/accounts/{self.account_id}/agents")

    async def list_canned_responses(self) -> list[dict[str, Any]]:
        return await self._collection(f"/api/v1/accounts/{self.account_id}/canned_responses")

    async def conversation(self, conversation_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}",
        )

    async def assign_conversation(
        self,
        conversation_id: str,
        *,
        assignee_id: int | None = None,
        team_id: int | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/assignments",
            json={"assignee_id": assignee_id, "team_id": team_id},
        )

    async def set_conversation_status(self, conversation_id: str, status: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/toggle_status",
            json={"status": status},
        )

    async def set_conversation_priority(
        self, conversation_id: str, priority: str | None
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/toggle_priority",
            json={"priority": priority},
        )

    async def set_conversation_labels(
        self, conversation_id: str, labels: list[str]
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/labels",
            json={"labels": labels},
        )

    async def find_contact(self, identifier: str) -> dict[str, Any] | None:
        payload = await self._request(
            "GET",
            f"/api/v1/accounts/{self.account_id}/contacts/search",
            params={"q": identifier, "include_contact_inboxes": "true"},
        )
        rows = payload.get("payload")
        if not isinstance(rows, list):
            return None
        return next(
            (row for row in rows if isinstance(row, dict) and row.get("identifier") == identifier),
            None,
        )

    async def create_contact(
        self,
        *,
        identifier: str,
        source_id: str,
        name: str,
        email: str | None,
        phone: str | None,
    ) -> dict[str, Any]:
        existing = await self.find_contact(identifier)
        if existing:
            return existing
        payload = await self._request(
            "POST",
            f"/api/v1/accounts/{self.account_id}/contacts",
            json={
                "name": name,
                "identifier": identifier,
                "email": email,
                "phone_number": phone,
                "inbox_id": int(self.inbox_id),
                "source_id": source_id,
                "custom_attributes": {"klm_managed": True},
            },
        )
        contact = (payload.get("payload") or {}).get("contact")
        if not isinstance(contact, dict) or not contact.get("id"):
            raise ChatwootClientError("Chatwoot não retornou o contato criado.")
        return contact

    async def create_conversation(
        self,
        *,
        contact_id: int,
        source_id: str,
        subject: str,
        thread_id: str,
    ) -> dict[str, Any]:
        existing = await self._request(
            "GET",
            f"/api/v1/accounts/{self.account_id}/contacts/{contact_id}/conversations",
        )
        rows = existing.get("payload")
        if isinstance(rows, list):
            exact = next(
                (
                    row
                    for row in rows
                    if isinstance(row, dict)
                    and str((row.get("custom_attributes") or {}).get("klm_thread_id")) == thread_id
                ),
                None,
            )
            if exact:
                return exact
        payload = await self._request(
            "POST",
            f"/api/v1/accounts/{self.account_id}/conversations",
            json={
                "inbox_id": int(self.inbox_id),
                "contact_id": contact_id,
                "source_id": source_id,
                "status": "open",
                "custom_attributes": {
                    "klm_managed": True,
                    "klm_subject": subject[:220],
                    "klm_thread_id": thread_id,
                },
            },
        )
        if not payload.get("id"):
            raise ChatwootClientError("Chatwoot não retornou a conversa criada.")
        return payload

    async def list_messages(
        self, conversation_id: str, maximum: int = 2000
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        after = 0
        while len(messages) < maximum:
            payload = await self._request(
                "GET",
                f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/messages",
                params={"after": after},
            )
            rows = payload.get("payload")
            if not isinstance(rows, list) or not rows:
                break
            valid_rows = [row for row in rows if isinstance(row, dict) and row.get("id")]
            if not valid_rows:
                break
            messages.extend(valid_rows)
            next_after = max(int(row["id"]) for row in valid_rows)
            if next_after <= after or len(valid_rows) < 100:
                break
            after = next_after
        return messages[:maximum]

    async def create_message(
        self,
        *,
        conversation_id: str,
        content: str,
        source_id: str,
        message_type: str,
        private: bool,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/messages",
            json={
                "content": content,
                "source_id": source_id,
                "message_type": message_type,
                "private": private,
            },
        )
