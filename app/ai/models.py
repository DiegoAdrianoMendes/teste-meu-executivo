"""Typed contracts used by routing, provider adapters, limits, and telemetry."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from app.ai.base import AIMessage


class ProviderName(StrEnum):
    FAKE = "fake"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI_API = "gemini_api"
    VERTEX_GEMINI = "vertex_gemini"
    VERTEX_GEMMA = "vertex_gemma"
    MARITACA = "maritaca"


class TaskClass(StrEnum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    PREMIUM = "premium"


class MediaKind(StrEnum):
    AUDIO = "audio"
    IMAGE = "image"
    DOCUMENT = "document"


# MIME types exatos aceitos por tipo de mídia sem prefixo homônimo.
# DOCUMENT hoje cobre somente PDF (contrato dos adapters Anthropic/Gemini).
_EXACT_MEDIA_MIME_TYPES: dict[str, frozenset[str]] = {
    "document": frozenset({"application/pdf"}),
}


class PromptCachePolicy(StrEnum):
    OFF = "off"
    AUTO = "auto"
    ONE_HOUR = "one_hour"


@dataclass(frozen=True, slots=True)
class ModelAttribution:
    """Safe-to-hash business attribution carried with every model request."""

    operation: str = "completion"
    matter_id: str | None = None
    context_digest: str = ""
    playbook_version: str = ""
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        operation = str(self.operation or "").strip().lower()
        if not operation or len(operation) > 120:
            raise ValueError("Model attribution requires a bounded operation.")
        matter_id = str(self.matter_id or "").strip() or None
        context_digest = str(self.context_digest or "").strip().lower()
        playbook_version = str(self.playbook_version or "").strip()
        sources = tuple(
            dict.fromkeys(
                source
                for value in self.source_refs
                if (source := str(value or "").strip())
            )
        )
        if matter_id is not None and len(matter_id) > 200:
            raise ValueError("Model attribution matter_id is too long.")
        if len(context_digest) > 128 or len(playbook_version) > 160:
            raise ValueError("Model attribution metadata is too long.")
        if len(sources) > 128 or any(len(source) > 240 for source in sources):
            raise ValueError("Model attribution contains too many source references.")
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "matter_id", matter_id)
        object.__setattr__(self, "context_digest", context_digest)
        object.__setattr__(self, "playbook_version", playbook_version)
        object.__setattr__(self, "source_refs", sources)


@dataclass(frozen=True, slots=True)
class ModelMediaPart:
    """Immutable multimodal input with content-addressed provenance."""

    kind: MediaKind
    mime_type: str
    data: bytes
    filename: str | None = None
    content_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        try:
            kind = MediaKind(self.kind)
        except ValueError as exc:
            raise ValueError("Unsupported model media kind.") from exc
        mime_type = str(self.mime_type or "").split(";", 1)[0].strip().lower()
        exact_mime_types = _EXACT_MEDIA_MIME_TYPES.get(kind.value)
        if exact_mime_types is not None:
            if mime_type not in exact_mime_types:
                allowed = ", ".join(sorted(exact_mime_types))
                raise ValueError(f"{kind.value} media requires one of: {allowed}.")
        else:
            expected_prefix = f"{kind.value}/"
            if not mime_type.startswith(expected_prefix):
                raise ValueError(f"{kind.value} media requires a {expected_prefix} MIME type.")
        if not isinstance(self.data, bytes) or not self.data:
            raise ValueError("Model media data must be non-empty bytes.")
        filename = str(self.filename or "").strip() or None
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "mime_type", mime_type)
        object.__setattr__(self, "filename", filename)
        object.__setattr__(self, "content_sha256", hashlib.sha256(self.data).hexdigest())

    @property
    def size_bytes(self) -> int:
        return len(self.data)

    @property
    def estimated_input_tokens(self) -> int:
        # Provider tokenization is authoritative. This bound keeps local preflight
        # deterministic while avoiding an unbounded byte-for-token assumption.
        return min(32_768, max(256, (self.size_bytes + 15) // 16))

    def descriptor(self) -> dict[str, Any]:
        """Return safe request material without embedding the sensitive payload."""
        return {
            "content_sha256": self.content_sha256,
            "filename": self.filename,
            "kind": self.kind.value,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    provider: ProviderName
    model: str
    timeout_seconds: float | None = None

    @property
    def key(self) -> str:
        return f"{self.provider.value}:{self.model}"


@dataclass(slots=True)
class ModelRequest:
    messages: list[AIMessage]
    workspace_id: str
    agent_name: str
    task_class: TaskClass
    temperature: float = 0.2
    max_output_tokens: int = 1024
    response_schema: dict[str, Any] | None = None
    requested_model: str | None = None
    idempotent: bool = False
    idempotency_key: str | None = None
    contains_sensitive_data: bool = True
    schema_contains_sensitive_data: bool = False
    allow_response_cache: bool = False
    max_cost_usd: Decimal | None = None
    media_parts: tuple[ModelMediaPart, ...] = ()
    attribution: ModelAttribution = field(default_factory=ModelAttribution)
    prompt_cache_policy: PromptCachePolicy = PromptCachePolicy.OFF
    prompt_cache_key: str | None = None
    request_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    provider_request_id: str = ""
    finish_reason: str = ""


@dataclass(frozen=True, slots=True)
class CostEstimate:
    amount_usd: Decimal
    input_tokens: int
    output_tokens: int
    catalog_version: str


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    reservation_id: str
    workspace_id: str
    amount_usd: Decimal
    day: str


@dataclass(frozen=True, slots=True)
class BudgetResolution:
    action: Literal["settled", "released"]
    actual_usd: Decimal
    total_usd: Decimal


@dataclass(frozen=True, slots=True)
class CacheEntry:
    response: ProviderResponse
    cost_usd: Decimal
    structured_data: Any | None
