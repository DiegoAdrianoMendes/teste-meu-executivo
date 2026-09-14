from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.client import Client
from app.models.flow import Flow, FlowRun
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import KanbanColumnId, Matter, MicroStatus, PracticeArea
from app.models.whatsapp import (
    ConnectionType,
    HumanControlState,
    WaConnectionStatus,
    WaQueueStatus,
    WhatsAppAutomationRuleDispatch,
    WhatsAppConnection,
    WhatsAppContact,
)
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignSendJobStatus,
    CampaignStatus,
    MetaTemplateStatus,
    WhatsAppAudienceList,
    WhatsAppAudienceListMember,
    WhatsAppCampaign,
    WhatsAppCampaignRecipient,
    WhatsAppCampaignSendJob,
    WhatsAppMetaTemplate,
    WhatsAppOptInRecord,
    WhatsAppSuppressionEntry,
)
from app.models.workspace import Workspace
from app.services.public_api_service import PublicApiService
from app.services.whatsapp.automation_rule_service import WhatsAppAutomationRuleService
from app.services.whatsapp.secrets import WhatsAppSecretCipher
from app.services.whatsapp_service import WhatsAppService

pytestmark = pytest.mark.usefixtures("active_subscription")


async def _seed_picker_sequence_target(db_session, auth_context, *, opted_in: bool):
    cipher = WhatsAppSecretCipher()
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        phone_number_id=f"pn-sequence-{uuid4().hex[:8]}",
        phone_number="5511999990000",
        access_token_encrypted=cipher.encrypt("meta-token"),
        status=WaConnectionStatus.connected,
    )
    client_record = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Sequência",
        email=f"sequence-{uuid4().hex[:8]}@example.com",
        phone="5511999991234",
    )
    db_session.add_all([connection, client_record])
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client_record.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Teste sequência picker",
        column_id=KanbanColumnId.entrada,
        micro_status=MicroStatus.lead_novo,
    )
    template = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        name=f"sequence_template_{uuid4().hex[:8]}",
        category="utility",
        language="pt_BR",
        status=MetaTemplateStatus.approved,
        components=[{"type": "BODY", "text": "Olá, cliente"}],
        raw_payload={},
    )
    db_session.add_all([matter, template])
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511999991234",
        name="Cliente Sequência",
        matter_id=matter.id,
    )
    db_session.add(contact)
    await db_session.flush()
    if opted_in:
        db_session.add(
            WhatsAppOptInRecord(
                workspace_id=auth_context.workspace_id,
                contact_id=contact.id,
                phone=contact.phone,
                channel="whatsapp",
            )
        )
    await db_session.commit()
    return connection, template, contact, matter


@pytest.mark.asyncio
async def test_picker_sequence_start_real_endpoint_returns_recipient_after_meta_accept(
    client, db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp_campaigns.external_delivery_allowed", lambda: True
    )
    monkeypatch.setattr(
        "app.services.whatsapp.campaign_service.external_delivery_allowed", lambda: True
    )
    connection, template, contact, matter = await _seed_picker_sequence_target(
        db_session, auth_context, opted_in=True
    )

    async def fake_send_template_message(self, **kwargs):
        assert kwargs["phone_number_id"] == connection.phone_number_id
        return {"messages": [{"id": "wamid-sequence-start"}]}

    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.send_template_message",
        fake_send_template_message,
    )
    response = await client.post(
        "/api/v1/whatsapp/sequences/start",
        headers={"Idempotency-Key": "sequence-real-accepted"},
        json={
            "contactId": str(contact.id),
            "matterId": str(matter.id),
            "connectionId": str(connection.id),
            "templateId": str(template.id),
            "stages": [{"templateId": str(template.id), "body": "adulterado", "bindings": []}],
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["dispatchState"] == "accepted"
    assert payload["code"] == "WA-SEQUENCE-DISPATCH-ACCEPTED"
    assert payload["firstRecipientId"]
    recipient = await db_session.scalar(
        select(WhatsAppCampaignRecipient).where(
            WhatsAppCampaignRecipient.id == payload["firstRecipientId"]
        )
    )
    assert recipient is not None
    assert recipient.status == CampaignRecipientStatus.sent
    assert recipient.wamid == "wamid-sequence-start"


@pytest.mark.asyncio
async def test_picker_sequence_start_fails_closed_without_eligible_recipient(
    client, db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp_campaigns.external_delivery_allowed", lambda: True
    )
    monkeypatch.setattr(
        "app.services.whatsapp.campaign_service.external_delivery_allowed", lambda: True
    )
    connection, template, contact, matter = await _seed_picker_sequence_target(
        db_session, auth_context, opted_in=False
    )
    response = await client.post(
        "/api/v1/whatsapp/sequences/start",
        headers={"Idempotency-Key": "sequence-zero-eligible"},
        json={
            "contactId": str(contact.id),
            "matterId": str(matter.id),
            "connectionId": str(connection.id),
            "templateId": str(template.id),
            "stages": [{"templateId": str(template.id), "bindings": []}],
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "WA-SEQUENCE-RECIPIENT-COUNT"
    assert "firstRecipientId" not in response.json()


@pytest.mark.asyncio
async def test_picker_sequence_start_does_not_confirm_missing_meta_message_id(
    client, db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp_campaigns.external_delivery_allowed", lambda: True
    )
    monkeypatch.setattr(
        "app.services.whatsapp.campaign_service.external_delivery_allowed", lambda: True
    )
    connection, template, contact, matter = await _seed_picker_sequence_target(
        db_session, auth_context, opted_in=True
    )

    async def fake_provider_rejection(self, **kwargs):
        return {"messages": []}

    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.send_template_message",
        fake_provider_rejection,
    )
    response = await client.post(
        "/api/v1/whatsapp/sequences/start",
        headers={"Idempotency-Key": "sequence-provider-rejected"},
        json={
            "contactId": str(contact.id),
            "matterId": str(matter.id),
            "connectionId": str(connection.id),
            "templateId": str(template.id),
            "stages": [{"templateId": str(template.id), "bindings": []}],
        },
    )

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "WA-SEQUENCE-FIRST-SEND-NOT-ACCEPTED"
    assert response.json()["detail"]["recipientStatus"] == "queued"


@pytest.mark.asyncio
async def test_new_workspace_whatsapp_automation_defaults_are_opt_in(
    db_session,
    client,
    auth_context,
):
    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    settings = dict(workspace.settings or {})
    settings.pop("whatsappAutomation", None)
    workspace.settings = settings
    await db_session.commit()

    response = await client.get("/api/v1/admin/automation-rules")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["onboardingCompleted"] is False
    assert all(rule["enabled"] is False for rule in payload["rules"])
    assert next(rule for rule in payload["rules"] if rule["triggerType"] == "off_hours")[
        "silenceWindowHours"
    ] == 12


@pytest.mark.asyncio
async def test_campaign_dry_run_and_launch_respect_opt_in_and_suppression(client, db_session, auth_context, monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp_campaigns.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.campaign_service.external_delivery_allowed",
        lambda: True,
    )
    cipher = WhatsAppSecretCipher()
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_campaign",
        phone_number="5511999990000",
        display_phone_number="+55 11 99999-0000",
        access_token_encrypted=cipher.encrypt("meta-token"),
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(connection)
    await db_session.flush()

    template = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        name="followup_proposta",
        category="marketing",
        language="pt_BR",
        status=MetaTemplateStatus.approved,
        components=[],
        raw_payload={},
    )
    db_session.add(template)

    matter_allowed_client = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Elegível",
        email="elegivel@example.com",
        phone="+5511911111111",
    )
    matter_blocked_client = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Suprimido",
        email="suprimido@example.com",
        phone="+5511922222222",
    )
    db_session.add_all([matter_allowed_client, matter_blocked_client])
    await db_session.flush()

    allowed_matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=matter_allowed_client.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Cobrança de horas extras",
        column_id=KanbanColumnId.proposta_fechamento,
        micro_status=MicroStatus.proposta_enviada,
    )
    blocked_matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=matter_blocked_client.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Rescisão indireta",
        column_id=KanbanColumnId.proposta_fechamento,
        micro_status=MicroStatus.proposta_enviada,
    )
    db_session.add_all([allowed_matter, blocked_matter])
    await db_session.flush()

    allowed_contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511911111111",
        name="Cliente Elegível",
        matter_id=allowed_matter.id,
    )
    blocked_contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511922222222",
        name="Cliente Suprimido",
        matter_id=blocked_matter.id,
    )
    missing_opt_in_contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511933333333",
        name="Sem Opt-in",
    )
    db_session.add_all([allowed_contact, blocked_contact, missing_opt_in_contact])
    await db_session.flush()

    db_session.add(
        WhatsAppOptInRecord(
            workspace_id=auth_context.workspace_id,
            contact_id=allowed_contact.id,
            phone="5511911111111",
            channel="whatsapp",
        )
    )
    db_session.add(
        WhatsAppOptInRecord(
            workspace_id=auth_context.workspace_id,
            contact_id=blocked_contact.id,
            phone="5511922222222",
            channel="whatsapp",
        )
    )
    db_session.add(
        WhatsAppSuppressionEntry(
            workspace_id=auth_context.workspace_id,
            contact_id=blocked_contact.id,
            phone="5511922222222",
            reason="blacklist",
            is_active=True,
        )
    )
    await db_session.commit()

    create_response = await client.post(
        "/api/v1/whatsapp/campaigns",
        json={
            "whatsappConnectionId": str(connection.id),
            "title": "Follow-up comercial",
            "description": "Reativa propostas paradas",
            "metaTemplateId": str(template.id),
            "headerParameters": [],
            "bodyParameters": [],
            "audienceFilter": {
                "columnIds": ["proposta_fechamento"],
                "includeUnlinkedContacts": True,
                "includeBlocked": False,
            },
        },
    )

    assert create_response.status_code == 201, create_response.text
    created = create_response.json()
    assert created["eligibleRecipients"] == 1
    assert created["blockedRecipients"] == 1
    assert created["totalRecipients"] == 2

    dry_run_response = await client.post(
        f"/api/v1/whatsapp/campaigns/{created['id']}/launch",
        json={"dryRun": True, "sampleSize": 5},
    )
    assert dry_run_response.status_code == 200, dry_run_response.text
    dry_run_payload = dry_run_response.json()
    assert dry_run_payload["audience"]["estimatedRecipients"] == 1
    assert any(item["reason"] == "ok" for item in dry_run_payload["audience"]["sampleRecipients"])

    async def fake_send_template_message(self, **kwargs):
        assert kwargs["phone_number_id"] == "pn_campaign"
        assert kwargs["to"] == "5511911111111"
        return {"messages": [{"id": "wamid-campaign-1"}]}

    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.send_template_message",
        fake_send_template_message,
    )

    launch_response = await client.post(
        f"/api/v1/whatsapp/campaigns/{created['id']}/launch",
        json={"dryRun": False, "sampleSize": 5},
    )
    assert launch_response.status_code == 200, launch_response.text
    launched = launch_response.json()
    assert launched["failedCount"] == 0
    assert launched["status"] in {"running", "completed"}

    recipients = (
        await db_session.execute(
            select(WhatsAppCampaignRecipient).where(
                WhatsAppCampaignRecipient.campaign_id == launched["id"]
            )
        )
    ).scalars().all()
    assert len(recipients) == 1
    assert recipients[0].phone == "5511911111111"
    assert recipients[0].status in {CampaignRecipientStatus.queued, CampaignRecipientStatus.sent}
    if recipients[0].wamid is not None:
        assert recipients[0].wamid == "wamid-campaign-1"


@pytest.mark.asyncio
async def test_scheduled_campaigns_are_processed_by_due_scheduler(client, db_session, auth_context, monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp_campaigns.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.campaign_service.external_delivery_allowed",
        lambda: True,
    )
    cipher = WhatsAppSecretCipher()
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_scheduled",
        phone_number="5511988880000",
        display_phone_number="+55 11 98888-0000",
        access_token_encrypted=cipher.encrypt("meta-token"),
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(connection)
    await db_session.flush()

    template = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        name="scheduled_followup",
        category="marketing",
        language="pt_BR",
        status=MetaTemplateStatus.approved,
        components=[],
        raw_payload={},
    )
    db_session.add(template)

    scheduled_client = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Agendado",
        email="agendado@example.com",
        phone="+5511966666666",
    )
    db_session.add(scheduled_client)
    await db_session.flush()

    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=scheduled_client.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Ação de equiparação salarial",
        column_id=KanbanColumnId.proposta_fechamento,
        micro_status=MicroStatus.proposta_enviada,
    )
    db_session.add(matter)
    await db_session.flush()

    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511966666666",
        name="Cliente Agendado",
        matter_id=matter.id,
    )
    db_session.add(contact)
    await db_session.flush()

    db_session.add(
        WhatsAppOptInRecord(
            workspace_id=auth_context.workspace_id,
            contact_id=contact.id,
            phone="5511966666666",
            channel="whatsapp",
        )
    )
    await db_session.commit()

    scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    create_response = await client.post(
        "/api/v1/whatsapp/campaigns",
        json={
            "whatsappConnectionId": str(connection.id),
            "title": "Campanha agendada",
            "description": "Disparo futuro",
            "metaTemplateId": str(template.id),
            "headerParameters": [],
            "bodyParameters": [],
            "scheduledAt": scheduled_at.isoformat(),
            "audienceFilter": {
                "columnIds": ["proposta_fechamento"],
                "includeBlocked": False,
            },
        },
    )
    assert create_response.status_code == 201, create_response.text
    created_campaign = create_response.json()
    assert created_campaign["status"] == "draft"
    campaign_id = created_campaign["id"]

    launch_response = await client.post(
        f"/api/v1/whatsapp/campaigns/{campaign_id}/launch",
        json={"dryRun": False, "sampleSize": 5},
    )
    assert launch_response.status_code == 200, launch_response.text
    assert launch_response.json()["status"] == "scheduled"

    async def fake_send_template_message(self, **kwargs):
        assert kwargs["phone_number_id"] == "pn_scheduled"
        assert kwargs["to"] == "5511966666666"
        return {"messages": [{"id": "wamid-scheduled-1"}]}

    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.send_template_message",
        fake_send_template_message,
    )

    campaign = await db_session.get(WhatsAppCampaign, campaign_id)
    assert campaign is not None
    campaign.scheduled_at = datetime.now(timezone.utc) - timedelta(minutes=1)

    jobs = (
        await db_session.execute(
            select(WhatsAppCampaignSendJob).where(WhatsAppCampaignSendJob.campaign_id == campaign_id)
        )
    ).scalars().all()
    assert jobs
    for job in jobs:
        job.status = CampaignSendJobStatus.scheduled
        job.scheduled_for = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.commit()

    processed = await PublicApiService(db_session).campaign_service.process_due_campaigns()
    assert processed >= 1

    await db_session.refresh(campaign)
    recipients = (
        await db_session.execute(
            select(WhatsAppCampaignRecipient).where(WhatsAppCampaignRecipient.campaign_id == campaign_id)
        )
    ).scalars().all()
    assert len(recipients) == 1
    assert recipients[0].status == CampaignRecipientStatus.sent
    assert recipients[0].wamid == "wamid-scheduled-1"
    assert campaign.status in {CampaignStatus.running, CampaignStatus.completed}


@pytest.mark.asyncio
async def test_campaign_rejects_qr_connection(client, db_session, auth_context):
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.QR_CODE,
        is_default=True,
        instance_id="qr-campaign",
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(connection)
    await db_session.flush()

    template = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        name="qr_template",
        category="marketing",
        language="pt_BR",
        status=MetaTemplateStatus.approved,
        components=[],
        raw_payload={},
    )
    db_session.add(template)
    await db_session.commit()

    response = await client.post(
        "/api/v1/whatsapp/campaigns",
        json={
            "whatsappConnectionId": str(connection.id),
            "title": "Campanha inválida",
            "metaTemplateId": str(template.id),
            "bodyParameters": [],
            "headerParameters": [],
            "audienceFilter": {},
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "WA-CAM-008"


@pytest.mark.asyncio
async def test_native_audience_list_csv_export_and_list_only_campaign(
    client,
    db_session,
    auth_context,
):
    cipher = WhatsAppSecretCipher()
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_list_only",
        phone_number="5511977770000",
        display_phone_number="+55 11 97777-0000",
        access_token_encrypted=cipher.encrypt("meta-token"),
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(connection)
    await db_session.flush()
    template = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        name="lista_followup",
        category="marketing",
        language="pt_BR",
        status=MetaTemplateStatus.approved,
        components=[],
        raw_payload={},
    )
    flow = Flow(
        workspace_id=auth_context.workspace_id,
        name="Recuperação da lista",
        description="Flow local depois da resposta",
        practice_area="universal",
        trigger="manual",
        is_active=True,
        nodes=[],
        edges=[],
    )
    db_session.add_all([template, flow])
    await db_session.commit()

    create_list = await client.post(
        "/api/v1/whatsapp/audience-lists",
        json={"name": "Negativas recentes", "description": "Importada do marketing", "source": "csv"},
    )
    assert create_list.status_code == 201, create_list.text
    audience_list_id = create_list.json()["id"]

    csv_payload = (
        "nome;telefone;email;origem;campanha;opt_in\n"
        "Ana Lima;+55 11 95555-0001;ana@example.com;meta;saude-julho;sim\n"
        "Ana Atualizada;5511955550001;ana@example.com;meta;saude-julho;sim\n"
        "Bruno;telefone-invalido;bruno@example.com;site;saude-julho;sim\n"
        "Carla;+55 11 95555-0002;carla@example.com;site;saude-julho;nao\n"
    )
    imported = await client.post(
        f"/api/v1/whatsapp/audience-lists/{audience_list_id}/import-csv",
        content=csv_payload.encode("utf-8"),
        headers={"Content-Type": "text/csv; charset=utf-8", "X-File-Name": "leads.csv"},
    )
    assert imported.status_code == 200, imported.text
    result = imported.json()
    assert result["imported"] == 2
    assert result["duplicates"] == 1
    assert result["invalid"] == 1
    assert result["optInsRecorded"] == 1
    assert result["normalized"] == 2
    assert result["audienceList"]["validCount"] == 2

    detail = await client.get(f"/api/v1/whatsapp/audience-lists/{audience_list_id}")
    assert detail.status_code == 200, detail.text
    assert {item["phone"] for item in detail.json()["members"]} == {
        "5511955550001",
        "5511955550002",
    }

    exported = await client.get(f"/api/v1/whatsapp/audience-lists/{audience_list_id}/export.csv")
    assert exported.status_code == 200, exported.text
    assert "Ana Atualizada" in exported.text
    assert "5511955550002" in exported.text

    campaign_response = await client.post(
        "/api/v1/whatsapp/campaigns",
        json={
            "whatsappConnectionId": str(connection.id),
            "title": "Campanha por lista",
            "metaTemplateId": str(template.id),
            "headerParameters": [],
            "bodyParameters": [],
            "followUpFlowId": str(flow.id),
            "flowStartMode": "on_reply",
            "audienceFilter": {
                "audienceListIds": [audience_list_id],
                "includeCrmContacts": False,
                "includeBlocked": False,
            },
        },
    )
    assert campaign_response.status_code == 201, campaign_response.text
    campaign = campaign_response.json()
    assert campaign["followUpFlowId"] == str(flow.id)
    assert campaign["flowStartMode"] == "on_reply"
    assert campaign["totalRecipients"] == 2
    assert campaign["eligibleRecipients"] == 1
    assert campaign["blockedRecipients"] == 1

    preview = await client.post(
        f"/api/v1/whatsapp/campaigns/{campaign['id']}/launch",
        json={"dryRun": True, "sampleSize": 8},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["audience"]["estimatedRecipients"] == 1

    stored_list = await db_session.get(WhatsAppAudienceList, audience_list_id)
    members = (
        await db_session.scalars(
            select(WhatsAppAudienceListMember).where(
                WhatsAppAudienceListMember.audience_list_id == audience_list_id
            )
        )
    ).all()
    assert stored_list is not None
    assert len(members) == 2


@pytest.mark.asyncio
async def test_campaign_delivery_starts_bound_flow_once(db_session, auth_context, monkeypatch):
    client_record = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Entrega",
        email="entrega@example.com",
        phone="+5511988880011",
    )
    db_session.add(client_record)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client_record.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Teste de fluxo por entrega",
        column_id=KanbanColumnId.entrada,
        micro_status=MicroStatus.lead_novo,
    )
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        phone_number_id="pn_flow_delivery",
        phone_number="5511988880000",
        status=WaConnectionStatus.connected,
    )
    flow = Flow(
        workspace_id=auth_context.workspace_id,
        name="Fluxo após entrega",
        practice_area="universal",
        trigger="manual",
        is_active=True,
        nodes=[{"id": "start", "type": "trigger", "config": {}}],
        edges=[],
    )
    db_session.add_all([matter, connection, flow])
    await db_session.flush()
    campaign = WhatsAppCampaign(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        follow_up_flow_id=flow.id,
        flow_start_mode="on_delivery",
        title="Entrega com fluxo",
        status=CampaignStatus.running,
    )
    db_session.add(campaign)
    await db_session.flush()
    recipient = WhatsAppCampaignRecipient(
        workspace_id=auth_context.workspace_id,
        campaign_id=campaign.id,
        matter_id=matter.id,
        phone="5511988880011",
        wamid="wamid-flow-delivery",
        status=CampaignRecipientStatus.sent,
        send_context={
            "client_name": "Cliente Entrega",
            "contact": {"name": "Cliente Entrega"},
            "matter": {"case_number": "CASE-DELIVERY"},
        },
    )
    db_session.add(recipient)
    await db_session.commit()

    service = WhatsAppService(db_session).campaign_service
    from app.services.flow_service import FlowService

    original_start_flow_run = FlowService.start_flow_run
    attempts = 0

    async def fail_first_start(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected follow-up start failure")
        return await original_start_flow_run(self, *args, **kwargs)

    monkeypatch.setattr(FlowService, "start_flow_run", fail_first_start)
    await service.sync_delivery_status(
        "wamid-flow-delivery",
        "delivered",
        {"id": "wamid-flow-delivery", "status": "delivered"},
    )
    await db_session.refresh(recipient)
    assert recipient.flow_run_id is None
    # The provider may replay the exact same delivered receipt. The durable
    # receipt must not suppress recovery of a follow-up that failed to start.
    await service.sync_delivery_status(
        "wamid-flow-delivery",
        "delivered",
        {"id": "wamid-flow-delivery", "status": "delivered"},
    )
    await service.sync_delivery_status(
        "wamid-flow-delivery",
        "read",
        {"id": "wamid-flow-delivery", "status": "read"},
    )

    await db_session.refresh(recipient)
    runs = list(
        (
            await db_session.scalars(
                select(FlowRun).where(
                    FlowRun.workspace_id == auth_context.workspace_id,
                    FlowRun.flow_id == flow.id,
                    FlowRun.matter_id == matter.id,
                )
            )
        ).all()
    )
    assert recipient.flow_run_id is not None
    assert recipient.flow_trigger_event == "on_delivery"
    assert len(runs) == 1
    assert runs[0].id == recipient.flow_run_id
    assert runs[0].context["client_name"] == "Cliente Entrega"
    assert runs[0].context["matter"]["case_number"] == "CASE-DELIVERY"


@pytest.mark.asyncio
async def test_campaign_reply_starts_latest_bound_flow_once(db_session, auth_context):
    client_record = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Resposta",
        email="resposta@example.com",
        phone="+5511988880022",
    )
    db_session.add(client_record)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client_record.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Teste de fluxo por resposta",
        column_id=KanbanColumnId.entrada,
        micro_status=MicroStatus.lead_novo,
    )
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        phone_number_id="pn_flow_reply",
        phone_number="5511988880000",
        status=WaConnectionStatus.connected,
    )
    flow = Flow(
        workspace_id=auth_context.workspace_id,
        name="Fluxo após resposta",
        practice_area="universal",
        trigger="manual",
        is_active=True,
        nodes=[{"id": "start", "type": "trigger", "config": {}}],
        edges=[],
    )
    db_session.add_all([matter, connection, flow])
    await db_session.flush()
    campaign = WhatsAppCampaign(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        follow_up_flow_id=flow.id,
        flow_start_mode="on_reply",
        title="Resposta com fluxo",
        status=CampaignStatus.completed,
    )
    db_session.add(campaign)
    await db_session.flush()
    recipient = WhatsAppCampaignRecipient(
        workspace_id=auth_context.workspace_id,
        campaign_id=campaign.id,
        matter_id=matter.id,
        phone="5511988880022",
        wamid="wamid-flow-reply",
        status=CampaignRecipientStatus.delivered,
        sent_at=datetime.now(timezone.utc),
        send_context={},
    )
    db_session.add(recipient)
    await db_session.commit()

    service = WhatsAppService(db_session).campaign_service
    first_run_id = await service.trigger_follow_up_flow_on_reply(
        workspace_id=auth_context.workspace_id,
        phone="+55 (11) 98888-0022",
        matter_id=matter.id,
    )
    second_run_id = await service.trigger_follow_up_flow_on_reply(
        workspace_id=auth_context.workspace_id,
        phone="5511988880022",
        matter_id=matter.id,
    )

    await db_session.refresh(recipient)
    runs = list(
        (
            await db_session.scalars(
                select(FlowRun).where(
                    FlowRun.workspace_id == auth_context.workspace_id,
                    FlowRun.flow_id == flow.id,
                    FlowRun.matter_id == matter.id,
                )
            )
        ).all()
    )
    assert first_run_id == recipient.flow_run_id
    assert second_run_id is None
    assert recipient.flow_trigger_event == "on_reply"
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_notes_drawer_and_move_stage_work_from_inbox(client, db_session, auth_context):
    client_record = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Drawer",
        email="drawer@example.com",
        phone="+5511944444444",
    )
    db_session.add(client_record)
    await db_session.flush()

    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client_record.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Horas extras",
        column_id=KanbanColumnId.entrada,
        micro_status=MicroStatus.lead_novo,
    )
    db_session.add(matter)
    await db_session.flush()

    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_drawer",
        phone_number="5511944444444",
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(connection)
    await db_session.flush()

    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511944444444",
        name="Cliente Drawer",
        matter_id=matter.id,
    )
    db_session.add(contact)
    await db_session.commit()

    note_response = await client.post(
        f"/api/v1/whatsapp/conversations/{contact.id}/notes",
        json={"body": "Cliente pediu proposta formal até amanhã."},
    )
    assert note_response.status_code == 201, note_response.text
    assert note_response.json()["body"] == "Cliente pediu proposta formal até amanhã."

    drawer_response = await client.get(f"/api/v1/whatsapp/conversations/{contact.id}/drawer")
    assert drawer_response.status_code == 200, drawer_response.text
    drawer = drawer_response.json()
    assert drawer["contact"]["id"] == str(contact.id)
    assert drawer["matter"]["id"] == str(matter.id)
    assert drawer["canEditMatter"] is True
    assert drawer["notes"][0]["body"] == "Cliente pediu proposta formal até amanhã."

    move_response = await client.post(
        f"/api/v1/whatsapp/conversations/{contact.id}/move-stage",
        json={"columnId": "qualificacao", "microStatus": "qualificado"},
    )
    assert move_response.status_code == 200, move_response.text
    moved = move_response.json()
    assert moved["matter"]["columnId"] == "qualificacao"
    assert moved["matter"]["microStatus"] == "qualificado"


@pytest.mark.asyncio
async def test_automation_rules_endpoint_persists_and_applies_keyword_flow(db_session, client, auth_context):
    save_response = await client.put(
        "/api/v1/admin/automation-rules",
        json={
            "enabled": True,
            "onboardingCompleted": True,
            "timezone": "America/Sao_Paulo",
            "businessHoursStart": 8,
            "businessHoursEnd": 19,
            "rules": [
                {
                    "id": "keyword-contract",
                    "label": "Fluxo comercial por palavra-chave",
                    "enabled": True,
                    "triggerType": "keyword",
                    "keyword": "#contrato",
                    "replyText": "Perfeito, vou te encaminhar para o fluxo comercial.",
                    "targetQueueStatus": "human",
                    "targetColumnId": "proposta_fechamento",
                    "flowTemplateId": None,
                    "escalateToHuman": True,
                    "onlyFirstMessage": False,
                    "silenceWindowHours": 0,
                }
            ],
        },
    )
    assert save_response.status_code == 200, save_response.text
    assert save_response.json()["rules"][0]["keyword"] == "#contrato"

    flow = Flow(
        workspace_id=auth_context.workspace_id,
        name="Fluxo Comercial",
        description="Fluxo interno",
        practice_area="trabalhista",
        trigger="keyword",
        is_template=False,
        is_active=True,
        nodes=[],
        edges=[],
    )
    db_session.add(flow)
    await db_session.flush()

    client_record = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Automação",
        email="auto@example.com",
        phone="+5511955555555",
    )
    db_session.add(client_record)
    await db_session.flush()

    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client_record.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Verbas rescisórias",
        column_id=KanbanColumnId.entrada,
        micro_status=MicroStatus.lead_novo,
    )
    db_session.add(matter)
    await db_session.flush()

    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_automation",
        phone_number="5511955555555",
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(connection)
    await db_session.flush()

    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511955555555",
        name="Cliente Automação",
        matter_id=matter.id,
    )
    db_session.add(contact)
    await db_session.commit()

    resave_response = await client.put(
        "/api/v1/admin/automation-rules",
        json={
            "enabled": True,
            "onboardingCompleted": True,
            "timezone": "America/Sao_Paulo",
            "businessHoursStart": 8,
            "businessHoursEnd": 19,
            "rules": [
                {
                    "id": "keyword-contract",
                    "label": "Fluxo comercial por palavra-chave",
                    "enabled": True,
                    "triggerType": "keyword",
                    "keyword": "#contrato",
                    "replyText": "Perfeito, vou te encaminhar para o fluxo comercial.",
                    "targetQueueStatus": "human",
                    "targetColumnId": "proposta_fechamento",
                    "flowTemplateId": str(flow.id),
                    "escalateToHuman": True,
                    "onlyFirstMessage": False,
                    "silenceWindowHours": 0,
                }
            ],
        },
    )
    assert resave_response.status_code == 200, resave_response.text
    assert resave_response.json()["rules"][0]["flowTemplateId"] == str(flow.id)

    service = WhatsAppAutomationRuleService(db_session)
    outcome = await service.apply_inbound_rules(contact=contact, inbound_text="Quero avançar com #contrato hoje")

    assert outcome is not None
    assert outcome["replyText"] == "Perfeito, vou te encaminhar para o fluxo comercial."
    assert outcome["flowTemplateId"] == str(flow.id)
    assert outcome["movedColumnId"] == "proposta_fechamento"
    assert outcome["escalateToHuman"] is True

    await db_session.refresh(contact)
    await db_session.refresh(matter)

    assert contact.queue_status == WaQueueStatus.human
    assert contact.human_control_state == HumanControlState.copilot
    assert contact.active_flow_template_id == str(flow.id)
    assert contact.active_flow_label == "Fluxo Comercial"
    assert matter.column_id == KanbanColumnId.proposta_fechamento


@pytest.mark.asyncio
async def test_automation_rules_reject_flow_from_another_workspace(
    db_session,
    client,
    auth_context,
):
    foreign_workspace = Workspace(
        name="Workspace externo",
        slug=f"workspace-externo-{auth_context.workspace_id}",
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign_flow = Flow(
        workspace_id=foreign_workspace.id,
        name="Flow externo",
        practice_area="universal",
        trigger="keyword",
        is_template=False,
        is_active=True,
        nodes=[],
        edges=[],
    )
    db_session.add(foreign_flow)
    await db_session.flush()

    response = await client.put(
        "/api/v1/admin/automation-rules",
        json={
            "enabled": True,
            "onboardingCompleted": True,
            "timezone": "America/Sao_Paulo",
            "businessHoursStart": 8,
            "businessHoursEnd": 19,
            "rules": [{
                "label": "Flow alheio",
                "triggerType": "keyword",
                "keyword": "externo",
                "flowTemplateId": str(foreign_flow.id),
                "silenceWindowHours": 0,
            }],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "WA-AUTO-001"
    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    assert str(foreign_flow.id) not in str(workspace.settings)


@pytest.mark.asyncio
async def test_tampered_automation_rule_cannot_apply_foreign_flow(
    db_session,
    auth_context,
):
    foreign_workspace = Workspace(
        name="Workspace legado externo",
        slug=f"workspace-legado-{auth_context.workspace_id}",
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign_flow = Flow(
        workspace_id=foreign_workspace.id,
        name="Flow legado externo",
        practice_area="universal",
        trigger="keyword",
        is_template=False,
        is_active=True,
        nodes=[],
        edges=[],
    )
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_tenant_flow",
        status=WaConnectionStatus.connected,
    )
    db_session.add_all([foreign_flow, connection])
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511900000010",
    )
    db_session.add(contact)
    await db_session.flush()

    with pytest.raises(HTTPException) as raised:
        await WhatsAppAutomationRuleService(db_session)._apply_rule(
            contact,
            {
                "label": "Configuração adulterada",
                "flowTemplateId": str(foreign_flow.id),
                "targetQueueStatus": "human",
            },
        )

    assert raised.value.status_code == 422
    assert contact.active_flow_template_id is None
    assert contact.queue_status == WaQueueStatus.ai


@pytest.mark.asyncio
async def test_off_hours_automation_requires_opt_in_and_persists_per_contact_silence_window(
    db_session,
    auth_context,
):
    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_automation_silence",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    first_contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511999990101",
    )
    second_contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511999990102",
    )
    db_session.add_all([first_contact, second_contact])

    legacy_unconfirmed = {
        "enabled": True,
        "timezone": "America/Sao_Paulo",
        "businessHoursStart": 8,
        "businessHoursEnd": 19,
        "rules": [{
            "id": "off-hours-default",
            "label": "Resposta do escritório",
            "enabled": True,
            "triggerType": "off_hours",
            "replyText": "Retornaremos no próximo expediente.",
            "onlyFirstMessage": False,
            "silenceWindowHours": 12,
        }],
    }
    workspace.settings = {**(workspace.settings or {}), "whatsappAutomation": legacy_unconfirmed}
    await db_session.commit()

    first_instant = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)  # 22h em São Paulo
    unconfirmed_service = WhatsAppAutomationRuleService(
        db_session,
        clock=lambda: first_instant,
        delivery_gate=lambda: True,
    )
    assert (
        await unconfirmed_service.apply_inbound_rules(contact=first_contact, inbound_text="Oi")
        is None
    )

    confirmed = {**legacy_unconfirmed, "onboardingCompleted": True}
    workspace.settings = {**workspace.settings, "whatsappAutomation": confirmed}
    await db_session.commit()

    service = WhatsAppAutomationRuleService(
        db_session,
        clock=lambda: first_instant,
        delivery_gate=lambda: True,
    )
    outcomes = [
        await service.apply_inbound_rules(contact=first_contact, inbound_text=f"Mensagem {index}")
        for index in range(5)
    ]
    assert outcomes[0]["replyText"] == "Retornaremos no próximo expediente."
    assert [outcome["suppressed"] for outcome in outcomes[1:]] == [True, True, True, True]

    audit_events = (
        await db_session.scalars(
            select(KlmAuditEvent).where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id,
                KlmAuditEvent.entity_id == first_contact.id,
                KlmAuditEvent.event_type == "whatsapp_automation_reply_suppressed",
            )
        )
    ).all()
    assert len(audit_events) == 4
    assert all(event.after_data["silenceWindowHours"] == 12 for event in audit_events)

    other_contact_outcome = await service.apply_inbound_rules(
        contact=second_contact,
        inbound_text="Oi",
    )
    assert other_contact_outcome["replyText"] == "Retornaremos no próximo expediente."

    after_window = WhatsAppAutomationRuleService(
        db_session,
        clock=lambda: first_instant + timedelta(hours=24),
        delivery_gate=lambda: True,
    )
    repeated_outcome = await after_window.apply_inbound_rules(
        contact=first_contact,
        inbound_text="Oi de novo",
    )
    assert repeated_outcome["replyText"] == "Retornaremos no próximo expediente."

    dispatch = await db_session.scalar(
        select(WhatsAppAutomationRuleDispatch).where(
            WhatsAppAutomationRuleDispatch.contact_id == first_contact.id,
            WhatsAppAutomationRuleDispatch.rule_id == "off-hours-default",
        )
    )
    assert dispatch is not None
    assert dispatch.dispatch_count == 2


@pytest.mark.asyncio
async def test_off_hours_automation_respects_workspace_text_and_business_hours(
    db_session,
    auth_context,
):
    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_automation_custom_hours",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511999990103",
    )
    db_session.add(contact)
    workspace.settings = {
        **(workspace.settings or {}),
        "whatsappAutomation": {
            "enabled": True,
            "onboardingCompleted": True,
            "timezone": "America/Sao_Paulo",
            "businessHoursStart": 0,
            "businessHoursEnd": 23,
            "rules": [{
                "id": "off-hours-custom",
                "label": "Fora do horário personalizado",
                "enabled": True,
                "triggerType": "off_hours",
                "replyText": "Texto aprovado pelo titular.",
                "silenceWindowHours": 12,
            }],
        },
    }
    await db_session.commit()

    instant = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)  # 22h em São Paulo
    service = WhatsAppAutomationRuleService(
        db_session,
        clock=lambda: instant,
        delivery_gate=lambda: True,
    )
    assert await service.apply_inbound_rules(contact=contact, inbound_text="Oi") is None

    workspace.settings = {
        **workspace.settings,
        "whatsappAutomation": {
            **workspace.settings["whatsappAutomation"],
            "businessHoursStart": 8,
            "businessHoursEnd": 19,
        },
    }
    await db_session.commit()
    outcome = await service.apply_inbound_rules(contact=contact, inbound_text="Oi")
    assert outcome["replyText"] == "Texto aprovado pelo titular."


@pytest.mark.asyncio
async def test_tampered_automation_rule_cannot_move_foreign_matter(
    db_session,
    auth_context,
):
    foreign_workspace = Workspace(
        name="Workspace do matter legado",
        slug=f"workspace-matter-legado-{auth_context.workspace_id}",
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign_client = Client(
        workspace_id=foreign_workspace.id,
        name="Cliente de outro workspace",
    )
    db_session.add(foreign_client)
    await db_session.flush()
    foreign_matter = Matter(
        workspace_id=foreign_workspace.id,
        client_id=foreign_client.id,
        demand_type="Caso externo",
        column_id=KanbanColumnId.entrada,
    )
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_tenant_matter",
        status=WaConnectionStatus.connected,
    )
    db_session.add_all([foreign_matter, connection])
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511900000011",
        matter_id=foreign_matter.id,
        queue_status=WaQueueStatus.ai,
    )
    db_session.add(contact)
    await db_session.flush()

    with pytest.raises(HTTPException) as raised:
        await WhatsAppAutomationRuleService(db_session)._apply_rule(
            contact,
            {
                "label": "Mover card adulterado",
                "targetColumnId": "proposta_fechamento",
                "targetQueueStatus": "human",
            },
        )

    assert raised.value.status_code == 422
    assert raised.value.detail["code"] == "WA-AUTO-002"
    assert contact.queue_status == WaQueueStatus.ai
    assert foreign_matter.column_id == KanbanColumnId.entrada


@pytest.mark.asyncio
async def test_launch_endpoint_rejects_completed_campaign_with_409(
    client, db_session, auth_context, monkeypatch
):
    # V4 (backend): o clique explícito em "Lançar" fora de rascunho recebe 409 WA-CAM-015;
    # o dry-run continua sendo prévia e não olha o estado.
    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp_campaigns.external_delivery_allowed", lambda: True
    )
    monkeypatch.setattr(
        "app.services.whatsapp.campaign_service.external_delivery_allowed", lambda: True
    )
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        phone_number_id="pn_v4_completed",
        phone_number="5511988880099",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    campaign = WhatsAppCampaign(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        title="Campanha concluída",
        status=CampaignStatus.completed,
    )
    db_session.add(campaign)
    await db_session.commit()

    response = await client.post(
        f"/api/v1/whatsapp/campaigns/{campaign.id}/launch", json={"dryRun": False}
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "WA-CAM-015"
    assert detail["status"] == "completed"
    assert "concluída" in detail["message"]

