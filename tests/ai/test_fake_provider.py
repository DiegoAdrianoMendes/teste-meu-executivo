from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from app.ai.base import AIMessage
from app.ai.errors import AIConfigurationError, ProviderNotConfiguredError
from app.ai.factory import build_model_gateway
from app.ai.models import (
    MediaKind,
    ModelMediaPart,
    ModelRequest,
    ProviderName,
    RouteCandidate,
    TaskClass,
)
from app.ai.providers.fake import FakeModelConfig, FakeModelProviderAdapter
from app.ai.telemetry import InMemoryTelemetrySink


def request(*, response_schema=None, idempotency_key=None) -> ModelRequest:
    return ModelRequest(
        messages=[AIMessage("user", "Classify this local fixture.")],
        workspace_id="workspace-a",
        agent_name="klm_sdr",
        task_class=TaskClass.SIMPLE,
        max_output_tokens=128,
        response_schema=response_schema,
        idempotent=idempotency_key is not None,
        idempotency_key=idempotency_key,
    )


def fake_route(model: str = "klm-fake-v1") -> RouteCandidate:
    return RouteCandidate(ProviderName.FAKE, model)


@pytest.mark.asyncio
async def test_fake_adapter_is_deterministic_across_equivalent_requests():
    adapter = FakeModelProviderAdapter()

    first = await adapter.generate(request(), fake_route(), timeout_seconds=1)
    second = await adapter.generate(request(), fake_route(), timeout_seconds=1)

    assert first == second


@pytest.mark.asyncio
async def test_fake_adapter_supports_content_addressed_audio_and_image_without_raw_payload():
    adapter = FakeModelProviderAdapter()
    model_request = request()
    model_request.media_parts = (
        ModelMediaPart(MediaKind.AUDIO, "audio/ogg", b"audio-fixture"),
        ModelMediaPart(MediaKind.IMAGE, "image/png", b"image-fixture"),
    )

    first = await adapter.generate(model_request, fake_route(), timeout_seconds=1)
    second = await adapter.generate(model_request, fake_route(), timeout_seconds=1)

    assert first == second
    assert first.provider_request_id.startswith("fake_")
    assert first.text == "Local deterministic simulation."
    assert first.provider_request_id.startswith("fake_")
    assert first.input_tokens > 0
    assert first.output_tokens > 0
    assert adapter.call_count == 2


@pytest.mark.asyncio
async def test_fake_adapter_determinism_does_not_depend_on_concurrent_call_order():
    adapter = FakeModelProviderAdapter()

    results = await asyncio.gather(
        *(adapter.generate(request(), fake_route(), timeout_seconds=1) for _ in range(25))
    )

    assert len({result for result in results}) == 1
    assert adapter.call_count == 25


@pytest.mark.asyncio
async def test_fake_adapter_synthesizes_valid_structured_output():
    schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["ready", "blocked"]},
            "score": {"type": "number", "minimum": 2},
            "notes": {"type": ["string", "null"]},
            "items": {
                "type": "array",
                "minItems": 2,
                "uniqueItems": True,
                "items": {"type": "string"},
            },
        },
        "required": ["status", "score", "notes", "items"],
        "additionalProperties": False,
    }

    result = await FakeModelProviderAdapter().generate(
        request(response_schema=schema),
        fake_route(),
        timeout_seconds=1,
    )
    value = json.loads(result.text)

    Draft202012Validator(schema).validate(value)
    assert value == {
        "items": ["fake-1", "fake-2"],
        "notes": "fake-1",
        "score": 2,
        "status": "ready",
    }


@pytest.mark.asyncio
async def test_fake_adapter_supports_deterministic_fixture_selectors_and_usage():
    adapter = FakeModelProviderAdapter(
        FakeModelConfig(
            responses={
                "agent:klm_sdr": "agent fixture",
                "idempotency:run-1": "idempotent fixture",
            },
            fixed_input_tokens=11,
            fixed_output_tokens=7,
        )
    )

    result = await adapter.generate(
        request(idempotency_key="run-1"),
        fake_route(),
        timeout_seconds=1,
    )

    assert result.text == "idempotent fixture"
    assert result.input_tokens == 11
    assert result.output_tokens == 7


def test_fake_adapter_can_be_disabled_without_starting_a_request():
    adapter = FakeModelProviderAdapter(FakeModelConfig(configured=False))

    with pytest.raises(ProviderNotConfiguredError):
        adapter.require_configured()
    assert adapter.call_count == 0


def test_fake_adapter_rejects_non_integer_usage_configuration():
    with pytest.raises(AIConfigurationError, match="fixed_input_tokens"):
        FakeModelConfig(fixed_input_tokens="11")


def local_settings(**overrides):
    values = {
        "AI_PROVIDER": "fake",
        "KLM_RUNTIME_MODE": "local_simulation",
        "KLM_EXTERNAL_ACTIONS_ENABLED": False,
        "KLM_AWS_ENABLED": False,
        "KLM_ONLINE_APPROVED": False,
        "KLM_MODEL_APIS_ENABLED": False,
        "KLM_AI_FAKE_MODEL": "klm-fake-v1",
        "KLM_AI_FAKE_SEED": "test-seed",
        "KLM_AI_FAKE_RESPONSE_TEXT": "factory fixture",
        "KLM_AI_FAKE_RESPONSES_JSON": "{}",
        "KLM_AI_RESPONSE_CACHE_ENABLED": False,
        "KLM_AI_MAX_INPUT_TOKENS_PER_CALL": 10_000,
        "KLM_AI_MAX_OUTPUT_TOKENS_PER_CALL": 2_000,
        "KLM_AI_MAX_COST_PER_CALL_USD": __import__("decimal").Decimal("1"),
        "KLM_AI_WORKSPACE_DAILY_BUDGET_USD": __import__("decimal").Decimal("5"),
        "KLM_AI_WORKSPACE_DAILY_BUDGETS_JSON": "{}",
        "KLM_AI_AGENT_CALL_CAPS_JSON": "{}",
        "KLM_AI_CIRCUIT_FAILURE_THRESHOLD": 2,
        "KLM_AI_CIRCUIT_COOLDOWN_SECONDS": 60,
        "KLM_AI_HTTP_TIMEOUT_SECONDS": 1,
        "KLM_AI_MAX_RETRIES": 0,
        "KLM_AI_RETRY_BASE_SECONDS": 0,
        "KLM_AI_RESPONSE_CACHE_TTL_SECONDS": 60,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_factory_uses_only_fake_adapter_when_local_gates_are_closed(monkeypatch):
    def unexpected_online_adapter(*args, **kwargs):
        raise AssertionError("online adapter must not be constructed")

    monkeypatch.setattr("app.ai.factory.OpenAIResponsesAdapter", unexpected_online_adapter)
    gateway = build_model_gateway(
        local_settings(),
        telemetry=InMemoryTelemetrySink(),
    )

    completion = await gateway.generate(request())

    assert set(gateway.providers) == {ProviderName.FAKE}
    assert completion.provider == "fake"
    assert completion.model_used == "klm-fake-v1"
    assert completion.text == "factory fixture"
    assert completion.cost_known is True
    assert completion.cost_usd == 0


@pytest.mark.parametrize(
    "unsafe_override",
    [
        {"KLM_RUNTIME_MODE": "production"},
        {"KLM_EXTERNAL_ACTIONS_ENABLED": True},
        {"KLM_AWS_ENABLED": True},
        {"KLM_ONLINE_APPROVED": True},
        {"KLM_MODEL_APIS_ENABLED": True},
    ],
)
def test_factory_refuses_fake_when_any_external_gate_is_open(unsafe_override):
    with pytest.raises(AIConfigurationError, match="every online/external gate closed"):
        build_model_gateway(local_settings(**unsafe_override))
