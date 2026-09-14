"""
Testes: Health Check e endpoints básicos.
"""
import pytest
from httpx import AsyncClient

from app.core.config import settings


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    """O endpoint /health deve retornar status ok."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "environment" in data


@pytest.mark.asyncio
async def test_health_reports_deployment_environment_when_security_posture_differs(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["environment"] == "staging"
    assert settings.is_production is True


@pytest.mark.asyncio
async def test_list_matters_empty(client: AsyncClient, active_subscription):
    """GET /api/v1/matters deve retornar lista vazia quando não há casos."""
    response = await client.get("/api/v1/matters")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_matter_not_found(client: AsyncClient, active_subscription):
    """GET /api/v1/matters/{id} deve retornar 404 para ID inexistente."""
    import uuid
    response = await client.get(f"/api/v1/matters/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_approvals_pending_empty(client: AsyncClient, active_subscription):
    """GET /api/v1/approvals/pending deve retornar lista vazia inicialmente."""
    response = await client.get("/api/v1/approvals/pending")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_reports_metrics(client: AsyncClient, active_subscription):
    """GET /api/v1/reports/metrics deve retornar estrutura de KPIs."""
    response = await client.get("/api/v1/reports/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "kpis" in data
    assert "casesByPhase" in data  # camelCase — alinhado com o frontend
    assert isinstance(data["kpis"], list)


@pytest.mark.asyncio
async def test_readiness_reports_database_ok(client: AsyncClient):
    """S11: /health/ready consulta o banco de verdade (SELECT 1)."""
    response = await client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["checks"]["database"] == "ok"


@pytest.mark.asyncio
async def test_readiness_returns_503_when_database_is_down(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    import app.main as main_module

    async def banco_fora(db):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(main_module, "_probe_database", banco_fora)
    response = await client.get("/health/ready")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "degraded"
    assert data["checks"]["database"] == "down"

    # O liveness continua verde: banco fora não recicla o container.
    assert (await client.get("/health")).status_code == 200
