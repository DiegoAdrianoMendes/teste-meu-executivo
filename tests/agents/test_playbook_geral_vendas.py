"""Cobertura do Playbook Geral de Vendas (transversal, acessível a todos os
agentes de atendimento/vendas).

Cobre:
1. O playbook tem no mínimo 100 objeções reais catalogadas (padrão contável
   `OBJEÇÃO:` no texto).
2. O playbook contém os marcadores de governança comercial obrigatórios:
   Provimento 205 (publicidade OAB), piso (banda de desconto inviolável),
   handoff (protocolo entre agentes) e banda (negociação do closer).
3. O playbook está registrado em `GLOBAL_PLAYBOOKS`
   (app/agents/knowledge/playbooks_index.py), acessível por chave a
   qualquer agente, independentemente de área.
4. O SYSTEM_PROMPT do `klm_sdr_saude` (agente de vendas existente) embute o
   playbook geral, prova de que o contorno de objeções está disponível na
   conversa real do agente, não apenas no arquivo de conhecimento isolado.
"""

from __future__ import annotations

import re

from app.agents.knowledge import playbook_geral_vendas as pgv
from app.agents.knowledge.playbooks_index import GLOBAL_PLAYBOOKS, get_global_playbook
from app.agents.stubs import klm_sdr_saude

_OBJECTION_PATTERN = re.compile(r"^OBJEÇÃO:", re.MULTILINE)

_REQUIRED_MARKERS = (
    "Provimento 205",
    "piso",
    "handoff",
    "banda",
)


def test_playbook_has_at_least_100_objections() -> None:
    matches = _OBJECTION_PATTERN.findall(pgv.PLAYBOOK_GERAL_VENDAS)
    assert len(matches) >= 100, (
        f"Playbook geral de vendas tem apenas {len(matches)} objeções "
        "catalogadas (padrão 'OBJEÇÃO:'), esperado >= 100."
    )


def test_playbook_has_governance_markers() -> None:
    for marker in _REQUIRED_MARKERS:
        assert marker in pgv.PLAYBOOK_GERAL_VENDAS, (
            f"Marcador de governança obrigatório ausente do playbook geral "
            f"de vendas: {marker!r}."
        )


def test_playbook_has_version_and_sources() -> None:
    assert pgv.PLAYBOOK_GERAL_VENDAS_VERSION
    assert isinstance(pgv.PLAYBOOK_GERAL_VENDAS_VERSION, str)
    assert pgv._SOURCES
    assert len(pgv._SOURCES) >= 1


def test_playbook_registered_in_global_playbooks_index() -> None:
    assert "geral_vendas" in GLOBAL_PLAYBOOKS
    version, text = GLOBAL_PLAYBOOKS["geral_vendas"]
    assert version == pgv.PLAYBOOK_GERAL_VENDAS_VERSION
    assert text == pgv.PLAYBOOK_GERAL_VENDAS


def test_get_global_playbook_helper_returns_geral_vendas() -> None:
    result = get_global_playbook("geral_vendas")
    assert result is not None
    version, text = result
    assert version == pgv.PLAYBOOK_GERAL_VENDAS_VERSION
    assert text == pgv.PLAYBOOK_GERAL_VENDAS


def test_get_global_playbook_returns_none_for_unknown_key() -> None:
    assert get_global_playbook("nao_existe") is None


def test_klm_sdr_saude_system_prompt_embeds_geral_vendas_playbook() -> None:
    assert pgv.PLAYBOOK_GERAL_VENDAS_VERSION in klm_sdr_saude.SYSTEM_PROMPT
    assert "PLAYBOOK GERAL DE VENDAS" in klm_sdr_saude.SYSTEM_PROMPT
    # Prova de que o corpo do playbook (não só a versão) foi embutido.
    assert "OBJEÇÃO:" in klm_sdr_saude.SYSTEM_PROMPT
    assert "grant_discount" in klm_sdr_saude.SYSTEM_PROMPT
