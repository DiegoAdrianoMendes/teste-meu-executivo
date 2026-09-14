"""Provider adapter interface."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.ai.errors import ProviderCapabilityError
from app.ai.models import (
    MediaKind,
    ModelRequest,
    PromptCachePolicy,
    ProviderName,
    ProviderResponse,
    RouteCandidate,
)


class ModelProviderAdapter(ABC):
    name: ProviderName
    supports_idempotent_retry: bool = False
    supported_media_kinds: frozenset[MediaKind] = frozenset()
    supported_prompt_cache_policies: frozenset[PromptCachePolicy] = frozenset(
        {PromptCachePolicy.OFF}
    )

    def require_supported(self, request: ModelRequest) -> None:
        unsupported = {
            part.kind for part in request.media_parts if part.kind not in self.supported_media_kinds
        }
        if unsupported:
            labels = ", ".join(sorted(item.value for item in unsupported))
            raise ProviderCapabilityError(
                f"Provider {self.name.value} does not support request media: {labels}."
            )
        if request.prompt_cache_policy not in self.supported_prompt_cache_policies:
            raise ProviderCapabilityError(
                f"Provider {self.name.value} does not support prompt cache policy "
                f"{request.prompt_cache_policy.value}."
            )

    @abstractmethod
    def require_configured(self) -> None:
        """Raise before network I/O when credentials/configuration are absent."""

    @abstractmethod
    async def generate(
        self,
        request: ModelRequest,
        route: RouteCandidate,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        """Execute exactly one provider request; adapters never retry internally."""
