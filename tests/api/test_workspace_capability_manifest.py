from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.entitlement import EntitlementKey
from app.models.whatsapp import WhatsAppConnection, WhatsAppContact
from app.schemas.commercial_catalog import (
    WorkspaceContractCreateRequest,
    WorkspaceContractLineWrite,
)
from app.services.commercial_catalog_service import CommercialCatalogService


@pytest.mark.asyncio
async def test_workspace_manifest_fails_closed_without_commercial_contract(client) -> None:
    response = await client.get("/api/v1/workspaces/capability-manifest")

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {
        "code": "COMMERCIAL-MANIFEST-NOT-CONFIGURED",
        "message": "A política comercial desta conta ainda não foi configurada.",
    }


@pytest.mark.asyncio
async def test_workspace_manifest_uses_authenticated_workspace_and_real_policy(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    service = CommercialCatalogService(db_session)
    catalog, replay = await service.bootstrap_default_catalog(
        actor_user_id=auth_context.user_id,
        idempotency_key="tenant-manifest-bootstrap",
    )
    assert replay is False
    start = next(item for item in catalog if item.sku == "crm_start")
    await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=auth_context.workspace_id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start",
                    policy_version_id=start.id,
                )
            ],
            reason="Contrato de teste do manifesto tenant.",
        ),
        actor_user_id=auth_context.user_id,
        idempotency_key="tenant-manifest-contract",
    )
    await db_session.commit()

    response = await client.get("/api/v1/workspaces/capability-manifest")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["workspaceId"] == str(auth_context.workspace_id)
    assert payload["capabilities"]["contacts"]["contracted"] is True
    assert payload["capabilities"]["contacts"]["enabled"] is True
    assert payload["capabilities"]["contacts"]["permitted"] is True
    assert payload["capabilities"]["contacts"]["limit"] == 1_000
    assert payload["capabilities"]["contacts"]["period"] == "stock"
    assert payload["capabilities"]["active_tasks"]["limit"] == 500
    assert payload["capabilities"]["task_participants"]["limit"] == 3
    assert payload["capabilities"]["legal_suite"]["contracted"] is False
    assert payload["capabilities"]["legal_suite"]["enabled"] is False


@pytest.mark.asyncio
async def test_workspace_manifest_measures_contact_stock_and_reports_exhaustion(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    service = CommercialCatalogService(db_session)
    catalog, _replay = await service.bootstrap_default_catalog(
        actor_user_id=auth_context.user_id,
        idempotency_key="tenant-contact-stock-bootstrap",
    )
    start = next(item for item in catalog if item.sku == "crm_start")
    contact_policy = start.snapshot.entitlements[EntitlementKey.contacts].model_copy(
        update={"limit_quantity": 1}
    )
    await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=auth_context.workspace_id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start",
                    policy_version_id=start.id,
                    entitlement_overrides={EntitlementKey.contacts: contact_policy},
                )
            ],
            reason="Contrato com uma vaga de contato para validar o estoque.",
        ),
        actor_user_id=auth_context.user_id,
        idempotency_key="tenant-contact-stock-contract",
    )
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        display_name="Canal de teste do estoque",
    )
    db_session.add(connection)
    await db_session.flush()
    db_session.add(
        WhatsAppContact(
            workspace_id=auth_context.workspace_id,
            whatsapp_connection_id=connection.id,
            phone="+5511999990001",
            name="Contato da quota",
        )
    )
    await db_session.commit()

    response = await client.get("/api/v1/workspaces/capability-manifest")

    assert response.status_code == 200, response.text
    contacts = response.json()["capabilities"]["contacts"]
    assert contacts["limit"] == 1
    assert contacts["used"] == 1
    assert contacts["remaining"] == 0
    assert contacts["permitted"] is False
    assert contacts["denialReason"] == "quota_exhausted"
