from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.commercial_account_request import CommercialAccountRequest
from app.models.klm_operations import KlmAuditEvent
from app.models.platform_work_item import PlatformWorkItem
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.commercial_catalog import (
    WorkspaceContractCreateRequest,
    WorkspaceContractLineWrite,
)
from app.schemas.commercial_self_service import CommercialAccountRequestCreate
from app.services.commercial_catalog_service import CommercialCatalogService
from app.services.commercial_self_service import (
    CommercialSelfService,
    CommercialSelfServiceConflictError,
)


async def _commercial_account(db_session, actor_id):
    catalog = CommercialCatalogService(db_session)
    offers, _ = await catalog.bootstrap_default_catalog(
        actor_user_id=actor_id,
        idempotency_key=f"commercial-self-service-catalog-{uuid4()}",
    )
    by_sku = {offer.sku: offer for offer in offers}
    workspace = Workspace(
        name="Conta de autosserviço comercial",
        slug=f"commercial-self-service-{uuid4().hex[:10]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    contract, _ = await catalog.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start",
                    policy_version_id=by_sku["crm_start"].id,
                )
            ],
            reason="Contrato inicial confirmado para o teste.",
        ),
        actor_user_id=actor_id,
        idempotency_key=f"commercial-self-service-contract-{uuid4()}",
    )
    return workspace, contract


@pytest.mark.asyncio
async def test_public_catalog_reflects_only_published_offers(
    db_session, platform_auth_context
) -> None:
    await CommercialCatalogService(db_session).bootstrap_default_catalog(
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"commercial-hidden-catalog-{uuid4()}",
    )

    service = CommercialSelfService(db_session)
    source, _ = await service.catalog.list_offers(limit=20, public_only=True)
    catalog = await service.public_catalog()

    assert [item.sku for item in catalog.items] == [item.sku for item in source]
    assert catalog.state == ("available" if source else "not_published")
    assert all(
        item.price_monthly_cents is None or item.price_monthly_cents >= 0 for item in catalog.items
    )


@pytest.mark.asyncio
async def test_request_is_idempotent_audited_and_projected_once(
    db_session, platform_auth_context
) -> None:
    workspace, contract = await _commercial_account(db_session, platform_auth_context.user_id)
    payload = CommercialAccountRequestCreate(
        request_type="cancel_contract",
        expected_contract_version=contract.version,
        reason="Encerrar o contrato após exportar os dados.",
    )
    service = CommercialSelfService(db_session)

    first, first_replay = await service.create_request(
        workspace_id=workspace.id,
        actor_user_id=platform_auth_context.user_id,
        payload=payload,
        idempotency_key="cancel-contract-idempotent",
    )
    replay, replayed = await service.create_request(
        workspace_id=workspace.id,
        actor_user_id=platform_auth_context.user_id,
        payload=payload,
        idempotency_key="cancel-contract-idempotent",
    )

    assert first_replay is False
    assert replayed is True
    assert replay.id == first.id
    assert await db_session.scalar(select(func.count()).select_from(CommercialAccountRequest)) == 1
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(PlatformWorkItem)
            .where(PlatformWorkItem.source_type == "commercial_request")
        )
        == 1
    )
    work_item = await db_session.scalar(
        select(PlatformWorkItem).where(PlatformWorkItem.source_type == "commercial_request")
    )
    assert work_item is not None
    assert f"sourceType=commercial_request&sourceId={work_item.source_id}" in work_item.href
    assert "tab=commercial&focus=workspace" in work_item.href
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(KlmAuditEvent)
            .where(KlmAuditEvent.event_type == "commercial_change_requested")
        )
        == 1
    )


@pytest.mark.asyncio
async def test_request_pagination_and_resolution_fail_closed(
    db_session, platform_auth_context
) -> None:
    workspace, contract = await _commercial_account(db_session, platform_auth_context.user_id)
    service = CommercialSelfService(db_session)
    for index in range(3):
        await service.create_request(
            workspace_id=workspace.id,
            actor_user_id=platform_auth_context.user_id,
            payload=CommercialAccountRequestCreate(
                request_type="cancel_contract",
                expected_contract_version=contract.version,
                reason=f"Solicitação auditável número {index + 1}.",
            ),
            idempotency_key=f"cancel-contract-{index}",
        )

    page, total = await service.list_requests(workspace.id, limit=1, offset=1)
    assert total == 3
    assert len(page) == 1

    with pytest.raises(CommercialSelfServiceConflictError, match="ainda não aparece"):
        await service.resolve(
            request_id=page[0].id,
            actor_user_id=platform_auth_context.user_id,
            expected_version=page[0].version,
            status="resolved",
            note="Tentar concluir sem alteração canônica.",
        )

    rejected = await service.resolve(
        request_id=page[0].id,
        actor_user_id=platform_auth_context.user_id,
        expected_version=page[0].version,
        status="rejected",
        note="Solicitação rejeitada após revisão comercial.",
    )
    assert rejected.status == "rejected"
    work_item = await db_session.scalar(
        select(PlatformWorkItem).where(
            PlatformWorkItem.source_type == "commercial_request",
            PlatformWorkItem.source_id == rejected.id,
        )
    )
    assert work_item is not None
    assert work_item.state == "cancelled"
