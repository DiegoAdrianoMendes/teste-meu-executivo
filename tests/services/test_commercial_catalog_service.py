from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.models.billing import BillingInterval, Plan, PlatformModelUsageLedger
from app.models.commercial_catalog import (
    CommercialAdjustment,
    CommercialAdjustmentSource,
    CommercialAdjustmentType,
    CommercialBillingInterval,
    CommercialCatalogEvent,
    CommercialCommandReceipt,
    CommercialContractStatus,
    CommercialOfferVersion,
    CommercialPortability,
    CommercialRecordStatus,
    EntitlementBonus,
    EntitlementBonusMode,
    WorkspaceCommercialContract,
    WorkspaceContractLine,
)
from app.models.entitlement import (
    EntitlementKey,
    OverrideMode,
    WorkspaceEntitlementOverride,
)
from app.models.subscription_commercial import WorkspaceSubscription
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.commercial_catalog import (
    CommercialAdjustmentCreateRequest,
    CommercialAdjustmentUpdateRequest,
    CommercialEntitlementEnforcement,
    CommercialOfferDraftPublishRequest,
    CommercialOfferDraftSaveRequest,
    CommercialOfferPublishRequest,
    CommercialOfferRestoreRequest,
    CommercialRecordRevokeRequest,
    EntitlementBonusCreateRequest,
    EntitlementBonusUpdateRequest,
    WorkspaceContractCreateRequest,
    WorkspaceContractLineWrite,
    WorkspaceContractPreviewRequest,
    WorkspaceContractRestoreRequest,
    WorkspaceContractRevisionPreviewRequest,
    WorkspaceContractRevisionRequest,
)
from app.services.commercial_catalog_service import (
    CATALOG_AUTHORITY_LOCK_NAMESPACE,
    CATALOG_AUTHORITY_LOCK_VALUE,
    CommercialCatalogConflictError,
    CommercialCatalogService,
    CommercialCatalogValidationError,
    canonical_sku_for_legacy_plan_key,
)
from app.services.commercial_legacy_bridge_service import LegacyCommercialProjection
from app.services.entitlement_service import (
    EntitlementDisabledError,
    EntitlementEnforcementService,
    EntitlementLimitSource,
    EntitlementNotConfiguredError,
    EntitlementResolver,
    MonthlyQuotaExceededError,
)
from app.services.plan_policy_service import (
    CATALOG_AUTHORITY_LOCK_NAMESPACE as LEGACY_CATALOG_AUTHORITY_LOCK_NAMESPACE,
)
from app.services.plan_policy_service import (
    CATALOG_AUTHORITY_LOCK_VALUE as LEGACY_CATALOG_AUTHORITY_LOCK_VALUE,
)
from app.services.platform_service import PlatformService
from app.services.workspace_service import WorkspaceService


async def _workspace(db_session, label: str = "Conta comercial") -> Workspace:
    workspace = Workspace(
        name=label,
        slug=f"commercial-{uuid4().hex[:10]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


async def _catalog(db_session, actor_id):
    service = CommercialCatalogService(db_session)
    items, replay = await service.bootstrap_default_catalog(
        actor_user_id=actor_id,
        idempotency_key=f"bootstrap-{uuid4()}",
    )
    assert replay is False
    return service, {item.sku: item for item in items}


@pytest.mark.asyncio
async def test_offer_identity_lookup_does_not_require_update_privilege() -> None:
    """The append-only offer table must remain readable by the runtime role."""

    db = AsyncMock()
    expected = object()
    db.scalar.return_value = expected

    result = await CommercialCatalogService(db)._offer_by_sku("crm_start")

    statement = db.scalar.await_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert result is expected
    assert "FOR UPDATE" not in compiled.upper()


def test_commercial_reason_rejects_whitespace_and_is_normalized() -> None:
    with pytest.raises(ValidationError):
        CommercialRecordRevokeRequest(reason="        ")

    payload = CommercialRecordRevokeRequest(reason="  Motivo auditável.  ")

    assert payload.reason == "Motivo auditável."


@pytest.mark.asyncio
async def test_canonical_publication_fences_legacy_policy_writers(
    db_session, platform_auth_context, monkeypatch
) -> None:
    lock_calls: list[tuple[str, str]] = []

    async def _capture_lock(_session, *, namespace: str, value: str) -> bool:
        lock_calls.append((namespace, value))
        return True

    monkeypatch.setattr(
        "app.services.commercial_catalog_service.acquire_transaction_advisory_lock",
        _capture_lock,
    )
    service = CommercialCatalogService(db_session)
    items, _ = await service.bootstrap_default_catalog(
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"catalog-fence-bootstrap-{uuid4()}",
    )
    first_offer = items[0]
    await service.publish_offer(
        payload=CommercialOfferPublishRequest(
            sku=first_offer.sku,
            family=first_offer.family,
            kind=first_offer.kind,
            legacy_tier=first_offer.legacy_tier,
            snapshot=first_offer.snapshot,
            reason="Comprovar fence bilateral do catálogo canônico.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"catalog-fence-publish-{uuid4()}",
    )

    authority_lock = (
        CATALOG_AUTHORITY_LOCK_NAMESPACE,
        CATALOG_AUTHORITY_LOCK_VALUE,
    )
    assert authority_lock == (
        LEGACY_CATALOG_AUTHORITY_LOCK_NAMESPACE,
        LEGACY_CATALOG_AUTHORITY_LOCK_VALUE,
    )
    assert lock_calls[0] == authority_lock
    assert lock_calls[3] == authority_lock


async def _seed_ai_unit_economics_evidence(
    db_session,
    workspace: Workspace,
    *,
    omit_route_key: str | None = None,
) -> None:
    from app.ai.canonical_catalog import resolve_price_catalog, resolve_route_catalog

    route_catalog = resolve_route_catalog(settings.KLM_AI_ROUTE_CATALOG_JSON)
    price_catalog = resolve_price_catalog(settings.KLM_AI_PRICE_CATALOG_JSON)
    active_routes = {
        candidate.key: candidate
        for candidates in (*route_catalog.classes.values(), *route_catalog.agents.values())
        for candidate in candidates
        if candidate.key != omit_route_key
    }
    routes = list(active_routes.values())
    assert routes
    now = datetime.now(UTC)
    db_session.add_all(
        [
            PlatformModelUsageLedger(
                workspace_id=workspace.id,
                request_id=f"unit-economics-{workspace.id}-{index}",
                task_class="simple",
                operation=f"unit_economics_evidence:{routes[index % len(routes)].key}",
                model_used=routes[index % len(routes)].model,
                provider=routes[index % len(routes)].provider.value,
                provider_request_id=f"provider-receipt-{workspace.id}-{index}",
                input_tokens=1_500,
                output_tokens=500,
                cached_input_tokens=250,
                cost_usd=price_catalog.compute_cost(
                    routes[index % len(routes)],
                    input_tokens=1_500,
                    output_tokens=500,
                    cached_input_tokens=250,
                ),
                price_catalog_version=price_catalog.version,
                route_catalog_version=route_catalog.version,
                recorded_at=now - timedelta(days=index % 7),
            )
            for index in range(max(30, len(routes) * 4))
        ]
    )
    await db_session.flush()


def _token_only_public_snapshot(offer):
    """Remove dimensions whose provider cost is not represented by token receipts."""

    entitlements = dict(offer.snapshot.entitlements)
    entitlements[EntitlementKey.audio_minutes] = entitlements[
        EntitlementKey.audio_minutes
    ].model_copy(update={"enabled": False, "limit_quantity": None})
    entitlements[EntitlementKey.tokens] = entitlements[
        EntitlementKey.tokens
    ].model_copy(update={"enabled": True, "limit_quantity": 100_000})
    return offer.snapshot.model_copy(
        update={"is_public": True, "entitlements": entitlements}
    )


def _legal_line(catalog, *, price: int = 89_900) -> WorkspaceContractLineWrite:
    legal = catalog["legal_360"]
    settings = legal.snapshot.entitlements
    return WorkspaceContractLineWrite(
        sku="legal_360",
        policy_version_id=legal.id,
        contracted_price_cents=price,
        entitlement_overrides={
            EntitlementKey.users: settings[EntitlementKey.users].model_copy(
                update={"limit_quantity": 2}
            ),
            EntitlementKey.legal_areas: settings[EntitlementKey.legal_areas].model_copy(
                update={"limit_quantity": 3}
            ),
            EntitlementKey.storage_bytes: settings[EntitlementKey.storage_bytes].model_copy(
                update={"limit_quantity": 10 * 1024**3}
            ),
            EntitlementKey.monitored_processes: settings[
                EntitlementKey.monitored_processes
            ].model_copy(update={"limit_quantity": 0}),
            EntitlementKey.legal_documents: settings[EntitlementKey.legal_documents].model_copy(
                update={"enabled": True, "limit_quantity": 1_000}
            ),
            EntitlementKey.protocol_folders: settings[EntitlementKey.protocol_folders].model_copy(
                update={"enabled": True, "limit_quantity": 100}
            ),
        },
    )


@pytest.mark.asyncio
async def test_bootstrap_has_four_canonical_offers_and_is_idempotent(
    db_session, platform_auth_context
) -> None:
    service = CommercialCatalogService(db_session)
    items, replay = await service.bootstrap_default_catalog(
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="catalog-v1-test",
    )
    assert replay is False
    by_sku = {item.sku: item for item in items}
    assert set(by_sku) == {
        "crm_start",
        "crm_advanced",
        "crm_evolution",
        "legal_360",
    }
    assert by_sku["crm_start"].snapshot.price_monthly_cents == 26_900
    assert by_sku["crm_advanced"].snapshot.price_monthly_cents == 67_900
    assert by_sku["crm_advanced"].snapshot.is_recommended is True
    assert by_sku["crm_evolution"].snapshot.price_monthly_cents == 124_900
    assert by_sku["legal_360"].snapshot.is_quote_only is True
    assert all(item.snapshot.price_annual_cents is None for item in items)
    assert all(
        by_sku[sku].snapshot.is_public is False
        for sku in ("crm_start", "crm_advanced", "crm_evolution")
    )
    assert by_sku["legal_360"].snapshot.is_public is True
    expected_campaign_limits = {
        "crm_start": 200,
        "crm_advanced": 1_000,
        "crm_evolution": 3_000,
    }
    expected_contact_limits = {
        "crm_start": 1_000,
        "crm_advanced": 10_000,
        "crm_evolution": 50_000,
    }
    for sku, expected_limit in expected_campaign_limits.items():
        campaign_policy = by_sku[sku].snapshot.entitlements[EntitlementKey.campaign_recipients]
        assert campaign_policy.enabled is True
        assert campaign_policy.limit_quantity == expected_limit
        assert campaign_policy.period.value == "monthly"
        assert campaign_policy.enforcement is CommercialEntitlementEnforcement.hard
    for sku, expected_limit in expected_contact_limits.items():
        snapshot = by_sku[sku].snapshot
        assert snapshot.schema_version == "3.0"
        contact_policy = snapshot.entitlements[EntitlementKey.contacts]
        assert contact_policy.enabled is True
        assert contact_policy.limit_quantity == expected_limit
        assert contact_policy.period.value == "stock"
        assert contact_policy.composition.value == "maximum"
        assert contact_policy.enforcement is CommercialEntitlementEnforcement.hard

    legal_contacts = by_sku["legal_360"].snapshot.entitlements[EntitlementKey.contacts]
    assert legal_contacts.enabled is False
    assert legal_contacts.limit_quantity is None

    replayed, was_replay = await service.bootstrap_default_catalog(
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="catalog-v1-test",
    )
    assert was_replay is True
    assert [item.id for item in replayed] == [item.id for item in items]
    assert int(await db_session.scalar(select(func.count(CommercialOfferVersion.id))) or 0) == 4


@pytest.mark.asyncio
async def test_canonical_catalog_blocks_parallel_legacy_plan_edits(
    db_session, platform_auth_context
) -> None:
    await _catalog(db_session, platform_auth_context.user_id)

    assert canonical_sku_for_legacy_plan_key("basic") == "crm_start"
    assert canonical_sku_for_legacy_plan_key("Advanced") == "crm_advanced"
    assert canonical_sku_for_legacy_plan_key("evolution") == "crm_evolution"
    with pytest.raises(ValueError, match="catálogo canônico já está ativo"):
        await PlatformService(db_session).upsert_plan_catalog_item("basic", {})


@pytest.mark.asyncio
async def test_incomplete_legacy_plan_editor_fails_before_any_catalog_write(
    db_session,
) -> None:
    service = PlatformService(db_session)

    gaps = service._legacy_plan_editor_unmapped_entitlements()
    assert set(gaps) == {
        item.value
        for item in set(EntitlementKey)
        - set(service.LIMIT_ENTITLEMENTS.values())
        - set(service.PLATFORM_FEATURE_ENTITLEMENTS.values())
    }
    assert gaps
    with pytest.raises(ValueError, match="editor legado está desativado"):
        await service.upsert_plan_catalog_item("basic", {})

    assert int(await db_session.scalar(select(func.count(Plan.id))) or 0) == 0
    assert int(await db_session.scalar(select(func.count(CommercialOfferVersion.id))) or 0) == 0


@pytest.mark.asyncio
async def test_public_ai_offer_requires_real_unit_economics_evidence(
    db_session,
    platform_auth_context,
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    start = catalog["crm_start"]
    public_snapshot = _token_only_public_snapshot(start)
    draft, _ = await service.save_offer_draft(
        sku=start.sku,
        payload=CommercialOfferDraftSaveRequest(
            expected_published_version=start.version,
            snapshot=public_snapshot,
            reason="Preparar oferta pública com franquia de IA.",
        ),
        expected_draft_version=None,
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="public-ai-draft",
    )

    blocked = await service.preview_offer_draft(start.sku)
    assert blocked.eligible is False
    assert blocked.unit_economics.status == "insufficient_data"
    assert {item.code for item in blocked.unit_economics.blockers} >= {
        "AI_COST_SAMPLE_TOO_SMALL",
        "AI_COST_WINDOW_TOO_NARROW",
        "AI_TOKEN_SAMPLE_TOO_SMALL",
    }
    with pytest.raises(CommercialCatalogValidationError, match="unit economics"):
        await service.publish_offer_draft(
            sku=start.sku,
            expected_draft_version=draft.draft_version,
            payload=CommercialOfferDraftPublishRequest(
                reason="Tentar publicar sem fabricar evidência."
            ),
            actor_user_id=platform_auth_context.user_id,
            idempotency_key="public-ai-blocked",
        )

    public_before, total_before = await service.list_offers(public_only=True)
    assert total_before == 1
    assert [item.sku for item in public_before] == ["legal_360"]

    evidence_workspace = await _workspace(db_session, "Amostra real de IA")
    await _seed_ai_unit_economics_evidence(
        db_session,
        evidence_workspace,
    )
    ready = await service.preview_offer_draft(start.sku)
    assert ready.eligible is True
    assert ready.unit_economics.status == "ready"
    assert ready.unit_economics.observed_calls >= 30
    assert ready.unit_economics.observed_days == 7
    assert ready.unit_economics.observed_tokens >= 60_000

    published, replay = await service.publish_offer_draft(
        sku=start.sku,
        expected_draft_version=draft.draft_version,
        payload=CommercialOfferDraftPublishRequest(reason="Publicar após amostra real suficiente."),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="public-ai-ready",
    )
    assert replay is False
    assert published.version == 2
    assert published.snapshot.is_public is True
    public_after, total_after = await service.list_offers(public_only=True)
    assert total_after == 2
    assert {item.sku for item in public_after} == {"crm_start", "legal_360"}


@pytest.mark.asyncio
async def test_public_ai_offer_prices_all_directions_with_active_internal_rate_card(
    db_session,
    platform_auth_context,
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    evidence_workspace = await _workspace(db_session, "Mix de rotas de IA")
    await _seed_ai_unit_economics_evidence(
        db_session,
        evidence_workspace,
    )
    start = catalog["crm_start"]
    expensive = _token_only_public_snapshot(start)
    expensive_entitlements = dict(expensive.entitlements)
    expensive_entitlements[EntitlementKey.tokens] = expensive_entitlements[
        EntitlementKey.tokens
    ].model_copy(update={"limit_quantity": 5_000_000})
    await service.save_offer_draft(
        sku=start.sku,
        payload=CommercialOfferDraftSaveRequest(
            expected_published_version=start.version,
            snapshot=expensive.model_copy(update={"entitlements": expensive_entitlements}),
            reason="Avaliar oferta contra todas as direções da tabela interna.",
        ),
        expected_draft_version=None,
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="public-ai-expensive-draft",
    )

    preview = await service.preview_offer_draft(start.sku)
    assert preview.eligible is False
    assert preview.unit_economics.status == "unprofitable"
    assert [item.code for item in preview.unit_economics.blockers] == [
        "AI_COST_SHARE_ABOVE_INTERNAL_CEILING"
    ]
    assert any(
        "input até" in item and "output até" in item and "cache até" in item
        for item in preview.unit_economics.diagnostics
    )


@pytest.mark.asyncio
async def test_public_ai_offer_requires_receipt_for_every_active_fallback_route(
    db_session,
    platform_auth_context,
) -> None:
    from app.ai.canonical_catalog import resolve_route_catalog

    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    evidence_workspace = await _workspace(db_session, "Amostra incompleta por rota")
    route_catalog = resolve_route_catalog(settings.KLM_AI_ROUTE_CATALOG_JSON)
    omitted = next(
        candidate.key
        for candidates in route_catalog.classes.values()
        for candidate in candidates
    )
    await _seed_ai_unit_economics_evidence(
        db_session,
        evidence_workspace,
        omit_route_key=omitted,
    )
    start = catalog["crm_start"]
    await service.save_offer_draft(
        sku=start.sku,
        payload=CommercialOfferDraftSaveRequest(
            expected_published_version=start.version,
            snapshot=_token_only_public_snapshot(start),
            reason="Exigir cobertura de todo fallback ativo.",
        ),
        expected_draft_version=None,
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="public-ai-missing-fallback-draft",
    )

    preview = await service.preview_offer_draft(start.sku)
    assert preview.eligible is False
    blocker = next(
        item
        for item in preview.unit_economics.blockers
        if item.code == "AI_ACTIVE_ROUTE_EVIDENCE_MISSING"
    )
    assert omitted in blocker.message


@pytest.mark.asyncio
async def test_public_offer_never_treats_token_receipts_as_audio_cost_evidence(
    db_session,
    platform_auth_context,
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    evidence_workspace = await _workspace(db_session, "Amostra apenas tokenizada")
    await _seed_ai_unit_economics_evidence(
        db_session,
        evidence_workspace,
    )
    start = catalog["crm_start"]
    await service.save_offer_draft(
        sku=start.sku,
        payload=CommercialOfferDraftSaveRequest(
            expected_published_version=start.version,
            snapshot=start.snapshot.model_copy(update={"is_public": True}),
            reason="Validar custo independente de transcrição.",
        ),
        expected_draft_version=None,
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="public-audio-unmodeled-draft",
    )

    preview = await service.preview_offer_draft(start.sku)
    assert preview.eligible is False
    assert preview.unit_economics.status == "insufficient_data"
    assert "PAID_DIMENSION_COST_MODEL_MISSING" in {
        item.code for item in preview.unit_economics.blockers
    }
    assert any(
        "Evidência de tokens não comprova esse custo" in item.message
        for item in preview.unit_economics.blockers
    )


@pytest.mark.asyncio
async def test_legal_standalone_and_combined_contracts_compose_without_duplicate_data(
    db_session, platform_auth_context
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    legal_workspace = await _workspace(db_session, "Jurídico standalone")
    legal_contract, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=legal_workspace.id,
            lines=[_legal_line(catalog)],
            reason="Contrato jurídico standalone aprovado.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="legal-standalone",
    )
    assert [line.sku for line in legal_contract.lines] == ["legal_360"]
    manifest = await service.resolve_effective_manifest(legal_workspace.id)
    assert manifest.active_products == ["legal_360"]
    assert manifest.capabilities[EntitlementKey.legal_suite].permitted is True
    assert manifest.capabilities[EntitlementKey.legal_areas].limit == 3
    assert manifest.capabilities[EntitlementKey.monitored_processes].limit == 0

    combined_workspace = await _workspace(db_session, "CRM e jurídico")
    combined, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=combined_workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start", policy_version_id=catalog["crm_start"].id
                ),
                _legal_line(catalog),
            ],
            reason="Contrato combinado aprovado.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="crm-plus-legal",
    )
    assert {line.sku for line in combined.lines} == {"crm_start", "legal_360"}
    combined_manifest = await service.resolve_effective_manifest(combined_workspace.id)
    assert combined_manifest.capabilities[EntitlementKey.users].limit == 3
    assert combined_manifest.capabilities[EntitlementKey.storage_bytes].limit == 15 * 1024**3
    assert combined_manifest.capabilities[EntitlementKey.legal_suite].enabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize("contracted_price_cents", [None, 0])
async def test_quote_only_offer_requires_strictly_positive_contracted_price(
    db_session,
    platform_auth_context,
    contracted_price_cents,
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session, "Jurídico sem preço válido")
    legal_line = _legal_line(catalog).model_copy(
        update={"contracted_price_cents": contracted_price_cents}
    )

    with pytest.raises(
        CommercialCatalogValidationError,
        match="preço contratado estritamente positivo",
    ):
        await service.preview_contract(
            WorkspaceContractPreviewRequest(
                workspace_id=workspace.id,
                lines=[legal_line],
            )
        )


@pytest.mark.asyncio
async def test_courtesy_is_a_finite_date_interval_instead_of_untracked_cycles(
    db_session,
    platform_auth_context,
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session, "Cortesia por intervalo")
    contract, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start",
                    policy_version_id=catalog["crm_start"].id,
                )
            ],
            reason="Contrato para validar cortesia finita.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="courtesy-interval-contract",
    )
    now = datetime.now(UTC)
    courtesy, _ = await service.add_adjustment(
        contract_id=contract.id,
        payload=CommercialAdjustmentCreateRequest(
            type=CommercialAdjustmentType.courtesy_period,
            value=1,
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=2),
            stack_group="courtesy-window",
            reason="Cortesia integral com intervalo explícito.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="courtesy-interval-adjustment",
    )

    assert (await service.get_contract(contract.id)).price.recurring_price_cents == 26_900

    courtesy_model = await db_session.get(CommercialAdjustment, courtesy.id)
    assert courtesy_model is not None
    courtesy_model.starts_at = now - timedelta(minutes=1)
    courtesy_model.ends_at = now + timedelta(hours=1)
    courtesy_model.status = CommercialRecordStatus.active
    await db_session.flush()
    assert (await service.get_contract(contract.id)).price.recurring_price_cents == 0

    courtesy_model.ends_at = now - timedelta(seconds=1)
    await db_session.flush()
    assert (await service.get_contract(contract.id)).price.recurring_price_cents == 26_900


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("record_status", "value", "remaining_cycles"),
    [
        (CommercialRecordStatus.active, 2, None),
        (CommercialRecordStatus.scheduled, 1, 2),
    ],
)
async def test_contract_revision_blocks_legacy_cycle_based_courtesy(
    db_session,
    platform_auth_context,
    record_status,
    value,
    remaining_cycles,
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session, f"Cortesia legada {record_status.value}")
    contract, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start",
                    policy_version_id=catalog["crm_start"].id,
                )
            ],
            reason="Contrato com exceção histórica controlada.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"legacy-courtesy-contract-{record_status.value}",
    )
    active_line = next(item for item in contract.lines if item.status.value == "active")
    now = datetime.now(UTC)
    starts_at = (
        now - timedelta(minutes=1)
        if record_status is CommercialRecordStatus.active
        else now + timedelta(days=1)
    )
    db_session.add(
        CommercialAdjustment(
            contract_id=contract.id,
            contract_line_id=active_line.id,
            type=CommercialAdjustmentType.courtesy_period,
            value=value,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=30),
            remaining_cycles=remaining_cycles,
            stack_group="legacy-cycle-courtesy",
            stackable=False,
            portability=CommercialPortability.preserve,
            reason="Registro legado anterior à política por intervalo.",
            source=CommercialAdjustmentSource.manual,
            status=record_status,
            version=1,
        )
    )
    await db_session.flush()

    preview = await service.preview_contract_revision(
        contract.id,
        WorkspaceContractRevisionPreviewRequest(
            expected_version=contract.version,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_advanced",
                    policy_version_id=catalog["crm_advanced"].id,
                )
            ],
        ),
    )

    assert preview.eligible is False
    assert any("cortesia histórica baseada em ciclos" in item.lower() for item in preview.blockers)


@pytest.mark.asyncio
async def test_catalog_publication_preserves_contract_price_bonus_and_override_precedence(
    db_session, platform_auth_context
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session)
    contract, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start", policy_version_id=catalog["crm_start"].id
                )
            ],
            reason="Contrato Start aprovado.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="create-start-contract",
    )
    now = datetime.now(UTC)
    adjustment, _ = await service.add_adjustment(
        contract_id=contract.id,
        payload=CommercialAdjustmentCreateRequest(
            type=CommercialAdjustmentType.percentage_discount,
            value=1_000,
            starts_at=now - timedelta(minutes=1),
            stack_group="manual-discount",
            reason="Desconto individual preservado.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="discount-start",
    )
    assert adjustment.value == 1_000
    priced = await service.get_contract(contract.id)
    assert priced.price.contracted_price_cents == 26_900
    assert priced.price.recurring_price_cents == 24_210

    await service.add_bonus(
        workspace_id=workspace.id,
        payload=EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.tokens,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=100_000,
            starts_at=now - timedelta(minutes=1),
            renews_each_period=True,
            reason="Bônus comercial individual.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="bonus-start",
    )
    before_publish = await service.resolve_effective_manifest(workspace.id)
    assert before_publish.capabilities[EntitlementKey.tokens].limit == 600_000

    newer = catalog["crm_start"].snapshot.model_copy(update={"price_monthly_cents": 29_900})
    await service.publish_offer(
        payload=CommercialOfferPublishRequest(
            sku="crm_start",
            family=catalog["crm_start"].family,
            kind=catalog["crm_start"].kind,
            legacy_tier="starter",
            snapshot=newer,
            reason="Novo preço apenas para novas propostas.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="publish-start-v2",
    )
    preserved = await service.get_contract(contract.id)
    assert preserved.lines[0].list_price_cents == 26_900
    assert preserved.price.recurring_price_cents == 24_210
    assert (await service.resolve_effective_manifest(workspace.id)).capabilities[
        EntitlementKey.tokens
    ].limit == 600_000

    db_session.add(
        WorkspaceEntitlementOverride(
            workspace_id=workspace.id,
            entitlement=EntitlementKey.tokens,
            mode=OverrideMode.revoke,
            reason="Revogação de segurança explícita.",
            created_by_user_id=platform_auth_context.user_id,
        )
    )
    await db_session.flush()
    revoked = await service.resolve_effective_manifest(workspace.id)
    token_capability = revoked.capabilities[EntitlementKey.tokens]
    assert token_capability.enabled is False
    assert token_capability.limit == 0
    assert token_capability.denial_reason == "permission_denied"
    assert token_capability.sources[-1].type == "override"


@pytest.mark.asyncio
async def test_offer_draft_never_changes_enforcement_and_can_be_published_or_restored(
    db_session, platform_auth_context
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    original = catalog["crm_start"]
    changed = original.snapshot.model_copy(update={"price_monthly_cents": 31_900})

    draft, replay = await service.save_offer_draft(
        sku="crm_start",
        payload=CommercialOfferDraftSaveRequest(
            expected_published_version=1,
            snapshot=changed,
            reason="Preparar preço para novas propostas.",
        ),
        expected_draft_version=None,
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="draft-start-v1",
    )
    assert replay is False
    assert draft.draft_version == 1
    assert (await service.list_offers())[0]
    still_published = next(
        item for item in (await service.list_offers())[0] if item.sku == "crm_start"
    )
    assert still_published.snapshot.price_monthly_cents == 26_900

    preview = await service.preview_offer_draft("crm_start")
    assert preview.eligible is True
    assert any(item.path == "price_monthly_cents" for item in preview.diff)
    published, _ = await service.publish_offer_draft(
        sku="crm_start",
        expected_draft_version=draft.draft_version,
        payload=CommercialOfferDraftPublishRequest(reason="Publicar preço revisado."),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="publish-draft-start-v1",
    )
    assert published.version == 2
    assert published.snapshot.price_monthly_cents == 31_900
    with pytest.raises(Exception, match="rascunho ativo"):
        await service.get_offer_draft("crm_start")

    history = await service.offer_revision_history("crm_start")
    assert [item.offer.version for item in history.items] == [2, 1]
    restore_preview = await service.preview_offer_restore(
        sku="crm_start",
        target_version=1,
        expected_current_version=2,
    )
    assert restore_preview.eligible is True
    restored, _ = await service.restore_offer(
        sku="crm_start",
        payload=CommercialOfferRestoreRequest(
            target_version=1,
            expected_current_version=2,
            reason="Restaurar política comercial anterior.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="restore-start-v1",
    )
    assert restored.version == 3
    assert restored.snapshot.price_monthly_cents == 26_900


@pytest.mark.asyncio
async def test_contract_restore_creates_new_version_and_preserves_discount_and_bonus(
    db_session, platform_auth_context
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session, "Contrato restaurável")
    contract, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start",
                    policy_version_id=catalog["crm_start"].id,
                )
            ],
            reason="Contrato Start original.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="restore-contract-create",
    )
    now = datetime.now(UTC)
    adjustment, _ = await service.add_adjustment(
        contract_id=contract.id,
        payload=CommercialAdjustmentCreateRequest(
            type=CommercialAdjustmentType.percentage_discount,
            value=1_000,
            starts_at=now - timedelta(minutes=1),
            stack_group="preserved",
            portability=CommercialPortability.expire_on_offer_change,
            reason="Desconto que restauração deve preservar.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="restore-contract-discount",
    )
    bonus, _ = await service.add_bonus(
        workspace_id=workspace.id,
        payload=EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.tokens,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=75_000,
            starts_at=now - timedelta(minutes=1),
            renews_each_period=True,
            portability=CommercialPortability.expire_on_offer_change,
            reason="Bônus que restauração deve preservar.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="restore-contract-bonus",
    )
    revised, _ = await service.revise_contract(
        contract_id=contract.id,
        expected_version=1,
        payload=WorkspaceContractRevisionRequest(
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_advanced",
                    policy_version_id=catalog["crm_advanced"].id,
                )
            ],
            reason="Troca para Advanced antes da restauração.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="restore-contract-revise",
    )
    # A troca normal respeita expire_on_offer_change; reative as exceções para
    # provar que a restauração, diferentemente da troca, nunca as apaga.
    adjustment_model = await db_session.get(CommercialAdjustment, adjustment.id)
    bonus_model = await db_session.get(EntitlementBonus, bonus.id)
    assert adjustment_model is not None and bonus_model is not None
    adjustment_model.status = CommercialRecordStatus.active
    adjustment_model.version += 1
    bonus_model.status = CommercialRecordStatus.active
    bonus_model.version += 1
    await db_session.flush()

    preview = await service.preview_contract_restore(
        contract_id=contract.id,
        target_version=1,
        expected_current_version=revised.version,
    )
    assert preview.revision.eligible is True
    assert preview.preserves_adjustments is True
    assert preview.preserves_bonuses is True
    restored, _ = await service.restore_contract(
        contract_id=contract.id,
        payload=WorkspaceContractRestoreRequest(
            target_version=1,
            expected_current_version=revised.version,
            reason="Restaurar Start sem apagar exceções individuais.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="restore-contract-v1",
    )
    assert restored.version == 3
    assert [line.sku for line in restored.lines if line.status.value == "active"] == ["crm_start"]
    restored_adjustment = await db_session.get(CommercialAdjustment, adjustment.id)
    restored_bonus = await db_session.get(EntitlementBonus, bonus.id)
    assert restored_adjustment is not None
    assert restored_adjustment.status is CommercialRecordStatus.active
    assert restored_bonus is not None
    assert restored_bonus.status is CommercialRecordStatus.active
    assert restored.price.recurring_price_cents == 24_210
    manifest = await service.resolve_effective_manifest(workspace.id)
    assert manifest.capabilities[EntitlementKey.tokens].limit == 575_000


@pytest.mark.asyncio
async def test_restore_preview_never_claims_exception_preservation_when_blocked(
    db_session, platform_auth_context
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session, "Restauração com intervenção")
    original, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start", policy_version_id=catalog["crm_start"].id
                )
            ],
            reason="Versão inicial sem Jurídico.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="restore-blocker-create",
    )
    combined, _ = await service.revise_contract(
        contract_id=original.id,
        expected_version=original.version,
        payload=WorkspaceContractRevisionRequest(
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start", policy_version_id=catalog["crm_start"].id
                ),
                _legal_line(catalog),
            ],
            reason="Adicionar linha jurídica.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="restore-blocker-add-legal",
    )
    legal_line = next(
        line
        for line in combined.lines
        if line.sku == "legal_360" and line.status.value == "active"
    )
    assert legal_line.id is not None
    await service.add_adjustment(
        contract_id=combined.id,
        payload=CommercialAdjustmentCreateRequest(
            contract_line_id=legal_line.id,
            type=CommercialAdjustmentType.fixed_discount,
            value=5_000,
            starts_at=datetime.now(UTC) - timedelta(minutes=1),
            stack_group="legal-negotiated",
            reason="Desconto específico do Jurídico.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="restore-blocker-adjustment",
    )

    preview = await service.preview_contract_restore(
        contract_id=combined.id,
        target_version=original.version,
        expected_current_version=combined.version,
    )
    assert preview.revision.eligible is False
    assert preview.revision.blockers
    assert preview.preserves_adjustments is False
    assert preview.preserves_bonuses is False


@pytest.mark.asyncio
async def test_new_annual_line_fails_closed_but_explicit_legacy_import_is_preserved(
    db_session, platform_auth_context
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session)
    annual = WorkspaceContractLineWrite(
        sku="crm_advanced",
        policy_version_id=catalog["crm_advanced"].id,
        billing_interval=CommercialBillingInterval.annual,
        contracted_price_cents=6_500_00,
    )
    with pytest.raises(CommercialCatalogValidationError, match="anuais"):
        await service.preview_contract(
            WorkspaceContractPreviewRequest(workspace_id=workspace.id, lines=[annual])
        )
    legacy = await service.preview_contract(
        WorkspaceContractPreviewRequest(workspace_id=workspace.id, lines=[annual]),
        allow_legacy_import=True,
    )
    assert legacy.lines[0].billing_interval is CommercialBillingInterval.annual
    assert legacy.warnings

    imported, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[annual],
            reason="Importar ciclo anual legado conferido.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="annual-legacy-contract",
        allow_legacy_import=True,
    )
    current_annual = next(line for line in imported.lines if line.status.value == "active")
    revision_lines = [
        WorkspaceContractLineWrite(
            sku=current_annual.sku,
            policy_version_id=current_annual.policy_version_id,
            quantity=current_annual.quantity,
            contracted_price_cents=current_annual.contracted_price_cents,
            billing_interval=current_annual.billing_interval,
            entitlement_overrides=current_annual.entitlement_overrides,
            effective_until=current_annual.effective_until,
        ),
        _legal_line(catalog),
    ]
    revision_preview = await service.preview_contract_revision(
        imported.id,
        WorkspaceContractRevisionPreviewRequest(
            expected_version=imported.version,
            lines=revision_lines,
        ),
    )
    assert revision_preview.eligible is True
    assert revision_preview.changed_families == [catalog["legal_360"].family]
    revised, _ = await service.revise_contract(
        contract_id=imported.id,
        expected_version=imported.version,
        payload=WorkspaceContractRevisionRequest(
            lines=revision_lines,
            reason="Adicionar Jurídico sem recontratar o ciclo anual legado.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="annual-add-legal",
    )
    preserved = next(
        line
        for line in revised.lines
        if line.sku == "crm_advanced" and line.status.value == "active"
    )
    assert preserved.id == current_annual.id
    assert preserved.policy_version_id == current_annual.policy_version_id
    assert preserved.contracted_price_cents == 650_000
    assert preserved.billing_interval is CommercialBillingInterval.annual

    changed_annual_price = [
        revision_lines[0].model_copy(update={"contracted_price_cents": 640_000}),
        revision_lines[1],
    ]
    with pytest.raises(CommercialCatalogValidationError, match="alterações em linha anual"):
        await service.preview_contract_revision(
            revised.id,
            WorkspaceContractRevisionPreviewRequest(
                expected_version=revised.version,
                lines=changed_annual_price,
            ),
        )


@pytest.mark.asyncio
async def test_annual_core_and_monthly_legal_use_one_monthly_price_basis(
    db_session, platform_auth_context
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session, "Contrato anual com Jurídico mensal")
    annual = WorkspaceContractLineWrite(
        sku="crm_advanced",
        policy_version_id=catalog["crm_advanced"].id,
        billing_interval=CommercialBillingInterval.annual,
        contracted_price_cents=650_000,
    )
    imported, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[annual],
            reason="Importar contrato anual antes do Jurídico.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="mixed-cycle-import",
        allow_legacy_import=True,
    )
    current_annual = next(line for line in imported.lines if line.status.value == "active")
    combined, _ = await service.revise_contract(
        contract_id=imported.id,
        expected_version=imported.version,
        payload=WorkspaceContractRevisionRequest(
            lines=[
                WorkspaceContractLineWrite(
                    sku=current_annual.sku,
                    policy_version_id=current_annual.policy_version_id,
                    quantity=current_annual.quantity,
                    contracted_price_cents=current_annual.contracted_price_cents,
                    billing_interval=current_annual.billing_interval,
                    entitlement_overrides=current_annual.entitlement_overrides,
                    effective_until=current_annual.effective_until,
                ),
                _legal_line(catalog),
            ],
            reason="Adicionar Jurídico mensal ao Core anual legado.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="mixed-cycle-add-legal",
    )
    assert combined.price.contracted_price_cents == 144_067
    assert combined.price.recurring_price_cents == 144_067

    starts_at = datetime.now(UTC) - timedelta(minutes=1)
    await service.add_adjustment(
        contract_id=combined.id,
        payload=CommercialAdjustmentCreateRequest(
            type=CommercialAdjustmentType.percentage_discount,
            value=1_000,
            starts_at=starts_at,
            stack_group="mixed-cycle-discount",
            reason="Aplicar dez por cento ao valor mensal equivalente.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="mixed-cycle-percentage",
    )
    await service.add_adjustment(
        contract_id=combined.id,
        payload=CommercialAdjustmentCreateRequest(
            type=CommercialAdjustmentType.invoice_credit,
            value=1_000,
            starts_at=starts_at,
            stack_group="mixed-cycle-credit",
            reason="Aplicar crédito pontual à cobrança mensal.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="mixed-cycle-credit",
    )

    priced = await service.get_contract(combined.id)
    assert priced.price.list_price_cents == 144_067
    assert priced.price.contracted_price_cents == 144_067
    assert priced.price.recurring_price_cents == 129_661
    assert priced.price.invoice_credit_cents == 1_000
    assert priced.price.amount_due_cents == 128_661
    subscription = await LegacyCommercialProjection(db_session).project_contract(
        priced,
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="mixed-cycle-legacy-projection",
    )
    local = await db_session.scalar(
        select(WorkspaceSubscription).where(
            WorkspaceSubscription.workspace_id == workspace.id
        )
    )
    assert subscription is not None and local is not None
    assert subscription.interval is BillingInterval.monthly
    assert subscription.amount_cents == 129_661
    assert local.ciclo == "mensal"
    assert local.valor_mensal_cents == 129_661


@pytest.mark.asyncio
async def test_canonical_workspace_summary_supports_legal_only_and_pinned_policy_plus_bonus(
    db_session, platform_auth_context
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    legal_workspace = await _workspace(db_session, "Resumo somente jurídico")
    await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=legal_workspace.id,
            lines=[_legal_line(catalog)],
            reason="Jurídico standalone no resumo da conta.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="summary-legal-only",
    )
    legal_summary = await WorkspaceService(db_session)._build_plan_summary(legal_workspace)
    assert legal_summary is not None
    assert legal_summary.tier == "legal_360"
    assert legal_summary.name == "Jurídico 360"
    assert legal_summary.max_users == 2
    assert legal_summary.max_legal_areas == 3
    assert legal_summary.max_monitored_processes == 0

    start_workspace = await _workspace(db_session, "Resumo canônico fixado")
    await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=start_workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start",
                    policy_version_id=catalog["crm_start"].id,
                )
            ],
            reason="Fixar política Start versão 1.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="summary-pinned-start",
    )
    await service.add_bonus(
        workspace_id=start_workspace.id,
        payload=EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.users,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=2,
            starts_at=datetime.now(UTC) - timedelta(minutes=1),
            renews_each_period=True,
            reason="Duas licenças bônus preservadas.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="summary-users-bonus",
    )
    new_entitlements = dict(catalog["crm_start"].snapshot.entitlements)
    new_entitlements[EntitlementKey.users] = new_entitlements[
        EntitlementKey.users
    ].model_copy(update={"limit_quantity": 99})
    draft, _ = await service.save_offer_draft(
        sku="crm_start",
        payload=CommercialOfferDraftSaveRequest(
            expected_published_version=1,
            snapshot=catalog["crm_start"].snapshot.model_copy(
                update={"name": "Start para novas contas", "entitlements": new_entitlements}
            ),
            reason="Nova política apenas para novas propostas.",
        ),
        expected_draft_version=None,
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="summary-new-start-draft",
    )
    await service.publish_offer_draft(
        sku="crm_start",
        expected_draft_version=draft.draft_version,
        payload=CommercialOfferDraftPublishRequest(
            reason="Publicar sem reescrever contratos fixados."
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="summary-new-start-publish",
    )

    pinned_summary = await WorkspaceService(db_session)._build_plan_summary(start_workspace)
    assert pinned_summary is not None
    assert pinned_summary.name == "Start"
    assert pinned_summary.max_users == 5


def test_unimplemented_commercial_adjustment_semantics_fail_closed() -> None:
    base = {
        "type": CommercialAdjustmentType.percentage_discount,
        "value": 500,
        "starts_at": datetime.now(UTC),
        "stack_group": "test",
        "reason": "Regra incompleta deve falhar fechada.",
    }
    with pytest.raises(ValidationError, match="consumo idempotente por ciclo"):
        CommercialAdjustmentCreateRequest(**base, remaining_cycles=2)
    with pytest.raises(ValidationError, match="regra determinística"):
        CommercialAdjustmentCreateRequest(
            **base,
            portability=CommercialPortability.recalculate,
        )
    with pytest.raises(ValidationError, match="regra determinística"):
        EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.users,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=1,
            starts_at=datetime.now(UTC),
            renews_each_period=True,
            portability=CommercialPortability.recalculate,
            reason="Recálculo ainda não implementado.",
        )
    with pytest.raises(ValidationError, match="ledger de consumo"):
        EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.users,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=1,
            starts_at=datetime.now(UTC),
            renews_each_period=False,
            reason="Bônus pontual sem vencimento deve falhar.",
        )


@pytest.mark.asyncio
async def test_canonical_contract_drives_existing_entitlement_resolver_and_idempotency_conflicts(
    db_session, platform_auth_context
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session)
    payload = WorkspaceContractCreateRequest(
        workspace_id=workspace.id,
        lines=[
            WorkspaceContractLineWrite(sku="crm_start", policy_version_id=catalog["crm_start"].id)
        ],
        reason="Contrato canônico para enforcement.",
    )
    contract, replay = await service.create_contract(
        payload=payload,
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="canonical-enforcement",
    )
    assert replay is False
    replayed, replay = await service.create_contract(
        payload=payload,
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="canonical-enforcement",
    )
    assert replay is True
    assert replayed.id == contract.id

    changed = payload.model_copy(update={"reason": "Payload diferente na mesma chave."})
    with pytest.raises(CommercialCatalogConflictError, match="idempotência"):
        await service.create_contract(
            payload=changed,
            actor_user_id=platform_auth_context.user_id,
            idempotency_key="canonical-enforcement",
        )

    resolved = await EntitlementResolver(db_session).resolve(workspace.id, EntitlementKey.tokens)
    assert resolved.limit_quantity == 500_000
    assert resolved.source is EntitlementLimitSource.commercial_contract


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entitlement",
    [
        EntitlementKey.tokens,
        EntitlementKey.ai_executions,
        EntitlementKey.storage_bytes,
        EntitlementKey.active_tasks,
        EntitlementKey.campaign_recipients,
    ],
)
async def test_contract_quantitative_override_requires_finite_quota(
    db_session,
    platform_auth_context,
    entitlement,
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session, "Capacidade sem infinito implícito")
    start = catalog["crm_start"]
    invalid_setting = start.snapshot.entitlements[entitlement].model_copy(
        update={"enabled": True, "limit_quantity": None}
    )

    with pytest.raises(
        CommercialCatalogValidationError,
        match=rf"{entitlement.value} exige quota finita",
    ):
        await service.preview_contract(
            WorkspaceContractPreviewRequest(
                workspace_id=workspace.id,
                lines=[
                    WorkspaceContractLineWrite(
                        sku=start.sku,
                        policy_version_id=start.id,
                        entitlement_overrides={
                            entitlement: invalid_setting
                        },
                    )
                ],
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entitlement",
    [
        EntitlementKey.tokens,
        EntitlementKey.ai_executions,
        EntitlementKey.storage_bytes,
        EntitlementKey.active_tasks,
        EntitlementKey.campaign_recipients,
    ],
)
async def test_manifest_denies_historical_quantitative_grant_without_quantity(
    db_session,
    platform_auth_context,
    entitlement,
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session, f"Override histórico {entitlement.value}")
    await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start",
                    policy_version_id=catalog["crm_start"].id,
                )
            ],
            reason="Contrato para testar defesa histórica.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"historical-quantitative-{entitlement.value}",
    )
    db_session.add(
        WorkspaceEntitlementOverride(
            workspace_id=workspace.id,
            entitlement=entitlement,
            mode=OverrideMode.grant,
            quantity=None,
            reason="Registro histórico inválido preservado apenas para leitura defensiva.",
            created_by_user_id=platform_auth_context.user_id,
        )
    )
    await db_session.flush()

    capability = (await service.resolve_effective_manifest(workspace.id)).capabilities[
        entitlement
    ]
    assert capability.enabled is False
    assert capability.permitted is False
    assert capability.limit is None
    assert capability.denial_reason == "not_configured"

    resolved = await EntitlementResolver(db_session).resolve(workspace.id, entitlement)
    assert resolved.enabled is False
    assert resolved.limit_quantity is None


@pytest.mark.asyncio
async def test_manifest_keeps_boolean_grant_without_quantity(
    db_session,
    platform_auth_context,
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session, "Grant booleano canônico")
    await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start",
                    policy_version_id=catalog["crm_start"].id,
                )
            ],
            reason="Contrato para provar feature sem quota.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="manifest-boolean-grant-contract",
    )
    db_session.add(
        WorkspaceEntitlementOverride(
            workspace_id=workspace.id,
            entitlement=EntitlementKey.calendar,
            mode=OverrideMode.grant,
            quantity=None,
            reason="Feature booleano não possui quantity.",
            created_by_user_id=platform_auth_context.user_id,
        )
    )
    await db_session.flush()

    capability = (await service.resolve_effective_manifest(workspace.id)).capabilities[
        EntitlementKey.calendar
    ]
    assert capability.enabled is True
    assert capability.permitted is True
    assert capability.limit is None
    assert capability.denial_reason is None


@pytest.mark.asyncio
async def test_paused_contract_fails_closed_and_bonus_extends_the_finite_grace_boundary(
    db_session, platform_auth_context
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    paused_workspace = await _workspace(db_session, "Contrato pausado")
    paused, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=paused_workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start", policy_version_id=catalog["crm_start"].id
                )
            ],
            reason="Contrato que será pausado no teste.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="paused-contract",
    )
    paused_model = await db_session.get(WorkspaceCommercialContract, paused.id)
    assert paused_model is not None
    paused_model.status = CommercialContractStatus.paused
    paused_model.version += 1
    await db_session.flush()

    disabled = await EntitlementResolver(db_session).resolve(
        paused_workspace.id, EntitlementKey.tokens
    )
    assert disabled.enabled is False
    with pytest.raises(EntitlementDisabledError):
        await EntitlementEnforcementService(db_session).reserve_metered(
            workspace_id=paused_workspace.id,
            entitlement=EntitlementKey.tokens,
            quantity=1,
            idempotency_key="paused-token-use",
        )

    grace_entitlements = dict(catalog["crm_start"].snapshot.entitlements)
    grace_entitlements[EntitlementKey.tokens] = grace_entitlements[
        EntitlementKey.tokens
    ].model_copy(
        update={
            "enforcement": CommercialEntitlementEnforcement.grace,
            "grace_quantity": 100_000,
        }
    )
    grace_offer, _ = await service.publish_offer(
        payload=CommercialOfferPublishRequest(
            sku="crm_start",
            family=catalog["crm_start"].family,
            kind=catalog["crm_start"].kind,
            legacy_tier="starter",
            snapshot=catalog["crm_start"].snapshot.model_copy(
                update={"entitlements": grace_entitlements}
            ),
            reason="Tolerância finita para validar o limite final.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="publish-start-grace",
    )
    grace_workspace = await _workspace(db_session, "Contrato com tolerância")
    await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=grace_workspace.id,
            lines=[WorkspaceContractLineWrite(sku="crm_start", policy_version_id=grace_offer.id)],
            reason="Contrato com limite e tolerância finitos.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="create-start-grace",
    )
    bonus, _ = await service.add_bonus(
        workspace_id=grace_workspace.id,
        payload=EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.tokens,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=50_000,
            starts_at=datetime.now(UTC) - timedelta(minutes=1),
            renews_each_period=True,
            reason="Bônus que também amplia a tolerância executável.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="start-grace-bonus",
    )
    capability = (await service.resolve_effective_manifest(grace_workspace.id)).capabilities[
        EntitlementKey.tokens
    ]
    assert capability.limit == 550_000
    assert capability.grace_limit == 650_000
    assert [(source.type, source.contribution) for source in capability.sources] == [
        ("plan", 500_000),
        ("bonus", 50_000),
    ]
    assert capability.sources[1].id == bonus.id

    resolved = await EntitlementResolver(db_session).resolve(
        grace_workspace.id, EntitlementKey.tokens
    )
    assert resolved.limit_quantity == 650_000
    assert resolved.hard_limit is True
    assert resolved.source is EntitlementLimitSource.commercial_bonus
    reservation = await EntitlementEnforcementService(db_session).reserve_metered(
        workspace_id=grace_workspace.id,
        entitlement=EntitlementKey.tokens,
        quantity=600_001,
        idempotency_key="inside-composed-grace-boundary",
    )
    assert reservation is not None
    with pytest.raises(MonthlyQuotaExceededError) as error:
        await EntitlementEnforcementService(db_session).reserve_metered(
            workspace_id=grace_workspace.id,
            entitlement=EntitlementKey.tokens,
            quantity=50_000,
            idempotency_key="over-grace-boundary",
        )
    assert error.value.limit_quantity == 650_000

    db_session.add(
        WorkspaceEntitlementOverride(
            workspace_id=grace_workspace.id,
            entitlement=EntitlementKey.tokens,
            mode=OverrideMode.limit,
            quantity=575_000,
            reason="Override absoluto não herda bônus nem tolerância.",
            created_by_user_id=platform_auth_context.user_id,
        )
    )
    await db_session.flush()
    overridden = (await service.resolve_effective_manifest(grace_workspace.id)).capabilities[
        EntitlementKey.tokens
    ]
    assert overridden.limit == 575_000
    assert overridden.grace_limit is None
    assert overridden.sources[-1].type == "override"
    assert overridden.sources[-1].contribution == 575_000
    override_resolution = await EntitlementResolver(db_session).resolve(
        grace_workspace.id, EntitlementKey.tokens
    )
    assert override_resolution.limit_quantity == 575_000
    assert override_resolution.source is EntitlementLimitSource.workspace_override


@pytest.mark.asyncio
async def test_maximum_composition_keeps_each_lines_grace_with_its_own_limit(
    db_session, platform_auth_context
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session, "Máximo com tolerâncias independentes")
    start_users = catalog["crm_start"].snapshot.entitlements[
        EntitlementKey.users
    ].model_copy(
        update={
            "limit_quantity": 3,
            "enforcement": CommercialEntitlementEnforcement.grace,
            "grace_quantity": 5,
        }
    )
    legal_users = catalog["legal_360"].snapshot.entitlements[
        EntitlementKey.users
    ].model_copy(
        update={
            "enabled": True,
            "limit_quantity": 10,
            "enforcement": CommercialEntitlementEnforcement.grace,
            "grace_quantity": 0,
        }
    )
    legal_line = _legal_line(catalog).model_copy(
        update={
            "entitlement_overrides": {
                **_legal_line(catalog).entitlement_overrides,
                EntitlementKey.users: legal_users,
            }
        }
    )
    await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start",
                    policy_version_id=catalog["crm_start"].id,
                    entitlement_overrides={EntitlementKey.users: start_users},
                ),
                legal_line,
            ],
            reason="Compor limites máximos sem misturar tolerâncias.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="maximum-grace-isolation",
    )

    capability = (await service.resolve_effective_manifest(workspace.id)).capabilities[
        EntitlementKey.users
    ]
    assert capability.limit == 10
    assert capability.grace_limit == 10


@pytest.mark.asyncio
async def test_scheduled_adjustment_and_bonus_activate_from_their_effective_window(
    db_session, platform_auth_context
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session, "Agendamentos comerciais")
    contract, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start", policy_version_id=catalog["crm_start"].id
                )
            ],
            reason="Contrato para testar ativação temporal.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="scheduled-contract",
    )
    now = datetime.now(UTC)
    adjustment, _ = await service.add_adjustment(
        contract_id=contract.id,
        payload=CommercialAdjustmentCreateRequest(
            type=CommercialAdjustmentType.percentage_discount,
            value=1_000,
            starts_at=now + timedelta(days=1),
            stack_group="scheduled-discount",
            reason="Desconto agendado para ativação automática.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="scheduled-adjustment",
    )
    bonus, _ = await service.add_bonus(
        workspace_id=workspace.id,
        payload=EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.tokens,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=50_000,
            starts_at=now + timedelta(days=1),
            renews_each_period=True,
            reason="Bônus agendado para ativação automática.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="scheduled-bonus",
    )
    assert adjustment.status is CommercialRecordStatus.scheduled
    assert bonus.status is CommercialRecordStatus.scheduled
    adjustment_model = await db_session.get(CommercialAdjustment, adjustment.id)
    bonus_model = await db_session.get(EntitlementBonus, bonus.id)
    assert adjustment_model is not None and bonus_model is not None
    adjustment_model.starts_at = now - timedelta(minutes=1)
    bonus_model.starts_at = now - timedelta(minutes=1)
    await db_session.flush()

    priced = await service.get_contract(contract.id)
    manifest = await service.resolve_effective_manifest(workspace.id)
    assert priced.price.recurring_price_cents == 24_210
    assert manifest.capabilities[EntitlementKey.tokens].limit == 550_000


def test_bonus_modes_and_courtesy_period_are_type_safe_and_finite() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="scheduler comercial"):
        WorkspaceContractLineWrite(
            sku="crm_start",
            effective_from=now + timedelta(minutes=5),
        )
    with pytest.raises(ValidationError, match="vigência já encerrada"):
        WorkspaceContractLineWrite(
            sku="crm_start",
            effective_from=now - timedelta(days=2),
            effective_until=now - timedelta(days=1),
        )
    with pytest.raises(ValidationError, match="booleano"):
        EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.tokens,
            mode=EntitlementBonusMode.feature_grant,
            starts_at=now,
            renews_each_period=True,
            reason="Não pode tornar token ilimitado.",
        )
    with pytest.raises(ValidationError, match="quantitativo"):
        EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.calendar,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=1,
            starts_at=now,
            renews_each_period=True,
            reason="Feature não aceita quota numérica.",
        )
    with pytest.raises(ValidationError, match="endsAt"):
        CommercialAdjustmentCreateRequest(
            type=CommercialAdjustmentType.courtesy_period,
            value=1,
            starts_at=now,
            stack_group="courtesy",
            reason="Cortesia precisa ter fim explícito.",
        )
    with pytest.raises(ValidationError, match="marcador 1"):
        CommercialAdjustmentCreateRequest(
            type=CommercialAdjustmentType.courtesy_period,
            value=2,
            starts_at=now,
            ends_at=now + timedelta(days=30),
            stack_group="courtesy",
            reason="Ciclos sem ledger não podem ser prometidos.",
        )
    with pytest.raises(ValidationError, match="vigência ainda não encerrada"):
        CommercialAdjustmentCreateRequest(
            type=CommercialAdjustmentType.fixed_discount,
            value=1_000,
            starts_at=now - timedelta(days=2),
            ends_at=now - timedelta(days=1),
            stack_group="expired-adjustment",
            reason="Janela encerrada não pode ser criada.",
        )
    with pytest.raises(ValidationError, match="vigência ainda não encerrada"):
        EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.tokens,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=10_000,
            starts_at=now - timedelta(days=2),
            ends_at=now - timedelta(days=1),
            reason="Bônus encerrado não pode ser criado.",
        )


@pytest.mark.asyncio
async def test_non_stackable_negotiated_price_cannot_be_bypassed_by_a_stackable_adjustment(
    db_session, platform_auth_context
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session, "Contrato com preço negociado")
    contract, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start", policy_version_id=catalog["crm_start"].id
                )
            ],
            reason="Contrato para validar regras de acumulação.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="negotiated-contract",
    )
    now = datetime.now(UTC)
    await service.add_adjustment(
        contract_id=contract.id,
        payload=CommercialAdjustmentCreateRequest(
            type=CommercialAdjustmentType.negotiated_price,
            value=20_000,
            starts_at=now - timedelta(minutes=1),
            stack_group="negotiated",
            stackable=False,
            reason="Preço negociado exclusivo.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="negotiated-price",
    )
    with pytest.raises(CommercialCatalogConflictError, match="exclusivo"):
        await service.add_adjustment(
            contract_id=contract.id,
            payload=CommercialAdjustmentCreateRequest(
                type=CommercialAdjustmentType.fixed_discount,
                value=1_000,
                starts_at=now,
                stack_group="other-group",
                stackable=True,
                reason="Não pode ignorar a regra do ajuste existente.",
            ),
            actor_user_id=platform_auth_context.user_id,
            idempotency_key="stackable-cannot-bypass-existing",
        )


@pytest.mark.asyncio
async def test_long_idempotency_key_fits_event_and_replays_without_truncation(
    db_session, platform_auth_context
) -> None:
    service = CommercialCatalogService(db_session)
    key = "i" * 200
    first, replay = await service.bootstrap_default_catalog(
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=key,
    )
    second, replayed = await service.bootstrap_default_catalog(
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=key,
    )
    assert replay is False
    assert replayed is True
    assert [item.id for item in first] == [item.id for item in second]
    assert CommercialCommandReceipt.__table__.c.idempotency_key.type.length == 200
    assert CommercialCatalogEvent.__table__.c.correlation_id.type.length == 200
    event = await db_session.scalar(
        select(CommercialCatalogEvent).where(CommercialCatalogEvent.correlation_id == key)
    )
    assert event is not None
    assert event.correlation_id == key


@pytest.mark.asyncio
async def test_uncontracted_workspace_bypasses_only_in_isolated_local_runtime(
    db_session, monkeypatch
) -> None:
    workspace = await _workspace(db_session, "Sem contrato")
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", True)
    with pytest.raises(EntitlementNotConfiguredError, match="sem assinatura"):
        await EntitlementEnforcementService(db_session).enforce_feature(
            workspace_id=workspace.id,
            entitlement=EntitlementKey.calendar,
        )


@pytest.mark.asyncio
async def test_offer_dimensions_and_legal_boundaries_fail_closed(
    db_session, platform_auth_context
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    invalid_dimensions = dict(catalog["crm_start"].snapshot.entitlements)
    invalid_dimensions[EntitlementKey.users] = invalid_dimensions[EntitlementKey.users].model_copy(
        update={"unit": "invented_unit"}
    )
    with pytest.raises(CommercialCatalogValidationError, match="não canônicos"):
        await service.publish_offer(
            payload=CommercialOfferPublishRequest(
                sku="crm_start",
                family=catalog["crm_start"].family,
                kind=catalog["crm_start"].kind,
                legacy_tier="starter",
                snapshot=catalog["crm_start"].snapshot.model_copy(
                    update={"entitlements": invalid_dimensions}
                ),
                reason="Tentativa inválida de mudar a unidade técnica.",
            ),
            actor_user_id=platform_auth_context.user_id,
            idempotency_key="invalid-canonical-unit",
        )

    invalid_quantitative = dict(catalog["crm_start"].snapshot.entitlements)
    invalid_quantitative[EntitlementKey.storage_bytes] = invalid_quantitative[
        EntitlementKey.storage_bytes
    ].model_copy(update={"enabled": True, "limit_quantity": None})
    with pytest.raises(
        CommercialCatalogValidationError,
        match="storage_bytes exige quota finita",
    ):
        await service.publish_offer(
            payload=CommercialOfferPublishRequest(
                sku="crm_start",
                family=catalog["crm_start"].family,
                kind=catalog["crm_start"].kind,
                legacy_tier="starter",
                snapshot=catalog["crm_start"].snapshot.model_copy(
                    update={"entitlements": invalid_quantitative}
                ),
                reason="Não publicar capacidade quantitativa sem limite.",
            ),
            actor_user_id=platform_auth_context.user_id,
            idempotency_key="invalid-canonical-quantitative",
        )

    unsupported = dict(catalog["legal_360"].snapshot.entitlements)
    unsupported[EntitlementKey.legal_cases] = unsupported[EntitlementKey.legal_cases].model_copy(
        update={"enabled": True, "limit_quantity": 100}
    )
    with pytest.raises(CommercialCatalogValidationError, match="sem enforcement"):
        await service.publish_offer(
            payload=CommercialOfferPublishRequest(
                sku="legal_360",
                family=catalog["legal_360"].family,
                kind=catalog["legal_360"].kind,
                snapshot=catalog["legal_360"].snapshot.model_copy(
                    update={"entitlements": unsupported}
                ),
                reason="Não publicar limite jurídico sem consumidor.",
            ),
            actor_user_id=platform_auth_context.user_id,
            idempotency_key="invalid-legal-boundary",
        )

    workspace = await _workspace(db_session, "Bônus jurídico bloqueado")
    with pytest.raises(CommercialCatalogValidationError, match="não aceita bônus"):
        await service.add_bonus(
            workspace_id=workspace.id,
            payload=EntitlementBonusCreateRequest(
                entitlement=EntitlementKey.legal_cases,
                mode=EntitlementBonusMode.quantity_delta,
                quantity=1,
                starts_at=datetime.now(UTC),
                renews_each_period=True,
                reason="Não permitir promessa sem enforcement.",
            ),
            actor_user_id=platform_auth_context.user_id,
            idempotency_key="invalid-legal-bonus",
        )


@pytest.mark.asyncio
async def test_contract_revision_replaces_core_removes_legal_and_retargets_exceptions(
    db_session, platform_auth_context
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session, "Revisão canônica")
    contract, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start", policy_version_id=catalog["crm_start"].id
                ),
                _legal_line(catalog),
            ],
            reason="Contrato inicial para revisar.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="revision-initial-contract",
    )
    start_line = next(line for line in contract.lines if line.sku == "crm_start")
    now = datetime.now(UTC)
    adjustment, _ = await service.add_adjustment(
        contract_id=contract.id,
        payload=CommercialAdjustmentCreateRequest(
            contract_line_id=start_line.id,
            type=CommercialAdjustmentType.percentage_discount,
            value=1_000,
            starts_at=now - timedelta(minutes=1),
            stack_group="portable",
            portability=CommercialPortability.preserve,
            reason="Desconto acompanha o Core.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="revision-portable-adjustment",
    )
    expiring_bonus, _ = await service.add_bonus(
        workspace_id=workspace.id,
        payload=EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.tokens,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=10_000,
            starts_at=now - timedelta(minutes=1),
            renews_each_period=True,
            portability=CommercialPortability.expire_on_offer_change,
            reason="Bônus válido apenas no plano anterior.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="revision-expiring-bonus",
    )
    target_lines = [
        WorkspaceContractLineWrite(sku="crm_advanced", policy_version_id=catalog["crm_advanced"].id)
    ]
    preview = await service.preview_contract_revision(
        contract.id,
        WorkspaceContractRevisionPreviewRequest(
            expected_version=contract.version,
            lines=target_lines,
        ),
    )
    assert preview.eligible is True
    assert {item.value for item in preview.changed_families} == {"crm", "legal"}
    assert preview.price.recurring_price_cents == 61_110
    assert preview.manifest.capabilities[EntitlementKey.tokens].limit == 2_000_000

    revised, _ = await service.revise_contract(
        contract_id=contract.id,
        expected_version=contract.version,
        payload=WorkspaceContractRevisionRequest(
            lines=target_lines,
            reason="Upgrade para Advanced e retirada do Jurídico.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="revision-apply",
    )
    assert revised.version == 2
    assert [line.sku for line in revised.lines if line.status.value == "active"] == ["crm_advanced"]
    refreshed_adjustment = await db_session.get(CommercialAdjustment, adjustment.id)
    refreshed_bonus = await db_session.get(EntitlementBonus, expiring_bonus.id)
    assert refreshed_adjustment is not None
    assert refreshed_adjustment.contract_line_id != start_line.id
    assert refreshed_adjustment.version == 2
    assert refreshed_bonus is not None
    assert refreshed_bonus.status is CommercialRecordStatus.revoked
    events = list(
        (
            await db_session.scalars(
                select(CommercialCatalogEvent).where(
                    CommercialCatalogEvent.correlation_id == "revision-apply"
                )
            )
        ).all()
    )
    by_type = {event.event_type: event for event in events}
    assert set(by_type) == {
        "commercial_adjustment_auto_rebound",
        "entitlement_bonus_auto_revoked",
        "contract_revised",
    }
    assert all(event.before and event.after for event in events)
    assert all(event.reason == "Upgrade para Advanced e retirada do Jurídico." for event in events)
    assert by_type["commercial_adjustment_auto_rebound"].before["contract_line_id"] == str(
        start_line.id
    )
    assert (
        by_type["commercial_adjustment_auto_rebound"].after["contract_line_id"]
        != str(start_line.id)
    )
    assert by_type["entitlement_bonus_auto_revoked"].before["status"] == "active"
    assert by_type["entitlement_bonus_auto_revoked"].after["status"] == "revoked"
    assert by_type["contract_revised"].before["version"] == 1
    assert by_type["contract_revised"].after["version"] == 2

    historical_line = await db_session.get(WorkspaceContractLine, start_line.id)
    assert historical_line is not None
    assert historical_line.status.value == "cancelled"
    with pytest.raises(CommercialCatalogValidationError, match="ativa e vigente"):
        await service.add_adjustment(
            contract_id=contract.id,
            payload=CommercialAdjustmentCreateRequest(
                contract_line_id=start_line.id,
                type=CommercialAdjustmentType.fixed_discount,
                value=500,
                starts_at=now,
                ends_at=now + timedelta(days=30),
                stack_group="historical-line",
                reason="Não vincular à linha histórica.",
            ),
            actor_user_id=platform_auth_context.user_id,
            idempotency_key="historical-line-adjustment",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scheduled", "expected_status"),
    [
        (False, CommercialRecordStatus.active),
        (True, CommercialRecordStatus.scheduled),
    ],
    ids=["active", "scheduled"],
)
async def test_contract_revision_applies_portability_to_active_and_scheduled_records(
    db_session,
    platform_auth_context,
    scheduled,
    expected_status,
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(
        db_session,
        f"Portabilidade {expected_status.value}",
    )
    contract, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start",
                    policy_version_id=catalog["crm_start"].id,
                )
            ],
            reason="Contrato para provar portabilidade temporal.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"portability-contract-{expected_status.value}",
    )
    start_line = next(line for line in contract.lines if line.sku == "crm_start")
    starts_at = datetime.now(UTC) + (
        timedelta(days=1) if scheduled else -timedelta(minutes=1)
    )

    preserve_adjustment, _ = await service.add_adjustment(
        contract_id=contract.id,
        payload=CommercialAdjustmentCreateRequest(
            contract_line_id=start_line.id,
            type=CommercialAdjustmentType.fixed_discount,
            value=100,
            starts_at=starts_at,
            stack_group=f"preserve-{expected_status.value}",
            portability=CommercialPortability.preserve,
            reason="Ajuste preservado deve apenas acompanhar a linha.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"portability-preserve-adjustment-{expected_status.value}",
    )
    expire_adjustment, _ = await service.add_adjustment(
        contract_id=contract.id,
        payload=CommercialAdjustmentCreateRequest(
            contract_line_id=start_line.id,
            type=CommercialAdjustmentType.fixed_discount,
            value=200,
            starts_at=starts_at,
            stack_group=f"expire-{expected_status.value}",
            portability=CommercialPortability.expire_on_offer_change,
            reason="Ajuste deve expirar quando a oferta mudar.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"portability-expire-adjustment-{expected_status.value}",
    )
    preserve_bonus, _ = await service.add_bonus(
        workspace_id=workspace.id,
        payload=EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.tokens,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=1_000,
            starts_at=starts_at,
            renews_each_period=True,
            portability=CommercialPortability.preserve,
            reason="Bônus preservado nunca será revogado automaticamente.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"portability-preserve-bonus-{expected_status.value}",
    )
    expire_bonus, _ = await service.add_bonus(
        workspace_id=workspace.id,
        payload=EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.tokens,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=2_000,
            starts_at=starts_at,
            renews_each_period=True,
            portability=CommercialPortability.expire_on_offer_change,
            reason="Bônus deve expirar quando a oferta mudar.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"portability-expire-bonus-{expected_status.value}",
    )
    assert preserve_adjustment.status is expected_status
    assert expire_adjustment.status is expected_status
    assert preserve_bonus.status is expected_status
    assert expire_bonus.status is expected_status

    revised, _ = await service.revise_contract(
        contract_id=contract.id,
        expected_version=contract.version,
        payload=WorkspaceContractRevisionRequest(
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_advanced",
                    policy_version_id=catalog["crm_advanced"].id,
                )
            ],
            reason="Trocar oferta e aplicar portabilidade declarada.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"portability-revise-{expected_status.value}",
    )
    advanced_line = next(line for line in revised.lines if line.sku == "crm_advanced")

    preserve_adjustment_model = await db_session.get(
        CommercialAdjustment,
        preserve_adjustment.id,
    )
    expire_adjustment_model = await db_session.get(
        CommercialAdjustment,
        expire_adjustment.id,
    )
    preserve_bonus_model = await db_session.get(EntitlementBonus, preserve_bonus.id)
    expire_bonus_model = await db_session.get(EntitlementBonus, expire_bonus.id)
    assert preserve_adjustment_model is not None
    assert preserve_adjustment_model.status is expected_status
    assert preserve_adjustment_model.contract_line_id == advanced_line.id
    assert preserve_adjustment_model.version == 2
    assert expire_adjustment_model is not None
    assert expire_adjustment_model.status is CommercialRecordStatus.revoked
    assert expire_adjustment_model.contract_line_id == start_line.id
    assert expire_adjustment_model.version == 2
    assert preserve_bonus_model is not None
    assert preserve_bonus_model.status is expected_status
    assert preserve_bonus_model.version == 1
    assert expire_bonus_model is not None
    assert expire_bonus_model.status is CommercialRecordStatus.revoked
    assert expire_bonus_model.version == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scheduled", "expected_status"),
    [
        (False, CommercialRecordStatus.active),
        (True, CommercialRecordStatus.scheduled),
    ],
    ids=["active", "scheduled"],
)
async def test_contract_restore_uses_explicit_context_and_preserves_current_exceptions(
    db_session,
    platform_auth_context,
    scheduled,
    expected_status,
) -> None:
    service, catalog = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(
        db_session,
        f"Restauração explícita {expected_status.value}",
    )
    original, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_start",
                    policy_version_id=catalog["crm_start"].id,
                )
            ],
            reason="Contrato original para restauração explícita.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"restore-context-create-{expected_status.value}",
    )
    upgraded, _ = await service.revise_contract(
        contract_id=original.id,
        expected_version=original.version,
        payload=WorkspaceContractRevisionRequest(
            lines=[
                WorkspaceContractLineWrite(
                    sku="crm_advanced",
                    policy_version_id=catalog["crm_advanced"].id,
                )
            ],
            reason="Criar versão atual antes da restauração.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"restore-context-upgrade-{expected_status.value}",
    )
    advanced_line = next(
        line
        for line in upgraded.lines
        if line.sku == "crm_advanced" and line.status.value == "active"
    )
    starts_at = datetime.now(UTC) + (
        timedelta(days=1) if scheduled else -timedelta(minutes=1)
    )

    adjustments = []
    for portability, value in (
        (CommercialPortability.expire_on_offer_change, 100),
        (CommercialPortability.manual_review, 200),
        (CommercialPortability.preserve, 300),
    ):
        adjustment, _ = await service.add_adjustment(
            contract_id=original.id,
            payload=CommercialAdjustmentCreateRequest(
                contract_line_id=advanced_line.id,
                type=CommercialAdjustmentType.fixed_discount,
                value=value,
                starts_at=starts_at,
                stack_group=(
                    f"restore-{portability.value}-{expected_status.value}"
                ),
                portability=portability,
                reason="Exceção atual deve sobreviver à restauração explícita.",
            ),
            actor_user_id=platform_auth_context.user_id,
            idempotency_key=(
                f"restore-context-adjustment-{portability.value}-"
                f"{expected_status.value}"
            ),
        )
        adjustments.append(adjustment)

    # A criação pública de ``recalculate`` é deliberadamente bloqueada até
    # existir fórmula determinística. Este registro simula uma exceção
    # histórica que a restauração precisa preservar, sem recalcular.
    recalculate_adjustment, _ = await service.add_adjustment(
        contract_id=original.id,
        payload=CommercialAdjustmentCreateRequest(
            contract_line_id=advanced_line.id,
            type=CommercialAdjustmentType.fixed_discount,
            value=400,
            starts_at=starts_at,
            stack_group=f"restore-recalculate-{expected_status.value}",
            portability=CommercialPortability.preserve,
            reason="Exceção histórica de recálculo deve ser preservada.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"restore-context-adjustment-recalculate-{expected_status.value}",
    )
    recalculate_adjustment_model = await db_session.get(
        CommercialAdjustment,
        recalculate_adjustment.id,
    )
    assert recalculate_adjustment_model is not None
    recalculate_adjustment_model.portability = CommercialPortability.recalculate

    manual_bonus, _ = await service.add_bonus(
        workspace_id=workspace.id,
        payload=EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.tokens,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=1_000,
            starts_at=starts_at,
            renews_each_period=True,
            portability=CommercialPortability.manual_review,
            reason="Bônus manual atual deve ser preservado na restauração.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"restore-context-bonus-manual-{expected_status.value}",
    )
    recalculate_bonus, _ = await service.add_bonus(
        workspace_id=workspace.id,
        payload=EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.tokens,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=2_000,
            starts_at=starts_at,
            renews_each_period=True,
            portability=CommercialPortability.preserve,
            reason="Bônus histórico de recálculo deve ser preservado.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"restore-context-bonus-recalculate-{expected_status.value}",
    )
    recalculate_bonus_model = await db_session.get(
        EntitlementBonus,
        recalculate_bonus.id,
    )
    assert recalculate_bonus_model is not None
    recalculate_bonus_model.portability = CommercialPortability.recalculate
    await db_session.flush()
    expected_adjustment_portability = {
        item.id: item.portability for item in adjustments
    }
    expected_adjustment_portability[recalculate_adjustment.id] = (
        CommercialPortability.recalculate
    )

    preview = await service.preview_contract_restore(
        contract_id=original.id,
        target_version=1,
        expected_current_version=upgraded.version,
    )
    assert preview.revision.eligible is True
    assert preview.preserves_adjustments is True
    assert preview.preserves_bonuses is True
    assert any(
        "Ajuste que expiraria numa troca normal" in warning
        for warning in preview.revision.warnings
    )
    assert any(
        "Ajuste com revisão manual" in warning
        for warning in preview.revision.warnings
    )
    assert any(
        "Ajuste histórico com recálculo" in warning
        for warning in preview.revision.warnings
    )
    assert any(
        "Bônus com revisão manual" in warning
        for warning in preview.revision.warnings
    )
    assert any(
        "Bônus histórico com recálculo" in warning
        for warning in preview.revision.warnings
    )

    context_events: list[str] = []

    @asynccontextmanager
    async def restore_rebind_context():
        context_events.append("entered")
        yield
        context_events.append("exited")

    restored, _ = await service.restore_contract(
        contract_id=original.id,
        payload=WorkspaceContractRestoreRequest(
            target_version=1,
            expected_current_version=upgraded.version,
            reason="Restaurar Start preservando todas as exceções atuais.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key=f"restore-context-apply-{expected_status.value}",
        restore_portability_context=restore_rebind_context,
    )
    restored_line = next(
        line
        for line in restored.lines
        if line.sku == "crm_start" and line.status.value == "active"
    )
    assert context_events == ["entered", "exited"]

    adjustment_ids = {item.id for item in adjustments} | {
        recalculate_adjustment.id
    }
    for adjustment_id in adjustment_ids:
        model = await db_session.get(CommercialAdjustment, adjustment_id)
        assert model is not None
        assert model.status is expected_status
        assert model.contract_line_id == restored_line.id
        assert model.portability is expected_adjustment_portability[adjustment_id]
        assert model.version == 2

    refreshed_manual_bonus = await db_session.get(EntitlementBonus, manual_bonus.id)
    refreshed_recalculate_bonus = await db_session.get(
        EntitlementBonus,
        recalculate_bonus.id,
    )
    assert refreshed_manual_bonus is not None
    assert refreshed_manual_bonus.status is expected_status
    assert refreshed_manual_bonus.portability is CommercialPortability.manual_review
    assert refreshed_manual_bonus.version == 1
    assert refreshed_recalculate_bonus is not None
    assert refreshed_recalculate_bonus.status is expected_status
    assert (
        refreshed_recalculate_bonus.portability
        is CommercialPortability.recalculate
    )
    assert refreshed_recalculate_bonus.version == 1

    rebound_events = list(
        (
            await db_session.scalars(
                select(CommercialCatalogEvent).where(
                    CommercialCatalogEvent.correlation_id
                    == f"restore-context-apply-{expected_status.value}",
                    CommercialCatalogEvent.event_type
                    == "commercial_adjustment_auto_rebound",
                )
            )
        ).all()
    )
    assert {event.resource_id for event in rebound_events} == adjustment_ids


@pytest.mark.asyncio
async def test_adjustment_and_bonus_update_revoke_are_versioned(
    db_session, platform_auth_context
) -> None:
    service, _ = await _catalog(db_session, platform_auth_context.user_id)
    workspace = await _workspace(db_session, "Exceções versionadas")
    contract, _ = await service.create_contract(
        payload=WorkspaceContractCreateRequest(
            workspace_id=workspace.id,
            lines=[WorkspaceContractLineWrite(sku="crm_start")],
            reason="Contrato para exceções versionadas.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="exception-contract",
    )
    now = datetime.now(UTC)
    adjustment, _ = await service.add_adjustment(
        contract_id=contract.id,
        payload=CommercialAdjustmentCreateRequest(
            type=CommercialAdjustmentType.fixed_discount,
            value=1_000,
            starts_at=now,
            stack_group="versioned",
            reason="Ajuste inicial.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="exception-adjustment",
    )
    updated_adjustment, _ = await service.update_adjustment(
        adjustment_id=adjustment.id,
        expected_version=1,
        payload=CommercialAdjustmentUpdateRequest(
            type=CommercialAdjustmentType.fixed_discount,
            value=2_000,
            starts_at=now,
            stack_group="versioned",
            reason="Ajuste corrigido.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="exception-adjustment-update",
    )
    revoked_adjustment, _ = await service.revoke_adjustment(
        adjustment_id=adjustment.id,
        expected_version=updated_adjustment.version,
        payload=CommercialRecordRevokeRequest(reason="Ajuste encerrado."),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="exception-adjustment-revoke",
    )
    assert revoked_adjustment.status is CommercialRecordStatus.revoked

    bonus, _ = await service.add_bonus(
        workspace_id=workspace.id,
        payload=EntitlementBonusCreateRequest(
            entitlement=EntitlementKey.tokens,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=10_000,
            starts_at=now,
            renews_each_period=True,
            reason="Bônus inicial.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="exception-bonus",
    )
    updated_bonus, _ = await service.update_bonus(
        bonus_id=bonus.id,
        expected_version=1,
        payload=EntitlementBonusUpdateRequest(
            entitlement=EntitlementKey.tokens,
            mode=EntitlementBonusMode.quantity_delta,
            quantity=20_000,
            starts_at=now,
            renews_each_period=True,
            reason="Bônus corrigido.",
        ),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="exception-bonus-update",
    )
    revoked_bonus, _ = await service.revoke_bonus(
        bonus_id=bonus.id,
        expected_version=updated_bonus.version,
        payload=CommercialRecordRevokeRequest(reason="Bônus encerrado."),
        actor_user_id=platform_auth_context.user_id,
        idempotency_key="exception-bonus-revoke",
    )
    assert revoked_bonus.status is CommercialRecordStatus.revoked
