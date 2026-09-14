"""GET /api/v1/klm/tracking/campaigns/ad-spend (auditoria 2026-07-26, item 10)."""

from __future__ import annotations

import pytest

from app.core.config import settings


@pytest.mark.asyncio
async def test_ad_spend_snapshot_without_subscription_fails_closed(client, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "test")

    response = await client.get("/api/v1/klm/tracking/campaigns/ad-spend")

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "SUBSCRIPTION_ACCESS_DENIED"
    assert response.json()["detail"]["reasonCode"] == "subscription_missing"


@pytest.mark.asyncio
async def test_ad_spend_snapshot_without_connected_accounts_reports_unavailable(
    client, active_subscription
):
    response = await client.get("/api/v1/klm/tracking/campaigns/ad-spend")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metaAds"]["available"] is False
    assert body["metaAds"]["reason"] == "conta_meta_ads_nao_conectada"
    assert body["googleAds"]["available"] is False
    assert body["googleAds"]["reason"] == "conta_google_ads_nao_conectada"
