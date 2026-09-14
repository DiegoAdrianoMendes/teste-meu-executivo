from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import app.api.v1.endpoints.platform_official_whatsapp as platform_endpoints
import app.api.v1.endpoints.tech_providers as tech_provider_endpoints
import app.api.v1.endpoints.whatsapp as whatsapp_endpoints
from app.services.platform_official_whatsapp_service import (
    PlatformOfficialWhatsAppService,
)
from app.services.whatsapp_service import WhatsAppService


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload", "service_method"),
    [
        (
            "PUT",
            "/api/v1/platform/official-whatsapp/notifications/manual",
            {
                "accessToken": "blocked-token",
                "phoneNumberId": "pn-blocked",
                "businessAccountId": "waba-blocked",
            },
            "connect_manual",
        ),
        (
            "POST",
            "/api/v1/platform/official-whatsapp/support/embedded-signup",
            {
                "code": "blocked-code",
                "wabaId": "waba-blocked",
                "phoneNumberId": "pn-blocked",
            },
            "complete_embedded_signup",
        ),
        (
            "POST",
            "/api/v1/platform/official-whatsapp/notifications/test",
            {"to": "+5511999999999", "body": "bloqueada"},
            "send_test_message",
        ),
    ],
)
async def test_platform_meta_aliases_fail_closed_before_service(
    platform_client,
    monkeypatch,
    method,
    path,
    payload,
    service_method,
):
    monkeypatch.setattr(platform_endpoints, "external_delivery_allowed", lambda: False)
    service_call = AsyncMock(side_effect=AssertionError("service must not run"))
    monkeypatch.setattr(PlatformOfficialWhatsAppService, service_method, service_call)

    response = await platform_client.request(
        method,
        path,
        json=payload,
        headers={"Idempotency-Key": "meta-fail-closed-001"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KLM-EXT-001"
    service_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_tech_provider_callback_fails_closed_before_service(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        tech_provider_endpoints,
        "external_delivery_allowed",
        lambda: False,
    )
    service_call = AsyncMock(side_effect=AssertionError("service must not run"))
    monkeypatch.setattr(WhatsAppService, "complete_embedded_signup", service_call)

    response = await client.post(
        "/api/v1/tech-providers/callback",
        json={
            "code": "blocked-code",
            "waba_id": "waba-blocked",
            "phone_number_id": "pn-blocked",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KLM-EXT-001"
    service_call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/whatsapp/connect",
        "/api/v1/whatsapp/connections/manual",
    ],
)
async def test_existing_connect_and_manual_aliases_remain_fail_closed(
    client,
    monkeypatch,
    path,
):
    monkeypatch.setattr(whatsapp_endpoints, "external_delivery_allowed", lambda: False)
    service_call = AsyncMock(side_effect=AssertionError("service must not run"))
    monkeypatch.setattr(WhatsAppService, "create_manual_connection", service_call)

    response = await client.post(
        path,
        json={
            "accessToken": "blocked-token",
            "phoneNumberId": "pn-blocked",
            "businessAccountId": "waba-blocked",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KLM-EXT-001"
    service_call.assert_not_awaited()
