from uuid import UUID

import pytest
from sqlalchemy import select

from app.api.v1.endpoints import finance_dimensional as finance_dimensional_endpoint
from app.core.deps import AuthContext
from app.models.agent_platform import HumanDecision, HumanNotificationDelivery
from app.models.finance_dimensional import FinanceForecast
from app.models.klm_operations import KlmAuditEvent
from app.models.workspace_membership import WorkspaceMembership


async def test_tax_review_routes_human_and_alerts_owner_in_app_and_phone(
    client, db_session, auth_context, active_subscription
):
    response = await client.post(
        "/api/v1/finance/dimensional/tax-reform/request-review",
        json={
            "requestKey": "tax-review-contract-001",
            "reason": "Validar premissas fiscais antes de decidir preços.",
            "simulation": {
                "annualRevenue": 100000,
                "annualExpense": 30000,
                "currentEffectiveRate": 10,
                "transitionRates": {"2026": 20},
                "pricePassThroughPercent": 50,
            },
            "slaMinutes": 240,
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["ownerNotified"] is True
    assert payload["phoneDeliveryStatus"] in {"pending", "not_configured"}

    decision = await db_session.get(HumanDecision, UUID(payload["decisionId"]))
    assert decision is not None
    assert decision.owner_user_id == auth_context.user_id
    assert decision.options["purpose"] == "finance_tax_reform_review"
    deliveries = list(
        (
            await db_session.scalars(
                select(HumanNotificationDelivery).where(
                    HumanNotificationDelivery.decision_id == decision.id,
                    HumanNotificationDelivery.recipient_user_id == auth_context.user_id,
                )
            )
        ).all()
    )
    assert {item.channel for item in deliveries} == {"in_app", "phone"}


async def test_finance_manage_denial_blocks_tax_review_even_for_owner(
    client,
    db_session,
    auth_context: AuthContext,
    active_subscription,
):
    membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.id == auth_context.membership_id,
            WorkspaceMembership.workspace_id == auth_context.workspace_id,
        )
    )
    assert membership is not None
    membership.permission_denials = [
        {"module": "finance", "action": "manage", "scope": "workspace"}
    ]
    await db_session.commit()

    response = await client.post(
        "/api/v1/finance/dimensional/tax-reform/request-review",
        json={
            "requestKey": "tax-review-denied-001",
            "reason": "Esta mutação precisa respeitar a negação explícita.",
            "simulation": {
                "annualRevenue": 100000,
                "annualExpense": 30000,
                "currentEffectiveRate": 10,
                "transitionRates": {"2026": 20},
                "pricePassThroughPercent": 50,
            },
            "slaMinutes": 240,
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "AUTH-012"


async def test_finance_view_denial_blocks_executive_analytics_even_for_owner(
    client,
    db_session,
    auth_context: AuthContext,
    active_subscription,
):
    membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.id == auth_context.membership_id,
            WorkspaceMembership.workspace_id == auth_context.workspace_id,
        )
    )
    assert membership is not None
    membership.permission_denials = [
        {"module": "finance", "action": "view", "scope": "workspace"}
    ]
    await db_session.commit()

    response = await client.get("/api/v1/finance/v1/executive/analytics")

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "AUTH-012"


async def test_forecast_generation_rolls_back_data_and_audit_together_on_audit_failure(
    client,
    db_session,
    auth_context: AuthContext,
    active_subscription,
    monkeypatch,
):
    original_audit = finance_dimensional_endpoint._audit
    calls = 0

    async def fail_during_second_audit(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected audit failure")
        await original_audit(*args, **kwargs)

    monkeypatch.setattr(finance_dimensional_endpoint, "_audit", fail_during_second_audit)

    with pytest.raises(RuntimeError, match="injected audit failure"):
        await client.post(
            "/api/v1/finance/dimensional/forecasts/generate",
            json={
                "asOf": "2026-08-03",
                "horizon": "90d",
                "scenarios": ["conservative", "base", "optimistic"],
            },
        )

    # The request transaction contains flushed rows but no commit. Rolling it
    # back must remove both the generated projections and the first audit row.
    await db_session.rollback()
    forecasts = list(
        (
            await db_session.scalars(
                select(FinanceForecast).where(
                    FinanceForecast.workspace_id == auth_context.workspace_id
                )
            )
        ).all()
    )
    audit_events = list(
        (
            await db_session.scalars(
                select(KlmAuditEvent).where(
                    KlmAuditEvent.workspace_id == auth_context.workspace_id,
                    KlmAuditEvent.event_type == "finance.forecast.generated",
                )
            )
        ).all()
    )
    assert forecasts == []
    assert audit_events == []


async def test_manual_execution_linked_decision_is_rejected_without_creating_wait(
    client, db_session, auth_context, active_subscription
):
    response = await client.post(
        "/api/v1/agent-platform/decisions",
        json={
            "decisionKey": "manual-runtime-link-001",
            "options": {},
            "flow_run_id": "00000000-0000-0000-0000-000000000001",
            "required_roles": ["workspace_owner"],
        },
    )
    assert response.status_code == 422
    decision = await db_session.scalar(
        select(HumanDecision).where(
            HumanDecision.workspace_id == auth_context.workspace_id,
            HumanDecision.decision_key == "manual-runtime-link-001",
        )
    )
    assert decision is None


async def test_human_routing_catalog_is_workspace_scoped_and_uses_owner_fallback(
    client, active_subscription
):
    response = await client.get("/api/v1/agent-platform/human-routing/catalog")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ownerFallback"]["available"] is True
    assert payload["directOwnerOnly"] is True
    assert payload["roles"] == [
        {
            "key": "workspace_owner",
            "label": "Dono do escritório",
            "routableCount": 1,
        }
    ]
    assert payload["skills"] == []
    assert payload["teams"] == []
    assert "id" not in response.text.lower()


async def test_manual_human_call_uses_server_key_and_returns_safe_projection(
    client, auth_context, active_subscription
):
    response = await client.post(
        "/api/v1/agent-platform/decisions",
        json={
            "decisionKey": "5511999999999",
            "options": {"reason": "CPF 000.000.000-00", "access_token": "secret"},
            "required_roles": ["workspace_owner"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {
        "id", "status", "purpose", "assignedUserId", "ownerUserId", "dueAt"
    }
    assert "5511999999999" not in response.text
    assert "000.000.000-00" not in response.text
    assert "secret" not in response.text


async def test_human_decision_list_uses_an_explicit_server_page_contract(
    client, active_subscription
):
    for suffix in ("first", "second"):
        created = await client.post(
            "/api/v1/agent-platform/decisions",
            json={
                "options": {"reason": f"Revisão paginada {suffix}"},
                "required_roles": ["workspace_owner"],
            },
        )
        assert created.status_code == 200, created.text

    response = await client.get("/api/v1/agent-platform/decisions?limit=1&offset=1")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"items", "total", "limit", "offset", "hasMore"}
    assert payload["limit"] == 1
    assert payload["offset"] == 1
    assert payload["total"] >= 2
    assert len(payload["items"]) == 1


async def test_agent_deployment_list_uses_an_explicit_server_page_contract(
    client, active_subscription
):
    created = await client.post(
        "/api/v1/agent-platform/deployments",
        json={
            "agent_key": "pagination_test_agent",
            "role_package": "legal_operations",
            "version": 1,
            "config": {
                "model": "local-test",
                "tools": ["tasks.read"],
                "knowledgeSources": ["workspace.tasks"],
                "autonomyPolicy": "draft_only",
                "humanRoutingPolicy": "legal_review_v1",
                "budgetPolicy": {"monthlyTokenLimit": 1_000},
            },
        },
    )
    assert created.status_code == 200, created.text

    response = await client.get("/api/v1/agent-platform/deployments?limit=1&offset=0")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"items", "total", "limit", "offset", "hasMore"}
    assert payload["limit"] == 1
    assert payload["offset"] == 0
    assert payload["total"] >= 1
    assert len(payload["items"]) == 1


async def test_manual_human_call_rejects_non_catalog_role(
    client, active_subscription
):
    response = await client.post(
        "/api/v1/agent-platform/decisions",
        json={
            "options": {},
            "required_roles": ["5511999999999"],
        },
    )
    assert response.status_code == 422, response.text


async def test_manual_human_call_rejects_numeric_skill_identifier(
    client, active_subscription
):
    response = await client.post(
        "/api/v1/agent-platform/decisions",
        json={
            "options": {},
            "required_skills": ["5511999999999"],
        },
    )
    assert response.status_code == 422, response.text

    prefixed = await client.post(
        "/api/v1/agent-platform/decisions",
        json={"options": {}, "required_skills": ["whatsapp_5511999999999"]},
    )
    assert prefixed.status_code == 422, prefixed.text
