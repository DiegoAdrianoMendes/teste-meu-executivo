from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.platform_audit_event import PlatformAuditEvent
from app.models.platform_official_whatsapp import (
    PlatformOfficialWhatsAppConnection,
    PlatformOfficialWhatsAppDelivery,
    PlatformOfficialWhatsAppDeliveryState,
    PlatformOfficialWhatsAppRole,
)
from app.services.platform_audit_event_service import PlatformAuditEventService


@pytest.mark.asyncio
async def test_platform_official_whatsapp_lists_fixed_roles(
    platform_client,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.api.v1.endpoints.platform_official_whatsapp.external_delivery_allowed",
        lambda: False,
    )

    response = await platform_client.get("/api/v1/platform/official-whatsapp")

    assert response.status_code == 200
    payload = response.json()
    assert [item["role"] for item in payload] == ["notifications", "outbound", "support"]


@pytest.mark.asyncio
async def test_platform_official_whatsapp_manual_connect_and_test_send(platform_client, db_session, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "app.api.v1.endpoints.platform_official_whatsapp.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.platform_official_whatsapp_service.external_delivery_allowed",
        lambda: True,
    )

    async def fake_get_phone_number_details(self, phone_number_id: str, access_token: str):
        assert phone_number_id == "pn_notify"
        assert access_token == "token-notify"
        return {"display_phone_number": "+55 11 98888-0001", "verified_name": "KLM Hub Notify"}

    async def fake_subscribe(self, waba_id: str, access_token: str):
        assert waba_id == "waba_notify"
        assert access_token == "token-notify"

    async def fake_register(self, phone_number_id: str, access_token: str, pin: str):
        assert phone_number_id == "pn_notify"
        assert access_token == "token-notify"
        seen["pin"] = pin

    async def fake_send_text_message(self, *, phone_number_id: str, access_token: str, to: str, body: str):
        assert phone_number_id == "pn_notify"
        assert access_token == "token-notify"
        assert to == "5511999990000"
        assert body == "oi"
        return {"messages": [{"id": "wamid-test"}]}

    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.get_phone_number_details",
        fake_get_phone_number_details,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.subscribe_app",
        fake_subscribe,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.register_phone_number",
        fake_register,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.send_text_message",
        fake_send_text_message,
    )

    connect_response = await platform_client.put(
        "/api/v1/platform/official-whatsapp/notifications/manual",
        json={
            "accessToken": "token-notify",
            "phoneNumberId": "pn_notify",
            "businessAccountId": "waba_notify",
            "isActive": True,
        },
    )

    assert connect_response.status_code == 200, connect_response.text
    body = connect_response.json()
    assert body["role"] == "notifications"
    assert body["status"] == "connected"
    assert body["displayPhoneNumber"] == "+55 11 98888-0001"
    assert body["webhookRegistered"] is True
    assert len(seen["pin"]) == 6

    stored = (
        await db_session.execute(
            select(PlatformOfficialWhatsAppConnection).where(
                PlatformOfficialWhatsAppConnection.role == PlatformOfficialWhatsAppRole.notifications
            )
        )
    ).scalar_one()
    assert stored.access_token_encrypted
    assert stored.registration_pin_encrypted
    assert "token-notify" not in stored.access_token_encrypted
    connect_audit = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_id == str(stored.id),
            PlatformAuditEvent.action == "official_whatsapp.connect_manual",
        )
    )
    assert connect_audit is not None
    assert connect_audit.before is None
    assert connect_audit.after["status"] == "connected"
    assert connect_audit.after["phoneNumberIdConfigured"] is True
    assert "token-notify" not in str(connect_audit.after)

    test_response = await platform_client.post(
        "/api/v1/platform/official-whatsapp/notifications/test",
        headers={"Idempotency-Key": "official-api-test-send"},
        json={"to": "+5511999990000", "body": "oi"},
    )
    assert test_response.status_code == 200, test_response.text
    assert test_response.json()["success"] is True
    test_audit = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_id == str(stored.id),
            PlatformAuditEvent.action == "official_whatsapp.test_send",
        )
    )
    assert test_audit is not None
    assert test_audit.after["testSucceeded"] is True
    assert test_audit.after["destinationLast4"] == "0000"
    assert test_audit.after["bodyLength"] == 2
    assert "+5511999990000" not in str(test_audit.after)
    assert "oi" not in str(test_audit.after)
    delivery = await db_session.scalar(select(PlatformOfficialWhatsAppDelivery))
    assert delivery is not None
    assert delivery.state is PlatformOfficialWhatsAppDeliveryState.sent
    assert delivery.external_message_id == "wamid-test"
    assert delivery.attempt_count == 1
    assert delivery.payload_ciphertext is None
    assert delivery.destination_last4 == "0000"
    assert delivery.body_length == 2
    assert "official-api-test-send" not in delivery.idempotency_key_hash

    disabled = await platform_client.patch(
        "/api/v1/platform/official-whatsapp/notifications/active",
        json={"isActive": False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["isActive"] is False
    reenabled = await platform_client.patch(
        "/api/v1/platform/official-whatsapp/notifications/active",
        json={"isActive": True},
    )
    assert reenabled.status_code == 200, reenabled.text

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    with monkeypatch.context() as audit_failure:
        audit_failure.setattr(PlatformAuditEventService, "record", fail_audit)
        with pytest.raises(RuntimeError, match="audit storage unavailable"):
            await platform_client.patch(
                "/api/v1/platform/official-whatsapp/notifications/active",
                json={"isActive": False},
            )
    db_session.expire_all()
    persisted = await db_session.get(PlatformOfficialWhatsAppConnection, stored.id)
    assert persisted is not None
    assert persisted.is_active is True

    disconnected = await platform_client.delete(
        "/api/v1/platform/official-whatsapp/notifications"
    )
    assert disconnected.status_code == 200, disconnected.text
    assert disconnected.json()["status"] == "disconnected"
    assert disconnected.json()["isActive"] is False
    disconnect_audit_count = int(
        (
            await db_session.scalar(
                select(func.count(PlatformAuditEvent.id)).where(
                    PlatformAuditEvent.resource_id == str(stored.id),
                    PlatformAuditEvent.action == "official_whatsapp.disconnect",
                )
            )
        )
        or 0
    )
    assert disconnect_audit_count == 1


@pytest.mark.asyncio
async def test_platform_official_whatsapp_embedded_signup_connects_role(
    platform_client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.api.v1.endpoints.platform_official_whatsapp.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.platform_official_whatsapp_service.external_delivery_allowed",
        lambda: True,
    )

    async def fake_exchange(self, code: str):
        assert code == "embedded-code"
        return "business-token-platform"

    async def fake_subscribe(self, waba_id: str, access_token: str):
        assert waba_id == "waba_support"
        assert access_token == "business-token-platform"

    register_called = False

    async def fake_register(self, phone_number_id: str, access_token: str, pin: str):
        nonlocal register_called
        register_called = True

    async def fake_sync(self, phone_number_id: str, access_token: str, *, sync_type: str):
        assert phone_number_id == "pn_support"
        assert access_token == "business-token-platform"
        return f"request-{sync_type}"

    async def fake_phone_details(self, phone_number_id: str, access_token: str):
        return {"display_phone_number": "+55 11 97777-0002", "verified_name": "KLM Hub Support"}

    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.exchange_code_for_business_token",
        fake_exchange,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.subscribe_app",
        fake_subscribe,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.register_phone_number",
        fake_register,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.request_business_app_sync",
        fake_sync,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.get_phone_number_details",
        fake_phone_details,
    )

    response = await platform_client.post(
        "/api/v1/platform/official-whatsapp/support/embedded-signup",
        json={
            "code": "embedded-code",
            "wabaId": "waba_support",
            "phoneNumberId": "pn_support",
            "isActive": True,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["role"] == "support"
    assert payload["connectionType"] == "TECH_PROVIDER"
    assert payload["status"] == "connected"
    assert payload["transportMode"] == "coexistence"
    assert payload["coexistenceSyncStatus"] == "requested"
    assert register_called is False
    embedded_event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.action == "official_whatsapp.embedded_signup"
        )
    )
    assert embedded_event is not None
    assert "business-token-platform" not in str(embedded_event.after)
