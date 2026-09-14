from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.commercial_catalog import (
    CommercialAdjustment,
    CommercialAdjustmentType,
    CommercialBillingInterval,
    CommercialContractStatus,
    CommercialRecordStatus,
    EntitlementBonus,
    EntitlementBonusMode,
    WorkspaceCommercialContract,
)
from app.models.entitlement import EntitlementKey
from app.models.subscription_commercial import (
    SubscriptionEvent,
    WorkspaceSubscription,
    WorkspaceSubscriptionStatus,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace
from app.schemas.commercial_catalog import (
    CommercialAdjustmentCreateRequest,
    CommercialAdjustmentUpdateRequest,
    CommercialRecordRevokeRequest,
    EntitlementBonusCreateRequest,
    WorkspaceContractCreateRequest,
    WorkspaceContractLineWrite,
    WorkspaceContractRestoreRequest,
)
from app.services import commercial_legacy_bridge_service as bridge_service_module
from app.services.commercial_catalog_service import (
    CommercialCatalogService,
    CommercialIdempotencyConflictError,
)
from app.services.commercial_legacy_bridge_service import (
    CanonicalContractCommand,
    CanonicalOfferQuery,
    CanonicalPlanDirectionError,
    CanonicalRevenueReadModel,
    CommercialBridgeError,
    LegacyCommercialProjection,
    canonical_sku,
)
from app.services.klm_local_seed_service import KlmLocalSeedService


async def test_border_aliases_resolve_to_stable_skus_and_catalog_prices(
    db_session,
) -> None:
    assert canonical_sku("basic") == "crm_start"
    assert canonical_sku("Advanced") == "crm_advanced"
    assert canonical_sku("performance") == "crm_evolution"

    offers = {
        item.sku: item
        for item in await CanonicalOfferQuery(db_session).list_active_core()
    }

    assert offers["crm_start"].snapshot.price_monthly_cents == 26_900
    assert offers["crm_advanced"].snapshot.price_monthly_cents == 67_900
    assert offers["crm_evolution"].snapshot.price_monthly_cents == 124_900
    assert all(item.snapshot.price_annual_cents is None for item in offers.values())


async def test_trial_writes_canonical_first_and_projects_legacy_read_models(
    db_session,
    auth_context,
) -> None:
    command = CanonicalContractCommand(db_session)
    contract = await command.provision_trial(
        workspace_id=auth_context.workspace_id,
        plan="basic",
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-trial-1",
        trial_days=14,
    )

    assert contract.status.value == "trialing"
    assert [line.sku for line in contract.lines] == ["crm_start"]
    assert await db_session.scalar(select(func.count(WorkspaceCommercialContract.id))) == 1

    subscription = await db_session.scalar(
        select(Subscription).where(Subscription.workspace_id == auth_context.workspace_id)
    )
    assert subscription is not None
    assert subscription.status is SubscriptionStatus.trialing
    assert subscription.amount_cents == 0
    assert subscription.trial_end is not None

    local_projection = await db_session.scalar(
        select(WorkspaceSubscription).where(
            WorkspaceSubscription.workspace_id == auth_context.workspace_id
        )
    )
    assert local_projection is not None
    assert local_projection.origem == "canonical_projection"
    assert local_projection.metadata_json["canonicalContractId"] == str(contract.id)
    assert local_projection.metadata_json["projectionOnly"] is True

    # Compatibility plans are populated from immutable offer revisions, not
    # from the former hard-coded table.
    projected_plan = await db_session.get(Plan, subscription.plan_id)
    assert projected_plan is not None
    assert projected_plan.price_monthly_cents == 26_900
    assert projected_plan.price_annually_cents == 0
    assert projected_plan.features["canonicalSku"] == "crm_start"


async def test_annual_legacy_projection_preserves_cycle_period_and_monthly_equivalent(
    db_session,
    auth_context,
) -> None:
    service = CommercialCatalogService(db_session)
    offers, _ = await service.bootstrap_default_catalog(
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-annual-catalog",
    )
    advanced = next(item for item in offers if item.sku == "crm_advanced")
    contract, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=auth_context.workspace_id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_advanced",
                    policy_version_id=advanced.id,
                    billing_interval=CommercialBillingInterval.annual,
                    contracted_price_cents=650_000,
                )
            ],
            reason="Contrato anual legado conferido.",
        ),
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-annual-contract",
        allow_legacy_import=True,
    )
    subscription = await LegacyCommercialProjection(db_session).project_contract(
        contract,
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-annual-projection",
    )
    assert subscription is not None
    assert subscription.interval is BillingInterval.annually
    assert subscription.amount_cents == 650_000
    assert subscription.current_period_start is not None
    assert subscription.current_period_end is not None
    assert (
        subscription.current_period_end - subscription.current_period_start
    ).days == 365

    local = await db_session.scalar(
        select(WorkspaceSubscription).where(
            WorkspaceSubscription.workspace_id == auth_context.workspace_id
        )
    )
    assert local is not None
    assert local.ciclo == "anual"
    assert local.valor_mensal_cents == 54_167
    assert local.metadata_json["canonicalCoreBillingInterval"] == "annual"
    assert local.metadata_json["projectionBillingInterval"] == "annually"
    assert local.metadata_json["canonicalRecurringPriceCents"] == 54_167
    assert local.current_period_start is not None
    assert local.current_period_end is not None
    assert (local.current_period_end - local.current_period_start).days == 365

    await service.add_adjustment(
        contract_id=contract.id,
        payload=CommercialAdjustmentCreateRequest(
            type=CommercialAdjustmentType.percentage_discount,
            value=1_000,
            starts_at=datetime.now(UTC) - timedelta(minutes=1),
            stack_group="annual-exact-cycle",
            reason="Aplicar dez por cento ao ciclo anual exato.",
        ),
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-annual-adjustment",
    )
    adjusted = await service.get_contract(contract.id)
    assert adjusted.price.recurring_price_cents == 48_751
    await LegacyCommercialProjection(db_session).project_contract(
        adjusted,
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-annual-adjusted-projection",
    )
    await db_session.refresh(subscription)
    assert subscription.amount_cents == 585_000
    assert subscription.amount_cents != adjusted.price.recurring_price_cents * 12


async def test_change_plan_revises_same_contract_and_read_model_deduplicates_workspace(
    db_session,
    auth_context,
) -> None:
    command = CanonicalContractCommand(db_session)
    before = await command.provision_trial(
        workspace_id=auth_context.workspace_id,
        plan="crm_start",
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-trial-2",
    )
    after = await command.change_core_plan(
        workspace_id=auth_context.workspace_id,
        plan="crm_advanced",
        direction="upgrade",
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-upgrade-1",
        reason="Upgrade solicitado durante o trial.",
    )

    assert after.id == before.id
    assert after.version == before.version + 1
    assert [line.sku for line in after.lines if line.status.value == "active"] == [
        "crm_advanced"
    ]

    records = await CanonicalRevenueReadModel(db_session).list_records()
    matching = [item for item in records if item.workspace_id == auth_context.workspace_id]
    assert len(matching) == 1
    assert matching[0].source == "canonical_contract"
    assert matching[0].sku == "crm_advanced"
    assert matching[0].status == "trial"
    assert matching[0].mrr_cents == 0

    activated = await command.change_status(
        workspace_id=auth_context.workspace_id,
        target_status=CommercialContractStatus.active,
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-activate-1",
        reason="Ativação interna comprovada no teste.",
    )
    assert activated.version == after.version + 1
    assert activated.status is CommercialContractStatus.active

    local_projection = await db_session.scalar(
        select(WorkspaceSubscription).where(
            WorkspaceSubscription.workspace_id == auth_context.workspace_id
        )
    )
    assert local_projection is not None
    assert local_projection.status is WorkspaceSubscriptionStatus.ativa

    active_records = await CanonicalRevenueReadModel(db_session).list_records()
    active_matching = [
        item for item in active_records if item.workspace_id == auth_context.workspace_id
    ]
    assert len(active_matching) == 1
    assert active_matching[0].source == "canonical_contract"
    assert active_matching[0].sku == "crm_advanced"
    assert active_matching[0].status == "ativa"
    assert active_matching[0].mrr_cents == 67_900

    try:
        await command.change_core_plan(
            workspace_id=auth_context.workspace_id,
            plan="crm_start",
            direction="upgrade",
            actor_user_id=auth_context.user_id,
            idempotency_key="bridge-invalid-direction",
            reason="Direção inválida para teste.",
        )
    except CanonicalPlanDirectionError:
        pass
    else:  # pragma: no cover - assertion aid
        raise AssertionError("downgrade não pode ser aceito como upgrade")


async def test_adjustment_commands_reproject_legacy_prices_atomically(
    db_session,
    auth_context,
) -> None:
    command = CanonicalContractCommand(db_session)
    contract = await command.ensure_core_plan(
        workspace_id=auth_context.workspace_id,
        plan="crm_start",
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-adjustment-contract",
        reason="Contrato Start para testar projeção de desconto.",
    )
    starts_at = datetime.now(UTC) - timedelta(minutes=1)
    adjustment, replay = await command.add_adjustment(
        contract_id=contract.id,
        payload=CommercialAdjustmentCreateRequest(
            type=CommercialAdjustmentType.fixed_discount,
            value=1_000,
            starts_at=starts_at,
            stack_group="bridge-adjustment",
            reason="Aplicar desconto e reprojetar leituras legadas.",
        ),
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-adjustment-add",
    )
    assert replay is False
    subscription = await db_session.scalar(
        select(Subscription).where(Subscription.workspace_id == auth_context.workspace_id)
    )
    local = await db_session.scalar(
        select(WorkspaceSubscription).where(
            WorkspaceSubscription.workspace_id == auth_context.workspace_id
        )
    )
    assert subscription is not None and local is not None
    assert subscription.amount_cents == 25_900
    assert local.valor_mensal_cents == 25_900

    updated, replay = await command.update_adjustment(
        adjustment_id=adjustment.id,
        expected_version=adjustment.version,
        payload=CommercialAdjustmentUpdateRequest(
            type=CommercialAdjustmentType.fixed_discount,
            value=2_000,
            starts_at=starts_at,
            stack_group="bridge-adjustment",
            reason="Corrigir desconto e reprojetar leituras legadas.",
        ),
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-adjustment-update",
    )
    assert replay is False
    assert updated.version == 2
    await db_session.refresh(subscription)
    await db_session.refresh(local)
    assert subscription.amount_cents == 24_900
    assert local.valor_mensal_cents == 24_900

    revoked, replay = await command.revoke_adjustment(
        adjustment_id=adjustment.id,
        expected_version=updated.version,
        payload=CommercialRecordRevokeRequest(
            reason="Encerrar desconto e restaurar preço contratado."
        ),
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-adjustment-revoke",
    )
    assert replay is False
    assert revoked.status is CommercialRecordStatus.revoked
    await db_session.refresh(subscription)
    await db_session.refresh(local)
    assert subscription.amount_cents == 26_900
    assert local.valor_mensal_cents == 26_900


async def test_projection_idempotency_is_namespaced_by_operation_and_contract_version(
    db_session,
    auth_context,
) -> None:
    command = CanonicalContractCommand(db_session)
    shared_key = "client-key-reused-across-valid-operations"
    contract = await command.ensure_core_plan(
        workspace_id=auth_context.workspace_id,
        plan="crm_start",
        actor_user_id=auth_context.user_id,
        idempotency_key=shared_key,
        reason="Criar contrato usando chave reutilizável por operação.",
    )
    revised = await command.change_core_plan(
        workspace_id=auth_context.workspace_id,
        plan="crm_advanced",
        direction="upgrade",
        actor_user_id=auth_context.user_id,
        idempotency_key=shared_key,
        reason="Atualizar contrato com a mesma chave em outra operação.",
    )
    paused = await command.change_status(
        workspace_id=auth_context.workspace_id,
        target_status=CommercialContractStatus.paused,
        actor_user_id=auth_context.user_id,
        idempotency_key=shared_key,
        reason="Pausar contrato com a mesma chave em outra operação.",
    )

    assert revised.version == contract.version + 1
    assert paused.version == revised.version + 1
    events = list(
        (
            await db_session.scalars(
                select(SubscriptionEvent).where(
                    SubscriptionEvent.workspace_id == auth_context.workspace_id,
                    SubscriptionEvent.motivo.like("canonical_projection:%"),
                )
            )
        ).all()
    )
    assert len(events) == 3
    assert len({event.motivo for event in events}) == 3
    assert any("create_contract:" in event.motivo for event in events)
    assert any("revise_contract:" in event.motivo for event in events)
    assert any("change_contract_status:" in event.motivo for event in events)


async def test_projection_namespace_includes_receipt_scope_and_resource(
    db_session,
    auth_context,
) -> None:
    command = CanonicalContractCommand(db_session)
    contract = await command.ensure_core_plan(
        workspace_id=auth_context.workspace_id,
        plan="crm_start",
        actor_user_id=auth_context.user_id,
        idempotency_key="projection-resource-contract",
        reason="Contrato para validar namespace por recurso.",
    )
    starts_at = datetime.now(UTC) - timedelta(minutes=1)
    first, _ = await command.add_adjustment(
        contract_id=contract.id,
        payload=CommercialAdjustmentCreateRequest(
            type=CommercialAdjustmentType.fixed_discount,
            value=1_000,
            starts_at=starts_at,
            stack_group="projection-resource-one",
            reason="Primeiro ajuste para validar o namespace.",
        ),
        actor_user_id=auth_context.user_id,
        idempotency_key="projection-resource-add-one",
    )
    second, _ = await command.add_adjustment(
        contract_id=contract.id,
        payload=CommercialAdjustmentCreateRequest(
            type=CommercialAdjustmentType.fixed_discount,
            value=2_000,
            starts_at=starts_at,
            stack_group="projection-resource-two",
            reason="Segundo ajuste para validar o namespace.",
        ),
        actor_user_id=auth_context.user_id,
        idempotency_key="projection-resource-add-two",
    )

    shared_receipt_key = "same-client-key-for-two-adjustments"
    first_updated, first_replay = await command.update_adjustment(
        adjustment_id=first.id,
        expected_version=first.version,
        payload=CommercialAdjustmentUpdateRequest(
            type=CommercialAdjustmentType.fixed_discount,
            value=1_500,
            starts_at=starts_at,
            stack_group="projection-resource-one",
            reason="Atualizar o primeiro recurso com chave compartilhada.",
        ),
        actor_user_id=auth_context.user_id,
        idempotency_key=shared_receipt_key,
    )
    second_updated, second_replay = await command.update_adjustment(
        adjustment_id=second.id,
        expected_version=second.version,
        payload=CommercialAdjustmentUpdateRequest(
            type=CommercialAdjustmentType.fixed_discount,
            value=2_500,
            starts_at=starts_at,
            stack_group="projection-resource-two",
            reason="Atualizar o segundo recurso com chave compartilhada.",
        ),
        actor_user_id=auth_context.user_id,
        idempotency_key=shared_receipt_key,
    )

    assert first_replay is False and second_replay is False
    assert first_updated.id != second_updated.id
    events = list(
        (
            await db_session.scalars(
                select(SubscriptionEvent).where(
                    SubscriptionEvent.workspace_id == auth_context.workspace_id,
                    SubscriptionEvent.motivo.like(
                        "canonical_projection:update_adjustment:%"
                    ),
                )
            )
        ).all()
    )
    assert len(events) == 2
    assert len({item.motivo for item in events}) == 2
    subscription = await db_session.scalar(
        select(Subscription).where(
            Subscription.workspace_id == auth_context.workspace_id
        )
    )
    assert subscription is not None
    assert subscription.amount_cents == 22_900


async def test_change_status_replay_uses_stored_temporal_envelope(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    command = CanonicalContractCommand(db_session)
    await command.ensure_core_plan(
        workspace_id=auth_context.workspace_id,
        plan="crm_start",
        actor_user_id=auth_context.user_id,
        idempotency_key="status-time-contract",
        reason="Contrato para validar o relógio da transição.",
        project_legacy=False,
    )
    command_at = datetime.now(UTC) + timedelta(seconds=1)
    receipt_key = "status-time-envelope"
    changed = await command.change_status(
        workspace_id=auth_context.workspace_id,
        target_status=CommercialContractStatus.past_due,
        actor_user_id=auth_context.user_id,
        idempotency_key=receipt_key,
        reason="Entrar em inadimplência no instante capturado.",
        effective_at=command_at,
    )
    subscription = await db_session.scalar(
        select(Subscription).where(
            Subscription.workspace_id == auth_context.workspace_id
        )
    )
    assert subscription is not None
    subscription.current_period_start = None
    subscription.current_period_end = None
    await db_session.flush()

    replay = await command.change_status(
        workspace_id=auth_context.workspace_id,
        target_status=CommercialContractStatus.past_due,
        actor_user_id=auth_context.user_id,
        idempotency_key=receipt_key,
        reason="Entrar em inadimplência no instante capturado.",
        effective_at=command_at,
    )
    assert replay.id == changed.id
    await db_session.refresh(subscription)
    assert subscription.current_period_start is not None
    assert subscription.current_period_end is not None
    assert subscription.current_period_start.replace(tzinfo=UTC) == command_at
    assert subscription.current_period_end.replace(tzinfo=UTC) == command_at + timedelta(
        days=30
    )

    with pytest.raises(CommercialIdempotencyConflictError):
        await command.change_status(
            workspace_id=auth_context.workspace_id,
            target_status=CommercialContractStatus.past_due,
            actor_user_id=auth_context.user_id,
            idempotency_key=receipt_key,
            reason="Entrar em inadimplência no instante capturado.",
            effective_at=command_at + timedelta(seconds=1),
        )

    generated_clock_key = "status-generated-time-envelope"
    generated_command_at = command_at + timedelta(seconds=2)
    real_utc = bridge_service_module._utc

    def _first_generated_wall_clock(value=None):
        if value is None:
            return generated_command_at
        return real_utc(value)

    monkeypatch.setattr(
        bridge_service_module,
        "_utc",
        _first_generated_wall_clock,
    )
    changed_without_explicit_time = await command.change_status(
        workspace_id=auth_context.workspace_id,
        target_status=CommercialContractStatus.active,
        actor_user_id=auth_context.user_id,
        idempotency_key=generated_clock_key,
        reason="Reativar sem instante explícito e preservar o primeiro relógio.",
    )
    await db_session.refresh(subscription)
    subscription.current_period_start = None
    subscription.current_period_end = None
    await db_session.flush()

    def _future_wall_clock(value=None):
        if value is None:
            return generated_command_at + timedelta(days=7)
        return real_utc(value)

    monkeypatch.setattr(bridge_service_module, "_utc", _future_wall_clock)
    replay_without_explicit_time = await command.change_status(
        workspace_id=auth_context.workspace_id,
        target_status=CommercialContractStatus.active,
        actor_user_id=auth_context.user_id,
        idempotency_key=generated_clock_key,
        reason="Reativar sem instante explícito e preservar o primeiro relógio.",
    )
    assert replay_without_explicit_time.id == changed_without_explicit_time.id
    await db_session.refresh(subscription)
    assert subscription.current_period_start is not None
    assert (
        subscription.current_period_start.replace(tzinfo=UTC)
        == generated_command_at
    )


@pytest.mark.parametrize(
    ("role_package", "grants"),
    [
        ("sales_sdr", []),
        ("onboarding", []),
        (
            "marketing_content",
            [{"module": "finance", "action": "manage"}],
        ),
    ],
)
async def test_broad_domain_permissions_cannot_manage_adjustments_or_bonuses(
    db_session,
    auth_context,
    role_package: str,
    grants: list[dict[str, str]],
) -> None:
    contract = await CanonicalContractCommand(db_session).ensure_core_plan(
        workspace_id=auth_context.workspace_id,
        plan="crm_start",
        actor_user_id=auth_context.user_id,
        idempotency_key=f"operation-auth-contract-{role_package}",
        reason="Contrato para validar autorização por operação.",
        project_legacy=False,
    )
    actor = User(
        name=f"Operador {role_package}",
        email=f"operation-auth-{role_package}-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="OA",
        is_active=True,
        is_internal_staff=True,
        platform_access_active=True,
        platform_role_package=role_package,
        platform_permission_grants=grants,
        platform_permission_denials=[],
    )
    db_session.add(actor)
    await db_session.flush()
    command = CanonicalContractCommand(db_session, control_plane=True)
    starts_at = datetime.now(UTC) - timedelta(minutes=1)

    with pytest.raises(CommercialBridgeError, match="esta operação"):
        await command.add_adjustment(
            contract_id=contract.id,
            payload=CommercialAdjustmentCreateRequest(
                type=CommercialAdjustmentType.fixed_discount,
                value=100,
                starts_at=starts_at,
                stack_group=f"unauthorized-{role_package}",
                reason="Permissão ampla não pode autorizar ajuste.",
            ),
            actor_user_id=actor.id,
            idempotency_key=f"unauthorized-adjustment-{role_package}",
            project_legacy=False,
        )
    with pytest.raises(CommercialBridgeError, match="esta operação"):
        await command.add_bonus(
            workspace_id=auth_context.workspace_id,
            payload=EntitlementBonusCreateRequest(
                entitlement=EntitlementKey.tokens,
                mode=EntitlementBonusMode.quantity_delta,
                quantity=1_000,
                starts_at=starts_at,
                renews_each_period=True,
                reason="Permissão ampla não pode autorizar bônus.",
            ),
            actor_user_id=actor.id,
            idempotency_key=f"unauthorized-bonus-{role_package}",
        )


async def test_portability_namespace_opens_only_after_contract_authorization(
    db_session,
    monkeypatch,
) -> None:
    actor = User(
        name="Root de teste da portabilidade",
        email=f"portability-root-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="PR",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=True,
        platform_access_active=True,
        platform_role_package="platform_owner",
        platform_permission_grants=[],
        platform_permission_denials=[],
    )
    db_session.add(actor)
    await db_session.flush()
    calls: list[tuple[object, ...]] = []

    async def record_generic_context(
        _session,
        *,
        actor_user_id,
        module,
        action,
        is_platform_root=False,
    ) -> None:
        calls.append(
            (
                "generic",
                actor_user_id,
                module,
                action,
                is_platform_root,
            )
        )

    async def record_normal_internal_context(
        _session,
        *,
        actor_user_id,
        is_platform_root,
    ) -> None:
        calls.append(("normal_internal", actor_user_id, is_platform_root))

    async def record_restore_internal_context(
        _session,
        *,
        actor_user_id,
        is_platform_root,
    ) -> None:
        calls.append(("restore_internal", actor_user_id, is_platform_root))

    monkeypatch.setattr(
        bridge_service_module,
        "set_platform_control_context",
        record_generic_context,
    )
    monkeypatch.setattr(
        bridge_service_module,
        "_set_commercial_portability_rebind_context",
        record_normal_internal_context,
    )
    monkeypatch.setattr(
        bridge_service_module,
        "_set_commercial_portability_restore_rebind_context",
        record_restore_internal_context,
    )
    monkeypatch.setattr(
        bridge_service_module,
        "_set_commercial_portability_restore_rebind_context",
        record_restore_internal_context,
    )
    command = CanonicalContractCommand(db_session, control_plane=True)

    with pytest.raises(
        CommercialBridgeError,
        match="operação de plataforma autorizada",
    ):
        async with command._portability_rebind_context(
            actor_user_id=actor.id,
            restore_permission=None,
        ):
            pytest.fail("O contexto reservado não pode abrir sem autorização externa.")
    with pytest.raises(
        CommercialBridgeError,
        match="operação de plataforma autorizada",
    ):
        async with command._portability_restore_rebind_context(
            actor_user_id=actor.id,
            restore_permission=None,
        ):
            pytest.fail("O contexto de restore não pode abrir sem autorização externa.")
    assert calls == []

    authorized_permission = await command._authorize_control_plane(
        actor.id,
        allowed_permissions=command._CONTRACT_MANAGE,
    )
    assert authorized_permission == ("commercial_contracts", "manage")

    async with command._portability_rebind_context(
        actor_user_id=actor.id,
        restore_permission=authorized_permission,
    ):
        calls.append(("body",))

    assert calls == [
        (
            "generic",
            actor.id,
            "commercial_contracts",
            "manage",
            True,
        ),
        ("normal_internal", actor.id, True),
        ("body",),
        (
            "generic",
            actor.id,
            "commercial_contracts",
            "manage",
            True,
        ),
    ]

    calls.clear()
    async with command._portability_restore_rebind_context(
        actor_user_id=actor.id,
        restore_permission=authorized_permission,
    ):
        calls.append(("restore_body",))

    assert calls == [
        ("restore_internal", actor.id, True),
        ("restore_body",),
        (
            "generic",
            actor.id,
            "commercial_contracts",
            "manage",
            True,
        ),
    ]

    calls.clear()
    with pytest.raises(RuntimeError, match="falha no restore"):
        async with command._portability_restore_rebind_context(
            actor_user_id=actor.id,
            restore_permission=authorized_permission,
        ):
            raise RuntimeError("falha no restore depois de abrir o contexto")

    assert calls == [
        ("restore_internal", actor.id, True),
        (
            "generic",
            actor.id,
            "commercial_contracts",
            "manage",
            True,
        ),
    ]

    calls.clear()
    with pytest.raises(RuntimeError, match="falha de aplicação"):
        async with command._portability_rebind_context(
            actor_user_id=actor.id,
            restore_permission=authorized_permission,
        ):
            raise RuntimeError("falha de aplicação depois de abrir o contexto")

    assert calls == [
        ("normal_internal", actor.id, True),
        (
            "generic",
            actor.id,
            "commercial_contracts",
            "manage",
            True,
        ),
    ]

    calls.clear()
    with pytest.raises(
        CommercialBridgeError,
        match="operação de plataforma autorizada",
    ):
        async with command._portability_restore_rebind_context(
            actor_user_id=actor.id,
            restore_permission=None,
        ):
            pytest.fail("O contexto de restauração não abre sem autorização externa.")
    assert calls == []

    async with command._portability_restore_rebind_context(
        actor_user_id=actor.id,
        restore_permission=authorized_permission,
    ):
        calls.append(("restore_body",))

    assert calls == [
        ("restore_internal", actor.id, True),
        ("restore_body",),
        (
            "generic",
            actor.id,
            "commercial_contracts",
            "manage",
            True,
        ),
    ]


async def test_restore_reprojects_both_legacy_models_and_preserves_exceptions(
    db_session,
    auth_context,
) -> None:
    command = CanonicalContractCommand(db_session)
    original = await command.ensure_core_plan(
        workspace_id=auth_context.workspace_id,
        plan="crm_start",
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-restore-create",
        reason="Contrato Start para testar restauração.",
    )
    original_line = next(line for line in original.lines if line.sku == "crm_start")
    now = datetime.now(UTC)
    adjustment, _ = await command.catalog.add_adjustment(
        contract_id=original.id,
        payload=CommercialAdjustmentCreateRequest(
            contract_line_id=original_line.id,
            type=CommercialAdjustmentType.fixed_discount,
            value=1_000,
            starts_at=now,
            stack_group="bridge-restore",
            reason="Desconto preservado na troca e restauração.",
        ),
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-restore-adjustment",
    )
    bonus, _ = await command.catalog.add_bonus(
        workspace_id=auth_context.workspace_id,
        payload=EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.tokens,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=25_000,
            starts_at=now,
            renews_each_period=True,
            reason="Bônus preservado na troca e restauração.",
        ),
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-restore-bonus",
    )

    upgraded = await command.ensure_core_plan(
        workspace_id=auth_context.workspace_id,
        plan="crm_advanced",
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-restore-upgrade",
        reason="Upgrade para validar a projeção avançada.",
    )
    assert upgraded.version == 2
    advanced_subscription = await db_session.scalar(
        select(Subscription).where(
            Subscription.workspace_id == auth_context.workspace_id
        )
    )
    assert advanced_subscription is not None
    advanced_plan = await db_session.get(Plan, advanced_subscription.plan_id)
    assert advanced_plan is not None and advanced_plan.tier is PlanTier.pro
    advanced_local = await db_session.scalar(
        select(WorkspaceSubscription).where(
            WorkspaceSubscription.workspace_id == auth_context.workspace_id
        )
    )
    assert advanced_local is not None
    assert advanced_local.plano.value == "escritorio"

    restored, replay = await command.restore_contract(
        contract_id=original.id,
        payload=WorkspaceContractRestoreRequest(
            target_version=1,
            expected_current_version=2,
            reason="Restaurar Start preservando exceções individuais.",
        ),
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-restore-apply",
    )
    assert replay is False
    assert restored.version == 3
    assert [line.sku for line in restored.lines if line.status.value == "active"] == [
        "crm_start"
    ]
    start_line = next(
        line
        for line in restored.lines
        if line.sku == "crm_start" and line.status.value == "active"
    )

    restored_subscription = await db_session.scalar(
        select(Subscription).where(
            Subscription.workspace_id == auth_context.workspace_id
        )
    )
    assert restored_subscription is not None
    restored_plan = await db_session.get(Plan, restored_subscription.plan_id)
    assert restored_plan is not None and restored_plan.tier is PlanTier.starter
    restored_local = await db_session.scalar(
        select(WorkspaceSubscription).where(
            WorkspaceSubscription.workspace_id == auth_context.workspace_id
        )
    )
    assert restored_local is not None
    assert restored_local.plano.value == "essencial"
    assert restored_local.metadata_json["canonicalContractVersion"] == 3

    preserved_adjustment = await db_session.get(CommercialAdjustment, adjustment.id)
    preserved_bonus = await db_session.get(EntitlementBonus, bonus.id)
    assert preserved_adjustment is not None
    assert preserved_adjustment.status is CommercialRecordStatus.active
    assert preserved_adjustment.contract_line_id == start_line.id
    assert preserved_bonus is not None
    assert preserved_bonus.status is CommercialRecordStatus.active


async def test_local_seed_converges_existing_core_contract_to_evolution_and_legal(
    db_session,
    auth_context,
) -> None:
    command = CanonicalContractCommand(db_session)
    existing = await command.ensure_core_plan(
        workspace_id=auth_context.workspace_id,
        plan="crm_start",
        actor_user_id=auth_context.user_id,
        idempotency_key="bridge-local-existing-start",
        reason="Contrato local antigo antes da convergência.",
    )
    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    assert workspace is not None

    local_seed = KlmLocalSeedService(db_session)
    platform_owner = await local_seed._ensure_local_platform_owner()
    await local_seed._ensure_local_commercial_contract(
        workspace,
        platform_owner,
    )

    converged = await command.catalog.get_contract(existing.id)
    assert converged.version == 2
    assert {
        line.sku for line in converged.lines if line.status.value == "active"
    } == {"crm_evolution", "legal_360"}
    projection = await db_session.scalar(
        select(WorkspaceSubscription).where(
            WorkspaceSubscription.workspace_id == auth_context.workspace_id
        )
    )
    assert projection is not None
    assert projection.plano.value == "performance"
    assert projection.metadata_json["canonicalContractVersion"] == 2


async def test_local_commercial_seed_rejects_tenant_actor(
    db_session,
    auth_context,
) -> None:
    user = await db_session.get(User, auth_context.user_id)
    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    assert user is not None and workspace is not None

    with pytest.raises(RuntimeError, match="platform_owner real"):
        await KlmLocalSeedService(db_session)._ensure_local_commercial_contract(
            workspace,
            user,
        )
