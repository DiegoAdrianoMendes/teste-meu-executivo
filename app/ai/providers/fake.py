"""Deterministic in-memory model adapter for local runtime and contract tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from app.ai.errors import (
    AIConfigurationError,
    ProviderCallError,
    ProviderNotConfiguredError,
)
from app.ai.models import (
    MediaKind,
    ModelRequest,
    PromptCachePolicy,
    ProviderName,
    ProviderResponse,
    RouteCandidate,
)
from app.ai.providers.base import ModelProviderAdapter
from app.ai.token_budget import DeterministicTokenEstimator

FAKE_MODEL_NAME = "klm-fake-v1"
FAKE_PROVIDER_VERSION = "1.0.0"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _resolve_local_ref(reference: str, root: Mapping[str, Any]) -> Mapping[str, Any]:
    if not reference.startswith("#/"):
        raise AIConfigurationError("Fake adapter supports only local JSON Schema references.")
    current: Any = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or part not in current:
            raise AIConfigurationError(f"Unresolved JSON Schema reference: {reference}.")
        current = current[part]
    if not isinstance(current, Mapping):
        raise AIConfigurationError(f"JSON Schema reference is not an object: {reference}.")
    return current


def _schema_type(schema: Mapping[str, Any]) -> str | None:
    raw_type = schema.get("type")
    if isinstance(raw_type, str):
        return raw_type
    if isinstance(raw_type, list):
        supported = [item for item in raw_type if isinstance(item, str)]
        return next((item for item in supported if item != "null"), "null")
    if "properties" in schema or "required" in schema:
        return "object"
    return None


def _fake_string(schema: Mapping[str, Any], index: int) -> str:
    format_name = schema.get("format")
    formatted = {
        "date": "2000-01-01",
        "date-time": "2000-01-01T00:00:00Z",
        "email": "fake@example.invalid",
        "hostname": "fake.example.invalid",
        "uri": "https://example.invalid/fake",
        "uuid": "00000000-0000-0000-0000-000000000000",
    }.get(format_name)
    value = formatted or (f"fake-{index}" if index else "fake")
    min_length = max(0, int(schema.get("minLength") or 0))
    max_length_raw = schema.get("maxLength")
    if len(value) < min_length:
        value += "x" * (min_length - len(value))
    if isinstance(max_length_raw, int):
        value = value[:max_length_raw]
    return value


def _fake_schema_value(
    schema: Mapping[str, Any],
    *,
    root: Mapping[str, Any],
    index: int = 0,
) -> Any:
    if "$ref" in schema:
        reference = schema["$ref"]
        if not isinstance(reference, str):
            raise AIConfigurationError("JSON Schema $ref must be a string.")
        return _fake_schema_value(_resolve_local_ref(reference, root), root=root, index=index)
    if "const" in schema:
        return schema["const"]
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return enum_values[0]
    if "default" in schema:
        return schema["default"]
    for keyword in ("oneOf", "anyOf"):
        alternatives = schema.get(keyword)
        if isinstance(alternatives, list) and alternatives:
            first = alternatives[0]
            if not isinstance(first, Mapping):
                raise AIConfigurationError(f"JSON Schema {keyword} entries must be objects.")
            return _fake_schema_value(first, root=root, index=index)
    all_of = schema.get("allOf")
    if isinstance(all_of, list) and all_of:
        values = []
        for item in all_of:
            if not isinstance(item, Mapping):
                raise AIConfigurationError("JSON Schema allOf entries must be objects.")
            values.append(_fake_schema_value(item, root=root, index=index))
        if all(isinstance(item, dict) for item in values):
            merged: dict[str, Any] = {}
            for item in values:
                merged.update(item)
            return merged
        return values[0]

    value_type = _schema_type(schema)
    if value_type == "object":
        properties = schema.get("properties") or {}
        if not isinstance(properties, Mapping):
            raise AIConfigurationError("JSON Schema properties must be an object.")
        required = schema.get("required") or []
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise AIConfigurationError("JSON Schema required must be a string list.")
        keys = sorted(set(required) | set(properties))
        result: dict[str, Any] = {}
        for property_index, key in enumerate(keys):
            property_schema = properties.get(key, {})
            if not isinstance(property_schema, Mapping):
                raise AIConfigurationError(f"JSON Schema property {key} must be an object.")
            result[key] = _fake_schema_value(
                property_schema,
                root=root,
                index=property_index,
            )
        return result
    if value_type == "array":
        item_schema = schema.get("items") or {}
        if not isinstance(item_schema, Mapping):
            raise AIConfigurationError("Fake adapter requires object-valued array items.")
        item_count = max(0, int(schema.get("minItems") or 0))
        return [
            _fake_schema_value(item_schema, root=root, index=item_index + 1)
            for item_index in range(item_count)
        ]
    if value_type == "string":
        return _fake_string(schema, index)
    if value_type in {"integer", "number"}:
        if "minimum" in schema:
            value = schema["minimum"]
        elif "exclusiveMinimum" in schema:
            value = schema["exclusiveMinimum"] + 1
        else:
            value = 0
        return int(value) if value_type == "integer" else value
    if value_type == "boolean":
        return False
    if value_type == "null":
        return None
    return None


@dataclass(frozen=True, slots=True)
class FakeModelConfig:
    """Immutable behavior settings for a fake adapter instance."""

    model: str = FAKE_MODEL_NAME
    seed: str = "klm-local-fake-v1"
    default_text: str = "Local deterministic simulation."
    responses: Mapping[str, str] = field(default_factory=dict)
    synthesize_structured_output: bool = True
    configured: bool = True
    fixed_input_tokens: int | None = None
    fixed_output_tokens: int | None = None
    finish_reason: str = "stop"

    def __post_init__(self) -> None:
        model = str(self.model or "").strip()
        seed = str(self.seed or "").strip()
        if not model or ":" in model:
            raise AIConfigurationError("Fake model name is required and cannot contain ':'.")
        if not seed:
            raise AIConfigurationError("Fake model seed is required.")
        if not isinstance(self.default_text, str):
            raise AIConfigurationError("Fake default_text must be a string.")
        normalized_responses: dict[str, str] = {}
        for raw_key, value in self.responses.items():
            key = str(raw_key or "").strip()
            if not key or not isinstance(value, str):
                raise AIConfigurationError("Fake response keys must be non-empty strings.")
            normalized_responses[key] = value
        for label, value in (
            ("fixed_input_tokens", self.fixed_input_tokens),
            ("fixed_output_tokens", self.fixed_output_tokens),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise AIConfigurationError(f"{label} must be a non-negative integer.")
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "seed", seed)
        object.__setattr__(self, "responses", MappingProxyType(normalized_responses))
        object.__setattr__(self, "finish_reason", str(self.finish_reason or "stop").strip())

    @classmethod
    def from_json(
        cls,
        raw_responses: str,
        **kwargs: Any,
    ) -> FakeModelConfig:
        try:
            responses = json.loads(raw_responses or "{}")
        except json.JSONDecodeError as exc:
            raise AIConfigurationError("KLM_AI_FAKE_RESPONSES_JSON contains invalid JSON.") from exc
        if not isinstance(responses, dict):
            raise AIConfigurationError("KLM_AI_FAKE_RESPONSES_JSON must be an object.")
        return cls(responses=responses, **kwargs)


class FakeModelProviderAdapter(ModelProviderAdapter):
    """Pure in-memory adapter whose response never depends on call order or wall time."""

    name = ProviderName.FAKE
    supports_idempotent_retry = True
    supported_media_kinds = frozenset({MediaKind.AUDIO, MediaKind.IMAGE, MediaKind.DOCUMENT})
    supported_prompt_cache_policies = frozenset(PromptCachePolicy)

    def __init__(
        self,
        config: FakeModelConfig | None = None,
        *,
        token_estimator: DeterministicTokenEstimator | None = None,
    ) -> None:
        self.config = config or FakeModelConfig()
        self.token_estimator = token_estimator or DeterministicTokenEstimator()
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count

    def require_configured(self) -> None:
        if not self.config.configured:
            raise ProviderNotConfiguredError("Fake model adapter is disabled by configuration.")

    def _configured_response(
        self,
        request: ModelRequest,
        route: RouteCandidate,
    ) -> str | None:
        selectors = []
        if request.idempotency_key:
            selectors.extend((f"idempotency:{request.idempotency_key}", request.idempotency_key))
        selectors.extend(
            (
                f"agent:{request.agent_name.strip().lower()}",
                f"task:{request.task_class.value}",
                f"route:{route.key}",
                "default",
            )
        )
        return next(
            (self.config.responses[key] for key in selectors if key in self.config.responses),
            None,
        )

    def _response_text(self, request: ModelRequest, route: RouteCandidate) -> str:
        configured = self._configured_response(request, route)
        if configured is not None:
            return configured
        if request.response_schema is not None and self.config.synthesize_structured_output:
            return _canonical_json(
                _fake_schema_value(request.response_schema, root=request.response_schema)
            )
        return self.config.default_text

    async def generate(
        self,
        request: ModelRequest,
        route: RouteCandidate,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        self.require_configured()
        if route.provider is not self.name or route.model != self.config.model:
            raise ProviderCallError(
                "Fake adapter received a route outside its configured model.",
                request_started=False,
            )
        if timeout_seconds <= 0:
            raise ProviderCallError("Fake adapter timeout must be positive.")

        self._call_count += 1
        text = self._response_text(request, route)
        request_material = {
            "agent_name": request.agent_name,
            "idempotency_key": request.idempotency_key,
            "max_output_tokens": request.max_output_tokens,
            "messages": [message.to_dict() for message in request.messages],
            "media_parts": [part.descriptor() for part in request.media_parts],
            "model": route.model,
            "response_schema": request.response_schema,
            "seed": self.config.seed,
            "task_class": request.task_class.value,
            "temperature": request.temperature,
            "workspace_id": request.workspace_id,
            "prompt_cache_policy": request.prompt_cache_policy.value,
            "prompt_cache_key": request.prompt_cache_key,
        }
        canonical_request = _canonical_json(request_material)
        request_digest = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        input_tokens = (
            self.config.fixed_input_tokens
            if self.config.fixed_input_tokens is not None
            else self.token_estimator.count_text(canonical_request)
        )
        output_tokens = (
            self.config.fixed_output_tokens
            if self.config.fixed_output_tokens is not None
            else self.token_estimator.count_text(text)
        )
        return ProviderResponse(
            text=text,
            model=route.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider_request_id=f"fake_{request_digest[:24]}",
            finish_reason=self.config.finish_reason,
        )
