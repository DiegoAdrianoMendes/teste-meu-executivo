"""Backward-compatible interface shared by the KLM model adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.ai.models import ModelAttribution, ModelMediaPart, PromptCachePolicy


@dataclass(frozen=True, slots=True)
class AIMessage:
    """A text message in a stateless model request."""

    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class AICompletionResult:
    """Normalized result with usage and cost provenance."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0
    cost_known: bool = False
    model_used: str = ""
    provider: str = ""
    request_id: str = ""
    provider_request_id: str = ""
    price_catalog_version: str = ""
    route_catalog_version: str = ""
    fallback_used: bool = False
    cache_hit: bool = False
    usage_accounted: bool = False
    structured_data: Any | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class AIProvider(ABC):
    """Compatibility surface used by existing KLM services."""

    @abstractmethod
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
        """Generate a completion without performing any channel-side action."""
        ...
