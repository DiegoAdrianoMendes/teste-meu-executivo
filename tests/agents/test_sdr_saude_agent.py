"""Contratos do SDR de Direito da Saúde (negativas de plano de saúde).

Cobre:
1. O agente aparece no registro do klm_agent_service com role, grupo e
   TaskClass corretos.
2. O SYSTEM_PROMPT embute o guia de vendas de Direito da Saúde (prova de que
   o método de 8 passos, as 5 teses, a norma da primeira mensagem, o
   Provimento 205/2021 e a regra de escalonamento estão embutidos, não
   dependentes de RAG externo).
3. Governança: elegível a plan/execute read-only seguindo a delegação
   parent-only (pai = sdr), mas SEM permissão de ação externa (envio real de
   mensagem) quando o hard gate está fechado — mesmo contrato fail-closed de
   tests/services/test_agent_governance_service.py.
"""

from __future__ import annotations

import pytest

from app.agents.stubs import klm_sdr_saude
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

_GUIDE_MARKERS = (
    "sinto muito",
    "uma pergunta",
    "Tema 1.295",
    "Provimento 205",
    "GLP-1",
    "escala",
)


# ---------------------------------------------------------------------------
# 1. Registro do klm_agent_service
# ---------------------------------------------------------------------------


def test_sdr_saude_is_registered_with_correct_role_and_task_class() -> None:
    assert "sdr_saude_vendas" in AGENTS_BY_KEY
    agent = AGENTS_BY_KEY["sdr_saude_vendas"]
    assert agent in AGENT_DEFINITIONS
    assert agent.name == "SDR de Direito da Saúde"
    assert agent.role == "Atendimento consultivo a negativas de plano de saúde"
    assert agent.group == "Comercial"
    assert AGENT_TASK_CLASSES["sdr_saude_vendas"] == TaskClass.MEDIUM


def test_sdr_saude_has_no_direct_write_or_draft_capability() -> None:
    """Read-only por padrão: nenhuma escrita de CRM, criação de rascunho
    direta nem solicitação de humano fora do fluxo — qualquer envio real de
    mensagem passa pelo gate de ação externa + aprovação humana."""
    agent = AGENTS_BY_KEY["sdr_saude_vendas"]
    assert agent.can_write_crm is False
    assert agent.can_create_draft is False
    assert agent.can_request_human is False


# ---------------------------------------------------------------------------
# 2. SYSTEM_PROMPT embute o guia de vendas (prova de embedding)
# ---------------------------------------------------------------------------


def test_system_prompt_embeds_direito_saude_vendas_guide() -> None:
    for marker in _GUIDE_MARKERS:
        assert marker in klm_sdr_saude.SYSTEM_PROMPT


def test_system_prompt_embeds_governance_and_external_action_gate() -> None:
    assert "read-only" in klm_sdr_saude.SYSTEM_PROMPT
    assert "external_delivery_allowed" in klm_sdr_saude.SYSTEM_PROMPT
    assert "HARD_HUMAN_GATES" in klm_sdr_saude.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 3. Governança: elegível a plan/execute read-only; sem ação externa com
#    gate fechado (mesmo contrato de tests/services/test_agent_governance_service.py)
# ---------------------------------------------------------------------------


def _authorize(**overrides):
    values = {
        "target_agent_key": "sdr_saude_vendas",
        "initiator_type": "agent",
        "requested_by_agent_key": "sdr",
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
        "available_tool_keys": AGENTS_BY_KEY["sdr_saude_vendas"].tools,
        "requested_tool_keys": (),
    }
    values.update(overrides)
    return AgentGovernanceService.authorize(**values)


def test_hierarchy_places_sdr_saude_under_sdr() -> None:
    assert PARENT_BY_KEY["sdr_saude_vendas"] == "sdr"
    assert AgentGovernanceService.hierarchy_path("sdr_saude_vendas") == (
        "orquestrador",
        "sdr",
        "sdr_saude_vendas",
    )


def test_sdr_saude_is_eligible_for_read_only_plan_and_diagnose() -> None:
    plan = _authorize(requested_action="plan")
    assert plan.target_agent_key == "sdr_saude_vendas"
    assert plan.external_actions_allowed is False

    diagnose = _authorize(
        requested_action="diagnose",
        requested_tool_keys=("diagnostico_tese_saude",),
    )
    assert diagnose.requested_tool_keys == ("diagnostico_tese_saude",)
    assert diagnose.external_actions_allowed is False


def test_sdr_saude_cannot_send_external_message_while_gate_is_closed() -> None:
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(requested_action="send_external")
    assert captured.value.code == "HUMAN_GATE_REQUIRED"


def test_sdr_saude_only_delegates_from_its_direct_parent() -> None:
    with pytest.raises(AgentGovernanceViolation) as invalid:
        _authorize(requested_by_agent_key="marketing", delegation_depth=1)
    assert invalid.value.code == "INVALID_DELEGATION_PATH"


def test_sdr_saude_cannot_use_tool_outside_its_declared_scope() -> None:
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(
            available_tool_keys=("checklist",),
            requested_tool_keys=("crm_write_guarded",),
        )
    assert captured.value.code == "TOOL_SCOPE_DENIED"
