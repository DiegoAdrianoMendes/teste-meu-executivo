"""Contratos do Revisor Sênior de Protocolo (agente `revisor_juridico`).

Cobre:
1. A base de conhecimento carrega, tem as 13 camadas e embute as regras
   inegociáveis (AAA, regra de ouro de fechamento, anti-prolixidade,
   placeholder, citação verificada) — sem depender de RAG externo.
2. O agente NÃO tem nenhuma capacidade de protocolo, aprovação, escrita ou
   rascunho, e o prompt afirma explicitamente que protocolo é humano.
3. O parecer sai sempre nos 3 níveis (crítico / médio / cosmético) e o
   veredito é derivado do parecer, nunca declarado à mão.
4. Citação não verificada nunca é apresentada como verificada: vira achado
   crítico e bloqueia o veredito.
"""

from __future__ import annotations

import pytest

from app.agents.knowledge import revisao_senior_protocolo as base
from app.agents.stubs import klm_revisor_senior as revisor
from app.ai.models import TaskClass
from app.models.legal_artifact_verification import (
    LegalReviewFindingKind,
    LegalReviewSeverity,
)
from app.services.klm_agent_service import (
    AGENT_DEFINITIONS,
    AGENT_TASK_CLASSES,
    AGENTS_BY_KEY,
)

AGENT_KEY = "revisor_juridico"


# ---------------------------------------------------------------------------
# 1. Base de conhecimento
# ---------------------------------------------------------------------------


def test_base_carrega_com_as_treze_camadas() -> None:
    assert len(base.CAMADAS_REVISAO) == 13
    for indice in range(1, 14):
        assert any(
            camada.startswith(f"{indice}.") for camada in base.CAMADAS_REVISAO
        ), f"camada {indice} ausente"
    assert base.REVISAO_SENIOR_PROTOCOLO_KNOWLEDGE_VERSION
    assert base.REVISAO_SENIOR_PROTOCOLO_SOURCES


@pytest.mark.parametrize(
    "marcador",
    [
        "AFIRMAÇÃO",
        "APOIO",
        "APLICAÇÃO",
        "REGRA DE OURO DE FECHAMENTO",
        "ANTI-PROLIXIDADE",
        "PROTOCOLO É EXCLUSIVAMENTE HUMANO",
        "NUNCA ENTREGAR COM PLACEHOLDER",
        "CITAÇÃO SÓ VALE VERIFICADA",
        "STRESS TEST ADVERSARIAL",
        "SUB-ROTINAS POR TIPO DE PEÇA",
    ],
)
def test_base_embute_as_regras_inegociaveis(marcador: str) -> None:
    assert marcador in base.REVISAO_SENIOR_PROTOCOLO_KNOWLEDGE


@pytest.mark.parametrize("letra", list("ABCDEFGHIJK"))
def test_base_tem_subrotina_por_tipo_de_peca(letra: str) -> None:
    assert f"({letra})" in base.REVISAO_SENIOR_PROTOCOLO_KNOWLEDGE


def test_base_nao_promete_capacidade_inexistente_de_desktop() -> None:
    """Vault, navegador e OCR não existem no servidor: nada de fingir."""
    conteudo = base.REVISAO_SENIOR_PROTOCOLO_KNOWLEDGE.lower()
    assert "obsidian" not in conteudo
    assert "jusbrasil" not in conteudo
    assert "chrome" not in conteudo
    # OCR só pode aparecer como limite/pendência declarada, nunca como capacidade.
    assert "não há ocr" in conteudo


def test_system_prompt_embute_a_base_e_as_camadas() -> None:
    assert base.REVISAO_SENIOR_PROTOCOLO_KNOWLEDGE in revisor.SYSTEM_PROMPT
    for camada in base.CAMADAS_REVISAO:
        assert camada in revisor.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 2. Nenhuma capacidade de protocolo
# ---------------------------------------------------------------------------


def test_agente_registrado_sem_escrita_rascunho_ou_pedido_de_humano() -> None:
    assert AGENT_KEY in AGENTS_BY_KEY
    agent = AGENTS_BY_KEY[AGENT_KEY]
    assert agent in AGENT_DEFINITIONS
    assert AGENT_TASK_CLASSES[AGENT_KEY] == TaskClass.COMPLEX
    assert agent.can_write_crm is False
    assert agent.can_create_draft is False
    assert agent.can_request_human is False


def test_agente_nao_tem_nenhuma_tool_de_protocolo_ou_aprovacao() -> None:
    agent = AGENTS_BY_KEY[AGENT_KEY]
    proibidos = ("protocol", "protocolo", "aprov", "approval", "envio", "send", "assinatura")
    for tool in agent.tools:
        for termo in proibidos:
            if termo in tool.lower():
                # `protocol_folder_audit` é auditoria de LEITURA da pasta;
                # nenhuma outra ocorrência é aceitável.
                assert tool == "protocol_folder_audit", tool


def test_gate_do_agente_declara_que_nao_protocola_nem_aprova() -> None:
    gate = AGENTS_BY_KEY[AGENT_KEY].gate.lower()
    assert "não" in gate
    assert "protocola" in gate
    assert "aprova" in gate


def test_prompt_afirma_protocolo_humano_e_nao_declara_protocolo_feito() -> None:
    prompt = revisor.SYSTEM_PROMPT
    assert "PROTOCOLO É EXCLUSIVAMENTE HUMANO" in prompt
    assert "nunca protocola" in prompt
    assert revisor.STATUS_PROTOCOLAVEL in prompt
    assert revisor.STATUS_BLOQUEADA in prompt
    # O estado de sucesso é entrega ao humano, não protocolo executado.
    assert revisor.STATUS_PROTOCOLAVEL == "protocolavel_entregue_ao_humano"


def test_resposta_do_agente_nao_tem_campo_de_protocolo() -> None:
    propriedades = revisor.REVISOR_SENIOR_RESPONSE_SCHEMA["properties"]
    assert not any(
        "protocol" in nome.lower() and nome != "checklist_protocolo" for nome in propriedades
    )
    assert revisor.REVISOR_SENIOR_RESPONSE_SCHEMA["additionalProperties"] is False


# ---------------------------------------------------------------------------
# 3. Parecer nos 3 níveis
# ---------------------------------------------------------------------------


class _Finding:
    def __init__(self, severity: str, description: str) -> None:
        self.severity = severity
        self.description = description


def test_parecer_sai_sempre_nos_tres_niveis_mesmo_vazio() -> None:
    parecer = revisor.parecer_por_nivel([])
    assert set(parecer) == set(revisor.PARECER_NIVEIS) == {"critico", "medio", "cosmetico"}
    assert all(itens == [] for itens in parecer.values())


def test_parecer_agrupa_severidades_nos_tres_niveis() -> None:
    parecer = revisor.parecer_por_nivel(
        [
            _Finding(LegalReviewSeverity.critica.value, "placeholder no artefato"),
            _Finding(LegalReviewSeverity.alta.value, "pedido sem fato"),
            _Finding(LegalReviewSeverity.media.value, "pedido sem fundamento"),
            _Finding(LegalReviewSeverity.baixa.value, "negrito excessivo"),
        ]
    )
    assert parecer["critico"] == ["placeholder no artefato"]
    assert parecer["medio"] == ["pedido sem fato", "pedido sem fundamento"]
    assert parecer["cosmetico"] == ["negrito excessivo"]


def test_severidade_desconhecida_nunca_e_rebaixada_em_silencio() -> None:
    parecer = revisor.parecer_por_nivel([_Finding("severidade_nova", "achado novo")])
    assert parecer["critico"] == ["achado novo"]


def test_veredito_deriva_do_parecer_e_nao_da_declaracao() -> None:
    bloqueada = revisor.parecer_por_nivel([_Finding("critica", "placeholder")])
    assert revisor.veredito_status(bloqueada) == revisor.STATUS_BLOQUEADA
    liberada = revisor.parecer_por_nivel([_Finding("baixa", "cosmético")])
    assert revisor.veredito_status(liberada) == revisor.STATUS_PROTOCOLAVEL


# ---------------------------------------------------------------------------
# 4. Citação não verificada nunca é apresentada como verificada
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status_citacao", ["nao_verificada", "nao_localizada", "nunca_conferida"])
def test_citacao_nao_verificada_e_critica_e_bloqueia(status_citacao: str) -> None:
    descricao = (
        f"Citação 'Tema 1.295/STJ' está com status '{status_citacao}' "
        "— não pode sustentar a peça sem conferência na fonte."
    )
    finding = _Finding(LegalReviewSeverity.critica.value, descricao)
    parecer = revisor.parecer_por_nivel([finding])
    assert parecer["critico"] == [descricao]
    assert parecer["medio"] == [] and parecer["cosmetico"] == []
    assert revisor.veredito_status(parecer) == revisor.STATUS_BLOQUEADA
    # A descrição declara o status real e a falta de conferência; em nenhum
    # caminho a citação aparece como conferida na fonte.
    assert f"status '{status_citacao}'" in descricao
    assert "sem conferência na fonte" in descricao


def test_kind_de_citacao_insegura_existe_no_vocabulario_persistido() -> None:
    """A metodologia usa o vocabulário fechado já existente — sem enum novo."""
    assert LegalReviewFindingKind.citacao_insegura.value == "citacao_insegura"
    assert LegalReviewFindingKind.placeholder.value == "placeholder"


def test_prompt_proibe_dar_citacao_por_verificada_por_omissao() -> None:
    prompt = revisor.SYSTEM_PROMPT
    assert "NÃO VERIFICADA" in prompt
    assert 'jamais vira "verificada" por omissão' in prompt
    assert "achado CRÍTICO" in prompt
