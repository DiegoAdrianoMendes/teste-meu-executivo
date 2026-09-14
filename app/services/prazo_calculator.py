"""Calculadora determinística de prazo processual — CPC/CLT/CPP/CE.

Código puro (sem I/O, sem rede, sem estado): toda regra aqui é uma tradução
literal do dossiê verificado em
`app/agents/knowledge/processual_monitoramento.py` (seções 4 a 10) — este
módulo NÃO infere regra, feriado ou canal que não esteja no dossiê; qualquer
ajuste de regra exige atualizar primeiro o dossiê de origem.

Toda saída carrega uma `memoria_calculo` passo a passo, auditável, com a lei
que sustenta cada etapa (dossiê, regra 10). A data fatal aqui é sempre
"sugerida" — nunca é gravada como compromisso oficial sem confirmação humana
explícita (ver `app/services/process_monitoring_service.py::confirm_deadline`).

Premissas explícitas assumidas para uma calculadora de granularidade diária
(sem hora), a confirmar caso a caso pelo advogado responsável ao revisar a
memória de cálculo:
  * Quarta-feira de Cinzas é tratada como dia INTEGRAL não útil (o dossiê a
    lista junto dos feriados nacionais, apesar do texto legal falar em "até
    meio-dia" — na prática, a maioria dos tribunais fecha o expediente do
    dia inteiro). Tratar o contrário (útil pela manhã) arriscaria calcular
    uma data fatal POSTERIOR à real — a direção perigosa.
  * Prazos em dias corridos (Penal/Eleitoral) que vencem em dia não útil são
    prorrogados para o próximo dia útil (doutrina uniforme, CPP art. 798
    §3º por analogia expressa aos demais ritos corridos).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

PRAZO_CALCULATOR_KNOWLEDGE_VERSION = "processual-monitoramento-knowledge-2026-07"


class TipoContagem(StrEnum):
    dias_uteis = "dias_uteis"
    dias_corridos = "dias_corridos"


class TipoParte(StrEnum):
    padrao = "padrao"
    fazenda_publica = "fazenda_publica"
    defensoria_publica = "defensoria_publica"


class OrigemIntimacao(StrEnum):
    consulta_eletronica = "consulta_eletronica"
    ciencia_ficta = "ciencia_ficta"


# ── Feriados nacionais fixos (Lei 662/1949, Lei 6.802/1980, Lei 10.607/2002,
# Lei 14.759/2023) — dossiê seção 8. ──────────────────────────────────────
FERIADOS_NACIONAIS_FIXOS: dict[tuple[int, int], str] = {
    (1, 1): "Confraternização Universal (Lei 662/1949)",
    (4, 21): "Tiradentes (Lei 662/1949)",
    (5, 1): "Dia do Trabalho (Lei 662/1949)",
    (9, 7): "Independência (Lei 662/1949)",
    (10, 12): "Nossa Senhora Aparecida (Lei 6.802/1980)",
    (11, 2): "Finados (Lei 662/1949)",
    (11, 15): "República (Lei 662/1949)",
    (11, 20): "Consciência Negra (Lei 14.759/2023)",
    (12, 25): "Natal (Lei 662/1949)",
}

# Recesso forense (CPC 220): 20/12 a 20/01, inclusive — atravessa o ano novo.
_RECESSO_INICIO = (12, 20)
_RECESSO_FIM = (1, 20)

TIPOS_ATO_CRITICOS = frozenset(
    {
        "contestacao",
        "embargos_execucao",
        "apelacao",
        "agravo_instrumento",
        "embargos_declaracao",
    }
)
TIPOS_ATO_ALTA = frozenset(
    {
        "recurso_especial",
        "recurso_extraordinario",
        "replica",
        "manifestacao_laudo",
    }
)


def _pascoa(ano: int) -> date:
    """Domingo de Páscoa pelo algoritmo de Meeus/Jones/Butcher (gregoriano)."""
    a = ano % 19
    b = ano // 100
    c = ano % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def _feriados_moveis(ano: int) -> dict[date, str]:
    pascoa = _pascoa(ano)
    return {
        pascoa - timedelta(days=48): "Segunda-feira de Carnaval (Lei 14.759/2023)",
        pascoa - timedelta(days=47): "Terça-feira de Carnaval (Lei 14.759/2023)",
        pascoa
        - timedelta(days=46): (
            "Quarta-feira de Cinzas — tratada como dia integral não útil nesta "
            "calculadora (Lei 14.759/2023, texto legal fala em \"até meio-dia\")"
        ),
        pascoa - timedelta(days=2): "Sexta-feira Santa (Lei 662/1949)",
    }


def feriado_label(dia: date) -> str | None:
    """Retorna o nome do feriado nacional (fixo ou móvel) para a data, se houver."""
    fixo = FERIADOS_NACIONAIS_FIXOS.get((dia.month, dia.day))
    if fixo is not None:
        return fixo
    return _feriados_moveis(dia.year).get(dia)


def em_recesso(dia: date) -> bool:
    """CPC 220 — recesso forense de 20/12 a 20/01, inclusive."""
    if dia.month == 12 and dia.day >= _RECESSO_INICIO[1]:
        return True
    if dia.month == 1 and dia.day <= _RECESSO_FIM[1]:
        return True
    return False


def dia_util(dia: date) -> bool:
    if dia.weekday() >= 5:  # sábado=5, domingo=6
        return False
    if em_recesso(dia):
        return False
    return feriado_label(dia) is None


def motivo_nao_util(dia: date) -> str:
    if dia.weekday() == 5:
        return "sábado"
    if dia.weekday() == 6:
        return "domingo"
    if em_recesso(dia):
        return "recesso forense (CPC 220, 20/12 a 20/01)"
    feriado = feriado_label(dia)
    if feriado is not None:
        return feriado
    raise ValueError(f"{dia.isoformat()} é dia útil — sem motivo de exclusão.")


def _proximo_dia_util_estritamente_apos(dia: date) -> date:
    cursor = dia + timedelta(days=1)
    while not dia_util(cursor):
        cursor += timedelta(days=1)
    return cursor


def _prorrogar_se_nao_util(dia: date) -> date:
    cursor = dia
    while not dia_util(cursor):
        cursor += timedelta(days=1)
    return cursor


def _avancar_dias_uteis(inicio: date, quantidade: int) -> tuple[date, list[date]]:
    """Conta `quantidade` dias úteis a partir de `inicio` (inclusive).

    `inicio` já deve ser um dia útil. Retorna a data final e a lista de dias
    NÃO úteis pulados no caminho (para a memória de cálculo).
    """
    if quantidade <= 0:
        raise ValueError("quantidade de dias deve ser positiva.")
    excluidos: list[date] = []
    cursor = inicio
    contados = 1
    while contados < quantidade:
        cursor += timedelta(days=1)
        if dia_util(cursor):
            contados += 1
        else:
            excluidos.append(cursor)
    return cursor, excluidos


def resolver_data_intimacao(
    data_disponibilizacao: date,
    data_consulta: date | None = None,
) -> tuple[date, OrigemIntimacao]:
    """Lei 11.419/2006 art. 5º §1º / CPC 231.

    A intimação se considera realizada na data da consulta eletrônica; se não
    consultada em 10 dias corridos, opera ciência ficta no 11º dia da
    disponibilização — o que ocorrer primeiro.
    """
    ficta = data_disponibilizacao + timedelta(days=11)
    if data_consulta is not None and data_consulta <= ficta:
        return data_consulta, OrigemIntimacao.consulta_eletronica
    return ficta, OrigemIntimacao.ciencia_ficta


def classificar_urgencia(
    *,
    tipo_ato: str | None,
    dias_uteis_equivalentes: int,
) -> str:
    """Dossiê seção 9 — tabela de urgência por tipo de ato, com fallback por dias."""
    if tipo_ato:
        normalizado = tipo_ato.strip().lower()
        if normalizado in TIPOS_ATO_CRITICOS:
            return "critica"
        if normalizado in TIPOS_ATO_ALTA:
            return "alta"
    if dias_uteis_equivalentes <= 0:
        return "baixa"
    if dias_uteis_equivalentes <= 15:
        return "critica"
    if dias_uteis_equivalentes <= 30:
        return "alta"
    return "media"


@dataclass(frozen=True, slots=True)
class ResultadoCalculoPrazo:
    data_intimacao: date
    origem_intimacao: OrigemIntimacao
    data_inicio_fluencia: date
    dias_solicitados: int
    dias_efetivos: int
    dobro_aplicado: bool
    tipo_contagem: TipoContagem
    data_fatal: date
    memoria_calculo: list[dict] = field(default_factory=list)


def calcular_prazo(
    *,
    data_disponibilizacao: date,
    dias: int,
    tipo_contagem: TipoContagem,
    tipo_parte: TipoParte = TipoParte.padrao,
    data_consulta: date | None = None,
) -> ResultadoCalculoPrazo:
    """Calcula a data fatal SUGERIDA de um prazo, com memória de cálculo completa.

    Nunca lançar a data retornada como compromisso fatal sem confirmação
    humana explícita — ver dossiê seção 10 e
    `ProcessMonitoringService.confirm_deadline`.
    """
    if dias <= 0:
        raise ValueError("dias deve ser positivo.")

    memoria: list[dict] = []

    data_intimacao, origem = resolver_data_intimacao(data_disponibilizacao, data_consulta)
    memoria.append(
        {
            "passo": 1,
            "regra": "Lei 11.419/2006 art. 5º §1º; CPC 231",
            "descricao": (
                "Data da intimação: consulta eletrônica se dentro de 10 dias "
                "corridos da disponibilização; senão, ciência ficta no 11º dia "
                "— o que ocorrer primeiro."
            ),
            "data_disponibilizacao": data_disponibilizacao.isoformat(),
            "data_consulta": data_consulta.isoformat() if data_consulta else None,
            "origem": origem.value,
            "data_intimacao_resultante": data_intimacao.isoformat(),
        }
    )

    dias_efetivos = dias
    dobro_aplicado = tipo_parte is not TipoParte.padrao
    if dobro_aplicado:
        dias_efetivos = dias * 2
        regra_dobro = "CPC 183" if tipo_parte is TipoParte.fazenda_publica else "CPC 186"
        memoria.append(
            {
                "passo": 2,
                "regra": regra_dobro,
                "descricao": (
                    f"Prazo em dobro para {tipo_parte.value.replace('_', ' ')}: "
                    f"{dias} dias passam a {dias_efetivos} dias."
                ),
                "dias_originais": dias,
                "dias_em_dobro": dias_efetivos,
            }
        )

    inicio_fluencia = _proximo_dia_util_estritamente_apos(data_intimacao)
    memoria.append(
        {
            "passo": len(memoria) + 1,
            "regra": "CPC 224 §2º",
            "descricao": (
                "Início da contagem no 1º dia útil seguinte à intimação, "
                "excluindo o dia da própria intimação."
            ),
            "data_intimacao": data_intimacao.isoformat(),
            "data_inicio_fluencia": inicio_fluencia.isoformat(),
        }
    )

    if tipo_contagem is TipoContagem.dias_uteis:
        data_fatal, excluidos = _avancar_dias_uteis(inicio_fluencia, dias_efetivos)
        memoria.append(
            {
                "passo": len(memoria) + 1,
                "regra": "CPC 219",
                "descricao": (
                    f"Contagem em {dias_efetivos} dias úteis a partir de "
                    f"{inicio_fluencia.isoformat()} (inclusive), excluindo "
                    "sábados, domingos, feriados e recesso forense (CPC 220)."
                ),
                "dias_uteis_contados": dias_efetivos,
                "dias_excluidos": [
                    {"data": dia.isoformat(), "motivo": motivo_nao_util(dia)}
                    for dia in excluidos
                ],
                "data_fatal": data_fatal.isoformat(),
            }
        )
    else:
        data_fatal_bruta = inicio_fluencia + timedelta(days=dias_efetivos - 1)
        data_fatal = _prorrogar_se_nao_util(data_fatal_bruta)
        memoria.append(
            {
                "passo": len(memoria) + 1,
                "regra": "CPP arts. 798-800; CE 184",
                "descricao": (
                    f"Contagem em {dias_efetivos} dias corridos a partir de "
                    f"{inicio_fluencia.isoformat()} (inclusive), sem exclusão de "
                    "fins de semana ou feriados."
                ),
                "dias_corridos_contados": dias_efetivos,
                "data_fatal_bruta": data_fatal_bruta.isoformat(),
            }
        )
        if data_fatal != data_fatal_bruta:
            memoria.append(
                {
                    "passo": len(memoria) + 1,
                    "regra": "CPP art. 798 §3º (prorrogação, por analogia aos ritos corridos)",
                    "descricao": (
                        "O termo final caiu em dia não útil "
                        f"({motivo_nao_util(data_fatal_bruta)}); prorrogado para o "
                        "próximo dia útil."
                    ),
                    "data_fatal_bruta": data_fatal_bruta.isoformat(),
                    "data_fatal_prorrogada": data_fatal.isoformat(),
                }
            )

    return ResultadoCalculoPrazo(
        data_intimacao=data_intimacao,
        origem_intimacao=origem,
        data_inicio_fluencia=inicio_fluencia,
        dias_solicitados=dias,
        dias_efetivos=dias_efetivos,
        dobro_aplicado=dobro_aplicado,
        tipo_contagem=tipo_contagem,
        data_fatal=data_fatal,
        memoria_calculo=memoria,
    )
