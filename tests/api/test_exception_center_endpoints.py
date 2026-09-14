"""Endpoint tests for /legal/exceptions (Central de Exceções — Plano
Mestre §6/§3). Mirrors the cross-tenant isolation and capability-gating
discipline already covered for /approvals in tests/api/test_security.py.
"""
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.legal_exception import ExceptionCenterStatus, ExceptionKind
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.exception_center_service import ExceptionCenterService


@pytest.mark.asyncio
async def test_unauthenticated_requests_are_rejected(unauthenticated_client):
    list_response = await unauthenticated_client.get("/api/v1/legal/exceptions")
    resolve_response = await unauthenticated_client.post(
        f"/api/v1/legal/exceptions/{uuid4()}/resolver", json={}
    )

    assert list_response.status_code == 401
    assert resolve_response.status_code == 401


@pytest.mark.asyncio
async def test_list_exceptions_filters_by_kind_and_status_via_endpoint(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    active_subscription,
):
    service = ExceptionCenterService(db_session, auth_context.workspace_id)
    open_item = await service.open_exception(
        exception_kind=ExceptionKind.homonimo,
        origin="api.test",
        dedupe_key="homonimo-1",
        title="Possível homônimo",
    )
    other_item = await service.open_exception(
        exception_kind=ExceptionKind.prazo_ambiguo,
        origin="api.test",
        dedupe_key="prazo-1",
        title="Prazo ambíguo",
    )
    await db_session.commit()

    all_response = await client.get("/api/v1/legal/exceptions")
    assert all_response.status_code == 200
    all_ids = {item["id"] for item in all_response.json()}
    assert {str(open_item.id), str(other_item.id)} <= all_ids

    kind_response = await client.get("/api/v1/legal/exceptions?kind=homonimo")
    assert kind_response.status_code == 200
    assert {item["id"] for item in kind_response.json()} == {str(open_item.id)}

    status_response = await client.get("/api/v1/legal/exceptions?status=aberta")
    assert status_response.status_code == 200
    status_ids = {item["id"] for item in status_response.json()}
    assert {str(open_item.id), str(other_item.id)} <= status_ids


@pytest.mark.asyncio
async def test_resolve_endpoint_always_attributes_to_the_authenticated_user(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    active_subscription,
):
    service = ExceptionCenterService(db_session, auth_context.workspace_id)
    item = await service.open_exception(
        exception_kind=ExceptionKind.custas_nao_comprovadas,
        origin="api.test",
        dedupe_key="custas-endpoint-1",
        title="Custas não comprovadas",
    )
    await db_session.commit()

    # Mesmo que o payload tente injetar um `resolvedBy` diferente, o
    # endpoint SEMPRE atribui o autenticado — o schema nem aceita o campo.
    response = await client.post(
        f"/api/v1/legal/exceptions/{item.id}/resolver",
        json={
            "resolutionReason": "Comprovante localizado.",
            "resolvedBy": str(uuid4()),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolvida"
    assert body["resolvedBy"] == str(auth_context.user_id)
    assert body["resolutionReason"] == "Comprovante localizado."


@pytest.mark.asyncio
async def test_resolve_endpoint_rejects_double_resolution_with_409(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    active_subscription,
):
    service = ExceptionCenterService(db_session, auth_context.workspace_id)
    item = await service.open_exception(
        exception_kind=ExceptionKind.documento_indispensavel_ausente,
        origin="api.test",
        dedupe_key="doc-endpoint-1",
        title="Procuração ausente",
    )
    await db_session.commit()

    first = await client.post(f"/api/v1/legal/exceptions/{item.id}/resolver", json={})
    second = await client.post(f"/api/v1/legal/exceptions/{item.id}/resolver", json={})

    assert first.status_code == 200
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_escalate_endpoint_sets_escalated_state(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    active_subscription,
):
    service = ExceptionCenterService(db_session, auth_context.workspace_id)
    item = await service.open_exception(
        exception_kind=ExceptionKind.alteracao_tese,
        origin="api.test",
        dedupe_key="tese-endpoint-1",
        title="Alteração de tese",
    )
    await db_session.commit()

    response = await client.post(
        f"/api/v1/legal/exceptions/{item.id}/escalar",
        json={"resolutionReason": "Precisa de decisão do sócio."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "escalada"
    assert body["resolvedBy"] is None


@pytest.mark.asyncio
async def test_cross_tenant_exception_access_is_blocked(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    active_subscription,
):
    other_workspace = Workspace(
        name="Workspace Externo Exceções",
        slug=f"workspace-externo-exc-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()

    foreign_service = ExceptionCenterService(db_session, other_workspace.id)
    foreign_item = await foreign_service.open_exception(
        exception_kind=ExceptionKind.homonimo,
        origin="api.test",
        dedupe_key="foreign-homonimo",
        title="Exceção de outro workspace",
    )
    await db_session.commit()

    list_response = await client.get("/api/v1/legal/exceptions")
    resolve_response = await client.post(
        f"/api/v1/legal/exceptions/{foreign_item.id}/resolver", json={}
    )

    assert list_response.status_code == 200
    assert all(item["id"] != str(foreign_item.id) for item in list_response.json())
    assert resolve_response.status_code == 404
