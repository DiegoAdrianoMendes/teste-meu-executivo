from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.ai.base import AIMessage
from app.ai.catalog import PriceCatalog
from app.ai.errors import ProviderCallError
from app.ai.models import (
    ModelRequest,
    PromptCachePolicy,
    ProviderName,
    RouteCandidate,
    TaskClass,
)
from app.ai.providers.anthropic_messages import AnthropicMessagesAdapter
from app.ai.providers.gemini_interactions import GeminiInteractionsAdapter
from app.ai.providers.openai_responses import OpenAIResponsesAdapter

SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
    "required": ["status"],
    "additionalProperties": False,
}


def model_request():
    return ModelRequest(
        messages=[AIMessage("system", "Seja preciso."), AIMessage("user", "Responda.")],
        workspace_id="workspace-a",
        agent_name="sdr",
        task_class=TaskClass.SIMPLE,
        max_output_tokens=128,
        response_schema=SCHEMA,
    )


def cached_model_request(policy: PromptCachePolicy = PromptCachePolicy.AUTO):
    request = model_request()
    request.prompt_cache_policy = policy
    request.prompt_cache_key = "static-sales-playbook-v1"
    return request


@pytest.mark.asyncio
async def test_openai_responses_contract_is_stateless_and_structured():
    captured = {}

    async def handler(request):
        captured.update(await request.aread() and __import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp-1",
                "status": "completed",
                "model": "openai-test",
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": '{"status":"ok"}'}
                ]}],
                "usage": {"input_tokens": 8, "output_tokens": 4},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAIResponsesAdapter(
            api_key="secret-test",
            base_url="https://api.openai.com/v1",
            client=client,
        ).generate(
            model_request(),
            RouteCandidate(ProviderName.OPENAI, "openai-test"),
            timeout_seconds=1,
        )

    assert captured["store"] is False
    assert captured["text"]["format"]["type"] == "json_schema"
    assert result.text == '{"status":"ok"}'


@pytest.mark.asyncio
async def test_openai_incomplete_response_is_not_success():
    async def handler(request):
        return httpx.Response(
            200,
            headers={"x-request-id": "header-request"},
            json={
                "id": "resp-incomplete",
                "status": "incomplete",
                "model": "openai-test",
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": '{"status":"partial"}'}
                ]}],
                "usage": {"input_tokens": 8, "output_tokens": 4},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAIResponsesAdapter(
            api_key="secret-test",
            base_url="https://api.openai.com/v1",
            client=client,
        )
        with pytest.raises(ProviderCallError) as caught:
            await adapter.generate(
                model_request(),
                RouteCandidate(ProviderName.OPENAI, "openai-test"),
                timeout_seconds=1,
            )

    error = caught.value
    assert error.error_code == "response_incomplete"
    assert error.request_started is True
    assert error.billing_unknown is True
    assert error.provider_request_id == "resp-incomplete"


@pytest.mark.asyncio
async def test_openai_prompt_cache_key_is_scoped_and_hashed():
    captured = {}

    async def handler(request):
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp-cache",
                "status": "completed",
                "model": "openai-test",
                "output": [],
                "usage": {"input_tokens": 8, "output_tokens": 0},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await OpenAIResponsesAdapter(
            api_key="secret-test",
            base_url="https://api.openai.com/v1",
            client=client,
        ).generate(
            cached_model_request(),
            RouteCandidate(ProviderName.OPENAI, "openai-test"),
            timeout_seconds=1,
        )

    assert len(captured["prompt_cache_key"]) == 64
    assert "playbook" not in captured["prompt_cache_key"]


@pytest.mark.asyncio
async def test_openai_completed_response_does_not_invent_missing_model():
    async def handler(request):
        return httpx.Response(
            200,
            json={
                "id": "resp-without-model",
                "status": "completed",
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": '{"status":"ok"}'}
                ]}],
                "usage": {"input_tokens": 8, "output_tokens": 4},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAIResponsesAdapter(
            api_key="secret-test",
            base_url="https://api.openai.com/v1",
            client=client,
        ).generate(
            model_request(),
            RouteCandidate(ProviderName.OPENAI, "openai-test"),
            timeout_seconds=1,
        )

    assert result.model == ""


@pytest.mark.asyncio
async def test_anthropic_messages_contract_uses_top_level_system_and_schema():
    captured = {}

    async def handler(request):
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "msg-1",
                "model": "claude-test",
                "content": [{"type": "text", "text": '{"status":"ok"}'}],
                "usage": {"input_tokens": 8, "output_tokens": 4},
                "stop_reason": "end_turn",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await AnthropicMessagesAdapter(
            api_key="secret-test",
            base_url="https://api.anthropic.com/v1",
            client=client,
        ).generate(
            model_request(),
            RouteCandidate(ProviderName.ANTHROPIC, "claude-test"),
            timeout_seconds=1,
        )

    assert captured["system"] == "Seja preciso."
    assert captured["messages"] == [{"role": "user", "content": "Responda."}]
    assert captured["output_config"]["format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_gemini_interactions_contract_disables_storage_and_background():
    captured = {}

    async def handler(request):
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "int-1",
                "status": "completed",
                "model": "gemini-test",
                "steps": [{"type": "model_output", "content": [
                    {"type": "text", "text": '{"status":"ok"}'}
                ]}],
                "usage": {"total_input_tokens": 8, "total_output_tokens": 4},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await GeminiInteractionsAdapter(
            api_key="secret-test",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            client=client,
        ).generate(
            model_request(),
            RouteCandidate(ProviderName.GEMINI_API, "gemini-test"),
            timeout_seconds=1,
        )

    assert captured["store"] is False
    assert captured["background"] is False
    assert captured["response_format"]["mime_type"] == "application/json"


@pytest.mark.asyncio
async def test_anthropic_one_hour_cache_marks_only_static_system_block():
    captured = {}

    async def handler(request):
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "msg-cache",
                "model": "claude-test",
                "content": [],
                "usage": {"input_tokens": 8, "output_tokens": 0},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await AnthropicMessagesAdapter(
            api_key="secret-test",
            base_url="https://api.anthropic.com/v1",
            client=client,
        ).generate(
            cached_model_request(PromptCachePolicy.ONE_HOUR),
            RouteCandidate(ProviderName.ANTHROPIC, "claude-test"),
            timeout_seconds=1,
        )

    assert captured["system"] == [
        {
            "type": "text",
            "text": "Seja preciso.",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]
    assert captured["messages"] == [{"role": "user", "content": "Responda."}]


@pytest.mark.asyncio
async def test_anthropic_cache_creation_is_charged_as_uncached_input():
    async def handler(request):
        return httpx.Response(
            200,
            json={
                "id": "msg-cache-usage",
                "model": "claude-test",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 30,
                    "output_tokens": 5,
                },
            },
        )

    route = RouteCandidate(ProviderName.ANTHROPIC, "claude-test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AnthropicMessagesAdapter(
            api_key="secret-test",
            base_url="https://api.anthropic.com/v1",
            client=client,
        ).generate(cached_model_request(), route, timeout_seconds=1)

    catalog = PriceCatalog.from_json(
        """
        {
          "version": "anthropic-cache-test",
          "models": {
            "anthropic:claude-test": {
              "input_per_million_usd": "10",
              "cached_input_per_million_usd": "1",
              "output_per_million_usd": "20"
            }
          }
        }
        """
    )

    assert result.input_tokens == 60
    assert result.cached_input_tokens == 30
    assert catalog.compute_cost(
        route,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cached_input_tokens=result.cached_input_tokens,
    ) == Decimal("0.00043000")


def _openai_completed_response(model: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "resp-temp",
            "status": "completed",
            "model": model,
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": '{"status":"ok"}'}
            ]}],
            "usage": {"input_tokens": 8, "output_tokens": 4},
        },
    )


@pytest.mark.asyncio
async def test_openai_gpt5_family_does_not_receive_temperature():
    """gpt-5* recusa `temperature` com HTTP 400; os demais modelos continuam recebendo."""
    captured: dict[str, dict] = {}

    async def handler(request):
        payload = __import__("json").loads(request.content)
        captured[payload["model"]] = payload
        return _openai_completed_response(payload["model"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAIResponsesAdapter(
            api_key="secret-test",
            base_url="https://api.openai.com/v1",
            client=client,
        )
        for model in ("gpt-5.6-luna", "openai-test"):
            await adapter.generate(
                model_request(),
                RouteCandidate(ProviderName.OPENAI, model),
                timeout_seconds=1,
            )

    assert "temperature" not in captured["gpt-5.6-luna"]
    assert captured["openai-test"]["temperature"] == 0.2


@pytest.mark.asyncio
async def test_provider_http_error_logs_the_error_detail():
    """Um 4xx do provedor deixa a causa no log, não só o código HTTP."""
    import structlog.testing

    async def handler(request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Unsupported parameter: 'temperature'",
                    "type": "invalid_request_error",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAIResponsesAdapter(
            api_key="secret-test",
            base_url="https://api.openai.com/v1",
            client=client,
        )
        with structlog.testing.capture_logs() as logs, pytest.raises(ProviderCallError) as excinfo:
            await adapter.generate(
                model_request(),
                RouteCandidate(ProviderName.OPENAI, "openai-test"),
                timeout_seconds=1,
            )

    assert excinfo.value.status_code == 400
    events = [entry for entry in logs if entry.get("event") == "ai_provider_http_error"]
    assert events, logs
    assert events[0]["provider"] == "openai"
    assert events[0]["status_code"] == 400
    assert "Unsupported parameter" in events[0]["detail"]
