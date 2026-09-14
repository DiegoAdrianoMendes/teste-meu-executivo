from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.platform_official_whatsapp_service as platform_service_module
import app.services.tech_provider_service as tech_provider_service_module
import app.services.whatsapp.meta_cloud_client as meta_cloud_client_module
from app.models.platform_official_whatsapp import (
    PlatformOfficialWhatsAppConnection,
    PlatformOfficialWhatsAppDelivery,
    PlatformOfficialWhatsAppDeliveryState,
    PlatformOfficialWhatsAppRole,
)
from app.models.whatsapp import ConnectionType, WaConnectionStatus
from app.schemas.platform_whatsapp import (
    PlatformOfficialWhatsAppConnectManual,
    PlatformOfficialWhatsAppEmbeddedCallback,
)
from app.schemas.whatsapp import TechProviderCallbackRequest
from app.services.platform_official_whatsapp_service import (
    PlatformOfficialWhatsAppService,
)
from app.services.platform_whatsapp_alert_service import PlatformWhatsAppAlertService
from app.services.tech_provider_service import TechProviderService
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.meta_cloud_client import MetaCloudClient


def test_official_delivery_outbox_is_global_without_workspace_sentinel():
    columns = set(PlatformOfficialWhatsAppDelivery.__table__.columns.keys())

    assert "workspace_id" not in columns
    assert {"connection_id", "role", "source_type", "source_id"} <= columns


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["manual", "embedded", "test"])
async def test_platform_service_external_operations_fail_closed_before_provider(
    db_session,
    monkeypatch,
    operation,
):
    monkeypatch.setattr(
        platform_service_module,
        "external_delivery_allowed",
        lambda: False,
    )
    meta_client = MagicMock(spec=MetaCloudClient)
    service = PlatformOfficialWhatsAppService(db_session, meta_client=meta_client)

    with pytest.raises(HTTPException) as raised:
        if operation == "manual":
            await service.connect_manual(
                role=PlatformOfficialWhatsAppRole.notifications,
                payload=PlatformOfficialWhatsAppConnectManual(
                    access_token="blocked-token",
                    phone_number_id="pn-blocked",
                    business_account_id="waba-blocked",
                ),
                user_id=uuid4(),
            )
        elif operation == "embedded":
            await service.complete_embedded_signup(
                role=PlatformOfficialWhatsAppRole.support,
                payload=PlatformOfficialWhatsAppEmbeddedCallback(
                    code="blocked-code",
                    waba_id="waba-blocked",
                    phone_number_id="pn-blocked",
                ),
                user_id=uuid4(),
            )
        else:
            await service.send_test_message(
                role=PlatformOfficialWhatsAppRole.notifications,
                to="+5511999999999",
                body="bloqueada",
                idempotency_key="blocked-test-send",
                requested_by_user_id=None,
            )

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "KLM-EXT-001"
    assert meta_client.method_calls == []


@pytest.mark.asyncio
async def test_tech_provider_service_fails_closed_before_provider_or_persistence(
    monkeypatch,
):
    monkeypatch.setattr(
        tech_provider_service_module,
        "external_delivery_allowed",
        lambda: False,
    )
    connection_service = MagicMock(spec=WhatsAppConnectionService)
    meta_client = MagicMock(spec=MetaCloudClient)
    service = TechProviderService(connection_service, meta_client=meta_client)

    with pytest.raises(HTTPException) as raised:
        await service.complete_embedded_signup(
            workspace_id=uuid4(),
            payload=TechProviderCallbackRequest(
                code="blocked-code",
                waba_id="waba-blocked",
                phone_number_id="pn-blocked",
            ),
        )

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "KLM-EXT-001"
    assert meta_client.method_calls == []
    assert connection_service.method_calls == []


@pytest.mark.asyncio
async def test_platform_service_local_list_remains_available_when_delivery_is_blocked(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(
        platform_service_module,
        "external_delivery_allowed",
        lambda: False,
    )
    meta_client = MagicMock(spec=MetaCloudClient)
    service = PlatformOfficialWhatsAppService(db_session, meta_client=meta_client)

    result = await service.list_connections()

    assert [item.role for item in result] == [
        PlatformOfficialWhatsAppRole.notifications,
        PlatformOfficialWhatsAppRole.outbound,
        PlatformOfficialWhatsAppRole.support,
    ]
    assert meta_client.method_calls == []


@pytest.mark.asyncio
async def test_meta_cloud_client_sink_blocks_before_http_client_is_created(monkeypatch):
    class ForbiddenAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("http client must not be created while delivery is blocked")

    monkeypatch.setattr(
        meta_cloud_client_module,
        "external_delivery_allowed",
        lambda: False,
    )
    monkeypatch.setattr(meta_cloud_client_module.httpx, "AsyncClient", ForbiddenAsyncClient)

    with pytest.raises(WhatsAppProviderError) as raised:
        await MetaCloudClient().get_phone_number_details("pn-blocked", "blocked-token")

    assert raised.value.code == "KLM-EXT-001"
    assert raised.value.status_code == 503


@pytest.mark.asyncio
async def test_meta_cloud_client_requests_both_supported_coexistence_sync_types():
    client = MetaCloudClient()
    client._request = AsyncMock(
        side_effect=[{"request_id": "contacts-1"}, {"request_id": "history-1"}]
    )

    contacts = await client.request_business_app_sync(
        "phone-1", "token-1", sync_type="smb_app_state_sync"
    )
    history = await client.request_business_app_sync(
        "phone-1", "token-1", sync_type="history"
    )

    assert (contacts, history) == ("contacts-1", "history-1")
    assert [call.kwargs["json"]["sync_type"] for call in client._request.await_args_list] == [
        "smb_app_state_sync",
        "history",
    ]


@pytest.mark.asyncio
async def test_meta_embedded_signup_token_records_provider_expiration(monkeypatch):
    monkeypatch.setattr(meta_cloud_client_module.settings, "META_APP_ID", "app-id")
    monkeypatch.setattr(meta_cloud_client_module.settings, "META_APP_SECRET", "app-secret")
    client = MetaCloudClient()
    client._request = AsyncMock(return_value={"access_token": "business-token", "expires_in": 3600})
    started_at = datetime.now(UTC)

    grant = await client.exchange_code_for_business_token("authorization-code")

    assert grant.access_token == "business-token"
    assert grant.expires_at is not None
    assert (
        started_at + timedelta(minutes=59)
        <= grant.expires_at
        <= started_at + timedelta(minutes=61)
    )
    assert client._request.await_args.kwargs["params"]["code"] == "authorization-code"


def test_meta_token_lifecycle_is_explicit_for_unknown_expiring_and_expired():
    connection = PlatformOfficialWhatsAppConnection(
        role=PlatformOfficialWhatsAppRole.support,
        connection_type=ConnectionType.TECH_PROVIDER,
    )
    assert PlatformOfficialWhatsAppService._token_lifecycle(connection) == ("unknown", None)

    connection.token_expires_at = datetime.now(UTC) + timedelta(days=7)
    state, days = PlatformOfficialWhatsAppService._token_lifecycle(connection)
    assert state == "expiring"
    assert days == 7

    connection.token_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert PlatformOfficialWhatsAppService._token_lifecycle(connection) == ("expired", 0)


@pytest.mark.asyncio
async def test_coexistence_signup_does_not_reregister_existing_business_app_number(
    monkeypatch,
):
    monkeypatch.setattr(
        tech_provider_service_module,
        "external_delivery_allowed",
        lambda: True,
    )
    connection_id = uuid4()
    connection_service = MagicMock(spec=WhatsAppConnectionService)
    connection_service.preflight_tech_provider_connection = AsyncMock()
    connection_service.save_tech_provider_connection = AsyncMock(
        return_value=MagicMock(id=connection_id)
    )
    connection_service.record_coexistence_sync = AsyncMock(
        return_value=MagicMock(id=connection_id)
    )
    meta_client = MagicMock(spec=MetaCloudClient)
    meta_client.exchange_code_for_business_token = AsyncMock(return_value="token")
    meta_client.subscribe_app = AsyncMock()
    meta_client.register_phone_number = AsyncMock()
    meta_client.request_business_app_sync = AsyncMock(
        side_effect=["contacts-request", "history-request"]
    )
    service = TechProviderService(connection_service, meta_client=meta_client)
    workspace_id = uuid4()

    await service.complete_embedded_signup(
        workspace_id=workspace_id,
        payload=TechProviderCallbackRequest(
            code="code",
            waba_id="waba",
            phone_number_id="phone",
            transport_mode="coexistence",
        ),
    )

    meta_client.register_phone_number.assert_not_awaited()
    connection_service.save_tech_provider_connection.assert_awaited_once_with(
        workspace_id=workspace_id,
        business_token="token",
        phone_number_id="phone",
        waba_id="waba",
        registration_pin=None,
        transport_mode="coexistence",
        is_default=True,
    )


async def _connected_service(
    db_session, meta_client, *, role=PlatformOfficialWhatsAppRole.outbound
):
    service = PlatformOfficialWhatsAppService(db_session, meta_client=meta_client)
    connection = PlatformOfficialWhatsAppConnection(
        role=role,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_active=True,
        phone_number_id=f"phone-{role.value}",
        access_token_encrypted=service.cipher.encrypt("provider-token"),
        status=WaConnectionStatus.connected,
        webhook_registered=True,
    )
    db_session.add(connection)
    await db_session.commit()
    return service, connection


@pytest.mark.asyncio
async def test_official_outbox_success_replay_never_sends_twice(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(platform_service_module, "external_delivery_allowed", lambda: True)
    calls: list[tuple[str, str]] = []

    class MetaClient:
        async def send_text_message(self, **kwargs):
            calls.append((kwargs["to"], kwargs["body"]))
            return {"messages": [{"id": "wamid.platform.once"}]}

    service, _connection = await _connected_service(db_session, MetaClient())
    request = {
        "role": PlatformOfficialWhatsAppRole.outbound,
        "to": "+5511999990001",
        "body": "Mensagem única",
        "idempotency_key": "official-success-replay",
        "source_type": "test",
        "source_id": "success-1",
    }

    first = await service.send_text_result(**request)
    second = await service.send_text_result(**request)

    assert first == (True, "wamid.platform.once")
    assert second == first
    assert calls == [("5511999990001", "Mensagem única")]
    delivery = await db_session.scalar(select(PlatformOfficialWhatsAppDelivery))
    assert delivery is not None
    assert delivery.state is PlatformOfficialWhatsAppDeliveryState.sent
    assert delivery.attempt_count == 1
    assert delivery.payload_ciphertext is None
    assert delivery.external_message_id == "wamid.platform.once"
    assert len(delivery.idempotency_key_hash) == 64
    assert len(delivery.payload_digest) == 64
    assert "official-success-replay" not in delivery.idempotency_key_hash


@pytest.mark.asyncio
async def test_official_send_commits_intent_not_unrelated_caller_changes(
    db_session,
    monkeypatch,
):
    """The provider sees a durable intent while caller-owned work stays pending."""
    monkeypatch.setattr(platform_service_module, "external_delivery_allowed", lambda: True)

    observer_factory = async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    provider_observation: dict[str, object] = {}

    class MetaClient:
        async def send_text_message(self, **_kwargs):
            async with observer_factory() as observer:
                persisted = await observer.scalar(select(PlatformOfficialWhatsAppDelivery))
                assert persisted is not None
                provider_observation["state"] = persisted.state
                provider_observation["attempt_count"] = persisted.attempt_count
            return {"messages": [{"id": "wamid.platform.transaction-boundary"}]}

    service, _connection = await _connected_service(db_session, MetaClient())
    unrelated = PlatformOfficialWhatsAppConnection(
        role=PlatformOfficialWhatsAppRole.support,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_active=False,
        status=WaConnectionStatus.disconnected,
        webhook_registered=False,
    )
    db_session.add(unrelated)
    await db_session.commit()

    # This change belongs to the caller and must not ride along with the
    # delivery service's commit-before-provider durability boundary.
    unrelated.display_name = "pending-caller-change"

    assert await service.send_text_result(
        role=PlatformOfficialWhatsAppRole.outbound,
        to="+5511999990010",
        body="Boundary transacional",
        idempotency_key="official-transaction-boundary",
        source_type="test",
        source_id="transaction-boundary",
    ) == (True, "wamid.platform.transaction-boundary")

    assert provider_observation == {
        "state": PlatformOfficialWhatsAppDeliveryState.provider_call_started,
        "attempt_count": 1,
    }
    await db_session.rollback()
    await db_session.refresh(unrelated)
    assert unrelated.display_name is None


@pytest.mark.asyncio
async def test_official_outbox_timeout_is_uncertain_and_never_blindly_retried(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(platform_service_module, "external_delivery_allowed", lambda: True)
    calls = 0

    class MetaClient:
        async def send_text_message(self, **_kwargs):
            nonlocal calls
            calls += 1
            raise TimeoutError("provider response lost")

    service, _connection = await _connected_service(db_session, MetaClient())
    request = {
        "role": PlatformOfficialWhatsAppRole.outbound,
        "to": "+5511999990002",
        "body": "Resultado incerto",
        "idempotency_key": "official-timeout-replay",
        "source_type": "test",
        "source_id": "uncertain-1",
    }

    with pytest.raises(HTTPException) as first:
        await service.send_text_result(**request)
    with pytest.raises(HTTPException) as replay:
        await service.send_text_result(**request)

    assert first.value.status_code == 409
    assert first.value.detail["code"] == "PLATFORM-WA-DELIVERY-UNCERTAIN"
    assert replay.value.detail["operationId"] == first.value.detail["operationId"]
    assert calls == 1
    delivery = await db_session.get(
        PlatformOfficialWhatsAppDelivery,
        UUID(first.value.detail["operationId"]),
    )
    assert delivery is not None
    assert delivery.state is PlatformOfficialWhatsAppDeliveryState.delivery_uncertain
    assert delivery.attempt_count == 1
    assert delivery.payload_ciphertext is None


@pytest.mark.asyncio
async def test_official_outbox_same_key_different_payload_is_conflict(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(platform_service_module, "external_delivery_allowed", lambda: True)
    calls = 0

    class MetaClient:
        async def send_text_message(self, **_kwargs):
            nonlocal calls
            calls += 1
            return {"messages": [{"id": "wamid.platform.conflict"}]}

    service, _connection = await _connected_service(db_session, MetaClient())
    await service.send_text_result(
        role=PlatformOfficialWhatsAppRole.outbound,
        to="+5511999990003",
        body="Primeiro corpo",
        idempotency_key="official-payload-conflict",
    )

    with pytest.raises(HTTPException) as raised:
        await service.send_text_result(
            role=PlatformOfficialWhatsAppRole.outbound,
            to="+5511999990003",
            body="Corpo diferente",
            idempotency_key="official-payload-conflict",
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "PLATFORM-WA-IDEMPOTENCY-CONFLICT"
    assert calls == 1


@pytest.mark.asyncio
async def test_official_outbox_known_provider_rejection_is_terminal_failure(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(platform_service_module, "external_delivery_allowed", lambda: True)
    calls = 0

    class MetaClient:
        async def send_text_message(self, **_kwargs):
            nonlocal calls
            calls += 1
            raise WhatsAppProviderError(
                code="WA-RECIPIENT-INVALID",
                message="recipient rejected",
                status_code=422,
                request_started=True,
                delivery_outcome_known=True,
            )

    service, _connection = await _connected_service(db_session, MetaClient())
    request = {
        "role": PlatformOfficialWhatsAppRole.outbound,
        "to": "+5511999990007",
        "body": "Falha conhecida",
        "idempotency_key": "official-known-rejection",
        "source_type": "test",
        "source_id": "failed-1",
    }

    assert await service.send_text_result(**request) == (False, None)
    assert await service.send_text_result(**request) == (False, None)
    assert calls == 1
    delivery = await db_session.scalar(select(PlatformOfficialWhatsAppDelivery))
    assert delivery is not None
    assert delivery.state is PlatformOfficialWhatsAppDeliveryState.failed
    assert delivery.attempt_count == 1
    assert delivery.payload_ciphertext is None
    assert delivery.last_error_code == "WA-RECIPIENT-INVALID"


@pytest.mark.asyncio
async def test_official_outbox_recovery_respects_external_kill_switch(
    db_session,
    monkeypatch,
):
    calls = 0

    class MetaClient:
        async def send_text_message(self, **_kwargs):
            nonlocal calls
            calls += 1
            return {"messages": [{"id": "unexpected"}]}

    service, connection = await _connected_service(db_session, MetaClient())
    monkeypatch.setattr(platform_service_module, "external_delivery_allowed", lambda: True)
    delivery = await service._prepare_text_delivery(
        connection=connection,
        role=PlatformOfficialWhatsAppRole.outbound,
        to="+5511999990004",
        body="Aguarda liberação",
        idempotency_key="official-gate-recovery",
        requested_by_user_id=None,
        source_type="test",
        source_id="gate-1",
    )
    monkeypatch.setattr(platform_service_module, "external_delivery_allowed", lambda: False)

    assert await service.dispatch_prepared() == 0
    await db_session.refresh(delivery)
    assert delivery.state is PlatformOfficialWhatsAppDeliveryState.prepared
    assert delivery.attempt_count == 0
    assert delivery.payload_ciphertext is not None
    assert "5511999990004" not in delivery.payload_ciphertext
    assert "Aguarda liberação" not in delivery.payload_ciphertext
    assert calls == 0


@pytest.mark.asyncio
async def test_official_outbox_rejects_ciphertext_tampering_before_provider(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(platform_service_module, "external_delivery_allowed", lambda: True)
    calls = 0

    class MetaClient:
        async def send_text_message(self, **_kwargs):
            nonlocal calls
            calls += 1
            return {"messages": [{"id": "must-not-send"}]}

    service, connection = await _connected_service(db_session, MetaClient())
    delivery = await service._prepare_text_delivery(
        connection=connection,
        role=PlatformOfficialWhatsAppRole.outbound,
        to="+5511999990011",
        body="Conteúdo original",
        idempotency_key="official-ciphertext-tamper",
        requested_by_user_id=None,
        source_type="test",
        source_id="tamper",
    )
    delivery.payload_ciphertext = service.cipher.encrypt(
        '{"to":"5511888880011","body":"Conteúdo adulterado"}'
    )
    await db_session.commit()

    assert await service.dispatch_delivery(delivery.id) == (False, None)
    await db_session.refresh(delivery)
    assert delivery.state is PlatformOfficialWhatsAppDeliveryState.failed
    assert delivery.last_error_code == "PLATFORM-WA-PAYLOAD-INTEGRITY"
    assert delivery.payload_ciphertext is None
    assert delivery.attempt_count == 0
    assert calls == 0


@pytest.mark.asyncio
async def test_platform_alert_without_global_outbox_session_never_calls_meta():
    meta_client = MagicMock(spec=MetaCloudClient)
    service = PlatformWhatsAppAlertService(None, client=meta_client)

    sent = await service.send_text(
        "+5511999990005",
        "Não pode sair por fallback.",
        idempotency_key="official-alert-no-session",
        source_id="alert-no-session",
    )

    assert sent is False
    assert meta_client.method_calls == []
