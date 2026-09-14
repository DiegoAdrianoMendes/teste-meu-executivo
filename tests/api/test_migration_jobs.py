from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole

pytestmark = pytest.mark.usefixtures("active_subscription")

_HEADER = "dominio,nome,documento,referencia_externa\n"
_CSV_CONTENT = (_HEADER + "clientes,Beatriz Ramos,44455566677,cli-beatriz\n").encode("utf-8")


async def _upload_job(client) -> dict:
    response = await client.post(
        "/api/v1/migration/jobs",
        files={"file": ("migracao.csv", _CSV_CONTENT, "text/csv")},
        data={"source_system": "universal_csv"},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_migration_job_full_flow_via_endpoints(client, db_session, auth_context):
    job = await _upload_job(client)
    job_id = job["id"]
    assert job["status"] == "validado"
    assert job["sourceManifest"]["formatDetected"] == "csv"

    status_response = await client.get(f"/api/v1/migration/jobs/{job_id}")
    assert status_response.status_code == 200
    assert status_response.json()["id"] == job_id

    preview_response = await client.post(f"/api/v1/migration/jobs/{job_id}/previa")
    assert preview_response.status_code == 200, preview_response.text
    assert preview_response.json()["status"] == "previa"
    assert preview_response.json()["previewSummary"]["clientes"]["staged"] == 1

    import_response = await client.post(
        f"/api/v1/migration/jobs/{job_id}/importar", json={"batchSize": 10}
    )
    assert import_response.status_code == 200, import_response.text
    assert import_response.json()["remaining"] == 0
    assert import_response.json()["status"] == "reconciliando"

    reconcile_response = await client.post(f"/api/v1/migration/jobs/{job_id}/reconciliacao")
    assert reconcile_response.status_code == 200, reconcile_response.text
    assert reconcile_response.json()["status"] == "concluido"
    assert reconcile_response.json()["semaphore"] == "verde"

    semaphore_response = await client.get(f"/api/v1/migration/jobs/{job_id}/semaforo")
    assert semaphore_response.status_code == 200
    assert semaphore_response.json()["semaphore"] == "verde"

    decision_response = await client.post(
        f"/api/v1/migration/jobs/{job_id}/decisao-corte",
        json={"decision": "prosseguir", "notes": "Aprovado pelo escritório."},
    )
    assert decision_response.status_code == 200, decision_response.text
    assert decision_response.json()["cutoverDecision"]["decision"] == "prosseguir"
    assert decision_response.json()["cutoverDecision"]["decidedBy"] == str(auth_context.user_id)

    staging_response = await client.get(f"/api/v1/migration/jobs/{job_id}/staging")
    assert staging_response.status_code == 200
    assert len(staging_response.json()) == 1
    assert staging_response.json()[0]["recordStatus"] == "importado"

    divergences_response = await client.get(f"/api/v1/migration/jobs/{job_id}/divergencias")
    assert divergences_response.status_code == 200
    assert divergences_response.json() == []

    rollback_response = await client.post(f"/api/v1/migration/jobs/{job_id}/rollback")
    assert rollback_response.status_code == 200, rollback_response.text
    assert rollback_response.json()["status"] == "revertido"


@pytest.mark.asyncio
async def test_migration_upload_requires_admin_manage(
    client, db_session: AsyncSession, auth_context
):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    assert membership is not None
    membership.role = WorkspaceRole.operator
    auth_context.role = WorkspaceRole.operator
    await db_session.flush()

    response = await client.post(
        "/api/v1/migration/jobs",
        files={"file": ("migracao.csv", _CSV_CONTENT, "text/csv")},
        data={"source_system": "universal_csv"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_migration_status_requires_at_least_admin_view(
    client, db_session: AsyncSession, auth_context
):
    job = await _upload_job(client)

    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    assert membership is not None
    membership.role = WorkspaceRole.operator
    auth_context.role = WorkspaceRole.operator
    await db_session.flush()

    response = await client.get(f"/api/v1/migration/jobs/{job['id']}")
    assert response.status_code == 403
