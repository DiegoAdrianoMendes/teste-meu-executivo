"""Auditoria 2026-07-26, item 10: MetaAdsService/GoogleAdsService tinham
fetch de spend pronto e zero caller no app — `ads_spend_snapshot_service.py`
é o caller real (best-effort, fail-closed sem inventar número)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
from app.services import ads_spend_snapshot_service as svc
from app.services.integration_secrets import protect_integration_settings

TEST_INTEGRATION_SECRET_KEY = "test-only-integration-key-7Hf9pQ2wX4mK8cR6vN3z"


@pytest.fixture(autouse=True)
def _configure_integration_secret_key(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", TEST_INTEGRATION_SECRET_KEY)


@pytest.mark.asyncio
async def test_meta_spend_unavailable_without_connected_account(
    db_session: AsyncSession, auth_context
):
    result = await svc.fetch_meta_campaign_spend_snapshot(db_session, auth_context.workspace_id)
    assert result["available"] is False
    assert result["reason"] == "conta_meta_ads_nao_conectada"
    assert result["campaigns"] == []


@pytest.mark.asyncio
async def test_meta_spend_blocked_when_gate_closed(
    db_session: AsyncSession, auth_context, monkeypatch
):
    db_session.add(
        IntegrationConnection(
            workspace_id=auth_context.workspace_id,
            provider=IntegrationProvider.meta_ads,
            label=svc.META_ADS_ACCOUNT_LABEL,
            status=IntegrationStatus.connected,
            settings=protect_integration_settings(
                {"accessToken": "token-value", "adAccountId": "act_123"}
            ),
        )
    )
    await db_session.flush()
    monkeypatch.setattr(
        "app.services.ads_spend_snapshot_service.external_delivery_allowed", lambda: False
    )

    result = await svc.fetch_meta_campaign_spend_snapshot(db_session, auth_context.workspace_id)
    assert result["available"] is False
    assert result["reason"] == "gate_externo_fechado"


@pytest.mark.asyncio
async def test_meta_spend_fetches_real_campaigns_when_configured_and_gate_open(
    db_session: AsyncSession, auth_context, monkeypatch
):
    db_session.add(
        IntegrationConnection(
            workspace_id=auth_context.workspace_id,
            provider=IntegrationProvider.meta_ads,
            label=svc.META_ADS_ACCOUNT_LABEL,
            status=IntegrationStatus.connected,
            settings=protect_integration_settings(
                {"accessToken": "token-value", "adAccountId": "act_123"}
            ),
        )
    )
    await db_session.flush()
    monkeypatch.setattr(
        "app.services.ads_spend_snapshot_service.external_delivery_allowed", lambda: True
    )

    async def fake_get_campaign_spend(self, date_preset="last_30d"):
        assert self.access_token == "token-value"
        assert self.ad_account_id == "act_123"
        return [{"campaign_id": "1", "campaign_name": "Campanha X", "spend_usd": 100.0}]

    monkeypatch.setattr(
        "app.services.meta_ads_service.MetaAdsService.get_campaign_spend",
        fake_get_campaign_spend,
    )

    result = await svc.fetch_meta_campaign_spend_snapshot(db_session, auth_context.workspace_id)
    assert result["available"] is True
    assert result["campaigns"][0]["campaign_name"] == "Campanha X"


@pytest.mark.asyncio
async def test_meta_spend_failure_is_best_effort_not_fatal(
    db_session: AsyncSession, auth_context, monkeypatch
):
    db_session.add(
        IntegrationConnection(
            workspace_id=auth_context.workspace_id,
            provider=IntegrationProvider.meta_ads,
            label=svc.META_ADS_ACCOUNT_LABEL,
            status=IntegrationStatus.connected,
            settings=protect_integration_settings(
                {"accessToken": "token-value", "adAccountId": "act_123"}
            ),
        )
    )
    await db_session.flush()
    monkeypatch.setattr(
        "app.services.ads_spend_snapshot_service.external_delivery_allowed", lambda: True
    )

    async def broken(self, date_preset="last_30d"):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.services.meta_ads_service.MetaAdsService.get_campaign_spend", broken
    )

    result = await svc.fetch_meta_campaign_spend_snapshot(db_session, auth_context.workspace_id)
    assert result["available"] is False
    assert result["reason"] == "erro_consulta_meta_ads"


@pytest.mark.asyncio
async def test_google_spend_unavailable_without_connected_account(
    db_session: AsyncSession, auth_context
):
    result = await svc.fetch_google_campaign_spend_snapshot(db_session, auth_context.workspace_id)
    assert result["available"] is False
    assert result["reason"] == "conta_google_ads_nao_conectada"


@pytest.mark.asyncio
async def test_google_spend_fetches_real_campaigns_when_configured_and_gate_open(
    db_session: AsyncSession, auth_context, monkeypatch
):
    db_session.add(
        IntegrationConnection(
            workspace_id=auth_context.workspace_id,
            provider=IntegrationProvider.google_ads,
            label=svc.GOOGLE_ADS_ACCOUNT_LABEL,
            status=IntegrationStatus.connected,
            settings=protect_integration_settings(
                {
                    "developerToken": "dev-token",
                    "clientId": "client-id",
                    "clientSecret": "client-secret",
                    "refreshToken": "refresh-token",
                    "customerId": "1234567890",
                }
            ),
        )
    )
    await db_session.flush()
    monkeypatch.setattr(
        "app.services.ads_spend_snapshot_service.external_delivery_allowed", lambda: True
    )

    async def fake_get_campaign_spend(self, start_date=None, end_date=None):
        assert self.customer_id == "1234567890"
        return [{"campaign_id": "1", "campaign_name": "Campanha Google", "spend_micros": 1000000}]

    monkeypatch.setattr(
        "app.services.google_ads_service.GoogleAdsService.get_campaign_spend",
        fake_get_campaign_spend,
    )

    result = await svc.fetch_google_campaign_spend_snapshot(db_session, auth_context.workspace_id)
    assert result["available"] is True
    assert result["campaigns"][0]["campaign_name"] == "Campanha Google"
