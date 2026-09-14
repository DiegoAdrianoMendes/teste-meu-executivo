"""
Testes dos endpoints do Growth CRM da plataforma (/api/v1/platform/growth/*).

Cobre: 403 para usuário não-staff em cada rota; CRUD de prospects; mover
estágio grava evento e reflete no kanban; registrar atividade; métricas do
funil refletem conversão/win-rate.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.main import app
from app.models.growth_crm import GrowthFunnelStage, GrowthProspect
from app.models.user import AgentType, User, UserRole
from app.models.identity import InvitationStatus, WorkspaceInvitation
from app.models.auth_security import AuthSecurityEvent
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.workspace import Workspace, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.services.platform_audit_event_service import PlatformAuditEventService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def sales_client(db_session):
    user = User(
        name="SDR da plataforma",
        email="sdr-plataforma@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="SP",
        color="#0e3d5c",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_access_active=True,
        platform_role_package="sales_sdr",
    )
    db_session.add(user)
    await db_session.flush()
    context = AuthContext(
        user_id=user.id,
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )

    async def override_get_db():
        yield db_session

    async def override_context():
        return context

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_auth_context] = override_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def support_client(db_session):
    user = User(
        name="Suporte da plataforma",
        email="suporte-plataforma@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="SP",
        color="#0e3d5c",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_access_active=True,
        platform_role_package="support",
    )
    db_session.add(user)
    await db_session.flush()
    context = AuthContext(
        user_id=user.id,
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )

    async def override_get_db():
        yield db_session

    async def override_context():
        return context

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_auth_context] = override_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def test_pipeline_requires_platform_staff(client):
    response = await client.get("/api/v1/platform/growth/pipeline")
    assert response.status_code == 403


async def test_prospects_list_requires_platform_staff(client):
    response = await client.get("/api/v1/platform/growth/prospects")
    assert response.status_code == 403


async def test_prospects_return_real_stable_pages(db_session, platform_client):
    prefix = f"Lead paginado {uuid4().hex[:8]}"
    db_session.add_all([
        GrowthProspect(
            nome_escritorio=f"{prefix} {index:02d}",
            estagio=GrowthFunnelStage.prospect,
            valor_estimado_cents=19700,
            metadata_json={},
        )
        for index in range(55)
    ])
    await db_session.commit()

    first = await platform_client.get(
        "/api/v1/platform/growth/prospects",
        params={"search": prefix, "estagio": "prospect", "limit": 25, "offset": 0},
    )
    second = await platform_client.get(
        "/api/v1/platform/growth/prospects",
        params={"search": prefix, "estagio": "prospect", "limit": 25, "offset": 25},
    )
    last = await platform_client.get(
        "/api/v1/platform/growth/prospects",
        params={"search": prefix, "estagio": "prospect", "limit": 25, "offset": 50},
    )

    assert first.status_code == second.status_code == last.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    last_payload = last.json()
    assert first_payload["total"] == first_payload["count"] == 55
    assert first_payload["hasMore"] is True
    assert second_payload["hasMore"] is True
    assert last_payload["hasMore"] is False
    assert len(first_payload["items"]) == len(second_payload["items"]) == 25
    assert len(last_payload["items"]) == 5
    assert {item["id"] for item in first_payload["items"]}.isdisjoint(
        {item["id"] for item in second_payload["items"]}
    )


async def test_create_prospect_requires_platform_staff(client):
    response = await client.post(
        "/api/v1/platform/growth/prospects", json={"nomeEscritorio": "Deveria Falhar"}
    )
    assert response.status_code == 403


async def test_sales_role_can_read_and_manage_growth_crm(sales_client):
    created = await sales_client.post(
        "/api/v1/platform/growth/prospects",
        json={"nomeEscritorio": "Conta prospectada pelo SDR"},
    )
    assert created.status_code == 201

    listed = await sales_client.get("/api/v1/platform/growth/prospects")
    assert listed.status_code == 200
    assert any(item["id"] == created.json()["id"] for item in listed.json()["items"])

    assignees = await sales_client.get("/api/v1/platform/growth/assignees")
    assert assignees.status_code == 200
    assert any(item["name"] == "SDR da plataforma" for item in assignees.json())

    conversion = await sales_client.post(
        f"/api/v1/platform/growth/prospects/{created.json()['id']}/convert",
        headers={"Idempotency-Key": f"sdr-convert-{uuid4()}"},
        json={
            "workspaceName": "Conta convertida pelo SDR",
            "ownerName": "Titular Comercial",
            "ownerEmail": f"titular-sdr-{uuid4().hex[:8]}@example.com",
            "planKey": "basic",
            "setupMode": "assisted",
        },
    )
    assert conversion.status_code == 201
    assert conversion.json()["workspace_status"] == "provisioning"


async def test_unrelated_platform_role_cannot_open_growth_crm(support_client):
    read = await support_client.get("/api/v1/platform/growth/pipeline")
    write = await support_client.post(
        "/api/v1/platform/growth/prospects",
        json={"nomeEscritorio": "Não deve ser criado"},
    )
    assert read.status_code == 403
    assert write.status_code == 403
    assert read.json()["detail"]["code"] == "PLATFORM-PERMISSION-DENIED"


async def test_create_and_list_prospect(platform_client):
    response = await platform_client.post(
        "/api/v1/platform/growth/prospects",
        json={
            "nomeEscritorio": "Escritório Endpoint Teste",
            "contatoEmail": "contato@endpoint.adv.br",
            "origem": "outbound",
            "valorEstimadoCents": 49700,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["nome_escritorio"] == "Escritório Endpoint Teste"
    assert body["estagio"] == "prospect"
    prospect_id = body["id"]

    listed = await platform_client.get("/api/v1/platform/growth/prospects")
    assert listed.status_code == 200
    assert any(item["id"] == prospect_id for item in listed.json()["items"])


async def test_move_stage_updates_pipeline_and_records_event(db_session, platform_client):
    created = await platform_client.post(
        "/api/v1/platform/growth/prospects",
        json={"nomeEscritorio": "Escritório Kanban Endpoint"},
    )
    prospect_id = created.json()["id"]

    moved = await platform_client.patch(
        f"/api/v1/platform/growth/prospects/{prospect_id}/stage",
        json={"toEstagio": "contatado", "motivo": "Primeira ligação feita."},
    )
    assert moved.status_code == 200
    assert moved.json()["estagio"] == "contatado"

    pipeline = await platform_client.get("/api/v1/platform/growth/pipeline")
    assert pipeline.status_code == 200
    board = pipeline.json()
    assert any(item["id"] == prospect_id for item in board["contatado"])

    detail = await platform_client.get(f"/api/v1/platform/growth/prospects/{prospect_id}")
    assert detail.status_code == 200
    stage_events = detail.json()["stage_events"]
    assert len(stage_events) == 2  # criação + contatado
    canonical = await db_session.scalar(
        select(PlatformAuditEvent)
        .where(
            PlatformAuditEvent.action == "move_growth_prospect_stage",
            PlatformAuditEvent.resource_id == prospect_id,
        )
        .order_by(PlatformAuditEvent.occurred_at.desc())
    )
    assert canonical is not None
    assert canonical.before["stage"] == "prospect"
    assert canonical.after["stage"] == "contatado"
    assert canonical.reason == "Primeira ligação feita."
    assert "stage" in canonical.changed_paths


async def test_move_stage_invalid_target_returns_422(platform_client):
    created = await platform_client.post(
        "/api/v1/platform/growth/prospects",
        json={"nomeEscritorio": "Escritório Estágio Inválido"},
    )
    prospect_id = created.json()["id"]

    response = await platform_client.patch(
        f"/api/v1/platform/growth/prospects/{prospect_id}/stage",
        json={"toEstagio": "nao_existe"},
    )
    assert response.status_code == 422


async def test_move_stage_unknown_prospect_returns_404(platform_client):
    response = await platform_client.patch(
        "/api/v1/platform/growth/prospects/00000000-0000-0000-0000-000000000000/stage",
        json={"toEstagio": "contatado"},
    )
    assert response.status_code == 404


async def test_register_activity(platform_client):
    created = await platform_client.post(
        "/api/v1/platform/growth/prospects",
        json={"nomeEscritorio": "Escritório Atividade"},
    )
    prospect_id = created.json()["id"]

    activity = await platform_client.post(
        f"/api/v1/platform/growth/prospects/{prospect_id}/activities",
        json={"tipo": "ligacao", "descricao": "Primeira ligação de qualificação."},
    )
    assert activity.status_code == 201
    assert activity.json()["tipo"] == "ligacao"

    detail = await platform_client.get(f"/api/v1/platform/growth/prospects/{prospect_id}")
    assert len(detail.json()["activities"]) == 1


async def test_funnel_metrics_reflect_won_and_lost(platform_client):
    won = await platform_client.post(
        "/api/v1/platform/growth/prospects",
        json={"nomeEscritorio": "Escritório Métricas Won", "valorEstimadoCents": 10000},
    )
    lost = await platform_client.post(
        "/api/v1/platform/growth/prospects",
        json={"nomeEscritorio": "Escritório Métricas Lost", "valorEstimadoCents": 20000},
    )
    await platform_client.patch(
        f"/api/v1/platform/growth/prospects/{won.json()['id']}/stage",
        json={"toEstagio": "pagante"},
    )
    await platform_client.patch(
        f"/api/v1/platform/growth/prospects/{lost.json()['id']}/stage",
        json={"toEstagio": "perdido", "motivo": "Sem orçamento para contratar."},
    )

    metrics = await platform_client.get("/api/v1/platform/growth/funnel-metrics")
    assert metrics.status_code == 200
    body = metrics.json()
    assert body["won"] >= 1
    assert body["lost"] >= 1
    assert "win_rate_pct" in body


async def test_lost_stage_requires_and_persists_loss_reason(platform_client):
    created = await platform_client.post(
        "/api/v1/platform/growth/prospects",
        json={"nomeEscritorio": "Escritório Motivo de Perda"},
    )
    prospect_id = created.json()["id"]

    missing = await platform_client.patch(
        f"/api/v1/platform/growth/prospects/{prospect_id}/stage",
        json={"toEstagio": "perdido", "motivo": ""},
    )
    assert missing.status_code == 422

    moved = await platform_client.patch(
        f"/api/v1/platform/growth/prospects/{prospect_id}/stage",
        json={"toEstagio": "perdido", "motivo": "Optou por solução concorrente."},
    )
    assert moved.status_code == 200
    assert moved.json()["motivo_perda"] == "Optou por solução concorrente."


async def test_update_prospect_patches_fields(platform_client):
    created = await platform_client.post(
        "/api/v1/platform/growth/prospects",
        json={"nomeEscritorio": "Escritório Update"},
    )
    prospect_id = created.json()["id"]

    updated = await platform_client.patch(
        f"/api/v1/platform/growth/prospects/{prospect_id}",
        json={
            "cidade": "Brasília",
            "uf": "DF",
            "score": 83,
            "nextAction": "Apresentar proposta ao sócio",
            "nextActionAt": "2026-08-01T14:30:00",
            "campaign": "Founder-led Q3",
            "utmSource": "linkedin",
            "utmMedium": "paid-social",
            "utmCampaign": "blackbox-q3",
            "proposalStatus": "sent",
            "proposalReference": "PROP-2026-0042",
            "proposalValueCents": 149900,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["cidade"] == "Brasília"
    assert updated.json()["uf"] == "DF"
    assert updated.json()["score"] == 83
    assert updated.json()["next_action"] == "Apresentar proposta ao sócio"
    assert updated.json()["next_action_at"] == "2026-08-01T14:30:00+00:00"
    assert updated.json()["campaign"] == "Founder-led Q3"
    assert updated.json()["utm_source"] == "linkedin"
    assert updated.json()["utm_medium"] == "paid-social"
    assert updated.json()["utm_campaign"] == "blackbox-q3"
    assert updated.json()["proposal_status"] == "sent"
    assert updated.json()["proposal_reference"] == "PROP-2026-0042"
    assert updated.json()["proposal_value_cents"] == 149900

    detail = await platform_client.get(f"/api/v1/platform/growth/prospects/{prospect_id}")
    assert detail.status_code == 200
    assert detail.json()["score"] == 83
    assert detail.json()["proposal_reference"] == "PROP-2026-0042"

    invalid_owner = await platform_client.patch(
        f"/api/v1/platform/growth/prospects/{prospect_id}",
        json={"ownerUserId": str(uuid4())},
    )
    assert invalid_owner.status_code == 422


async def test_convert_prospect_creates_workspace_owner_invitation_and_links_pipeline(
    db_session,
    platform_client,
):
    created = await platform_client.post(
        "/api/v1/platform/growth/prospects",
        json={
            "nomeEscritorio": "Escritório Convertido",
            "contatoNome": "Carla Titular",
            "contatoEmail": "carla-titular@example.com",
            "origem": "inbound_site",
        },
    )
    prospect_id = created.json()["id"]
    headers = {"Idempotency-Key": f"growth-convert-{uuid4()}"}
    response = await platform_client.post(
        f"/api/v1/platform/growth/prospects/{prospect_id}/convert",
        headers=headers,
        json={
            "workspaceName": "Escritório Convertido",
            "ownerName": "Carla Titular",
            "ownerEmail": "carla-titular@example.com",
            "planKey": "pro",
            "setupMode": "assisted",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["already_converted"] is False
    workspace = await db_session.get(Workspace, payload["workspace_id"])
    assert workspace is not None
    assert workspace.status == WorkspaceStatus.provisioning
    assert workspace.is_active is False
    invitation = await db_session.get(WorkspaceInvitation, payload["invitation_id"])
    assert invitation is not None
    assert invitation.status == InvitationStatus.pending
    membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.id == invitation.membership_id,
        )
    )
    assert membership is not None
    assert membership.role == WorkspaceRole.workspace_owner
    assert membership.status == MembershipStatus.invited
    detail = await platform_client.get(f"/api/v1/platform/growth/prospects/{prospect_id}")
    assert detail.json()["workspace_id"] == payload["workspace_id"]
    assert detail.json()["estagio"] == "pagante"
    audit = await db_session.scalar(
        select(AuthSecurityEvent).where(
            AuthSecurityEvent.event_type == "platform_growth_prospect_converted",
            AuthSecurityEvent.subject_user_id == invitation.user_id,
        )
    )
    assert audit is not None
    assert audit.details["workspace_id"] == payload["workspace_id"]

    replay = await platform_client.post(
        f"/api/v1/platform/growth/prospects/{prospect_id}/convert",
        headers=headers,
        json={
            "workspaceName": "Escritório Convertido",
            "ownerName": "Carla Titular",
            "ownerEmail": "carla-titular@example.com",
            "planKey": "pro",
            "setupMode": "assisted",
        },
    )
    assert replay.status_code == 201
    assert replay.json() == {
        "prospect_id": prospect_id,
        "workspace_id": payload["workspace_id"],
        "already_converted": True,
    }


async def test_conversion_rolls_back_workspace_and_invitation_when_audit_fails(
    db_session,
    platform_client,
    monkeypatch,
):
    created = await platform_client.post(
        "/api/v1/platform/growth/prospects",
        json={"nomeEscritorio": "Conversão com rollback"},
    )
    assert created.status_code == 201
    prospect_id = created.json()["id"]
    owner_email = f"rollback-growth-{uuid4().hex[:8]}@example.com"

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.post(
            f"/api/v1/platform/growth/prospects/{prospect_id}/convert",
            headers={"Idempotency-Key": f"growth-convert-{uuid4()}"},
            json={
                "workspaceName": "Conta que deve ser revertida",
                "ownerName": "Titular com rollback",
                "ownerEmail": owner_email,
                "planKey": "pro",
                "setupMode": "assisted",
            },
        )

    db_session.expire_all()
    prospect = await db_session.get(GrowthProspect, prospect_id)
    workspace = await db_session.scalar(
        select(Workspace).where(Workspace.name == "Conta que deve ser revertida")
    )
    invitation = await db_session.scalar(
        select(WorkspaceInvitation).where(WorkspaceInvitation.invited_email == owner_email)
    )
    assert prospect is not None
    assert prospect.workspace_id is None
    assert prospect.estagio.value == "prospect"
    assert workspace is None
    assert invitation is None
