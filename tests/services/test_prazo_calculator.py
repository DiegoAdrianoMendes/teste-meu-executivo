"""Suíte de conferência do calculador de prazo (app/services/prazo_calculator.py).

Cada caso abaixo foi conferido à mão contra o calendário real de 2026/2027
(ver comentário por caso: dia da semana, feriado e/ou recesso aplicável) e
independentemente cross-checado com um segundo laço de contagem de dias
úteis escrito à parte (fora do módulo sob teste), antes de ser fixado aqui
como valor esperado.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.prazo_calculator import (
    OrigemIntimacao,
    TipoContagem,
    TipoParte,
    calcular_prazo,
    classificar_urgencia,
    dia_util,
    em_recesso,
    feriado_label,
    resolver_data_intimacao,
)


def _excluded_dates(resultado) -> set[str]:
    for passo in resultado.memoria_calculo:
        if "dias_excluidos" in passo:
            return {item["data"] for item in passo["dias_excluidos"]}
    return set()


# ── 1. Dias úteis simples (sem feriado nas proximidades) ──────────────────
def test_dias_uteis_simples_sem_feriado():
    # Disponibilização segunda 2026-08-03, consultada no mesmo dia.
    # Início da fluência: terça 2026-08-04. 5 dias úteis:
    # 04(ter,1) 05(qua,2) 06(qui,3) 07(sex,4) [08 sáb, 09 dom] 10(seg,5).
    resultado = calcular_prazo(
        data_disponibilizacao=date(2026, 8, 3),
        data_consulta=date(2026, 8, 3),
        dias=5,
        tipo_contagem=TipoContagem.dias_uteis,
    )
    assert resultado.data_inicio_fluencia == date(2026, 8, 4)
    assert resultado.data_fatal == date(2026, 8, 10)
    assert resultado.dobro_aplicado is False
    assert _excluded_dates(resultado) == {"2026-08-08", "2026-08-09"}


# ── 2. Publicação numa sexta-feira — início da fluência salta o fim de semana ──
def test_publicacao_na_sexta_inicia_fluencia_na_segunda():
    # Disponibilização sexta 2026-08-07; início da fluência = segunda 2026-08-10
    # (CPC 224 §2º pula sáb/dom). Contestação, 15 dias úteis (CPC 335):
    # fatal em 2026-08-28 (conferido dia a dia, sem feriado no intervalo).
    resultado = calcular_prazo(
        data_disponibilizacao=date(2026, 8, 7),
        data_consulta=date(2026, 8, 7),
        dias=15,
        tipo_contagem=TipoContagem.dias_uteis,
    )
    assert resultado.data_inicio_fluencia == date(2026, 8, 10)
    assert resultado.data_fatal == date(2026, 8, 28)


# ── 3. Feriado nacional no meio da contagem (Independência, 07/09, segunda) ──
def test_feriado_nacional_no_meio_da_contagem():
    # Disponibilização segunda 2026-08-31; início 2026-09-01 (terça).
    # 5 dias úteis: 01(1) 02(2) 03(3) 04(4) [05 sáb,06 dom] 07 feriado(Independência)
    # 08(5) -> fatal 2026-09-08.
    resultado = calcular_prazo(
        data_disponibilizacao=date(2026, 8, 31),
        data_consulta=date(2026, 8, 31),
        dias=5,
        tipo_contagem=TipoContagem.dias_uteis,
    )
    assert resultado.data_fatal == date(2026, 9, 8)
    excluidos = _excluded_dates(resultado)
    assert excluidos == {"2026-09-05", "2026-09-06", "2026-09-07"}
    passo_contagem = next(p for p in resultado.memoria_calculo if "dias_excluidos" in p)
    motivo_07 = next(
        item["motivo"] for item in passo_contagem["dias_excluidos"] if item["data"] == "2026-09-07"
    )
    assert "Independência" in motivo_07


# ── 4. Feriado em sexta-feira, emendando com o fim de semana ─────────────
def test_feriado_em_sexta_emenda_fim_de_semana():
    # Consciência Negra (20/11) cai numa sexta em 2026. Disponibilização
    # segunda 2026-11-16; início 2026-11-17 (terça). 5 dias úteis:
    # 17(1) 18(2) 19(3) [20 feriado] [21 sáb, 22 dom] 23(4) 24(5) -> fatal 24/11.
    resultado = calcular_prazo(
        data_disponibilizacao=date(2026, 11, 16),
        data_consulta=date(2026, 11, 16),
        dias=5,
        tipo_contagem=TipoContagem.dias_uteis,
    )
    assert resultado.data_fatal == date(2026, 11, 24)
    assert _excluded_dates(resultado) == {"2026-11-20", "2026-11-21", "2026-11-22"}


# ── 5. Carnaval (segunda, terça e quarta de cinzas — 3 dias não úteis) ────
def test_carnaval_exclui_tres_dias():
    # Carnaval 2026: segunda 16/02, terça 17/02, quarta de cinzas 18/02.
    # Disponibilização segunda 2026-02-09; início 2026-02-10 (terça).
    # 10 dias úteis: 10(1) 11(2) 12(3) 13(4) [14 sáb,15 dom] [16,17,18 carnaval]
    # 19(5) 20(6) [21 sáb,22 dom] 23(7) 24(8) 25(9) 26(10) -> fatal 26/02.
    resultado = calcular_prazo(
        data_disponibilizacao=date(2026, 2, 9),
        data_consulta=date(2026, 2, 9),
        dias=10,
        tipo_contagem=TipoContagem.dias_uteis,
    )
    assert resultado.data_fatal == date(2026, 2, 26)
    excluidos = _excluded_dates(resultado)
    assert {"2026-02-16", "2026-02-17", "2026-02-18"} <= excluidos


# ── 6. Recesso forense atravessado (20/12 a 20/01) ────────────────────────
def test_recesso_forense_atravessado():
    # Disponibilização segunda 2026-12-14; início 2026-12-15 (terça).
    # 10 dias úteis, atravessando integralmente o recesso de 20/12 a 20/01:
    # fatal conferido independentemente em 2027-01-28 (ver script de
    # verificação; todo o intervalo 19/12/2026-20/01/2027 é pulado por
    # fim de semana ou recesso).
    resultado = calcular_prazo(
        data_disponibilizacao=date(2026, 12, 14),
        data_consulta=date(2026, 12, 14),
        dias=10,
        tipo_contagem=TipoContagem.dias_uteis,
    )
    assert resultado.data_inicio_fluencia == date(2026, 12, 15)
    assert resultado.data_fatal == date(2027, 1, 28)
    excluidos = _excluded_dates(resultado)
    assert "2026-12-25" in excluidos  # Natal, dentro do recesso
    assert "2027-01-20" in excluidos  # último dia do recesso (CPC 220)
    assert "2027-01-21" not in excluidos  # 1º dia útil após o recesso, conta


# ── 7. Fazenda Pública em dobro (CPC 183) ─────────────────────────────────
def test_fazenda_publica_dobra_o_prazo():
    # Mesmo início do caso 1 (2026-08-04). Contestação 15 dias -> 30 em
    # dobro. Fatal conferido independentemente em 2026-09-15.
    resultado = calcular_prazo(
        data_disponibilizacao=date(2026, 8, 3),
        data_consulta=date(2026, 8, 3),
        dias=15,
        tipo_contagem=TipoContagem.dias_uteis,
        tipo_parte=TipoParte.fazenda_publica,
    )
    assert resultado.dobro_aplicado is True
    assert resultado.dias_efetivos == 30
    assert resultado.data_fatal == date(2026, 9, 15)
    regra_dobro = next(p for p in resultado.memoria_calculo if p.get("passo") == 2)
    assert regra_dobro["regra"] == "CPC 183"


# ── 8. Defensoria Pública em dobro (CPC 186) ──────────────────────────────
def test_defensoria_publica_dobra_o_prazo():
    resultado = calcular_prazo(
        data_disponibilizacao=date(2026, 8, 3),
        data_consulta=date(2026, 8, 3),
        dias=5,
        tipo_contagem=TipoContagem.dias_uteis,
        tipo_parte=TipoParte.defensoria_publica,
    )
    assert resultado.dias_efetivos == 10
    assert resultado.data_fatal == date(2026, 8, 17)
    regra_dobro = next(p for p in resultado.memoria_calculo if p.get("passo") == 2)
    assert regra_dobro["regra"] == "CPC 186"


# ── 9. CLT — recurso ordinário trabalhista, 8 dias úteis (pós Lei 13.467/2017) ──
def test_clt_recurso_ordinario_dias_uteis():
    # Disponibilização quinta 2026-10-01; início sexta 2026-10-02.
    # 8 dias úteis, atravessando o feriado de Nossa Senhora Aparecida
    # (12/10, segunda): fatal conferido independentemente em 2026-10-14.
    resultado = calcular_prazo(
        data_disponibilizacao=date(2026, 10, 1),
        data_consulta=date(2026, 10, 1),
        dias=8,
        tipo_contagem=TipoContagem.dias_uteis,
    )
    assert resultado.data_inicio_fluencia == date(2026, 10, 2)
    assert resultado.data_fatal == date(2026, 10, 14)
    assert "2026-10-12" in _excluded_dates(resultado)


# ── 10. CPP corridos simples — termo final já cai em dia útil ────────────
def test_cpp_dias_corridos_sem_prorrogacao():
    # Disponibilização terça 2026-10-06; início quarta 2026-10-07.
    # 10 dias corridos (CPP 396): bruta = 2026-10-16 (sexta, útil) -> sem
    # prorrogação.
    resultado = calcular_prazo(
        data_disponibilizacao=date(2026, 10, 6),
        data_consulta=date(2026, 10, 6),
        dias=10,
        tipo_contagem=TipoContagem.dias_corridos,
    )
    assert resultado.data_inicio_fluencia == date(2026, 10, 7)
    assert resultado.data_fatal == date(2026, 10, 16)
    assert not any("prorrogad" in p.get("descricao", "") for p in resultado.memoria_calculo[3:])


# ── 11. CPP corridos com prorrogação — termo final cai em fim de semana + feriado ──
def test_cpp_dias_corridos_com_prorrogacao():
    # Disponibilização segunda 2026-10-05; início terça 2026-10-06.
    # 5 dias corridos (apelação criminal, CPP 593): bruta = 2026-10-10
    # (sábado) -> prorroga por domingo (11) e pelo feriado de Nossa Senhora
    # Aparecida (12/10, segunda) -> fatal em 2026-10-13 (terça).
    resultado = calcular_prazo(
        data_disponibilizacao=date(2026, 10, 5),
        data_consulta=date(2026, 10, 5),
        dias=5,
        tipo_contagem=TipoContagem.dias_corridos,
    )
    assert resultado.data_fatal == date(2026, 10, 13)
    prorrogacao = next(
        p for p in resultado.memoria_calculo if "data_fatal_prorrogada" in p
    )
    assert prorrogacao["data_fatal_bruta"] == "2026-10-10"
    assert prorrogacao["data_fatal_prorrogada"] == "2026-10-13"


# ── 12. Intimação eletrônica consultada dentro de 10 dias (CPC 231) ───────
def test_intimacao_por_consulta_eletronica_dentro_do_prazo():
    resultado = calcular_prazo(
        data_disponibilizacao=date(2026, 8, 3),
        data_consulta=date(2026, 8, 5),
        dias=5,
        tipo_contagem=TipoContagem.dias_uteis,
    )
    assert resultado.origem_intimacao is OrigemIntimacao.consulta_eletronica
    assert resultado.data_intimacao == date(2026, 8, 5)


# ── 13. Ciência ficta — sem consulta em 10 dias corridos, opera no 11º dia ──
def test_ciencia_ficta_no_11o_dia_sem_consulta():
    data_intimacao, origem = resolver_data_intimacao(date(2026, 8, 3), None)
    assert origem is OrigemIntimacao.ciencia_ficta
    assert data_intimacao == date(2026, 8, 14)

    # Consulta feita depois do 11º dia: ciência ficta já operou antes —
    # prevalece a data da ficta, não a da consulta tardia.
    tarde, origem_tarde = resolver_data_intimacao(date(2026, 8, 3), date(2026, 8, 20))
    assert origem_tarde is OrigemIntimacao.ciencia_ficta
    assert tarde == date(2026, 8, 14)

    # Consulta exatamente no 11º dia: o "o que ocorrer primeiro" empata a
    # favor da consulta eletrônica (ela é o evento real; a ficta é o limite).
    no_limite, origem_limite = resolver_data_intimacao(date(2026, 8, 3), date(2026, 8, 14))
    assert origem_limite is OrigemIntimacao.consulta_eletronica
    assert no_limite == date(2026, 8, 14)


# ── 14. Classificação de urgência (dossiê seção 9) ────────────────────────
@pytest.mark.parametrize(
    "tipo_ato,dias,esperado",
    [
        ("contestacao", 15, "critica"),
        ("embargos_declaracao", 5, "critica"),  # crítica pela lista, não pelo nº de dias
        ("agravo_instrumento", 15, "critica"),
        ("recurso_especial", 15, "alta"),
        ("replica", 15, "alta"),
        (None, 20, "alta"),
        (None, 45, "media"),
        (None, 0, "baixa"),
    ],
)
def test_classificar_urgencia(tipo_ato, dias, esperado):
    assert classificar_urgencia(tipo_ato=tipo_ato, dias_uteis_equivalentes=dias) == esperado


# ── Helpers de calendário — casos de fronteira ────────────────────────────
def test_quarta_de_cinzas_tratada_como_feriado_integral():
    assert feriado_label(date(2026, 2, 18)) is not None
    assert "Cinzas" in feriado_label(date(2026, 2, 18))
    assert dia_util(date(2026, 2, 18)) is False


def test_fronteiras_do_recesso_forense():
    assert em_recesso(date(2026, 12, 19)) is False
    assert em_recesso(date(2026, 12, 20)) is True
    assert em_recesso(date(2027, 1, 20)) is True
    assert em_recesso(date(2027, 1, 21)) is False


def test_dias_zero_ou_negativos_sao_rejeitados():
    with pytest.raises(ValueError):
        calcular_prazo(
            data_disponibilizacao=date(2026, 8, 3),
            dias=0,
            tipo_contagem=TipoContagem.dias_uteis,
        )
