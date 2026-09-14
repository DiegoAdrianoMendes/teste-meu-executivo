from __future__ import annotations

import pytest

from app.core.config import settings


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
async def test_get_manual_connection_webhook_info_returns_url_and_token(client, monkeypatch):
    monkeypatch.setattr(settings, "PAPERCLIP_PUBLIC_URL", "https://klm.example.com/")
    monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", "verify-token-123")
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "some-secret")

    response = await client.get("/api/v1/whatsapp/connections/webhook-info")

    assert response.status_code == 200
    data = response.json()
    assert data["webhookUrl"] == "https://klm.example.com/api/v1/whatsapp/webhook"
    assert data["verifyToken"] == "verify-token-123"
    assert data["verifyTokenConfigured"] is True
    assert data["appSecretConfigured"] is True
    assert isinstance(data["instructions"], list) and len(data["instructions"]) >= 3


@pytest.mark.asyncio
async def test_get_manual_connection_webhook_info_flags_missing_verify_token(client, monkeypatch):
    monkeypatch.setattr(settings, "PAPERCLIP_PUBLIC_URL", "https://klm.example.com")
    monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", "")
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "")

    response = await client.get("/api/v1/whatsapp/connections/webhook-info")

    assert response.status_code == 200
    data = response.json()
    assert data["verifyToken"] is None
    assert data["verifyTokenConfigured"] is False
    assert data["appSecretConfigured"] is False
    assert "WHATSAPP_VERIFY_TOKEN" in data["instructions"][0]
