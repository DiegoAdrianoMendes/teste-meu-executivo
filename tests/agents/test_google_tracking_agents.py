"""Contratos dos agentes nativos de tráfego/tracking Google (Frente C).

Cobre:
1. Os dois agentes aparecem no registro do klm_agent_service com o role certo
   e TaskClass.MEDIUM.
2. O SYSTEM_PROMPT de cada um embute o conhecimento verificado do dossiê
   (prova de que o conhecimento está embutido, não dependente de RAG externo).
3. Governança: são elegíveis a plan/execute read-only seguindo a delegação
   parent-only, mas NÃO têm permissão de ação externa quando o hard gate
   está fechado (mesmo contrato fail-closed de
   tests/services/test_agent_governance_service.py).
"""

from __future__ import annotations

import pytest

from app.agents.stubs import klm_tracking_engineer, klm_trafego_google
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

_KNOWLEDGE_MARKERS = ("Data Manager API", "google_key", "Consent Mode v2")


# ---------------------------------------------------------------------------
# 1. Registro do klm_agent_service
# ---------------------------------------------------------------------------


def test_trafego_google_is_registered_with_correct_role_and_task_class() -> None:
    assert "trafego_google" in AGENTS_BY_KEY
    agent = AGENTS_BY_KEY["trafego_google"]
    assert agent in AGENT_DEFINITIONS
    assert agent.name == "Gestor de Tráfego Google"
    assert agent.role == "Planejamento e diagnóstico de campanhas Google Ads"
    assert agent.group == "Aquisição"
    assert AGENT_TASK_CLASSES["trafego_google"] == TaskClass.MEDIUM


def test_tracking_engineer_is_registered_with_correct_role_and_task_class() -> None:
    assert "tracking_engineer" in AGENTS_BY_KEY
    agent = AGENTS_BY_KEY["tracking_engineer"]
    assert agent in AGENT_DEFINITIONS
    assert agent.name == "Engenheiro de Tracking"
    assert agent.role == "Contêiner GTM, tags GA4/Ads e Consent Mode v2"
    assert agent.group == "Aquisição"
    assert AGENT_TASK_CLASSES["tracking_engineer"] == TaskClass.MEDIUM


@pytest.mark.parametrize("agent_key", ["trafego_google", "tracking_engineer"])
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


@pytest.mark.parametrize("module", [klm_trafego_google, klm_tracking_engineer])
def test_system_prompt_embeds_google_tracking_knowledge(module) -> None:
    for marker in _KNOWLEDGE_MARKERS:
        assert marker in module.SYSTEM_PROMPT


@pytest.mark.parametrize("module", [klm_trafego_google, klm_tracking_engineer])
def test_system_prompt_embeds_governance_rules_from_dossier_section_7(module) -> None:
    assert "read-only" in module.SYSTEM_PROMPT
    assert "external_delivery_allowed" in module.SYSTEM_PROMPT
    assert "HARD_HUMAN_GATES" in module.SYSTEM_PROMPT
    assert "nunca inventa" in module.SYSTEM_PROMPT or "nunca fabrica" in module.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 3. Governança: elegíveis a plan/execute read-only; sem ação externa com
#    gate fechado (mesmo contrato de tests/services/test_agent_governance_service.py)
# ---------------------------------------------------------------------------


def _authorize(**overrides):
    values = {
        "target_agent_key": "trafego_google",
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
        "available_tool_keys": AGENTS_BY_KEY["trafego_google"].tools,
        "requested_tool_keys": (),
    }
    values.update(overrides)
    return AgentGovernanceService.authorize(**values)


def _authorize_tracking_engineer(**overrides):
    values = {
        "target_agent_key": "tracking_engineer",
        "requested_by_agent_key": "track",
        "available_tool_keys": AGENTS_BY_KEY["tracking_engineer"].tools,
    }
    values.update(overrides)
    return _authorize(**values)


def test_hierarchy_places_new_agents_under_their_declared_parent() -> None:
    assert PARENT_BY_KEY["trafego_google"] == "marketing"
    assert PARENT_BY_KEY["tracking_engineer"] == "track"
    assert AgentGovernanceService.hierarchy_path("trafego_google") == (
        "orquestrador",
        "marketing",
        "trafego_google",
    )
    assert AgentGovernanceService.hierarchy_path("tracking_engineer") == (
        "orquestrador",
        "track",
        "tracking_engineer",
    )


def test_trafego_google_is_eligible_for_read_only_plan_and_diagnose() -> None:
    plan = _authorize(requested_action="plan")
    assert plan.target_agent_key == "trafego_google"
    assert plan.external_actions_allowed is False

    diagnose = _authorize(
        requested_action="diagnose",
        requested_tool_keys=("ads_manager_read",),
    )
    assert diagnose.requested_tool_keys == ("ads_manager_read",)
    assert diagnose.external_actions_allowed is False


def test_tracking_engineer_is_eligible_for_read_only_plan_and_diagnose() -> None:
    plan = _authorize_tracking_engineer(requested_action="plan")
    assert plan.target_agent_key == "tracking_engineer"
    assert plan.external_actions_allowed is False

    diagnose = _authorize_tracking_engineer(
        requested_action="diagnose",
        requested_tool_keys=("pixels_read",),
    )
    assert diagnose.requested_tool_keys == ("pixels_read",)
    assert diagnose.external_actions_allowed is False


def test_trafego_google_cannot_execute_external_action_while_gate_is_closed() -> None:
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(requested_action="send_external")
    assert captured.value.code == "HUMAN_GATE_REQUIRED"


def test_tracking_engineer_cannot_execute_external_action_while_gate_is_closed() -> None:
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize_tracking_engineer(requested_action="send_external")
    assert captured.value.code == "HUMAN_GATE_REQUIRED"


def test_new_agents_only_delegate_from_their_direct_parent() -> None:
    with pytest.raises(AgentGovernanceViolation) as invalid:
        _authorize(requested_by_agent_key="track", delegation_depth=1)
    assert invalid.value.code == "INVALID_DELEGATION_PATH"

    with pytest.raises(AgentGovernanceViolation) as invalid_engineer:
        _authorize_tracking_engineer(requested_by_agent_key="marketing", delegation_depth=1)
    assert invalid_engineer.value.code == "INVALID_DELEGATION_PATH"


def test_new_agents_cannot_use_tool_outside_their_declared_scope() -> None:
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(
            available_tool_keys=("kpis",),
            requested_tool_keys=("crm_write_guarded",),
        )
    assert captured.value.code == "TOOL_SCOPE_DENIED"
