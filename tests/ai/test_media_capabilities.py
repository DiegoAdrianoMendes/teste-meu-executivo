"""Capabilities multimodais (imagem/PDF) e roteamento por capability/preferência."""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.ai.base import AIMessage
from app.ai.catalog import RouteCatalog
from app.ai.models import (
    MediaKind,
    ModelMediaPart,
    ModelRequest,
    ProviderName,
    ProviderResponse,
    RouteCandidate,
    TaskClass,
)
from app.ai.providers.anthropic_messages import AnthropicMessagesAdapter
from app.ai.providers.fake import FakeModelProviderAdapter
from app.ai.providers.gemini_interactions import GeminiInteractionsAdapter
from app.ai.routing import ModelRouter
from tests.ai.test_gateway import FakeProvider, make_gateway, request

PNG_BYTES = b"png-fixture-bytes"
PDF_BYTES = b"%PDF-1.7 fixture"


def media_request(**overrides):
    values = {
        "messages": [
            AIMessage("system", "Seja preciso."),
            AIMessage("user", "Descreva o anexo."),
        ],
        "workspace_id": "workspace-a",
        "agent_name": "sdr",
        "task_class": TaskClass.SIMPLE,
        "max_output_tokens": 128,
        "media_parts": (
            ModelMediaPart(MediaKind.IMAGE, "image/png", PNG_BYTES),
            ModelMediaPart(MediaKind.DOCUMENT, "application/pdf", PDF_BYTES),
        ),
    }
    values.update(overrides)
    return ModelRequest(**values)


# ── ModelMediaPart: contrato do tipo DOCUMENT (PDF) ──────────────────────────


def test_document_media_part_accepts_only_pdf():
    part = ModelMediaPart(MediaKind.DOCUMENT, "application/pdf; charset=binary", PDF_BYTES)
    assert part.mime_type == "application/pdf"
    assert part.descriptor()["kind"] == "document"

    with pytest.raises(ValueError):
        ModelMediaPart(MediaKind.DOCUMENT, "application/msword", b"doc")
    with pytest.raises(ValueError):
        ModelMediaPart(MediaKind.DOCUMENT, "document/pdf", b"doc")


def test_image_and_audio_media_parts_keep_prefix_contract():
    assert ModelMediaPart(MediaKind.IMAGE, "image/png", PNG_BYTES).kind is MediaKind.IMAGE
    with pytest.raises(ValueError):
        ModelMediaPart(MediaKind.IMAGE, "application/pdf", PDF_BYTES)


# ── Adapter Anthropic: blocos base64 image/document da Messages API ──────────


@pytest.mark.asyncio
async def test_anthropic_sends_base64_image_and_document_blocks():
    captured: dict = {}

    async def handler(http_request):
        captured.update(json.loads(http_request.content))
        return httpx.Response(
            200,
            json={
                "id": "msg-media",
                "model": "claude-test",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 20, "output_tokens": 3},
                "stop_reason": "end_turn",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await AnthropicMessagesAdapter(
            api_key="secret-test",
            base_url="https://api.anthropic.com/v1",
            client=client,
        ).generate(
            media_request(),
            RouteCandidate(ProviderName.ANTHROPIC, "claude-test"),
            timeout_seconds=1,
        )

    assert captured["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(PNG_BYTES).decode("ascii"),
                    },
                },
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.b64encode(PDF_BYTES).decode("ascii"),
                    },
                },
                {"type": "text", "text": "Descreva o anexo."},
            ],
        }
    ]
    assert AnthropicMessagesAdapter.supported_media_kinds == frozenset(
        {MediaKind.IMAGE, MediaKind.DOCUMENT}
    )


# ── Adapter Gemini API: blocos inline_data base64 ────────────────────────────


@pytest.mark.asyncio
async def test_gemini_api_sends_inline_data_blocks_before_text():
    captured: dict = {}

    async def handler(http_request):
        captured.update(json.loads(http_request.content))
        return httpx.Response(
            200,
            json={
                "id": "int-media",
                "status": "completed",
                "model": "gemini-test",
                "steps": [
                    {"type": "model_output", "content": [{"type": "text", "text": "ok"}]}
                ],
                "usage": {"total_input_tokens": 20, "total_output_tokens": 3},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await GeminiInteractionsAdapter(
            api_key="secret-test",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            client=client,
        ).generate(
            media_request(),
            RouteCandidate(ProviderName.GEMINI_API, "gemini-test"),
            timeout_seconds=1,
        )

    (user_input,) = captured["input"]
    assert user_input["type"] == "user_input"
    assert user_input["content"] == [
        {
            "type": "inline_data",
            "inline_data": {
                "mime_type": "image/png",
                "data": base64.b64encode(PNG_BYTES).decode("ascii"),
            },
        },
        {
            "type": "inline_data",
            "inline_data": {
                "mime_type": "application/pdf",
                "data": base64.b64encode(PDF_BYTES).decode("ascii"),
            },
        },
        {"type": "text", "text": "Descreva o anexo."},
    ]
    assert GeminiInteractionsAdapter.supported_media_kinds == frozenset(
        {MediaKind.IMAGE, MediaKind.DOCUMENT}
    )


# ── Negociação de capability no gateway ──────────────────────────────────────


class MediaCapableFakeProvider(FakeProvider):
    supported_media_kinds = frozenset({MediaKind.IMAGE, MediaKind.DOCUMENT})


@pytest.mark.asyncio
async def test_gateway_falls_back_to_media_capable_route():
    text_only = FakeProvider(ProviderName.OPENAI)
    media_capable = MediaCapableFakeProvider(
        ProviderName.ANTHROPIC,
        response=ProviderResponse(
            text="descrito",
            model="claude-media",
            input_tokens=30,
            output_tokens=5,
            provider_request_id="media-request",
        ),
    )
    gateway, telemetry, _ = make_gateway(
        routes=(
            '{"version":"routes-test","classes":{"simple":['
            '{"provider":"openai","model":"test-cheap"},'
            '{"provider":"anthropic","model":"claude-media"}]},"agents":{}}'
        ),
        prices=(
            '{"version":"prices-test","currency":"USD","models":{'
            '"openai:test-cheap":{"input_per_million_usd":"1","output_per_million_usd":"2"},'
            '"anthropic:claude-media":{"input_per_million_usd":"1","output_per_million_usd":"2"}}}'
        ),
        providers={
            ProviderName.OPENAI: text_only,
            ProviderName.ANTHROPIC: media_capable,
        },
    )

    result = await gateway.generate(
        request(
            media_parts=(
                ModelMediaPart(MediaKind.DOCUMENT, "application/pdf", PDF_BYTES),
            )
        )
    )

    # A rota sem capability é bloqueada antes de credencial/rede; a segunda atende.
    assert text_only.config_checks == 0
    assert text_only.calls == 0
    assert media_capable.calls == 1
    assert result.text == "descrito"
    assert result.fallback_used is True
    outcomes = [event.outcome for event in telemetry.events]
    assert any(outcome.startswith("preflight_blocked:") for outcome in outcomes)
    assert outcomes[-1] == "success"


def test_fake_provider_supports_document_media():
    assert MediaKind.DOCUMENT in FakeModelProviderAdapter.supported_media_kinds


# ── Preferência de roteamento: SIMPLE pode preferir sabiazinho ───────────────

ROUTES_JSON = (
    '{"version":"routes-test","classes":{'
    '"simple":['
    '{"provider":"openai","model":"test-cheap"},'
    '{"provider":"maritaca","model":"sabiazinho-3"},'
    '{"provider":"maritaca","model":"sabia-3"}],'
    '"medium":['
    '{"provider":"openai","model":"test-cheap"},'
    '{"provider":"maritaca","model":"sabiazinho-3"}]'
    '},"agents":{}}'
)


def _router(prefer: bool) -> ModelRouter:
    return ModelRouter(
        RouteCatalog.from_json(ROUTES_JSON),
        prefer_maritaca_for_simple=prefer,
    )


def test_router_default_keeps_catalog_order_for_simple():
    routes = _router(False).candidates(request())
    assert [route.key for route in routes] == [
        "openai:test-cheap",
        "maritaca:sabiazinho-3",
        "maritaca:sabia-3",
    ]


def test_router_flag_prefers_maritaca_for_simple_keeping_relative_order():
    routes = _router(True).candidates(request())
    assert [route.key for route in routes] == [
        "maritaca:sabiazinho-3",
        "maritaca:sabia-3",
        "openai:test-cheap",
    ]


def test_router_flag_does_not_touch_other_task_classes():
    routes = _router(True).candidates(request(task_class=TaskClass.MEDIUM))
    assert [route.key for route in routes] == [
        "openai:test-cheap",
        "maritaca:sabiazinho-3",
    ]


def test_router_flag_respects_requested_model_filter():
    routes = _router(True).candidates(request(requested_model="sabia-3"))
    assert [route.key for route in routes] == ["maritaca:sabia-3"]
