from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.v1.endpoints import whatsapp_campaigns as campaign_endpoints
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WhatsAppConnection,
    WhatsAppContact,
)
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignSendJobStatus,
    CampaignStatus,
    MetaTemplateStatus,
    WhatsAppCampaign,
    WhatsAppCampaignAudience,
    WhatsAppCampaignDeliveryEvent,
    WhatsAppCampaignRecipient,
    WhatsAppCampaignSendJob,
    WhatsAppCampaignTemplateSnapshot,
)
from app.schemas.whatsapp_campaign import CampaignLaunchRequest
from app.services.entitlement_service import (
    CurrentStockLimitExceededError,
    EntitlementError,
)
from app.services.whatsapp import campaign_service as campaign_service_module
from app.services.whatsapp.campaign_service import WhatsAppCampaignService
from app.services.whatsapp.webhook_service import WhatsAppWebhookService


async def _set_contact_limit(db_session, active_subscription, limit: int) -> None:
    entitlement = await db_session.scalar(
        select(PlanEntitlement).where(
            PlanEntitlement.plan_id == active_subscription.plan_id,
            PlanEntitlement.entitlement == EntitlementKey.contacts,
        )
    )
    assert entitlement is not None
    entitlement.limit_quantity = limit
    await db_session.flush()


async def _connection(db_session, workspace_id) -> WhatsAppConnection:
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id=f"contact-quota-{uuid4().hex}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    return connection


def _webhook_service(db_session) -> WhatsAppWebhookService:
    return WhatsAppWebhookService(
        db_session,
        connection_service=None,  # type: ignore[arg-type]
        message_service=None,  # type: ignore[arg-type]
        automation_rule_service=None,  # type: ignore[arg-type]
        campaign_service=None,  # type: ignore[arg-type]
    )


def _campaign_service(db_session) -> WhatsAppCampaignService:
    return WhatsAppCampaignService(
        db_session,
        connection_service=None,  # type: ignore[arg-type]
        meta_client=None,  # type: ignore[arg-type]
    )


class _ConnectionService:
    def __init__(self, connection: WhatsAppConnection) -> None:
        self.connection = connection

    async def get_connection(self, connection_id, workspace_id):
        assert connection_id == self.connection.id
        assert workspace_id == self.connection.workspace_id
        return self.connection

    async def get_default_connection(self, workspace_id, *, connected_only=False):
        assert workspace_id == self.connection.workspace_id
        return self.connection

    async def resolve_meta_access_token(self, connection):
        assert connection.id == self.connection.id
        return "local-test-token"


class _RecordingMetaClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send_template_message(self, **kwargs):
        self.calls.append(kwargs["to"])
        return {"messages": [{"id": f"wamid-{len(self.calls)}"}]}


def _operational_campaign_service(
    db_session,
    connection: WhatsAppConnection,
    meta_client: _RecordingMetaClient,
) -> WhatsAppCampaignService:
    return WhatsAppCampaignService(
        db_session,
        connection_service=_ConnectionService(connection),  # type: ignore[arg-type]
        meta_client=meta_client,  # type: ignore[arg-type]
    )


async def _campaign(
    db_session,
    *,
    workspace_id,
    connection: WhatsAppConnection,
    status: CampaignStatus,
) -> WhatsAppCampaign:
    campaign = WhatsAppCampaign(
        workspace_id=workspace_id,
        whatsapp_connection_id=connection.id,
        title=f"Quota de contatos {uuid4().hex[:8]}",
        status=status,
    )
    db_session.add(campaign)
    await db_session.flush()
    db_session.add_all(
        [
            WhatsAppCampaignAudience(campaign_id=campaign.id, filters={}),
            WhatsAppCampaignTemplateSnapshot(
                campaign_id=campaign.id,
                template_name="contact_stock",
                language="pt_BR",
                status=MetaTemplateStatus.approved,
                header_parameters=[],
                body_parameters=[],
                components=[],
                raw_payload={},
            ),
        ]
    )
    await db_session.flush()
    return campaign


async def _pending_recipient(
    db_session,
    *,
    campaign: WhatsAppCampaign,
    connection: WhatsAppConnection,
    phone: str,
) -> tuple[WhatsAppCampaignRecipient, WhatsAppCampaignSendJob]:
    recipient = WhatsAppCampaignRecipient(
        campaign_id=campaign.id,
        workspace_id=campaign.workspace_id,
        phone=phone,
        name="Contato bloqueado",
        status=CampaignRecipientStatus.queued,
        send_context={},
    )
    db_session.add(recipient)
    await db_session.flush()
    job = WhatsAppCampaignSendJob(
        campaign_id=campaign.id,
        recipient_id=recipient.id,
        workspace_id=campaign.workspace_id,
        whatsapp_connection_id=connection.id,
        status=CampaignSendJobStatus.pending,
    )
    db_session.add(job)
    await db_session.commit()
    return recipient, job


def _missing_contact_preview(*phones: str) -> dict:
    return {
        "total": len(phones),
        "eligible": len(phones),
        "blocked": 0,
        "sample": [],
        "eligible_rows": [
            {
                "contact": None,
                "matter": None,
                "contact_id": None,
                "matter_id": None,
                "phone": phone,
                "name": f"Contato {index}",
                "context": {},
            }
            for index, phone in enumerate(phones, start=1)
        ],
    }


@pytest.mark.asyncio
async def test_webhook_sync_blocks_only_new_contacts_after_limit_or_downgrade(
    db_session,
    auth_context,
    active_subscription,
) -> None:
    await _set_contact_limit(db_session, active_subscription, 1)
    connection = await _connection(db_session, auth_context.workspace_id)
    service = _webhook_service(db_session)

    existing = await service._get_or_create_contact(
        "5561999990001",
        auth_context.workspace_id,
        connection.id,
        "Contato existente",
    )
    assert (
        await service._get_or_create_contact(
            "5561999990001",
            auth_context.workspace_id,
            connection.id,
            "Contato existente",
        )
    ).id == existing.id

    with pytest.raises(CurrentStockLimitExceededError):
        await service._get_or_create_contact(
            "5561999990002",
            auth_context.workspace_id,
            connection.id,
            "Contato excedente",
        )

    await _set_contact_limit(db_session, active_subscription, 0)
    assert (
        await service._get_or_create_contact(
            "5561999990001",
            auth_context.workspace_id,
            connection.id,
            "Contato existente",
        )
    ).id == existing.id
    assert (
        await db_session.scalar(
            select(func.count(WhatsAppContact.id)).where(
                WhatsAppContact.workspace_id == auth_context.workspace_id
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_campaign_import_projection_uses_same_contact_stock_gate(
    db_session,
    auth_context,
    active_subscription,
) -> None:
    await _set_contact_limit(db_session, active_subscription, 1)
    connection = await _connection(db_session, auth_context.workspace_id)
    service = _campaign_service(db_session)

    first_recipient = WhatsAppCampaignRecipient(
        campaign_id=uuid4(),
        workspace_id=auth_context.workspace_id,
        phone="5561999990101",
        name="Primeiro importado",
    )
    first = await service._ensure_campaign_contact(
        workspace_id=auth_context.workspace_id,
        connection=connection,
        recipient=first_recipient,
    )
    retry_recipient = WhatsAppCampaignRecipient(
        campaign_id=uuid4(),
        workspace_id=auth_context.workspace_id,
        phone=first.phone,
        name="Mesmo importado",
    )
    assert (
        await service._ensure_campaign_contact(
            workspace_id=auth_context.workspace_id,
            connection=connection,
            recipient=retry_recipient,
        )
    ).id == first.id

    excess_recipient = WhatsAppCampaignRecipient(
        campaign_id=uuid4(),
        workspace_id=auth_context.workspace_id,
        phone="5561999990102",
        name="Importado excedente",
    )
    with pytest.raises(CurrentStockLimitExceededError):
        await service._ensure_campaign_contact(
            workspace_id=auth_context.workspace_id,
            connection=connection,
            recipient=excess_recipient,
        )


@pytest.mark.asyncio
async def test_campaign_launch_preflight_blocks_full_contact_stock_before_running_or_meta(
    db_session,
    auth_context,
    active_subscription,
    monkeypatch,
) -> None:
    await _set_contact_limit(db_session, active_subscription, 1)
    connection = await _connection(db_session, auth_context.workspace_id)
    campaign = await _campaign(
        db_session,
        workspace_id=auth_context.workspace_id,
        connection=connection,
        status=CampaignStatus.draft,
    )
    await db_session.commit()
    meta_client = _RecordingMetaClient()
    service = _operational_campaign_service(db_session, connection, meta_client)
    preview = _missing_contact_preview("5561999990201", "5561999990202")

    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)

    async def build_preview(*_args, **_kwargs):
        return preview

    async def reserve_launch_quota(**_kwargs):
        return date(2026, 7, 1), datetime.now(UTC)

    monkeypatch.setattr(service, "_build_audience_preview", build_preview)
    monkeypatch.setattr(service, "_reserve_launch_quota", reserve_launch_quota)

    with pytest.raises(CurrentStockLimitExceededError):
        await service.launch_campaign(
            auth_context.workspace_id,
            campaign.id,
            CampaignLaunchRequest(dry_run=False),
        )

    await db_session.refresh(campaign)
    assert campaign.status == CampaignStatus.draft
    assert campaign.launched_at is None
    assert campaign.total_recipients == 0
    assert meta_client.calls == []
    assert (
        await db_session.scalar(
            select(func.count(WhatsAppContact.id)).where(
                WhatsAppContact.workspace_id == auth_context.workspace_id
            )
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count(WhatsAppCampaignRecipient.id)).where(
                WhatsAppCampaignRecipient.campaign_id == campaign.id
            )
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count(WhatsAppCampaignSendJob.id)).where(
                WhatsAppCampaignSendJob.campaign_id == campaign.id
            )
        )
        == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_phone", "preview_phone"),
    [
        ("556182502136", "5561982502136"),
        ("5561982502136", "556182502136"),
    ],
)
async def test_campaign_launch_reuses_brazilian_phone_alias_and_replay_is_idempotent(
    db_session,
    auth_context,
    active_subscription,
    monkeypatch,
    stored_phone: str,
    preview_phone: str,
) -> None:
    await _set_contact_limit(db_session, active_subscription, 10)
    connection = await _connection(db_session, auth_context.workspace_id)
    existing = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone=stored_phone,
        name="Contato existente",
    )
    db_session.add(existing)
    campaign = await _campaign(
        db_session,
        workspace_id=auth_context.workspace_id,
        connection=connection,
        status=CampaignStatus.draft,
    )
    campaign.scheduled_at = datetime.now(UTC) + timedelta(hours=1)
    await db_session.commit()

    service = _operational_campaign_service(db_session, connection, _RecordingMetaClient())
    preview = _missing_contact_preview(preview_phone)
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)

    async def build_preview(*_args, **_kwargs):
        return preview

    async def reserve_launch_quota(**_kwargs):
        return date(2026, 8, 1), datetime.now(UTC)

    monkeypatch.setattr(service, "_build_audience_preview", build_preview)
    monkeypatch.setattr(service, "_reserve_launch_quota", reserve_launch_quota)

    launched = await service.launch_campaign(
        auth_context.workspace_id,
        campaign.id,
        CampaignLaunchRequest(dry_run=False),
    )
    replayed = await service.launch_campaign(
        auth_context.workspace_id,
        campaign.id,
        CampaignLaunchRequest(dry_run=False),
    )

    assert launched.status == CampaignStatus.scheduled
    assert replayed.status == CampaignStatus.scheduled
    contacts = list(
        (
            await db_session.scalars(
                select(WhatsAppContact).where(
                    WhatsAppContact.workspace_id == auth_context.workspace_id,
                    WhatsAppContact.whatsapp_connection_id == connection.id,
                )
            )
        ).all()
    )
    assert [contact.id for contact in contacts] == [existing.id]
    recipient = await db_session.scalar(
        select(WhatsAppCampaignRecipient).where(
            WhatsAppCampaignRecipient.campaign_id == campaign.id
        )
    )
    assert recipient is not None
    assert recipient.contact_id == existing.id
    assert recipient.phone == "5561982502136"
    assert (
        await db_session.scalar(
            select(func.count(WhatsAppCampaignRecipient.id)).where(
                WhatsAppCampaignRecipient.campaign_id == campaign.id
            )
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count(WhatsAppCampaignSendJob.id)).where(
                WhatsAppCampaignSendJob.campaign_id == campaign.id
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_contact_capacity_failure_is_terminal_without_attempt_or_meta_retry(
    db_session,
    auth_context,
    active_subscription,
    monkeypatch,
) -> None:
    await _set_contact_limit(db_session, active_subscription, 0)
    connection = await _connection(db_session, auth_context.workspace_id)
    campaign = await _campaign(
        db_session,
        workspace_id=auth_context.workspace_id,
        connection=connection,
        status=CampaignStatus.running,
    )
    recipient, job = await _pending_recipient(
        db_session,
        campaign=campaign,
        connection=connection,
        phone="5561999990301",
    )
    meta_client = _RecordingMetaClient()
    service = _operational_campaign_service(db_session, connection, meta_client)
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)

    async def reserve_worker_quota(*, jobs, **_kwargs):
        return jobs, [], {}

    monkeypatch.setattr(service, "_reserve_worker_quota", reserve_worker_quota)

    assert (
        await service._process_ready_jobs(
            campaign.id,
            connection,
            workspace_id=auth_context.workspace_id,
        )
        == 1
    )
    await db_session.refresh(recipient)
    await db_session.refresh(job)
    assert recipient.status == CampaignRecipientStatus.failed
    assert job.status == CampaignSendJobStatus.failed
    assert job.attempt_count == 0
    assert job.scheduled_for is None
    assert job.processed_at is not None
    assert "contact_capacity_exceeded" in (job.last_error or "")
    assert meta_client.calls == []
    assert (
        await service._process_ready_jobs(
            campaign.id,
            connection,
            workspace_id=auth_context.workspace_id,
        )
        == 0
    )
    assert meta_client.calls == []

    event = await db_session.scalar(
        select(WhatsAppCampaignDeliveryEvent).where(
            WhatsAppCampaignDeliveryEvent.recipient_id == recipient.id,
            WhatsAppCampaignDeliveryEvent.status == CampaignRecipientStatus.failed,
        )
    )
    assert event is not None
    assert event.payload["event"] == "contact_capacity_exceeded"
    assert event.payload["providerAttempted"] is False
    assert event.payload["retryable"] is False


@pytest.mark.asyncio
async def test_contact_flush_failure_rolls_back_only_one_job_and_records_clean_retry(
    db_session,
    auth_context,
    active_subscription,
    monkeypatch,
) -> None:
    await _set_contact_limit(db_session, active_subscription, 10)
    connection = await _connection(db_session, auth_context.workspace_id)
    campaign = await _campaign(
        db_session,
        workspace_id=auth_context.workspace_id,
        connection=connection,
        status=CampaignStatus.running,
    )
    failed_recipient, failed_job = await _pending_recipient(
        db_session,
        campaign=campaign,
        connection=connection,
        phone="5561999990351",
    )
    sent_recipient, sent_job = await _pending_recipient(
        db_session,
        campaign=campaign,
        connection=connection,
        phone="5561999990352",
    )
    db_session.add(
        WhatsAppContact(
            workspace_id=auth_context.workspace_id,
            whatsapp_connection_id=connection.id,
            phone=failed_recipient.phone,
            name="Contato que provoca conflito controlado",
        )
    )
    await db_session.commit()

    meta_client = _RecordingMetaClient()
    service = _operational_campaign_service(db_session, connection, meta_client)
    original_ensure = service._ensure_campaign_contact
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)

    async def reserve_worker_quota(*, jobs, **_kwargs):
        return jobs, [], {}

    async def force_one_flush_failure(*, workspace_id, connection, recipient):
        if recipient.id == failed_recipient.id:
            db_session.add(
                WhatsAppContact(
                    workspace_id=workspace_id,
                    whatsapp_connection_id=connection.id,
                    phone=recipient.phone,
                    name="Duplicado concorrente",
                )
            )
            await db_session.flush()
        return await original_ensure(
            workspace_id=workspace_id,
            connection=connection,
            recipient=recipient,
        )

    async def skip_message_projection(**_kwargs):
        return None

    monkeypatch.setattr(service, "_reserve_worker_quota", reserve_worker_quota)
    monkeypatch.setattr(service, "_ensure_campaign_contact", force_one_flush_failure)
    monkeypatch.setattr(service, "_persist_campaign_message", skip_message_projection)

    assert (
        await service._process_ready_jobs(
            campaign.id,
            connection,
            workspace_id=auth_context.workspace_id,
        )
        == 2
    )
    await db_session.refresh(failed_recipient)
    await db_session.refresh(failed_job)
    await db_session.refresh(sent_recipient)
    await db_session.refresh(sent_job)

    assert failed_job.status == CampaignSendJobStatus.pending
    assert failed_recipient.status == CampaignRecipientStatus.queued
    assert failed_job.attempt_count == 0
    assert "campaign_contact_materialization_failed" in (failed_job.last_error or "")
    assert failed_job.scheduled_for is not None
    assert sent_job.status == CampaignSendJobStatus.sent
    assert sent_recipient.status == CampaignRecipientStatus.sent
    assert meta_client.calls == [sent_recipient.phone]


@pytest.mark.asyncio
async def test_generic_contact_entitlement_error_never_enters_transient_retry(
    db_session,
    auth_context,
    active_subscription,
    monkeypatch,
) -> None:
    connection = await _connection(db_session, auth_context.workspace_id)
    campaign = await _campaign(
        db_session,
        workspace_id=auth_context.workspace_id,
        connection=connection,
        status=CampaignStatus.running,
    )
    recipient, job = await _pending_recipient(
        db_session,
        campaign=campaign,
        connection=connection,
        phone="5561999990401",
    )
    meta_client = _RecordingMetaClient()
    service = _operational_campaign_service(db_session, connection, meta_client)
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)

    async def reserve_worker_quota(*, jobs, **_kwargs):
        return jobs, [], {}

    async def deny_contact(**_kwargs):
        raise EntitlementError("política inválida")

    monkeypatch.setattr(service, "_reserve_worker_quota", reserve_worker_quota)
    monkeypatch.setattr(service, "_ensure_campaign_contact", deny_contact)

    assert (
        await service._process_ready_jobs(
            campaign.id,
            connection,
            workspace_id=auth_context.workspace_id,
        )
        == 1
    )
    await db_session.refresh(recipient)
    await db_session.refresh(job)
    assert recipient.status == CampaignRecipientStatus.failed
    assert job.status == CampaignSendJobStatus.failed
    assert job.attempt_count == 0
    assert job.scheduled_for is None
    assert meta_client.calls == []


@pytest.mark.asyncio
async def test_campaign_endpoint_exposes_contact_capacity_as_structured_conflict(
    auth_context,
    monkeypatch,
) -> None:
    monkeypatch.setattr(campaign_endpoints, "external_delivery_allowed", lambda: True)

    class _DeniedLaunchService:
        async def launch_campaign(self, workspace_id, campaign_id, payload, **_kwargs):
            raise CurrentStockLimitExceededError(
                entitlement=EntitlementKey.contacts,
                limit_quantity=1,
                current_quantity=1,
                requested_quantity=1,
            )

    with pytest.raises(HTTPException) as blocked:
        await campaign_endpoints.launch_campaign(
            campaign_id=uuid4(),
            payload=CampaignLaunchRequest(dry_run=False),
            svc=_DeniedLaunchService(),  # type: ignore[arg-type]
            workspace_id=auth_context.workspace_id,
            _=object(),
            can_view_sensitive=True,
        )

    assert blocked.value.status_code == 409
    assert blocked.value.detail == {
        "code": "ENT-003",
        "message": "O limite corrente deste recurso foi atingido.",
        "entitlement": "contacts",
        "usage": 1,
        "limit": 1,
        "remaining": 0,
        "limitQuantity": 1,
        "currentQuantity": 1,
        "requestedQuantity": 1,
    }
