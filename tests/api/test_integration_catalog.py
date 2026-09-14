"""
Frente B — catálogo de integrações: novos providers de automação, ads,
tracking e canais devem aparecer no catálogo com setup_steps acionáveis.
"""
from __future__ import annotations

import pytest

from app.models.finance import IntegrationProvider
from app.services.integration_service import PROVIDER_CATALOG

NEW_PROVIDER_KEYS = (
    "pluga",
    "zapier",
    "google_ads",
    "google_tag_manager",
    "ga4",
    "meta_ads",
    "meta_messenger",
    "meta_instagram",
    "whatsapp_cloud",
)


@pytest.mark.parametrize("provider_key", NEW_PROVIDER_KEYS)
def test_new_provider_is_a_valid_integration_provider_enum_member(provider_key):
    # Cada chave do catálogo precisa existir no enum nativo, senão get_catalog quebra.
    assert IntegrationProvider(provider_key).value == provider_key


@pytest.mark.parametrize("provider_key", NEW_PROVIDER_KEYS)
def test_new_provider_has_actionable_setup_steps_and_required_settings(provider_key):
    entry = PROVIDER_CATALOG[provider_key]
    setup_hint = entry["setup_hint"]

    assert entry["label"]
    assert entry["category"]
    assert entry["description"]
    assert setup_hint["setup_steps"], f"{provider_key} sem setup_steps"
    assert all(isinstance(step, str) and step.strip() for step in setup_hint["setup_steps"])
    assert "required_settings" in setup_hint
    assert isinstance(setup_hint["required_settings"], list)


@pytest.mark.asyncio
async def test_catalog_endpoint_exposes_new_providers_grouped_by_category(
    client,
    active_subscription,
):
    response = await client.get("/api/v1/integrations/catalog")

    assert response.status_code == 200, response.text
    payload = response.json()
    keys = {item["key"] for item in payload}
    for provider_key in NEW_PROVIDER_KEYS:
        assert provider_key in keys, f"{provider_key} ausente do catálogo"

    by_key = {item["key"]: item for item in payload}
    categories = {
        "pluga": "automation",
        "zapier": "automation",
        "google_ads": "ads",
        "meta_ads": "ads",
        "google_tag_manager": "tracking",
        "ga4": "tracking",
        "meta_messenger": "channels",
        "meta_instagram": "channels",
        "whatsapp_cloud": "channels",
    }
    for provider_key, expected_category in categories.items():
        item = by_key[provider_key]
        assert item["category"] == expected_category
        assert item["setupHint"]["setupSteps"], f"{provider_key} sem setupSteps na resposta"
        assert item["defaultWebhookPath"]


@pytest.mark.asyncio
async def test_catalog_points_whatsapp_cloud_to_existing_dedicated_webhook(
    client,
    active_subscription,
):
    response = await client.get("/api/v1/integrations/catalog")

    assert response.status_code == 200, response.text
    whatsapp_cloud = next(item for item in response.json() if item["key"] == "whatsapp_cloud")
    assert whatsapp_cloud["defaultWebhookPath"].endswith("/api/v1/whatsapp/webhook")
    assert "/integrations/webhooks/" not in whatsapp_cloud["defaultWebhookPath"]
