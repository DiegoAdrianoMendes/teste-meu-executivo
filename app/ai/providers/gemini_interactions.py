"""Gemini Interactions API adapter with ``store=false`` stateless execution."""
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
from app.core.logging import logger


def _media_content(part: ModelMediaPart) -> dict[str, Any]:
    """Bloco ``inline_data`` (convenção Gemini) com payload base64 embutido."""
    return {
        "type": "inline_data",
        "inline_data": {
            "mime_type": part.mime_type,
            "data": base64.b64encode(part.data).decode("ascii"),
        },
    }


class GeminiInteractionsAdapter(HTTPModelProvider):
    name = ProviderName.GEMINI_API
    supports_idempotent_retry = False
    supported_media_kinds = frozenset({MediaKind.IMAGE, MediaKind.DOCUMENT})
    supported_prompt_cache_policies = frozenset(
        {PromptCachePolicy.OFF, PromptCachePolicy.AUTO}
    )

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
        system_instruction = "\n\n".join(
            message.content for message in request.messages if message.role == "system"
        )
        inputs: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role == "system":
                continue
            inputs.append(
                {
                    "type": "model_output" if message.role == "assistant" else "user_input",
                    "content": [{"type": "text", "text": message.content}],
                }
            )
        if not inputs:
            raise ProviderCallError("Gemini Interactions API exige uma entrada não system.")
        if request.media_parts:
            # Mídia (imagem/PDF) entra antes do texto na última entrada de usuário,
            # espelhando a convenção multimodal do Gemini (inline_data + texto).
            media_contents = [_media_content(part) for part in request.media_parts]
            last_user_index = next(
                (
                    index
                    for index in range(len(inputs) - 1, -1, -1)
                    if inputs[index]["type"] == "user_input"
                ),
                None,
            )
            if last_user_index is None:
                inputs.append({"type": "user_input", "content": media_contents})
            else:
                inputs[last_user_index]["content"] = [
                    *media_contents,
                    *inputs[last_user_index]["content"],
                ]
        payload: dict[str, Any] = {
            "model": route.model,
            "input": inputs,
            "store": False,
            "background": False,
            "generation_config": {
                "max_output_tokens": request.max_output_tokens,
                "temperature": request.temperature,
            },
        }
        if system_instruction:
            payload["system_instruction"] = system_instruction
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "text",
                "mime_type": "application/json",
                "schema": request.response_schema,
            }
        body, provider_request_id = await self._post_json(
            "/interactions",
            payload=payload,
            headers={
                "x-goog-api-key": self._api_key,
                "Content-Type": "application/json",
            },
            timeout_seconds=timeout_seconds,
        )
        status = str(body.get("status") or "")
        if status and status != "completed":
            # Em produção (08/09/2026) a API respondeu 200 em 61 ms com status
            # diferente de "completed" e a mensagem genérica escondia qual. O status
            # e o objeto `error` do corpo não carregam segredo; vão para o log e
            # para a mensagem.
            error_detail = str(body.get("error") or "")[:400]
            logger.warning(
                "gemini_interaction_not_completed",
                status=status,
                interaction_id=str(body.get("id") or ""),
                error=error_detail,
                provider_request_id=provider_request_id,
            )
            raise ProviderCallError(
                f"Gemini Interactions API não concluiu a geração (status={status}).",
                request_started=True,
                billing_unknown=True,
                provider_request_id=provider_request_id,
            )
        parts: list[str] = []
        for step in body.get("steps") or []:
            if not isinstance(step, dict) or step.get("type") != "model_output":
                continue
            for content in step.get("content") or []:
                if isinstance(content, dict) and content.get("type") == "text":
                    value = content.get("text")
                    if isinstance(value, str):
                        parts.append(value)
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return ProviderResponse(
            text="".join(parts),
            model=str(body.get("model") or route.model),
            input_tokens=int(usage.get("total_input_tokens") or 0),
            output_tokens=int(usage.get("total_output_tokens") or 0),
            cached_input_tokens=int(usage.get("total_cached_tokens") or 0),
            provider_request_id=str(body.get("id") or provider_request_id),
            finish_reason=status,
        )
