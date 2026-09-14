"""OpenAI Responses API adapter with server-side storage disabled."""
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
from app.ai.prompt_cache import provider_prompt_cache_key
from app.ai.providers.http import HTTPModelProvider
from app.ai.telemetry import safe_provider_request_id


def _response_text(body: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


class OpenAIResponsesAdapter(HTTPModelProvider):
    name = ProviderName.OPENAI
    supports_idempotent_retry = False
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
        instructions = "\n\n".join(
            message.content for message in request.messages if message.role == "system"
        )
        input_messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
            if message.role != "system"
        ]
        payload: dict[str, Any] = {
            "model": route.model,
            "input": input_messages,
            "max_output_tokens": request.max_output_tokens,
            "store": False,
        }
        if instructions:
            payload["instructions"] = instructions
        if request.prompt_cache_policy is PromptCachePolicy.AUTO:
            payload["prompt_cache_key"] = provider_prompt_cache_key(request, route)
        # A família gpt-5 (modelos com raciocínio) recusa `temperature` com HTTP 400
        # ("Unsupported parameter"); só os demais recebem o valor pedido.
        if request.temperature is not None and not route.model.startswith("gpt-5"):
            payload["temperature"] = request.temperature
        if request.response_schema is not None:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "klm_structured_response",
                    "strict": True,
                    "schema": request.response_schema,
                }
            }

        body, provider_request_id = await self._post_json(
            "/responses",
            payload=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout_seconds=timeout_seconds,
        )
        status = str(body.get("status") or "")
        response_request_id = safe_provider_request_id(body.get("id") or provider_request_id)
        if status != "completed":
            error = ProviderCallError(
                "OpenAI Responses API não concluiu a geração.",
                request_started=True,
                billing_unknown=True,
                provider_request_id=response_request_id,
            )
            error.error_code = (
                "response_incomplete" if status == "incomplete" else "response_not_completed"
            )
            raise error
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        input_details = (
            usage.get("input_tokens_details")
            if isinstance(usage.get("input_tokens_details"), dict)
            else {}
        )
        response_model = body.get("model") if isinstance(body.get("model"), str) else ""
        return ProviderResponse(
            text=_response_text(body),
            model=response_model,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cached_input_tokens=int(input_details.get("cached_tokens") or 0),
            provider_request_id=response_request_id,
            finish_reason=status,
        )
