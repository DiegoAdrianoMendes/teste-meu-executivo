from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.api.v1.endpoints.stripe_webhook import (
    _handle_canonical_checkout_completed,
    _handle_canonical_subscription_event,
)
from app.models.commercial_catalog import (
    CommercialCheckoutIntent,
    CommercialCheckoutIntentStatus,
    CommercialContractProviderBinding,
    CommercialContractStatus,
    CommercialProvider,
)
from app.models.platform_work_item import PlatformWorkItem
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.commercial_catalog import (
    CommercialOfferPublishRequest,
    WorkspaceContractCreateRequest,
    WorkspaceContractLineWrite,
    WorkspaceContractRevisionRequest,
)
from app.services.commercial_catalog_service import CommercialCatalogService
from app.services.commercial_legacy_bridge_service import (
    CanonicalContractCommand,
    ProviderManagedContractError,
)
from app.services.commercial_provider_reconciliation import activate_checkout
from app.services.commercial_stripe_bridge import (
    CommercialStripeBridge,
    CommercialStripeBridgeConflict,
)


async def _account(db_session, actor_id, monkeypatch):
    catalog = CommercialCatalogService(db_session)
    offers, _ = await catalog.bootstrap_default_catalog(
        actor_user_id=actor_id,
        idempotency_key=f"stripe-bridge-catalog-{uuid4()}",
    )
    by_sku = {offer.sku: offer for offer in offers}

    # O gate de unit economics possui testes próprios. Aqui isolamos a ponte
    # Stripe e ainda publicamos uma nova revisão pelo serviço canônico, sem
    # adulterar a revisão append-only criada pelo bootstrap.
    async def _eligible(_self, _snapshot):
        return SimpleNamespace(eligible=True, blockers=[])

    monkeypatch.setattr(
        "app.services.commercial_catalog_service.CommercialUnitEconomicsService.evaluate",
        _eligible,
    )
    advanced = by_sku["crm_advanced"]
    published_advanced, _ = await catalog.publish_offer(
        payload=CommercialOfferPublishRequest(
            sku=advanced.sku,
            family=advanced.family,
            kind=advanced.kind,
            legacy_tier=advanced.legacy_tier,
            snapshot=advanced.snapshot.model_copy(update={"is_public": True}),
            reason="Publicar Advanced para o checkout canônico do teste.",
        ),
        actor_user_id=actor_id,
        idempotency_key=f"stripe-bridge-publish-{uuid4()}",
    )
    by_sku["crm_advanced"] = published_advanced
    workspace = Workspace(
        name="Conta Stripe canônica",
        slug=f"canonical-stripe-{uuid4().hex[:10]}",
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
            reason="Contrato inicial do teste Stripe canônico.",
        ),
        actor_user_id=actor_id,
        idempotency_key=f"stripe-bridge-contract-{uuid4()}",
    )
    return workspace, contract


@pytest.mark.asyncio
async def test_asaas_binding_uses_canonical_offer_without_external_price_id(
    db_session, platform_auth_context, monkeypatch
) -> None:
    await _account(db_session, platform_auth_context.user_id, monkeypatch)
    bridge = CommercialStripeBridge(db_session, CommercialProvider.asaas)

    binding = await bridge.configure_price_binding(
        sku="crm_advanced",
        provider_price_id=None,
        actor_user_id=platform_auth_context.user_id,
        reason="Habilitar checkout Asaas com preço do catálogo canônico.",
    )

    assert binding.provider == CommercialProvider.asaas
    assert binding.provider_price_id == f"asaas:{binding.offer_version_id}"


@pytest.mark.asyncio
async def test_asaas_checkout_activation_revises_same_canonical_contract(
    db_session, platform_auth_context, monkeypatch
) -> None:
    workspace, contract = await _account(
        db_session, platform_auth_context.user_id, monkeypatch
    )
    bridge = CommercialStripeBridge(db_session, CommercialProvider.asaas)
    await bridge.configure_price_binding(
        sku="crm_advanced",
        provider_price_id=None,
        actor_user_id=platform_auth_context.user_id,
        reason="Habilitar assinatura recorrente Asaas no teste canônico.",
    )
    intent, _, replayed = await bridge.prepare_checkout(
        workspace_id=workspace.id,
        actor_user_id=platform_auth_context.user_id,
        target_sku="crm_advanced",
        expected_contract_version=contract.version,
        reason="Migrar contrato do teste para Advanced pelo Asaas.",
        idempotency_key=f"asaas-checkout-{uuid4()}",
    )
    assert replayed is False
    intent.provider_session_id = "checkout_asaas_123"
    intent.status = CommercialCheckoutIntentStatus.session_created
    await db_session.flush()

    binding = await activate_checkout(
        db_session,
        provider=CommercialProvider.asaas,
        event_id="evt_asaas_paid_123",
        event_created=1_786_590_000,
        external_reference=f"{intent.id}|{workspace.id}",
        provider_session_id="checkout_asaas_123",
        provider_subscription_id="sub_asaas_123",
        provider_customer_id="cus_asaas_123",
        provider_status="active",
    )

    refreshed = await CommercialCatalogService(db_session).get_workspace_contract(workspace.id)
    assert binding.provider == CommercialProvider.asaas
    assert binding.provider_subscription_id == "sub_asaas_123"
    assert refreshed.status == CommercialContractStatus.active
    assert [line.sku for line in refreshed.lines if line.status.value == "active"] == [
        "crm_advanced"
    ]


@pytest.mark.asyncio
async def test_checkout_intent_is_idempotent_and_signed_event_updates_canonical_contract(
    db_session, platform_auth_context, monkeypatch
) -> None:
    workspace, contract = await _account(db_session, platform_auth_context.user_id, monkeypatch)
    bridge = CommercialStripeBridge(db_session)
    await bridge.configure_price_binding(
        sku="crm_start",
        provider_price_id="price_start_test",
        actor_user_id=platform_auth_context.user_id,
        reason="Vincular preço Start para reconciliação do portal.",
    )
    await bridge.configure_price_binding(
        sku="crm_advanced",
        provider_price_id="price_advanced_test",
        actor_user_id=platform_auth_context.user_id,
        reason="Vincular preço controlado para o teste.",
    )
    intent, _, replayed = await bridge.prepare_checkout(
        workspace_id=workspace.id,
        actor_user_id=platform_auth_context.user_id,
        target_sku="crm_advanced",
        expected_contract_version=contract.version,
        reason="Migrar para o plano Advanced após confirmação.",
        idempotency_key="canonical-checkout-test",
    )
    replay, _, was_replayed = await bridge.prepare_checkout(
        workspace_id=workspace.id,
        actor_user_id=platform_auth_context.user_id,
        target_sku="crm_advanced",
        expected_contract_version=contract.version,
        reason="Migrar para o plano Advanced após confirmação.",
        idempotency_key="canonical-checkout-test",
    )
    assert replayed is False
    assert was_replayed is True
    assert replay.id == intent.id
    assert await db_session.scalar(select(func.count()).select_from(CommercialCheckoutIntent)) == 1

    intent.provider_session_id = "cs_canonical_test"
    intent.status = CommercialCheckoutIntentStatus.session_created
    await db_session.flush()
    binding = await _handle_canonical_checkout_completed(
        {
            "object": {
                "id": "cs_canonical_test",
                "customer": "cus_canonical_test",
                "subscription": "sub_canonical_test",
                "metadata": {
                    "commercial_checkout_intent_id": str(intent.id),
                    "commercial_contract_id": str(contract.id),
                    "workspace_id": str(workspace.id),
                },
            }
        },
        db_session,
        event_created=1_786_500_000,
        event_id="evt_canonical_checkout",
    )

    assert binding is not None
    assert binding.version == 1
    assert intent.status is CommercialCheckoutIntentStatus.completed
    updated = await bridge.catalog.get_workspace_contract(workspace.id)
    assert updated.version == contract.version + 1
    assert {line.sku for line in updated.lines if line.status.value == "active"} == {"crm_advanced"}

    await _handle_canonical_subscription_event(
        {"object": {"id": "sub_canonical_test", "status": "past_due"}},
        db_session,
        event_type="customer.subscription.updated",
        event_created=1_786_500_001,
        event_id="evt_canonical_subscription_updated",
    )
    reconciled = await bridge.catalog.get_workspace_contract(workspace.id)
    assert reconciled.status.value == "past_due"
    assert {line.sku for line in reconciled.lines if line.status.value == "active"} == {
        "crm_advanced"
    }

    await _handle_canonical_subscription_event(
        {
            "object": {
                "id": "sub_canonical_test",
                "status": "active",
                "items": {
                    "data": [
                        {
                            "price": {"id": "price_start_test"},
                            "quantity": 1,
                        }
                    ]
                },
            }
        },
        db_session,
        event_type="customer.subscription.updated",
        event_created=1_786_500_002,
        event_id="evt_canonical_subscription_plan_changed",
    )
    plan_reconciled = await bridge.catalog.get_workspace_contract(workspace.id)
    assert plan_reconciled.status.value == "active"
    assert {
        line.sku for line in plan_reconciled.lines if line.status.value == "active"
    } == {"crm_start"}

    await _handle_canonical_subscription_event(
        {
            "object": {
                "id": "sub_canonical_test",
                "status": "active",
                "cancel_at_period_end": True,
                "items": {
                    "data": [{"price": {"id": "price_start_test"}, "quantity": 1}]
                },
            }
        },
        db_session,
        event_type="customer.subscription.updated",
        event_created=1_786_500_003,
        event_id="evt_canonical_subscription_cancel_scheduled",
    )
    assert binding.provider_status == "cancel_at_period_end"


@pytest.mark.asyncio
async def test_existing_provider_subscription_blocks_second_checkout(
    db_session, platform_auth_context, monkeypatch
) -> None:
    workspace, contract = await _account(db_session, platform_auth_context.user_id, monkeypatch)
    bridge = CommercialStripeBridge(db_session)
    await bridge.configure_price_binding(
        sku="crm_advanced",
        provider_price_id="price_advanced_existing",
        actor_user_id=platform_auth_context.user_id,
        reason="Vincular preço do cenário com assinatura.",
    )
    db_session.add(
        CommercialContractProviderBinding(
            workspace_id=workspace.id,
            contract_id=contract.id,
            provider=CommercialProvider.stripe,
            provider_customer_id="cus_existing",
            provider_subscription_id="sub_existing",
            provider_status="active",
            version=1,
        )
    )
    await db_session.flush()

    with pytest.raises(CommercialStripeBridgeConflict, match="administração de cobrança"):
        await bridge.prepare_checkout(
            workspace_id=workspace.id,
            actor_user_id=platform_auth_context.user_id,
            target_sku="crm_advanced",
            expected_contract_version=contract.version,
            reason="Tentar evitar assinatura Stripe duplicada.",
            idempotency_key="blocked-duplicate-checkout",
        )


@pytest.mark.parametrize(
    "provider",
    [
        CommercialProvider.stripe,
        CommercialProvider.asaas,
        CommercialProvider.mercado_pago,
    ],
)
@pytest.mark.asyncio
async def test_linked_provider_subscription_blocks_local_core_and_status_changes(
    db_session, platform_auth_context, monkeypatch, provider
) -> None:
    workspace, contract = await _account(
        db_session, platform_auth_context.user_id, monkeypatch
    )
    db_session.add(
        CommercialContractProviderBinding(
            workspace_id=workspace.id,
            contract_id=contract.id,
            provider=provider,
            provider_customer_id="cus_provider_managed",
            provider_subscription_id="sub_provider_managed",
            provider_status="active",
            version=1,
        )
    )
    await db_session.flush()
    command = CanonicalContractCommand(db_session)

    with pytest.raises(ProviderManagedContractError, match="provedor de cobrança"):
        await command.ensure_core_plan(
            workspace_id=workspace.id,
            plan="crm_advanced",
            actor_user_id=platform_auth_context.user_id,
            idempotency_key="provider-managed-core-change",
            reason="Tentativa local deve falhar fechada.",
        )

    with pytest.raises(ProviderManagedContractError, match="provedor de cobrança"):
        await command.revise_contract(
            contract_id=contract.id,
            expected_version=contract.version,
            payload=WorkspaceContractRevisionRequest(
                lines=[WorkspaceContractLineWrite(sku="crm_advanced")],
                reason="Editor canônico também deve respeitar o provedor.",
            ),
            actor_user_id=platform_auth_context.user_id,
            idempotency_key="provider-managed-contract-revision",
        )

    with pytest.raises(ProviderManagedContractError, match="provedor de cobrança"):
        await command.change_status(
            workspace_id=workspace.id,
            target_status=CommercialContractStatus.cancelled,
            actor_user_id=platform_auth_context.user_id,
            idempotency_key="provider-managed-status-change",
            reason="Cancelamento local deve falhar fechada.",
        )

    unchanged = await command.catalog.get_workspace_contract(workspace.id)
    assert unchanged.version == contract.version
    assert unchanged.status is CommercialContractStatus.active
    assert {
        line.sku for line in unchanged.lines if line.status.value == "active"
    } == {"crm_start"}


@pytest.mark.asyncio
async def test_provider_binding_blocks_replay_of_older_local_status_receipt(
    db_session, platform_auth_context, monkeypatch
) -> None:
    workspace, contract = await _account(
        db_session, platform_auth_context.user_id, monkeypatch
    )
    command = CanonicalContractCommand(db_session)
    await command.change_status(
        workspace_id=workspace.id,
        target_status=CommercialContractStatus.paused,
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="status-before-provider-binding",
        reason="Criar recibo anterior ao vínculo do provedor.",
    )
    db_session.add(
        CommercialContractProviderBinding(
            workspace_id=workspace.id,
            contract_id=contract.id,
            provider=CommercialProvider.stripe,
            provider_customer_id="cus_after_receipt",
            provider_subscription_id="sub_after_receipt",
            provider_status="paused",
            version=1,
        )
    )
    await db_session.flush()

    with pytest.raises(ProviderManagedContractError, match="provedor de cobrança"):
        await command.change_status(
            workspace_id=workspace.id,
            target_status=CommercialContractStatus.paused,
            actor_user_id=platform_auth_context.user_id,
            idempotency_key="status-before-provider-binding",
            reason="Criar recibo anterior ao vínculo do provedor.",
        )


@pytest.mark.asyncio
async def test_unknown_portal_price_keeps_contract_and_opens_reconciliation(
    db_session, platform_auth_context, monkeypatch
) -> None:
    workspace, contract = await _account(
        db_session, platform_auth_context.user_id, monkeypatch
    )
    binding = CommercialContractProviderBinding(
        workspace_id=workspace.id,
        contract_id=contract.id,
        provider=CommercialProvider.stripe,
        provider_customer_id="cus_unknown_price",
        provider_subscription_id="sub_unknown_price",
        provider_status="active",
        version=1,
    )
    db_session.add(binding)
    await db_session.flush()

    await _handle_canonical_subscription_event(
        {
            "object": {
                "id": "sub_unknown_price",
                "status": "active",
                "items": {
                    "data": [{"price": {"id": "price_not_mapped"}, "quantity": 1}]
                },
            }
        },
        db_session,
        event_type="customer.subscription.updated",
        event_created=1_786_500_010,
        event_id="evt_unknown_portal_price",
    )

    unchanged = await CommercialCatalogService(db_session).get_workspace_contract(
        workspace.id
    )
    assert unchanged.version == contract.version
    assert {
        line.sku for line in unchanged.lines if line.status.value == "active"
    } == {"crm_start"}
    work_item = await db_session.scalar(
        select(PlatformWorkItem).where(
            PlatformWorkItem.dedupe_key
            == f"commercial_subscription_reconcile:{binding.id}"
        )
    )
    assert work_item is not None
    assert work_item.priority == "P1"
    assert str(workspace.id) in work_item.href
    assert "price_not_mapped" not in work_item.safe_summary

    bridge = CommercialStripeBridge(db_session)
    await bridge.configure_price_binding(
        sku="crm_advanced",
        provider_price_id="price_now_mapped",
        actor_user_id=platform_auth_context.user_id,
        reason="Vincular preço e comprovar resolução automática da pendência.",
    )
    await _handle_canonical_subscription_event(
        {
            "object": {
                "id": "sub_unknown_price",
                "status": "active",
                "items": {
                    "data": [{"price": {"id": "price_now_mapped"}, "quantity": 1}]
                },
            }
        },
        db_session,
        event_type="customer.subscription.updated",
        event_created=1_786_500_011,
        event_id="evt_portal_price_reconciled",
    )
    assert work_item.state == "done"
    assert work_item.closed_at is not None
