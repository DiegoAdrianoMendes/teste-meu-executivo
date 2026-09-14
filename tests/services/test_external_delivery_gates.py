from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.services.email_service as email_service
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
    CampaignStatus,
    MetaTemplateStatus,
    WhatsAppCampaign,
    WhatsAppMetaTemplate,
)
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole
from app.schemas.public_api import (
    PublicApiCampaignCreateRequest,
    PublicApiTemplateSendRequest,
    WorkspacePublicApiTokenCreate,
)
from app.schemas.whatsapp import SendDocumentRequest, SendMessageRequest
from app.schemas.whatsapp_campaign import CampaignAudienceFilter, CampaignLaunchRequest
from app.services.public_api_service import PublicApiService
from app.services.whatsapp.message_service import WhatsAppMessageService
from app.services.whatsapp.webhook_service import WhatsAppWebhookService

pytestmark = pytest.mark.usefixtures("active_subscription")


class _ConnectionServiceStub:
    def __init__(self, connection: WhatsAppConnection) -> None:
        self.connection = connection
        self.resolve_calls = 0

    async def get_connection(self, connection_id, workspace_id):
        self.resolve_calls += 1
        return self.connection

    async def get_default_connection(self, workspace_id, connected_only=False):
        self.resolve_calls += 1
        return self.connection

    async def mark_connection_error(self, connection, code, message):
        raise AssertionError("connection state must not change while delivery is blocked")


class _StrategyFactoryStub:
    def __init__(self) -> None:
        self.build_calls = 0

    def build(self, connection):
        self.build_calls += 1
        raise AssertionError("provider strategy must not be built while delivery is blocked")


class _TimeoutStrategy:
    async def send_text(self, connection, phone, body):
        raise TimeoutError("provider outcome is uncertain")


class _TimeoutStrategyFactory:
    def build(self, connection):
        return _TimeoutStrategy()


async def _connection_and_contact(db_session, workspace_id):
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn-gate",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511999999999",
        name="Contato Gate",
    )
    db_session.add(contact)
    await db_session.flush()
    return connection, contact


@pytest.mark.asyncio
async def test_blocked_message_and_document_are_persisted_pending_without_provider(
    db_session,
    auth_context,
    monkeypatch,
):
    connection, contact = await _connection_and_contact(db_session, auth_context.workspace_id)
    connection_service = _ConnectionServiceStub(connection)
    strategy_factory = _StrategyFactoryStub()
    service = WhatsAppMessageService(
        db_session,
        connection_service=connection_service,
        strategy_factory=strategy_factory,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: False,
    )

    text_result = await service.send_message(
        SendMessageRequest(
            contact_id=contact.id,
            body="Mensagem auditavel",
            idempotency_key="test-blocked-text-delivery",
        ),
        auth_context.user_id,
        auth_context.workspace_id,
    )
    document_result = await service.send_document(
        SendDocumentRequest(
            contact_id=contact.id,
            url="https://local.invalid/minuta.pdf",
            filename="minuta.pdf",
            idempotency_key="test-blocked-document-delivery",
        ),
        auth_context.user_id,
        auth_context.workspace_id,
    )

    stored = list(
        (
            await db_session.scalars(
                select(WhatsAppMessage).where(WhatsAppMessage.contact_id == contact.id)
            )
        ).all()
    )
    await db_session.refresh(contact)

    assert text_result.status == WaMessageStatus.pending
    assert document_result.status == WaMessageStatus.pending
    assert {message.status for message in stored} == {WaMessageStatus.pending}
    assert all(message.wamid is None for message in stored)
    assert contact.whatsapp_connection_id == connection.id
    assert strategy_factory.build_calls == 0


@pytest.mark.asyncio
async def test_uncertain_provider_failure_requires_delivery_reconciliation(
    db_session,
    auth_context,
    monkeypatch,
):
    connection, contact = await _connection_and_contact(db_session, auth_context.workspace_id)
    service = WhatsAppMessageService(
        db_session,
        connection_service=_ConnectionServiceStub(connection),
        strategy_factory=_TimeoutStrategyFactory(),
    )
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )

    with pytest.raises(TimeoutError):
        await service.send_message(
            SendMessageRequest(
                contact_id=contact.id,
                body="Intent before timeout",
                idempotency_key="test-timeout-delivery-intent",
            ),
            auth_context.user_id,
            auth_context.workspace_id,
        )

    stored = await db_session.scalar(
        select(WhatsAppMessage).where(WhatsAppMessage.contact_id == contact.id)
    )
    assert stored is not None
    assert stored.status == WaMessageStatus.delivery_uncertain
    assert stored.wamid is None
    assert stored.outbound_request_started_at is not None
    assert stored.delivery_uncertain_at is not None
    assert stored.outbound_error["deliveryOutcomeKnown"] is False


@pytest.mark.asyncio
async def test_blocked_direct_send_does_not_even_resolve_a_connection(
    db_session,
    auth_context,
    monkeypatch,
):
    connection, _ = await _connection_and_contact(db_session, auth_context.workspace_id)
    connection_service = _ConnectionServiceStub(connection)
    strategy_factory = _StrategyFactoryStub()
    service = WhatsAppMessageService(
        db_session,
        connection_service=connection_service,
        strategy_factory=strategy_factory,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: False,
    )

    sent = await service.send_text_direct(
        phone="5511888888888",
        body="Resposta automatica",
        workspace_id=auth_context.workspace_id,
        idempotency_key="test-blocked-direct-send",
    )

    assert sent is False
    assert connection_service.resolve_calls == 0
    assert strategy_factory.build_calls == 0


@pytest.mark.asyncio
async def test_blocked_webhook_status_does_not_mark_message_sent_or_sync_campaign(
    db_session,
    auth_context,
    monkeypatch,
):
    connection, contact = await _connection_and_contact(db_session, auth_context.workspace_id)
    message = WhatsAppMessage(
        contact_id=contact.id,
        whatsapp_connection_id=connection.id,
        wamid="wamid-blocked-status",
        direction=WaMessageDirection.outbound,
        status=WaMessageStatus.pending,
        body="Intent local",
    )
    db_session.add(message)
    await db_session.commit()
    campaign_service = AsyncMock()
    service = WhatsAppWebhookService(
        db_session,
        connection_service=AsyncMock(),
        message_service=AsyncMock(),
        automation_rule_service=AsyncMock(),
        campaign_service=campaign_service,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed",
        lambda: False,
    )

    await service._handle_status_update(
        connection.id,
        {"id": message.wamid, "status": "sent"},
    )
    await db_session.refresh(message)

    assert message.status == WaMessageStatus.pending
    campaign_service.sync_delivery_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_template_send_stays_pending_when_external_delivery_is_blocked(
    db_session,
    auth_context,
    monkeypatch,
):
    connection, contact = await _connection_and_contact(db_session, auth_context.workspace_id)
    contact.whatsapp_connection_id = connection.id
    template = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        name="template_gate",
        language="pt_BR",
        status=MetaTemplateStatus.approved,
        components=[],
        raw_payload={},
    )
    db_session.add(template)
    await db_session.flush()

    service = PublicApiService(db_session)
    service.meta_client.send_template_message = AsyncMock(
        side_effect=AssertionError("Meta must not be called while delivery is blocked")
    )
    monkeypatch.setattr(
        "app.services.public_api_service.external_delivery_allowed",
        lambda: False,
    )

    response = await service.send_meta_template(
        auth_context.workspace_id,
        PublicApiTemplateSendRequest(
            meta_template_id=template.id,
            contact_id=contact.id,
        ),
    )
    stored = await db_session.scalar(
        select(WhatsAppMessage).where(WhatsAppMessage.contact_id == contact.id)
    )

    assert response.wamid is None
    assert stored is not None
    assert stored.status == WaMessageStatus.pending
    assert stored.wamid is None
    service.meta_client.send_template_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_public_template_failure_keeps_committed_pending_intent(
    db_session,
    auth_context,
    monkeypatch,
):
    connection, contact = await _connection_and_contact(db_session, auth_context.workspace_id)
    template = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        name="template_timeout",
        language="pt_BR",
        status=MetaTemplateStatus.approved,
        components=[],
        raw_payload={},
    )
    db_session.add(template)
    await db_session.flush()
    service = PublicApiService(db_session)
    service.connection_service.get_meta_access_token = lambda connection: "test-token"
    service.meta_client.send_template_message = AsyncMock(
        side_effect=TimeoutError("provider outcome is uncertain")
    )
    monkeypatch.setattr(
        "app.services.public_api_service.external_delivery_allowed",
        lambda: True,
    )

    with pytest.raises(TimeoutError):
        await service.send_meta_template(
            auth_context.workspace_id,
            PublicApiTemplateSendRequest(
                meta_template_id=template.id,
                contact_id=contact.id,
            ),
        )

    stored = await db_session.scalar(
        select(WhatsAppMessage).where(WhatsAppMessage.contact_id == contact.id)
    )
    assert stored is not None
    assert stored.status == WaMessageStatus.pending
    assert stored.wamid is None


@pytest.mark.asyncio
async def test_public_campaign_launch_now_remains_a_draft_when_gate_is_closed(
    db_session,
    auth_context,
    monkeypatch,
):
    connection, _ = await _connection_and_contact(db_session, auth_context.workspace_id)
    template = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        name="campaign_gate",
        language="pt_BR",
        status=MetaTemplateStatus.approved,
        components=[
            {"type": "BODY", "text": "Confira"},
            {
                "type": "BUTTONS",
                "buttons": [
                    {"type": "QUICK_REPLY", "text": "Depois"},
                    {"type": "URL", "text": "Abrir", "url": "https://example.com/{{1}}"},
                ],
            },
        ],
        raw_payload={},
    )
    db_session.add(template)
    await db_session.flush()

    service = PublicApiService(db_session)
    service.campaign_service.launch_campaign = AsyncMock(
        side_effect=AssertionError("campaign launch must not run while delivery is blocked")
    )
    monkeypatch.setattr(
        "app.services.public_api_service.external_delivery_allowed",
        lambda: False,
    )

    response = await service.create_campaign(
        auth_context.workspace_id,
        acting_user_id=auth_context.user_id,
        payload=PublicApiCampaignCreateRequest(
            whatsapp_connection_id=connection.id,
            meta_template_id=template.id,
            title="Campanha local",
            audience_filter=CampaignAudienceFilter(),
            button_parameters=[{"index": 1, "value": "proposta-123"}],
            launch_now=True,
        ),
    )

    assert response.campaign.status == CampaignStatus.draft
    assert response.campaign.launched_at is None
    assert response.campaign.template_snapshot is not None
    assert [
        item.model_dump(mode="json")
        for item in response.campaign.template_snapshot.button_parameters
    ] == [{"index": 1, "value": "proposta-123"}]
    service.campaign_service.launch_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_campaign_launch_and_resume_endpoints_fail_closed_without_mutation(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    connection, _ = await _connection_and_contact(db_session, auth_context.workspace_id)
    draft = WhatsAppCampaign(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        created_by_id=auth_context.user_id,
        title="Campanha bloqueada",
        status=CampaignStatus.draft,
    )
    paused = WhatsAppCampaign(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        created_by_id=auth_context.user_id,
        title="Campanha pausada",
        status=CampaignStatus.paused,
    )
    db_session.add_all([draft, paused])
    await db_session.commit()
    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp_campaigns.external_delivery_allowed",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.campaign_service.external_delivery_allowed",
        lambda: False,
    )

    launch_response = await client.post(
        f"/api/v1/whatsapp/campaigns/{draft.id}/launch",
        json={"dryRun": False, "sampleSize": 5},
    )
    resume_response = await client.post(
        f"/api/v1/whatsapp/campaigns/{paused.id}/resume",
    )

    assert launch_response.status_code == 503
    assert launch_response.json()["detail"]["code"] == "KLM-EXT-001"
    assert resume_response.status_code == 503
    assert resume_response.json()["detail"]["code"] == "KLM-EXT-001"
    await db_session.refresh(draft)
    await db_session.refresh(paused)
    assert draft.status == CampaignStatus.draft
    assert paused.status == CampaignStatus.paused

    campaign_service = PublicApiService(db_session).campaign_service
    with pytest.raises(HTTPException) as launch_error:
        await campaign_service.launch_campaign(
            auth_context.workspace_id,
            draft.id,
            CampaignLaunchRequest(dry_run=False, sample_size=5),
        )
    with pytest.raises(HTTPException) as resume_error:
        await campaign_service.resume_campaign(auth_context.workspace_id, paused.id)
    assert launch_error.value.status_code == 503
    assert resume_error.value.status_code == 503


@pytest.mark.asyncio
async def test_password_reset_email_never_reaches_resend_when_gate_is_closed(
    monkeypatch,
):
    resend_factory_called = False

    def forbidden_resend_factory():
        nonlocal resend_factory_called
        resend_factory_called = True
        raise AssertionError("Resend must not be initialized while delivery is blocked")

    monkeypatch.setattr(email_service, "external_delivery_allowed", lambda: False)
    monkeypatch.setattr(email_service.settings, "RESEND_API_KEY", "configured-key")
    monkeypatch.setattr(email_service, "_get_resend_client", forbidden_resend_factory)

    sent = await email_service.send_password_reset_email(
        to_email="owner@example.com",
        to_name="Owner",
        reset_token="local-reset-token",
    )

    assert sent is False
    assert resend_factory_called is False


@pytest.mark.asyncio
async def test_ses_transactional_email_sets_configured_reply_to(monkeypatch):
    captured: dict[str, object] = {}

    class FakeSesClient:
        def send_email(self, **kwargs):
            captured.update(kwargs)
            return {"MessageId": "ses-message-id"}

    monkeypatch.setattr(email_service, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(email_service.settings, "EMAIL_TRANSPORT", "ses")
    monkeypatch.setattr(email_service.settings, "EMAIL_FROM", "noreply@meuexecutivo.com")
    monkeypatch.setattr(email_service.settings, "EMAIL_FROM_NAME", "Meu Executivo")
    monkeypatch.setattr(
        email_service.settings,
        "EMAIL_REPLY_TO",
        "contato@meuexecutivo.com",
    )
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        SimpleNamespace(client=lambda *_args, **_kwargs: FakeSesClient()),
    )

    result = await email_service._send_email_result(
        "owner@example.com",
        "Assunto",
        "<p>Mensagem</p>",
    )

    assert result.sent is True
    assert captured["ReplyToAddresses"] == ["contato@meuexecutivo.com"]


@pytest.mark.asyncio
async def test_public_api_send_scope_requires_message_send_permission(
    db_session,
    auth_context,
):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.role = WorkspaceRole.workspace_admin
    membership.permission_denials = [
        {"module": "mensagens", "action": "send_message", "scope": "workspace"}
    ]
    await db_session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await PublicApiService(db_session).create_token(
            auth_context.workspace_id,
            auth_context.user_id,
            WorkspacePublicApiTokenCreate(
                label="Token sem envio",
                scopes=["templates:send"],
            ),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "PUBAPI-408"
