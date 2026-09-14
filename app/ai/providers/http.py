"""Shared no-retry HTTP transport with sanitized failures."""
from __future__ import annotations

from typing import Any

import httpx

from app.ai.errors import ProviderCallError, ProviderNotConfiguredError
from app.ai.models import ProviderName
from app.ai.providers.base import ModelProviderAdapter
from app.core.logging import logger

_ERROR_DETAIL_MAX_CHARS = 600


def _error_detail(response: httpx.Response) -> str:
    """Resumo do corpo de erro do provedor: só o objeto `error` quando existir."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:_ERROR_DETAIL_MAX_CHARS]
    if isinstance(body, dict) and body.get("error") is not None:
        return str(body["error"])[:_ERROR_DETAIL_MAX_CHARS]
    return str(body)[:_ERROR_DETAIL_MAX_CHARS]

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class HTTPModelProvider(ModelProviderAdapter):
    def __init__(
        self,
        *,
        name: ProviderName,
        api_key: str,
        base_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = client

    def require_configured(self) -> None:
        if not self._api_key.strip():
            raise ProviderNotConfiguredError(
                f"Credencial não configurada para o provider {self.name.value}."
            )

    async def _post_json(
        self,
        path: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], str]:
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_seconds),
                follow_redirects=False,
            )
        try:
            response = await client.post(
                f"{self.base_url}{path}",
                json=payload,
                headers=headers,
                timeout=timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ProviderCallError(
                f"Timeout do provider {self.name.value}.",
                retryable=True,
                request_started=True,
                billing_unknown=True,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderCallError(
                f"Falha de transporte do provider {self.name.value}.",
                retryable=True,
                request_started=True,
                billing_unknown=True,
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        provider_request_id = (
            response.headers.get("x-request-id")
            or response.headers.get("request-id")
            or response.headers.get("x-goog-request-id")
            or ""
        )
        if response.status_code >= 400:
            # Sem isto o log só dizia "respondeu HTTP 400" e a causa (modelo, parâmetro,
            # esquema) ficava invisível em produção (08/09/2026). O corpo de erro dos
            # provedores não carrega segredo; truncado para não inflar o log.
            logger.warning(
                "ai_provider_http_error",
                provider=self.name.value,
                status_code=response.status_code,
                provider_request_id=provider_request_id,
                detail=_error_detail(response),
            )
            raise ProviderCallError(
                f"Provider {self.name.value} respondeu HTTP {response.status_code}.",
                retryable=response.status_code in _RETRYABLE_STATUS,
                request_started=True,
                billing_unknown=response.status_code in _RETRYABLE_STATUS,
                status_code=response.status_code,
                provider_request_id=provider_request_id,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderCallError(
                f"Provider {self.name.value} retornou resposta não JSON.",
                request_started=True,
                billing_unknown=True,
                status_code=response.status_code,
                provider_request_id=provider_request_id,
            ) from exc
        if not isinstance(body, dict):
            raise ProviderCallError(
                f"Provider {self.name.value} retornou envelope inválido.",
                request_started=True,
                billing_unknown=True,
                provider_request_id=provider_request_id,
            )
        return body, provider_request_id
