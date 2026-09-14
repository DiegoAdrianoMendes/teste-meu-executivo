"""Adapter for the existing official Vertex AI SDK provider."""
from __future__ import annotations

from typing import Any

from app.ai.errors import ProviderNotConfiguredError
from app.ai.models import (
    MediaKind,
    ModelRequest,
    PromptCachePolicy,
    ProviderName,
    ProviderResponse,
    RouteCandidate,
)
from app.ai.providers.base import ModelProviderAdapter


class VertexGeminiAdapter(ModelProviderAdapter):
    name = ProviderName.VERTEX_GEMINI
    supports_idempotent_retry = False
    supported_media_kinds = frozenset({MediaKind.AUDIO, MediaKind.IMAGE})
    supported_prompt_cache_policies = frozenset(
        {PromptCachePolicy.OFF, PromptCachePolicy.AUTO}
    )

    def __init__(
        self,
        *,
        project_id: str,
        location: str,
        provider: Any | None = None,
    ) -> None:
        self.project_id = project_id
        self.location = location
        self._provider = provider

    def require_configured(self) -> None:
        if not self.project_id.strip() or not self.location.strip():
            raise ProviderNotConfiguredError("Projeto/localização do Vertex AI não configurados.")

    def _get_provider(self, model: str):
        if self._provider is None:
            from app.ai.vertex_gemini import VertexGeminiProvider

            self._provider = VertexGeminiProvider(
                project_id=self.project_id,
                location=self.location,
                model=model,
                enforce_runtime_gate=False,
            )
        return self._provider

    async def generate(
        self,
        request: ModelRequest,
        route: RouteCandidate,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        self.require_configured()
        provider = self._get_provider(route.model)
        result = await provider._complete_for_gateway(
            request.messages,
            temperature=request.temperature,
            max_tokens=request.max_output_tokens,
            response_schema=request.response_schema,
            model_override=route.model,
            media_parts=request.media_parts,
        )
        return ProviderResponse(
            text=result.text,
            model=result.model_used or route.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cached_input_tokens=result.cached_input_tokens,
            provider_request_id=result.provider_request_id,
        )
