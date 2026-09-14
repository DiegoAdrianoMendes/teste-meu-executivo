"""Contrato do adapter Maritaca (Sabiá): HTTP mockado, sem chamada real."""
from __future__ import annotations

import json

import httpx
import pytest

from app.ai.base import AIMessage
from app.ai.errors import (
    ProviderCallError,
    ProviderCapabilityError,
    ProviderNotConfiguredError,
)
from app.ai.models import (
    MediaKind,
    ModelMediaPart,
    ModelRequest,
    PromptCachePolicy,
    ProviderName,
    RouteCandidate,
    TaskClass,
)
from app.ai.providers.maritaca_chat import MaritacaChatAdapter

BASE_URL = "https://chat.maritaca.ai/api"

SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
    "required": ["status"],
    "additionalProperties": False,
}


def model_request(**overrides):
    values = {
        "messages": [
            AIMessage("system", "Seja preciso."),
            AIMessage("user", "Responda em português."),
        ],
        "workspace_id": "workspace-a",
        "agent_name": "sdr",
        "task_class": TaskClass.SIMPLE,
        "max_output_tokens": 128,
    }
    values.update(overrides)
    return ModelRequest(**values)


def success_body(**overrides):
    body = {
        "id": "chatcmpl-1",
        "model": "sabiazinho-3",
        "choices": [
            {
                "message": {"role": "assistant", "content": "Tudo certo."},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 5},
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_maritaca_contract_openai_compatible_payload_and_auth():
    captured: dict = {}
    headers: dict = {}

    async def handler(request):
        captured.update(json.loads(request.content))
        headers.update(dict(request.headers))
        assert request.url == httpx.URL(f"{BASE_URL}/chat/completions")
        return httpx.Response(200, json=success_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await MaritacaChatAdapter(
            api_key="secret-test",
            base_url=BASE_URL,
            client=client,
        ).generate(
            model_request(),
            RouteCandidate(ProviderName.MARITACA, "sabiazinho-3"),
            timeout_seconds=1,
        )

    assert headers["authorization"] == "Bearer secret-test"
    assert captured["model"] == "sabiazinho-3"
    assert captured["stream"] is False
    assert captured["max_tokens"] == 128
    assert captured["messages"] == [
        {"role": "system", "content": "Seja preciso."},
        {"role": "user", "content": "Responda em português."},
    ]
    assert result.text == "Tudo certo."
    assert result.model == "sabiazinho-3"
    assert result.input_tokens == 12
    assert result.output_tokens == 5
    assert result.provider_request_id == "chatcmpl-1"
    assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_maritaca_sends_json_schema_response_format_when_schema_present():
    captured: dict = {}

    async def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json=success_body(
                choices=[
                    {
                        "message": {"role": "assistant", "content": '{"status":"ok"}'},
                        "finish_reason": "stop",
                    }
                ]
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await MaritacaChatAdapter(
            api_key="secret-test",
            base_url=BASE_URL,
            client=client,
        ).generate(
            model_request(response_schema=SCHEMA),
            RouteCandidate(ProviderName.MARITACA, "sabia-3"),
            timeout_seconds=1,
        )

    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert captured["response_format"]["json_schema"]["schema"] == SCHEMA
    assert result.text == '{"status":"ok"}'


@pytest.mark.asyncio
async def test_maritaca_response_without_content_is_fail_closed():
    async def handler(request):
        return httpx.Response(200, json=success_body(choices=[]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = MaritacaChatAdapter(
            api_key="secret-test",
            base_url=BASE_URL,
            client=client,
        )
        with pytest.raises(ProviderCallError) as caught:
            await adapter.generate(
                model_request(),
                RouteCandidate(ProviderName.MARITACA, "sabiazinho-3"),
                timeout_seconds=1,
            )

    assert caught.value.request_started is True
    assert caught.value.billing_unknown is True


@pytest.mark.asyncio
async def test_maritaca_http_429_is_retryable_provider_error():
    async def handler(request):
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = MaritacaChatAdapter(
            api_key="secret-test",
            base_url=BASE_URL,
            client=client,
        )
        with pytest.raises(ProviderCallError) as caught:
            await adapter.generate(
                model_request(),
                RouteCandidate(ProviderName.MARITACA, "sabiazinho-3"),
                timeout_seconds=1,
            )

    assert caught.value.retryable is True
    assert caught.value.status_code == 429


@pytest.mark.asyncio
async def test_maritaca_missing_key_blocks_before_any_network():
    async def handler(request):
        raise AssertionError("nenhuma chamada de rede deveria acontecer sem credencial")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = MaritacaChatAdapter(api_key="", base_url=BASE_URL, client=client)
        with pytest.raises(ProviderNotConfiguredError):
            await adapter.generate(
                model_request(),
                RouteCandidate(ProviderName.MARITACA, "sabiazinho-3"),
                timeout_seconds=1,
            )


@pytest.mark.asyncio
async def test_maritaca_declares_text_only_and_rejects_media():
    async def handler(request):
        raise AssertionError("mídia não suportada não deve chegar à rede")

    request = model_request(
        media_parts=(ModelMediaPart(MediaKind.IMAGE, "image/png", b"png-bytes"),),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = MaritacaChatAdapter(
            api_key="secret-test",
            base_url=BASE_URL,
            client=client,
        )
        assert adapter.supported_media_kinds == frozenset()
        assert adapter.supported_prompt_cache_policies == frozenset({PromptCachePolicy.OFF})
        with pytest.raises(ProviderCapabilityError):
            await adapter.generate(
                request,
                RouteCandidate(ProviderName.MARITACA, "sabiazinho-3"),
                timeout_seconds=1,
            )
