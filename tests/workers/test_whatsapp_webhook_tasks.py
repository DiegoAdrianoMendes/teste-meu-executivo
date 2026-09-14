"""Durable worker contract for signed WhatsApp webhook envelopes.

Cobre:
- claim/lease before domain processing and idempotent replay after success;
- recovery of an orphan lease;
- persisted retryable and terminal failures with sanitized errors;
- an absent envelope remains a retryable infrastructure error.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.klm_operations import KlmAuditEvent
from app.models.omnichannel_v6 import (
    OmnichannelProviderWebhookEnvelope,
    OmnichannelProviderWebhookProcessingState,
)
from app.models.platform_inbox import (
    PlatformInboxConversation,
    PlatformInboxMessage,
    PlatformInboxQueue,
)
from app.models.platform_official_whatsapp import (
    PlatformOfficialWhatsAppConnection,
    PlatformOfficialWhatsAppRole,
)
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WaMessageDirection,
    WaMessageStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignStatus,
    WhatsAppCampaign,
    WhatsAppCampaignDeliveryEvent,
    WhatsAppCampaignRecipient,
    WhatsAppSuppressionEntry,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.message_evidence_service import (
    DEFAULT_PROVIDER_WEBHOOK_MAX_ATTEMPTS,
    MessageEvidenceService,
    ProviderWebhookProcessingLeaseLostError,
)
from app.workers import whatsapp_webhook_tasks


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)


async def _seed_envelope(
    db_session: AsyncSession,
    *,
    payload: dict,
) -> OmnichannelProviderWebhookEnvelope:
    import json

    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    context = await MessageEvidenceService(db_session).capture_provider_envelope(
        provider="meta_whatsapp",
        raw_payload=raw,
        signature_header="sha256=stub",
        signature_scheme="x-hub-signature-256",
        signature_verified=True,
        request_metadata={},
    )
    await db_session.commit()
    envelope = await db_session.get(OmnichannelProviderWebhookEnvelope, context.id)
    assert envelope is not None
    await db_session.refresh(envelope)
    return envelope


def _text_webhook_payload(*, phone_number_id: str, wamid: str, from_phone: str, body: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            "contacts": [{"wa_id": from_phone, "profile": {"name": "Cliente"}}],
                            "messages": [
                                {
                                    "id": wamid,
                                    "from": from_phone,
                                    "timestamp": "1712345678",
                                    "type": "text",
                                    "text": {"body": body},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _failed_status_webhook_payload(*, phone_number_id: str, wamid: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-status",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            "statuses": [
                                {
                                    "id": wamid,
                                    "status": "failed",
                                    "timestamp": "1712345678",
                                    "errors": [
                                        {
                                            "code": 131050,
                                            "title": "User opted out",
                                            "message": "opt-out",
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_process_inbound_webhook_task_ingests_message_from_persisted_envelope(
    db_session: AsyncSession,
):
    workspace = Workspace(
        name="Webhook Task",
        slug=f"webhook-task-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    connection = WhatsAppConnection(
        workspace_id=workspace.id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn-webhook-task",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.commit()

    wamid = "wamid-task-ingest-1"
    payload = _text_webhook_payload(
        phone_number_id="pn-webhook-task",
        wamid=wamid,
        from_phone="5511966665555",
        body="Preciso de ajuda com meu processo",
    )
    envelope = await _seed_envelope(db_session, payload=payload)

    factory = _session_factory(db_session)
    outcome = await whatsapp_webhook_tasks._async_process_inbound_webhook(
        envelope.id, session_factory=factory
    )

    message = await db_session.scalar(
        select(WhatsAppMessage).where(WhatsAppMessage.wamid == wamid)
    )
    assert message is not None
    assert message.direction == WaMessageDirection.inbound
    assert message.body == "Preciso de ajuda com meu processo"
    assert outcome.status == "processed"
    state = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        envelope.id,
        populate_existing=True,
    )
    assert state is not None
    assert state.status == "processed"
    assert state.attempts == 1
    assert state.processed_at is not None
    assert state.lease_token is None
    assert state.lease_expires_at is None


@pytest.mark.asyncio
async def test_process_inbound_webhook_task_is_idempotent_on_duplicate_envelope_replay(
    db_session: AsyncSession,
):
    workspace = Workspace(
        name="Webhook Task Dup",
        slug=f"webhook-task-dup-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    connection = WhatsAppConnection(
        workspace_id=workspace.id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn-webhook-task-dup",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.commit()

    wamid = "wamid-task-ingest-dup"
    payload = _text_webhook_payload(
        phone_number_id="pn-webhook-task-dup",
        wamid=wamid,
        from_phone="5511966665556",
        body="Mensagem repetida pela Meta",
    )
    envelope = await _seed_envelope(db_session, payload=payload)
    factory = _session_factory(db_session)

    # Simula reentrega/retry da task com o MESMO envelope: nenhum novo efeito.
    first = await whatsapp_webhook_tasks._async_process_inbound_webhook(
        envelope.id, session_factory=factory
    )
    replay = await whatsapp_webhook_tasks._async_process_inbound_webhook(
        envelope.id, session_factory=factory
    )

    messages = (
        await db_session.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.wamid == wamid)
        )
    ).scalars().all()
    assert len(messages) == 1
    assert first.status == "processed"
    assert replay.status == "processed"
    state = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        envelope.id,
        populate_existing=True,
    )
    assert state is not None
    assert state.attempts == 1


@pytest.mark.asyncio
async def test_platform_inbox_effect_is_idempotent_across_post_commit_lease_loss(
    db_session: AsyncSession,
    monkeypatch,
):
    db_session.add(
        PlatformOfficialWhatsAppConnection(
            role=PlatformOfficialWhatsAppRole.support,
            connection_type=ConnectionType.OFFICIAL_MANUAL,
            phone_number_id="pn-platform-crash-window",
            is_active=True,
            status=WaConnectionStatus.connected,
        )
    )
    await db_session.commit()
    wamid = "wamid-platform-crash-window"
    envelope = await _seed_envelope(
        db_session,
        payload=_text_webhook_payload(
            phone_number_id="pn-platform-crash-window",
            wamid=wamid,
            from_phone="5511966665510",
            body="Uma única mensagem apesar do crash",
        ),
    )
    original_mark_processed = MessageEvidenceService.mark_provider_webhook_processed

    async def lose_lease_after_domain_commit(self, claim, **_kwargs):
        raise ProviderWebhookProcessingLeaseLostError("simulated crash window")

    monkeypatch.setattr(
        MessageEvidenceService,
        "mark_provider_webhook_processed",
        lose_lease_after_domain_commit,
    )
    first = await whatsapp_webhook_tasks._async_process_inbound_webhook(
        envelope.id,
        session_factory=_session_factory(db_session),
    )
    assert first.status == "processing"
    assert first.should_retry is True
    assert await db_session.scalar(
        select(func.count())
        .select_from(PlatformInboxMessage)
        .where(PlatformInboxMessage.external_message_id == wamid)
    ) == 1

    state = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        envelope.id,
        populate_existing=True,
    )
    assert state is not None
    state.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    monkeypatch.setattr(
        MessageEvidenceService,
        "mark_provider_webhook_processed",
        original_mark_processed,
    )

    second = await whatsapp_webhook_tasks._async_process_inbound_webhook(
        envelope.id,
        session_factory=_session_factory(db_session),
    )
    assert second.status == "processed"
    assert await db_session.scalar(
        select(func.count())
        .select_from(PlatformInboxMessage)
        .where(PlatformInboxMessage.external_message_id == wamid)
    ) == 1
    conversation = await db_session.scalar(
        select(PlatformInboxConversation).where(
            PlatformInboxConversation.external_thread_id
            == "pn-platform-crash-window:5511966665510"
        )
    )
    assert conversation is not None
    assert conversation.queue == PlatformInboxQueue.support
    assert conversation.unread_count == 1
    refreshed = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        envelope.id,
        populate_existing=True,
    )
    assert refreshed is not None
    assert refreshed.status == "processed"
    assert refreshed.attempts == 2


@pytest.mark.asyncio
async def test_status_effects_are_idempotent_across_post_commit_lease_loss(
    db_session: AsyncSession,
    monkeypatch,
):
    workspace = Workspace(
        name="Webhook status crash window",
        slug=f"webhook-status-crash-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    connection = WhatsAppConnection(
        workspace_id=workspace.id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn-status-crash-window",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        phone="5511966665599",
        name="Cliente status crash",
    )
    db_session.add(contact)
    await db_session.flush()
    wamid = "wamid-status-crash-window"
    db_session.add(
        WhatsAppMessage(
            contact_id=contact.id,
            whatsapp_connection_id=connection.id,
            wamid=wamid,
            direction=WaMessageDirection.outbound,
            status=WaMessageStatus.sent,
            body="Mensagem de campanha",
            timestamp=datetime.now(UTC),
        )
    )
    campaign = WhatsAppCampaign(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        title="Campanha status crash",
        status=CampaignStatus.running,
    )
    db_session.add(campaign)
    await db_session.flush()
    db_session.add(
        WhatsAppCampaignRecipient(
            workspace_id=workspace.id,
            campaign_id=campaign.id,
            phone=contact.phone,
            contact_id=contact.id,
            wamid=wamid,
            status=CampaignRecipientStatus.sent,
            send_context={},
        )
    )
    await db_session.commit()
    envelope = await _seed_envelope(
        db_session,
        payload=_failed_status_webhook_payload(
            phone_number_id=connection.phone_number_id,
            wamid=wamid,
        ),
    )
    original_mark_processed = MessageEvidenceService.mark_provider_webhook_processed

    async def lose_lease_after_domain_commit(self, claim, **_kwargs):
        raise ProviderWebhookProcessingLeaseLostError("simulated status crash window")

    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        MessageEvidenceService,
        "mark_provider_webhook_processed",
        lose_lease_after_domain_commit,
    )
    first = await whatsapp_webhook_tasks._async_process_inbound_webhook(
        envelope.id,
        session_factory=_session_factory(db_session),
    )
    assert first.status == "processing"
    assert first.should_retry is True

    state = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        envelope.id,
        populate_existing=True,
    )
    assert state is not None
    state.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    monkeypatch.setattr(
        MessageEvidenceService,
        "mark_provider_webhook_processed",
        original_mark_processed,
    )

    second = await whatsapp_webhook_tasks._async_process_inbound_webhook(
        envelope.id,
        session_factory=_session_factory(db_session),
    )
    assert second.status == "processed"
    assert await db_session.scalar(
        select(func.count())
        .select_from(WhatsAppCampaignDeliveryEvent)
        .where(WhatsAppCampaignDeliveryEvent.campaign_id == campaign.id)
    ) == 1
    assert await db_session.scalar(
        select(func.count())
        .select_from(WhatsAppSuppressionEntry)
        .where(
            WhatsAppSuppressionEntry.workspace_id == workspace.id,
            WhatsAppSuppressionEntry.phone == contact.phone,
        )
    ) == 1
    assert await db_session.scalar(
        select(func.count())
        .select_from(KlmAuditEvent)
        .where(
            KlmAuditEvent.workspace_id == workspace.id,
            KlmAuditEvent.event_type == "whatsapp_suppression_created",
        )
    ) == 1
    message = await db_session.scalar(
        select(WhatsAppMessage).where(WhatsAppMessage.wamid == wamid)
    )
    assert message is not None
    assert len(message.message_metadata["providerStatusReceiptKeys"]) == 1
    refreshed = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        envelope.id,
        populate_existing=True,
    )
    assert refreshed is not None
    assert refreshed.status == "processed"
    assert refreshed.attempts == 2


@pytest.mark.asyncio
async def test_mixed_platform_and_tenant_batch_processes_both_routes(
    db_session: AsyncSession,
):
    workspace = Workspace(
        name="Webhook mixed routing",
        slug=f"webhook-mixed-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    db_session.add_all(
        (
            WhatsAppConnection(
                workspace_id=workspace.id,
                connection_type=ConnectionType.OFFICIAL_MANUAL,
                is_default=True,
                phone_number_id="pn-tenant-mixed",
                status=WaConnectionStatus.connected,
            ),
            PlatformOfficialWhatsAppConnection(
                role=PlatformOfficialWhatsAppRole.support,
                connection_type=ConnectionType.OFFICIAL_MANUAL,
                phone_number_id="pn-platform-mixed",
                is_active=True,
                status=WaConnectionStatus.connected,
            ),
        )
    )
    await db_session.commit()
    tenant_wamid = "wamid-tenant-mixed"
    platform_wamid = "wamid-platform-mixed"
    tenant_payload = _text_webhook_payload(
        phone_number_id="pn-tenant-mixed",
        wamid=tenant_wamid,
        from_phone="5511966665520",
        body="Mensagem do tenant no lote misto",
    )
    platform_payload = _text_webhook_payload(
        phone_number_id="pn-platform-mixed",
        wamid=platform_wamid,
        from_phone="5511966665521",
        body="Mensagem da plataforma no lote misto",
    )
    payload = {
        "object": "whatsapp_business_account",
        "entry": platform_payload["entry"] + tenant_payload["entry"],
    }
    envelope = await _seed_envelope(db_session, payload=payload)

    outcome = await whatsapp_webhook_tasks._async_process_inbound_webhook(
        envelope.id,
        session_factory=_session_factory(db_session),
    )

    assert outcome.status == "processed"
    assert await db_session.scalar(
        select(func.count())
        .select_from(PlatformInboxMessage)
        .where(PlatformInboxMessage.external_message_id == platform_wamid)
    ) == 1
    assert await db_session.scalar(
        select(func.count())
        .select_from(WhatsAppMessage)
        .where(WhatsAppMessage.wamid == tenant_wamid)
    ) == 1


@pytest.mark.asyncio
async def test_process_inbound_webhook_task_raises_on_missing_envelope(db_session: AsyncSession):
    factory = _session_factory(db_session)
    with pytest.raises(whatsapp_webhook_tasks.WebhookEnvelopeNotFoundError):
        await whatsapp_webhook_tasks._async_process_inbound_webhook(
            uuid4(), session_factory=factory
        )


@pytest.mark.asyncio
async def test_active_lease_defers_duplicate_without_running_domain_effect(
    db_session: AsyncSession,
    monkeypatch,
):
    envelope = await _seed_envelope(
        db_session,
        payload={"object": "whatsapp_business_account", "entry": []},
    )
    state = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        envelope.id,
    )
    assert state is not None
    state.status = "processing"
    state.attempts = 1
    state.lease_token = uuid4().hex
    state.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    state.processing_started_at = datetime.now(UTC)
    state.last_attempted_at = datetime.now(UTC)
    await db_session.commit()

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("an active lease must block duplicate processing")

    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService.process_webhook",
        forbidden,
    )
    outcome = await whatsapp_webhook_tasks._async_process_inbound_webhook(
        envelope.id,
        session_factory=_session_factory(db_session),
    )

    assert outcome.status == "processing"
    assert outcome.should_retry is True
    refreshed = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        envelope.id,
        populate_existing=True,
    )
    assert refreshed is not None
    assert refreshed.attempts == 1


@pytest.mark.asyncio
async def test_expired_orphan_lease_is_recovered_as_new_attempt(
    db_session: AsyncSession,
    monkeypatch,
):
    envelope = await _seed_envelope(
        db_session,
        payload={"object": "whatsapp_business_account", "entry": []},
    )
    state = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        envelope.id,
    )
    assert state is not None
    state.status = "processing"
    state.attempts = 1
    state.lease_token = uuid4().hex
    state.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    state.processing_started_at = datetime.now(UTC) - timedelta(minutes=3)
    state.last_attempted_at = state.processing_started_at
    await db_session.commit()

    calls = 0

    async def processed(*_args, **_kwargs):
        nonlocal calls
        calls += 1

    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService.process_webhook",
        processed,
    )
    outcome = await whatsapp_webhook_tasks._async_process_inbound_webhook(
        envelope.id,
        session_factory=_session_factory(db_session),
    )

    assert outcome.status == "processed"
    assert calls == 1
    refreshed = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        envelope.id,
        populate_existing=True,
    )
    assert refreshed is not None
    assert refreshed.status == "processed"
    assert refreshed.attempts == 2


@pytest.mark.asyncio
async def test_transient_failure_is_persisted_then_retry_succeeds(
    db_session: AsyncSession,
    monkeypatch,
):
    envelope = await _seed_envelope(
        db_session,
        payload={"object": "whatsapp_business_account", "entry": []},
    )
    calls = 0

    async def flaky(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("authorization=must-not-be-persisted")

    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService.process_webhook",
        flaky,
    )
    factory = _session_factory(db_session)
    first = await whatsapp_webhook_tasks._async_process_inbound_webhook(
        envelope.id,
        session_factory=factory,
    )

    assert first.status == "retryable_failed"
    assert first.should_retry is True
    state = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        envelope.id,
        populate_existing=True,
    )
    assert state is not None
    assert state.attempts == 1
    assert state.last_error_code == "ConnectionError"
    assert "must-not-be-persisted" not in (state.last_error_message or "")
    state.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    second = await whatsapp_webhook_tasks._async_process_inbound_webhook(
        envelope.id,
        session_factory=factory,
    )
    assert second.status == "processed"
    assert calls == 2
    refreshed = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        envelope.id,
        populate_existing=True,
    )
    assert refreshed is not None
    assert refreshed.status == "processed"
    assert refreshed.attempts == 2
    assert refreshed.last_error_code is None


@pytest.mark.asyncio
async def test_exhausted_failure_becomes_queryable_terminal_state(
    db_session: AsyncSession,
    monkeypatch,
):
    envelope = await _seed_envelope(
        db_session,
        payload={"object": "whatsapp_business_account", "entry": []},
    )
    state = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        envelope.id,
    )
    assert state is not None
    state.status = "retryable_failed"
    state.attempts = 8
    state.last_failed_at = datetime.now(UTC) - timedelta(minutes=1)
    state.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    state.last_error_code = "ConnectionError"
    state.last_error_message = "Sanitized retryable failure."
    await db_session.commit()

    async def always_fails(*_args, **_kwargs):
        raise RuntimeError("access_token=must-never-be-stored")

    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService.process_webhook",
        always_fails,
    )
    outcome = await whatsapp_webhook_tasks._async_process_inbound_webhook(
        envelope.id,
        session_factory=_session_factory(db_session),
    )

    assert outcome.status == "terminal_failed"
    assert outcome.should_retry is False
    terminal = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        envelope.id,
        populate_existing=True,
    )
    assert terminal is not None
    assert terminal.status == "terminal_failed"
    assert terminal.attempts == 9
    assert terminal.terminal_failed_at is not None
    assert terminal.next_retry_at is None
    assert terminal.lease_token is None
    assert terminal.last_error_code == "RuntimeError"
    assert "must-never-be-stored" not in (terminal.last_error_message or "")


@pytest.mark.asyncio
async def test_invalid_stored_json_is_terminal_without_retry(db_session: AsyncSession):
    context = await MessageEvidenceService(db_session).capture_provider_envelope(
        provider="meta_whatsapp",
        raw_payload=b"{",
        signature_header="sha256=stub",
        signature_scheme="x-hub-signature-256",
        signature_verified=True,
        request_metadata={},
    )
    await db_session.commit()

    outcome = await whatsapp_webhook_tasks._async_process_inbound_webhook(
        context.id,
        session_factory=_session_factory(db_session),
    )

    assert outcome.status == "terminal_failed"
    state = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        context.id,
        populate_existing=True,
    )
    assert state is not None
    assert state.attempts == 1
    assert state.last_error_code == "InvalidStoredWebhookJson"


@pytest.mark.asyncio
async def test_recovery_scanner_dispatches_queued_due_retry_and_expired_lease(
    db_session: AsyncSession,
):
    reference_now = datetime.now(UTC)
    queued = await _seed_envelope(
        db_session,
        payload={"object": "whatsapp_business_account", "entry": [], "sequence": 1},
    )
    due_retry = await _seed_envelope(
        db_session,
        payload={"object": "whatsapp_business_account", "entry": [], "sequence": 2},
    )
    future_retry = await _seed_envelope(
        db_session,
        payload={"object": "whatsapp_business_account", "entry": [], "sequence": 3},
    )
    expired_lease = await _seed_envelope(
        db_session,
        payload={"object": "whatsapp_business_account", "entry": [], "sequence": 4},
    )
    active_lease = await _seed_envelope(
        db_session,
        payload={"object": "whatsapp_business_account", "entry": [], "sequence": 5},
    )

    due_state = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        due_retry.id,
    )
    future_state = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        future_retry.id,
    )
    expired_state = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        expired_lease.id,
    )
    active_state = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        active_lease.id,
    )
    assert due_state and future_state and expired_state and active_state
    for state, next_retry_at in (
        (due_state, reference_now - timedelta(seconds=1)),
        (future_state, reference_now + timedelta(minutes=5)),
    ):
        state.status = "retryable_failed"
        state.attempts = 1
        state.last_failed_at = reference_now - timedelta(minutes=1)
        state.next_retry_at = next_retry_at
        state.last_error_code = "ConnectionError"
        state.last_error_message = "Sanitized retry."
    for state, lease_expires_at in (
        (expired_state, reference_now - timedelta(seconds=1)),
        (active_state, reference_now + timedelta(minutes=1)),
    ):
        state.status = "processing"
        state.attempts = 1
        state.lease_token = uuid4().hex
        state.lease_expires_at = lease_expires_at
        state.processing_started_at = reference_now - timedelta(minutes=1)
        state.last_attempted_at = state.processing_started_at
    await db_session.commit()

    dispatched: list[str] = []
    result = await whatsapp_webhook_tasks._async_recover_provider_webhooks(
        session_factory=_session_factory(db_session),
        publish=dispatched.append,
        now=reference_now,
    )

    assert set(dispatched) == {
        str(queued.id),
        str(due_retry.id),
        str(expired_lease.id),
    }
    assert result == {"attempted": 3, "dispatched": 3, "publishFailed": 0}


def test_webhook_recovery_schedule_and_attempt_limits_are_coherent():
    assert (
        whatsapp_webhook_tasks.WEBHOOK_TASK_MAX_RETRIES + 1
        == DEFAULT_PROVIDER_WEBHOOK_MAX_ATTEMPTS
    )
    schedule = whatsapp_webhook_tasks.celery_app.conf.beat_schedule[
        "recover-whatsapp-webhooks-every-minute"
    ]
    assert schedule["task"] == "whatsapp_webhook_tasks.recover"
    assert str(schedule["schedule"]) == "<crontab: * * * * * (m/h/dM/MY/d)>"
