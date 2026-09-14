"""Compatibility wrapper for explicit legacy OpenAI provider imports.

The launch route uses :class:`OpenAIResponsesAdapter` through ``ModelGateway``.
This wrapper remains functional only behind the legacy direct-call gate and
uses the same versioned allowlist and price catalog.
"""
from __future__ import annotations

from typing import Any

from app.ai.base import AICompletionResult, AIMessage, AIProvider
from app.ai.catalog import PriceCatalog, parse_json_string_set
from app.ai.models import (
    ModelAttribution,
    ModelMediaPart,
    ModelRequest,
    PromptCachePolicy,
    ProviderName,
    RouteCandidate,
    TaskClass,
)
from app.ai.policy import require_legacy_direct_calls_enabled
from app.ai.providers.openai_responses import OpenAIResponsesAdapter
from app.ai.security import require_allowed_base_url, require_allowed_model
from app.ai.structured_output import parse_and_validate, validate_response_schema


class OpenAIProvider(AIProvider):
    """Explicit migration-only adapter; new code should use the gateway."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        *,
        enforce_runtime_gate: bool = True,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.enforce_runtime_gate = enforce_runtime_gate

    async def complete(
        self,
        messages: list[AIMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
        model_override: str | None = None,
        *,
        workspace_id: str | None = None,
        agent_name: str | None = None,
        idempotent: bool = False,
        idempotency_key: str | None = None,
        contains_sensitive_data: bool = True,
        allow_response_cache: bool = False,
        media_parts: tuple[ModelMediaPart, ...] = (),
        attribution: ModelAttribution | None = None,
        prompt_cache_policy: PromptCachePolicy | None = None,
        prompt_cache_key: str | None = None,
    ) -> AICompletionResult:
        from app.core.config import settings

        require_legacy_direct_calls_enabled()
        model = model_override or self.model
        route = RouteCandidate(ProviderName.OPENAI, model)
        allowlist = parse_json_string_set(
            settings.KLM_AI_MODEL_ALLOWLIST_JSON,
            "KLM_AI_MODEL_ALLOWLIST_JSON",
        )
        require_allowed_model(route, allowlist)
        prices = PriceCatalog.from_json(settings.KLM_AI_PRICE_CATALOG_JSON)
        prices.require_price(route)
        allowed_urls = parse_json_string_set(
            settings.KLM_AI_ALLOWED_BASE_URLS_JSON,
            "KLM_AI_ALLOWED_BASE_URLS_JSON",
        )
        base_url = require_allowed_base_url(settings.KLM_AI_OPENAI_BASE_URL, allowed_urls)
        if response_schema is not None:
            validate_response_schema(response_schema, contains_sensitive_data=False)

        response = await OpenAIResponsesAdapter(
            api_key=self.api_key,
            base_url=base_url,
        ).generate(
            ModelRequest(
                messages=messages,
                workspace_id=workspace_id or "legacy-direct",
                agent_name=agent_name or "legacy-openai",
                task_class=TaskClass.MEDIUM,
                temperature=temperature,
                max_output_tokens=max_tokens or settings.KLM_AI_DEFAULT_MAX_OUTPUT_TOKENS,
                response_schema=response_schema,
                idempotent=idempotent,
                idempotency_key=idempotency_key,
                contains_sensitive_data=contains_sensitive_data,
                allow_response_cache=allow_response_cache,
                media_parts=media_parts,
                attribution=attribution
                or ModelAttribution(operation="legacy.openai.complete"),
                prompt_cache_policy=prompt_cache_policy or PromptCachePolicy.OFF,
                prompt_cache_key=prompt_cache_key,
            ),
            route,
            timeout_seconds=settings.KLM_AI_HTTP_TIMEOUT_SECONDS,
        )
        cost = prices.compute_cost(
            route,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cached_input_tokens=response.cached_input_tokens,
        )
        structured = (
            parse_and_validate(response.text, response_schema)
            if response_schema is not None
            else None
        )
        return AICompletionResult(
            text=response.text,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cached_input_tokens=response.cached_input_tokens,
            cost_usd=float(cost),
            cost_known=True,
            model_used=response.model,
            provider=ProviderName.OPENAI.value,
            provider_request_id=response.provider_request_id,
            price_catalog_version=prices.version,
            structured_data=structured,
        )
