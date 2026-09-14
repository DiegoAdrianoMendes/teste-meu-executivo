"""Plano Mestre §14 — `AutonomyPolicyService.evaluate`: dado um evento e um
contexto, qual nível A0-A4 vale e o que é permitido agora.

Cobre: fail-closed sem política (A4/aprovação obrigatória); casamento por
evento+escopo (mais específico vence); A2 prepara mas nunca executa; A3
executa reversível mas ação externa continua gated (mesmo com
`external_delivery_allowed()` fechado no ambiente de teste); A4 sempre
humano mesmo que a política tente dispensar aprovação.
"""

from __future__ import annotations

import pytest

from app.services.agent_governance_service import HUMAN_QUEUE_AGENT_KEY
from app.services.autonomy_policy_service import AutonomyPolicyService


async def _create(service: AutonomyPolicyService, workspace_id, user_id, **overrides):
    payload = {
        "name": "Política de teste",
        "event_type": "lead_replied",
        "action": "draft_response",
        "autonomy_level": "A2",
        "risk_level": "baixo",
        "approval_required": True,
        "conditions": {},
        "budget_currency": "BRL",
        "priority": 100,
        "is_active": True,
    }
    payload.update(overrides)
    return await service.create_policy(workspace_id=workspace_id, user_id=user_id, payload=payload)


@pytest.mark.asyncio
async def test_evaluate_fails_closed_to_a4_when_no_policy_matches(db_session, auth_context):
    service = AutonomyPolicyService(db_session)
    decision = await service.evaluate(
        workspace_id=auth_context.workspace_id,
        event_type="evento_nunca_cadastrado",
        action="qualquer_acao",
    )
    assert decision.matched_policy_id is None
    assert decision.autonomy_level == "A4"
    assert decision.approval_required is True
    assert decision.can_prepare is False
    assert decision.can_execute_internal is False
    assert decision.escalation_target_agent_key == HUMAN_QUEUE_AGENT_KEY
    assert decision.idempotency_key.startswith("autonomy:")


@pytest.mark.asyncio
async def test_evaluate_matches_event_and_scope_picks_most_specific_policy(
    db_session, auth_context
):
    service = AutonomyPolicyService(db_session)
    await _create(
        service,
        auth_context.workspace_id,
        auth_context.user_id,
        name="Genérica",
        autonomy_level="A2",
        priority=100,
    )
    specific = await _create(
        service,
        auth_context.workspace_id,
        auth_context.user_id,
        name="Específica do agente closer",
        autonomy_level="A3",
        agent_key="closer",
        approval_required=False,
        priority=10,
    )
    await db_session.flush()

    decision = await service.evaluate(
        workspace_id=auth_context.workspace_id,
        event_type="lead_replied",
        action="draft_response",
        agent_key="closer",
    )
    assert decision.matched_policy_id == str(specific.id)
    assert decision.autonomy_level == "A3"


@pytest.mark.asyncio
async def test_evaluate_a2_can_prepare_but_never_execute(db_session, auth_context):
    service = AutonomyPolicyService(db_session)
    await _create(
        service,
        auth_context.workspace_id,
        auth_context.user_id,
        autonomy_level="A2",
        approval_required=False,
    )
    await db_session.flush()

    decision = await service.evaluate(
        workspace_id=auth_context.workspace_id,
        event_type="lead_replied",
        action="draft_response",
    )
    assert decision.autonomy_level == "A2"
    assert decision.can_prepare is True
    assert decision.can_execute_internal is False


@pytest.mark.asyncio
async def test_evaluate_a3_executes_internal_reversible_but_external_stays_gated(
    db_session, auth_context
):
    service = AutonomyPolicyService(db_session)
    await _create(
        service,
        auth_context.workspace_id,
        auth_context.user_id,
        event_type="follow_up_due",
        action="send_internal_reminder",
        autonomy_level="A3",
        approval_required=False,
    )
    await db_session.flush()

    internal_decision = await service.evaluate(
        workspace_id=auth_context.workspace_id,
        event_type="follow_up_due",
        action="send_internal_reminder",
        is_external_action=False,
    )
    assert internal_decision.autonomy_level == "A3"
    assert internal_decision.can_prepare is True
    assert internal_decision.can_execute_internal is True
    # No ambiente de teste external_delivery_allowed() é sempre False
    # (fail-closed por padrão) — reportado, mas não é o motivo do bloqueio
    # de uma ação interna.
    assert internal_decision.external_action_gate_open is False

    external_decision = await service.evaluate(
        workspace_id=auth_context.workspace_id,
        event_type="follow_up_due",
        action="send_internal_reminder",
        is_external_action=True,
    )
    assert external_decision.autonomy_level == "A3"
    assert external_decision.can_execute_internal is False
    assert external_decision.is_external_action is True


@pytest.mark.asyncio
async def test_evaluate_hard_human_gate_action_is_always_treated_as_external(
    db_session, auth_context
):
    """grant_discount/sign_contract/etc. (HARD_HUMAN_GATES de
    agent_governance_service) contam como externas mesmo sem o chamador
    marcar is_external_action — reaproveita o catálogo em vez de duplicar."""
    service = AutonomyPolicyService(db_session)
    await _create(
        service,
        auth_context.workspace_id,
        auth_context.user_id,
        event_type="negotiation_milestone",
        action="grant_discount",
        autonomy_level="A3",
        approval_required=False,
    )
    await db_session.flush()

    decision = await service.evaluate(
        workspace_id=auth_context.workspace_id,
        event_type="negotiation_milestone",
        action="grant_discount",
    )
    assert decision.is_external_action is True
    assert decision.can_execute_internal is False


@pytest.mark.asyncio
async def test_evaluate_a4_always_requires_human_even_if_policy_says_otherwise(
    db_session, auth_context
):
    service = AutonomyPolicyService(db_session)
    await _create(
        service,
        auth_context.workspace_id,
        auth_context.user_id,
        event_type="contract_signature_due",
        action="sign_contract",
        autonomy_level="A4",
        # Mesmo que alguém tente configurar approval_required=False em A4,
        # o enforcement do nível vence a configuração da política.
        approval_required=False,
    )
    await db_session.flush()

    decision = await service.evaluate(
        workspace_id=auth_context.workspace_id,
        event_type="contract_signature_due",
        action="sign_contract",
    )
    assert decision.autonomy_level == "A4"
    assert decision.approval_required is True
    assert decision.can_prepare is False
    assert decision.can_execute_internal is False


@pytest.mark.asyncio
async def test_evaluate_renders_idempotency_key_template(db_session, auth_context):
    service = AutonomyPolicyService(db_session)
    await _create(
        service,
        auth_context.workspace_id,
        auth_context.user_id,
        event_type="lead_replied",
        action="draft_response",
        idempotency_key_template="autonomy:{eventType}:{agentKey}:{action}",
        agent_key="closer",
    )
    await db_session.flush()

    decision = await service.evaluate(
        workspace_id=auth_context.workspace_id,
        event_type="lead_replied",
        action="draft_response",
        agent_key="closer",
    )
    assert decision.idempotency_key == "autonomy:lead_replied:closer:draft_response"
