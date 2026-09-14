"""Contratos dos agentes nativos de mensageria Meta (Frente C).

Cobre:
1. Os dois agentes aparecem no registro do klm_agent_service com o role
   certo e TaskClass.MEDIUM.
2. O SYSTEM_PROMPT de cada um embute o conhecimento verificado do dossiê
   de mensageria Meta (prova de que o conhecimento está embutido, não
   dependente de RAG externo) e as marcas de política que não podem faltar:
   janela de 24h, HUMAN_AGENT, private reply, erro 131049 e a data de
   deprecação 27/04/2026.
3. Governança: são elegíveis a plan/diagnose read-only seguindo a
   delegação parent-only, mas NÃO têm permissão de ação externa quando o
   hard gate está fechado (mesmo contrato fail-closed de
   tests/services/test_agent_governance_service.py).
4. O Especialista de Disparos Meta recusa, de forma determinística e sem
   chamar o modelo, qualquer pedido que viole política Meta (broadcast
   frio no Instagram, tag deprecada no Messenger).
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.stubs import klm_atendimento_meta, klm_disparos_meta
from app.ai.models import TaskClass
from app.services.agent_governance_service import (
    PARENT_BY_KEY,
    AgentGovernanceService,
    AgentGovernanceViolation,
)
from app.services.klm_agent_service import (
    AGENT_DEFINITIONS,
    AGENT_TASK_CLASSES,
    AGENTS_BY_KEY,
)

_POLICY_MARKERS = ("janela de 24", "HUMAN_AGENT", "private reply", "131049", "27/04/2026")


# ---------------------------------------------------------------------------
# 1. Registro do klm_agent_service
# ---------------------------------------------------------------------------


def test_atendimento_meta_is_registered_with_correct_role_and_task_class() -> None:
    assert "atendimento_meta" in AGENTS_BY_KEY
    agent = AGENTS_BY_KEY["atendimento_meta"]
    assert agent in AGENT_DEFINITIONS
    assert agent.name == "Especialista de Atendimento Meta"
    assert agent.role == "Orquestração de atendimento inbound WhatsApp/Messenger/Instagram"
    assert agent.group == "Atendimento"
    assert AGENT_TASK_CLASSES["atendimento_meta"] == TaskClass.MEDIUM


def test_disparos_meta_is_registered_with_correct_role_and_task_class() -> None:
    assert "disparos_meta" in AGENTS_BY_KEY
    agent = AGENTS_BY_KEY["disparos_meta"]
    assert agent in AGENT_DEFINITIONS
    assert agent.name == "Especialista de Disparos Meta"
    assert agent.role == "Campanhas compliant de disparo WhatsApp/Messenger/Instagram"
    assert agent.group == "Aquisição"
    assert AGENT_TASK_CLASSES["disparos_meta"] == TaskClass.MEDIUM


@pytest.mark.parametrize("agent_key", ["atendimento_meta", "disparos_meta"])
def test_new_agents_have_no_direct_write_or_draft_capability(agent_key: str) -> None:
    """Read-only por padrão: nenhuma escrita de CRM nem criação de rascunho
    direta — qualquer ação externa passa pelo gate + aprovação humana."""
    agent = AGENTS_BY_KEY[agent_key]
    assert agent.can_write_crm is False
    assert agent.can_create_draft is False
    assert agent.can_request_human is False


# ---------------------------------------------------------------------------
# 2. SYSTEM_PROMPT embute o conhecimento verificado (prova de embedding)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", [klm_atendimento_meta, klm_disparos_meta])
def test_system_prompt_embeds_meta_messaging_policy_markers(module) -> None:
    for marker in _POLICY_MARKERS:
        assert marker in module.SYSTEM_PROMPT


@pytest.mark.parametrize("module", [klm_atendimento_meta, klm_disparos_meta])
def test_system_prompt_embeds_governance_rules(module) -> None:
    assert "read-only" in module.SYSTEM_PROMPT
    assert "external_delivery_allowed" in module.SYSTEM_PROMPT
    assert "HARD_HUMAN_GATES" in module.SYSTEM_PROMPT
    assert "nunca inventa" in module.SYSTEM_PROMPT or "nunca fabrica" in module.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 3. Governança: elegíveis a plan/diagnose read-only; sem ação externa com
#    gate fechado (mesmo contrato de tests/services/test_agent_governance_service.py)
# ---------------------------------------------------------------------------


def _authorize(**overrides):
    values = {
        "target_agent_key": "disparos_meta",
        "initiator_type": "agent",
        "requested_by_agent_key": "marketing",
        "delegation_depth": 2,
        "matter_area_key": None,
        "matter_product_key": None,
        "run_workspace_id": "workspace-a",
        "matter_workspace_id": "workspace-a",
        "context_client_id": "cliente-a",
        "matter_client_id": "cliente-a",
        "manual_context_revision": 0,
        "area_enabled": True,
        "request_human": False,
        "requested_action": "plan",
        "available_tool_keys": AGENTS_BY_KEY["disparos_meta"].tools,
        "requested_tool_keys": (),
    }
    values.update(overrides)
    return AgentGovernanceService.authorize(**values)


def _authorize_atendimento_meta(**overrides):
    values = {
        "target_agent_key": "atendimento_meta",
        "requested_by_agent_key": "sdr",
        "available_tool_keys": AGENTS_BY_KEY["atendimento_meta"].tools,
    }
    values.update(overrides)
    return _authorize(**values)


def test_hierarchy_places_new_agents_under_their_declared_parent() -> None:
    assert PARENT_BY_KEY["disparos_meta"] == "marketing"
    assert PARENT_BY_KEY["atendimento_meta"] == "sdr"
    assert AgentGovernanceService.hierarchy_path("disparos_meta") == (
        "orquestrador",
        "marketing",
        "disparos_meta",
    )
    assert AgentGovernanceService.hierarchy_path("atendimento_meta") == (
        "orquestrador",
        "sdr",
        "atendimento_meta",
    )


def test_disparos_meta_is_eligible_for_read_only_plan_and_diagnose() -> None:
    plan = _authorize(requested_action="plan")
    assert plan.target_agent_key == "disparos_meta"
    assert plan.external_actions_allowed is False

    diagnose = _authorize(
        requested_action="diagnose",
        requested_tool_keys=("campanhas_read",),
    )
    assert diagnose.requested_tool_keys == ("campanhas_read",)
    assert diagnose.external_actions_allowed is False


def test_atendimento_meta_is_eligible_for_read_only_plan_and_diagnose() -> None:
    plan = _authorize_atendimento_meta(requested_action="plan")
    assert plan.target_agent_key == "atendimento_meta"
    assert plan.external_actions_allowed is False

    diagnose = _authorize_atendimento_meta(
        requested_action="diagnose",
        requested_tool_keys=("canonical_conversation",),
    )
    assert diagnose.requested_tool_keys == ("canonical_conversation",)
    assert diagnose.external_actions_allowed is False


def test_disparos_meta_cannot_execute_external_action_while_gate_is_closed() -> None:
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(requested_action="send_external")
    assert captured.value.code == "HUMAN_GATE_REQUIRED"


def test_atendimento_meta_cannot_execute_external_action_while_gate_is_closed() -> None:
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize_atendimento_meta(requested_action="send_external")
    assert captured.value.code == "HUMAN_GATE_REQUIRED"


def test_new_agents_only_delegate_from_their_direct_parent() -> None:
    with pytest.raises(AgentGovernanceViolation) as invalid:
        _authorize(requested_by_agent_key="track", delegation_depth=1)
    assert invalid.value.code == "INVALID_DELEGATION_PATH"

    with pytest.raises(AgentGovernanceViolation) as invalid_atendimento:
        _authorize_atendimento_meta(requested_by_agent_key="marketing", delegation_depth=1)
    assert invalid_atendimento.value.code == "INVALID_DELEGATION_PATH"


def test_new_agents_cannot_use_tool_outside_their_declared_scope() -> None:
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(
            available_tool_keys=("kpis",),
            requested_tool_keys=("crm_write_guarded",),
        )
    assert captured.value.code == "TOOL_SCOPE_DENIED"


# ---------------------------------------------------------------------------
# 4. Read-only sem ação externa com gate fechado: sem integração/canal
#    conectado, os stubs nunca chamam o modelo — devolvem o plano padrão.
# ---------------------------------------------------------------------------


def test_atendimento_meta_without_connected_channel_never_calls_model() -> None:
    result = asyncio.run(
        klm_atendimento_meta.run_atendimento_meta(
            "workspace-a",
            request_action="diagnose",
            connected_channels=(),
        )
    )
    assert result["status"] == "missing_integration"
    assert result["requires_human_approval"] is True
    assert result["plan_steps"]


def test_disparos_meta_without_connected_integration_never_calls_model() -> None:
    result = asyncio.run(
        klm_disparos_meta.run_disparos_meta(
            "workspace-a",
            request_action="plan",
            integration_connected=False,
        )
    )
    assert result["status"] == "missing_integration"
    assert result["requires_human_approval"] is True
    assert result["plan_steps"]


# ---------------------------------------------------------------------------
# 5. Recusa determinística de pedidos que violam política Meta.
# ---------------------------------------------------------------------------


def test_disparos_meta_refuses_cold_broadcast_on_instagram_without_calling_model() -> None:
    result = asyncio.run(
        klm_disparos_meta.run_disparos_meta(
            "workspace-a",
            request_action="plan",
            campaign_type="broadcast_frio_instagram",
            integration_connected=True,
        )
    )
    assert result["status"] == "policy_refused"
    assert result["violation_type"] == "instagram_cold_broadcast"
    assert result["compliant_alternative"]
    assert result["requires_human_approval"] is False


@pytest.mark.parametrize(
    "deprecated_tag",
    ["CONFIRMED_EVENT_UPDATE", "ACCOUNT_UPDATE", "POST_PURCHASE_UPDATE"],
)
def test_disparos_meta_refuses_deprecated_messenger_tag_without_calling_model(
    deprecated_tag: str,
) -> None:
    result = asyncio.run(
        klm_disparos_meta.run_disparos_meta(
            "workspace-a",
            request_action="plan",
            requested_message_tag=deprecated_tag,
            integration_connected=True,
        )
    )
    assert result["status"] == "policy_refused"
    assert result["violation_type"] == "messenger_deprecated_tag"
    assert "27/04/2026" in result["message"]
    assert result["compliant_alternative"]
    assert result["requires_human_approval"] is False
