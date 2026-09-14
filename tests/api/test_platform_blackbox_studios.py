"""Segurança de público dos Studios analíticos do Blackbox."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.main import app
from app.models.configuration import PlatformConfigDocument
from app.models.growth_crm import GrowthFunnelStage, GrowthProspect
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.support_ticket import (
    SupportTicket,
    SupportTicketPriority,
    SupportTicketQueue,
    SupportTicketSeverity,
    SupportTicketStatus,
    SupportTicketType,
)
from app.models.user import AgentType, User, UserRole
from app.services.platform_blackbox_studios_service import (
    CREATIVE_LIBRARY_NAMESPACE,
    PlatformBlackboxStudiosService,
)
from app.services.platform_audit_event_service import PlatformAuditEventService

pytestmark = pytest.mark.asyncio


async def _platform_client_for_role(
    db_session,
    *,
    role_package: str,
) -> AsyncIterator[AsyncClient]:
    user = User(
        name=f"Funcionário {role_package}",
        email=f"{role_package}@blackbox.test",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="BB",
        color="#0e3d5c",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_access_active=True,
        platform_role_package=role_package,
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
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def sdr_blackbox_client(db_session):
    async for client in _platform_client_for_role(
        db_session,
        role_package="sales_sdr",
    ):
        yield client


@pytest_asyncio.fixture
async def manager_blackbox_client(db_session):
    async for client in _platform_client_for_role(
        db_session,
        role_package="commercial_manager",
    ):
        yield client


@pytest_asyncio.fixture
async def marketing_blackbox_client(db_session):
    async for client in _platform_client_for_role(
        db_session,
        role_package="marketing_content",
    ):
        yield client


@pytest_asyncio.fixture
async def finance_blackbox_client(db_session):
    async for client in _platform_client_for_role(
        db_session,
        role_package="finance_billing",
    ):
        yield client


async def test_workspace_identity_cannot_open_blackbox_studios(client):
    response = await client.get(
        "/api/v1/platform/blackbox-studios/performance/saas-team"
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "BLACKBOX-IDENTITY-REQUIRED"


async def test_sdr_cannot_read_management_performance_or_reports(
    sdr_blackbox_client,
):
    performance = await sdr_blackbox_client.get(
        "/api/v1/platform/blackbox-studios/performance/saas-team"
    )
    report = await sdr_blackbox_client.get(
        "/api/v1/platform/blackbox-studios/reports"
    )
    assert performance.status_code == 403
    assert report.status_code == 403
    assert performance.json()["detail"]["module"] == "performance"
    assert report.json()["detail"]["module"] == "reports"


async def test_commercial_manager_sees_saas_team_but_not_workspace_data(
    manager_blackbox_client,
):
    saas_team = await manager_blackbox_client.get(
        "/api/v1/platform/blackbox-studios/performance/saas-team"
    )
    workspace_team = await manager_blackbox_client.get(
        "/api/v1/platform/blackbox-studios/performance/workspace-team"
    )
    benchmark = await manager_blackbox_client.get(
        "/api/v1/platform/blackbox-studios/performance/workspace-benchmark"
    )

    assert saas_team.status_code == 200
    assert saas_team.json()["audience"] == "saas_team"
    assert workspace_team.status_code == 403
    assert benchmark.status_code == 403
    assert workspace_team.json()["detail"]["code"] == "PLATFORM-ROOT-REQUIRED"


async def test_root_can_open_cross_workspace_analytics(platform_client):
    workspace_team = await platform_client.get(
        "/api/v1/platform/blackbox-studios/performance/workspace-team"
    )
    benchmark = await platform_client.get(
        "/api/v1/platform/blackbox-studios/performance/workspace-benchmark"
    )
    library = await platform_client.get(
        "/api/v1/platform/blackbox-studios/creative-library"
    )

    assert workspace_team.status_code == 200
    assert workspace_team.json()["audience"] == "workspace_team"
    assert benchmark.status_code == 200
    assert benchmark.json()["audience"] == "workspace_benchmark"
    assert library.status_code == 200


async def test_saas_performance_uses_resolution_and_conversion_dates(
    db_session,
    auth_context,
):
    now = datetime.now(UTC)
    outside_period = now - timedelta(days=90)
    inside_period = now - timedelta(days=2)
    employee = User(
        name="Funcionário com resultado recente",
        email="resultado-recente@blackbox.test",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="FR",
        color="#0e3d5c",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_access_active=True,
        platform_role_package="commercial_manager",
    )
    db_session.add(employee)
    await db_session.flush()
    db_session.add(
        SupportTicket(
            public_number="SAC-PERIOD-0001",
            workspace_id=auth_context.workspace_id,
            reporter_user_id=auth_context.user_id,
            assigned_to_user_id=employee.id,
            ticket_type=SupportTicketType.problem,
            priority=SupportTicketPriority.normal,
            severity=SupportTicketSeverity.medium,
            queue=SupportTicketQueue.support,
            status=SupportTicketStatus.resolved,
            subject="Ticket criado antes e resolvido agora",
            description="Validação da janela temporal da gamificação.",
            technical_context={},
            idempotency_key="performance-period-ticket",
            version=1,
            first_response_due_at=outside_period + timedelta(hours=4),
            resolution_due_at=outside_period + timedelta(days=3),
            resolved_at=inside_period,
            created_at=outside_period,
            updated_at=inside_period,
        )
    )
    db_session.add(
        GrowthProspect(
            nome_escritorio="Escritório convertido recentemente",
            owner_user_id=employee.id,
            estagio=GrowthFunnelStage.pagante,
            won_at=inside_period,
            created_at=outside_period,
            updated_at=inside_period,
            metadata_json={},
        )
    )
    await db_session.flush()

    board = await PlatformBlackboxStudiosService(db_session).saas_team_performance(
        period_days=30
    )
    row = next(item for item in board.rows if item.id == str(employee.id))

    assert row.primary_count == 0
    assert row.completed_count == 2
    assert row.score == 20


async def test_saas_performance_uses_stable_server_pages(
    platform_client,
    db_session,
):
    for index in range(30):
        db_session.add(
            User(
                name=f"Funcionário paginado {index:02d}",
                email=f"performance-page-{index:02d}@blackbox.test",
                agent_type=AgentType.human,
                role=UserRole.admin,
                initials="FP",
                color="#0e3d5c",
                is_active=True,
                is_internal_staff=True,
                is_platform_admin=False,
                platform_access_active=True,
                platform_role_package="support",
            )
        )
    await db_session.flush()

    first = await platform_client.get(
        "/api/v1/platform/blackbox-studios/performance/saas-team",
        params={"periodDays": 30, "limit": 10, "offset": 0},
    )
    second = await platform_client.get(
        "/api/v1/platform/blackbox-studios/performance/saas-team",
        params={"periodDays": 30, "limit": 10, "offset": 10},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_page = first.json()
    second_page = second.json()
    assert first_page["total"] >= 30
    assert first_page["limit"] == 10
    assert first_page["offset"] == 0
    assert first_page["hasMore"] is True
    assert second_page["offset"] == 10
    assert len(first_page["rows"]) == 10
    assert len(second_page["rows"]) == 10
    assert {row["id"] for row in first_page["rows"]}.isdisjoint(
        {row["id"] for row in second_page["rows"]}
    )


async def test_root_manages_creative_library_with_optimistic_concurrency(
    platform_client,
    db_session,
):
    created = await platform_client.put(
        "/api/v1/platform/blackbox-studios/creative-library/items",
        json={
            "name": "Criativo saúde consultiva",
            "kind": "creative",
            "channel": "meta",
            "objective": "Qualificar empresas interessadas em benefício saúde.",
            "status": "approved",
            "tags": ["B2B", "saúde", "B2B"],
            "copyText": "Converse com uma equipe jurídica especializada.",
        },
    )
    assert created.status_code == 200, created.text
    library = created.json()
    assert library["currentVersion"] == 1
    assert len(library["items"]) == 1
    assert library["items"][0]["tags"] == ["B2B", "saúde"]

    stale_update = await platform_client.put(
        "/api/v1/platform/blackbox-studios/creative-library/items",
        json={
            "id": library["items"][0]["id"],
            "name": "Criativo alterado sem versão",
            "kind": "creative",
            "channel": "meta",
            "objective": "Não deve sobrescrever silenciosamente.",
            "expectedEtag": "versao-antiga",
        },
    )
    assert stale_update.status_code == 409
    assert stale_update.json()["detail"]["code"] == "BLACKBOX-STUDIO-CONFLICT"

    archived = await platform_client.post(
        (
            "/api/v1/platform/blackbox-studios/creative-library/items/"
            f"{library['items'][0]['id']}/archive"
        ),
        json={"expectedEtag": library["etag"]},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["items"][0]["status"] == "archived"
    assert archived.json()["currentVersion"] == 2
    canonical = list(await db_session.scalars(
        select(PlatformAuditEvent)
        .where(PlatformAuditEvent.resource_id == CREATIVE_LIBRARY_NAMESPACE)
        .order_by(PlatformAuditEvent.occurred_at.asc())
    ))
    assert [event.action for event in canonical] == [
        "upsert_creative_library_item",
        "archive_creative_library_item",
    ]
    assert canonical[0].before is None
    assert canonical[1].before["currentVersion"] == 1
    assert canonical[1].after["currentVersion"] == 2


async def test_creative_library_page_filters_before_pagination(
    platform_client,
    db_session,
):
    now = datetime.now(UTC)
    items = []
    for index in range(30):
        timestamp = (now + timedelta(seconds=index)).isoformat()
        items.append(
            {
                "id": f"creative-page-{index:02d}",
                "name": f"Criativo paginado {index:02d}",
                "kind": "creative",
                "channel": "meta",
                "objective": "Provar paginação autoritativa.",
                "status": "archived" if index < 5 else "approved",
                "tags": ["paginado"],
                "assetUrl": None,
                "copyText": None,
                "notes": None,
                "createdAt": timestamp,
                "updatedAt": timestamp,
            }
        )
    db_session.add(
        PlatformConfigDocument(
            namespace=CREATIVE_LIBRARY_NAMESPACE,
            current_version=7,
            etag="creative-page-etag",
            payload={"items": items},
        )
    )
    await db_session.flush()

    first = await platform_client.get(
        "/api/v1/platform/blackbox-studios/creative-library/page",
        params={"status": "active", "limit": 10, "offset": 0},
    )
    second = await platform_client.get(
        "/api/v1/platform/blackbox-studios/creative-library/page",
        params={"status": "active", "limit": 10, "offset": 10},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_page = first.json()
    second_page = second.json()
    assert first_page["currentVersion"] == 7
    assert first_page["total"] == 25
    assert first_page["hasMore"] is True
    assert len(first_page["items"]) == 10
    assert second_page["offset"] == 10
    assert {item["id"] for item in first_page["items"]}.isdisjoint(
        {item["id"] for item in second_page["items"]}
    )
    assert all(item["status"] != "archived" for item in first_page["items"])


async def test_creative_library_rolls_back_when_canonical_audit_fails(
    platform_client,
    db_session,
    monkeypatch,
):
    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.put(
            "/api/v1/platform/blackbox-studios/creative-library/items",
            json={
                "name": "Criativo que deve ser revertido",
                "kind": "creative",
                "channel": "site",
                "objective": "Provar atomicidade da biblioteca e auditoria.",
            },
        )

    db_session.expire_all()
    document = await db_session.scalar(
        select(PlatformConfigDocument).where(
            PlatformConfigDocument.namespace == CREATIVE_LIBRARY_NAMESPACE
        )
    )
    assert document is None


async def test_advanced_report_applies_filter_sort_and_limit(platform_client):
    initial = await platform_client.get(
        "/api/v1/platform/blackbox-studios/reports",
        params={"periodDays": 30, "limit": 500},
    )
    assert initial.status_code == 200, initial.text
    payload = initial.json()
    assert payload["availableCategories"]
    assert payload["rows"]

    selected = payload["rows"][0]
    filtered = await platform_client.get(
        "/api/v1/platform/blackbox-studios/reports",
        params={
            "periodDays": 14,
            "category": selected["category"],
            "query": selected["label"],
            "sortBy": "value",
            "sortDirection": "desc",
            "limit": 2,
        },
    )
    assert filtered.status_code == 200, filtered.text
    report = filtered.json()
    assert report["periodDays"] == 14
    assert report["appliedFilters"]["category"] == selected["category"]
    assert report["appliedFilters"]["query"] == selected["label"]
    assert len(report["rows"]) <= 2
    assert all(row["category"] == selected["category"] for row in report["rows"])
    assert all(
        selected["label"].casefold() in row["label"].casefold()
        for row in report["rows"]
    )


async def test_advanced_report_returns_stable_server_pages(platform_client):
    first = await platform_client.get(
        "/api/v1/platform/blackbox-studios/reports",
        params={"periodDays": 30, "limit": 1, "offset": 0},
    )
    second = await platform_client.get(
        "/api/v1/platform/blackbox-studios/reports",
        params={"periodDays": 30, "limit": 1, "offset": 1},
    )

    assert first.status_code == second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["totalRows"] > 1
    assert first_payload["limit"] == 1
    assert first_payload["offset"] == 0
    assert first_payload["hasMore"] is True
    assert second_payload["offset"] == 1
    assert second_payload["rowsWithData"] == first_payload["rowsWithData"]
    assert first_payload["rows"][0]["key"] != second_payload["rows"][0]["key"]


async def test_advanced_report_hides_financial_category_from_marketing(
    marketing_blackbox_client,
):
    response = await marketing_blackbox_client.get(
        "/api/v1/platform/blackbox-studios/reports",
        params={"category": "receita", "limit": 500},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert "receita" not in {
        category["key"] for category in report["availableCategories"]
    }
    assert report["rows"] == []


async def test_advanced_report_never_exposes_ai_costs_to_non_root_staff(
    marketing_blackbox_client,
    finance_blackbox_client,
):
    for client in (marketing_blackbox_client, finance_blackbox_client):
        response = await client.get(
            "/api/v1/platform/blackbox-studios/reports",
            params={"category": "operacao_ia", "limit": 500},
        )
        assert response.status_code == 200, response.text
        keys = {row["key"] for row in response.json()["rows"]}
        assert not any(key.startswith("ia.custo") for key in keys)
        assert any(key.startswith("ia.tokens") for key in keys)


async def test_advanced_report_finance_sees_revenue_without_root_only_costs(
    finance_blackbox_client,
):
    response = await finance_blackbox_client.get(
        "/api/v1/platform/blackbox-studios/reports",
        params={"category": "receita", "limit": 500},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    keys = {row["key"] for row in report["rows"]}
    assert "receita" in {
        category["key"] for category in report["availableCategories"]
    }
    assert "receita.mrr" in keys
    assert not any(
        key.startswith(
            (
                "receita.custo",
                "receita.cac",
                "receita.lucro",
                "receita.margem",
                "receita.ebitda",
            )
        )
        for key in keys
    )


async def test_advanced_report_finance_view_can_read_non_sensitive_revenue(
    manager_blackbox_client,
):
    response = await manager_blackbox_client.get(
        "/api/v1/platform/blackbox-studios/reports",
        params={"category": "receita", "limit": 500},
    )
    assert response.status_code == 200, response.text
    keys = {row["key"] for row in response.json()["rows"]}
    assert "receita.mrr" in keys
    assert not any(
        key.startswith(
            (
                "receita.custo",
                "receita.cac",
                "receita.lucro",
                "receita.margem",
                "receita.ebitda",
            )
        )
        for key in keys
    )


async def test_advanced_report_root_sees_cost_and_profit_metrics(platform_client):
    response = await platform_client.get(
        "/api/v1/platform/blackbox-studios/reports",
        params={"category": "receita", "limit": 500},
    )
    assert response.status_code == 200, response.text
    keys = {row["key"] for row in response.json()["rows"]}
    assert "receita.custo_total" in keys
    assert "receita.cac" in keys
    assert "receita.lucro_liquido" in keys

    ai_response = await platform_client.get(
        "/api/v1/platform/blackbox-studios/reports",
        params={"category": "operacao_ia", "limit": 500},
    )
    assert ai_response.status_code == 200, ai_response.text
    ai_keys = {row["key"] for row in ai_response.json()["rows"]}
    assert any(key.startswith("ia.custo") for key in ai_keys)
