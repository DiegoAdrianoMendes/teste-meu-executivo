"""Contratos do agente de Pós-venda / Sucesso do Cliente.

Cobre:
1. O agente aparece no registro do klm_agent_service com role, grupo e
   TaskClass corretos, e no registro de governança com o pai declarado
   (`andamento`).
2. O SYSTEM_PROMPT embute o dossiê verificado de pós-venda/relacionamento
   (`app/agents/knowledge/pos_venda_cliente.py`), prova de que ele não
   depende de RAG externo, e as marcas obrigatórias do contrato: tradução
   de juridiquês, aprovação humana, informar != vender e template aprovado
   fora da janela de 24h.
3. Governança: elegível a plan/diagnose read-only seguindo a delegação
   parent-only (pai = andamento), mas SEM permissão de ação externa (envio
   real de mensagem) quando o hard gate está fechado — mesmo contrato
   fail-closed de tests/services/test_agent_governance_service.py.
4. `run_pos_venda` é fail-closed em três camadas determinísticas, nenhuma
   delas dependente de chamada de modelo: movimentação trivial nunca vira
   mensagem individual (agrupa no resumo periódico); decisão desfavorável e
   sentença nascem sempre com rascunho pendente de aprovação do advogado
   (nunca saem sozinhas do agente); e, sem o gate de execução de modelo
   aberto (default fail-closed deste runtime), o agente nunca fabrica
   rascunho para os demais eventos — a intenção fica registrada como
   "simulada" (`registro_simulado`), nunca um envio real.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.stubs import klm_pos_venda
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

_KNOWLEDGE_MARKERS = (
    "juridiquês",
    "aprovação",
    "informar",
    "template",
)


# ---------------------------------------------------------------------------
# 1. Registro do klm_agent_service
# ---------------------------------------------------------------------------


def test_pos_venda_cliente_is_registered_with_correct_role_and_task_class() -> None:
    assert "pos_venda_cliente" in AGENTS_BY_KEY
    agent = AGENTS_BY_KEY["pos_venda_cliente"]
    assert agent in AGENT_DEFINITIONS
    assert agent.name == "Pós-venda / Sucesso do Cliente"
    assert agent.group == "Atendimento"
    assert AGENT_TASK_CLASSES["pos_venda_cliente"] == TaskClass.MEDIUM


def test_pos_venda_cliente_can_draft_but_never_writes_crm_or_requests_human_directly() -> None:
    """Prepara rascunho de mensagem (`can_create_draft`), mas nunca escreve
    CRM nem solicita humano diretamente — qualquer envio real passa pelo
    gate de ação externa + aprovação humana (e decisão sensível passa,
    além disso, pelo hard gate de conteúdo do advogado)."""
    agent = AGENTS_BY_KEY["pos_venda_cliente"]
    assert agent.can_create_draft is True
    assert agent.can_write_crm is False
    assert agent.can_request_human is False


# ---------------------------------------------------------------------------
# 2. SYSTEM_PROMPT embute o dossiê verificado (prova de embedding)
# ---------------------------------------------------------------------------


def test_system_prompt_embeds_pos_venda_knowledge_markers() -> None:
    for marker in _KNOWLEDGE_MARKERS:
        assert marker in klm_pos_venda.SYSTEM_PROMPT


def test_system_prompt_embeds_governance_and_external_action_gate() -> None:
    assert "read-only" in klm_pos_venda.SYSTEM_PROMPT
    assert "external_delivery_allowed" in klm_pos_venda.SYSTEM_PROMPT
    assert "HARD_HUMAN_GATES" in klm_pos_venda.SYSTEM_PROMPT


def test_system_prompt_embeds_single_voice_protocol() -> None:
    assert "VOZ ÚNICA" in klm_pos_venda.SYSTEM_PROMPT
    assert "conversa canônica" in klm_pos_venda.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 3. Governança: elegível a plan/diagnose read-only; sem ação externa com
#    gate fechado (mesmo contrato de tests/services/test_agent_governance_service.py)
# ---------------------------------------------------------------------------


def _authorize(**overrides):
    values = {
        "target_agent_key": "pos_venda_cliente",
        "initiator_type": "agent",
        "requested_by_agent_key": "andamento",
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
        "available_tool_keys": AGENTS_BY_KEY["pos_venda_cliente"].tools,
        "requested_tool_keys": (),
    }
    values.update(overrides)
    return AgentGovernanceService.authorize(**values)


def test_hierarchy_places_pos_venda_cliente_under_andamento() -> None:
    assert PARENT_BY_KEY["pos_venda_cliente"] == "andamento"
    assert AgentGovernanceService.hierarchy_path("pos_venda_cliente") == (
        "orquestrador",
        "andamento",
        "pos_venda_cliente",
    )


def test_pos_venda_cliente_is_eligible_for_read_only_plan_and_diagnose() -> None:
    plan = _authorize(requested_action="plan")
    assert plan.target_agent_key == "pos_venda_cliente"
    assert plan.external_actions_allowed is False

    diagnose = _authorize(
        requested_action="diagnose",
        requested_tool_keys=("timeline",),
    )
    assert diagnose.requested_tool_keys == ("timeline",)
    assert diagnose.external_actions_allowed is False


def test_pos_venda_cliente_cannot_send_external_message_while_gate_is_closed() -> None:
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(requested_action="send_external")
    assert captured.value.code == "HUMAN_GATE_REQUIRED"


def test_pos_venda_cliente_only_delegates_from_its_direct_parent() -> None:
    with pytest.raises(AgentGovernanceViolation) as invalid:
        _authorize(requested_by_agent_key="marketing", delegation_depth=1)
    assert invalid.value.code == "INVALID_DELEGATION_PATH"


def test_pos_venda_cliente_cannot_use_tool_outside_its_declared_scope() -> None:
    with pytest.raises(AgentGovernanceViolation) as captured:
        _authorize(
            available_tool_keys=("timeline",),
            requested_tool_keys=("crm_write_guarded",),
        )
    assert captured.value.code == "TOOL_SCOPE_DENIED"


# ---------------------------------------------------------------------------
# 4. `run_pos_venda` — fail-closed em três camadas determinísticas
# ---------------------------------------------------------------------------


def test_movimentacao_trivial_nao_gera_mensagem_individual() -> None:
    resultado = asyncio.run(
        klm_pos_venda.run_pos_venda(
            "workspace-a",
            evento_tipo="movimentacao_trivial",
            evento_resumo={"numero_cnj": "0001234-37.2026.8.07.0001", "tipo": "juntada"},
        )
    )
    assert resultado["status"] == "grouped_in_periodic_summary"
    assert resultado["agrupar_resumo_periodico"] is True
    assert resultado["rascunho_mensagem"] == ""
    assert resultado["requires_human_approval"] is False


@pytest.mark.parametrize("evento_tipo", ["decisao_desfavoravel", "sentenca"])
def test_decisao_sensivel_gera_rascunho_pendente_de_aprovacao_do_advogado(
    evento_tipo: str,
) -> None:
    """Decisão desfavorável e sentença nunca saem sozinhas do agente: o
    rascunho nasce pendente de aprovação humana, nunca como envio direto."""
    resultado = asyncio.run(
        klm_pos_venda.run_pos_venda(
            "workspace-a",
            evento_tipo=evento_tipo,
            evento_resumo={
                "numero_cnj": "0001234-37.2026.8.07.0001",
                "descricao": "Pedido julgado improcedente em primeira instância.",
            },
        )
    )
    assert resultado["status"] == "pending_lawyer_approval"
    assert resultado["requer_aprovacao_advogado"] is True
    assert resultado["requires_human_approval"] is True
    assert resultado["rascunho_mensagem"]  # texto pronto para a revisão do advogado
    assert resultado["agrupar_resumo_periodico"] is False


def test_gate_fechado_gera_intencao_simulada_persistida() -> None:
    """Com o gate geral de entrega externa fechado (default fail-closed
    deste runtime), a intenção de comunicação é sempre um "registro
    simulado com o texto pronto" — nunca um envio real."""
    from app.core.klm_runtime import external_delivery_allowed

    assert external_delivery_allowed() is False  # pressuposto do ambiente de teste

    resultado = asyncio.run(
        klm_pos_venda.run_pos_venda(
            "workspace-a",
            evento_tipo="decisao_desfavoravel",
            evento_resumo={"numero_cnj": "0001234-37.2026.8.07.0001", "descricao": "Improcedente."},
        )
    )
    assert resultado["registro_simulado"] is True
    assert resultado["rascunho_mensagem"]


def test_evento_relevante_sem_gate_de_modelo_nunca_fabrica_rascunho() -> None:
    """Sem o gate de execução de modelo aberto (default fail-closed deste
    runtime), o agente nunca inventa um rascunho de comunicação para os
    eventos que dependeriam do modelo — devolve a intenção com o texto
    vazio e status `model_unavailable`."""
    from app.core.klm_runtime import model_api_execution_allowed

    assert model_api_execution_allowed() is False  # pressuposto do ambiente de teste

    resultado = asyncio.run(
        klm_pos_venda.run_pos_venda(
            "workspace-a",
            evento_tipo="movimentacao_nova",
            evento_resumo={"numero_cnj": "0001234-37.2026.8.07.0001"},
            dentro_da_janela_24h=False,
        )
    )
    assert resultado["status"] == "model_unavailable"
    assert resultado["rascunho_mensagem"] == ""
    assert resultado["requires_human_approval"] is True
    # Fora da janela de 24h, mesmo no fallback, só template aprovado.
    assert resultado["usar_template"] is True
    assert resultado["template_sugerido"]


def test_run_pos_venda_rejects_unknown_evento_tipo() -> None:
    with pytest.raises(ValueError):
        asyncio.run(
            klm_pos_venda.run_pos_venda(
                "workspace-a",
                evento_tipo="evento_inexistente",
                evento_resumo={},
            )
        )


# ---------------------------------------------------------------------------
# 5. `classify_evento_tipo` — classificação determinística de trivialidade
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tipo_ato",
    ["Juntada de petição", "Conclusos para despacho", "Certidão de decurso de prazo"],
)
def test_classify_evento_tipo_reconhece_movimentacao_trivial(tipo_ato: str) -> None:
    assert klm_pos_venda.classify_evento_tipo(tipo_ato=tipo_ato) == "movimentacao_trivial"


def test_classify_evento_tipo_default_e_movimentacao_nova() -> None:
    assert (
        klm_pos_venda.classify_evento_tipo(tipo_ato="Intimação para contestar")
        == "movimentacao_nova"
    )
    assert klm_pos_venda.classify_evento_tipo() == "movimentacao_nova"
