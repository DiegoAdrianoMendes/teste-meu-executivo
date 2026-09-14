"""Índice das bases de PRODUÇÃO de peças — seleção por tipo de peça ou área.

Existe uma diferença que vale registrar, porque ela define quando usar este
índice e quando usar `playbooks_index`:

- `playbooks_index` responde "como VENDER esta matéria" (objeções, gatilhos,
  handoff comercial). É consumido por SDR, especialista e closer.
- este índice responde "como REDIGIR esta peça" (requisitos legais, estrutura,
  prazo, provas, erros que a matam). É consumido pelo agente de produção.
- `revisao_senior_protocolo` responde "como REVISAR antes do protocolo".

As três bases somadas passam de 130 mil caracteres. Injetar tudo no prompt
não melhora a peça: estoura o orçamento de contexto e dilui o que importa.
Por isso a seleção aqui devolve UMA base — a do tipo de peça em produção.
Quem redige uma renovatória recebe o método da renovatória, não o rito do
habeas corpus.

Origem: dossiês proprietários do titular (agentes 06-57 de MV Advocacia),
traduzidos para os serviços reais da plataforma. O que dependia de navegador,
execução de código ou vault local foi omitido e declarado em cada módulo, em
vez de prometer capacidade que o servidor não tem.
"""
from __future__ import annotations

from app.agents.knowledge.producao_apoio_tecnico import (
    PRODUCAO_APOIO_TECNICO_KNOWLEDGE,
    PRODUCAO_APOIO_TECNICO_KNOWLEDGE_VERSION,
)
from app.agents.knowledge.producao_civel_processual import (
    PRODUCAO_CIVEL_PROCESSUAL_KNOWLEDGE,
    PRODUCAO_CIVEL_PROCESSUAL_KNOWLEDGE_VERSION,
)
from app.agents.knowledge.producao_familia_criminal import (
    PRODUCAO_FAMILIA_CRIMINAL_KNOWLEDGE,
    PRODUCAO_FAMILIA_CRIMINAL_KNOWLEDGE_VERSION,
)
from app.agents.knowledge.producao_trabalhista_previdenciario import (
    PRODUCAO_TRABALHISTA_PREVIDENCIARIO_KNOWLEDGE,
    PRODUCAO_TRABALHISTA_PREVIDENCIARIO_KNOWLEDGE_VERSION,
)
from app.agents.knowledge.producao_tributario_empresarial_imobiliario import (
    PRODUCAO_TRIBUTARIO_EMPRESARIAL_IMOBILIARIO_KNOWLEDGE,
    PRODUCAO_TRIBUTARIO_EMPRESARIAL_IMOBILIARIO_KNOWLEDGE_VERSION,
)

CIVEL_PROCESSUAL = "civel_processual"
TRABALHISTA_PREVIDENCIARIO = "trabalhista_previdenciario"
FAMILIA_CRIMINAL = "familia_criminal"
TRIBUTARIO_EMPRESARIAL_IMOBILIARIO = "tributario_empresarial_imobiliario"
APOIO_TECNICO = "apoio_tecnico"

PRODUCAO_BASES: dict[str, tuple[str, str]] = {
    CIVEL_PROCESSUAL: (
        PRODUCAO_CIVEL_PROCESSUAL_KNOWLEDGE_VERSION,
        PRODUCAO_CIVEL_PROCESSUAL_KNOWLEDGE,
    ),
    TRABALHISTA_PREVIDENCIARIO: (
        PRODUCAO_TRABALHISTA_PREVIDENCIARIO_KNOWLEDGE_VERSION,
        PRODUCAO_TRABALHISTA_PREVIDENCIARIO_KNOWLEDGE,
    ),
    FAMILIA_CRIMINAL: (
        PRODUCAO_FAMILIA_CRIMINAL_KNOWLEDGE_VERSION,
        PRODUCAO_FAMILIA_CRIMINAL_KNOWLEDGE,
    ),
    TRIBUTARIO_EMPRESARIAL_IMOBILIARIO: (
        PRODUCAO_TRIBUTARIO_EMPRESARIAL_IMOBILIARIO_KNOWLEDGE_VERSION,
        PRODUCAO_TRIBUTARIO_EMPRESARIAL_IMOBILIARIO_KNOWLEDGE,
    ),
    APOIO_TECNICO: (
        PRODUCAO_APOIO_TECNICO_KNOWLEDGE_VERSION,
        PRODUCAO_APOIO_TECNICO_KNOWLEDGE,
    ),
}

# `areaKey` do LEGAL_AREA_CATALOG -> base de produção. Note que a área de venda
# e a base de produção não são a mesma coisa: `saude_suplementar` vende como
# saúde, mas a peça que dela nasce é uma inicial cível com tutela de urgência.
AREA_TO_BASE: dict[str, str] = {
    "saude_suplementar": CIVEL_PROCESSUAL,
    "erro_medico": CIVEL_PROCESSUAL,
    "bancario": CIVEL_PROCESSUAL,
    "consumidor": TRIBUTARIO_EMPRESARIAL_IMOBILIARIO,
    "seguros": CIVEL_PROCESSUAL,
    "digital": CIVEL_PROCESSUAL,
    "propriedade_intelectual": CIVEL_PROCESSUAL,
    "trabalhista": TRABALHISTA_PREVIDENCIARIO,
    "previdenciario": TRABALHISTA_PREVIDENCIARIO,
    "servidor_publico": TRABALHISTA_PREVIDENCIARIO,
    "saude_publica_sus": CIVEL_PROCESSUAL,
    "familia": FAMILIA_CRIMINAL,
    "sucessoes": FAMILIA_CRIMINAL,
    "criminal": FAMILIA_CRIMINAL,
    "tributario": TRIBUTARIO_EMPRESARIAL_IMOBILIARIO,
    "empresarial": TRIBUTARIO_EMPRESARIAL_IMOBILIARIO,
    "societario": TRIBUTARIO_EMPRESARIAL_IMOBILIARIO,
    "imobiliario": TRIBUTARIO_EMPRESARIAL_IMOBILIARIO,
}

# Roteamento por tipo de peça. Tem precedência sobre a área, porque o tipo de
# peça é mais específico: um habeas corpus continua sendo habeas corpus mesmo
# que o caso tenha entrado pela porta trabalhista.
#
# As chaves são fragmentos normalizados procurados por substring, não rótulos
# exatos, porque o tipo de peça chega de fonte livre (título do artefato,
# escolha do usuário, campo do caso) e nunca de um enum fechado.
TIPO_PECA_TO_BASE: tuple[tuple[str, str], ...] = (
    # Apoio técnico primeiro: são os entregáveis que NÃO são peça processual e
    # que qualquer matéria pode pedir.
    ("procuracao", APOIO_TECNICO),
    ("parecer", APOIO_TECNICO),
    ("contrato de prestacao", APOIO_TECNICO),
    ("minuta de contrato", APOIO_TECNICO),
    ("calculo", APOIO_TECNICO),
    ("atualizacao monetaria", APOIO_TECNICO),
    # Criminal e família.
    ("habeas corpus", FAMILIA_CRIMINAL),
    ("resposta a acusacao", FAMILIA_CRIMINAL),
    ("divorcio", FAMILIA_CRIMINAL),
    ("alimentos", FAMILIA_CRIMINAL),
    ("guarda", FAMILIA_CRIMINAL),
    ("inventario", FAMILIA_CRIMINAL),
    ("partilha", FAMILIA_CRIMINAL),
    # Trabalhista e previdenciário.
    ("reclamacao trabalhista", TRABALHISTA_PREVIDENCIARIO),
    ("defesa trabalhista", TRABALHISTA_PREVIDENCIARIO),
    ("verbas rescisorias", TRABALHISTA_PREVIDENCIARIO),
    ("aposentadoria", TRABALHISTA_PREVIDENCIARIO),
    ("bpc", TRABALHISTA_PREVIDENCIARIO),
    ("loas", TRABALHISTA_PREVIDENCIARIO),
    ("auxilio", TRABALHISTA_PREVIDENCIARIO),
    # Tributário, empresarial, imobiliário.
    ("mandado de seguranca", TRIBUTARIO_EMPRESARIAL_IMOBILIARIO),
    ("embargos a execucao fiscal", TRIBUTARIO_EMPRESARIAL_IMOBILIARIO),
    ("execucao fiscal", TRIBUTARIO_EMPRESARIAL_IMOBILIARIO),
    ("recuperacao judicial", TRIBUTARIO_EMPRESARIAL_IMOBILIARIO),
    ("contrato social", TRIBUTARIO_EMPRESARIAL_IMOBILIARIO),
    ("acordo de socios", TRIBUTARIO_EMPRESARIAL_IMOBILIARIO),
    ("acordo de acionistas", TRIBUTARIO_EMPRESARIAL_IMOBILIARIO),
    ("despejo", TRIBUTARIO_EMPRESARIAL_IMOBILIARIO),
    ("renovatoria", TRIBUTARIO_EMPRESARIAL_IMOBILIARIO),
    ("usucapiao", TRIBUTARIO_EMPRESARIAL_IMOBILIARIO),
    # Cível e processual por último: são os termos mais genéricos e não podem
    # capturar antes dos específicos (um "recurso" em matéria criminal já foi
    # resolvido acima pelo tipo concreto).
    ("peticao inicial", CIVEL_PROCESSUAL),
    ("inicial", CIVEL_PROCESSUAL),
    ("contestacao", CIVEL_PROCESSUAL),
    ("apelacao", CIVEL_PROCESSUAL),
    ("agravo", CIVEL_PROCESSUAL),
    ("impugnacao ao cumprimento", CIVEL_PROCESSUAL),
    ("cumprimento de sentenca", CIVEL_PROCESSUAL),
    ("cobranca", CIVEL_PROCESSUAL),
    ("monitoria", CIVEL_PROCESSUAL),
    ("recurso", CIVEL_PROCESSUAL),
)

_ACENTOS = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ",
                         "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC")


def _normalizar(valor: str) -> str:
    return " ".join(valor.translate(_ACENTOS).lower().split())


def get_producao_base(base_key: str) -> tuple[str, str] | None:
    """Retorna (versão, texto) da base de produção pela chave, ou None."""
    return PRODUCAO_BASES.get(base_key)


def resolve_producao_base_key(
    *, tipo_peca: str | None = None, area_key: str | None = None
) -> str | None:
    """Escolhe a base de produção adequada.

    O tipo de peça vence a área quando ambos são informados. Devolve None
    quando nada casa — e nesse caso o chamador NÃO deve inventar uma base
    qualquer: peça sem método reconhecido é peça que o humano precisa
    conduzir, e entregar o manual errado é pior que não entregar nenhum.
    """
    if tipo_peca:
        alvo = _normalizar(tipo_peca)
        for fragmento, base in TIPO_PECA_TO_BASE:
            if fragmento in alvo:
                return base
    if area_key:
        return AREA_TO_BASE.get(area_key)
    return None


def get_producao_knowledge(
    *, tipo_peca: str | None = None, area_key: str | None = None
) -> tuple[str, str] | None:
    """Retorna (versão, texto) da base de produção para o que será redigido."""
    base_key = resolve_producao_base_key(tipo_peca=tipo_peca, area_key=area_key)
    if base_key is None:
        return None
    return PRODUCAO_BASES[base_key]
