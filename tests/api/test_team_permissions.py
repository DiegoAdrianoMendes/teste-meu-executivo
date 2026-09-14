"""Permissões individuais e proteção dos dados comerciais dos leads."""
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select

import app.api.v1.endpoints.auth as auth_endpoints
from app.core.config import settings
from app.core.security import hash_password, verify_token
from app.core.token_store import is_token_revoked
from app.models.auth_security import AuthSecurityEvent
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.identity import InvitationStatus, WorkspaceInvitation
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter
from app.models.notification import Notification, NotificationType
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.services.identity_outbox_service import LocalIdentityOutboxService
from tests.identity_helpers import (
    create_team_invitation,
    invitation_headers,
    invite_and_accept_team_member,
)


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest_asyncio.fixture(autouse=True)
async def _privileged_local_outbox_client(client, local_outbox_headers, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_LOCAL_BOOTSTRAP_ENABLED", True)
    original = dict(client.headers)
    client.headers.update(local_outbox_headers)
    yield
    client.headers.clear()
    client.headers.update(original)


async def _create_team_member(client, **overrides):
    return await invite_and_accept_team_member(client, **overrides)


def _assume_identity(auth_context, member):
    auth_context.user_id = member["userId"]
    auth_context.membership_id = member["membershipId"]
    auth_context.role = WorkspaceRole(member["workspaceRole"])


@pytest.mark.asyncio
async def test_tracking_read_does_not_grant_event_ingestion(
    client,
    auth_context,
):
    manager, _ = await _create_team_member(
        client,
        name="Gestora Jurídica de Relatórios",
        email=f"relatorios-{uuid4().hex[:8]}@example.com",
        workspaceRole="legal_manager",
    )
    _assume_identity(auth_context, manager)

    summary = await client.get("/api/v1/klm/tracking/summary")
    write = await client.post(
        "/api/v1/klm/tracking/events",
        json={
            "eventName": "Lead",
            "channel": "google_search",
            "dedupeKey": "forbidden-tracking-write-1",
        },
    )

    assert summary.status_code == 200
    assert write.status_code == 403


@pytest.mark.asyncio
async def test_invited_operator_accepts_then_logs_in_with_lead_data_hidden(client, auth_context):
    member, request = await _create_team_member(client)

    assert member["workspaceRole"] == "operator"
    assert member["status"] == "active"
    assert member["canViewSensitiveLeadData"] is False
    assert not any(
        item["module"] == "lead_data" and item["action"] == "view"
        for item in member["permissions"]
    )

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": request["email"], "password": request["password"]},
    )
    assert login.status_code == 200, login.text
    session = login.json()["session"]
    assert session["activeRole"] == "operator"
    assert not any(item["module"] == "lead_data" for item in session["permissions"])

    _assume_identity(auth_context, member)
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["activeRole"] == "operator"


@pytest.mark.asyncio
async def test_owner_reads_only_own_pending_invitation_delivery_in_local_runtime(
    client,
    db_session,
    auth_context,
):
    response, _ = await create_team_invitation(
        client,
        email=f"gestora-qa-e2e-{uuid4().hex[:8]}@example.com",
    )
    assert response.status_code == 202, response.text
    invitation = response.json()

    delivery = await client.get(
        f"/api/v1/klm/local/invitations/{invitation['invitationId']}/delivery"
    )

    assert delivery.status_code == 200, delivery.text
    assert delivery.json()["id"] == invitation["invitationDeliveryId"]
    assert delivery.json()["purpose"] == "workspace_invitation"
    assert delivery.json()["link"].startswith(
        f"/accept-invitation/{invitation['invitationId']}?token="
    )
    event = await db_session.scalar(
        select(AuthSecurityEvent)
        .where(
            AuthSecurityEvent.event_type == "identity_outbox_opened",
            AuthSecurityEvent.actor_user_id == auth_context.user_id,
            AuthSecurityEvent.subject_user_id == UUID(invitation["userId"]),
        )
        .order_by(AuthSecurityEvent.occurred_at.desc())
    )
    assert event is not None
    assert event.administrative_session_id is None
    assert event.details["access_scope"] == "workspace_invitation_only"

    with pytest.raises(HTTPException) as hidden:
        await LocalIdentityOutboxService(db_session).read_workspace_invitation(
            UUID(invitation["invitationId"]),
            workspace_id=uuid4(),
            actor_user_id=auth_context.user_id,
        )
    assert hidden.value.status_code == 404


@pytest.mark.asyncio
async def test_workspace_invitation_delivery_is_absent_when_local_bootstrap_is_closed(
    client,
    monkeypatch,
):
    response, _ = await create_team_invitation(client)
    assert response.status_code == 202, response.text
    monkeypatch.setattr(settings, "KLM_LOCAL_BOOTSTRAP_ENABLED", False)

    hidden = await client.get(
        f"/api/v1/klm/local/invitations/{response.json()['invitationId']}/delivery"
    )

    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_local_verification_cleanup_removes_only_namespaced_qa_identity(
    client,
    db_session,
):
    qa_email = f"gestora-qa-e2e-{uuid4().hex[:8]}@example.com"
    member, request_payload = await _create_team_member(
        client,
        name="Gestora QA descartável",
        email=qa_email,
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": qa_email, "password": request_payload["password"]},
    )
    assert login.status_code == 200, login.text

    cleanup = await client.delete("/api/v1/klm/local/verification-artifacts")

    assert cleanup.status_code == 200, cleanup.text
    assert cleanup.json()["qaUsers"] == 1
    assert cleanup.json()["workspaceMemberships"] == 1
    assert cleanup.json()["workspaceInvitations"] == 1
    assert cleanup.json()["identityIdempotencyRecords"] >= 2
    retired_user = await db_session.get(User, UUID(member["userId"]))
    retired_membership = await db_session.get(
        WorkspaceMembership,
        UUID(member["membershipId"]),
    )
    assert retired_user is not None
    assert retired_user.is_active is False
    assert retired_user.deleted_at is not None
    assert retired_membership is not None
    assert retired_membership.status == MembershipStatus.suspended
    owner = await db_session.scalar(select(User).where(User.email == "teste@example.com"))
    assert owner is not None

    rejected = await client.post(
        "/api/v1/auth/login",
        json={"email": qa_email, "password": request_payload["password"]},
    )
    assert rejected.status_code == 401


@pytest.mark.asyncio
async def test_owner_can_grant_and_deny_sensitive_lead_access_per_user(client):
    member, _ = await _create_team_member(client)
    granted = await client.patch(
        f"/api/v1/admin/team-members/{member['membershipId']}",
        json={
            "permissionGrants": [
                {"module": "lead_data", "action": "view", "scope": "workspace"}
            ]
        },
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["canViewSensitiveLeadData"] is True

    manager, _ = await _create_team_member(
        client,
        name="Gestora Jurídica",
        email=f"gestora-{uuid4().hex[:8]}@example.com",
        workspaceRole="legal_manager",
    )
    assert manager["canViewSensitiveLeadData"] is True
    denied = await client.patch(
        f"/api/v1/admin/team-members/{manager['membershipId']}",
        json={
            "permissionDenials": [
                {"module": "lead_data", "action": "view", "scope": "workspace"}
            ]
        },
    )
    assert denied.status_code == 200, denied.text
    assert denied.json()["canViewSensitiveLeadData"] is False


@pytest.mark.asyncio
async def test_owner_can_persist_an_individual_permission_block_on_own_membership(client):
    members = await client.get("/api/v1/admin/team-members")
    assert members.status_code == 200, members.text
    owner = next(item for item in members.json() if item["workspaceRole"] == "workspace_owner")

    response = await client.patch(
        f"/api/v1/admin/team-members/{owner['membershipId']}",
        json={
            "permissionDenials": [
                {"module": "dashboard", "action": "view", "scope": "workspace"}
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["permissionDenials"] == [
        {"module": "dashboard", "action": "view", "scope": "workspace"}
    ]
    assert not any(
        item["module"] == "dashboard" and item["action"] == "view"
        for item in response.json()["permissions"]
    )


@pytest.mark.asyncio
async def test_unimplemented_record_scopes_are_rejected_and_legacy_grants_fail_closed(
    client,
    db_session,
    auth_context,
):
    member, _ = await _create_team_member(client)

    rejected = await client.patch(
        f"/api/v1/admin/team-members/{member['membershipId']}",
        json={
            "permissionGrants": [
                {"module": "integrations", "action": "view", "scope": "own"}
            ]
        },
    )
    assert rejected.status_code == 422, rejected.text

    membership = await db_session.get(
        WorkspaceMembership,
        UUID(member["membershipId"]),
    )
    assert membership is not None
    membership.permission_grants = [
        {"module": "integrations", "action": "view", "scope": "team"}
    ]
    await db_session.flush()
    _assume_identity(auth_context, member)

    protected = await client.get("/api/v1/integrations/public-api-tokens")
    assert protected.status_code == 403, protected.text


@pytest.mark.asyncio
async def test_operator_without_sensitive_permission_receives_masked_lead_surfaces(
    client,
    db_session,
    auth_context,
):
    member, _ = await _create_team_member(client)
    person = (
        await client.post(
            "/api/v1/clients",
            json={
                "name": "Ana Paula Ribeiro",
                "cpf": "123.456.789-10",
                "email": "ana.ribeiro@example.com",
                "phone": "+55 11 99999-1234",
                "employer": "Empresa Confidencial",
                "position": "Diretora",
            },
        )
    ).json()
    matter_response = await client.post(
        "/api/v1/matters",
        json={
            "clientId": person["id"],
            "practiceArea": "saude_suplementar",
            "demandType": "Negativa de medicamento",
            "columnId": "novo_lead",
            "microStatus": "lead_novo",
            "healthSubtheme": "medicamento_alto_custo",
            "healthObjectName": "Medicamento X",
            "leadScore": 82,
        },
    )
    assert matter_response.status_code == 201, matter_response.text
    matter = matter_response.json()
    thread_response = await client.post(
        "/api/v1/klm/omnichannel/threads",
        json={
            "matterId": matter["id"],
            "clientId": person["id"],
            "primaryChannel": "email",
            "subject": "Negativa de medicamento",
            "threadMetadata": {
                "clientName": "Ana Paula Ribeiro",
                "emails": ["ana.ribeiro@example.com"],
            },
        },
    )
    assert thread_response.status_code == 201, thread_response.text
    thread = thread_response.json()
    message_response = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "email",
            "direction": "inbound",
            "authorType": "client",
            "authorLabel": "Ana Paula Ribeiro",
            "body": "Meu e-mail é ana.ribeiro@example.com e meu telefone é +55 11 99999-1234.",
            "idempotencyKey": "privacy-message-1",
            "messageMetadata": {"senderEmail": "ana.ribeiro@example.com"},
        },
    )
    assert message_response.status_code == 201, message_response.text

    _assume_identity(auth_context, member)

    clients = await client.get("/api/v1/clients")
    assert clients.status_code == 200
    masked_client = clients.json()[0]
    assert masked_client["name"].startswith("Lead ")
    assert masked_client["cpf"] is None
    assert masked_client["email"] is None
    assert masked_client["phone"] == "Oculto pelo administrador"
    assert masked_client["employer"] is None
    assert masked_client["sensitiveDataHidden"] is True

    matters = await client.get("/api/v1/matters")
    assert matters.status_code == 200
    assert matters.json()[0]["client"]["sensitiveDataHidden"] is True

    funnel = await client.get("/api/v1/klm/funnel")
    assert funnel.status_code == 200
    assert funnel.json()["cards"][0]["name"].startswith("Lead ")
    assert funnel.json()["cards"][0]["sensitiveDataHidden"] is True

    threads = await client.get("/api/v1/klm/omnichannel/threads")
    assert threads.status_code == 200
    assert threads.json()[0]["threadMetadata"]["clientName"] == "[oculto]"
    assert threads.json()[0]["threadMetadata"]["emails"] == "[oculto]"

    messages = await client.get(f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages")
    assert messages.status_code == 200
    masked_message = messages.json()[0]
    assert masked_message["authorLabel"].startswith("Lead ")
    assert "ana.ribeiro@example.com" not in masked_message["body"]
    assert "99999-1234" not in masked_message["body"]
    assert masked_message["messageMetadata"]["senderEmail"] == "[oculto]"

    membership = await db_session.get(WorkspaceMembership, member["membershipId"])
    membership.permission_grants = [
        {"module": "lead_data", "action": "view", "scope": "workspace"}
    ]
    await db_session.flush()
    visible = await client.get(f"/api/v1/clients/{person['id']}")
    assert visible.status_code == 200
    assert visible.json()["name"] == "Ana Paula Ribeiro"
    assert visible.json()["email"] == "ana.ribeiro@example.com"


@pytest.mark.asyncio
async def test_hidden_operator_receives_masked_legal_artifacts_and_process_records(
    client,
    auth_context,
):
    member, _ = await _create_team_member(client)
    person = (
        await client.post(
            "/api/v1/clients",
            json={
                "name": "Paciente Confidencial",
                "email": "paciente.secreto@example.com",
                "phone": "+55 11 96666-5544",
            },
        )
    ).json()
    matter = (
        await client.post(
            "/api/v1/matters",
            json={
                "clientId": person["id"],
                "practiceArea": "saude_suplementar",
                "demandType": "Negativa de medicamento oncológico",
                "columnId": "novo_lead",
                "microStatus": "lead_novo",
            },
        )
    ).json()
    artifact = await client.post(
        "/api/v1/klm/legal-artifacts",
        json={
            "matterId": matter["id"],
            "kind": "petition",
            "title": "Petição da Paciente Confidencial",
            "content": {
                "body": "Paciente Confidencial usa paciente.secreto@example.com e +55 11 96666-5544.",
            },
        },
    )
    assert artifact.status_code == 201, artifact.text
    process = await client.post(
        "/api/v1/klm/process-monitoring",
        json={
            "matterId": matter["id"],
            "caseNumber": "1001234-56.2026.8.26.0100",
            "court": "Vara da Paciente Confidencial",
            "status": "liminar_pendente",
            "lastEventSummary": "Contato paciente.secreto@example.com; telefone +55 11 96666-5544.",
        },
    )
    assert process.status_code == 201, process.text
    event = await client.post(
        f"/api/v1/klm/process-monitoring/{process.json()['id']}/events",
        json={
            "title": "Movimentação da Paciente Confidencial",
            "description": "Ligar para +55 11 96666-5544.",
            "source": "manual_local",
        },
    )
    assert event.status_code == 200, event.text

    _assume_identity(auth_context, member)
    artifacts = await client.get(
        "/api/v1/klm/legal-artifacts",
        params={"matter_id": matter["id"]},
    )
    processes = await client.get(
        "/api/v1/klm/process-monitoring",
        params={"matter_id": matter["id"]},
    )
    assert artifacts.status_code == 200, artifacts.text
    assert processes.status_code == 200, processes.text

    masked_artifact = artifacts.json()[0]
    assert masked_artifact["sensitiveDataHidden"] is True
    assert masked_artifact["content"] == {"sensitiveDataHidden": True}
    assert masked_artifact["sourceSnapshot"] == {"sensitiveDataHidden": True}
    assert "Paciente Confidencial" not in masked_artifact["title"]

    masked_process = processes.json()[0]
    assert masked_process["sensitiveDataHidden"] is True
    assert masked_process["caseNumber"] == "Oculto pelo administrador"
    assert masked_process["court"] == "Dados processuais protegidos"
    assert masked_process["events"] == []

    combined = json.dumps(
        {"artifacts": artifacts.json(), "processes": processes.json()},
        ensure_ascii=False,
    ).lower()
    for secret in (
        "paciente confidencial",
        "paciente.secreto",
        "96666-5544",
        "1001234-56.2026.8.26.0100",
    ):
        assert secret not in combined


@pytest.mark.asyncio
async def test_sensitive_denial_closes_auxiliary_export_paths(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    restricted, _ = await _create_team_member(
        client,
        name="Administradora Restrita",
        email=f"admin-restrita-{uuid4().hex[:8]}@example.com",
        workspaceRole="workspace_admin",
        permissionDenials=[
            {"module": "lead_data", "action": "view", "scope": "workspace"}
        ],
    )
    person = (
        await client.post(
            "/api/v1/clients",
            json={
                "name": "Ana Paula Ribeiro",
                "cpf": "123.456.789-10",
                "email": "ana.ribeiro@example.com",
                "phone": "+55 11 99999-1234",
            },
        )
    ).json()
    matter = (
        await client.post(
            "/api/v1/matters",
            json={
                "clientId": person["id"],
                "practiceArea": "saude_suplementar",
                "demandType": "Negativa de medicamento",
                "columnId": "novo_lead",
                "microStatus": "lead_novo",
                "executiveSummary": "Ana Paula Ribeiro usa ana.ribeiro@example.com e +55 11 99999-1234.",
            },
        )
    ).json()

    task = await client.post(
        f"/api/v1/matters/{matter['id']}/tasks",
        json={
            "title": "Ligar para Ana Paula Ribeiro no +55 11 99999-1234",
            "description": "Confirmar ana.ribeiro@example.com",
        },
    )
    assert task.status_code == 201, task.text
    note = await client.post(
        f"/api/v1/matters/{matter['id']}/notes",
        json={"note": "Ana Paula Ribeiro confirmou +55 11 99999-1234."},
    )
    assert note.status_code == 201, note.text
    meeting = await client.post(
        "/api/v1/meetings",
        headers={"Idempotency-Key": f"meeting-permissions-{uuid4().hex}"},
        json={
            "matterId": matter["id"],
            "title": "Reunião com Ana Paula Ribeiro",
            "startsAt": "2027-01-10T14:00:00Z",
            "location": "Google Meet",
            "meetingUrl": "https://meet.google.com/ana-segredo",
            "notes": "Contato +55 11 99999-1234",
        },
    )
    assert meeting.status_code == 201, meeting.text
    finance = await client.post(
        "/api/v1/finance/entries",
        json={
            "matterId": matter["id"],
            "title": "Entrada Ana Paula Ribeiro",
            "description": "Cobrar em ana.ribeiro@example.com",
            "entryType": "receivable",
            "status": "pending",
            "externalRef": "external-ref-ana",
            "amount": 2500,
            "entryMetadata": {"phone": "+55 11 99999-1234"},
            "reminders": [
                {
                    "remindAt": "2027-01-09T14:00:00Z",
                    "channel": "whatsapp",
                    "message": "Ana, responda em +55 11 99999-1234",
                }
            ],
        },
    )
    assert finance.status_code == 201, finance.text
    # This test needs a persisted scheduled message to exercise the redaction
    # path. Permit only the in-process fixture creation; production delivery
    # remains protected by the runtime gate.
    monkeypatch.setattr(
        "app.services.flow_service.external_delivery_allowed", lambda: True
    )
    scheduled = await client.post(
        "/api/v1/flows/scheduled-messages",
        json={
            "matter_id": matter["id"],
            "contact_phone": "+55 11 99999-1234",
            "message": "Olá Ana Paula Ribeiro, escreva para ana.ribeiro@example.com",
            "scheduled_at": "2027-01-09T15:00:00Z",
        },
    )
    assert scheduled.status_code == 201, scheduled.text
    opt_in = await client.post(
        "/api/v1/whatsapp/opt-ins",
        json={
            "contactId": None,
            "phone": "+55 11 99999-1234",
            "channel": "whatsapp",
            "status": "opted_in",
            "details": {"email": "ana.ribeiro@example.com"},
        },
    )
    assert opt_in.status_code == 200, opt_in.text
    suppression = await client.post(
        "/api/v1/whatsapp/suppressions",
        json={
            "phone": "+55 11 99999-1234",
            "reason": "manual",
            "details": {"clientName": "Ana Paula Ribeiro"},
        },
    )
    assert suppression.status_code == 200, suppression.text
    intent = await client.post(
        "/api/v1/klm/external-intents",
        json={
            "matterId": matter["id"],
            "actionType": "send_message",
            "provider": "offline",
            "idempotencyKey": f"privacy-intent-{uuid4().hex[:8]}",
            "payload": {
                "name": "Ana Paula Ribeiro",
                "phone": "+55 11 99999-1234",
                "email": "ana.ribeiro@example.com",
            },
        },
    )
    assert intent.status_code == 201, intent.text

    document = Document(
        matter_id=UUID(matter["id"]),
        created_by=auth_context.user_id,
        name="CPF Ana Paula Ribeiro 123.456.789-10.pdf",
        doc_type=DocumentType.comprovante,
        status=DocumentStatus.completo,
        s3_key="clientes/ana/segredo.pdf",
        s3_url="https://s3.example.test/clientes/ana/segredo.pdf",
        mime_type="application/pdf",
    )
    notification = Notification(
        user_id=UUID(restricted["userId"]),
        workspace_id=auth_context.workspace_id,
        matter_id=UUID(matter["id"]),
        title="Ana Paula Ribeiro enviou documento",
        message="Responder ana.ribeiro@example.com ou +55 11 99999-1234",
        notification_type=NotificationType.info,
    )
    db_session.add_all([document, notification])
    persisted_matter = await db_session.get(Matter, UUID(matter["id"]))
    persisted_matter.sdr_qualification = {
        "client_name": "Ana Paula Ribeiro",
        "phone": "+55 11 99999-1234",
        "employer": "Empresa Secreta",
        "viability_notes": "Contato ana.ribeiro@example.com",
        "current_step": "qualificado",
        "completed": True,
    }
    await db_session.flush()

    _assume_identity(auth_context, restricted)
    responses = {
        "matter": await client.get(f"/api/v1/matters/{matter['id']}"),
        "documents": await client.get(f"/api/v1/matters/{matter['id']}/documents"),
        "tasks": await client.get(f"/api/v1/matters/{matter['id']}/tasks"),
        "notes": await client.get(f"/api/v1/matters/{matter['id']}/notes"),
        "audit": await client.get(f"/api/v1/matters/{matter['id']}/audit"),
        "meetings": await client.get("/api/v1/meetings"),
        "finance": await client.get("/api/v1/finance/entries"),
        "scheduled": await client.get("/api/v1/flows/scheduled-messages"),
        "opt_ins": await client.get("/api/v1/whatsapp/opt-ins"),
        "suppressions": await client.get("/api/v1/whatsapp/suppressions"),
        "notifications": await client.get("/api/v1/notifications"),
        "sdr": await client.get(f"/api/v1/sdr/matters/{matter['id']}"),
        "intents": await client.get("/api/v1/klm/external-intents"),
        "activity": await client.get("/api/v1/klm/activity"),
    }
    for label, response in responses.items():
        assert response.status_code == 200, f"{label}: {response.text}"

    payload = json.dumps(
        {label: response.json() for label, response in responses.items()},
        ensure_ascii=False,
    ).lower()
    for secret in (
        "ana paula",
        "ana.ribeiro",
        "99999-1234",
        "123.456.789-10",
        "meet.google.com/ana-segredo",
        "s3.example.test",
        "external-ref-ana",
        "empresa secreta",
    ):
        assert secret not in payload

    assert responses["matter"].json()["documents"][0]["url"] is None
    assert responses["scheduled"].json()[0]["contact_phone"] == "Oculto pelo administrador"
    assert responses["opt_ins"].json()[0]["phone"] == "Oculto pelo administrador"
    assert responses["sdr"].json()["qualification"]["phone"] is None
    assert responses["intents"].json()[0]["payload"] == {"sensitiveDataHidden": True}


@pytest.mark.asyncio
async def test_permission_denial_is_enforced_by_backend_not_only_ui(client, auth_context):
    member, _ = await _create_team_member(
        client,
        permissionDenials=[
            {"module": "kanban", "action": "view", "scope": "workspace"}
        ],
    )
    _assume_identity(auth_context, member)
    response = await client.get("/api/v1/matters")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AUTH-012"


@pytest.mark.asyncio
async def test_hidden_operator_cannot_probe_or_overwrite_contact_data(client, auth_context):
    member, _ = await _create_team_member(client)
    person = (
        await client.post(
            "/api/v1/clients",
            json={
                "name": "Contato Ultrassecreto",
                "cpf": "987.654.321-00",
                "email": "sigilo@example.com",
                "phone": "+55 11 97777-6666",
            },
        )
    ).json()
    matter = await client.post(
        "/api/v1/matters",
        json={
            "clientId": person["id"],
            "practiceArea": "saude_suplementar",
            "demandType": "Negativa de cirurgia cardíaca",
            "columnId": "novo_lead",
            "microStatus": "lead_novo",
            "leadSource": "google_ads",
        },
    )
    assert matter.status_code == 201

    _assume_identity(auth_context, member)
    probe = await client.get("/api/v1/clients", params={"search": "Ultrassecreto"})
    assert probe.status_code == 403
    assert probe.json()["detail"]["code"] == "PRIVACY-001"

    overwrite = await client.patch(
        f"/api/v1/clients/{person['id']}",
        json={"name": "Contato substituído"},
    )
    assert overwrite.status_code == 403
    assert overwrite.json()["detail"]["code"] == "AUTH-012"

    identity_search = await client.get("/api/v1/matters", params={"search": "Ultrassecreto"})
    assert identity_search.status_code == 200
    assert identity_search.json() == []

    safe_search = await client.get("/api/v1/matters", params={"search": "cirurgia cardíaca"})
    assert safe_search.status_code == 200
    assert len(safe_search.json()) == 1
    assert safe_search.json()[0]["client"]["sensitiveDataHidden"] is True


@pytest.mark.asyncio
async def test_sensitive_public_api_token_follows_creator_permissions(
    client,
    db_session,
    auth_context,
):
    admin, _ = await _create_team_member(
        client,
        name="Administradora de Integrações",
        email=f"integracoes-{uuid4().hex[:8]}@example.com",
        workspaceRole="workspace_admin",
    )
    owner_identity = (
        auth_context.user_id,
        auth_context.membership_id,
        auth_context.role,
    )
    _assume_identity(auth_context, admin)
    token_response = await client.post(
        "/api/v1/integrations/public-api-tokens",
        json={
            "label": "CRM externo sensível",
            "scopes": ["contacts:read"],
            "rateLimitPerMinute": 60,
        },
    )
    assert token_response.status_code == 201, token_response.text
    plain_token = token_response.json()["plainTextToken"]

    membership = await db_session.get(WorkspaceMembership, admin["membershipId"])
    membership.permission_denials = [
        {"module": "lead_data", "action": "view", "scope": "workspace"},
        {"module": "lead_data", "action": "edit", "scope": "workspace"},
    ]
    await db_session.flush()

    denied = await client.get(
        "/api/v1/public-api/contacts",
        headers={"X-KLM-API-Key": plain_token},
    )
    assert denied.status_code == 401
    assert denied.json()["detail"]["code"] == "PUBAPI-404"

    auth_context.user_id, auth_context.membership_id, auth_context.role = owner_identity
    restricted_create = await client.patch(
        f"/api/v1/admin/team-members/{admin['membershipId']}",
        json={
            "permissionDenials": [
                {"module": "lead_data", "action": "view", "scope": "workspace"},
                {"module": "lead_data", "action": "edit", "scope": "workspace"},
            ]
        },
    )
    assert restricted_create.status_code == 200

    _assume_identity(auth_context, restricted_create.json())
    create_after_restriction = await client.post(
        "/api/v1/integrations/public-api-tokens",
        json={
            "label": "Tentativa bloqueada",
            "scopes": ["conversations:read"],
            "rateLimitPerMinute": 60,
        },
    )
    assert create_after_restriction.status_code == 403
    assert create_after_restriction.json()["detail"]["code"] == "PUBAPI-407"


@pytest.mark.asyncio
async def test_suspension_revokes_workspace_access_immediately(client, auth_context):
    member, _ = await _create_team_member(client)
    suspended = await client.patch(
        f"/api/v1/admin/team-members/{member['membershipId']}",
        json={"status": "suspended"},
    )
    assert suspended.status_code == 200
    assert suspended.json()["isActive"] is False

    _assume_identity(auth_context, member)
    response = await client.get("/api/v1/clients")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AUTH-010"


@pytest.mark.asyncio
async def test_workspace_admin_cannot_change_member_password_or_revoke_identity_session(client):
    member, request = await _create_team_member(client)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": request["email"], "password": request["password"]},
    )
    assert login.status_code == 200
    token_id = verify_token(login.json()["accessToken"])["jti"]

    changed = await client.patch(
        f"/api/v1/users/{member['userId']}",
        json={"password": "SenhaLegadaNova#2026"},
    )

    assert changed.status_code == 403
    assert changed.json()["detail"]["code"] == "USR-003"
    assert await is_token_revoked(token_id) is False


@pytest.mark.asyncio
async def test_workspace_admin_cannot_create_owner_or_grant_permission_they_do_not_have(
    client,
    auth_context,
):
    admin, _ = await _create_team_member(
        client,
        name="Administrador Delegado",
        email=f"admin-{uuid4().hex[:8]}@example.com",
        workspaceRole="workspace_admin",
    )
    _assume_identity(auth_context, admin)

    owner_attempt = await client.post(
        "/api/v1/admin/team-members",
        json={
            "name": "Novo Titular",
            "email": f"owner-{uuid4().hex[:8]}@example.com",
            "workspaceRole": "workspace_owner",
        },
        headers=invitation_headers("owner-attempt"),
    )
    assert owner_attempt.status_code == 403
    assert owner_attempt.json()["detail"]["code"] == "TEAM-005"

    escalation_attempt = await client.post(
        "/api/v1/admin/team-members",
        json={
            "name": "Escalada Indevida",
            "email": f"escalada-{uuid4().hex[:8]}@example.com",
            "workspaceRole": "operator",
            "permissionGrants": [
                {"module": "workspace", "action": "manage", "scope": "workspace"}
            ],
        },
        headers=invitation_headers("escalation-attempt"),
    )
    assert escalation_attempt.status_code == 403
    assert escalation_attempt.json()["detail"]["code"] == "TEAM-009"


@pytest.mark.asyncio
async def test_membership_update_is_workspace_scoped_and_permission_audit_has_no_password(
    client,
    db_session,
    auth_context,
):
    member, member_request = await _create_team_member(client)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": member_request["email"], "password": member_request["password"]},
    )
    assert login.status_code == 200
    old_token_id = verify_token(login.json()["accessToken"])["jti"]
    rejected_identity_change = await client.patch(
        f"/api/v1/admin/team-members/{member['membershipId']}",
        json={"password": "OutraSenha#2026"},
    )
    assert rejected_identity_change.status_code == 422
    assert await is_token_revoked(old_token_id) is False

    updated = await client.patch(
        f"/api/v1/admin/team-members/{member['membershipId']}",
        json={"workspaceRole": "viewer"},
    )
    assert updated.status_code == 200, updated.text

    other_workspace = Workspace(
        name="Outro Workspace",
        slug=f"outro-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    other_user = User(
        name="Outro Usuário",
        email=f"outro-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="OU",
        color="#111827",
        is_active=True,
    )
    db_session.add_all([other_workspace, other_user])
    await db_session.flush()
    outside_membership = WorkspaceMembership(
        workspace_id=other_workspace.id,
        user_id=other_user.id,
        role=WorkspaceRole.operator,
        status=MembershipStatus.active,
    )
    db_session.add(outside_membership)
    await db_session.flush()

    cross_workspace = await client.patch(
        f"/api/v1/admin/memberships/{outside_membership.id}",
        json={"role": "viewer"},
    )
    assert cross_workspace.status_code == 404

    events = list(
        (
            await db_session.execute(
                select(KlmAuditEvent).where(
                    KlmAuditEvent.workspace_id == auth_context.workspace_id,
                    KlmAuditEvent.entity_id == member["membershipId"],
                )
            )
        ).scalars()
    )
    assert [event.event_type for event in events] == [
        "team.invitation.created",
        "team.member.updated",
    ]
    assert "passwordChanged" not in events[-1].after_data
    assert "OutraSenha" not in str(events[-1].after_data)


@pytest.mark.asyncio
async def test_invitation_replay_resend_rotation_and_revoke_are_idempotent(
    client,
):
    email = f"convite-{uuid4().hex[:8]}@example.com"
    key = f"create-invitation-{uuid4().hex}"
    first, _ = await create_team_invitation(
        client,
        idempotency_key=key,
        name="Pessoa Convidada",
        email=email,
        workspaceRole="operator",
    )
    replay, _ = await create_team_invitation(
        client,
        idempotency_key=key,
        name="Pessoa Convidada",
        email=email,
        workspaceRole="operator",
    )
    assert first.status_code == replay.status_code == 202
    assert replay.json()["invitationId"] == first.json()["invitationId"]
    assert replay.json()["membershipId"] == first.json()["membershipId"]
    invitation = first.json()

    admin_link = await client.get(
        f"/api/v1/admin/team-members/invitations/{invitation['invitationId']}/link"
    )
    assert admin_link.status_code == 200, admin_link.text
    assert admin_link.json()["link"].startswith(
        f"/accept-invitation/{invitation['invitationId']}?token="
    )

    old_delivery = await client.get(
        f"/api/v1/auth/local-outbox/{invitation['invitationDeliveryId']}"
    )
    old_token = parse_qs(urlsplit(old_delivery.json()["link"]).query)["token"][0]
    resent = await client.post(
        f"/api/v1/admin/team-members/invitations/{invitation['invitationId']}/resend",
        headers=invitation_headers("resend"),
    )
    assert resent.status_code == 200, resent.text
    assert resent.json()["invitationDeliveryId"] != invitation["invitationDeliveryId"]

    stale_accept = await client.post(
        f"/api/v1/auth/invitations/{invitation['invitationId']}/accept",
        json={"token": old_token, "password": "SenhaLocal#2026"},
        headers=invitation_headers("stale-accept"),
    )
    assert stale_accept.status_code == 400
    assert stale_accept.json()["detail"]["code"] == "AUTH-INVITATION-INVALID"

    revoked = await client.delete(
        f"/api/v1/admin/team-members/invitations/{invitation['invitationId']}"
    )
    replayed_revoke = await client.delete(
        f"/api/v1/admin/team-members/invitations/{invitation['invitationId']}"
    )
    assert revoked.status_code == replayed_revoke.status_code == 200
    assert revoked.json()["status"] == "suspended"
    assert revoked.json()["invitationStatus"] == "revoked"
    assert replayed_revoke.json()["invitationStatus"] == "revoked"
    reactivation = await client.patch(
        f"/api/v1/admin/team-members/{invitation['membershipId']}",
        json={"status": "active"},
    )
    assert reactivation.status_code == 409
    assert reactivation.json()["detail"]["code"] == "TEAM-INVITATION-REQUIRED"


@pytest.mark.asyncio
async def test_invitation_accept_replay_is_single_use_without_duplicate_membership(
    client,
    db_session,
):
    created, _ = await create_team_invitation(
        client,
        name="Nova Identidade",
        email=f"nova-identidade-{uuid4().hex[:8]}@example.com",
    )
    assert created.status_code == 202, created.text
    invitation = created.json()
    delivery = await client.get(
        f"/api/v1/auth/local-outbox/{invitation['invitationDeliveryId']}"
    )
    token = parse_qs(urlsplit(delivery.json()["link"]).query)["token"][0]
    key = f"accept-replay-{uuid4().hex}"
    body = {"token": token, "password": "SenhaLocal#2026"}

    accepted = await client.post(
        f"/api/v1/auth/invitations/{invitation['invitationId']}/accept",
        json=body,
        headers={"Idempotency-Key": key},
    )
    replay = await client.post(
        f"/api/v1/auth/invitations/{invitation['invitationId']}/accept",
        json=body,
        headers={"Idempotency-Key": key},
    )
    assert accepted.status_code == replay.status_code == 200
    assert replay.json() == accepted.json()
    consumed_with_new_key = await client.post(
        f"/api/v1/auth/invitations/{invitation['invitationId']}/accept",
        json=body,
        headers=invitation_headers("consumed-new-reservation"),
    )
    assert consumed_with_new_key.status_code == 400
    assert consumed_with_new_key.json()["detail"]["code"] == "AUTH-INVITATION-INVALID"
    membership_count = await db_session.scalar(
        select(func.count(WorkspaceMembership.id)).where(
            WorkspaceMembership.id == UUID(invitation["membershipId"])
        )
    )
    assert membership_count == 1


@pytest.mark.asyncio
async def test_invitation_code_is_delivery_bound_and_distributed_failures_revoke(
    client,
    db_session,
    monkeypatch,
):
    created, _ = await create_team_invitation(
        client,
        name="Codigo Protegido",
        email=f"codigo-{uuid4().hex[:8]}@example.com",
    )
    assert created.status_code == 202, created.text
    invitation = created.json()
    delivery = await client.get(
        f"/api/v1/auth/local-outbox/{invitation['invitationDeliveryId']}"
    )
    assert delivery.status_code == 200, delivery.text

    missing_binding = await client.post(
        f"/api/v1/auth/invitations/{invitation['invitationId']}/accept",
        json={"code": delivery.json()["code"], "password": "SenhaLocal#2026"},
        headers=invitation_headers("code-without-delivery"),
    )
    assert missing_binding.status_code == 422

    networks = iter(["192.0.2.10", "198.51.100.20", "203.0.113.30", "203.0.113.40"])
    monkeypatch.setattr(auth_endpoints, "request_network_key", lambda _request: next(networks))
    attempts = []
    for index in range(3):
        attempts.append(
            await client.post(
                f"/api/v1/auth/invitations/{invitation['invitationId']}/accept",
                json={
                    "deliveryId": invitation["invitationDeliveryId"],
                    "code": f"{index + 1:06d}",
                    "password": "SenhaLocal#2026",
                },
                headers=invitation_headers(f"distributed-failure-{index}"),
            )
        )
    assert [response.status_code for response in attempts] == [400, 400, 400]

    stored = await db_session.get(
        WorkspaceInvitation,
        UUID(invitation["invitationId"]),
    )
    membership = await db_session.get(
        WorkspaceMembership,
        UUID(invitation["membershipId"]),
    )
    await db_session.refresh(stored)
    await db_session.refresh(membership)
    assert stored.failed_attempt_count == 3
    assert stored.status == InvitationStatus.revoked
    assert stored.locked_at is not None
    assert stored.last_delivery_id is None
    assert membership.status == MembershipStatus.suspended

    blocked = await client.post(
        f"/api/v1/auth/invitations/{invitation['invitationId']}/accept",
        json={
            "deliveryId": invitation["invitationDeliveryId"],
            "code": delivery.json()["code"],
            "password": "SenhaLocal#2026",
        },
        headers=invitation_headers("distributed-blocked"),
    )
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "AUTH-RATE-LIMITED"


@pytest.mark.asyncio
async def test_expired_invitation_state_survives_rejected_accept_rollback(
    client,
    db_session,
):
    created, _ = await create_team_invitation(client)
    invitation = created.json()
    delivery = await client.get(
        f"/api/v1/auth/local-outbox/{invitation['invitationDeliveryId']}"
    )
    token = parse_qs(urlsplit(delivery.json()["link"]).query)["token"][0]
    stored = await db_session.get(
        WorkspaceInvitation,
        UUID(invitation["invitationId"]),
    )
    stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    rejected = await client.post(
        f"/api/v1/auth/invitations/{invitation['invitationId']}/accept",
        json={"token": token, "password": "SenhaLocal#2026"},
        headers=invitation_headers("expired-accept"),
    )

    assert rejected.status_code == 400
    assert rejected.json()["detail"]["code"] == "AUTH-INVITATION-INVALID"
    await db_session.refresh(stored)
    assert stored.status == InvitationStatus.expired
    assert stored.last_delivery_id is None


@pytest.mark.asyncio
async def test_admin_a_cannot_mutate_global_identity_or_membership_b(
    client,
    db_session,
    auth_context,
):
    member, credentials = await _create_team_member(client)
    user = await db_session.get(User, UUID(member["userId"]))
    original_identity = (user.email, user.hashed_password, user.is_active)

    workspace_b = Workspace(
        name="Workspace B",
        slug=f"workspace-b-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace_b)
    await db_session.flush()
    membership_b = WorkspaceMembership(
        workspace_id=workspace_b.id,
        user_id=user.id,
        role=WorkspaceRole.legal_manager,
        status=MembershipStatus.active,
        is_default=False,
    )
    db_session.add(membership_b)
    await db_session.flush()

    cross_tenant = await client.patch(
        f"/api/v1/admin/team-members/{membership_b.id}",
        json={"workspaceRole": "viewer", "status": "suspended"},
    )
    identity_takeover = await client.patch(
        f"/api/v1/users/{user.id}",
        json={
            "email": "admin-a-controlled@example.com",
            "password": "SenhaControlada#2026",
            "isActive": False,
        },
    )
    assert cross_tenant.status_code == 404
    assert identity_takeover.status_code == 403
    assert identity_takeover.json()["detail"]["code"] == "USR-003"

    suspended_a = await client.patch(
        f"/api/v1/admin/team-members/{member['membershipId']}",
        json={"status": "suspended"},
    )
    assert suspended_a.status_code == 200
    await db_session.refresh(user)
    await db_session.refresh(membership_b)
    assert (user.email, user.hashed_password, user.is_active) == original_identity
    assert membership_b.status == MembershipStatus.active
    assert membership_b.role == WorkspaceRole.legal_manager

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": credentials["email"], "password": credentials["password"]},
    )
    assert login.status_code == 200, login.text
    available_ids = {
        item["workspace"]["id"] for item in login.json()["session"]["availableWorkspaces"]
    }
    assert available_ids == {str(workspace_b.id)}
    assert str(auth_context.workspace_id) not in available_ids


@pytest.mark.asyncio
async def test_existing_identity_accepts_invite_without_admin_or_invitee_resetting_password(
    client,
    db_session,
):
    email = f"existing-{uuid4().hex[:8]}@example.com"
    existing_hash = hash_password("SenhaExistente#2026")
    existing = User(
        name="Identidade Existente",
        email=email,
        hashed_password=existing_hash,
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="IE",
        color="#111827",
        is_active=True,
    )
    db_session.add(existing)
    await db_session.flush()

    created, _ = await create_team_invitation(
        client,
        name="Nome fornecido pelo admin",
        email=email,
        workspaceRole="operator",
    )
    assert created.status_code == 202, created.text
    invitation = created.json()
    assert invitation["userId"] == str(existing.id)
    delivery = await client.get(
        f"/api/v1/auth/local-outbox/{invitation['invitationDeliveryId']}"
    )
    token = parse_qs(urlsplit(delivery.json()["link"]).query)["token"][0]
    accepted = await client.post(
        f"/api/v1/auth/invitations/{invitation['invitationId']}/accept",
        json={"token": token},
        headers=invitation_headers("existing-accept"),
    )
    assert accepted.status_code == 200, accepted.text
    await db_session.refresh(existing)
    assert existing.hashed_password == existing_hash
    assert existing.name == "Identidade Existente"

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "SenhaExistente#2026"},
    )
    assert login.status_code == 200, login.text


# --- V18: convite expirado deixa de ser beco sem saída ----------------------


@pytest.mark.asyncio
async def test_expired_invitation_is_reopened_on_resend_and_revoked_is_not(
    client,
    db_session,
):
    from datetime import UTC, datetime, timedelta

    from app.models.identity import WorkspaceInvitation

    created, _ = await create_team_invitation(
        client,
        name="Pessoa Expirada",
        email=f"expirado-{uuid4().hex[:8]}@example.com",
        workspaceRole="operator",
    )
    assert created.status_code == 202, created.text
    invitation = created.json()
    row = await db_session.get(WorkspaceInvitation, UUID(invitation["invitationId"]))
    row.expires_at = datetime.now(UTC) - timedelta(days=1)
    await db_session.flush()

    reopened = await client.post(
        f"/api/v1/admin/team-members/invitations/{invitation['invitationId']}/resend",
        headers=invitation_headers("resend-expired"),
    )
    assert reopened.status_code == 200, reopened.text
    body = reopened.json()
    assert body["invitationStatus"] == "pending"
    assert body["invitationDeliveryId"] != invitation["invitationDeliveryId"]
    expires_at = datetime.fromisoformat(body["invitationExpiresAt"].replace("Z", "+00:00"))
    assert expires_at > datetime.now(UTC)

    revoked = await client.delete(
        f"/api/v1/admin/team-members/invitations/{invitation['invitationId']}",
        headers=invitation_headers("revoke"),
    )
    assert revoked.status_code == 200, revoked.text
    after_revoke = await client.post(
        f"/api/v1/admin/team-members/invitations/{invitation['invitationId']}/resend",
        headers=invitation_headers("resend-revoked"),
    )
    assert after_revoke.status_code == 400
