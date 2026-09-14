from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest

import app.services.whatsapp.qr_gateway_client as qr_module
import app.services.zapsign_service as zapsign_module
from app.core.config import settings
from app.core.klm_runtime import ExternalActionBlockedError
from app.integrations.slack import SlackNotifier
from app.services.google_ads_service import GoogleAdsService
from app.services.meta_ads_service import MetaAdsService
from app.services.sdr_intake_service import upload_media_to_s3
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.qr_gateway_client import QrGatewayClient
from app.services.zapsign_service import ZapSignService


def close_external_gates(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_MODEL_APIS_ENABLED", True)


@pytest.mark.asyncio
async def test_remaining_integration_sinks_never_construct_http_clients_when_offline(
    monkeypatch,
):
    close_external_gates(monkeypatch)
    http_clients = Mock(
        side_effect=AssertionError("HTTP client must not be constructed while channels are offline")
    )
    monkeypatch.setattr(zapsign_module.httpx, "AsyncClient", http_clients)

    zapsign = ZapSignService(api_token="configured-token")
    with pytest.raises(ExternalActionBlockedError) as create_error:
        await zapsign.create_document(name="Contrato", signers=[])
    with pytest.raises(ExternalActionBlockedError):
        await zapsign.get_document("doc-token")
    with pytest.raises(ExternalActionBlockedError):
        await zapsign.register_webhook("https://example.test/webhook")
    status = await zapsign.get_status()

    qr = QrGatewayClient()
    qr._base_url = "https://qr.example.test"
    with pytest.raises(WhatsAppProviderError) as qr_error:
        await qr._request("GET", "/instances/blocked")

    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "configured-token")
    assert await SlackNotifier().send_message("blocked") is False

    google_ads = GoogleAdsService("dev", "client", "secret", "refresh", "123")
    with pytest.raises(ExternalActionBlockedError):
        await google_ads.get_campaign_spend()

    meta_ads = MetaAdsService("token", "act_123")
    with pytest.raises(ExternalActionBlockedError):
        await meta_ads.get_campaign_spend()
    with pytest.raises(ExternalActionBlockedError):
        await meta_ads.get_lead_form_data("lead-123")

    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "configured-token")
    assert (
        await upload_media_to_s3(
            media_id="100000000000123",
            media_type="document",
            contact_id=uuid4(),
            matter_id=None,
        )
        is None
    )

    assert create_error.value.code == "KLM-EXT-001"
    assert qr_error.value.code == "KLM-EXT-001"
    assert status == {
        "connected": False,
        "api_token_configured": True,
        "webhook_registered": False,
        "external_actions_enabled": False,
    }
    http_clients.assert_not_called()


@pytest.mark.asyncio
async def test_qr_sink_keeps_approved_transport_path_functional(monkeypatch):
    monkeypatch.setattr(qr_module, "external_delivery_allowed", lambda: True)

    response = Mock(status_code=200, content=b"{}")
    response.json.return_value = {"status": "connected"}
    client = Mock()
    client.request = Mock()

    async def request(*args, **kwargs):
        client.request(*args, **kwargs)
        return response

    client.request_async = request

    class ClientContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, *args, **kwargs):
            return await client.request_async(*args, **kwargs)

    async_client = Mock(return_value=ClientContext())
    monkeypatch.setattr(qr_module.httpx, "AsyncClient", async_client)

    qr = QrGatewayClient()
    qr._base_url = "https://qr.example.test"
    result = await qr._request("GET", "/instances/ok")

    assert result == {"status": "connected"}
    async_client.assert_called_once_with(timeout=20)
    client.request.assert_called_once()
