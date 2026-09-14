"""Contrato do índice de bases de PRODUÇÃO de peças.

O que estes testes protegem, na ordem em que doem quando quebram:

1. Entregar o manual ERRADO é pior que não entregar nenhum. Um habeas corpus
   redigido com o método da apelação cível é uma peça perdida, e a ordem das
   regras de roteamento é o que impede isso.
2. Base sem método reconhecido devolve None, e não uma base qualquer.
3. As cinco bases carregam e trazem as regras invioláveis do titular
   (protocolo humano, zero placeholder, citação verificada).
"""
from __future__ import annotations

import pytest

from app.agents.knowledge.producao_index import (
    APOIO_TECNICO,
    AREA_TO_BASE,
    CIVEL_PROCESSUAL,
    FAMILIA_CRIMINAL,
    PRODUCAO_BASES,
    TRABALHISTA_PREVIDENCIARIO,
    TRIBUTARIO_EMPRESARIAL_IMOBILIARIO,
    get_producao_base,
    get_producao_knowledge,
    resolve_producao_base_key,
)


def test_as_cinco_bases_carregam_com_versao_e_conteudo() -> None:
    assert set(PRODUCAO_BASES) == {
        CIVEL_PROCESSUAL,
        TRABALHISTA_PREVIDENCIARIO,
        FAMILIA_CRIMINAL,
        TRIBUTARIO_EMPRESARIAL_IMOBILIARIO,
        APOIO_TECNICO,
    }
    for chave, (versao, texto) in PRODUCAO_BASES.items():
        assert versao.startswith("producao-"), chave
        # Base curta demais é base que virou casca em alguma refatoração.
        assert len(texto) > 15_000, f"{chave} encolheu para {len(texto)} chars"


@pytest.mark.parametrize(
    ("tipo_peca", "esperado"),
    [
        # O tipo específico tem que vencer o termo genérico que também casaria.
        ("Habeas corpus liberatório", FAMILIA_CRIMINAL),
        ("Recurso em sentido estrito", CIVEL_PROCESSUAL),
        ("Apelação cível", CIVEL_PROCESSUAL),
        ("Agravo de instrumento", CIVEL_PROCESSUAL),
        ("Impugnação ao cumprimento de sentença", CIVEL_PROCESSUAL),
        ("Cumprimento de sentença", CIVEL_PROCESSUAL),
        ("Reclamação trabalhista", TRABALHISTA_PREVIDENCIARIO),
        ("Aposentadoria por tempo de contribuição", TRABALHISTA_PREVIDENCIARIO),
        ("BPC-LOAS", TRABALHISTA_PREVIDENCIARIO),
        ("Ação de alimentos", FAMILIA_CRIMINAL),
        ("Divórcio litigioso", FAMILIA_CRIMINAL),
        ("Mandado de segurança tributário", TRIBUTARIO_EMPRESARIAL_IMOBILIARIO),
        ("Embargos à execução fiscal", TRIBUTARIO_EMPRESARIAL_IMOBILIARIO),
        ("Ação renovatória de locação", TRIBUTARIO_EMPRESARIAL_IMOBILIARIO),
        ("Usucapião extrajudicial", TRIBUTARIO_EMPRESARIAL_IMOBILIARIO),
        ("Procuração ad judicia et extra", APOIO_TECNICO),
        ("Parecer jurídico", APOIO_TECNICO),
    ],
)
def test_tipo_de_peca_roteia_para_a_base_certa(tipo_peca: str, esperado: str) -> None:
    assert resolve_producao_base_key(tipo_peca=tipo_peca) == esperado


def test_acento_e_caixa_nao_mudam_o_roteamento() -> None:
    # O tipo de peça chega de fonte livre (título do artefato, campo do caso),
    # nunca de um enum fechado, então grafia não pode decidir o método.
    assert (
        resolve_producao_base_key(tipo_peca="HABEAS CORPUS")
        == resolve_producao_base_key(tipo_peca="habeas corpus")
        == FAMILIA_CRIMINAL
    )
    assert (
        resolve_producao_base_key(tipo_peca="Renovatória")
        == resolve_producao_base_key(tipo_peca="renovatoria")
        == TRIBUTARIO_EMPRESARIAL_IMOBILIARIO
    )


def test_tipo_de_peca_vence_a_area() -> None:
    """Um HC continua sendo HC mesmo que o caso tenha entrado pela porta
    trabalhista. Se a área vencesse, a peça sairia com o método errado."""
    assert (
        resolve_producao_base_key(tipo_peca="Habeas corpus", area_key="trabalhista")
        == FAMILIA_CRIMINAL
    )


def test_area_serve_de_fallback_quando_nao_ha_tipo_de_peca() -> None:
    assert resolve_producao_base_key(area_key="criminal") == FAMILIA_CRIMINAL
    assert resolve_producao_base_key(area_key="societario") == TRIBUTARIO_EMPRESARIAL_IMOBILIARIO
    # Saúde suplementar VENDE como saúde, mas a peça é inicial cível.
    assert resolve_producao_base_key(area_key="saude_suplementar") == CIVEL_PROCESSUAL


def test_toda_area_do_catalogo_de_playbooks_tem_base_de_producao() -> None:
    """Área que vende e não sabe redigir é lacuna silenciosa: o lead entra e a
    peça não sai."""
    from app.agents.knowledge.playbooks_index import AREA_PLAYBOOKS

    faltando = sorted(set(AREA_PLAYBOOKS) - set(AREA_TO_BASE))
    assert not faltando, f"áreas sem base de produção: {faltando}"


def test_sem_correspondencia_devolve_none_em_vez_de_base_qualquer() -> None:
    assert resolve_producao_base_key(tipo_peca="ofício de encaminhamento") is None
    assert resolve_producao_base_key() is None
    assert get_producao_knowledge(tipo_peca="assunto que não existe") is None
    assert get_producao_base("base_inexistente") is None


def test_get_producao_knowledge_devolve_uma_base_so() -> None:
    """Concatenar as cinco estouraria o contexto e diluiria o que importa."""
    resultado = get_producao_knowledge(tipo_peca="Contestação cível")
    assert resultado is not None
    versao, texto = resultado
    assert versao == PRODUCAO_BASES[CIVEL_PROCESSUAL][0]
    assert texto == PRODUCAO_BASES[CIVEL_PROCESSUAL][1]


def test_regras_inviolaveis_do_titular_estao_em_todas_as_bases() -> None:
    """Estas três não são estilo, são o que já custou caro: peça protocolada
    por agente, peça entregue com campo em branco, e citação inventada."""
    for chave, (_versao, texto) in PRODUCAO_BASES.items():
        normalizado = texto.lower()
        assert "protocolo" in normalizado and "humano" in normalizado, chave
        assert "placeholder" in normalizado or "preencher" in normalizado, chave
        assert "verificad" in normalizado, chave


# ── Acoplamento ao agente de produção ──────────────────────────────────────


class _AreaFake:
    def __init__(self, value: str) -> None:
        self.value = value


class _MatterFake:
    def __init__(self, demand_type: str, area: str | None) -> None:
        self.demand_type = demand_type
        self.practice_area = _AreaFake(area) if area else None


def test_drafter_injeta_apenas_a_base_do_tipo_de_peca() -> None:
    """O redator recebe UM método, não os cinco: 130 mil caracteres no prompt
    estouram o contexto e diluem exatamente o que decide a peça."""
    from app.agents.stubs.klm_drafter import DRAFTER_PROMPT, _producao_system_prompt

    prompt, versao = _producao_system_prompt(_MatterFake("Habeas corpus", "criminal"))

    assert versao == PRODUCAO_BASES[FAMILIA_CRIMINAL][0]
    assert PRODUCAO_BASES[FAMILIA_CRIMINAL][1] in prompt
    assert DRAFTER_PROMPT in prompt
    # As outras quatro NÃO podem estar no prompt.
    for chave, (_v, texto) in PRODUCAO_BASES.items():
        if chave != FAMILIA_CRIMINAL:
            assert texto not in prompt, f"{chave} vazou para o prompt"


def test_drafter_sem_metodo_reconhecido_segue_com_o_contrato_base() -> None:
    """Entregar o manual errado é pior que não entregar nenhum."""
    from app.agents.stubs.klm_drafter import DRAFTER_PROMPT, _producao_system_prompt

    prompt, versao = _producao_system_prompt(_MatterFake("Ofício de encaminhamento", None))

    assert versao is None
    assert prompt == DRAFTER_PROMPT


def test_drafter_usa_a_area_quando_o_tipo_de_peca_nao_diz_nada() -> None:
    from app.agents.stubs.klm_drafter import _producao_system_prompt

    _prompt, versao = _producao_system_prompt(_MatterFake("Demanda do cliente", "trabalhista"))

    assert versao == PRODUCAO_BASES[TRABALHISTA_PREVIDENCIARIO][0]
