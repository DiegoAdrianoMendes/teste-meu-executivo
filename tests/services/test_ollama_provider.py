from __future__ import annotations

import pytest

from app.ai.base import AIMessage
from app.ai.errors import ModelAPIDisabledError
from app.ai.ollama_provider import OllamaProvider


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        return None


@pytest.mark.asyncio
async def test_ollama_legacy_direct_call_is_blocked_before_network(monkeypatch):
    network_called = False

    def fake_post(url, json, timeout):
        nonlocal network_called
        network_called = True
        return _FakeResponse({"message": {"content": "não deve executar"}})

    monkeypatch.setattr("app.ai.ollama_provider.requests.post", fake_post)
    monkeypatch.setattr("app.ai.policy.require_model_api_enabled", lambda: None)

    provider = OllamaProvider(
        base_url="http://ollama:11434/",
        default_model="qwen3:14b",
        timeout_seconds=45,
        keep_alive="5m",
        num_ctx=4096,
    )
    with pytest.raises(ModelAPIDisabledError, match="gateway de modelos"):
        await provider.complete(
            [AIMessage("system", "Você é jurídico."), AIMessage("user", "Oi")],
            temperature=0.1,
            max_tokens=128,
        )

    assert network_called is False


@pytest.mark.asyncio
async def test_ollama_schema_path_cannot_bypass_gateway(monkeypatch):
    network_called = False
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
    }

    def fake_post(url, json, timeout):
        nonlocal network_called
        network_called = True
        return _FakeResponse({"message": {"content": '{"status":"ok"}'}})

    monkeypatch.setattr("app.ai.ollama_provider.requests.post", fake_post)
    monkeypatch.setattr("app.ai.policy.require_model_api_enabled", lambda: None)

    provider = OllamaProvider(base_url="http://localhost:11434", default_model="gemma4:12b")
    with pytest.raises(ModelAPIDisabledError, match="gateway de modelos"):
        await provider.complete(
            [AIMessage("user", "Retorne status ok.")],
            response_schema=schema,
            model_override="gemma4:12b",
        )

    assert network_called is False
