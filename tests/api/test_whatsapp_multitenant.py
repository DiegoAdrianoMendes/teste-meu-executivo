from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.client import Client
from app.models.flow import Flow
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import KanbanColumnId, Matter, MicroStatus, PracticeArea
from app.models.timeline_event import TimelineEvent
from app.models.user import AgentType, User, UserRole
from app.models.whatsapp import (
    ConnectionType,
    HumanControlState,
    WaConnectionStatus,
    WaMessageDirection,
    WaMessageStatus,
    WaQueueStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.services.whatsapp.secrets import WhatsAppSecretCipher
from app.services.whatsapp_service import WhatsAppService


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
async def test_create_manual_connection_persists_encrypted_credentials(client, db_session, monkeypatch, auth_context):
    seen = {}
    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp.external_delivery_allowed",
        lambda: True,
    )

    async def fake_get_phone_number_details(self, phone_number_id: str, access_token: str):
        assert phone_number_id == "pn_123"
        assert access_token == "token-manual"
        return {"display_phone_number": "+55 11 99999-0000", "verified_name": "Desk KLM"}

    async def fake_subscribe(self, waba_id: str, access_token: str):
        assert waba_id == "waba_123"
        assert access_token == "token-manual"

    async def fake_register(self, phone_number_id: str, access_token: str, pin: str):
        assert phone_number_id == "pn_123"
        assert access_token == "token-manual"
        seen["pin"] = pin

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

    response = await client.post(
        "/api/v1/whatsapp/connections/manual",
        json={
            "accessToken": "token-manual",
            "phoneNumberId": "pn_123",
            "businessAccountId": "waba_123",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["connectionType"] == "OFFICIAL_MANUAL"
    assert data["isDefault"] is True
    assert data["phoneNumberId"] == "pn_123"
    assert data["displayPhoneNumber"] == "+55 11 99999-0000"
    assert data["webhookRegistered"] is True
    assert len(seen["pin"]) == 6

    connection = (
        await db_session.execute(select(WhatsAppConnection).where(WhatsAppConnection.workspace_id == auth_context.workspace_id))
    ).scalars().one()
    assert connection.access_token_encrypted
    assert connection.registration_pin_encrypted
    assert "token-manual" not in connection.access_token_encrypted

    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    assert (workspace.settings or {}).get("whatsapp") is None


@pytest.mark.asyncio
async def test_tech_provider_callback_saves_business_token(client, db_session, monkeypatch, auth_context):
    monkeypatch.setattr(
        "app.api.v1.endpoints.tech_providers.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.tech_provider_service.external_delivery_allowed",
        lambda: True,
    )

    async def fake_exchange(self, code: str):
        assert code == "oauth-code"
        return "business-token-abc"

    async def fake_subscribe(self, waba_id: str, access_token: str):
        assert waba_id == "waba_tech"
        assert access_token == "business-token-abc"

    seen = {}

    async def fake_register(self, phone_number_id: str, access_token: str, pin: str):
        seen["pin"] = pin
        assert phone_number_id == "pn_tech"
        assert access_token == "business-token-abc"

    async def fake_phone_details(self, phone_number_id: str, access_token: str):
        return {"display_phone_number": "+55 11 98888-0000", "verified_name": "Desk Tech"}

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
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.get_phone_number_details",
        fake_phone_details,
    )

    response = await client.post(
        "/api/v1/tech-providers/callback",
        json={
            "code": "oauth-code",
            "waba_id": "waba_tech",
            "phone_number_id": "pn_tech",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["connectionType"] == "TECH_PROVIDER"
    assert data["wabaId"] == "waba_tech"
    assert len(seen["pin"]) == 6

    connection = (
        await db_session.execute(select(WhatsAppConnection).where(WhatsAppConnection.workspace_id == auth_context.workspace_id))
    ).scalars().one()
    assert connection.business_token_encrypted
    assert connection.registration_pin_encrypted


@pytest.mark.asyncio
async def test_webhook_routes_inbound_message_to_correct_workspace(db_session):
    workspace_one = Workspace(
        name="Workspace One",
        slug="workspace-one",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    workspace_two = Workspace(
        name="Workspace Two",
        slug="workspace-two",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add_all([workspace_one, workspace_two])
    await db_session.flush()

    conn_one = WhatsAppConnection(
        workspace_id=workspace_one.id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_one",
        phone_number="5511999990001",
        display_phone_number="+55 11 99999-0001",
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(timezone.utc),
    )
    conn_two = WhatsAppConnection(
        workspace_id=workspace_two.id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_two",
        phone_number="5511999990002",
        display_phone_number="+55 11 99999-0002",
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add_all([conn_one, conn_two])
    await db_session.commit()

    svc = WhatsAppService(db_session)
    await svc.process_webhook(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {
                                    "phone_number_id": "pn_two",
                                    "display_phone_number": "+55 11 99999-0002",
                                },
                                "contacts": [
                                    {"wa_id": "5511998887777", "profile": {"name": "Cliente 2"}},
                                ],
                                "messages": [
                                    {
                                        "id": "wamid-tenant-two",
                                        "from": "5511998887777",
                                        "timestamp": "1712345678",
                                        "type": "text",
                                        "text": {"body": "Preciso de ajuda"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }
    )

    contact = (
        await db_session.execute(select(WhatsAppContact).where(WhatsAppContact.phone == "5511998887777"))
    ).scalars().one()
    assert contact.workspace_id == workspace_two.id
    assert contact.whatsapp_connection_id == conn_two.id

    message = (
        await db_session.execute(select(WhatsAppMessage).where(WhatsAppMessage.wamid == "wamid-tenant-two"))
    ).scalars().one()
    assert message.whatsapp_connection_id == conn_two.id
    assert message.direction == WaMessageDirection.inbound
    assert message.inbound_pipeline["state"] == "completed"
    assert message.inbound_pipeline["attempt"] == 1


@pytest.mark.asyncio
async def test_webhook_replay_fences_uncertain_inbound_stage(
    db_session,
    monkeypatch,
):
    workspace = Workspace(
        name="Workspace Pipeline",
        slug="workspace-pipeline",
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
        phone_number_id="pn_pipeline",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        phone="5511988887777",
        name="Cliente Pipeline",
    )
    db_session.add(contact)
    await db_session.flush()
    message = WhatsAppMessage(
        contact_id=contact.id,
        whatsapp_connection_id=connection.id,
        wamid="wamid-pipeline-uncertain",
        direction=WaMessageDirection.inbound,
        status=WaMessageStatus.delivered,
        body="Quero atendimento",
        inbound_pipeline={
            "state": "automation_dispatching",
            "attempt": 1,
            "automationDispatchStarted": True,
        },
    )
    db_session.add(message)
    await db_session.commit()

    async def forbidden_automation(*_args, **_kwargs):
        raise AssertionError("replay incerto não pode repetir a automação")

    monkeypatch.setattr(
        "app.services.whatsapp.automation_rule_service.WhatsAppAutomationRuleService.apply_inbound_rules",
        forbidden_automation,
    )
    await WhatsAppService(db_session).process_webhook(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "pn_pipeline"},
                                "contacts": [
                                    {
                                        "wa_id": "5511988887777",
                                        "profile": {"name": "Cliente Pipeline"},
                                    }
                                ],
                                "messages": [
                                    {
                                        "id": "wamid-pipeline-uncertain",
                                        "from": "5511988887777",
                                        "timestamp": "1712345678",
                                        "type": "text",
                                        "text": {"body": "Quero atendimento"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }
    )

    await db_session.refresh(message)
    assert message.inbound_pipeline["state"] == "reconciliation_required"
    assert message.inbound_pipeline["reconciliationRequired"] is True
    assert message.inbound_pipeline["uncertainStage"] == "automation_dispatching"


@pytest.mark.asyncio
async def test_admin_resolves_uncertain_inbound_once_with_tenant_bound_audit(
    client,
    db_session,
    monkeypatch,
    auth_context,
):
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn-reconciliation-local",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511977776001",
        name="Cliente Reconciliação",
    )
    db_session.add(contact)
    await db_session.flush()
    message = WhatsAppMessage(
        contact_id=contact.id,
        whatsapp_connection_id=connection.id,
        wamid="wamid-reconciliation-local",
        direction=WaMessageDirection.inbound,
        status=WaMessageStatus.delivered,
        body="Mensagem com resultado externo incerto",
        inbound_pipeline={
            "state": "reconciliation_required",
            "attempt": 1,
            "reconciliationRequired": True,
            "uncertainStage": "automation_send",
            "automationCompleted": False,
            "automationDeliveryOutcome": "unknown",
        },
    )
    db_session.add(message)
    await db_session.commit()

    async def keep_resume_local(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.WhatsAppWebhookService._resume_inbound_pipeline",
        keep_resume_local,
    )

    response = await client.post(
        f"/api/v1/whatsapp/messages/{message.id}/inbound-reconciliation",
        json={
            "decision": "delivered",
            "reason": "Entrega confirmada manualmente no provedor.",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "messageId": str(message.id),
        "decision": "delivered",
        "stage": "automation",
        "state": "automation_completed",
        "resumed": True,
        "resolvedAt": response.json()["resolvedAt"],
    }
    await db_session.refresh(message)
    assert message.inbound_pipeline["reconciliationRequired"] is False
    assert message.inbound_pipeline["uncertainStage"] is None
    assert message.inbound_pipeline["automationDeliveryOutcome"] == "delivered"
    assert message.inbound_pipeline["reconciliationResolution"]["reason"] == (
        "Entrega confirmada manualmente no provedor."
    )
    assert message.inbound_lease_token is not None
    assert message.inbound_lease_expires_at is not None
    lease_expires_at = message.inbound_lease_expires_at
    if lease_expires_at.tzinfo is None:
        lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
    assert lease_expires_at <= datetime.now(timezone.utc)
    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.entity_id == message.id,
            KlmAuditEvent.event_type == "whatsapp.inbound_reconciliation_resolved",
        )
    )
    assert audit is not None
    assert audit.actor_user_id == auth_context.user_id
    assert audit.after_data["decision"] == "delivered"

    replay = await client.post(
        f"/api/v1/whatsapp/messages/{message.id}/inbound-reconciliation",
        json={"decision": "not_delivered", "reason": "Tentativa de replay explícita."},
    )
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "WA-RECON-002"


@pytest.mark.asyncio
async def test_inbound_reconciliation_hides_a_foreign_workspace_message(
    client,
    db_session,
):
    workspace = Workspace(
        name="Workspace estrangeiro de reconciliação",
        slug="foreign-inbound-reconciliation",
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
        phone_number_id="pn-reconciliation-foreign",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        phone="5511977776002",
        name="Cliente estrangeiro",
    )
    db_session.add(contact)
    await db_session.flush()
    message = WhatsAppMessage(
        contact_id=contact.id,
        whatsapp_connection_id=connection.id,
        wamid="wamid-reconciliation-foreign",
        direction=WaMessageDirection.inbound,
        status=WaMessageStatus.delivered,
        body="Não pode vazar",
        inbound_pipeline={
            "state": "reconciliation_required",
            "reconciliationRequired": True,
            "uncertainStage": "escalation_dispatching",
        },
    )
    db_session.add(message)
    await db_session.commit()

    response = await client.post(
        f"/api/v1/whatsapp/messages/{message.id}/inbound-reconciliation",
        json={"decision": "delivered", "reason": "Tentativa entre workspaces."},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "WA-RECON-001"
    await db_session.refresh(message)
    assert message.inbound_pipeline["reconciliationRequired"] is True
    assert message.inbound_pipeline["uncertainStage"] == "escalation_dispatching"


@pytest.mark.asyncio
async def test_send_message_uses_qr_strategy_for_qr_connection(client, db_session, monkeypatch, auth_context):
    monkeypatch.setattr(
        "app.services.whatsapp.message_service.external_delivery_allowed",
        lambda: True,
    )
    cipher = WhatsAppSecretCipher()
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.QR_CODE,
        is_default=True,
        instance_id="instance-123",
        instance_token_encrypted=cipher.encrypt("instance-secret"),
        qr_provider="evolution",
        qr_status="connected",
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(connection)
    await db_session.flush()

    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511998877665",
        name="Cliente QR",
    )
    db_session.add(contact)
    await db_session.commit()

    async def fake_qr_send(self, *, connection, instance_token, to, body):
        assert connection.instance_id == "instance-123"
        assert instance_token == "instance-secret"
        assert to == "5511998877665"
        assert body == "Oi do QR"
        return {"id": "qr-message-1"}

    async def forbidden_meta_send(*args, **kwargs):
        raise AssertionError("A strategy Meta não deveria ser usada para conexão QR.")

    monkeypatch.setattr(
        "app.services.whatsapp.qr_gateway_client.QrGatewayClient.send_text_message",
        fake_qr_send,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.send_text_message",
        forbidden_meta_send,
    )

    response = await client.post(
        "/api/v1/whatsapp/messages",
        json={"contactId": str(contact.id), "body": "Oi do QR"},
        headers={"Idempotency-Key": "test-qr-outbound-message"},
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["wamid"] == "qr-message-1"
    assert data["status"] == "sent"

    stored = (
        await db_session.execute(select(WhatsAppMessage).where(WhatsAppMessage.contact_id == contact.id))
    ).scalars().one()
    assert stored.whatsapp_connection_id == connection.id
    assert stored.status == WaMessageStatus.sent


@pytest.mark.asyncio
async def test_inbox_assign_and_handoff_updates_contact_and_matter(client, db_session, auth_context):
    second_user = User(
        name="Atendente Dois",
        email="atendente2@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="AD",
        color="#1f2937",
        is_active=True,
        is_internal_staff=False,
    )
    db_session.add(second_user)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=auth_context.workspace_id,
            user_id=second_user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
            is_default=False,
        )
    )
    client_record = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Inbox",
        email="cliente@example.com",
        phone="+5511998776655",
    )
    db_session.add(client_record)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client_record.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Rescisão indireta",
        column_id=KanbanColumnId.entrada,
        micro_status=MicroStatus.lead_novo,
    )
    db_session.add(matter)
    await db_session.flush()
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_inbox",
        phone_number="5511998776655",
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511998776655",
        name="Cliente Inbox",
        matter_id=matter.id,
    )
    db_session.add(contact)
    await db_session.commit()

    assign_response = await client.post(
        f"/api/v1/whatsapp/conversations/{contact.id}/assign",
        json={"userId": str(second_user.id)},
    )
    assert assign_response.status_code == 200, assign_response.text
    assign_payload = assign_response.json()
    assert assign_payload["contact"]["assignedUserId"] == str(second_user.id)
    assert assign_payload["contact"]["queueStatus"] == WaQueueStatus.human.value

    updated_matter = await db_session.get(Matter, matter.id)
    assert updated_matter is not None
    assert updated_matter.responsible_human_id == second_user.id

    handoff_response = await client.post(
        f"/api/v1/whatsapp/conversations/{contact.id}/handoff",
        json={
            "queueStatus": WaQueueStatus.follow_up.value,
            "humanControlState": HumanControlState.manual_pause.value,
        },
    )
    assert handoff_response.status_code == 200, handoff_response.text
    handoff_payload = handoff_response.json()
    assert handoff_payload["contact"]["queueStatus"] == WaQueueStatus.follow_up.value
    assert handoff_payload["contact"]["humanControlState"] == HumanControlState.manual_pause.value

    unassign_response = await client.post(
        f"/api/v1/whatsapp/conversations/{contact.id}/assign",
        json={"userId": None},
    )
    assert unassign_response.status_code == 200, unassign_response.text
    assert unassign_response.json()["contact"]["assignedUserId"] is None

    await db_session.refresh(updated_matter)
    assert updated_matter.responsible_human_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_name", "same_workspace", "membership_status", "user_is_active"),
    [
        ("other-workspace", False, MembershipStatus.active, True),
        ("suspended-membership", True, MembershipStatus.suspended, True),
        ("inactive-user", True, MembershipStatus.active, False),
    ],
)
async def test_inbox_assignment_rejects_ineligible_user_without_changing_records(
    client,
    db_session,
    auth_context,
    case_name,
    same_workspace,
    membership_status,
    user_is_active,
):
    membership_workspace_id = auth_context.workspace_id
    if not same_workspace:
        other_workspace = Workspace(
            name="Workspace Externo",
            slug=f"workspace-{case_name}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        db_session.add(other_workspace)
        await db_session.flush()
        membership_workspace_id = other_workspace.id

    target_user = User(
        name=f"Destino inválido {case_name}",
        email=f"{case_name}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="DI",
        color="#334155",
        is_active=user_is_active,
        is_internal_staff=False,
    )
    db_session.add(target_user)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=membership_workspace_id,
            user_id=target_user.id,
            role=WorkspaceRole.operator,
            status=membership_status,
            is_default=False,
        )
    )

    client_record = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Protegido",
        email="cliente-protegido@example.com",
        phone="+5511987654321",
    )
    db_session.add(client_record)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client_record.id,
        responsible_human_id=auth_context.user_id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Caso protegido",
        column_id=KanbanColumnId.entrada,
        micro_status=MicroStatus.lead_novo,
        next_action="Ação original do caso.",
    )
    db_session.add(matter)
    await db_session.flush()
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_assignment_guard",
        phone_number="5511987654321",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511987654321",
        name="Cliente Protegido",
        matter_id=matter.id,
        assigned_user_id=auth_context.user_id,
        queue_status=WaQueueStatus.follow_up,
        human_control_state=HumanControlState.manual_pause,
    )
    db_session.add(contact)
    await db_session.commit()

    contact_before = {
        column.key: getattr(contact, column.key) for column in WhatsAppContact.__table__.columns
    }
    matter_before = {column.key: getattr(matter, column.key) for column in Matter.__table__.columns}

    response = await client.post(
        f"/api/v1/whatsapp/conversations/{contact.id}/assign",
        json={"userId": str(target_user.id)},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == {
        "code": "WA-INBOX-001",
        "message": "O responsável deve ser um membro ativo deste workspace.",
    }
    assert {
        column.key: getattr(contact, column.key) for column in WhatsAppContact.__table__.columns
    } == contact_before
    matter_after = {
        column.key: getattr(matter, column.key) for column in Matter.__table__.columns
    }
    assert matter_after == matter_before
    timeline_events = (
        await db_session.execute(select(TimelineEvent).where(TimelineEvent.matter_id == matter.id))
    ).scalars().all()
    assert timeline_events == []


@pytest.mark.asyncio
async def test_inbox_assignment_rejects_legacy_contact_linked_to_foreign_matter(
    client,
    db_session,
    auth_context,
):
    foreign_workspace = Workspace(
        name="Workspace do card externo",
        slug="workspace-card-externo",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign_client = Client(
        workspace_id=foreign_workspace.id,
        name="Cliente externo",
        phone="+5511977770000",
    )
    db_session.add(foreign_client)
    await db_session.flush()
    foreign_matter = Matter(
        workspace_id=foreign_workspace.id,
        client_id=foreign_client.id,
        demand_type="Caso externo",
        next_action="Não alterar",
    )
    db_session.add(foreign_matter)
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_foreign_matter_guard",
        phone_number="5511966660000",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511966660000",
        matter_id=foreign_matter.id,
        queue_status=WaQueueStatus.follow_up,
    )
    db_session.add(contact)
    await db_session.commit()

    response = await client.post(
        f"/api/v1/whatsapp/conversations/{contact.id}/assign",
        json={"userId": str(auth_context.user_id)},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "WA-INBOX-002"
    await db_session.refresh(contact)
    await db_session.refresh(foreign_matter)
    assert contact.assigned_user_id is None
    assert contact.queue_status == WaQueueStatus.follow_up
    assert foreign_matter.next_action == "Não alterar"

    timeline_events = (
        await db_session.execute(
            select(TimelineEvent).where(TimelineEvent.matter_id == foreign_matter.id)
        )
    ).scalars().all()
    assert timeline_events == []


@pytest.mark.asyncio
async def test_message_templates_endpoint_returns_workspace_proposal_and_contract(client, db_session, auth_context):
    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    assert workspace is not None
    workspace.settings = {
        "messageTemplates": {
            "workspaceTemplates": [
                {
                    "id": "workspace-msg-1",
                    "kind": "message",
                    "category": "follow_up",
                    "label": "Cobrança leve",
                    "body": "Olá, {client_name}. Ficamos à disposição.",
                    "format": "text",
                }
            ],
            "proposalTemplates": [
                {
                    "id": "proposal-1",
                    "kind": "proposal",
                    "category": "comercial",
                    "label": "Proposta rápida",
                    "body": "Proposta para {client_name}",
                    "format": "pdf",
                }
            ],
            "contractTemplates": [
                {
                    "id": "contract-1",
                    "kind": "contract",
                    "category": "assinatura",
                    "label": "Contrato padrão",
                    "body": "Contrato para {client_name}",
                    "format": "pdf",
                }
            ],
        }
    }
    await db_session.commit()

    response = await client.get("/api/v1/whatsapp/templates")
    assert response.status_code == 200, response.text
    payload = response.json()
    kinds = {item["kind"] for item in payload}
    ids = {item["id"] for item in payload}
    assert "message" in kinds
    assert "proposal" in kinds
    assert "contract" in kinds
    assert "workspace-msg-1" in ids
    assert "proposal-1" in ids
    assert "contract-1" in ids


@pytest.mark.asyncio
async def test_apply_flow_updates_conversation_metadata(client, db_session, monkeypatch, auth_context):
    client_record = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Flow",
        email="flow@example.com",
        phone="+5511988887777",
    )
    db_session.add(client_record)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client_record.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Ação trabalhista",
        column_id=KanbanColumnId.entrada,
        micro_status=MicroStatus.lead_novo,
    )
    db_session.add(matter)
    await db_session.flush()
    template = Flow(
        workspace_id=None,
        name="Follow-up Trabalhista",
        description="Template de follow-up",
        practice_area="trabalhista",
        trigger="manual",
        is_template=True,
        is_active=True,
        nodes=[],
        edges=[],
    )
    db_session.add(template)
    await db_session.flush()
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_flow",
        phone_number="5511988887777",
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511988887777",
        name="Cliente Flow",
        matter_id=matter.id,
    )
    db_session.add(contact)
    await db_session.commit()

    created_flow = Flow(
        id=template.id,
        workspace_id=auth_context.workspace_id,
        name="Follow-up Aplicado",
        description="Cópia",
        practice_area="trabalhista",
        trigger="manual",
        is_template=False,
        is_active=False,
        nodes=[],
        edges=[],
    )

    async def fake_apply_template(
        self,
        template_id,
        workspace_id,
        custom_name=None,
        *,
        actor_user_id=None,
        activate=False,
        commit=True,
    ):
        assert template_id == template.id
        assert activate is True
        assert actor_user_id == auth_context.user_id
        assert commit is False
        return created_flow

    async def fake_start_flow_run(
        self,
        flow_id,
        matter_id,
        workspace_id,
        context=None,
        *,
        actor_user_id=None,
        commit=True,
        publish=True,
    ):
        assert flow_id == created_flow.id
        assert matter_id == matter.id
        assert workspace_id == auth_context.workspace_id
        assert actor_user_id == auth_context.user_id
        assert commit is False
        assert publish is False
        return None

    monkeypatch.setattr("app.services.flow_service.FlowService.apply_template", fake_apply_template)
    monkeypatch.setattr("app.services.flow_service.FlowService.start_flow_run", fake_start_flow_run)

    response = await client.post(
        f"/api/v1/whatsapp/conversations/{contact.id}/apply-flow",
        json={
            "templateId": str(template.id),
            "moveToFollowUp": True,
            "nextFollowUpAt": datetime.now(timezone.utc).isoformat(),
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["contact"]["activeFlowTemplateId"] == str(template.id)
    assert payload["contact"]["activeFlowLabel"] == "Follow-up Trabalhista"
    assert payload["contact"]["queueStatus"] == WaQueueStatus.follow_up.value


@pytest.mark.asyncio
async def test_apply_flow_rolls_back_contact_and_copied_flow_when_run_creation_fails(
    client,
    db_session,
    monkeypatch,
    auth_context,
):
    client_record = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Atomicidade",
        email="atomicidade@example.com",
        phone="+5511977776666",
    )
    db_session.add(client_record)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client_record.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Ação de atomicidade",
        column_id=KanbanColumnId.entrada,
        micro_status=MicroStatus.lead_novo,
    )
    db_session.add(matter)
    await db_session.flush()
    template = Flow(
        workspace_id=None,
        name="Template atômico",
        trigger="manual",
        trigger_config={},
        is_template=True,
        is_active=True,
        nodes=[{"id": "start", "type": "trigger", "config": {}}],
        edges=[],
    )
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_atomic",
        phone_number="5511977776666",
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add_all([template, connection])
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511977776666",
        name="Cliente Atomicidade",
        matter_id=matter.id,
    )
    db_session.add(contact)
    await db_session.commit()

    async def fake_apply_template(
        self,
        template_id,
        workspace_id,
        custom_name=None,
        *,
        actor_user_id=None,
        activate=False,
        commit=True,
    ):
        assert commit is False
        copied = Flow(
            workspace_id=workspace_id,
            name="Cópia que deve ser revertida",
            trigger="manual",
            trigger_config={},
            nodes=[{"id": "start", "type": "trigger", "config": {}}],
            edges=[],
            is_active=activate,
            is_template=False,
        )
        self.db.add(copied)
        await self.db.flush()
        return copied

    async def fail_start_flow_run(self, *args, **kwargs):
        assert kwargs["commit"] is False
        assert kwargs["publish"] is False
        raise RuntimeError("falha injetada antes do commit")

    publish = monkeypatch.setattr(
        "app.services.flow_service.FlowService.publish_initial_flow_run",
        lambda *args, **kwargs: pytest.fail("broker não pode ser chamado antes do commit"),
    )
    monkeypatch.setattr(
        "app.services.flow_service.FlowService.apply_template",
        fake_apply_template,
    )
    monkeypatch.setattr(
        "app.services.flow_service.FlowService.start_flow_run",
        fail_start_flow_run,
    )

    with pytest.raises(RuntimeError, match="falha injetada"):
        await client.post(
            f"/api/v1/whatsapp/conversations/{contact.id}/apply-flow",
            json={
                "templateId": str(template.id),
                "moveToFollowUp": True,
                "nextFollowUpAt": datetime.now(timezone.utc).isoformat(),
            },
        )

    await db_session.refresh(contact)
    await db_session.refresh(matter)
    orphan = await db_session.scalar(
        select(Flow).where(Flow.name == "Cópia que deve ser revertida")
    )
    assert publish is None
    assert contact.active_flow_template_id is None
    assert contact.active_flow_label is None
    assert contact.queue_status != WaQueueStatus.follow_up
    assert matter.next_action is None
    assert orphan is None


@pytest.mark.asyncio
async def test_webhook_escalates_follow_up_conversation_to_human(client, db_session, auth_context, monkeypatch):
    sent_notifications: list[str] = []
    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed",
        lambda: True,
    )

    async def fake_notify(self, contact_id, *, reason):
        sent_notifications.append(reason)

    monkeypatch.setattr(
        "app.services.operational_alert_service.OperationalAlertService.notify_human_takeover",
        fake_notify,
    )

    async def skip_sdr_for_escalation_test(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.services.sdr_intake_service.handle_inbound_lead",
        skip_sdr_for_escalation_test,
    )

    async def confirm_automation_reply(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        "app.services.whatsapp.message_service.WhatsAppMessageService.send_text_direct",
        confirm_automation_reply,
    )

    workspace_owner = await db_session.get(User, auth_context.user_id)
    assert workspace_owner is not None
    client_record = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Escalada",
        email="escalada@example.com",
        phone="+5511977776666",
    )
    db_session.add(client_record)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client_record.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Dúvida sobre contrato",
        responsible_human_id=workspace_owner.id,
        column_id=KanbanColumnId.proposta_fechamento,
        micro_status=MicroStatus.proposta_enviada,
    )
    db_session.add(matter)
    await db_session.flush()
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_follow_up",
        phone_number="5511977776666",
        status=WaConnectionStatus.connected,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511977776666",
        name="Cliente Escalada",
        matter_id=matter.id,
        queue_status=WaQueueStatus.follow_up,
    )
    db_session.add(contact)
    await db_session.commit()

    svc = WhatsAppService(db_session)
    await svc.process_webhook(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {
                                    "phone_number_id": "pn_follow_up",
                                    "display_phone_number": "+55 11 97777-6666",
                                },
                                "contacts": [{"wa_id": "5511977776666", "profile": {"name": "Cliente Escalada"}}],
                                "messages": [
                                    {
                                        "id": "wamid-follow-up-escalation",
                                        "from": "5511977776666",
                                        "timestamp": "1712345678",
                                        "type": "text",
                                        "text": {"body": "Oi, quero retomar isso com alguém do time"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }
    )

    await db_session.refresh(contact)
    assert contact.queue_status == WaQueueStatus.human
    assert contact.assigned_user_id == workspace_owner.id
    assert sent_notifications
