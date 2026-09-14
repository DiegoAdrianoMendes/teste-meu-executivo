"""Anthropic Messages API adapter using stateless requests."""
from __future__ import annotations

import base64
from typing import Any

import httpx

from app.ai.errors import ProviderCallError
from app.ai.models import (
    MediaKind,
    ModelMediaPart,
    ModelRequest,
    PromptCachePolicy,
    ProviderName,
    ProviderResponse,
    RouteCandidate,
)
from app.ai.providers.http import HTTPModelProvider


def _media_block(part: ModelMediaPart) -> dict[str, Any]:
    """Bloco base64 da Messages API: ``image`` para imagem, ``document`` para PDF."""
    block_type = "document" if part.kind is MediaKind.DOCUMENT else "image"
    return {
        "type": block_type,
        "source": {
            "type": "base64",
            "media_type": part.mime_type,
            "data": base64.b64encode(part.data).decode("ascii"),
        },
    }


class AnthropicMessagesAdapter(HTTPModelProvider):
    name = ProviderName.ANTHROPIC
    supports_idempotent_retry = False
    supported_media_kinds = frozenset({MediaKind.IMAGE, MediaKind.DOCUMENT})
    supported_prompt_cache_policies = frozenset(PromptCachePolicy)

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
        system = "\n\n".join(
            message.content for message in request.messages if message.role == "system"
        )
        messages: list[dict[str, Any]] = [
            {"role": message.role, "content": message.content}
            for message in request.messages
            if message.role != "system"
        ]
        if not messages:
            raise ProviderCallError("Anthropic Messages API exige uma mensagem não system.")
        if request.media_parts:
            # Blocos de mídia entram ANTES do texto da última mensagem de usuário,
            # conforme a recomendação da Messages API para imagem/PDF base64.
            media_blocks = [_media_block(part) for part in request.media_parts]
            last_user_index = next(
                (
                    index
                    for index in range(len(messages) - 1, -1, -1)
                    if messages[index]["role"] == "user"
                ),
                None,
            )
            if last_user_index is None:
                messages.append({"role": "user", "content": media_blocks})
            else:
                text = messages[last_user_index]["content"]
                content: list[dict[str, Any]] = list(media_blocks)
                if isinstance(text, str) and text:
                    content.append({"type": "text", "text": text})
                messages[last_user_index]["content"] = content
        payload: dict[str, Any] = {
            "model": route.model,
            "max_tokens": request.max_output_tokens,
            "messages": messages,
            "temperature": request.temperature,
        }
        if system:
            if request.prompt_cache_policy is PromptCachePolicy.OFF:
                payload["system"] = system
            else:
                cache_control = {"type": "ephemeral"}
                if request.prompt_cache_policy is PromptCachePolicy.ONE_HOUR:
                    cache_control["ttl"] = "1h"
                payload["system"] = [
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": cache_control,
                    }
                ]
        if request.response_schema is not None:
            payload["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": request.response_schema,
                }
            }
        body, provider_request_id = await self._post_json(
            "/messages",
            payload=payload,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            timeout_seconds=timeout_seconds,
        )
        content = body.get("content") or []
        text = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        direct_input = int(usage.get("input_tokens") or 0)
        cache_creation = int(usage.get("cache_creation_input_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        return ProviderResponse(
            text=text,
            model=str(body.get("model") or route.model),
            input_tokens=direct_input + cache_creation + cache_read,
            output_tokens=int(usage.get("output_tokens") or 0),
            cached_input_tokens=cache_read,
            provider_request_id=str(body.get("id") or provider_request_id),
            finish_reason=str(body.get("stop_reason") or ""),
        )
