"""Índice único de playbooks de atendimento e conversão por área jurídica.

Consolida em um único dicionário todos os playbooks (`playbook_*.py`) mais o
padrão-ouro de Direito da Saúde (`direito_saude_vendas.py`), mapeando cada
`areaKey` de `app.data.legal_area_catalog.LEGAL_AREA_CATALOG` à tupla
`(versão, texto_do_playbook)`. É a fonte única consultada pelo router de
agentes para embutir a base de conhecimento correta no `SYSTEM_PROMPT` do
especialista/closer de cada área, sem depender de RAG externo.

Cada entrada nova de área no catálogo precisa de uma entrada correspondente
aqui — o teste `tests/agents/test_area_playbooks.py` falha se alguma área
ficar sem playbook.
"""
from __future__ import annotations

from app.agents.knowledge.direito_saude_vendas import (
    DIREITO_SAUDE_VENDAS_KNOWLEDGE,
    DIREITO_SAUDE_VENDAS_KNOWLEDGE_VERSION,
)
from app.agents.knowledge.playbook_bancario import (
    PLAYBOOK_BANCARIO,
    PLAYBOOK_BANCARIO_VERSION,
)
from app.agents.knowledge.playbook_consumidor import (
    PLAYBOOK_CONSUMIDOR,
    PLAYBOOK_CONSUMIDOR_VERSION,
)
from app.agents.knowledge.playbook_criminal import (
    PLAYBOOK_CRIMINAL,
    PLAYBOOK_CRIMINAL_VERSION,
)
from app.agents.knowledge.playbook_digital import (
    PLAYBOOK_DIGITAL,
    PLAYBOOK_DIGITAL_VERSION,
)
from app.agents.knowledge.playbook_empresarial import (
    PLAYBOOK_EMPRESARIAL,
    PLAYBOOK_EMPRESARIAL_VERSION,
)
from app.agents.knowledge.playbook_erro_medico import (
    PLAYBOOK_ERRO_MEDICO,
    PLAYBOOK_ERRO_MEDICO_VERSION,
)
from app.agents.knowledge.playbook_familia import (
    PLAYBOOK_FAMILIA,
    PLAYBOOK_FAMILIA_VERSION,
)
from app.agents.knowledge.playbook_geral_vendas import (
    PLAYBOOK_GERAL_VENDAS,
    PLAYBOOK_GERAL_VENDAS_VERSION,
)
from app.agents.knowledge.playbook_imobiliario import (
    PLAYBOOK_IMOBILIARIO,
    PLAYBOOK_IMOBILIARIO_VERSION,
)
from app.agents.knowledge.playbook_previdenciario import (
    PLAYBOOK_PREVIDENCIARIO,
    PLAYBOOK_PREVIDENCIARIO_VERSION,
)
from app.agents.knowledge.playbook_propriedade_intelectual import (
    PLAYBOOK_PROPRIEDADE_INTELECTUAL,
    PLAYBOOK_PROPRIEDADE_INTELECTUAL_VERSION,
)
from app.agents.knowledge.playbook_saude_publica_sus import (
    PLAYBOOK_SAUDE_PUBLICA_SUS,
    PLAYBOOK_SAUDE_PUBLICA_SUS_VERSION,
)
from app.agents.knowledge.playbook_seguros import (
    PLAYBOOK_SEGUROS,
    PLAYBOOK_SEGUROS_VERSION,
)
from app.agents.knowledge.playbook_servidor_publico import (
    PLAYBOOK_SERVIDOR_PUBLICO,
    PLAYBOOK_SERVIDOR_PUBLICO_VERSION,
)
from app.agents.knowledge.playbook_societario import (
    PLAYBOOK_SOCIETARIO,
    PLAYBOOK_SOCIETARIO_VERSION,
)
from app.agents.knowledge.playbook_sucessoes import (
    PLAYBOOK_SUCESSOES,
    PLAYBOOK_SUCESSOES_VERSION,
)
from app.agents.knowledge.playbook_trabalhista import (
    PLAYBOOK_TRABALHISTA,
    PLAYBOOK_TRABALHISTA_VERSION,
)
from app.agents.knowledge.playbook_tributario import (
    PLAYBOOK_TRIBUTARIO,
    PLAYBOOK_TRIBUTARIO_VERSION,
)

# `areaKey` (de LEGAL_AREA_CATALOG) -> (versão do playbook, texto do playbook).
AREA_PLAYBOOKS: dict[str, tuple[str, str]] = {
    "saude_suplementar": (DIREITO_SAUDE_VENDAS_KNOWLEDGE_VERSION, DIREITO_SAUDE_VENDAS_KNOWLEDGE),
    "erro_medico": (PLAYBOOK_ERRO_MEDICO_VERSION, PLAYBOOK_ERRO_MEDICO),
    "bancario": (PLAYBOOK_BANCARIO_VERSION, PLAYBOOK_BANCARIO),
    "trabalhista": (PLAYBOOK_TRABALHISTA_VERSION, PLAYBOOK_TRABALHISTA),
    "previdenciario": (PLAYBOOK_PREVIDENCIARIO_VERSION, PLAYBOOK_PREVIDENCIARIO),
    "tributario": (PLAYBOOK_TRIBUTARIO_VERSION, PLAYBOOK_TRIBUTARIO),
    "consumidor": (PLAYBOOK_CONSUMIDOR_VERSION, PLAYBOOK_CONSUMIDOR),
    "digital": (PLAYBOOK_DIGITAL_VERSION, PLAYBOOK_DIGITAL),
    "propriedade_intelectual": (PLAYBOOK_PROPRIEDADE_INTELECTUAL_VERSION, PLAYBOOK_PROPRIEDADE_INTELECTUAL),
    "seguros": (PLAYBOOK_SEGUROS_VERSION, PLAYBOOK_SEGUROS),
    "servidor_publico": (PLAYBOOK_SERVIDOR_PUBLICO_VERSION, PLAYBOOK_SERVIDOR_PUBLICO),
    "familia": (PLAYBOOK_FAMILIA_VERSION, PLAYBOOK_FAMILIA),
    "empresarial": (PLAYBOOK_EMPRESARIAL_VERSION, PLAYBOOK_EMPRESARIAL),
    "imobiliario": (PLAYBOOK_IMOBILIARIO_VERSION, PLAYBOOK_IMOBILIARIO),
    "criminal": (PLAYBOOK_CRIMINAL_VERSION, PLAYBOOK_CRIMINAL),
    "sucessoes": (PLAYBOOK_SUCESSOES_VERSION, PLAYBOOK_SUCESSOES),
    "societario": (PLAYBOOK_SOCIETARIO_VERSION, PLAYBOOK_SOCIETARIO),
    "saude_publica_sus": (PLAYBOOK_SAUDE_PUBLICA_SUS_VERSION, PLAYBOOK_SAUDE_PUBLICA_SUS),
}


def get_area_playbook(area_key: str) -> tuple[str, str] | None:
    """Retorna (versão, texto) do playbook da área, ou None se não houver."""
    return AREA_PLAYBOOKS.get(area_key)


# Playbooks transversais, não vinculados a uma área do catálogo — acessíveis
# a QUALQUER agente (SDR, especialista, closer) independentemente da matéria.
# `geral_vendas`: contorno de objeções, limites éticos e protocolo de
# handoff/fechamento comuns a todo atendimento comercial jurídico.
GLOBAL_PLAYBOOKS: dict[str, tuple[str, str]] = {
    "geral_vendas": (PLAYBOOK_GERAL_VENDAS_VERSION, PLAYBOOK_GERAL_VENDAS),
}


def get_global_playbook(playbook_key: str) -> tuple[str, str] | None:
    """Retorna (versão, texto) do playbook transversal, ou None se não houver."""
    return GLOBAL_PLAYBOOKS.get(playbook_key)
