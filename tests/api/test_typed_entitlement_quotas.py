from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AICompletionResult
from app.ai.errors import ProviderCallError
from app.ai.limits import InMemoryDailyBudgetLedger
from app.ai.models import (
    BudgetReservation,
    CostEstimate,
    ProviderName,
    RouteCandidate,
)
from app.core.deps import AuthContext, get_current_auth_context
from app.main import app as fastapi_app
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.entitlement import (
    EntitlementKey,
    EntitlementUsageReservation,
    PlanEntitlement,
    UsageReservationStatus,
    WorkspaceMonthlyUsage,
)
from app.models.klm_operations import AreaBlueprint, AreaBlueprintStatus, KlmAuditEvent
from app.models.whatsapp import ConnectionType, WaConnectionStatus, WhatsAppConnection
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.services.entitlement_service import MonthlyUsageService
from app.services.klm_agent_service import AGENT_DEFINITIONS
from tests.agent_deployment_helpers import seed_verified_agent_deployments
from tests.identity_helpers import invitation_headers, invite_and_accept_team_member


@pytest.fixture(autouse=True)
async def _explicit_agent_deployment(db_session, auth_context, monkeypatch):
    await seed_verified_agent_deployments(
        db_session, auth_context.workspace_id, {"sdr"}, monkeypatch
    )


async def _seed_entitlements(
    db: AsyncSession,
    workspace_id: UUID,
    limits: dict[EntitlementKey, int],
) -> None:
    plan = Plan(
        name=f"Plano API quota {uuid4().hex[:8]}",
        tier=PlanTier.enterprise,
        price_monthly_cents=0,
        price_annually_cents=0,
        max_users=100,
        max_matters=None,
        max_tokens_per_month=1_000_000,
        max_ai_agents=100,
        features={},
        is_active=True,
        is_public=False,
        sort_order=1,
    )
    db.add(plan)
    await db.flush()
    db.add(
        Subscription(
            workspace_id=workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            amount_cents=0,
            currency="BRL",
        )
    )
    db.add_all(
        [
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=entitlement,
                enabled=True,
                limit_quantity=limit,
                hard_limit=True,
            )
            for entitlement, limit in limits.items()
        ]
    )
    db.add(
        AreaBlueprint(
            workspace_id=workspace_id,
            area_key="saude_suplementar",
            name="Saúde suplementar",
            status=AreaBlueprintStatus.active,
            version="v1.0.0-test",
            readiness=100,
            is_active=True,
            blueprint={},
        )
    )
    await db.commit()


def _flow_payload(name: str, *, active: bool = True) -> dict:
    return {
        "name": name,
        "trigger": "manual",
        "trigger_config": {},
        "nodes": [{"id": "start", "type": "trigger", "config": {}}],
        "edges": [],
        "is_active": active,
    }


async def _case(client) -> tuple[dict, dict]:
    client_response = await client.post(
        "/api/v1/clients",
        json={
            "name": "Cliente quota IA",
            "email": f"quota-{uuid4().hex[:8]}@example.com",
            "phone": "+5511999999999",
        },
    )
    assert client_response.status_code == 201, client_response.text
    person = client_response.json()
    matter_response = await client.post(
        "/api/v1/matters",
        json={
            "clientId": person["id"],
            "practiceArea": "saude_suplementar",
            "demandType": "Negativa para teste de quota",
            "columnId": "novo_lead",
            "microStatus": "lead_novo",
            "healthSubtheme": "medicamento_alto_custo",
            "healthObjectName": "Medicamento quota",
            "leadScore": 80,
        },
    )
    assert matter_response.status_code == 201, matter_response.text
    matter = matter_response.json()
    thread_response = await client.post(
        "/api/v1/klm/omnichannel/threads",
        json={
            "matterId": matter["id"],
            "clientId": person["id"],
            "primaryChannel": "whatsapp",
            "subject": "Conversa quota IA",
            "ownerAgentKey": "sdr",
            "score": 80,
        },
    )
    assert thread_response.status_code == 201, thread_response.text
    return matter, thread_response.json()


def _run_payload(matter: dict, thread: dict, key: str) -> dict:
    return {
        "agentKey": "sdr",
        "matterId": matter["id"],
        "threadId": thread["id"],
        "requestHuman": False,
        "instructions": "Executar teste de quota tipada.",
        "idempotencyKey": key,
    }


class _ReservationAwareGateway:
    def __init__(self, *, fail_after_reservation: bool = False) -> None:
        self.fail_after_reservation = fail_after_reservation
        self.generate_calls = 0
        self.provider_calls = 0
        self.price_catalog = SimpleNamespace(version="prices-quota-test")
        self.route_catalog = SimpleNamespace(version="routes-quota-test")

    async def generate(self, request, *, on_reservation=None) -> AICompletionResult:
        self.generate_calls += 1
        estimate = CostEstimate(
            amount_usd=Decimal("0.01"),
            input_tokens=100,
            output_tokens=100,
            catalog_version="prices-quota-test",
        )
        route = RouteCandidate(provider=ProviderName.OPENAI, model="model-quota-test")
        budget_reservation = BudgetReservation(
            reservation_id=f"budget-{request.request_id}",
            workspace_id=request.workspace_id,
            amount_usd=estimate.amount_usd,
            day=datetime.now(UTC).date().isoformat(),
        )
        if on_reservation is not None:
            await on_reservation(budget_reservation, estimate, route, request)
        if self.fail_after_reservation:
            error = ProviderCallError(
                "falha comprovada antes do provider",
                request_started=False,
                billing_unknown=False,
            )
            error.provider = route.provider.value
            error.model = route.model
            error.route_key = route.key
            error.gateway_request_id = request.request_id
            error.estimated_cost_usd = str(estimate.amount_usd)
            error.estimated_input_tokens = estimate.input_tokens
            error.estimated_output_tokens = estimate.output_tokens
            error.price_catalog_version = self.price_catalog.version
            error.route_catalog_version = self.route_catalog.version
            error.budget_reservation_id = budget_reservation.reservation_id
            error.budget_reservation_day = budget_reservation.day
            error.budget_reserved_amount_usd = str(budget_reservation.amount_usd)
            raise error

        self.provider_calls += 1
        return AICompletionResult(
            text='{"summary":"Análise quota","recommendation":"Revisar",'
            '"missingInformation":[],"riskFlags":[],"confidence":0.9}',
            input_tokens=60,
            output_tokens=20,
            cost_usd=0.004,
            cost_known=True,
            model_used=route.model,
            provider=route.provider.value,
            request_id=request.request_id,
            provider_request_id="provider-quota-test",
            price_catalog_version=self.price_catalog.version,
            route_catalog_version=self.route_catalog.version,
            structured_data={
                "summary": "Análise quota",
                "recommendation": "Revisar",
                "missingInformation": [],
                "riskFlags": [],
                "confidence": 0.9,
            },
        )


class _UnknownBillingGateway(_ReservationAwareGateway):
    def __init__(self) -> None:
        super().__init__()
        self.budget_ledger = InMemoryDailyBudgetLedger()

    async def generate(self, request, *, on_reservation=None) -> AICompletionResult:
        self.generate_calls += 1
        estimate = CostEstimate(
            amount_usd=Decimal("0.01"),
            input_tokens=100,
            output_tokens=100,
            catalog_version="prices-quota-test",
        )
        route = RouteCandidate(provider=ProviderName.OPENAI, model="model-quota-test")
        budget_reservation = await self.budget_ledger.reserve(
            request.workspace_id,
            estimate.amount_usd,
            Decimal("10"),
        )
        if on_reservation is not None:
            await on_reservation(budget_reservation, estimate, route, request)
        self.provider_calls += 1
        error = ProviderCallError(
            "resultado do provider incerto",
            request_started=True,
            billing_unknown=True,
            provider_request_id="provider-quota-unknown",
        )
        error.provider = route.provider.value
        error.model = route.model
        error.route_key = route.key
        error.gateway_request_id = request.request_id
        error.estimated_cost_usd = str(estimate.amount_usd)
        error.estimated_input_tokens = estimate.input_tokens
        error.estimated_output_tokens = estimate.output_tokens
        error.price_catalog_version = self.price_catalog.version
        error.route_catalog_version = self.route_catalog.version
        error.budget_reservation_id = budget_reservation.reservation_id
        error.budget_reservation_day = budget_reservation.day
        error.budget_reserved_amount_usd = str(budget_reservation.amount_usd)
        raise error


def _enable_model_api(monkeypatch: pytest.MonkeyPatch, gateway) -> None:
    monkeypatch.setattr(
        "app.services.klm_agent_service.settings.KLM_MODEL_APIS_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "app.services.klm_agent_service.settings.KLM_ONLINE_APPROVED",
        True,
    )
    monkeypatch.setattr(
        "app.services.klm_agent_service.get_model_gateway",
        lambda: gateway,
    )


@pytest.mark.asyncio
async def test_stock_quota_errors_expose_usage_limit_remaining_for_users_and_flows(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_entitlements(
        db_session,
        auth_context.workspace_id,
        {
            EntitlementKey.users: 1,
            EntitlementKey.automations: 1,
        },
    )

    user_response = await client.post(
        "/api/v1/admin/team-members",
        json={
            "name": "Segundo usuário",
            "email": "segundo-usuario@example.com",
            "workspaceRole": "operator",
        },
        headers=invitation_headers("user-quota"),
    )
    assert user_response.status_code == 409, user_response.text
    user_error = user_response.json()["detail"]
    assert user_error == {
        "code": "ENT-003",
        "message": "O limite corrente deste recurso foi atingido.",
        "entitlement": "users",
        "usage": 1,
        "limit": 1,
        "remaining": 0,
        "limitQuantity": 1,
        "currentQuantity": 1,
        "requestedQuantity": 1,
    }

    first = await client.post("/api/v1/flows", json=_flow_payload("Quota flow 1"))
    blocked = await client.post("/api/v1/flows", json=_flow_payload("Quota flow 2"))
    inactive = await client.post(
        "/api/v1/flows",
        json=_flow_payload("Quota flow inativo", active=False),
    )
    assert first.status_code == inactive.status_code == 201
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["usage"] == 1
    assert blocked.json()["detail"]["limit"] == 1
    assert blocked.json()["detail"]["remaining"] == 0

    activation = await client.put(
        f"/api/v1/flows/{inactive.json()['id']}",
        json={"is_active": True, "version": inactive.json()["version"]},
    )
    assert activation.status_code == 409, activation.text
    assert activation.json()["detail"]["entitlement"] == "automations"


@pytest.mark.asyncio
async def test_whatsapp_number_quota_is_typed_before_manual_and_tech_provider_calls(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_entitlements(
        db_session,
        auth_context.workspace_id,
        {EntitlementKey.whatsapp_numbers: 1},
    )
    db_session.add(
        WhatsAppConnection(
            workspace_id=auth_context.workspace_id,
            connection_type=ConnectionType.OFFICIAL_MANUAL,
            status=WaConnectionStatus.connected,
            phone_number_id="quota-existing-number",
            is_default=True,
        )
    )
    await db_session.commit()
    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.api.v1.endpoints.tech_providers.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.tech_provider_service.external_delivery_allowed",
        lambda: True,
    )
    provider_probe = AsyncMock(side_effect=AssertionError("provider não deve ser chamado"))
    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.get_phone_number_details",
        provider_probe,
    )
    exchange_probe = AsyncMock(side_effect=AssertionError("OAuth não deve ser chamado"))
    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.exchange_code_for_business_token",
        exchange_probe,
    )

    manual = await client.post(
        "/api/v1/whatsapp/connections/manual",
        json={
            "accessToken": "blocked-token",
            "phoneNumberId": "quota-new-number",
            "businessAccountId": "quota-waba",
        },
    )
    tech_provider = await client.post(
        "/api/v1/tech-providers/callback",
        json={
            "code": "blocked-code",
            "waba_id": "quota-waba",
            "phone_number_id": "quota-tech-number",
            "is_default": False,
        },
    )

    assert manual.status_code == tech_provider.status_code == 409
    for response in (manual, tech_provider):
        detail = response.json()["detail"]
        assert detail["code"] == "ENT-003"
        assert detail["entitlement"] == "whatsapp_numbers"
        assert detail["usage"] == detail["limit"] == 1
        assert detail["remaining"] == 0
    provider_probe.assert_not_awaited()
    exchange_probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_reactivation_rechecks_capacity(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    local_outbox_headers,
) -> None:
    client.headers.update(local_outbox_headers)
    await _seed_entitlements(
        db_session,
        auth_context.workspace_id,
        {EntitlementKey.users: 2},
    )
    member, _ = await invite_and_accept_team_member(
        client,
        name="Usuário reativável",
        email="reativavel@example.com",
        workspaceRole="operator",
    )
    user_id = UUID(member["userId"])
    deleted = await client.delete(f"/api/v1/users/{user_id}")
    assert deleted.status_code == 204, deleted.text

    entitlement = await db_session.scalar(
        select(PlanEntitlement).where(PlanEntitlement.entitlement == EntitlementKey.users)
    )
    assert entitlement is not None
    entitlement.limit_quantity = 1
    await db_session.commit()

    reactivation = await client.patch(
        f"/api/v1/admin/team-members/{member['membershipId']}",
        json={"status": "active"},
    )
    assert reactivation.status_code == 409, reactivation.text
    assert reactivation.json()["detail"]["usage"] == 1
    persisted = await db_session.get(
        WorkspaceMembership,
        UUID(member["membershipId"]),
    )
    assert persisted is not None and persisted.status == MembershipStatus.suspended


@pytest.mark.asyncio
async def test_agent_state_replay_is_idempotent_and_resume_is_quota_gated(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_entitlements(
        db_session,
        auth_context.workspace_id,
        {EntitlementKey.agents: 12},
    )

    first_pause = await client.patch(
        "/api/v1/klm/agents/crm/state",
        json={"state": "paused", "reason": "Reduzir capacidade"},
    )
    replay_pause = await client.patch(
        "/api/v1/klm/agents/crm/state",
        json={"state": "paused", "reason": "Replay sem novo write"},
    )
    resume = await client.patch(
        "/api/v1/klm/agents/crm/state",
        json={"state": "active", "reason": "Tentar exceder plano"},
    )

    assert first_pause.status_code == replay_pause.status_code == 200
    assert resume.status_code == 409, resume.text
    assert resume.json()["detail"]["entitlement"] == "agents"
    assert resume.json()["detail"]["usage"] == len(AGENT_DEFINITIONS) - 1
    audit_count = int(
        await db_session.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id,
                KlmAuditEvent.event_type == "agent_state_changed",
            )
        )
        or 0
    )
    assert audit_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("blocked_entitlement", "limits"),
    [
        (
            EntitlementKey.ai_executions,
            {
                EntitlementKey.ai_executions: 0,
                EntitlementKey.ai_conversations: 1,
            },
        ),
        (
            EntitlementKey.ai_conversations,
            {
                EntitlementKey.ai_executions: 1,
                EntitlementKey.ai_conversations: 0,
            },
        ),
    ],
)
async def test_ai_execution_and_conversation_blocks_are_typed_and_atomic(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    blocked_entitlement: EntitlementKey,
    limits: dict[EntitlementKey, int],
) -> None:
    await _seed_entitlements(db_session, auth_context.workspace_id, limits)
    matter, thread = await _case(client)
    request = _run_payload(matter, thread, f"blocked-{blocked_entitlement.value}")

    first = await client.post("/api/v1/klm/agents/runs", json=request)
    replay = await client.post("/api/v1/klm/agents/runs", json=request)

    assert first.status_code == replay.status_code == 409
    assert first.json() == replay.json()
    detail = first.json()["detail"]
    assert detail["code"] == "ENT-004"
    assert detail["entitlement"] == blocked_entitlement.value
    assert detail["usage"] == detail["limit"] == detail["remaining"] == 0
    assert detail["requestedQuantity"] == 1
    assert int(await db_session.scalar(select(func.count(AgentRun.id))) or 0) == 0
    for entitlement in (EntitlementKey.ai_executions, EntitlementKey.ai_conversations):
        usage = await MonthlyUsageService(db_session).get_usage(
            workspace_id=auth_context.workspace_id,
            entitlement=entitlement,
        )
        assert usage.consumed_quantity == usage.reserved_quantity == 0


@pytest.mark.asyncio
async def test_ai_token_reservation_commits_actual_usage_and_replay_does_not_charge_twice(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_entitlements(
        db_session,
        auth_context.workspace_id,
        {
            EntitlementKey.ai_executions: 2,
            EntitlementKey.ai_conversations: 2,
            EntitlementKey.tokens: 200,
        },
    )
    gateway = _ReservationAwareGateway()
    _enable_model_api(monkeypatch, gateway)
    matter, thread = await _case(client)
    request = _run_payload(matter, thread, "typed-token-success")

    first = await client.post("/api/v1/klm/agents/runs", json=request)
    replay = await client.post("/api/v1/klm/agents/runs", json=request)

    assert first.status_code == replay.status_code == 201
    assert replay.json()["deduplicated"] is True
    assert gateway.generate_calls == gateway.provider_calls == 1
    token_usage = await MonthlyUsageService(db_session).get_usage(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.tokens,
    )
    assert token_usage.consumed_quantity == 80
    assert token_usage.reserved_quantity == 0
    execution_usage = await MonthlyUsageService(db_session).get_usage(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.ai_executions,
    )
    conversation_usage = await MonthlyUsageService(db_session).get_usage(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.ai_conversations,
    )
    assert execution_usage.consumed_quantity == 1
    assert conversation_usage.consumed_quantity == 1
    reservations = list(
        (
            await db_session.scalars(
                select(EntitlementUsageReservation).where(
                    EntitlementUsageReservation.workspace_id == auth_context.workspace_id
                )
            )
        ).all()
    )
    assert len(reservations) == 3
    assert all(item.status == UsageReservationStatus.committed for item in reservations)


@pytest.mark.asyncio
async def test_ai_failure_after_token_reservation_releases_without_ghost_usage(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_entitlements(
        db_session,
        auth_context.workspace_id,
        {
            EntitlementKey.ai_executions: 2,
            EntitlementKey.ai_conversations: 2,
            EntitlementKey.tokens: 200,
        },
    )
    gateway = _ReservationAwareGateway(fail_after_reservation=True)
    _enable_model_api(monkeypatch, gateway)
    matter, thread = await _case(client)

    response = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(matter, thread, "typed-token-release"),
    )

    assert response.status_code == 201, response.text
    assert response.json()["runs"][0]["status"] == AgentRunStatus.failed.value
    assert gateway.generate_calls == 1
    assert gateway.provider_calls == 0
    token_usage = await MonthlyUsageService(db_session).get_usage(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.tokens,
    )
    assert token_usage.consumed_quantity == token_usage.reserved_quantity == 0
    token_reservation = await db_session.scalar(
        select(EntitlementUsageReservation).where(
            EntitlementUsageReservation.workspace_id == auth_context.workspace_id,
            EntitlementUsageReservation.entitlement == EntitlementKey.tokens,
        )
    )
    assert token_reservation is not None
    assert token_reservation.status == UsageReservationStatus.released


@pytest.mark.asyncio
async def test_uncertain_provider_failure_keeps_tokens_reserved_until_human_release(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    platform_auth_context: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_entitlements(
        db_session,
        auth_context.workspace_id,
        {
            EntitlementKey.ai_executions: 2,
            EntitlementKey.ai_conversations: 2,
            EntitlementKey.tokens: 200,
        },
    )
    gateway = _UnknownBillingGateway()
    _enable_model_api(monkeypatch, gateway)
    matter, thread = await _case(client)

    failed = await client.post(
        "/api/v1/klm/agents/runs",
        json=_run_payload(matter, thread, "typed-token-unknown"),
    )
    assert failed.status_code == 201, failed.text
    failed_run = failed.json()["runs"][0]
    assert failed_run["status"] == AgentRunStatus.failed.value
    assert failed_run["outputPayload"]["billingReconciliation"]["reconciliationRequired"]
    pending_usage = await MonthlyUsageService(db_session).get_usage(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.tokens,
    )
    assert pending_usage.consumed_quantity == 0
    assert pending_usage.reserved_quantity == 200

    reconciliation = {
        "decision": "not_charged",
        "actualCostUsd": "0",
        "evidenceReference": "provider-console-no-charge",
        "providerRequestId": "provider-quota-unknown",
        "reason": "O provider confirmou que não houve cobrança.",
    }
    async def override_platform_auth_context():
        return platform_auth_context

    fastapi_app.dependency_overrides[get_current_auth_context] = (
        override_platform_auth_context
    )
    first = await client.post(
        f"/api/v1/platform/workspaces/{auth_context.workspace_id}"
        f"/agent-runs/{failed_run['id']}/billing-reconciliation",
        json=reconciliation,
    )
    replay = await client.post(
        f"/api/v1/platform/workspaces/{auth_context.workspace_id}"
        f"/agent-runs/{failed_run['id']}/billing-reconciliation",
        json=reconciliation,
    )

    assert first.status_code == replay.status_code == 200
    assert first.json()["outputPayload"]["billingReconciliation"]["status"] == (
        "reconciled_not_charged"
    )
    released_usage = await MonthlyUsageService(db_session).get_usage(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.tokens,
    )
    assert released_usage.consumed_quantity == released_usage.reserved_quantity == 0
    token_reservation = await db_session.scalar(
        select(EntitlementUsageReservation).where(
            EntitlementUsageReservation.workspace_id == auth_context.workspace_id,
            EntitlementUsageReservation.entitlement == EntitlementKey.tokens,
        )
    )
    assert token_reservation is not None
    assert token_reservation.status == UsageReservationStatus.released
    assert gateway.generate_calls == gateway.provider_calls == 1


@pytest.mark.asyncio
async def test_ai_token_quota_block_is_typed_and_replays_the_same_error_without_provider_call(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_entitlements(
        db_session,
        auth_context.workspace_id,
        {
            EntitlementKey.ai_executions: 2,
            EntitlementKey.ai_conversations: 2,
            EntitlementKey.tokens: 199,
        },
    )
    gateway = _ReservationAwareGateway()
    _enable_model_api(monkeypatch, gateway)
    matter, thread = await _case(client)
    request = _run_payload(matter, thread, "typed-token-block")

    first = await client.post("/api/v1/klm/agents/runs", json=request)
    replay = await client.post("/api/v1/klm/agents/runs", json=request)

    assert first.status_code == replay.status_code == 409
    assert first.json() == replay.json()
    error = first.json()["detail"]
    assert error["code"] == "ENT-004"
    assert error["entitlement"] == "tokens"
    assert error["usage"] == 0
    assert error["limit"] == 199
    assert error["remaining"] == 199
    assert error["requestedQuantity"] == 200
    assert gateway.generate_calls == 1
    assert gateway.provider_calls == 0
    run = await db_session.scalar(
        select(AgentRun).where(AgentRun.workspace_id == auth_context.workspace_id)
    )
    assert run is not None and run.status == AgentRunStatus.failed


@pytest.mark.asyncio
async def test_audio_minutes_are_rounded_reserved_and_idempotent(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_entitlements(
        db_session,
        auth_context.workspace_id,
        {EntitlementKey.audio_minutes: 3},
    )
    _, thread = await _case(client)
    request = {
        "threadId": thread["id"],
        "transcript": "Quero explicar o caso por áudio.",
        "durationSeconds": 121,
        "idempotencyKey": "audio-quota-three-minutes",
    }

    first = await client.post("/api/v1/klm/audio/requests", json=request)
    replay = await client.post("/api/v1/klm/audio/requests", json=request)

    assert first.status_code == replay.status_code == 201
    assert first.json()["message"]["messageMetadata"]["durationSeconds"] == 121
    usage = await db_session.scalar(
        select(WorkspaceMonthlyUsage).where(
            WorkspaceMonthlyUsage.workspace_id == auth_context.workspace_id,
            WorkspaceMonthlyUsage.entitlement == EntitlementKey.audio_minutes,
        )
    )
    assert usage is not None
    assert usage.consumed_quantity == 3
    assert usage.reserved_quantity == 0

    blocked = await client.post(
        "/api/v1/klm/audio/requests",
        json={
            **request,
            "durationSeconds": 1,
            "idempotencyKey": "audio-quota-blocked",
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "ENT-004"
    assert blocked.json()["detail"]["entitlement"] == "audio_minutes"

    manual_only = await client.post(
        "/api/v1/klm/audio/requests",
        json={
            **request,
            "durationSeconds": 600,
            "transcriptionEnabled": False,
            "summaryEnabled": False,
            "voiceReplyRequested": False,
            "idempotencyKey": "audio-manual-no-ai-quota",
        },
    )
    assert manual_only.status_code == 201
    await db_session.refresh(usage)
    assert usage.consumed_quantity == 3
