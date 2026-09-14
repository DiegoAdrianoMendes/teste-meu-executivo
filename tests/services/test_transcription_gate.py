from unittest.mock import Mock

import pytest

from app.ai.base import AICompletionResult
from app.ai.errors import ModelAPIDisabledError, ProviderCallError
from app.ai.models import MediaKind
from app.core.config import settings
from app.services.transcription_service import TranscriptionService


class BlockingGateway:
    def __init__(self) -> None:
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        raise ModelAPIDisabledError("closed")


class CapturingGateway:
    def __init__(self, text: str) -> None:
        self.text = text
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return AICompletionResult(
            text=self.text,
            request_id="local-request",
            provider="fake",
        )


class UncertainGateway:
    async def generate(self, request):
        raise ProviderCallError(
            "timeout",
            request_started=True,
            billing_unknown=True,
        )


@pytest.mark.asyncio
async def test_audio_transcription_does_not_initialize_vertex_while_model_gate_is_closed(
    monkeypatch,
):
    provider = Mock(side_effect=AssertionError("Vertex não deve ser inicializado"))
    monkeypatch.setattr(settings, "KLM_MODEL_APIS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr("app.ai.vertex_gemini.VertexGeminiProvider", provider)

    gateway = BlockingGateway()
    result = await TranscriptionService(gateway).transcribe_audio(
        b"audio",
        workspace_id="workspace-a",
    )

    assert result == "[Áudio recebido — transcrição não disponível]"
    assert gateway.requests[0].workspace_id == "workspace-a"
    assert gateway.requests[0].agent_name == "media_transcriber"
    provider.assert_not_called()


@pytest.mark.asyncio
async def test_image_transcription_does_not_initialize_vertex_while_legacy_calls_are_closed(
    monkeypatch,
):
    provider = Mock(side_effect=AssertionError("Vertex não deve ser inicializado"))
    monkeypatch.setattr(settings, "KLM_MODEL_APIS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_AI_LEGACY_DIRECT_CALLS_ENABLED", False)
    monkeypatch.setattr("app.ai.vertex_gemini.VertexGeminiProvider", provider)

    gateway = BlockingGateway()
    result = await TranscriptionService(gateway).transcribe_image(
        b"image",
        workspace_id="workspace-a",
    )

    assert result == "[Imagem recebida — texto não extraído]"
    assert gateway.requests[0].media_parts[0].kind is MediaKind.IMAGE
    provider.assert_not_called()


@pytest.mark.asyncio
async def test_media_request_is_scoped_sensitive_content_addressed_and_not_cacheable():
    gateway = CapturingGateway("texto extraído")

    result = await TranscriptionService(gateway).transcribe_image(
        b"image fixture",
        "image/png; charset=binary",
        workspace_id="workspace-a",
        source_ref="message-1",
    )

    assert result == "texto extraído"
    request = gateway.requests[0]
    assert request.workspace_id == "workspace-a"
    assert request.agent_name == "media_ocr"
    assert request.idempotent is True
    assert request.idempotency_key.startswith("media:image:")
    assert request.contains_sensitive_data is True
    assert request.allow_response_cache is False
    assert request.media_parts[0].mime_type == "image/png"
    assert len(request.media_parts[0].content_sha256) == 64


@pytest.mark.asyncio
async def test_whatsapp_media_download_is_blocked_before_http(monkeypatch):
    client = Mock(side_effect=AssertionError("HTTP não deve ser inicializado"))
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr("app.services.transcription_service.httpx.AsyncClient", client)

    with pytest.raises(RuntimeError, match="bloqueado"):
        await TranscriptionService().download_whatsapp_media("100000000000005", "token")

    client.assert_not_called()


@pytest.mark.asyncio
async def test_media_billing_uncertainty_never_becomes_a_placeholder():
    with pytest.raises(ProviderCallError) as caught:
        await TranscriptionService(UncertainGateway()).transcribe_audio(
            b"audio",
            workspace_id="workspace-a",
            source_ref="message-a",
        )

    assert caught.value.request_started is True
    assert caught.value.billing_unknown is True
