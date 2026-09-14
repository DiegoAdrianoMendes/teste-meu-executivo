from __future__ import annotations

import pytest


pytestmark = pytest.mark.usefixtures("active_subscription")


async def test_operation_bi_endpoint_returns_honesty_metadata(client, db_session, auth_context):
    response = await client.get("/api/v1/legal/bi/operacao")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "honesty" in body
    assert "periodFrom" in body["honesty"]
    assert "periodTo" in body["honesty"]
    assert "sampleSize" in body["honesty"]
    assert "origin" in body["honesty"]
    assert "backlogPorResponsavel" in body


async def test_legal_bi_endpoint_returns_estoque_por_fase(client, db_session, auth_context):
    response = await client.get("/api/v1/legal/bi/juridico")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "estoquePorFase" in body
    assert "resultadoSemMatterVinculado" in body


async def test_ai_bi_endpoint_flags_missing_fields(client, db_session, auth_context):
    response = await client.get("/api/v1/legal/bi/ia")
    assert response.status_code == 200, response.text
    body = response.json()
    missing = " ".join(body["honesty"]["missingFields"])
    assert "tempo_economizado" in missing
    assert "rollback" in missing


async def test_capacity_endpoint_returns_por_responsavel(client, db_session, auth_context):
    response = await client.get("/api/v1/legal/capacity")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "porResponsavel" in body
    assert "porUnidade" in body
    assert "porEquipe" in body
