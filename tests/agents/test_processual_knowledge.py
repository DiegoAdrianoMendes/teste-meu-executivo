"""Contrato das bases de conhecimento nativas de monitoramento processual e
pós-venda (Frente D — extração de dossiês de ~/.claude/agents).

Cobre:
1. Os dois módulos existem e expõem as constantes de conhecimento com o
   padrão de `app/agents/knowledge/google_tracking.py` (KNOWLEDGE + VERSION
   + SOURCES).
2. Cada módulo contém os marcadores que provam que o conteúdo verificado do
   dossiê de origem foi de fato condensado (não é um resumo genérico).
3. Cada módulo tem volume suficiente (> 3000 caracteres) para sustentar o
   SYSTEM_PROMPT de um agente sem depender de RAG externo.
"""

from __future__ import annotations

from app.agents.knowledge import pos_venda_cliente, processual_monitoramento

_PROCESSUAL_MARKERS = (
    "CPC 219",
    "memória de cálculo",
    "comunica.pje.jus.br",
    "DataJud",
)

_POS_VENDA_MARKERS = (
    "juridiquês",
    "aprovação",
)


def test_processual_knowledge_module_exists_with_expected_shape() -> None:
    assert hasattr(processual_monitoramento, "PROCESSUAL_KNOWLEDGE")
    assert hasattr(processual_monitoramento, "PROCESSUAL_KNOWLEDGE_VERSION")
    assert hasattr(processual_monitoramento, "PROCESSUAL_KNOWLEDGE_SOURCES")
    assert isinstance(processual_monitoramento.PROCESSUAL_KNOWLEDGE, str)
    assert isinstance(processual_monitoramento.PROCESSUAL_KNOWLEDGE_VERSION, str)
    assert isinstance(processual_monitoramento.PROCESSUAL_KNOWLEDGE_SOURCES, tuple)
    assert len(processual_monitoramento.PROCESSUAL_KNOWLEDGE_SOURCES) > 0


def test_processual_knowledge_contains_verified_markers() -> None:
    content = processual_monitoramento.PROCESSUAL_KNOWLEDGE
    for marker in _PROCESSUAL_MARKERS:
        assert marker in content, f"marcador ausente na base processual: {marker!r}"


def test_processual_knowledge_has_sufficient_volume() -> None:
    assert len(processual_monitoramento.PROCESSUAL_KNOWLEDGE) > 3000


def test_pos_venda_knowledge_module_exists_with_expected_shape() -> None:
    assert hasattr(pos_venda_cliente, "POS_VENDA_KNOWLEDGE")
    assert hasattr(pos_venda_cliente, "POS_VENDA_KNOWLEDGE_VERSION")
    assert hasattr(pos_venda_cliente, "POS_VENDA_KNOWLEDGE_SOURCES")
    assert isinstance(pos_venda_cliente.POS_VENDA_KNOWLEDGE, str)
    assert isinstance(pos_venda_cliente.POS_VENDA_KNOWLEDGE_VERSION, str)
    assert isinstance(pos_venda_cliente.POS_VENDA_KNOWLEDGE_SOURCES, tuple)
    assert len(pos_venda_cliente.POS_VENDA_KNOWLEDGE_SOURCES) > 0


def test_pos_venda_knowledge_contains_verified_markers() -> None:
    content = pos_venda_cliente.POS_VENDA_KNOWLEDGE
    for marker in _POS_VENDA_MARKERS:
        assert marker in content, f"marcador ausente na base pós-venda: {marker!r}"


def test_pos_venda_knowledge_has_sufficient_volume() -> None:
    assert len(pos_venda_cliente.POS_VENDA_KNOWLEDGE) > 3000
