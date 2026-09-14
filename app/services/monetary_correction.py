"""
Serviço: correção monetária (F9) — cálculo puro, sem rede e sem banco.

`calc_atualizacao` aplica, em cima do valor original:
  1. Correção monetária mensal composta a partir dos índices informados
     (`indices`, injetados pelo chamador — este módulo nunca busca índice
     em fonte externa).
  2. Juros de mora simples ao mês (`juros_am`, sobre o valor original,
     pro-rata pelos meses decorridos entre `vencimento` e `referencia`).
  3. Multa moratória única (`multa_pct`, sobre o valor original), aplicada
     apenas se `referencia` for posterior a `vencimento`.

Limites fail-closed (Lei 14.905/CDC art. 52 §1º): `multa_pct` <= 2 e
`juros_am` <= 1 — ambos em pontos percentuais (ex.: `Decimal("2")` = 2%).
Violar o limite é erro do chamador, nunca corrigido silenciosamente:
`ValueError`.

Meses decorridos são contados por diferença de ano/mês entre `vencimento` e
`referencia` (sem fração de dia) — mesma simplificação usada nas demais
premissas financeiras do módulo (ver `finance_charge_service._add_months`,
reaproveitado aqui para localizar a competência de cada índice mensal).
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Mapping

from app.services.finance_charge_service import _add_months

CENTS = Decimal("0.01")
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
MAX_MULTA_PCT = Decimal("2")
MAX_JUROS_AM = Decimal("1")


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def _months_elapsed(vencimento: date, referencia: date) -> int:
    diff = (referencia.year - vencimento.year) * 12 + (referencia.month - vencimento.month)
    if referencia.day < vencimento.day:
        diff -= 1
    return max(diff, 0)


def calc_atualizacao(
    valor: Decimal,
    vencimento: date,
    referencia: date,
    indices: Mapping[str, Decimal],
    multa_pct: Decimal,
    juros_am: Decimal,
) -> dict:
    """Calcula o valor atualizado de um débito vencido, com memória de cálculo.

    `indices` é um dict `{"AAAA-MM": taxa_mensal}` (ex.: `{"2026-02":
    Decimal("0.005")}` para 0,5% no mês). Uma competência ausente do dict é
    tratada como índice zero naquele mês (o chamador decide o que injetar).

    Retorna `{"valor_atualizado": Decimal, "memoria_calculo": {...}}`.
    """
    if multa_pct > MAX_MULTA_PCT:
        raise ValueError(
            f"Multa moratória de {multa_pct}% excede o limite legal de {MAX_MULTA_PCT}%."
        )
    if juros_am > MAX_JUROS_AM:
        raise ValueError(
            f"Juros de mora de {juros_am}% a.m. excedem o limite de {MAX_JUROS_AM}% a.m."
        )
    if multa_pct < _ZERO or juros_am < _ZERO:
        raise ValueError("Multa e juros de mora não podem ser negativos.")
    if valor < _ZERO:
        raise ValueError("Valor original não pode ser negativo.")

    months_elapsed = _months_elapsed(vencimento, referencia)

    indices_aplicados: dict[str, Decimal] = {}
    correction_factor = Decimal("1")
    for month_index in range(1, months_elapsed + 1):
        competence = _add_months(vencimento, month_index)
        key = f"{competence.year:04d}-{competence.month:02d}"
        rate = Decimal(indices.get(key, _ZERO))
        indices_aplicados[key] = rate
        correction_factor *= Decimal("1") + rate

    valor_corrigido = valor * correction_factor
    valor_juros = valor * (juros_am / _HUNDRED) * months_elapsed
    valor_multa = valor * (multa_pct / _HUNDRED) if months_elapsed > 0 else _ZERO

    valor_atualizado = _quantize(valor_corrigido + valor_juros + valor_multa)

    memoria_calculo = {
        "1_valor_original": {
            "descricao": "Valor original do débito.",
            "valor": str(_quantize(valor)),
        },
        "2_meses_em_atraso": {
            "descricao": "Meses decorridos entre o vencimento e a data de referência.",
            "vencimento": vencimento.isoformat(),
            "referencia": referencia.isoformat(),
            "meses": months_elapsed,
        },
        "3_correcao_monetaria": {
            "descricao": "Correção monetária composta pelos índices informados por competência.",
            "indices_aplicados": {k: str(v) for k, v in indices_aplicados.items()},
            "fator_composto": str(correction_factor),
            "valor_corrigido": str(_quantize(valor_corrigido)),
        },
        "4_juros_de_mora": {
            "descricao": "Juros simples de mora sobre o valor original, pro-rata mensal.",
            "taxa_am_pct": str(juros_am),
            "valor_juros": str(_quantize(valor_juros)),
        },
        "5_multa_moratoria": {
            "descricao": "Multa moratória única sobre o valor original (só incide se vencido).",
            "percentual": str(multa_pct),
            "valor_multa": str(_quantize(valor_multa)),
        },
        "6_valor_atualizado": {
            "descricao": "Soma de valor corrigido + juros de mora + multa.",
            "valor": str(valor_atualizado),
        },
    }

    return {
        "valor_atualizado": valor_atualizado,
        "memoria_calculo": memoria_calculo,
    }
