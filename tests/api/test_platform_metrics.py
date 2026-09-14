"""
Testes dos endpoints de KPIs do Super Admin: GET /platform/metrics e
GET /platform/metrics/{categoria}.

Cobre: staff-only 403, resposta agrupada por categoria, filtro por categoria
específica, e 404 para categoria desconhecida.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription_commercial import WorkspaceSubscription, WorkspaceSubscriptionStatus
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus

pytestmark = pytest.mark.asyncio


def _workspace(name: str) -> Workspace:
    return Workspace(
        name=name,
        slug=f"metrics-api-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )


async def _seed(db_session: AsyncSession) -> None:
    workspace = _workspace("Metrics API WS")
    db_session.add(workspace)
    await db_session.flush()
    db_session.add(
        WorkspaceSubscription(
            workspace_id=workspace.id,
            plano="essencial",
            status=WorkspaceSubscriptionStatus.ativa,
            valor_mensal_cents=19700,
            ciclo="mensal",
            origem="test",
        )
    )
    await db_session.commit()


async def test_metrics_requires_platform_staff(client):
    response = await client.get("/api/v1/platform/metrics")
    assert response.status_code == 403


async def test_metrics_category_requires_platform_staff(client):
    response = await client.get("/api/v1/platform/metrics/receita")
    assert response.status_code == 403


async def test_metrics_returns_all_categories(db_session, platform_client):
    await _seed(db_session)

    response = await platform_client.get("/api/v1/platform/metrics")
    assert response.status_code == 200
    payload = response.json()
    category_keys = {c["key"] for c in payload["categories"]}
    assert category_keys == {
        "receita", "assinaturas", "cobranca", "retencao", "churn",
        "aquisicao", "vendas", "onboarding", "sucesso_cliente", "suporte",
        "conversas", "canais", "produto", "ia", "infraestrutura", "equipe",
        "seguranca",
    }
    assert payload["total_kpis"] >= 100

    receita = next(c for c in payload["categories"] if c["key"] == "receita")
    mrr = next(k for k in receita["kpis"] if k["key"] == "receita.mrr")
    assert mrr["value"] >= 197.0
    assert mrr["sem_dado"] is False
    assert mrr["source"]
    assert mrr["formula"]
    assert mrr["quality"] == "atualizado"
    assert mrr["updated_at"]


async def test_metrics_period_days_filter_is_honored(db_session, platform_client):
    await _seed(db_session)

    response = await platform_client.get("/api/v1/platform/metrics", params={"periodDays": 7})
    assert response.status_code == 200
    assert response.json()["period_days"] == 7


async def test_metrics_category_endpoint_returns_single_category(db_session, platform_client):
    await _seed(db_session)

    response = await platform_client.get("/api/v1/platform/metrics/assinantes")
    assert response.status_code == 200
    payload = response.json()
    assert payload["key"] == "assinaturas"
    assert all(k["category"] == "assinaturas" for k in payload["kpis"])


async def test_metrics_category_endpoint_404_for_unknown_category(platform_client):
    response = await platform_client.get("/api/v1/platform/metrics/inexistente")
    assert response.status_code == 404


async def test_metrics_sem_dado_kpis_never_fabricate_a_number(db_session, platform_client):
    await _seed(db_session)

    response = await platform_client.get("/api/v1/platform/metrics/produto")
    payload = response.json()
    dau = next(k for k in payload["kpis"] if k["key"] == "uso.dau")
    assert dau["sem_dado"] is True
    assert dau["value"] is None
