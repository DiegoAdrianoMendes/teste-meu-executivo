"""Adapter Maritaca (Sabiá) via API OpenAI-compatível de chat completions.

A Maritaca expõe ``https://chat.maritaca.ai/api`` com o contrato de
``/chat/completions`` da OpenAI. Modelos alvo: ``sabiazinho-3`` (barato,
classe SIMPLE/PT) e ``sabia-3``. O adapter é stateless, nunca faz retry
próprio e falha fechado em resposta incompleta ou sem escolha.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.ai.errors import ProviderCallError
from app.ai.models import (
    ModelRequest,
    PromptCachePolicy,
    ProviderName,
    ProviderResponse,
    RouteCandidate,
)
from app.ai.providers.http import HTTPModelProvider


class MaritacaChatAdapter(HTTPModelProvider):
    name = ProviderName.MARITACA
    supports_idempotent_retry = False
    # Texto apenas: sem capability de mídia declarada, o roteador por
    # capability bloqueia requisições multimodais antes de qualquer rede.
    supported_media_kinds = frozenset()
    supported_prompt_cache_policies = frozenset({PromptCachePolicy.OFF})

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(name=self.name, api_key=api_key, base_url=base_url, client=client)

    async def generate(
        self,
        request: ModelRequest,
        route: RouteCandidate,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        self.require_configured()
        self.require_supported(request)
        messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ]
        if not any(message["role"] != "system" for message in messages):
            raise ProviderCallError("Maritaca chat API exige uma mensagem não system.")
        payload: dict[str, Any] = {
            "model": route.model,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "stream": False,
        }
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "klm_structured_response",
                    "strict": True,
                    "schema": request.response_schema,
                },
            }
        body, provider_request_id = await self._post_json(
            "/chat/completions",
            payload=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout_seconds=timeout_seconds,
        )
        choices = body.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ProviderCallError(
                "Maritaca chat API retornou resposta sem conteúdo utilizável.",
                request_started=True,
                billing_unknown=True,
                provider_request_id=provider_request_id,
            )
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        prompt_details = (
            usage.get("prompt_tokens_details")
            if isinstance(usage.get("prompt_tokens_details"), dict)
            else {}
        )
        return ProviderResponse(
            text=content,
            model=str(body.get("model") or route.model),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cached_input_tokens=int(prompt_details.get("cached_tokens") or 0),
            provider_request_id=str(body.get("id") or provider_request_id),
            finish_reason=str((first or {}).get("finish_reason") or ""),
        )
