"""Contratos end-to-end da camada operacional local do KLM Hub."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.agent_platform import AgentDeployment, DeploymentStatus
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.client import Client
from app.models.commercial_catalog import (
    CommercialOffer,
    CommercialOfferVersion,
    WorkspaceCommercialContract,
    WorkspaceContractLine,
)
from app.models.conversation_collaboration import (
    ConversationMention,
    ConversationMessageReference,
)
from app.models.document import Document, DocumentStatus
from app.models.finance import FinanceEntry
from app.models.finance_dimensional import FinanceAllocation, FinanceCostCenter
from app.models.flow import Flow
from app.models.honorarium_estimate import HonorariumEstimate
from app.models.klm_operations import (
    AcquisitionEvent,
    AreaBlueprint,
    AreaBlueprintStatus,
    EmailIngestReceipt,
    ExternalActionIntent,
    FollowUpStatus,
    FollowUpTask,
    KlmAuditEvent,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
    ThreadStatus,
)
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.matter import KanbanColumnId, Matter, PracticeArea
from app.models.meeting import Meeting, MeetingCreationReceipt, MeetingStatus
from app.models.notification import Notification
from app.models.omnichannel_v6 import (
    LeadCaptureReceipt,
    OmnichannelAutomationRule,
    OmnichannelChannelConnection,
    OmnichannelChannelEvent,
    OmnichannelEndpoint,
    OmnichannelIdentity,
    OmnichannelMessageEvidenceEvent,
    OmnichannelScheduledMessage,
    OmnichannelThreadBinding,
    WorkspaceTeam,
)
from app.models.revenue_projection import RevenueProjection
from app.models.storage_object import StorageObject, StorageObjectState
from app.models.subscription_commercial import SubscriptionEvent, WorkspaceSubscription
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.services.klm_agent_service import KlmAgentService
from app.services.omnichannel_delivery import ScheduledDispatchReceipt
from app.services.omnichannel_orchestration_service import (
    OmnichannelOrchestrationService,
)
from app.services.s3_service import S3Service

pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.fixture(autouse=True)
def _local_klm_runtime(monkeypatch):
    """Exercise the local-only KLM surface under its real environment contract.

    The CI suite sets ``APP_ENV=test`` globally, while these end-to-end tests
    intentionally bootstrap the credential-free local simulation. Individual
    fail-closed tests still override this fixture with ``production``.
    """
    monkeypatch.setattr(settings, "APP_ENV", "development")


class RecordingImmediateDispatcher:
    supports_idempotency = True

    def __init__(self) -> None:
        self.calls = []

    async def dispatch(self, request):
        self.calls.append(request)
        return ScheduledDispatchReceipt(
            provider="meta_cloud",
            external_message_id="wamid.immediate.accepted",
            provider_status="sent",
            external_event_id="meta-event-immediate-accepted",
        )


async def _seed_official_whatsapp_route(db_session, workspace_id):
    suffix = uuid4().hex[:12]
    connection = OmnichannelChannelConnection(
        workspace_id=workspace_id,
        channel="whatsapp",
        provider="meta_cloud",
        connection_key=f"whatsapp:test:{suffix}",
        display_name="WhatsApp oficial de teste",
        transport_mode="coexistence",
        status="connected",
        capabilities={},
        limits={},
        routing_policy={"serviceWindowHours": 24},
        cost_policy={},
        health={"state": "connected"},
        configuration={"phoneNumberId": f"phone-{suffix}"},
    )
    identity = OmnichannelIdentity(
        workspace_id=workspace_id,
        display_name="Contato WhatsApp",
        source="test",
        identity_metadata={},
    )
    db_session.add_all([connection, identity])
    await db_session.flush()
    endpoint = OmnichannelEndpoint(
        workspace_id=workspace_id,
        identity_id=identity.id,
        channel="whatsapp",
        provider="meta_cloud",
        connection_id=connection.id,
        connection_key=connection.connection_key,
        external_user_id=f"55119{suffix[:8]}",
        address=f"+55 11 9{suffix[:8]}",
        capabilities={},
        endpoint_metadata={},
    )
    db_session.add(endpoint)
    await db_session.flush()
    return connection, endpoint


async def _create_client(client, name="Ana Paula R.", email="ana@example.com"):
    response = await client.post(
        "/api/v1/clients",
        json={"name": name, "email": email, "phone": "+5511999999999"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_health_matter(client, client_id):
    response = await client.post(
        "/api/v1/matters",
        json={
            "clientId": client_id,
            "practiceArea": "saude_suplementar",
            "demandType": "Negativa de Zolgensma",
            "columnId": "novo_lead",
            "microStatus": "lead_novo",
            "healthSubtheme": "medicamento_alto_custo",
            "healthObjectName": "Zolgensma",
            "healthInsurer": "Plano Exemplo",
            "healthDenialReason": "Fora do rol",
            "medicalUrgencyHours": 72,
            "economicValueAnnual": 2100000,
            "leadScore": 94,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_thread(client, matter_id=None, client_id=None, channel="whatsapp"):
    response = await client.post(
        "/api/v1/klm/omnichannel/threads",
        json={
            "matterId": matter_id,
            "clientId": client_id,
            "primaryChannel": channel,
            "subject": "Medicamento de alto custo",
            "objectName": "Zolgensma",
            "ownerAgentKey": "sdr_saude",
            "score": 94,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_local_session(client):
    response = await client.post("/api/v1/klm/local/session")
    assert response.status_code == 200, response.text
    payload = response.json()
    token = payload["accessToken"]
    client.headers["Authorization"] = f"Bearer {token}"
    return payload


async def _move_funnel_card(client, funnel, card, column_id, position=0, operation_id=None):
    return await client.patch(
        f"/api/v1/klm/funnel/{card['id']}/stage",
        json={
            "columnId": column_id,
            "position": position,
            "sourceColumnId": card["stage"],
            "expectedBoardVersion": funnel["boardVersion"],
            "operationId": operation_id or str(uuid4()),
        },
    )


async def _create_outside_user(db_session):
    suffix = uuid4().hex[:8]
    workspace = Workspace(
        name="Workspace Externo",
        slug=f"workspace-externo-{suffix}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    user = User(
        name="Usuário Externo",
        email=f"externo-{suffix}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="UE",
        color="#111827",
        is_active=True,
        is_internal_staff=False,
    )
    db_session.add_all([workspace, user])
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
            is_default=True,
        )
    )
    await db_session.flush()
    return user


@pytest.mark.asyncio
async def test_runtime_is_fail_closed(client, monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "fake")
    response = await client.get("/api/v1/klm/runtime")
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "local_simulation"
    assert payload["localOnly"] is True
    assert payload["localInfrastructure"] is True
    assert payload["externalActionsEnabled"] is False
    assert payload["modelApisEnabled"] is False
    assert payload["awsEnabled"] is False
    assert payload["localBootstrapEnabled"] is True
    assert payload["providers"]["models"] == "local_fake"
    assert {state for provider, state in payload["providers"].items() if provider != "models"} == {
        "offline"
    }


@pytest.mark.asyncio
async def test_tool_catalog_exposes_governed_states(client):
    response = await client.get("/api/v1/klm/tools")
    assert response.status_code == 200
    tools = response.json()
    assert len(tools) >= 12
    assert any(item["key"] == "email_inbound" and item["state"] == "local_ready" for item in tools)
    assert any(
        item["key"] == "integrations" and item["state"] == "contract_local" for item in tools
    )
    assert any(
        item["key"] == "process_monitor" and item["state"] == "local_ready" for item in tools
    )


@pytest.mark.asyncio
async def test_area_registry_seeds_complete_catalog_frozen_by_default(client):
    response = await client.get("/api/v1/klm/areas")
    assert response.status_code == 200
    areas = response.json()
    assert len(areas) == 18
    active = [item for item in areas if item["isActive"]]
    assert active == []
    expected = {
        "saude_suplementar",
        "erro_medico",
        "bancario",
        "trabalhista",
        "previdenciario",
        "servidor_publico",
        "tributario",
        "consumidor",
        "digital",
        "propriedade_intelectual",
        "seguros",
        "familia",
        "empresarial",
        "imobiliario",
        "criminal",
        "sucessoes",
        "societario",
        "saude_publica_sus",
    }
    assert {item["areaKey"] for item in areas} == expected
    for area in areas:
        blueprint = area["blueprint"]
        assert blueprint["routing"]["coordinationAgentKey"] == "orquestrador"
        assert blueprint["routing"]["ownerBridgeAgentKey"] == "hermes"
        assert blueprint["routing"]["ownerBridgeDelegatorKey"] == "secretaria"
        assert blueprint["gates"]["humanHandoffCoordinator"] == "orquestrador"
        assert blueprint["agentGovernance"]["manualOverrideWins"] is True
        assert blueprint["agentGovernance"]["crossWorkspaceAccessDenied"] is True


@pytest.mark.asyncio
async def test_create_area_always_starts_non_active(client):
    area_key = f"custom_bancario_{uuid4().hex[:8]}"
    response = await client.post(
        "/api/v1/klm/areas",
        json={"areaKey": area_key, "name": "Direito bancário customizado", "status": "draft"},
    )
    assert response.status_code == 201
    assert response.json()["isActive"] is False


@pytest.mark.asyncio
async def test_duplicate_area_key_is_rejected(client):
    payload = {
        "areaKey": f"custom_duplicado_{uuid4().hex[:8]}",
        "name": "Direito bancário customizado",
        "status": "draft",
    }
    assert (await client.post("/api/v1/klm/areas", json=payload)).status_code == 201
    duplicate = await client.post("/api/v1/klm/areas", json=payload)
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_new_area_cannot_be_activated(client):
    response = await client.post(
        "/api/v1/klm/areas",
        json={
            "areaKey": f"custom_ativa_{uuid4().hex[:8]}",
            "name": "Área customizada ativa",
            "status": "active",
        },
    )
    assert response.status_code == 409
    assert "draft ou blueprint" in response.json()["detail"]


@pytest.mark.asyncio
async def test_complete_health_blueprint_can_be_validated_without_activation(client):
    areas = (await client.get("/api/v1/klm/areas")).json()
    health = next(item for item in areas if item["areaKey"] == "saude_suplementar")
    response = await client.post(f"/api/v1/klm/areas/{health['id']}/validate")
    assert response.status_code == 200
    assert response.json()["validatedAt"] is not None
    assert response.json()["isActive"] is False


@pytest.mark.asyncio
async def test_incomplete_custom_blueprint_fails_closed_on_creation(client):
    response = await client.post(
        "/api/v1/klm/areas",
        json={
            "areaKey": f"custom_incompleta_{uuid4().hex[:8]}",
            "name": "Área customizada incompleta",
            "blueprint": {
                "taxonomy": {"subthemes": ["outro"], "requiredFields": []},
                "documents": [],
                "funnel": [],
                "agentTeam": [],
                "gates": {"externalActionsDisabled": True},
            },
        },
    )
    assert response.status_code == 409
    assert "incompleto" in response.json()["detail"]


@pytest.mark.asyncio
async def test_health_matter_round_trips_structured_taxonomy(client):
    person = await _create_client(client)
    matter = await _create_health_matter(client, person["id"])
    assert matter["practiceArea"] == "saude_suplementar"
    assert matter["healthSubtheme"] == "medicamento_alto_custo"
    assert matter["healthObjectName"] == "Zolgensma"
    assert matter["medicalUrgencyHours"] == 72
    assert matter["economicValueAnnual"] == 2100000
    assert matter["leadScore"] == 94


@pytest.mark.asyncio
async def test_thread_rejects_matter_from_another_workspace(client):
    response = await client.post(
        "/api/v1/klm/omnichannel/threads",
        json={"matterId": str(uuid4()), "primaryChannel": "whatsapp"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_thread_list_is_tenant_scoped(client, db_session, auth_context):
    await _create_thread(client)
    other = Workspace(
        name="Outro Workspace",
        slug="outro-workspace",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        OmnichannelThread(
            workspace_id=other.id,
            primary_channel="email",
            subject="Não pode vazar",
        )
    )
    await db_session.flush()
    response = await client.get("/api/v1/klm/omnichannel/threads")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["subject"] != "Não pode vazar"


@pytest.mark.asyncio
async def test_thread_list_hides_thread_preserved_for_merged_identity(
    client,
    db_session,
    auth_context,
):
    active_identity = OmnichannelIdentity(
        workspace_id=auth_context.workspace_id,
        display_name="Contato canônico",
        canonical_phone="+5561982502136",
        identity_state="verified",
        source="meta_cloud",
        identity_metadata={},
    )
    merged_identity = OmnichannelIdentity(
        workspace_id=auth_context.workspace_id,
        display_name="Alias histórico",
        canonical_phone=None,
        identity_state="merged",
        source="meta_cloud",
        identity_metadata={"mergedIntoIdentityId": "pending-flush"},
    )
    db_session.add_all([active_identity, merged_identity])
    await db_session.flush()
    merged_identity.identity_metadata = {
        "mergedIntoIdentityId": str(active_identity.id),
    }
    active_thread = OmnichannelThread(
        workspace_id=auth_context.workspace_id,
        identity_id=active_identity.id,
        primary_channel="whatsapp",
        subject="Conversa principal",
    )
    preserved_thread = OmnichannelThread(
        workspace_id=auth_context.workspace_id,
        identity_id=merged_identity.id,
        primary_channel="whatsapp",
        status=ThreadStatus.closed,
        queue="merged",
        subject="Não deve virar segundo card",
    )
    orphan_thread = OmnichannelThread(
        workspace_id=auth_context.workspace_id,
        identity_id=uuid4(),
        primary_channel="whatsapp",
        subject="Vínculo órfão precisa continuar diagnosticável",
    )
    db_session.add_all([active_thread, preserved_thread, orphan_thread])
    await db_session.commit()

    response = await client.get("/api/v1/klm/omnichannel/threads")

    assert response.status_code == 200
    listed_ids = {item["id"] for item in response.json()}
    assert str(active_thread.id) in listed_ids
    assert str(orphan_thread.id) in listed_ids
    assert str(preserved_thread.id) not in listed_ids


@pytest.mark.asyncio
async def test_thread_list_applies_advanced_operational_filters(client, auth_context):
    first_client = await _create_client(client, "Lead prioritário", "prioritario@example.com")
    first_matter = await _create_health_matter(client, first_client["id"])
    matter_update = await client.patch(
        f"/api/v1/matters/{first_matter['id']}",
        json={
            "priority": "alta",
            "urgency": "urgente",
            "leadSource": "google_ads",
            "tags": ["oncologia"],
        },
    )
    assert matter_update.status_code == 200, matter_update.text
    first_thread = await _create_thread(client, first_matter["id"], first_client["id"])
    assigned = await client.patch(
        f"/api/v1/klm/omnichannel/threads/{first_thread['id']}",
        json={"assignedHumanId": str(auth_context.user_id), "queue": "closer", "priority": 90},
    )
    assert assigned.status_code == 200, assigned.text

    second_client = await _create_client(client, "Lead comum", "comum@example.com")
    second_matter = await _create_health_matter(client, second_client["id"])
    second_thread = await _create_thread(client, second_matter["id"], second_client["id"])
    lowered = await client.patch(
        f"/api/v1/klm/omnichannel/threads/{second_thread['id']}",
        json={"score": 40, "queue": "triagem"},
    )
    assert lowered.status_code == 200, lowered.text

    response = await client.get(
        "/api/v1/klm/omnichannel/threads",
        params={
            "score_min": 90,
            "thread_priority_min": 90,
            "thread_priority_max": 90,
            "assigned_human_id": str(auth_context.user_id),
            "queue": "closer",
            "matter_priority": "alta",
            "urgency": "urgente",
            "health_subtheme": "medicamento_alto_custo",
            "demand_type": "medicamento_alto_custo",
            "lead_source": "google_ads",
            "tag": "oncologia",
            "sort_by": "score",
        },
    )
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == [first_thread["id"]]


@pytest.mark.asyncio
async def test_case_360_inline_updates_are_audited_and_visible_to_agents(client):
    person = await _create_client(client, "Nome antes", "antes@example.com")
    matter = await _create_health_matter(client, person["id"])

    updated = await client.patch(
        f"/api/v1/matters/{matter['id']}/case-360",
        json={
            "client": {"name": "Nome corrigido", "phone": "+5511987654321"},
            "attribution": {
                "source": "meta_ads",
                "channel": "instagram",
                "campaign": "oncologia-julho",
            },
            "matter": {"nextAction": "Revisar relatório médico", "priority": "alta"},
        },
    )
    assert updated.status_code == 200, updated.text
    payload = updated.json()
    assert payload["client"]["name"] == "Nome corrigido"
    assert payload["campaignAttribution"]["channel"] == "instagram"
    assert payload["campaignAttribution"]["campaign"] == "oncologia-julho"
    assert payload["nextAction"] == "Revisar relatório médico"
    assert payload["manualContextRevision"] >= 1

    changes = await client.get(f"/api/v1/matters/{matter['id']}/field-changes")
    assert changes.status_code == 200, changes.text
    changed_fields = {item["fieldName"] for item in changes.json()}
    assert {"client.name", "campaign_attribution.channel", "next_action"}.issubset(changed_fields)

    moved = await client.put(
        f"/api/v1/matters/{matter['id']}/move",
        json={"columnId": "triagem_sdr", "microStatus": "qualificado"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["manualContextRevision"] > payload["manualContextRevision"]


@pytest.mark.asyncio
async def test_outbound_message_becomes_local_draft_and_intent(client, db_session):
    thread = await _create_thread(client)
    response = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "whatsapp",
            "direction": "outbound",
            "authorType": "human",
            "authorLabel": "Dr. Kleistr",
            "body": "Mensagem local de teste.",
            "idempotencyKey": "msg-out-1",
        },
    )
    assert response.status_code == 201
    assert response.json()["deliveryStatus"] == "local_draft"
    assert response.json()["authorType"] == "human"
    assert response.json()["authorLabel"] == "Usuário Teste"
    assert response.json()["authorUserId"] is not None
    intents = (await client.get("/api/v1/klm/external-intents")).json()
    assert len(intents) == 1
    assert intents[0]["status"] == "blocked_external"


@pytest.mark.asyncio
async def test_message_atomically_materializes_reply_mentions_and_notification(
    client,
    db_session,
    auth_context,
):
    thread = await _create_thread(client)
    target = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "whatsapp",
            "direction": "inbound",
            "authorType": "client",
            "authorLabel": "Ana",
            "body": "Mensagem que precisa de revisão.",
            "externalMessageId": "wamid.atomic.target",
            "idempotencyKey": "msg-atomic-target",
        },
    )
    assert target.status_code == 201, target.text

    payload = {
        "channel": "internal",
        "direction": "internal",
        "authorType": "human",
        "authorLabel": "Será normalizado",
        "body": "Revise este contexto, por favor.",
        "idempotencyKey": "msg-atomic-collaboration",
        "messageMetadata": {
            "collaboration": {
                "schemaVersion": "klm.conversation-collaboration.v1",
                "replyToMessageId": target.json()["id"],
                "referenceType": "reply",
                "mentions": [
                    {
                        "mentionType": "user",
                        "targetId": str(auth_context.user_id),
                        "resolvedLabel": "Rótulo do cliente não é confiado",
                    }
                ],
            }
        },
    }
    created = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json=payload,
    )
    replay = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json=payload,
    )

    assert created.status_code == replay.status_code == 201
    assert replay.headers["X-KLM-Deduplicated"] == "true"
    assert replay.json()["id"] == created.json()["id"]
    reference = await db_session.scalar(
        select(ConversationMessageReference).where(
            ConversationMessageReference.source_message_id == created.json()["id"]
        )
    )
    assert reference is not None
    assert str(reference.target_message_id) == target.json()["id"]
    assert (
        int(
            await db_session.scalar(
                select(func.count())
                .select_from(ConversationMention)
                .where(ConversationMention.source_message_id == created.json()["id"])
            )
            or 0
        )
        == 1
    )
    assert (
        int(
            await db_session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.workspace_id == auth_context.workspace_id,
                    Notification.user_id == auth_context.user_id,
                )
            )
            or 0
        )
        == 1
    )

    structured_replay = await client.post(
        f"/api/v1/klm/omnichannel/collaboration/threads/{thread['id']}/references",
        json={
            "sourceMessageId": created.json()["id"],
            "targetMessageId": target.json()["id"],
            "referenceType": "reply",
            "mentions": [{"mentionType": "user", "targetId": str(auth_context.user_id)}],
            "idempotencyKey": (f"spa-reference:{thread['id']}:{created.json()['id']}"),
        },
    )
    assert structured_replay.status_code == 201, structured_replay.text
    assert structured_replay.headers["X-KLM-Deduplicated"] == "true"


@pytest.mark.asyncio
async def test_invalid_structured_mention_rolls_back_canonical_message(
    client,
    db_session,
):
    thread = await _create_thread(client)
    response = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "internal",
            "direction": "internal",
            "authorType": "human",
            "authorLabel": "Será normalizado",
            "body": "Esta mensagem não pode ficar órfã.",
            "idempotencyKey": "msg-invalid-mention-atomic",
            "collaboration": {"mentions": [{"mentionType": "user", "targetId": str(uuid4())}]},
        },
    )

    assert response.status_code == 404, response.text
    assert (
        await db_session.scalar(
            select(OmnichannelMessage.id).where(
                OmnichannelMessage.idempotency_key == "msg-invalid-mention-atomic"
            )
        )
        is None
    )


@pytest.mark.asyncio
async def test_immediate_composer_uses_official_dispatcher_once_when_gate_is_open(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    thread = await _create_thread(client)
    target = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "whatsapp",
            "direction": "inbound",
            "authorType": "client",
            "authorLabel": "Ana",
            "body": "Mensagem oficial de origem.",
            "externalMessageId": "wamid.immediate.target",
            "idempotencyKey": "msg-immediate-target",
        },
    )
    assert target.status_code == 201, target.text
    _connection, endpoint = await _seed_official_whatsapp_route(
        db_session,
        auth_context.workspace_id,
    )
    dispatcher = RecordingImmediateDispatcher()
    monkeypatch.setattr(
        "app.services.klm_operations_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.omnichannel_orchestration_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        OmnichannelOrchestrationService,
        "_official_whatsapp_dispatcher",
        lambda _service, _connection: dispatcher,
    )
    payload = {
        "endpointId": str(endpoint.id),
        "channel": "whatsapp",
        "direction": "outbound",
        "authorType": "human",
        "authorLabel": "Será normalizado",
        "body": "Resposta enviada pelo compositor canônico.",
        "idempotencyKey": "msg-immediate-official-once",
        "collaboration": {
            "replyToMessageId": target.json()["id"],
            "mentions": [{"mentionType": "user", "targetId": str(auth_context.user_id)}],
        },
    }

    created = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json=payload,
    )
    replay = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json=payload,
    )

    assert created.status_code == replay.status_code == 201
    assert created.headers["X-KLM-Dispatch-State"] == "sent"
    assert created.headers["X-KLM-External-Actions-Executed"] == "1"
    assert created.json()["deliveryStatus"] == "sent"
    assert created.json()["externalMessageId"] == "wamid.immediate.accepted"
    assert replay.headers["X-KLM-Deduplicated"] == "true"
    assert replay.headers["X-KLM-Dispatch-State"] == "sent"
    assert len(dispatcher.calls) == 1
    assert (
        dispatcher.calls[0].message_payload["collaboration"]["replyToMessageId"]
        == target.json()["id"]
    )


@pytest.mark.asyncio
async def test_immediate_composer_replay_resumes_before_attempt_checkpoint(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    thread = await _create_thread(client)
    _connection, endpoint = await _seed_official_whatsapp_route(
        db_session,
        auth_context.workspace_id,
    )
    monkeypatch.setattr(
        "app.services.klm_operations_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.omnichannel_orchestration_service.external_delivery_allowed",
        lambda: True,
    )
    original_dispatch = OmnichannelOrchestrationService.dispatch_immediate_message

    async def crash_before_attempt_checkpoint(_service, **_kwargs):
        raise RuntimeError("injected crash before provider checkpoint")

    monkeypatch.setattr(
        OmnichannelOrchestrationService,
        "dispatch_immediate_message",
        crash_before_attempt_checkpoint,
    )
    payload = {
        "endpointId": str(endpoint.id),
        "channel": "whatsapp",
        "direction": "outbound",
        "authorType": "human",
        "authorLabel": "Será normalizado",
        "body": "Mensagem recuperável antes do checkpoint.",
        "idempotencyKey": "msg-immediate-resume-before-checkpoint",
    }
    with pytest.raises(RuntimeError, match="injected crash"):
        await client.post(
            f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
            json=payload,
        )

    dispatcher = RecordingImmediateDispatcher()
    monkeypatch.setattr(
        OmnichannelOrchestrationService,
        "dispatch_immediate_message",
        original_dispatch,
    )
    monkeypatch.setattr(
        OmnichannelOrchestrationService,
        "_official_whatsapp_dispatcher",
        lambda _service, _connection: dispatcher,
    )
    replay = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json=payload,
    )

    assert replay.status_code == 201, replay.text
    assert replay.headers["X-KLM-Deduplicated"] == "true"
    assert replay.headers["X-KLM-Dispatch-State"] == "sent"
    assert replay.json()["deliveryStatus"] == "sent"
    assert len(dispatcher.calls) == 1


@pytest.mark.asyncio
async def test_immediate_composer_stays_local_if_gate_closes_before_dispatch(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    thread = await _create_thread(client)
    _connection, endpoint = await _seed_official_whatsapp_route(
        db_session,
        auth_context.workspace_id,
    )
    monkeypatch.setattr(
        "app.services.klm_operations_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.omnichannel_orchestration_service.external_delivery_allowed",
        lambda: False,
    )

    def forbidden_dispatcher(_service, _connection):
        raise AssertionError("closed gate must not instantiate the provider dispatcher")

    monkeypatch.setattr(
        OmnichannelOrchestrationService,
        "_official_whatsapp_dispatcher",
        forbidden_dispatcher,
    )
    created = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "endpointId": str(endpoint.id),
            "channel": "whatsapp",
            "direction": "outbound",
            "authorType": "human",
            "authorLabel": "Será normalizado",
            "body": "Permanece local se o gate fechar.",
            "idempotencyKey": "msg-immediate-gate-race",
        },
    )

    assert created.status_code == 201, created.text
    assert created.headers["X-KLM-Dispatch-State"] == "local_only"
    assert created.headers["X-KLM-External-Actions-Executed"] == "0"
    assert created.json()["deliveryStatus"] == "local_draft"
    assert created.json()["providerStatus"] == "local_only"


@pytest.mark.asyncio
async def test_scheduled_message_preserves_validated_collaboration_context(
    client,
    db_session,
    auth_context,
):
    thread = await _create_thread(client)
    target = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "whatsapp",
            "direction": "inbound",
            "authorType": "client",
            "authorLabel": "Ana",
            "body": "Mensagem que receberá resposta agendada.",
            "externalMessageId": "wamid.scheduled.target",
            "idempotencyKey": "msg-scheduled-target",
        },
    )
    assert target.status_code == 201, target.text
    _connection, endpoint = await _seed_official_whatsapp_route(
        db_session,
        auth_context.workspace_id,
    )

    created = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "endpointId": str(endpoint.id),
            "channel": "whatsapp",
            "direction": "outbound",
            "authorType": "human",
            "authorLabel": "Será normalizado",
            "body": "Resposta contextual agendada.",
            "scheduledAt": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            "timezone": "America/Sao_Paulo",
            "idempotencyKey": "msg-scheduled-context",
            "messageMetadata": {
                "collaboration": {
                    "schemaVersion": "klm.conversation-collaboration.v1",
                    "replyToMessageId": target.json()["id"],
                    "referenceType": "reply",
                    "mentions": [
                        {
                            "mentionType": "user",
                            "targetId": str(auth_context.user_id),
                        }
                    ],
                }
            },
        },
    )

    assert created.status_code == 201, created.text
    assert created.headers["X-KLM-Dispatch-State"] == "scheduled"
    assert created.headers["X-KLM-External-Actions-Executed"] == "0"
    canonical_message_id = UUID(created.json()["id"])
    scheduled = await db_session.scalar(
        select(OmnichannelScheduledMessage).where(
            OmnichannelScheduledMessage.canonical_message_id == canonical_message_id
        )
    )
    assert scheduled is not None
    assert scheduled.state == "scheduled"
    assert scheduled.message_payload["collaboration"]["replyToMessageId"] == (target.json()["id"])
    assert scheduled.provider_receipt["collaborationValidated"] is True
    reference = await db_session.scalar(
        select(ConversationMessageReference).where(
            ConversationMessageReference.source_message_id == canonical_message_id
        )
    )
    assert reference is not None
    assert str(reference.target_message_id) == target.json()["id"]


@pytest.mark.asyncio
async def test_message_evidence_is_available_and_exportable_without_leaving_thread(client):
    thread = await _create_thread(client)
    created = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "whatsapp",
            "direction": "inbound",
            "authorType": "client",
            "authorLabel": "Ana",
            "body": "O plano apagou a resposta, mas eu havia recebido a negativa.",
            "idempotencyKey": "msg-evidence-api-1",
        },
    )
    assert created.status_code == 201, created.text
    message = created.json()
    assert message["evidenceEventCount"] == 1
    assert message["evidenceRevisionCount"] == 1

    evidence = await client.get(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages/{message['id']}/evidence"
    )
    assert evidence.status_code == 200, evidence.text
    chain = evidence.json()
    assert chain["integrityStatus"] == "verified"
    assert chain["originalSnapshot"]["bodySnapshot"] == message["body"]
    assert chain["events"][0]["eventType"] == "captured"

    exported = await client.get(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages/{message['id']}/evidence/export"
    )
    assert exported.status_code == 200, exported.text
    manifest = exported.json()
    assert manifest["schemaVersion"] == "klm-message-evidence-export/v1"
    assert len(manifest["manifestSha256"]) == 64
    assert manifest["rawProviderEnvelopeIncluded"] is False

    wrong_thread = await _create_thread(client, channel="email")
    hidden = await client.get(
        f"/api/v1/klm/omnichannel/threads/{wrong_thread['id']}/messages/{message['id']}/evidence"
    )
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_message_endpoint_rejects_external_audio_metadata(client):
    thread = await _create_thread(client)
    response = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "whatsapp",
            "direction": "outbound",
            "authorType": "human",
            "authorLabel": "Identidade será substituída",
            "body": "Mensagem com mídia não confiável.",
            "idempotencyKey": "msg-external-audio-rejected",
            "messageMetadata": {"audioUrl": "https://attacker.example/audio.mp3"},
        },
    )

    assert response.status_code == 422
    assert "ticket temporário" in response.json()["detail"]


@pytest.mark.asyncio
async def test_message_endpoint_accepts_private_local_audio_ticket(client):
    thread = await _create_thread(client)
    response = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "whatsapp",
            "direction": "outbound",
            "authorType": "human",
            "authorLabel": "Identidade será substituída",
            "body": "Mensagem com mídia privada.",
            "idempotencyKey": "msg-private-audio-accepted",
            "messageMetadata": {
                "audioUrl": (
                    f"{settings.PAPERCLIP_PUBLIC_URL}/api/v1/local-storage/download"
                    "?ticket=temporary-local-ticket"
                )
            },
        },
    )

    assert response.status_code == 201
    assert response.json()["messageMetadata"]["audioUrl"].endswith("ticket=temporary-local-ticket")


@pytest.mark.asyncio
async def test_message_endpoint_rejects_forged_agent_identity_outside_local_runtime(
    client,
    monkeypatch,
):
    thread = await _create_thread(client)
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "online")
    monkeypatch.setattr(settings, "KLM_LOCAL_BOOTSTRAP_ENABLED", False)

    response = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "internal",
            "direction": "internal",
            "authorType": "agent",
            "authorLabel": "Agente forjado",
            "body": "Tentativa de forjar autoria.",
            "idempotencyKey": "forged-agent-identity",
        },
    )

    assert response.status_code == 422
    assert "serviços internos autenticados" in response.json()["detail"]


@pytest.mark.asyncio
async def test_message_idempotency_returns_same_record(client):
    thread = await _create_thread(client)
    payload = {
        "channel": "email",
        "direction": "inbound",
        "authorType": "client",
        "authorLabel": "Ana",
        "body": "Enviei a negativa.",
        "idempotencyKey": "msg-in-1",
    }
    first = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages", json=payload
    )
    second = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages", json=payload
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.headers["X-KLM-Deduplicated"] == "true"
    messages = (await client.get(f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages")).json()
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_message_idempotency_rejects_changed_payload_and_other_thread(client):
    first_thread = await _create_thread(client)
    second_thread = await _create_thread(client, channel="email")
    payload = {
        "channel": "email",
        "direction": "inbound",
        "authorType": "client",
        "authorLabel": "Ana",
        "body": "Enviei a negativa.",
        "idempotencyKey": "msg-conflict-1",
        "messageMetadata": {"provider": "email"},
    }

    first = await client.post(
        f"/api/v1/klm/omnichannel/threads/{first_thread['id']}/messages",
        json=payload,
    )
    changed = await client.post(
        f"/api/v1/klm/omnichannel/threads/{first_thread['id']}/messages",
        json={**payload, "body": "Conteúdo diferente."},
    )
    other_thread = await client.post(
        f"/api/v1/klm/omnichannel/threads/{second_thread['id']}/messages",
        json=payload,
    )

    assert first.status_code == 201, first.text
    assert changed.status_code == 409, changed.text
    assert other_thread.status_code == 409, other_thread.text
    assert "outra mensagem ou conversa" in changed.json()["detail"]
    assert "outra mensagem ou conversa" in other_thread.json()["detail"]
    first_messages = (
        await client.get(f"/api/v1/klm/omnichannel/threads/{first_thread['id']}/messages")
    ).json()
    second_messages = (
        await client.get(f"/api/v1/klm/omnichannel/threads/{second_thread['id']}/messages")
    ).json()
    assert [message["body"] for message in first_messages] == [payload["body"]]
    assert second_messages == []


@pytest.mark.asyncio
async def test_older_message_does_not_replace_latest_thread_preview(client):
    thread = await _create_thread(client)
    now = datetime.now(UTC)
    latest = {
        "channel": "whatsapp",
        "direction": "inbound",
        "authorType": "client",
        "authorLabel": "Ana",
        "body": "Mensagem mais recente.",
        "idempotencyKey": "msg-preview-latest",
        "occurredAt": now.isoformat(),
    }
    older = {
        **latest,
        "body": "Mensagem recebida fora de ordem.",
        "idempotencyKey": "msg-preview-older",
        "occurredAt": (now - timedelta(hours=1)).isoformat(),
    }

    assert (
        await client.post(
            f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
            json=latest,
        )
    ).status_code == 201
    assert (
        await client.post(
            f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
            json=older,
        )
    ).status_code == 201

    listed = (await client.get("/api/v1/klm/omnichannel/threads")).json()
    loaded = next(item for item in listed if item["id"] == thread["id"])
    assert loaded["lastMessagePreview"] == latest["body"]
    assert loaded["unreadCount"] == 2


@pytest.mark.asyncio
async def test_inbound_message_increments_unread_count(client):
    thread = await _create_thread(client)
    await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "whatsapp",
            "direction": "inbound",
            "authorType": "client",
            "authorLabel": "Ana",
            "body": "Tenho os documentos.",
            "idempotencyKey": "msg-in-unread",
        },
    )
    listed = (await client.get("/api/v1/klm/omnichannel/threads")).json()
    assert listed[0]["unreadCount"] == 1


@pytest.mark.asyncio
async def test_email_ingest_creates_case_documents_rag_and_follow_up(client, db_session):
    response = await client.post(
        "/api/v1/klm/omnichannel/email-ingest",
        json={
            "messageId": "email-provider-1",
            "senderEmail": "novo.lead@example.com",
            "senderName": "Novo Lead",
            "subject": "Negativa de medicamento",
            "body": "O plano negou o medicamento prescrito.",
            "attachments": [
                {
                    "name": "negativa.pdf",
                    "mimeType": "application/pdf",
                    "contentText": "Negativa do plano por ausência no rol.",
                    "documentType": "outro",
                },
                {
                    "name": "prescricao.pdf",
                    "mimeType": "application/pdf",
                    "contentText": "Prescrição do medicamento Zolgensma.",
                    "documentType": "outro",
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["deduplicated"] is False
    assert len(payload["documentIds"]) == 2
    assert len(payload["knowledgeChunkIds"]) == 2
    matter = await db_session.get(Matter, payload["matterId"])
    assert matter.practice_area == PracticeArea.saude_suplementar
    chunks = int(
        await db_session.scalar(
            select(func.count(KnowledgeChunk.id)).where(KnowledgeChunk.matter_id == matter.id)
        )
        or 0
    )
    assert chunks == 3
    chunk_sources = set(
        (
            await db_session.scalars(
                select(KnowledgeChunk.source_type).where(KnowledgeChunk.matter_id == matter.id)
            )
        ).all()
    )
    assert chunk_sources == {"conversation_message", "email_attachment"}
    documents = list(
        (
            await db_session.scalars(
                select(Document).where(Document.matter_id == matter.id).order_by(Document.name)
            )
        ).all()
    )
    assert len(documents) == 2
    assert all(document.status is DocumentStatus.aguardando for document in documents)
    assert all(document.s3_key is None for document in documents)
    assert all(document.s3_url is None for document in documents)
    message = await db_session.get(OmnichannelMessage, payload["message"]["id"])
    assert message is not None
    assert {
        attachment["storageState"] for attachment in message.message_metadata["attachments"]
    } == {"awaiting_binary"}
    assert {attachment["documentId"] for attachment in message.message_metadata["attachments"]} == {
        str(document.id) for document in documents
    }
    follow_ups = (await client.get("/api/v1/klm/follow-ups")).json()
    assert len(follow_ups) == 1
    assert follow_ups[0]["policyKey"] == "lead_never_unanswered"


@pytest.mark.asyncio
async def test_email_ingest_is_idempotent_without_duplicate_documents(client, db_session):
    payload = {
        "messageId": "email-provider-idem",
        "senderEmail": "idem@example.com",
        "subject": "Negativa de exame",
        "body": "Segue a negativa.",
        "attachments": [{"name": "negativa.pdf", "contentText": "Conteúdo da negativa."}],
    }
    first = await client.post("/api/v1/klm/omnichannel/email-ingest", json=payload)
    second = await client.post("/api/v1/klm/omnichannel/email-ingest", json=payload)
    assert first.status_code == second.status_code == 201
    assert second.json()["deduplicated"] is True
    assert first.json()["documentIds"] == second.json()["documentIds"]
    documents = int(await db_session.scalar(select(func.count()).select_from(Document)) or 0)
    messages = int(
        await db_session.scalar(select(func.count()).select_from(OmnichannelMessage)) or 0
    )
    receipts = int(
        await db_session.scalar(select(func.count()).select_from(EmailIngestReceipt)) or 0
    )
    assert documents == messages == receipts == 1


@pytest.mark.asyncio
async def test_email_ingest_rejects_same_provider_id_with_different_payload(client, db_session):
    payload = {
        "messageId": "email-provider-conflict",
        "senderEmail": "conflict@example.com",
        "subject": "Negativa de exame",
        "body": "Primeiro conteúdo.",
        "attachments": [{"name": "negativa.pdf", "contentText": "Negativa original."}],
    }
    first = await client.post("/api/v1/klm/omnichannel/email-ingest", json=payload)
    conflict = await client.post(
        "/api/v1/klm/omnichannel/email-ingest",
        json={**payload, "body": "Conteúdo alterado com o mesmo identificador."},
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert "conteúdo diferente" in conflict.json()["detail"]
    assert int(await db_session.scalar(select(func.count()).select_from(Document)) or 0) == 1
    assert (
        int(await db_session.scalar(select(func.count()).select_from(OmnichannelMessage)) or 0) == 1
    )
    assert (
        int(await db_session.scalar(select(func.count()).select_from(EmailIngestReceipt)) or 0) == 1
    )


@pytest.mark.asyncio
async def test_email_ingest_backfills_receipt_for_legacy_message(client, db_session):
    legacy_client = await _create_client(
        client,
        name="Legacy Lead",
        email="legacy.email@example.com",
    )
    matter = await _create_health_matter(client, legacy_client["id"])
    thread = await _create_thread(
        client,
        matter_id=matter["id"],
        client_id=legacy_client["id"],
        channel="email",
    )
    recorded = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "email",
            "direction": "inbound",
            "authorType": "client",
            "authorLabel": "Legacy Lead",
            "body": "O plano negou o tratamento.",
            "externalMessageId": "legacy-provider-1",
            "idempotencyKey": "email:legacy-provider-1",
            "messageMetadata": {
                "senderEmail": "legacy.email@example.com",
                "subject": "Negativa legada",
                "documentIds": [],
                "knowledgeChunkIds": [],
            },
        },
    )
    assert recorded.status_code == 201

    replay = await client.post(
        "/api/v1/klm/omnichannel/email-ingest",
        json={
            "messageId": "legacy-provider-1",
            "senderEmail": "legacy.email@example.com",
            "senderName": "Legacy Lead",
            "subject": "Negativa legada",
            "body": "O plano negou o tratamento.",
            "clientId": legacy_client["id"],
            "matterId": matter["id"],
        },
    )

    assert replay.status_code == 201, replay.text
    assert replay.json()["deduplicated"] is True
    assert replay.json()["thread"]["id"] == thread["id"]
    assert replay.json()["message"]["id"] == recorded.json()["id"]
    assert (
        int(await db_session.scalar(select(func.count()).select_from(EmailIngestReceipt)) or 0) == 1
    )
    assert (
        int(await db_session.scalar(select(func.count()).select_from(OmnichannelMessage)) or 0) == 1
    )


@pytest.mark.asyncio
async def test_follow_up_cannot_stop_without_client_block(client):
    thread = await _create_thread(client)
    response = await client.post(
        f"/api/v1/klm/follow-ups/threads/{thread['id']}/suppress",
        json={"reason": "Lead não respondeu", "blockedByClient": False},
    )
    assert response.status_code == 409
    assert "só pode parar" in response.json()["detail"]


@pytest.mark.asyncio
async def test_follow_up_stops_after_explicit_block(client, db_session):
    thread = await _create_thread(client)
    due = datetime.now(UTC) + timedelta(hours=1)
    created = await client.post(
        "/api/v1/klm/follow-ups",
        json={"threadId": thread["id"], "channel": "whatsapp", "dueAt": due.isoformat()},
    )
    assert created.status_code == 201
    response = await client.post(
        f"/api/v1/klm/follow-ups/threads/{thread['id']}/suppress",
        json={"reason": "Cliente bloqueou o número", "blockedByClient": True},
    )
    assert response.status_code == 200
    assert response.json()["suppressed"] == 1
    thread_row = await db_session.get(OmnichannelThread, thread["id"])
    assert thread_row.status == ThreadStatus.blocked
    task = await db_session.scalar(select(FollowUpTask))
    assert task.status == FollowUpStatus.suppressed


@pytest.mark.asyncio
async def test_follow_up_update_persists_status_and_audit(client, db_session):
    thread = await _create_thread(client)
    original_due = datetime.now(UTC) + timedelta(hours=1)
    created = await client.post(
        "/api/v1/klm/follow-ups",
        json={
            "threadId": thread["id"],
            "channel": "whatsapp",
            "dueAt": original_due.isoformat(),
            "reason": "Retomar contato",
        },
    )
    assert created.status_code == 201, created.text

    rescheduled_due = datetime.now(UTC) + timedelta(days=2)
    updated = await client.patch(
        f"/api/v1/klm/follow-ups/{created.json()['id']}",
        json={
            "dueAt": rescheduled_due.isoformat(),
            "reason": "  Confirmar documentos pendentes  ",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["reason"] == "Confirmar documentos pendentes"
    assert updated.json()["status"] == "scheduled"
    assert datetime.fromisoformat(updated.json()["dueAt"]) == rescheduled_due

    task = await db_session.get(FollowUpTask, UUID(created.json()["id"]))
    assert task is not None
    assert task.reason == "Confirmar documentos pendentes"
    assert task.due_at.replace(tzinfo=UTC) == rescheduled_due

    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.entity_id == task.id,
            KlmAuditEvent.event_type == "follow_up_updated",
        )
    )
    assert audit is not None
    assert audit.before_data["reason"] == "Retomar contato"
    assert audit.after_data["reason"] == "Confirmar documentos pendentes"
    assert audit.after_data["dueAt"] == rescheduled_due.isoformat()

    completed = await client.patch(
        f"/api/v1/klm/follow-ups/{task.id}",
        json={"status": "completed"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"

    terminal_edit = await client.patch(
        f"/api/v1/klm/follow-ups/{task.id}",
        json={"reason": "Não deve persistir"},
    )
    assert terminal_edit.status_code == 409
    assert "agendados ou vencidos" in terminal_edit.json()["detail"]


@pytest.mark.asyncio
async def test_follow_up_update_is_workspace_scoped_and_rejects_internal_statuses(
    client,
    db_session,
):
    other_workspace = Workspace(
        name="Workspace externo",
        slug=f"follow-up-externo-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    external = FollowUpTask(
        workspace_id=other_workspace.id,
        channel=OmnichannelChannel.whatsapp,
        due_at=datetime.now(UTC) + timedelta(hours=2),
        reason="Follow-up de outro tenant",
        payload={},
    )
    db_session.add(external)
    await db_session.flush()

    cross_workspace = await client.patch(
        f"/api/v1/klm/follow-ups/{external.id}",
        json={"reason": "Tentativa indevida"},
    )
    assert cross_workspace.status_code == 404

    thread = await _create_thread(client)
    created = await client.post(
        "/api/v1/klm/follow-ups",
        json={
            "threadId": thread["id"],
            "channel": "whatsapp",
            "dueAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    invalid_status = await client.patch(
        f"/api/v1/klm/follow-ups/{created.json()['id']}",
        json={"status": "suppressed"},
    )
    assert invalid_status.status_code == 409
    assert "não pode ser definido manualmente" in invalid_status.json()["detail"]

    empty_update = await client.patch(
        f"/api/v1/klm/follow-ups/{created.json()['id']}",
        json={},
    )
    assert empty_update.status_code == 422


@pytest.mark.asyncio
async def test_follow_up_update_requires_send_message_permission(
    client,
    db_session,
    auth_context,
):
    thread = await _create_thread(client)
    created = await client.post(
        "/api/v1/klm/follow-ups",
        json={
            "threadId": thread["id"],
            "channel": "whatsapp",
            "dueAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    membership = await db_session.get(
        WorkspaceMembership,
        auth_context.membership_id,
    )
    assert membership is not None
    membership.role = WorkspaceRole.viewer
    await db_session.flush()

    denied = await client.patch(
        f"/api/v1/klm/follow-ups/{created.json()['id']}",
        json={"reason": "Sem permissão"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "AUTH-012"


@pytest.mark.asyncio
async def test_tracking_event_is_idempotent(client):
    payload = {
        "eventName": "Lead",
        "channel": "google_search",
        "source": "google",
        "campaign": "negativa_medicamento",
        "dedupeKey": "lead:gclid-123",
        "clickIds": {"gclid": "gclid-123"},
    }
    first = await client.post("/api/v1/klm/tracking/events", json=payload)
    second = await client.post("/api/v1/klm/tracking/events", json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.headers["X-KLM-Deduplicated"] == "true"

    conflict = await client.post(
        "/api/v1/klm/tracking/events",
        json={**payload, "campaign": "outra_campanha"},
    )
    assert conflict.status_code == 409
    assert "outro evento" in conflict.json()["detail"]


@pytest.mark.asyncio
async def test_tracking_summary_groups_channels_and_coverage(client):
    for index, event_name in enumerate(["PageView", "Lead", "Client", "PaymentConfirmed"]):
        response = await client.post(
            "/api/v1/klm/tracking/events",
            json={
                "eventName": event_name,
                "channel": "google_search",
                "dedupeKey": f"event-{index}",
                "value": 7500 if event_name == "PaymentConfirmed" else 0,
            },
        )
        assert response.status_code == 201
    summary = (await client.get("/api/v1/klm/tracking/summary")).json()
    assert summary["totalEvents"] == 4
    assert summary["byChannel"][0]["leads"] == 1
    # "clients" agora conta toda conversão atribuída (TRACKING_CONVERSION_EVENTS),
    # a mesma definição usada em "vendas atribuídas": Client + PaymentConfirmed = 2.
    assert summary["byChannel"][0]["clients"] == 2
    assert summary["byChannel"][0]["value"] == 7500


@pytest.mark.asyncio
async def test_technical_request_lifecycle(client):
    created = await client.post(
        "/api/v1/klm/technical-requests",
        json={
            "title": "Corrigir chat mobile",
            "description": "O composer deve permanecer visível.",
            "route": "/app/conversas",
            "severity": "high",
        },
    )
    assert created.status_code == 201
    request_id = created.json()["id"]
    updated = await client.patch(
        f"/api/v1/klm/technical-requests/{request_id}",
        json={"status": "patch_ready", "resolution": "Patch local pronto para validar."},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "patch_ready"
    assert len((await client.get("/api/v1/klm/technical-requests")).json()) == 1


@pytest.mark.asyncio
async def test_activity_records_human_actions(client):
    await client.post(
        "/api/v1/klm/technical-requests",
        json={"title": "Auditar rota", "description": "Verificar a rota mobile."},
    )
    activity = (await client.get("/api/v1/klm/activity")).json()
    assert activity
    assert activity[0]["eventType"] == "technical_request_created"
    assert activity[0]["actorType"] == "human"


@pytest.mark.asyncio
async def test_external_intent_never_executes_before_approval(client):
    created = await client.post(
        "/api/v1/klm/external-intents",
        json={
            "actionType": "send_contract",
            "provider": "zapsign",
            "idempotencyKey": "contract-ana-1",
            "payload": {"document": "contrato-local"},
        },
    )
    assert created.status_code == 201
    assert created.json()["status"] == "blocked_external"
    approved = await client.post(
        f"/api/v1/klm/external-intents/{created.json()['id']}/approve-local"
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved_local"
    assert "continua bloqueada" in approved.json()["reason"]


@pytest.mark.asyncio
async def test_external_intent_can_be_rejected_locally_without_execution(client):
    created = await client.post(
        "/api/v1/klm/external-intents",
        json={
            "actionType": "sync_audience",
            "provider": "meta",
            "idempotencyKey": "audience-reject-local-1",
            "payload": {"externalActionsExecuted": 0},
        },
    )
    rejected = await client.post(
        f"/api/v1/klm/external-intents/{created.json()['id']}/reject-local"
    )

    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "cancelled"
    assert "nenhuma entrega externa" in rejected.json()["reason"]
    activity = (await client.get("/api/v1/klm/activity")).json()
    assert any(item["eventType"] == "external_intent_rejected_local" for item in activity)


@pytest.mark.asyncio
async def test_external_intent_is_idempotent(client):
    payload = {
        "actionType": "schedule_meeting",
        "provider": "google_calendar",
        "idempotencyKey": "meeting-ana-17h",
    }
    first = await client.post("/api/v1/klm/external-intents", json=payload)
    second = await client.post("/api/v1/klm/external-intents", json=payload)
    assert first.json()["id"] == second.json()["id"]
    assert second.headers["X-KLM-Deduplicated"] == "true"

    conflict = await client.post(
        "/api/v1/klm/external-intents",
        json={**payload, "payload": {"slot": "18:00"}},
    )
    assert conflict.status_code == 409
    assert "outra intenção" in conflict.json()["detail"]


@pytest.mark.asyncio
async def test_dashboard_reports_zero_external_actions(client):
    person = await _create_client(client)
    await _create_health_matter(client, person["id"])
    dashboard = await client.get("/api/v1/klm/dashboard")
    assert dashboard.status_code == 200, dashboard.text
    payload = dashboard.json()
    assert payload["metrics"]["leads"] == 1
    assert payload["metrics"]["qualified"] == 1
    assert payload["metrics"]["externalActions"] == 0
    assert payload["runtime"]["localOnly"] is True


@pytest.mark.asyncio
async def test_local_session_bootstraps_customer_jwt_and_workspace(
    unauthenticated_client,
):
    payload = await _create_local_session(unauthenticated_client)
    assert payload["tokenType"] == "bearer"
    assert payload["user"]["email"] == "local@klmadvogados.com.br"
    assert payload["user"]["isPlatformAdmin"] is False
    assert payload["user"]["isInternalStaff"] is False
    assert payload["user"]["platformPermissions"] == []
    assert payload["session"]["surface"] == "workspace"
    assert payload["session"]["activeWorkspace"]["slug"] == "klm-hub-local"
    assert payload["session"]["activeWorkspace"]["onboardingCompleted"] is False
    runtime = await unauthenticated_client.get("/api/v1/klm/runtime")
    assert runtime.status_code == 200


@pytest.mark.asyncio
async def test_platform_sessions_are_isolated_from_customer_workspaces(
    unauthenticated_client,
    db_session,
):
    owner = await unauthenticated_client.post("/api/v1/klm/local/platform-session")
    assert owner.status_code == 200, owner.text
    owner_payload = owner.json()
    assert owner_payload["session"]["surface"] == "platform"
    assert owner_payload["session"]["activeWorkspace"] is None
    assert owner_payload["user"]["isPlatformAdmin"] is True
    assert owner_payload["user"]["isInternalStaff"] is True
    assert owner_payload["user"]["platformRolePackage"] == "platform_owner"

    expected_staff_identities = (
        ("platform_admin", "administracao@meuexecutivo.local"),
        ("sales_sdr", "vendas@meuexecutivo.local"),
        ("commercial_manager", "comercial@meuexecutivo.local"),
        ("onboarding", "onboarding@meuexecutivo.local"),
        ("customer_success", "cs@meuexecutivo.local"),
        ("support", "suporte@meuexecutivo.local"),
        ("technical_support", "suporte.ti@meuexecutivo.local"),
        ("finance_billing", "financeiro@meuexecutivo.local"),
        ("marketing_content", "marketing@meuexecutivo.local"),
        ("community_manager", "comunidade@meuexecutivo.local"),
        ("compliance_security", "compliance@meuexecutivo.local"),
        ("product_operations", "produto@meuexecutivo.local"),
        ("engineering", "ti@meuexecutivo.local"),
    )
    for role_package, email in expected_staff_identities:
        response = await unauthenticated_client.post(
            f"/api/v1/klm/local/platform-session/{role_package}"
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["session"]["surface"] == "platform"
        assert payload["session"]["activeWorkspace"] is None
        assert payload["user"]["email"] == email
        assert payload["user"]["isPlatformAdmin"] is False
        assert payload["user"]["isInternalStaff"] is True
        assert payload["user"]["platformRolePackage"] == role_package

    invalid = await unauthenticated_client.post(
        "/api/v1/klm/local/platform-session/funcao_inexistente"
    )
    assert invalid.status_code == 404

    platform_user_ids = select(User.id).where(User.is_internal_staff.is_(True))
    platform_memberships = await db_session.scalar(
        select(func.count(WorkspaceMembership.id)).where(
            WorkspaceMembership.user_id.in_(platform_user_ids)
        )
    )
    assert int(platform_memberships or 0) == 0
    assert int(await db_session.scalar(select(func.count(WorkspaceSubscription.id))) or 0) == 1
    assert int(await db_session.scalar(select(func.count(SubscriptionEvent.id))) or 0) == 1


@pytest.mark.asyncio
async def test_local_session_bootstrap_seeds_documents_through_storage_objects(
    unauthenticated_client,
    db_session,
    monkeypatch,
):
    def reject_direct_upload(*_args, **_kwargs):
        raise AssertionError("bootstrap não pode usar upload_bytes")

    monkeypatch.setattr(S3Service, "upload_bytes", reject_direct_upload)

    await _create_local_session(unauthenticated_client)
    documents = list((await db_session.scalars(select(Document))).all())
    storage_objects = list((await db_session.scalars(select(StorageObject))).all())

    assert documents
    assert len(storage_objects) == len(documents)
    by_object_id = {item.object_id: item for item in storage_objects}
    for document in documents:
        stored = by_object_id[document.id]
        matter = await db_session.get(Matter, document.matter_id)
        assert matter is not None
        assert stored.workspace_id == matter.workspace_id
        assert stored.matter_id == matter.id
        assert stored.client_id == matter.client_id
        assert stored.version == 1
        assert stored.state is StorageObjectState.available
        assert stored.byte_size
        assert stored.checksum_sha256
        assert document.s3_key == stored.storage_key
        assert S3Service.local_object_path(stored.storage_key).is_file()


@pytest.mark.asyncio
async def test_local_session_seed_is_idempotent(unauthenticated_client, db_session):
    counted_models = (
        User,
        Workspace,
        WorkspaceMembership,
        CommercialOffer,
        CommercialOfferVersion,
        WorkspaceCommercialContract,
        WorkspaceContractLine,
        Matter,
        OmnichannelThread,
        OmnichannelMessage,
        OmnichannelChannelConnection,
        OmnichannelIdentity,
        OmnichannelThreadBinding,
        WorkspaceTeam,
        OmnichannelAutomationRule,
        OmnichannelChannelEvent,
        LeadCaptureReceipt,
    )
    initial_counts = {
        model.__name__: int(await db_session.scalar(select(func.count(model.id))) or 0)
        for model in counted_models
    }
    first = await _create_local_session(unauthenticated_client)
    first_counts = {
        model.__name__: int(await db_session.scalar(select(func.count(model.id))) or 0)
        for model in counted_models
    }
    unauthenticated_client.headers.pop("Authorization", None)
    second = await _create_local_session(unauthenticated_client)
    second_counts = {
        model.__name__: int(await db_session.scalar(select(func.count(model.id))) or 0)
        for model in counted_models
    }
    assert first["user"]["id"] == second["user"]["id"]
    assert first["session"]["activeWorkspace"]["id"] == second["session"]["activeWorkspace"]["id"]
    assert first_counts == second_counts
    # The tenant user and the separately attributable platform root are both
    # materialized once. Repeating the tenant bootstrap must create neither.
    assert second_counts["User"] == initial_counts["User"] + 2
    assert second_counts["Workspace"] == initial_counts["Workspace"] + 1
    assert second_counts["WorkspaceMembership"] == initial_counts["WorkspaceMembership"] + 1
    assert second_counts["CommercialOffer"] == initial_counts["CommercialOffer"] + 4
    assert second_counts["CommercialOfferVersion"] == initial_counts["CommercialOfferVersion"] + 4
    assert (
        second_counts["WorkspaceCommercialContract"]
        == initial_counts["WorkspaceCommercialContract"] + 1
    )
    assert second_counts["WorkspaceContractLine"] == initial_counts["WorkspaceContractLine"] + 2
    assert second_counts["Matter"] == 10
    assert second_counts["OmnichannelThread"] == 10
    assert second_counts["OmnichannelChannelConnection"] == 11
    assert second_counts["OmnichannelIdentity"] == 10
    assert second_counts["WorkspaceTeam"] == 12
    assert second_counts["OmnichannelAutomationRule"] == 4
    assert second_counts["LeadCaptureReceipt"] == 2
    assert (
        int(
            await db_session.scalar(
                select(func.count(OmnichannelThread.id)).where(
                    OmnichannelThread.identity_id.is_(None)
                )
            )
            or 0
        )
        == 0
    )
    assert (
        int(
            await db_session.scalar(
                select(func.count(OmnichannelMessage.id)).where(
                    OmnichannelMessage.channel != OmnichannelChannel.internal,
                    (
                        OmnichannelMessage.endpoint_id.is_(None)
                        | OmnichannelMessage.binding_id.is_(None)
                    ),
                )
            )
            or 0
        )
        == 0
    )


@pytest.mark.asyncio
async def test_local_finance_e2e_seed_is_guarded_idempotent_and_cleanable(
    unauthenticated_client,
    db_session,
):
    await _create_local_session(unauthenticated_client)
    run_id = f"qa{uuid4().hex[:8]}"
    headers = {"X-KLM-Local-Seed-Intent": run_id}
    body = {"runId": run_id, "recordCount": 51}

    hidden = await unauthenticated_client.post("/api/v1/klm/local/finance-e2e-seed", json=body)
    assert hidden.status_code == 404

    created = await unauthenticated_client.post(
        "/api/v1/klm/local/finance-e2e-seed", json=body, headers=headers
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["recordCount"] == 51

    repeated = await unauthenticated_client.post(
        "/api/v1/klm/local/finance-e2e-seed", json=body, headers=headers
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["financeEntryId"] == payload["financeEntryId"]
    assert repeated.json()["recordCount"] == 51
    assert (
        int(
            await db_session.scalar(
                select(func.count(FinanceEntry.id)).where(
                    FinanceEntry.external_ref.like(f"e2e-finance:{run_id}:%")
                )
            )
            or 0
        )
        == 51
    )

    cleaned = await unauthenticated_client.delete(
        f"/api/v1/klm/local/finance-e2e-seed/{run_id}", headers=headers
    )
    assert cleaned.status_code == 200, cleaned.text
    assert cleaned.json()["artifactsRemoved"] == 51
    assert not await db_session.scalar(
        select(FinanceAllocation.id).where(
            FinanceAllocation.audit_metadata["runId"].as_string() == run_id
        )
    )
    assert not await db_session.scalar(
        select(FinanceCostCenter.id).where(FinanceCostCenter.code == f"E2E-{run_id}")
    )


@pytest.mark.asyncio
async def test_local_agent_budget_seed_is_guarded_and_idempotent(
    unauthenticated_client,
    db_session,
):
    session = await _create_local_session(unauthenticated_client)
    workspace_id = UUID(session["session"]["activeWorkspace"]["id"])
    deployment = AgentDeployment(
        workspace_id=workspace_id,
        agent_key=f"e2e-budget-{uuid4().hex[:8]}",
        version=1,
        status=DeploymentStatus.active,
        config={"definitionVersion": 1, "model": "test"},
        gates={"published": True},
        budget_policy={"monthlyTokenLimit": 7},
    )
    db_session.add(deployment)
    await db_session.commit()

    run_id = f"agent-budget-{uuid4().hex[:12]}"
    body = {"deploymentId": str(deployment.id), "runId": run_id}
    hidden = await unauthenticated_client.post(
        "/api/v1/klm/local/agent-budget-e2e-seed",
        json=body,
    )
    assert hidden.status_code == 404

    headers = {"X-KLM-Local-Seed-Intent": run_id}
    created = await unauthenticated_client.post(
        "/api/v1/klm/local/agent-budget-e2e-seed",
        json=body,
        headers=headers,
    )
    assert created.status_code == 200, created.text
    assert created.json()["consumedTokens"] == 7

    repeated = await unauthenticated_client.post(
        "/api/v1/klm/local/agent-budget-e2e-seed",
        json=body,
        headers=headers,
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["agentRunId"] == created.json()["agentRunId"]
    assert (
        int(
            await db_session.scalar(
                select(func.count(AgentRun.id)).where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.dedupe_key == f"local-e2e-agent-budget:{run_id}",
                )
            )
            or 0
        )
        == 1
    )


@pytest.mark.asyncio
async def test_local_session_bootstrap_exposes_canonical_core_and_legal_manifest(
    unauthenticated_client,
):
    await _create_local_session(unauthenticated_client)

    response = await unauthenticated_client.get("/api/v1/workspaces/capability-manifest")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["activeProducts"] == ["crm_evolution", "legal_360"]
    assert payload["capabilities"]["legal_suite"]["permitted"] is True
    assert payload["capabilities"]["monitored_processes"]["limit"] == 100
    assert payload["capabilities"]["active_tasks"]["limit"] == 20_000


@pytest.mark.asyncio
async def test_local_verification_cleanup_removes_qa_namespace_and_restores_seed_card(
    unauthenticated_client,
    db_session,
    monkeypatch,
):
    await _create_local_session(unauthenticated_client)
    funnel = (await unauthenticated_client.get("/api/v1/klm/funnel")).json()
    matter_card = next(card for card in funnel["cards"] if card["object"] == "Zolgensma")
    matter = await db_session.get(Matter, matter_card["id"])
    matter_id = matter.id
    baseline = {
        "lead_score": matter.lead_score,
        "next_action": matter.next_action,
        "qualification": matter.sdr_qualification,
        "needs_approval": matter.needs_approval,
    }
    threads = (await unauthenticated_client.get("/api/v1/klm/omnichannel/threads")).json()
    thread = next(item for item in threads if item["matterId"] == matter_card["id"])

    # This test exercises cleanup, so it explicitly models the prior privileged
    # unlock instead of bypassing the frozen-area policy in the runtime.
    health_area = await db_session.scalar(
        select(AreaBlueprint).where(
            AreaBlueprint.workspace_id == matter.workspace_id,
            AreaBlueprint.area_key == "saude_suplementar",
        )
    )
    assert health_area is not None
    health_area.status = AreaBlueprintStatus.active
    health_area.is_active = True
    for agent_key in {
        "crm",
        "juridico",
        "orquestrador",
        "sdr",
        "secretaria",
    }:
        db_session.add(
            AgentDeployment(
                workspace_id=matter.workspace_id,
                agent_key=agent_key,
                version=1,
                status=DeploymentStatus.active,
                config={
                    "definitionVersion": 1,
                    "model": "test",
                    "testedAt": "2026-08-03T00:00:00Z",
                },
                gates={"published": True},
                budget_policy={"monthlyTokenLimit": 100_000},
            )
        )
    monkeypatch.setattr(
        "app.services.agent_runtime_service.verify_deployment",
        AsyncMock(return_value={"testPassed": True}),
    )
    monkeypatch.setattr(
        "app.services.agent_runtime_service.validate_deployment_ready",
        lambda config, gates: None,
    )
    await db_session.flush()

    simulated = await unauthenticated_client.post(
        "/api/v1/klm/agents/simulations/health-intake",
        json={
            "matterId": matter_card["id"],
            "threadId": thread["id"],
            "idempotencyKey": "verify-simulation-cleanup-test",
        },
    )
    assert simulated.status_code == 201, simulated.text
    message = await unauthenticated_client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "whatsapp",
            "direction": "outbound",
            "authorType": "human",
            "authorLabel": "Verificador local",
            "body": "Artefato local transitório.",
            "idempotencyKey": "verify-message-cleanup-test",
        },
    )
    assert message.status_code == 201, message.text
    tracked = await unauthenticated_client.post(
        "/api/v1/klm/tracking/events",
        json={
            "eventName": "Lead",
            "channel": "local_hardening",
            "source": "verifier",
            "dedupeKey": "verify-track-cleanup-test",
        },
    )
    assert tracked.status_code == 201, tracked.text
    intent = await unauthenticated_client.post(
        "/api/v1/klm/external-intents",
        json={
            "actionType": "send_message",
            "provider": "whatsapp",
            "idempotencyKey": "verify-intent-cleanup-test",
            "payload": {"localOnly": True},
        },
    )
    assert intent.status_code == 201, intent.text
    email = await unauthenticated_client.post(
        "/api/v1/klm/omnichannel/email-ingest",
        json={
            "messageId": "verify-email-cleanup-test",
            "senderEmail": "cleanup-test@local.invalid",
            "senderName": "Lead de verificação",
            "subject": "Negativa de exame",
            "body": "Conteúdo transitório do QA.",
            "attachments": [{"name": "qa.pdf", "contentText": "Somente QA local."}],
        },
    )
    assert email.status_code == 201, email.text
    qa_simulation = await unauthenticated_client.post(
        "/api/v1/klm/agents/simulations/health-intake",
        json={
            "matterId": email.json()["matterId"],
            "threadId": email.json()["thread"]["id"],
            "idempotencyKey": "spa-health-team:cleanup-ui-test",
        },
    )
    assert qa_simulation.status_code == 201, qa_simulation.text
    qa_flow = await unauthenticated_client.post(
        "/api/v1/flows",
        json={
            "name": "QA descartável cleanup flow",
            "description": "Registro transitório.",
            "trigger": "manual",
            "nodes": [],
            "edges": [],
            "is_active": False,
        },
    )
    assert qa_flow.status_code == 201, qa_flow.text
    archived_flow = await unauthenticated_client.delete(f"/api/v1/flows/{qa_flow.json()['id']}")
    assert archived_flow.status_code == 204, archived_flow.text

    audit_count_before_cleanup = int(
        await db_session.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.correlation_id.like("%verify-%")
            )
        )
        or 0
    )
    assert audit_count_before_cleanup > 0

    cleanup = await unauthenticated_client.delete("/api/v1/klm/local/verification-artifacts")
    assert cleanup.status_code == 200, cleanup.text
    payload = cleanup.json()
    assert payload["artifactsRemoved"] > 0
    # Cada simulação executa Orquestrador, SDR, Jurídico, CRM, Secretaria e
    # Hermes como ponte humana da Secretaria.
    assert payload["agentRuns"] == 12
    assert payload["clients"] == payload["matters"] == 1
    assert payload["restoredMatters"] == 1
    assert payload["auditEvents"] == 0

    db_session.expire_all()
    restored = await db_session.get(Matter, matter_id)
    assert restored.lead_score == baseline["lead_score"]
    assert restored.next_action == baseline["next_action"]
    assert restored.sdr_qualification == baseline["qualification"]
    assert restored.needs_approval == baseline["needs_approval"]
    assert (
        int(
            await db_session.scalar(
                select(func.count(Matter.id)).where(Matter.deleted_at.is_(None))
            )
            or 0
        )
        == 10
    )
    assert (
        int(
            await db_session.scalar(
                select(func.count(Client.id)).where(Client.deleted_at.is_(None))
            )
            or 0
        )
        == 10
    )
    assert (
        int(
            await db_session.scalar(
                select(func.count(KlmAuditEvent.id)).where(
                    KlmAuditEvent.correlation_id.like("%verify-%")
                )
            )
            or 0
        )
        == audit_count_before_cleanup
    )
    assert (
        int(
            await db_session.scalar(
                select(func.count(AgentRun.id)).where(
                    AgentRun.dedupe_key.like("%verify-simulation-%")
                )
            )
            or 0
        )
        == 0
    )
    assert (
        int(
            await db_session.scalar(
                select(func.count(AcquisitionEvent.id)).where(
                    AcquisitionEvent.dedupe_key.like("verify-%")
                )
            )
            or 0
        )
        == 0
    )
    assert (
        int(
            await db_session.scalar(
                select(func.count(ExternalActionIntent.id)).where(
                    ExternalActionIntent.idempotency_key.like("%verify-%")
                )
            )
            or 0
        )
        == 0
    )
    assert (
        int(
            await db_session.scalar(
                select(func.count(Document.id)).where(~Document.matter_id.in_(select(Matter.id)))
            )
            or 0
        )
        == 0
    )
    assert (
        int(
            await db_session.scalar(
                select(func.count(AgentRun.id)).where(~AgentRun.matter_id.in_(select(Matter.id)))
            )
            or 0
        )
        == 0
    )


@pytest.mark.asyncio
async def test_local_verification_cleanup_retires_idempotent_meeting_graph(
    unauthenticated_client,
    db_session,
):
    """Cleanup must preserve the immutable POST /meetings receipt graph."""
    session = await _create_local_session(unauthenticated_client)
    ingested = await unauthenticated_client.post(
        "/api/v1/klm/omnichannel/email-ingest",
        json={
            "messageId": "verify-email-meeting-retention-test",
            "senderEmail": "meeting-retention-test@local.invalid",
            "senderName": "Lead de verificação",
            "subject": "Compromisso com recibo imutável",
            "body": "Conteúdo transitório do QA.",
            "attachments": [],
        },
    )
    assert ingested.status_code == 201, ingested.text
    matter_id = UUID(ingested.json()["matterId"])
    matter = await db_session.get(Matter, matter_id)
    assert matter is not None
    client_id = matter.client_id

    starts_at = datetime.now(UTC) + timedelta(days=3)
    meeting_response = await unauthenticated_client.post(
        "/api/v1/meetings",
        headers={"Idempotency-Key": "verify-meeting-retention-test"},
        json={
            "matterId": str(matter_id),
            "responsibleUserId": session["user"]["id"],
            "title": "QA descartável reunião com recibo imutável",
            "startsAt": starts_at.isoformat(),
            "endsAt": (starts_at + timedelta(hours=1)).isoformat(),
            "location": "Google Meet",
            "meetingUrl": "https://meet.google.com/qa-retention-test",
        },
    )
    assert meeting_response.status_code == 201, meeting_response.text
    meeting_id = UUID(meeting_response.json()["id"])
    thread_id = UUID(ingested.json()["thread"]["id"])
    evidence = await db_session.scalar(
        select(OmnichannelMessageEvidenceEvent).where(
            OmnichannelMessageEvidenceEvent.thread_id == thread_id
        )
    )
    assert evidence is not None
    assert evidence.message_id is not None
    evidence_id = evidence.id
    evidence_message_id = evidence.message_id

    cleanup = await unauthenticated_client.delete("/api/v1/klm/local/verification-artifacts")
    assert cleanup.status_code == 200, cleanup.text
    assert cleanup.json()["meetings"] == 1
    assert cleanup.json()["matters"] == 1
    assert cleanup.json()["clients"] == 1

    db_session.expire_all()
    retained_meeting = await db_session.get(Meeting, meeting_id)
    retained_matter = await db_session.get(Matter, matter_id)
    retained_client = await db_session.get(Client, client_id)
    receipt = await db_session.scalar(
        select(MeetingCreationReceipt).where(MeetingCreationReceipt.meeting_id == meeting_id)
    )
    assert retained_meeting is not None
    assert retained_meeting.status == MeetingStatus.cancelled
    assert receipt is not None
    assert await db_session.get(OmnichannelMessageEvidenceEvent, evidence_id) is not None
    assert await db_session.get(OmnichannelMessage, evidence_message_id) is not None
    assert await db_session.get(OmnichannelThread, thread_id) is not None
    assert retained_matter is not None and retained_matter.deleted_at is not None
    assert retained_client is not None and retained_client.deleted_at is not None

    replayed_cleanup = await unauthenticated_client.delete(
        "/api/v1/klm/local/verification-artifacts"
    )
    assert replayed_cleanup.status_code == 200, replayed_cleanup.text
    assert replayed_cleanup.json()["meetings"] == 0
    assert replayed_cleanup.json()["omnichannelMessages"] == 0
    assert replayed_cleanup.json()["threads"] == 0
    assert replayed_cleanup.json()["matters"] == 0
    assert replayed_cleanup.json()["clients"] == 0


@pytest.mark.asyncio
async def test_local_seed_populates_operational_domains(
    unauthenticated_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app.services.s3_service import S3Service

    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "objects")
    await _create_local_session(unauthenticated_client)
    assert int(await db_session.scalar(select(func.count(Document.id))) or 0) >= 20
    assert int(await db_session.scalar(select(func.count(KnowledgeChunk.id))) or 0) >= 20
    assert int(await db_session.scalar(select(func.count(OmnichannelThread.id))) or 0) == 10
    assert int(await db_session.scalar(select(func.count(Flow.id))) or 0) == 3
    assert int(await db_session.scalar(select(func.count(Meeting.id))) or 0) >= 4
    assert int(await db_session.scalar(select(func.count(FinanceEntry.id))) or 0) >= 2
    assert int(await db_session.scalar(select(func.count(HonorariumEstimate.id))) or 0) == 8
    document = await db_session.scalar(select(Document).where(Document.s3_key.is_not(None)))
    assert document is not None
    assert S3Service.local_object_path(document.s3_key).read_bytes().startswith(b"%PDF-")
    download_ticket = await unauthenticated_client.get(
        f"/api/v1/documents/{document.id}/download-url"
    )
    assert download_ticket.status_code == 200, download_ticket.text
    local_file = await unauthenticated_client.get(download_ticket.json()["downloadUrl"])
    assert local_file.status_code == 200
    assert local_file.content.startswith(b"%PDF-")
    finance = await unauthenticated_client.get("/api/v1/finance/overview")
    assert finance.status_code == 200, finance.text
    assert finance.json()["paidAmount"] > 0


@pytest.mark.asyncio
async def test_local_funnel_exposes_ten_health_cases(unauthenticated_client):
    await _create_local_session(unauthenticated_client)
    response = await unauthenticated_client.get("/api/v1/klm/funnel")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["cards"]) == 10
    assert len(payload["stages"]) == 9
    assert payload["boardVersion"] >= 0
    assert payload["metrics"]["qualified"] == 6
    assert any(card["object"] == "Zolgensma" for card in payload["cards"])
    assert all(isinstance(card["cardOrder"], int) for card in payload["cards"])


@pytest.mark.asyncio
async def test_local_funnel_includes_non_health_legal_areas(unauthenticated_client):
    await _create_local_session(unauthenticated_client)
    client_response = await unauthenticated_client.post(
        "/api/v1/clients",
        json={
            "name": "Cliente Bancário",
            "email": "cliente.bancario@example.com",
            "phone": "+5511987654321",
        },
    )
    assert client_response.status_code == 201, client_response.text
    matter_response = await unauthenticated_client.post(
        "/api/v1/matters",
        json={
            "clientId": client_response.json()["id"],
            "practiceArea": "bancario",
            "legalAreaKey": "bancario",
            "legalProductKey": "rmc_consignado",
            "legalObjectName": "Revisão de reserva de margem consignável",
            "demandType": "RMC consignado",
            "columnId": "novo_lead",
            "microStatus": "lead_novo",
            "leadScore": 81,
        },
    )
    assert matter_response.status_code == 201, matter_response.text

    response = await unauthenticated_client.get("/api/v1/klm/funnel")
    assert response.status_code == 200, response.text
    card = next(
        item for item in response.json()["cards"] if item["id"] == matter_response.json()["id"]
    )
    assert card["practiceArea"] == "bancario"
    assert card["subject"] == "rmc_consignado"
    assert card["object"] == "Revisão de reserva de margem consignável"


@pytest.mark.asyncio
async def test_local_funnel_repairs_legacy_civil_area_when_health_taxonomy_is_present(
    unauthenticated_client,
    db_session,
):
    await _create_local_session(unauthenticated_client)
    matter = await db_session.scalar(select(Matter).where(Matter.health_object_name.is_not(None)))
    assert matter is not None
    matter.practice_area = PracticeArea.civel
    matter.legal_area_key = "civel"
    await db_session.commit()

    response = await unauthenticated_client.get("/api/v1/klm/funnel")
    assert response.status_code == 200, response.text
    card = next(item for item in response.json()["cards"] if item["id"] == str(matter.id))
    assert card["practiceArea"] == "saude_suplementar"


@pytest.mark.asyncio
async def test_local_funnel_move_dispatches_audited_crm_orchestration(unauthenticated_client):
    await _create_local_session(unauthenticated_client)
    funnel = (await unauthenticated_client.get("/api/v1/klm/funnel")).json()
    card = next(card for card in funnel["cards"] if card["stage"] == "novo_lead")
    moved = await _move_funnel_card(unauthenticated_client, funnel, card, "triagem_sdr")
    assert moved.status_code == 200, moved.text
    assert moved.json()["columnId"] == "triagem_sdr"
    assert moved.json()["agentDispatched"] is True
    assert moved.json()["automationStatus"] == "queued"
    assert moved.json()["agentsTriggered"][0] == "crm"
    assert (
        moved.json()["automationMessage"]
        == "A orquestração foi enfileirada para execução assíncrona."
    )
    activity = (await unauthenticated_client.get("/api/v1/klm/activity")).json()
    assert any(item["eventType"] == "funnel_stage_changed" for item in activity)


@pytest.mark.asyncio
async def test_funnel_move_runs_crm_orchestrator_and_stage_specialist(
    unauthenticated_client, monkeypatch
):
    await _create_local_session(unauthenticated_client)
    calls: list[str] = []

    async def execute_stub(self, **kwargs):
        agent_key = kwargs["agent_key"]
        calls.append(agent_key)
        return {
            "runs": [SimpleNamespace(id=uuid4(), status=AgentRunStatus.completed)],
            "approvalId": None,
        }

    monkeypatch.setattr(KlmAgentService, "execute", execute_stub)
    funnel = (await unauthenticated_client.get("/api/v1/klm/funnel")).json()
    card = next(card for card in funnel["cards"] if card["stage"] == "novo_lead")
    moved = await _move_funnel_card(unauthenticated_client, funnel, card, "triagem_sdr")

    assert moved.status_code == 200, moved.text
    assert calls == []
    assert moved.json()["automationStatus"] == "queued"
    assert moved.json()["agentsTriggered"] == ["crm", "orquestrador", "sdr"]


@pytest.mark.asyncio
async def test_local_session_bootstrap_never_resets_operational_changes(unauthenticated_client):
    await _create_local_session(unauthenticated_client)
    funnel = (await unauthenticated_client.get("/api/v1/klm/funnel")).json()
    card = next(card for card in funnel["cards"] if card["stage"] == "novo_lead")
    matter_id = card["id"]
    moved = await _move_funnel_card(unauthenticated_client, funnel, card, "triagem_sdr")
    assert moved.status_code == 200, moved.text

    await _create_local_session(unauthenticated_client)
    reloaded = (await unauthenticated_client.get("/api/v1/klm/funnel")).json()
    persisted = next(card for card in reloaded["cards"] if card["id"] == matter_id)
    assert persisted["stage"] == "triagem_sdr"
    assert persisted["cardOrder"] == 0


@pytest.mark.asyncio
async def test_local_funnel_move_persists_destination_position(unauthenticated_client):
    await _create_local_session(unauthenticated_client)
    funnel = (await unauthenticated_client.get("/api/v1/klm/funnel")).json()
    moved_card = next(card for card in funnel["cards"] if card["stage"] == "novo_lead")

    response = await _move_funnel_card(
        unauthenticated_client, funnel, moved_card, "triagem_sdr", position=0
    )

    assert response.status_code == 200, response.text
    assert response.json()["cardOrder"] == 0
    reloaded = (await unauthenticated_client.get("/api/v1/klm/funnel")).json()
    triage_cards = [card for card in reloaded["cards"] if card["stage"] == "triagem_sdr"]
    assert triage_cards[0]["id"] == moved_card["id"]
    assert [card["cardOrder"] for card in triage_cards] == list(range(len(triage_cards)))


@pytest.mark.asyncio
async def test_local_funnel_reorders_within_the_same_stage(unauthenticated_client):
    await _create_local_session(unauthenticated_client)
    funnel = (await unauthenticated_client.get("/api/v1/klm/funnel")).json()
    new_leads = [card for card in funnel["cards"] if card["stage"] == "novo_lead"]
    assert len(new_leads) >= 2

    response = await _move_funnel_card(
        unauthenticated_client, funnel, new_leads[-1], "novo_lead", position=0
    )

    assert response.status_code == 200, response.text
    reloaded = (await unauthenticated_client.get("/api/v1/klm/funnel")).json()
    reordered = [card for card in reloaded["cards"] if card["stage"] == "novo_lead"]
    assert reordered[0]["id"] == new_leads[-1]["id"]
    assert [card["cardOrder"] for card in reordered] == list(range(len(reordered)))


@pytest.mark.asyncio
async def test_local_funnel_rejects_stale_board_version(unauthenticated_client):
    await _create_local_session(unauthenticated_client)
    funnel = (await unauthenticated_client.get("/api/v1/klm/funnel")).json()
    cards = [card for card in funnel["cards"] if card["stage"] == "novo_lead"]
    assert len(cards) >= 2

    first = await _move_funnel_card(unauthenticated_client, funnel, cards[0], "triagem_sdr")
    assert first.status_code == 200
    assert first.json()["boardVersion"] == funnel["boardVersion"] + 1

    stale = await _move_funnel_card(unauthenticated_client, funnel, cards[1], "triagem_sdr")
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "funnel_version_conflict"
    assert stale.json()["detail"]["currentBoardVersion"] == first.json()["boardVersion"]


@pytest.mark.asyncio
async def test_local_funnel_move_is_idempotent_by_operation_id(unauthenticated_client):
    await _create_local_session(unauthenticated_client)
    funnel = (await unauthenticated_client.get("/api/v1/klm/funnel")).json()
    card = next(card for card in funnel["cards"] if card["stage"] == "novo_lead")
    operation_id = str(uuid4())

    first = await _move_funnel_card(
        unauthenticated_client,
        funnel,
        card,
        "triagem_sdr",
        operation_id=operation_id,
    )
    replay = await _move_funnel_card(
        unauthenticated_client,
        funnel,
        card,
        "triagem_sdr",
        operation_id=operation_id,
    )

    assert first.status_code == replay.status_code == 200
    assert replay.json()["deduplicated"] is True
    assert replay.json()["operationId"] == operation_id
    assert replay.json()["boardVersion"] == first.json()["boardVersion"]


@pytest.mark.asyncio
async def test_local_funnel_rejects_negative_position(unauthenticated_client):
    await _create_local_session(unauthenticated_client)
    funnel = (await unauthenticated_client.get("/api/v1/klm/funnel")).json()
    card = funnel["cards"][0]
    response = await unauthenticated_client.patch(
        f"/api/v1/klm/funnel/{card['id']}/stage",
        json={
            "columnId": "triagem_sdr",
            "sourceColumnId": card["stage"],
            "position": -1,
            "expectedBoardVersion": funnel["boardVersion"],
            "operationId": str(uuid4()),
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_local_kpis_are_database_backed(unauthenticated_client):
    await _create_local_session(unauthenticated_client)
    response = await unauthenticated_client.get("/api/v1/klm/kpis")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source"] == "local_database"
    assert len(payload["topMetrics"]) == 12
    assert len(payload["families"]) >= 8
    assert any(item["channel"] == "google_ads" for item in payload["channels"])
    assert payload["teamPerformance"]["audience"] == "workspace_team"
    assert payload["teamPerformance"]["periodDays"] == 30
    assert payload["teamPerformance"]["scoreFormula"]


@pytest.mark.asyncio
async def test_workspace_kpi_gamification_never_includes_another_workspace(
    client,
    db_session,
    auth_context,
):
    other_workspace = Workspace(
        name="Conta externa",
        slug=f"conta-externa-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    other_user = User(
        name="Pessoa de outra conta",
        email=f"outra-conta-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="OC",
        color="#111827",
        is_active=True,
        is_internal_staff=False,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add_all([other_workspace, other_user])
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=other_workspace.id,
            user_id=other_user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
            is_default=True,
        )
    )
    await db_session.flush()

    response = await client.get("/api/v1/klm/kpis", params={"period_days": 30})

    assert response.status_code == 200, response.text
    rows = response.json()["teamPerformance"]["rows"]
    assert rows
    assert {row["scopeName"] for row in rows} == {"Workspace Teste"}
    assert "Pessoa de outra conta" not in {row["name"] for row in rows}
    assert str(auth_context.user_id) in {row["id"].split(":")[-1] for row in rows}


@pytest.mark.asyncio
async def test_kpis_apply_period_and_subtheme_and_measure_first_response(client):
    person = await _create_client(
        client,
        name="Lead SLA KPI",
        email=f"sla-kpi-{uuid4().hex[:8]}@example.com",
    )
    matter = await _create_health_matter(client, person["id"])
    thread = await _create_thread(client, matter["id"], person["id"])
    now = datetime.now(UTC)
    inbound = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "whatsapp",
            "direction": "inbound",
            "authorType": "client",
            "authorLabel": "Lead SLA KPI",
            "body": "Preciso de orientação.",
            "idempotencyKey": f"kpi-inbound-{uuid4()}",
            "occurredAt": (now - timedelta(minutes=5)).isoformat(),
        },
    )
    outbound = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "whatsapp",
            "direction": "outbound",
            "authorType": "human",
            "authorLabel": "Equipe KLM",
            "body": "Recebi seu caso e vou analisar.",
            "idempotencyKey": f"kpi-outbound-{uuid4()}",
            "occurredAt": now.isoformat(),
        },
    )
    assert inbound.status_code == 201, inbound.text
    assert outbound.status_code == 201, outbound.text

    response = await client.get(
        "/api/v1/klm/kpis",
        params={
            "period_days": 7,
            "health_subtheme": "medicamento_alto_custo",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["periodDays"] == 7
    assert payload["subtheme"] == "medicamento_alto_custo"
    assert (
        next(item["value"] for item in payload["topMetrics"] if item["key"] == "first_response")
        == 300
    )


@pytest.mark.asyncio
async def test_scoped_kpis_do_not_invent_acquisition_or_response_metrics(
    client,
    db_session,
    auth_context,
):
    person = await _create_client(
        client,
        name="Lead Empresarial KPI",
        email=f"empresarial-kpi-{uuid4().hex[:8]}@example.com",
    )
    matter_response = await client.post(
        "/api/v1/matters",
        json={
            "clientId": person["id"],
            "practiceArea": "empresarial",
            "legalAreaKey": "empresarial",
            "legalProductKey": "contratos_empresariais",
            "demandType": "Contrato empresarial",
            "columnId": "novo_lead",
            "microStatus": "lead_novo",
            "leadScore": 72,
        },
    )
    assert matter_response.status_code == 201, matter_response.text
    matter_id = UUID(matter_response.json()["id"])
    now = datetime.now(UTC)
    db_session.add_all(
        [
            Meeting(
                workspace_id=auth_context.workspace_id,
                matter_id=matter_id,
                title="Reunião concluída",
                starts_at=now - timedelta(days=2),
                status=MeetingStatus.completed,
            ),
            Meeting(
                workspace_id=auth_context.workspace_id,
                matter_id=matter_id,
                title="Não compareceu",
                starts_at=now - timedelta(days=1),
                status=MeetingStatus.no_show,
            ),
            Meeting(
                workspace_id=auth_context.workspace_id,
                matter_id=matter_id,
                title="Agendada",
                starts_at=now - timedelta(hours=2),
                status=MeetingStatus.scheduled,
            ),
            Meeting(
                workspace_id=auth_context.workspace_id,
                matter_id=matter_id,
                title="Cancelada",
                starts_at=now - timedelta(hours=1),
                status=MeetingStatus.cancelled,
            ),
        ]
    )
    await db_session.flush()

    response = await client.get(
        "/api/v1/klm/kpis",
        params={"period_days": 30, "legal_area_key": "empresarial"},
    )

    assert response.status_code == 200, response.text
    metrics = {item["key"]: item for item in response.json()["topMetrics"]}
    assert metrics["first_response"]["value"] is None
    assert metrics["cpl"]["value"] is None
    assert metrics["cac"]["value"] is None
    assert metrics["roas"]["value"] is None
    assert metrics["show_rate"]["value"] == 50
    assert metrics["cac"]["label"] == "CAC por caso ganho"


@pytest.mark.asyncio
async def test_kpis_expose_14_day_trend_for_sparklines(unauthenticated_client):
    await _create_local_session(unauthenticated_client)
    response = await unauthenticated_client.get("/api/v1/klm/kpis")
    assert response.status_code == 200, response.text
    payload = response.json()
    trend = payload["trend"]
    assert set(trend.keys()) >= {"leads", "revenue"}
    assert len(trend["leads"]) == 14
    assert len(trend["revenue"]) == 14
    for point in trend["leads"]:
        assert set(point.keys()) == {"date", "value"}
    for point in trend["revenue"]:
        assert set(point.keys()) == {"date", "value"}


@pytest.mark.asyncio
async def test_advanced_kpis_expose_multi_area_catalog_and_response_metrics(
    unauthenticated_client,
):
    await _create_local_session(unauthenticated_client)

    response = await unauthenticated_client.get(
        "/api/v1/klm/kpis/advanced",
        params={"period_days": 30, "limit": 1000},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    rows = payload["rows"]
    keys = [row["key"] for row in rows]
    categories = {item["key"]: item for item in payload["categories"]}

    assert payload["catalogTotal"] >= 400
    assert payload["totalRows"] >= 400
    assert payload["coverageTotal"] == payload["totalRows"]
    assert payload["coverageWithData"] == payload["rowsWithData"]
    assert payload["truncated"] is False
    assert len(keys) == len(set(keys))
    assert "service.response.median" in keys
    assert all(row["polarity"] in {"positive", "negative", "neutral"} for row in rows)
    assert categories["service"]["metricCount"] > 0
    assert categories["legal_areas"]["metricCount"] >= 50
    assert categories["legal_products"]["metricCount"] >= 250
    assert len(payload["options"]["legalAreas"]) >= 10
    assert len(payload["options"]["legalProducts"]) >= 50

    oversized_period = await unauthenticated_client.get(
        "/api/v1/klm/kpis/advanced",
        params={"period_days": 366},
    )
    assert oversized_period.status_code == 422


@pytest.mark.asyncio
async def test_advanced_kpis_apply_legal_area_filter_without_health_lock_in(
    unauthenticated_client,
):
    await _create_local_session(unauthenticated_client)

    response = await unauthenticated_client.get(
        "/api/v1/klm/kpis/advanced",
        params={
            "period_days": 30,
            "legal_area_key": "empresarial",
            "category": "legal_products",
            "limit": 600,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["appliedFilters"]["legalAreaKey"] == "empresarial"
    assert payload["rows"]
    assert all(row["dimensions"].get("legalArea") == "empresarial" for row in payload["rows"])
    assert payload["options"]["legalProducts"]
    assert all(item["parentValue"] == "empresarial" for item in payload["options"]["legalProducts"])


@pytest.mark.asyncio
async def test_advanced_kpis_never_include_another_workspace(
    client,
    db_session,
    auth_context,
):
    other_workspace = Workspace(
        name="Conta analítica externa",
        slug=f"conta-analitica-externa-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    other_user = User(
        name="Responsável analítico externo",
        email=f"analitico-externo-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="AE",
        color="#111827",
        is_active=True,
        is_internal_staff=False,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add_all([other_workspace, other_user])
    await db_session.flush()
    other_client = Client(
        workspace_id=other_workspace.id,
        name="Cliente analítico externo",
        email=f"cliente-analitico-{uuid4().hex[:8]}@example.com",
    )
    db_session.add(other_client)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=other_workspace.id,
            user_id=other_user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
            is_default=True,
        )
    )
    db_session.add(
        Matter(
            workspace_id=other_workspace.id,
            client_id=other_client.id,
            practice_area=PracticeArea.empresarial,
            legal_area_key="empresarial",
            legal_product_key="contratos_empresariais",
            demand_type="Contrato empresarial externo",
            column_id=KanbanColumnId.novo_lead,
            micro_status="lead_novo",
            lead_score=99,
            responsible_human_id=other_user.id,
        )
    )
    current_client = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente da conta atual",
        email=f"cliente-atual-{uuid4().hex[:8]}@example.com",
    )
    db_session.add(current_client)
    await db_session.flush()
    db_session.add_all(
        [
            Matter(
                workspace_id=auth_context.workspace_id,
                client_id=current_client.id,
                practice_area=PracticeArea.empresarial,
                legal_area_key="empresarial",
                legal_product_key="contratos_empresariais",
                demand_type="Referência inconsistente de responsável",
                column_id=KanbanColumnId.novo_lead,
                micro_status="lead_novo",
                responsible_human_id=other_user.id,
            ),
            Matter(
                workspace_id=auth_context.workspace_id,
                client_id=current_client.id,
                practice_area=PracticeArea.tributario,
                legal_area_key="tributario",
                legal_product_key="isencao_ir_doenca_grave",
                demand_type="Caso futuro que não pertence ao período",
                column_id=KanbanColumnId.novo_lead,
                micro_status="lead_novo",
                created_at=datetime.now(UTC) + timedelta(days=1),
            ),
        ]
    )
    await db_session.flush()

    response = await client.get(
        "/api/v1/klm/kpis/advanced",
        params={"period_days": 30, "limit": 600},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    responsible_options = payload["options"]["responsibleUsers"]
    assert "Responsável analítico externo" not in {item["label"] for item in responsible_options}

    future_scope = await client.get(
        "/api/v1/klm/kpis/advanced",
        params={
            "period_days": 30,
            "legal_area_key": "tributario",
            "legal_product_key": "isencao_ir_doenca_grave",
            "category": "legal_products",
            "limit": 600,
        },
    )
    assert future_scope.status_code == 200, future_scope.text
    leads = next(
        row
        for row in future_scope.json()["rows"]
        if row["key"] == "legal_products.isencao_ir_doenca_grave.leads"
    )
    assert leads["value"] == 0


@pytest.mark.asyncio
async def test_local_quality_proves_external_delivery_zero(unauthenticated_client):
    await _create_local_session(unauthenticated_client)
    response = await unauthenticated_client.get("/api/v1/klm/quality")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["metrics"]["externalMessagesSent"] == 0
    guardrail = next(item for item in payload["dimensions"] if item["key"] == "guardrail")
    assert guardrail["score"] == 100
    assert 0 <= payload["score"] <= 100


@pytest.mark.asyncio
async def test_audio_requires_repeated_request_before_creating_intent(unauthenticated_client):
    await _create_local_session(unauthenticated_client)
    thread = (await unauthenticated_client.get("/api/v1/klm/omnichannel/threads")).json()[0]
    first = await unauthenticated_client.post(
        "/api/v1/klm/audio/requests",
        json={
            "threadId": thread["id"],
            "transcript": "Prefiro explicar por áudio.",
            "durationSeconds": 73,
            "idempotencyKey": "audio-gate-1",
        },
    )
    assert first.status_code == 201, first.text
    assert first.json()["gatePassed"] is False
    assert first.json()["requestCount"] == 1
    assert first.json()["externalIntent"] is None
    replay = await unauthenticated_client.post(
        "/api/v1/klm/audio/requests",
        json={
            "threadId": thread["id"],
            "transcript": "Prefiro explicar por áudio.",
            "durationSeconds": 73,
            "idempotencyKey": "audio-gate-1",
        },
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["requestCount"] == 1
    assert replay.json()["gatePassed"] is False

    forged = await unauthenticated_client.post(
        "/api/v1/klm/audio/requests",
        json={
            "threadId": thread["id"],
            "requestCount": 20,
            "requestedByLabel": "Pessoa inventada pelo cliente",
            "transcript": "Tentativa de forjar o gate.",
            "durationSeconds": 10,
            "idempotencyKey": "audio-gate-forged",
        },
    )
    assert forged.status_code == 422, forged.text

    second = await unauthenticated_client.post(
        "/api/v1/klm/audio/requests",
        json={
            "threadId": thread["id"],
            "transcript": "Insisto em falar por áudio.",
            "durationSeconds": 121,
            "summary": "Lead reiterou o pedido.",
            "transcriptionEnabled": True,
            "summaryEnabled": True,
            "voiceReplyRequested": True,
            "idempotencyKey": "audio-gate-2",
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["requestCount"] == 2
    assert second.json()["gatePassed"] is True
    assert second.json()["externalIntent"]["provider"] == "elevenlabs"
    assert second.json()["externalIntent"]["status"] == "blocked_external"
    assert second.json()["message"]["direction"] == "internal"
    assert second.json()["message"]["authorType"] == "human"
    assert second.json()["message"]["authorLabel"] == "Kleist Monteiro"
    assert second.json()["message"]["authorUserId"] is not None
    metadata = second.json()["message"]["messageMetadata"]
    assert metadata["requestCount"] == 2
    assert metadata["voiceGatePassed"] is True
    assert metadata["summary"] == "Lead reiterou o pedido."
    assert metadata["durationSeconds"] == 121
    assert metadata["transcriptionEnabled"] is True
    assert metadata["summaryEnabled"] is True
    assert metadata["voiceReplyRequested"] is True
    assert metadata["spokenByLabel"] != "Lead solicitante"
    assert metadata["recordedByUserId"] == second.json()["message"]["authorUserId"]
    assert metadata["source"] == "operator_audio_gate"
    assert metadata["transcriptionMethod"] == "operator_reviewed"
    assert metadata["originalDirection"] == "inbound"
    assert metadata["externalActionsExecuted"] == 0
    assert second.json()["externalIntent"]["payload"]["voiceReplyRequested"] is True


@pytest.mark.asyncio
async def test_local_session_is_hidden_outside_development(unauthenticated_client, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    response = await unauthenticated_client.post("/api/v1/klm/local/session")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_local_source_binding_matches_loaded_source(unauthenticated_client):
    await _create_local_session(unauthenticated_client)

    response = await unauthenticated_client.get("/api/v1/klm/local/source-binding")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["scope"] == "klm-local-source-binding"
    assert len(payload["runtimeSourceDigest"]) == 64
    assert payload["runtimeSourceFileCount"] > 0
    assert payload["runtimeSourceMatchesCurrent"] is True


@pytest.mark.asyncio
async def test_local_source_binding_is_hidden_outside_development(
    unauthenticated_client,
    monkeypatch,
):
    await _create_local_session(unauthenticated_client)
    monkeypatch.setattr(settings, "APP_ENV", "production")

    response = await unauthenticated_client.get("/api/v1/klm/local/source-binding")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_flow_run_is_persisted_simulation_without_queue(unauthenticated_client):
    await _create_local_session(unauthenticated_client)
    flows = (await unauthenticated_client.get("/api/v1/flows")).json()
    flow = next(item for item in flows if item["name"] == "Saúde: triagem até aprovação local")
    matter = (await unauthenticated_client.get("/api/v1/klm/funnel")).json()["cards"][0]
    response = await unauthenticated_client.post(
        f"/api/v1/flows/{flow['id']}/run",
        json={"matter_id": matter["id"], "context": {"source": "test"}},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["status"] == "waiting_human"
    assert payload["context"]["klmRuntime"] == "local_simulation"
    assert payload["context"]["externalActionsExecuted"] == 0
    assert [item["nodeId"] for item in payload["context"]["trace"]] == [
        "lead",
        "sdr",
        "docs",
        "legal",
        "meeting",
    ]

    for expected_node, expected_status in (
        ("payment", "waiting_human"),
        ("petition", "waiting_human"),
        ("petition", "completed"),
    ):
        resumed = await unauthenticated_client.post(f"/api/v1/flows/runs/{payload['id']}/resume")
        assert resumed.status_code == 200, resumed.text
        payload = resumed.json()
        assert payload["current_node_id"] == expected_node
        assert payload["status"] == expected_status
    assert len(payload["context"]["trace"]) == 8


@pytest.mark.asyncio
async def test_conversation_flow_activation_is_linked_and_logged_internally(
    unauthenticated_client,
):
    await _create_local_session(unauthenticated_client)
    flows = (await unauthenticated_client.get("/api/v1/flows")).json()
    flow = next(item for item in flows if item["name"] == "Follow-up: documentos pendentes")
    threads = (await unauthenticated_client.get("/api/v1/klm/omnichannel/threads")).json()
    thread = next(item for item in threads if item["matterId"])

    response = await unauthenticated_client.post(
        f"/api/v1/flows/{flow['id']}/run",
        json={
            "matter_id": thread["matterId"],
            "thread_id": thread["id"],
            "context": {"source": "conversation"},
        },
    )

    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "waiting_human"
    assert run["context"]["threadId"] == thread["id"]
    assert run["context"]["externalActionsExecuted"] == 0

    refreshed_threads = (await unauthenticated_client.get("/api/v1/klm/omnichannel/threads")).json()
    refreshed = next(item for item in refreshed_threads if item["id"] == thread["id"])
    execution = refreshed["threadMetadata"]["latestFlowExecution"]
    assert execution["flowId"] == flow["id"]
    assert execution["runId"] == run["id"]
    assert execution["status"] == "waiting_human"

    resumed = await unauthenticated_client.post(f"/api/v1/flows/runs/{run['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "completed"
    refreshed_threads = (await unauthenticated_client.get("/api/v1/klm/omnichannel/threads")).json()
    refreshed = next(item for item in refreshed_threads if item["id"] == thread["id"])
    assert refreshed["threadMetadata"]["latestFlowExecution"]["status"] == "completed"

    messages = (
        await unauthenticated_client.get(f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages")
    ).json()
    flow_log = next(
        item for item in messages if item["messageMetadata"].get("source") == "flow_activation"
    )
    assert flow_log["direction"] == "internal"
    assert flow_log["messageMetadata"]["customerVisible"] is False
    assert flow["name"] in flow_log["body"]

    runs = (
        await unauthenticated_client.get(
            f"/api/v1/flows/runs?matter_id={thread['matterId']}&include_terminal=true"
        )
    ).json()
    assert runs[0]["id"] == run["id"]


@pytest.mark.asyncio
async def test_conversation_flow_rejects_thread_from_another_matter(
    unauthenticated_client,
):
    await _create_local_session(unauthenticated_client)
    flow = (await unauthenticated_client.get("/api/v1/flows")).json()[0]
    threads = [
        item
        for item in (await unauthenticated_client.get("/api/v1/klm/omnichannel/threads")).json()
        if item["matterId"]
    ]

    response = await unauthenticated_client.post(
        f"/api/v1/flows/{flow['id']}/run",
        json={"matter_id": threads[0]["matterId"], "thread_id": threads[1]["id"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Conversa não pertence ao caso informado"


@pytest.mark.asyncio
async def test_local_only_flow_never_enqueues_external_work(
    unauthenticated_client,
    monkeypatch,
):
    await _create_local_session(unauthenticated_client)
    monkeypatch.setattr(
        "app.services.flow_service.external_delivery_allowed",
        lambda: True,
    )
    flow = (await unauthenticated_client.get("/api/v1/flows")).json()[0]
    matter = (await unauthenticated_client.get("/api/v1/klm/funnel")).json()["cards"][0]

    response = await unauthenticated_client.post(
        f"/api/v1/flows/{flow['id']}/run",
        json={"matter_id": matter["id"]},
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "waiting_human"
    assert response.json()["context"]["klmRuntime"] == "local_simulation"
    assert response.json()["context"]["externalActionsExecuted"] == 0


@pytest.mark.asyncio
async def test_area_update_is_audited_and_activation_stays_blocked(client):
    created = (
        await client.post(
            "/api/v1/klm/areas",
            json={"areaKey": "bancario", "name": "Direito bancário"},
        )
    ).json()
    updated = await client.patch(
        f"/api/v1/klm/areas/{created['id']}",
        json={"name": "Bancário premium", "status": "blueprint", "readiness": 55},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Bancário premium"
    assert updated.json()["readiness"] == 55
    blocked = await client.patch(f"/api/v1/klm/areas/{created['id']}", json={"status": "active"})
    assert blocked.status_code == 409
    activity = (await client.get("/api/v1/klm/activity")).json()
    assert any(item["eventType"] == "area_blueprint_updated" for item in activity)


@pytest.mark.asyncio
async def test_thread_rejects_mismatched_client_and_matter(client):
    first = await _create_client(client, "Cliente A", "cliente-a@example.com")
    matter = await _create_health_matter(client, first["id"])
    second = await _create_client(client, "Cliente B", "cliente-b@example.com")
    response = await client.post(
        "/api/v1/klm/omnichannel/threads",
        json={
            "matterId": matter["id"],
            "clientId": second["id"],
            "primaryChannel": "email",
        },
    )
    assert response.status_code == 409
    assert "não pertence" in response.json()["detail"]


@pytest.mark.asyncio
async def test_human_takeover_assigns_actor_and_release_clears_assignment(client, auth_context):
    thread = await _create_thread(client)
    takeover = await client.patch(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}",
        json={"status": "human_active"},
    )
    assert takeover.status_code == 200, takeover.text
    assert takeover.json()["status"] == "human_active"
    assert takeover.json()["assignedHumanId"] == str(auth_context.user_id)
    released = await client.patch(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}",
        json={"status": "open"},
    )
    assert released.status_code == 200, released.text
    assert released.json()["assignedHumanId"] is None
    activity = (await client.get("/api/v1/klm/activity")).json()
    assert sum(item["eventType"] == "omnichannel_thread_updated" for item in activity) == 2


@pytest.mark.asyncio
async def test_thread_score_update_advances_one_version_and_honors_expected_version(client):
    thread = await _create_thread(client)
    updated = await client.patch(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}",
        json={"score": 80, "expectedVersion": thread["version"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["score"] == 80
    assert updated.json()["version"] == thread["version"] + 1

    stale = await client.patch(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}",
        json={"score": 100, "expectedVersion": thread["version"]},
    )
    assert stale.status_code == 409
    assert "versão atual" in stale.json()["detail"]


@pytest.mark.asyncio
async def test_thread_rejects_human_from_another_workspace(client, db_session):
    thread = await _create_thread(client)
    outsider = await _create_outside_user(db_session)
    response = await client.patch(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}",
        json={"assignedHumanId": str(outsider.id)},
    )
    assert response.status_code == 404
    assert "Humano não encontrado" in response.json()["detail"]


@pytest.mark.asyncio
async def test_thread_filters_combine_channel_status_and_search(client):
    email_thread = await _create_thread(client, channel="email")
    await _create_thread(client, channel="whatsapp")
    updated = await client.patch(
        f"/api/v1/klm/omnichannel/threads/{email_thread['id']}",
        json={"status": "waiting_human", "subject": "Cirurgia urgente"},
    )
    assert updated.status_code == 200
    response = await client.get(
        "/api/v1/klm/omnichannel/threads",
        params={"channel": "email", "status": "waiting_human", "search": "Cirurgia"},
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [email_thread["id"]]


@pytest.mark.asyncio
async def test_internal_message_is_received_without_external_intent(client):
    thread = await _create_thread(client)
    response = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/messages",
        json={
            "channel": "internal",
            "direction": "internal",
            "authorType": "agent",
            "authorLabel": "Jurídico Saúde",
            "body": "Revisar a prescrição antes do handoff.",
            "idempotencyKey": "internal-review-1",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["deliveryStatus"] == "received"
    assert response.json()["direction"] == "internal"
    assert response.json()["messageMetadata"]["localSimulation"] is True
    assert response.json()["messageMetadata"]["externalActionsExecuted"] == 0
    assert (await client.get("/api/v1/klm/external-intents")).json() == []


@pytest.mark.asyncio
async def test_email_ingest_rejects_client_matter_mismatch(client):
    first = await _create_client(client, "Cliente A", "email-a@example.com")
    matter = await _create_health_matter(client, first["id"])
    second = await _create_client(client, "Cliente B", "email-b@example.com")
    response = await client.post(
        "/api/v1/klm/omnichannel/email-ingest",
        json={
            "messageId": "email-mismatch-1",
            "senderEmail": second["email"],
            "senderName": second["name"],
            "subject": "Negativa",
            "body": "Envio relacionado a outro caso.",
            "clientId": second["id"],
            "matterId": matter["id"],
        },
    )
    assert response.status_code == 409
    assert "não pertence" in response.json()["detail"]


@pytest.mark.asyncio
async def test_follow_up_requires_target_and_rejects_mismatched_case(client):
    due_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    missing = await client.post(
        "/api/v1/klm/follow-ups",
        json={"channel": "whatsapp", "dueAt": due_at},
    )
    assert missing.status_code == 409

    first = await _create_client(client, "Cliente A", "follow-a@example.com")
    first_matter = await _create_health_matter(client, first["id"])
    thread = await _create_thread(client, matter_id=first_matter["id"], client_id=first["id"])
    second = await _create_client(client, "Cliente B", "follow-b@example.com")
    second_matter = await _create_health_matter(client, second["id"])
    mismatch = await client.post(
        "/api/v1/klm/follow-ups",
        json={
            "threadId": thread["id"],
            "matterId": second_matter["id"],
            "channel": "whatsapp",
            "dueAt": due_at,
        },
    )
    assert mismatch.status_code == 409
    assert "não pertence" in mismatch.json()["detail"]


@pytest.mark.asyncio
async def test_tracking_rejects_mismatched_client_and_matter(client):
    first = await _create_client(client, "Cliente A", "track-a@example.com")
    matter = await _create_health_matter(client, first["id"])
    second = await _create_client(client, "Cliente B", "track-b@example.com")
    response = await client.post(
        "/api/v1/klm/tracking/events",
        json={
            "matterId": matter["id"],
            "clientId": second["id"],
            "eventName": "Lead",
            "channel": "google_search",
            "dedupeKey": "track-mismatch-1",
        },
    )
    assert response.status_code == 409
    assert "não pertence" in response.json()["detail"]


@pytest.mark.asyncio
async def test_workspace_identity_cannot_assign_saas_technical_queue(
    client, db_session, auth_context
):
    created = await client.post(
        "/api/v1/klm/technical-requests",
        json={"title": "Validar webhook", "description": "Conferir contrato local."},
    )
    request_id = created.json()["id"]
    tenant_assignment = await client.patch(
        f"/api/v1/klm/technical-requests/{request_id}",
        json={"status": "analyzing", "assignedToUserId": str(auth_context.user_id)},
    )
    assert tenant_assignment.status_code == 403
    assert "support:assign" in tenant_assignment.json()["detail"]

    outsider = await _create_outside_user(db_session)
    rejected = await client.patch(
        f"/api/v1/klm/technical-requests/{request_id}",
        json={"assignedToUserId": str(outsider.id)},
    )
    assert rejected.status_code == 403
    assert "support:assign" in rejected.json()["detail"]

    suffix = uuid4().hex[:8]
    technical_support = User(
        name="Suporte Técnico",
        email=f"suporte-tecnico-{suffix}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="ST",
        color="#0f172a",
        is_active=True,
        is_internal_staff=True,
        platform_role_package="technical_support",
        platform_access_active=True,
    )
    db_session.add(technical_support)
    await db_session.flush()
    rejected_saas_assignment = await client.patch(
        f"/api/v1/klm/technical-requests/{request_id}",
        json={
            "status": "analyzing",
            "assignedToUserId": str(technical_support.id),
        },
    )
    assert rejected_saas_assignment.status_code == 403
    assert "support:assign" in rejected_saas_assignment.json()["detail"]


@pytest.mark.asyncio
async def test_financial_projection_versions_existing_records_and_audits(client, db_session):
    person = await _create_client(client)
    matter = await _create_health_matter(client, person["id"])
    first = await client.patch(
        f"/api/v1/klm/matters/{matter['id']}/financial-projection",
        json={
            "fixedFee": 5000,
            "successPercentage": 20,
            "economicValueAnnual": 100000,
            "probability": 60,
            "economicObligation": True,
            "reviewStatus": "rascunho",
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["conservative"] == pytest.approx(10200)
    assert first.json()["likely"] == pytest.approx(15000)
    assert first.json()["aggressive"] == pytest.approx(25200)
    assert first.json()["withoutEconomicValue"] == pytest.approx(9000)
    assert first.json()["projectionVersion"] == 1

    second = await client.patch(
        f"/api/v1/klm/matters/{matter['id']}/financial-projection",
        json={
            "fixedFee": 6000,
            "successPercentage": 10,
            "economicValueAnnual": 200000,
            "probability": 50,
            "economicObligation": False,
            "reviewStatus": "revisado",
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["likely"] == pytest.approx(5500)
    assert second.json()["withoutEconomicValue"] == pytest.approx(5500)
    assert second.json()["projectionVersion"] == 2
    matter_id = matter["id"]
    assert (
        int(
            await db_session.scalar(
                select(func.count(HonorariumEstimate.id)).where(
                    HonorariumEstimate.matter_id == matter_id
                )
            )
            or 0
        )
        == 1
    )
    persisted_matter = await client.get(f"/api/v1/matters/{matter_id}")
    assert persisted_matter.status_code == 200, persisted_matter.text
    persisted_projection = persisted_matter.json()["revenueProjection"]
    assert persisted_projection["projectionVersion"] == 2
    assert persisted_projection["projectionPremises"] == {
        "fixedFee": 6000.0,
        "successPercentage": 10.0,
        "economicValueAnnual": 200000.0,
        "probability": 50.0,
        "economicObligation": False,
        "paymentMode": "initial_plus_success",
        "successBaseType": "economic_obligation",
        "moralDamagesEstimate": None,
        "injunctionMilestoneFee": 0.0,
        "obligationFee": 0.0,
        "reviewStatus": "revisado",
    }
    assert (
        int(
            await db_session.scalar(
                select(func.count(RevenueProjection.id)).where(
                    RevenueProjection.matter_id == matter_id
                )
            )
            or 0
        )
        == 1
    )
    activity = (await client.get("/api/v1/klm/activity")).json()
    assert sum(item["eventType"] == "matter_financial_projection_updated" for item in activity) == 2


@pytest.mark.asyncio
async def test_financial_projection_requires_finance_permission_even_with_kanban_edit(
    client, db_session, auth_context
):
    """Risco 3: kanban:edit sozinho não pode autorizar edição de fee/%/valor.

    legal_manager tem kanban:edit mas não finance:manage — precisa ser
    barrado mesmo tendo a permissão de kanban."""
    person = await _create_client(client)
    matter = await _create_health_matter(client, person["id"])

    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    assert membership is not None
    membership.role = WorkspaceRole.legal_manager
    auth_context.role = WorkspaceRole.legal_manager
    await db_session.flush()

    denied = await client.patch(
        f"/api/v1/klm/matters/{matter['id']}/financial-projection",
        json={
            "fixedFee": 5000,
            "successPercentage": 20,
            "economicValueAnnual": 100000,
            "probability": 60,
            "economicObligation": True,
            "reviewStatus": "rascunho",
        },
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["detail"]["code"] == "AUTH-012"


@pytest.mark.asyncio
async def test_financial_projection_allows_role_with_kanban_and_finance_permission(
    client, db_session, auth_context
):
    """Contraprova do teste acima: com kanban:edit + finance:manage, passa."""
    person = await _create_client(client)
    matter = await _create_health_matter(client, person["id"])

    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    assert membership is not None
    membership.role = WorkspaceRole.legal_manager
    membership.permission_grants = [{"module": "finance", "action": "manage", "scope": "workspace"}]
    auth_context.role = WorkspaceRole.legal_manager
    await db_session.flush()

    allowed = await client.patch(
        f"/api/v1/klm/matters/{matter['id']}/financial-projection",
        json={
            "fixedFee": 5000,
            "successPercentage": 20,
            "economicValueAnnual": 100000,
            "probability": 60,
            "economicObligation": True,
            "reviewStatus": "rascunho",
        },
    )
    assert allowed.status_code == 200, allowed.text


@pytest.mark.asyncio
async def test_legal_artifact_versions_are_immutable_and_drive_approval(client):
    person = await _create_client(client)
    matter = await _create_health_matter(client, person["id"])
    first = await client.post(
        "/api/v1/klm/legal-artifacts",
        json={
            "matterId": matter["id"],
            "kind": "proposal",
            "title": "Proposta de atuação em saúde",
            "content": {"sections": [{"title": "Caso", "body": "Negativa registrada."}]},
        },
    )
    assert first.status_code == 201, first.text
    first_payload = first.json()
    assert first_payload["version"] == 1
    assert first_payload["status"] == "draft"
    assert first_payload["sourceSnapshot"]["matterId"] == matter["id"]
    assert "evidenceSchemaVersion" not in first_payload
    assert "evidenceSha256" not in first_payload

    stale = await client.post(
        "/api/v1/klm/legal-artifacts",
        json={
            "matterId": matter["id"],
            "kind": "proposal",
            "title": "Proposta revisada",
            "content": {"sections": [{"title": "Caso", "body": "Revisão."}]},
            "artifactKey": first_payload["artifactKey"],
            "expectedVersion": 2,
        },
    )
    assert stale.status_code == 409

    second = await client.post(
        "/api/v1/klm/legal-artifacts",
        json={
            "matterId": matter["id"],
            "kind": "proposal",
            "title": "Proposta revisada",
            "content": {"sections": [{"title": "Caso", "body": "Revisão."}]},
            "artifactKey": first_payload["artifactKey"],
            "expectedVersion": 1,
        },
    )
    assert second.status_code == 201, second.text
    second_payload = second.json()
    assert second_payload["version"] == 2
    assert (await client.get(f"/api/v1/klm/legal-artifacts/{first_payload['id']}")).json()[
        "version"
    ] == 1

    old_submit = await client.post(f"/api/v1/klm/legal-artifacts/{first_payload['id']}/submit")
    assert old_submit.status_code == 409
    submitted = await client.post(f"/api/v1/klm/legal-artifacts/{second_payload['id']}/submit")
    assert submitted.status_code == 200, submitted.text
    approval_id = submitted.json()["approvalId"]
    assert submitted.json()["artifact"]["status"] == "review_pending"

    approved = await client.post(
        f"/api/v1/approvals/{approval_id}/resolve",
        json={"status": "approved", "resolutionReason": "Conteúdo conferido."},
    )
    assert approved.status_code == 200, approved.text
    exact = await client.get(f"/api/v1/klm/legal-artifacts/{second_payload['id']}")
    assert exact.status_code == 200
    assert exact.json()["status"] == "approved"
    listed = await client.get(
        "/api/v1/klm/legal-artifacts",
        params={"matter_id": matter["id"], "kind": "proposal"},
    )
    assert [item["version"] for item in listed.json()] == [2, 1]


@pytest.mark.asyncio
async def test_process_monitoring_is_card_bound_persistent_and_deduplicated(client):
    person = await _create_client(client)
    matter = await _create_health_matter(client, person["id"])
    created = await client.post(
        "/api/v1/klm/process-monitoring",
        json={
            "matterId": matter["id"],
            "caseNumber": "0001234-56.2026.8.26.0100",
            "court": "TJSP",
            "status": "liminar_pendente",
            "nextDeadlineAt": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "lastEventSummary": "Petição inicial protocolada.",
        },
    )
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["matterId"] == matter["id"]
    assert len(record["events"]) == 1

    duplicate = await client.post(
        "/api/v1/klm/process-monitoring",
        json={
            "matterId": matter["id"],
            "caseNumber": "00012345620268260100",
            "status": "duplicado",
        },
    )
    assert duplicate.status_code == 409

    updated = await client.patch(
        f"/api/v1/klm/process-monitoring/{record['id']}",
        json={"status": "liminar_deferida", "lastEventSummary": "Tutela concedida."},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "liminar_deferida"
    event = await client.post(
        f"/api/v1/klm/process-monitoring/{record['id']}/events",
        json={
            "title": "Intimação publicada",
            "description": "Prazo para cumprimento iniciado.",
            "source": "manual_local",
        },
    )
    assert event.status_code == 200, event.text
    assert len(event.json()["events"]) == 2
    listed = await client.get("/api/v1/klm/process-monitoring", params={"matter_id": matter["id"]})
    assert [item["id"] for item in listed.json()] == [record["id"]]


@pytest.mark.asyncio
async def test_missing_operational_resources_fail_closed(client):
    missing_id = str(uuid4())
    projection = {
        "fixedFee": 5000,
        "successPercentage": 20,
        "economicValueAnnual": 100000,
        "probability": 60,
    }
    responses = [
        await client.patch(f"/api/v1/klm/areas/{missing_id}", json={"name": "Ausente"}),
        await client.patch(
            f"/api/v1/klm/omnichannel/threads/{missing_id}", json={"status": "open"}
        ),
        await client.get(f"/api/v1/klm/omnichannel/threads/{missing_id}/messages"),
        await client.patch(
            f"/api/v1/klm/technical-requests/{missing_id}", json={"status": "analyzing"}
        ),
        await client.post(f"/api/v1/klm/external-intents/{missing_id}/approve-local"),
        await client.patch(
            f"/api/v1/klm/matters/{missing_id}/financial-projection", json=projection
        ),
        await client.get(f"/api/v1/klm/legal-artifacts/{missing_id}"),
        await client.patch(
            f"/api/v1/klm/process-monitoring/{missing_id}", json={"status": "ausente"}
        ),
    ]
    assert [response.status_code for response in responses] == [404] * len(responses)
