from __future__ import annotations

import pytest

from app.ai.base import AIMessage
from app.ai.errors import ModelAPIDisabledError
from app.ai.gemma_vertex_provider import VertexGemmaProvider
from app.ai.openai_provider import OpenAIProvider
from app.ai.vertex_gemini import VertexGeminiProvider


@pytest.fixture(autouse=True)
def _simulate_open_main_gate(monkeypatch):
    monkeypatch.setattr("app.ai.policy.require_model_api_enabled", lambda: None)


@pytest.mark.asyncio
async def test_openai_legacy_provider_is_blocked_before_adapter_network(monkeypatch) -> None:
    adapter_constructed = False

    def unexpected_adapter(*_args, **_kwargs):
        nonlocal adapter_constructed
        adapter_constructed = True
        raise AssertionError("legacy adapter must not be constructed")

    monkeypatch.setattr("app.ai.openai_provider.OpenAIResponsesAdapter", unexpected_adapter)

    with pytest.raises(ModelAPIDisabledError, match="gateway de modelos"):
        await OpenAIProvider(api_key="test-key").complete(
            [AIMessage("user", "teste")],
        )

    assert adapter_constructed is False


@pytest.mark.asyncio
async def test_vertex_gemini_legacy_provider_is_blocked_before_initialization(
    monkeypatch,
) -> None:
    initialized = False

    def unexpected_initialization():
        nonlocal initialized
        initialized = True
        raise AssertionError("legacy Vertex must not initialize")

    provider = VertexGeminiProvider(
        project_id="test-project",
        location="us-central1",
        model="gemini-test",
    )
    monkeypatch.setattr(provider, "_ensure_initialized", unexpected_initialization)

    with pytest.raises(ModelAPIDisabledError, match="gateway de modelos"):
        await provider.complete([AIMessage("user", "teste")])

    assert initialized is False


@pytest.mark.asyncio
async def test_vertex_gemma_legacy_provider_is_blocked_before_credentials_or_network(
    monkeypatch,
) -> None:
    endpoint_resolved = False
    credentials_loaded = False
    network_called = False

    def unexpected_endpoint_resolution(_model_override):
        nonlocal endpoint_resolved
        endpoint_resolved = True
        raise AssertionError("legacy Gemma must not resolve an endpoint")

    def unexpected_credentials():
        nonlocal credentials_loaded
        credentials_loaded = True
        raise AssertionError("legacy Gemma must not load credentials")

    def unexpected_post(*_args, **_kwargs):
        nonlocal network_called
        network_called = True
        raise AssertionError("legacy Gemma must not call network")

    provider = VertexGemmaProvider(
        project_id="test-project",
        location="us-central1",
        default_model="google/gemma-test",
        simple_endpoint_id="endpoint-test",
    )
    monkeypatch.setattr(provider, "_resolve_endpoint", unexpected_endpoint_resolution)
    monkeypatch.setattr(provider, "_ensure_credentials", unexpected_credentials)
    monkeypatch.setattr("app.ai.gemma_vertex_provider.requests.post", unexpected_post)

    with pytest.raises(ModelAPIDisabledError, match="gateway de modelos"):
        await provider.complete([AIMessage("user", "teste")])

    assert endpoint_resolved is False
    assert credentials_loaded is False
    assert network_called is False
