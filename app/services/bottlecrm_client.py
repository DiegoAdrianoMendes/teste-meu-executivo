"""Server-side REST client for the loopback-only BottleCRM projection."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings


class BottleCRMClientError(RuntimeError):
    """Raised when the pinned local BottleCRM API rejects a bridge operation."""


class BottleCRMNotFoundError(BottleCRMClientError):
    """Raised when a previously linked BottleCRM object no longer exists."""


_DETAIL_CONTRACTS = {
    "account": ("accounts", "account_obj"),
    "contact": ("contacts", "contact_obj"),
    "lead": ("leads", "lead_obj"),
    "opportunity": ("opportunities", "opportunity_obj"),
    "case": ("cases", "cases_obj"),
    "task": ("tasks", "task_obj"),
}
_RELATION_FIELDS = {"account", "case", "contacts"}
_DECIMAL_FIELDS = {"amount", "opportunity_amount"}
_DATE_FIELDS = {"due_date"}


def _require_loopback(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise BottleCRMClientError("A ponte BottleCRM aceita somente a origem HTTP local.")
    return value.rstrip("/")


def _relation_value(value: Any) -> str | list[str] | None:
    is_collection = isinstance(value, list)
    values = value if is_collection else [value]
    identifiers: list[str] = []
    for item in values:
        identifier = item.get("id") if isinstance(item, dict) else item
        if identifier is None:
            return None
        identifiers.append(str(identifier))
    return sorted(identifiers) if is_collection else identifiers[0]


def _field_matches(field: str, actual: Any, expected: Any) -> bool:
    if field in _RELATION_FIELDS:
        return _relation_value(actual) == _relation_value(expected)
    if field in _DECIMAL_FIELDS:
        try:
            return Decimal(str(actual)) == Decimal(str(expected))
        except (InvalidOperation, TypeError, ValueError):
            return False
    if field in _DATE_FIELDS:
        return str(actual)[:10] == str(expected)[:10]
    return actual == expected


class BottleCRMClient:
    def __init__(self) -> None:
        self.base_url = _require_loopback(settings.KLM_BOTTLECRM_URL)
        self.bridge_secret = settings.KLM_BOTTLECRM_BRIDGE_SECRET.strip()
        if len(self.bridge_secret) < 32:
            raise BottleCRMClientError("Segredo da ponte local do BottleCRM não configurado.")
        self._access_token = ""
        self._organization_id = ""

    async def authenticate(self, *, force: bool = False) -> dict[str, str]:
        if self._access_token and self._organization_id and not force:
            return {
                "accessToken": self._access_token,
                "organizationId": self._organization_id,
            }
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(15.0, connect=3.0),
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    "/api/auth/klm-local-session/",
                    headers={"X-KLM-Local-Secret": self.bridge_secret},
                )
        except httpx.HTTPError as error:
            raise BottleCRMClientError(f"BottleCRM local indisponível: {error}") from error
        if response.status_code != 200:
            raise BottleCRMClientError(
                f"BottleCRM rejeitou a sessão local (HTTP {response.status_code})."
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise BottleCRMClientError("BottleCRM retornou uma sessão inválida.") from error
        if not isinstance(payload, dict):
            raise BottleCRMClientError("BottleCRM retornou um contrato de sessão inesperado.")
        access_token = str(payload.get("access_token") or "")
        organization_id = str(payload.get("org_id") or "")
        if not access_token or not organization_id:
            raise BottleCRMClientError("BottleCRM não retornou a identidade da organização.")
        self._access_token = access_token
        self._organization_id = organization_id
        return {"accessToken": access_token, "organizationId": organization_id}

    async def organization_id(self) -> str:
        return (await self.authenticate())["organizationId"]

    async def entity_matches(
        self,
        resource: str,
        external_id: str,
        expected: dict[str, Any],
    ) -> bool:
        contract = _DETAIL_CONTRACTS.get(resource)
        if contract is None:
            raise BottleCRMClientError(f"Recurso BottleCRM desconhecido: {resource}.")
        collection, object_key = contract
        try:
            payload = await self._request("GET", f"/api/{collection}/{external_id}/")
        except BottleCRMNotFoundError:
            return False
        entity = payload.get(object_key)
        if not isinstance(entity, dict):
            raise BottleCRMClientError(
                f"BottleCRM não retornou o contrato de detalhe para {resource}."
            )
        return all(
            field in entity and _field_matches(field, entity[field], value)
            for field, value in expected.items()
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        payload = await self._request_payload(
            method,
            path,
            params=params,
            json=json,
            expected=expected,
        )
        if not isinstance(payload, dict):
            raise BottleCRMClientError("BottleCRM retornou um contrato inesperado.")
        return payload

    async def read_payload(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Read a pinned BottleCRM endpoint that may return an object or a list."""
        payload = await self._request_payload("GET", path, params=params)
        if not isinstance(payload, (dict, list)):
            raise BottleCRMClientError("BottleCRM retornou um contrato de leitura inesperado.")
        return payload

    async def _request_payload(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        for attempt in range(2):
            identity = await self.authenticate(force=attempt == 1)
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url,
                    headers={"Authorization": f"Bearer {identity['accessToken']}"},
                    timeout=httpx.Timeout(20.0, connect=3.0),
                    follow_redirects=False,
                ) as client:
                    response = await client.request(method, path, params=params, json=json)
            except httpx.HTTPError as error:
                raise BottleCRMClientError(f"BottleCRM local indisponível: {error}") from error
            if response.status_code in {401, 403} and attempt == 0:
                continue
            if response.status_code == 404:
                raise BottleCRMNotFoundError(f"Objeto BottleCRM ausente em {path}.")
            if response.status_code not in expected:
                detail = response.text[:600]
                raise BottleCRMClientError(
                    f"BottleCRM rejeitou {method} {path} (HTTP {response.status_code}): {detail}"
                )
            try:
                payload = response.json()
            except ValueError as error:
                raise BottleCRMClientError("BottleCRM retornou JSON inválido.") from error
            if isinstance(payload, dict) and payload.get("error") is True:
                raise BottleCRMClientError(
                    f"BottleCRM rejeitou a operação: {payload.get('errors')}"
                )
            return payload
        raise BottleCRMClientError("BottleCRM rejeitou a autenticação da ponte local.")

    @staticmethod
    def _required_id(payload: dict[str, Any], resource: str) -> str:
        external_id = str(payload.get("id") or "")
        if not external_id:
            raise BottleCRMClientError(f"BottleCRM não retornou o ID de {resource}.")
        return external_id

    async def find_account(self, name: str) -> dict[str, Any] | None:
        payload = await self._request("GET", "/api/accounts/", params={"name": name})
        container = payload.get("active_accounts")
        if not isinstance(container, dict):
            raise BottleCRMClientError("BottleCRM retornou active_accounts em formato inesperado.")
        rows = container.get("open_accounts")
        if not isinstance(rows, list):
            raise BottleCRMClientError(
                "BottleCRM não retornou a lista active_accounts.open_accounts."
            )
        return next(
            (row for row in rows if isinstance(row, dict) and row.get("name") == name),
            None,
        )

    async def create_account(self, payload: dict[str, Any]) -> str:
        return self._required_id(
            await self._request("POST", "/api/accounts/", json=payload),
            "conta",
        )

    async def update_account(self, external_id: str, payload: dict[str, Any]) -> None:
        await self._request("PUT", f"/api/accounts/{external_id}/", json=payload)

    async def find_contact(self, marker: str, query: str) -> dict[str, Any] | None:
        payload = await self._request("GET", "/api/contacts/", params={"search": query})
        rows = payload.get("results")
        if not isinstance(rows, list):
            return None
        return next(
            (
                row
                for row in rows
                if isinstance(row, dict) and marker in str(row.get("description") or "")
            ),
            None,
        )

    async def create_contact(self, payload: dict[str, Any]) -> str:
        return self._required_id(
            await self._request("POST", "/api/contacts/", json=payload),
            "contato",
        )

    async def update_contact(self, external_id: str, payload: dict[str, Any]) -> None:
        await self._request("PUT", f"/api/contacts/{external_id}/", json=payload)

    async def find_lead(self, marker: str) -> dict[str, Any] | None:
        payload = await self._request("GET", "/api/leads/", params={"search": marker})
        rows: list[Any] = []
        for key, child_key in (("open_leads", "open_leads"), ("close_leads", "close_leads")):
            container = payload.get(key)
            candidate = container.get(child_key) if isinstance(container, dict) else None
            if isinstance(candidate, list):
                rows.extend(candidate)
        return next(
            (
                row
                for row in rows
                if isinstance(row, dict) and marker in str(row.get("company_name") or "")
            ),
            None,
        )

    async def create_lead(self, payload: dict[str, Any]) -> str:
        return self._required_id(
            await self._request("POST", "/api/leads/", json=payload),
            "lead",
        )

    async def update_lead(self, external_id: str, payload: dict[str, Any]) -> None:
        await self._request("PUT", f"/api/leads/{external_id}/", json=payload)

    async def find_opportunity(self, name: str) -> dict[str, Any] | None:
        payload = await self._request("GET", "/api/opportunities/", params={"name": name})
        rows = payload.get("opportunities")
        if not isinstance(rows, list):
            return None
        return next(
            (row for row in rows if isinstance(row, dict) and row.get("name") == name),
            None,
        )

    async def create_opportunity(self, payload: dict[str, Any]) -> str:
        return self._required_id(
            await self._request("POST", "/api/opportunities/", json=payload),
            "oportunidade",
        )

    async def update_opportunity(self, external_id: str, payload: dict[str, Any]) -> None:
        await self._request("PUT", f"/api/opportunities/{external_id}/", json=payload)

    async def find_case(self, name: str) -> dict[str, Any] | None:
        payload = await self._request("GET", "/api/cases/", params={"name": name})
        rows = payload.get("cases")
        if not isinstance(rows, list):
            return None
        return next(
            (row for row in rows if isinstance(row, dict) and row.get("name") == name),
            None,
        )

    async def create_case(self, payload: dict[str, Any]) -> str:
        return self._required_id(
            await self._request("POST", "/api/cases/", json=payload),
            "caso",
        )

    async def update_case(self, external_id: str, payload: dict[str, Any]) -> None:
        await self._request("PUT", f"/api/cases/{external_id}/", json=payload)

    async def find_task(self, title: str) -> dict[str, Any] | None:
        payload = await self._request("GET", "/api/tasks/", params={"title": title})
        rows = payload.get("tasks")
        if not isinstance(rows, list):
            return None
        return next(
            (row for row in rows if isinstance(row, dict) and row.get("title") == title),
            None,
        )

    async def create_task(self, payload: dict[str, Any]) -> str:
        return self._required_id(
            await self._request("POST", "/api/tasks/", json=payload),
            "tarefa",
        )

    async def update_task(self, external_id: str, payload: dict[str, Any]) -> None:
        await self._request("PUT", f"/api/tasks/{external_id}/", json=payload)
