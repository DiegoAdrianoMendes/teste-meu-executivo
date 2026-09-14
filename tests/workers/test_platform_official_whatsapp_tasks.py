from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.platform_official_whatsapp_service as platform_service_module
from app.models.platform_official_whatsapp import (
    PlatformOfficialWhatsAppConnection,
    PlatformOfficialWhatsAppDeliveryState,
    PlatformOfficialWhatsAppRole,
)
from app.models.whatsapp import ConnectionType, WaConnectionStatus
from app.services.platform_official_whatsapp_service import (
    PlatformOfficialWhatsAppService,
)
from app.workers import platform_official_whatsapp_tasks
from app.workers.celery_app import celery_app


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def _seed_prepared_delivery(db_session, *, suffix: str):
    service = PlatformOfficialWhatsAppService(db_session)
    connection = PlatformOfficialWhatsAppConnection(
        role=PlatformOfficialWhatsAppRole.outbound,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_active=True,
        phone_number_id="phone-worker",
        access_token_encrypted=service.cipher.encrypt("worker-token"),
        status=WaConnectionStatus.connected,
        webhook_registered=True,
    )
    db_session.add(connection)
    await db_session.commit()
    delivery = await service._prepare_text_delivery(
        connection=connection,
        role=PlatformOfficialWhatsAppRole.outbound,
        to="+5511999990006",
        body=f"Worker {suffix}",
        idempotency_key=f"official-worker-{suffix}",
        requested_by_user_id=None,
        source_type="worker_test",
        source_id=suffix,
    )
    return delivery


@pytest.mark.asyncio
async def test_worker_dispatches_only_committed_prepared_delivery(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(platform_service_module, "external_delivery_allowed", lambda: True)
    calls = 0

    async def fake_send(self, **kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["to"] == "5511999990006"
        return {"messages": [{"id": "wamid.worker.once"}]}

    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.send_text_message",
        fake_send,
    )
    delivery = await _seed_prepared_delivery(db_session, suffix="dispatch")
    delivery_id = delivery.id

    result = await platform_official_whatsapp_tasks._async_recover_official_whatsapp_deliveries(
        session_factory=_session_factory(db_session),
    )

    assert result == {"fenced": 0, "sent": 1}
    assert calls == 1
    db_session.expire_all()
    persisted = await db_session.get(type(delivery), delivery_id)
    assert persisted is not None
    assert persisted.state is PlatformOfficialWhatsAppDeliveryState.sent
    assert persisted.external_message_id == "wamid.worker.once"
    assert persisted.payload_ciphertext is None


@pytest.mark.asyncio
async def test_worker_fences_stale_started_delivery_without_provider_retry(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(platform_service_module, "external_delivery_allowed", lambda: True)

    async def forbidden_send(self, **_kwargs):
        raise AssertionError("stale provider call must never be replayed")

    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.send_text_message",
        forbidden_send,
    )
    delivery = await _seed_prepared_delivery(db_session, suffix="stale")
    delivery_id = delivery.id
    delivery.state = PlatformOfficialWhatsAppDeliveryState.provider_call_started
    delivery.provider_call_started_at = datetime.now(UTC) - timedelta(minutes=10)
    delivery.attempt_count = 1
    await db_session.commit()

    result = await platform_official_whatsapp_tasks._async_recover_official_whatsapp_deliveries(
        session_factory=_session_factory(db_session),
    )

    assert result == {"fenced": 1, "sent": 0}
    db_session.expire_all()
    persisted = await db_session.get(type(delivery), delivery_id)
    assert persisted is not None
    assert persisted.state is PlatformOfficialWhatsAppDeliveryState.delivery_uncertain
    assert persisted.attempt_count == 1
    assert persisted.payload_ciphertext is None
    assert persisted.last_error_code == "PLATFORM-WA-DISPATCH-LEASE-EXPIRED"


def test_worker_is_registered_in_periodic_recovery_schedule():
    schedule = celery_app.conf.beat_schedule[
        "recover-platform-official-whatsapp-every-minute"
    ]
    assert schedule["task"] == "platform_official_whatsapp_tasks.recover"
