"""Sprint S1b — Hermes barato + canal do dono.

Cobre:
1. Template determinístico da notificação de rotina (função pura, sem LLM).
2. Entrega bloqueada com gates externos fechados => local_draft.
3. MetaPolicyGate respeitado (janela fechada => exige template utility, não envia).
4. Janela aberta + gates abertos => envia pelo WhatsAppService existente.
5. Endpoint de configuração do owner_whatsapp_phone (RBAC de admin do workspace).
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalPriority, ApprovalQueueItem, ApprovalStatus
from app.models.client import Client
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import (
    ClientClassification,
    KanbanColumnId,
    Matter,
    MicroStatus,
    PracticeArea,
    Priority,
    Urgency,
)
from app.models.user import AgentType, User, UserRole
from app.models.whatsapp import (
    ConnectionType,
    WaMessageDirection,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.services.klm_agent_service import (
    HERMES_NOTIFICATION_TEMPLATE_VERSION,
    KlmAgentService,
    render_hermes_routine_notification,
)

pytestmark = pytest.mark.usefixtures("active_subscription")

OWNER_PHONE = "5561999998888"


def _approval_stub(
    *,
    title: str = "Hermes solicita intervenção humana - Cliente Teste",
    description: str | None = "Validar a próxima ação do caso.",
    priority: ApprovalPriority = ApprovalPriority.alta,
) -> ApprovalQueueItem:
    item = ApprovalQueueItem(
        matter_id=uuid4(),
        approval_type="agent_handoff",
        intent_key="agent-run:stub",
        title=title,
        description=description,
        priority=priority,
        status=ApprovalStatus.pending,
    )
    return item


async def _seed_case(db: AsyncSession) -> tuple[Workspace, Matter, ApprovalQueueItem]:
    workspace = Workspace(
        name="Workspace Hermes",
        slug=f"workspace-hermes-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db.add(workspace)
    await db.flush()

    user = User(
        name="Dono Teste",
        email=f"dono-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="DT",
        color="#0f172a",
        is_active=True,
        is_internal_staff=False,
    )
    db.add(user)
    await db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.workspace_owner,
            status=MembershipStatus.active,
            is_default=True,
        )
    )
    await db.flush()

    client = Client(
        workspace_id=workspace.id,
        name="Cliente Hermes",
        cpf="000.000.000-00",
    )
    db.add(client)
    await db.flush()

    matter = Matter(
        workspace_id=workspace.id,
        client_id=client.id,
        demand_type="Plano de saúde",
        practice_area=PracticeArea.civel,
        column_id=KanbanColumnId.redacao_peca,
        micro_status=MicroStatus.minuta_em_elaboracao,
        priority=Priority.alta,
        urgency=Urgency.urgente,
        classification=ClientClassification.Premium,
        responsible_human_id=user.id,
    )
    db.add(matter)
    await db.flush()

    approval = ApprovalQueueItem(
        matter_id=matter.id,
        approval_type="agent_handoff",
        intent_key=f"agent-run:{uuid4()}",
        title="Hermes solicita intervenção humana - Cliente Hermes",
        description="Validar a próxima ação do caso.",
        priority=ApprovalPriority.alta,
        status=ApprovalStatus.pending,
    )
    db.add(approval)
    await db.flush()
    return workspace, matter, approval


async def _seed_owner_inbound(
    db: AsyncSession, workspace: Workspace, *, minutes_ago: int
) -> None:
    connection = WhatsAppConnection(
        workspace_id=workspace.id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id=f"pn-{uuid4().hex[:10]}",
    )
    db.add(connection)
    await db.flush()
    contact = WhatsAppContact(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        phone=OWNER_PHONE,
        name="Dono",
    )
    db.add(contact)
    await db.flush()
    db.add(
        WhatsAppMessage(
            contact_id=contact.id,
            whatsapp_connection_id=connection.id,
            direction=WaMessageDirection.inbound,
            body="Oi Hermes",
            timestamp=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        )
    )
    await db.flush()


def _forbid_external_send(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _explode(self, **kwargs):  # pragma: no cover - só dispara em regressão
        raise AssertionError("Envio externo não deveria ter sido tentado.")

    from app.services.whatsapp_service import WhatsAppService

    monkeypatch.setattr(WhatsAppService, "_send_text_direct", _explode)


# ── 1. Template determinístico (sem LLM) ────────────────────────────────────


def test_template_de_rotina_e_deterministico_e_completo() -> None:
    item = _approval_stub()
    first = render_hermes_routine_notification(item, client_name="Cliente Teste")
    second = render_hermes_routine_notification(item, client_name="Cliente Teste")

    assert first == second, "Mesma entrada deve produzir exatamente a mesma saída."
    assert first["templateVersion"] == HERMES_NOTIFICATION_TEMPLATE_VERSION
    assert first["renderer"] == "deterministic_template"
    assert first["title"] == item.title
    assert first["case"] == "Cliente Teste"
    assert first["action"] == item.description
    assert first["priority"] == "alta"
    assert first["priorityLabel"] == "Alta"
    for fragment in (item.title, "Cliente Teste", item.description, "Alta"):
        assert fragment in first["body"]


def test_template_cobre_prioridades_e_campos_ausentes() -> None:
    baixa = render_hermes_routine_notification(
        _approval_stub(priority=ApprovalPriority.baixa, description=None),
        client_name=None,
    )
    assert baixa["priorityLabel"] == "Baixa"
    assert baixa["action"] == "Revisar e decidir o item na fila de aprovação."
    assert baixa["case"].startswith("Caso ")

    media = render_hermes_routine_notification(
        _approval_stub(priority=ApprovalPriority.media), client_name="Fulana"
    )
    assert media["priorityLabel"] == "Média"


# ── 2. Gates externos fechados => local_draft ───────────────────────────────


@pytest.mark.asyncio
async def test_gates_fechados_registra_local_draft_sem_chamada_externa(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _matter, approval = await _seed_case(db_session)
    workspace.owner_whatsapp_phone = OWNER_PHONE
    await db_session.flush()
    _forbid_external_send(monkeypatch)

    notification = await KlmAgentService(db_session)._deliver_hermes_owner_notification(
        workspace_id=workspace.id,
        approval=approval,
        client_name="Cliente Hermes",
    )

    assert notification["deliveryStatus"] == "local_draft"
    assert notification["reasonCode"] == "external_gates_closed"
    assert notification["externalActionsExecuted"] == 0
    assert notification["llmUsed"] is False
    assert notification["template"]["renderer"] == "deterministic_template"

    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == workspace.id,
            KlmAuditEvent.event_type == "hermes_owner_notification",
        )
    )
    assert audit is not None
    assert audit.after_data["deliveryStatus"] == "local_draft"
    assert audit.after_data["llmUsed"] is False


@pytest.mark.asyncio
async def test_sem_telefone_configurado_registra_local_draft(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _matter, approval = await _seed_case(db_session)
    _forbid_external_send(monkeypatch)

    notification = await KlmAgentService(db_session)._deliver_hermes_owner_notification(
        workspace_id=workspace.id,
        approval=approval,
        client_name="Cliente Hermes",
    )

    assert notification["deliveryStatus"] == "local_draft"
    assert notification["reasonCode"] == "owner_whatsapp_phone_not_configured"
    assert notification["externalActionsExecuted"] == 0


# ── 3. MetaPolicyGate respeitado (janela fechada => template utility) ───────


@pytest.mark.asyncio
async def test_janela_fechada_exige_template_utility_e_nao_envia(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _matter, approval = await _seed_case(db_session)
    workspace.owner_whatsapp_phone = OWNER_PHONE
    await db_session.flush()
    # Inbound do dono há 30h => janela de 24h expirada.
    await _seed_owner_inbound(db_session, workspace, minutes_ago=30 * 60)
    monkeypatch.setattr(
        "app.services.klm_agent_service.external_delivery_allowed", lambda: True
    )
    _forbid_external_send(monkeypatch)

    notification = await KlmAgentService(db_session)._deliver_hermes_owner_notification(
        workspace_id=workspace.id,
        approval=approval,
        client_name="Cliente Hermes",
    )

    assert notification["deliveryStatus"] == "local_draft"
    assert notification["reasonCode"] == "window_expired"
    assert notification["requires"] == "template"
    assert notification["externalActionsExecuted"] == 0


@pytest.mark.asyncio
async def test_sem_inbound_do_dono_nao_envia_texto_livre(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _matter, approval = await _seed_case(db_session)
    workspace.owner_whatsapp_phone = OWNER_PHONE
    await db_session.flush()
    monkeypatch.setattr(
        "app.services.klm_agent_service.external_delivery_allowed", lambda: True
    )
    _forbid_external_send(monkeypatch)

    notification = await KlmAgentService(db_session)._deliver_hermes_owner_notification(
        workspace_id=workspace.id,
        approval=approval,
        client_name="Cliente Hermes",
    )

    assert notification["deliveryStatus"] == "local_draft"
    assert notification["reasonCode"] == "no_inbound_yet"
    assert notification["requires"] == "template"


# ── 4. Janela aberta + gates abertos => envia pelo WhatsAppService ──────────


@pytest.mark.asyncio
async def test_janela_aberta_envia_template_pelo_whatsapp_service(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _matter, approval = await _seed_case(db_session)
    workspace.owner_whatsapp_phone = OWNER_PHONE
    await db_session.flush()
    await _seed_owner_inbound(db_session, workspace, minutes_ago=60)
    monkeypatch.setattr(
        "app.services.klm_agent_service.external_delivery_allowed", lambda: True
    )

    sent_calls: list[dict] = []

    async def _fake_send(
        self,
        *,
        phone,
        body,
        workspace_id,
        idempotency_key,
        connection_id=None,
    ):
        sent_calls.append(
            {
                "phone": phone,
                "body": body,
                "workspace_id": workspace_id,
                "idempotency_key": idempotency_key,
            }
        )
        return True

    from app.services.whatsapp_service import WhatsAppService

    monkeypatch.setattr(WhatsAppService, "_send_text_direct", _fake_send)

    notification = await KlmAgentService(db_session)._deliver_hermes_owner_notification(
        workspace_id=workspace.id,
        approval=approval,
        client_name="Cliente Hermes",
    )

    assert notification["deliveryStatus"] == "sent"
    assert notification["externalActionsExecuted"] == 1
    assert len(sent_calls) == 1
    assert sent_calls[0]["phone"] == OWNER_PHONE
    assert sent_calls[0]["workspace_id"] == workspace.id
    assert sent_calls[0]["idempotency_key"] == (
        f"hermes-owner-notification:{approval.id}"
    )
    # O corpo enviado é o TEMPLATE determinístico, nunca saída de LLM.
    assert sent_calls[0]["body"] == notification["template"]["body"]


# ── 5. Endpoint de configuração (RBAC admin do workspace) ───────────────────


@pytest.mark.asyncio
async def test_endpoint_configura_e_remove_telefone_do_dono(client) -> None:
    response = await client.put(
        "/api/v1/workspaces/settings/owner-channel",
        json={"ownerWhatsappPhone": "+55 (61) 99999-8888"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ownerWhatsappPhone"] == OWNER_PHONE
    assert payload["configured"] is True

    read_back = await client.get("/api/v1/workspaces/settings/owner-channel")
    assert read_back.status_code == 200
    assert read_back.json()["ownerWhatsappPhone"] == OWNER_PHONE

    cleared = await client.put(
        "/api/v1/workspaces/settings/owner-channel",
        json={"ownerWhatsappPhone": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["configured"] is False
    assert cleared.json()["ownerWhatsappPhone"] is None


@pytest.mark.asyncio
async def test_endpoint_rejeita_telefone_invalido(client) -> None:
    response = await client.put(
        "/api/v1/workspaces/settings/owner-channel",
        json={"ownerWhatsappPhone": "123"},
    )
    assert response.status_code == 422
