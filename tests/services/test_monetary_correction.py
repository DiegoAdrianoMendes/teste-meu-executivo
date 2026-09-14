from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.monetary_correction import calc_atualizacao


def test_calc_atualizacao_matches_manual_reference_case():
    """Caso de referência calculado à mão:

    R$ 1000,00, vencido há 3 meses, juros de mora 1% a.m., multa 2%,
    índice de correção fixo de 0,5%/mês (Fev/Mar/Abr de 2026).

    fator de correção = 1.005^3 = 1.015075125
    valor corrigido    = 1000 * 1.015075125 = 1015.075125
    juros              = 1000 * 0.01 * 3     = 30.00
    multa               = 1000 * 0.02         = 20.00
    total               = 1015.075125 + 30 + 20 = 1065.075125 -> 1065.08
    """
    result = calc_atualizacao(
        Decimal("1000.00"),
        date(2026, 1, 25),
        date(2026, 4, 25),
        {
            "2026-02": Decimal("0.005"),
            "2026-03": Decimal("0.005"),
            "2026-04": Decimal("0.005"),
        },
        Decimal("2"),
        Decimal("1"),
    )

    assert result["valor_atualizado"] == Decimal("1065.08")
    memoria = result["memoria_calculo"]
    assert memoria["2_meses_em_atraso"]["meses"] == 3
    assert memoria["4_juros_de_mora"]["valor_juros"] == "30.00"
    assert memoria["5_multa_moratoria"]["valor_multa"] == "20.00"
    assert memoria["3_correcao_monetaria"]["valor_corrigido"] == "1015.08"


def test_calc_atualizacao_no_delay_returns_original_value():
    result = calc_atualizacao(
        Decimal("500.00"),
        date(2026, 1, 25),
        date(2026, 1, 25),
        {},
        Decimal("2"),
        Decimal("1"),
    )
    assert result["valor_atualizado"] == Decimal("500.00")
    assert result["memoria_calculo"]["2_meses_em_atraso"]["meses"] == 0


def test_calc_atualizacao_missing_index_competence_defaults_to_zero():
    result = calc_atualizacao(
        Decimal("1000.00"),
        date(2026, 1, 25),
        date(2026, 3, 25),
        {"2026-02": Decimal("0.01")},
        Decimal("0"),
        Decimal("0"),
    )
    # Só a competência de fevereiro tem índice; março fica em 0.
    assert result["valor_atualizado"] == Decimal("1010.00")


@pytest.mark.parametrize(
    "multa_pct,juros_am",
    [
        (Decimal("2.01"), Decimal("1")),
        (Decimal("2"), Decimal("1.01")),
        (Decimal("3"), Decimal("2")),
    ],
)
def test_calc_atualizacao_rejects_limits_above_legal_cap(multa_pct, juros_am):
    with pytest.raises(ValueError):
        calc_atualizacao(
            Decimal("1000.00"),
            date(2026, 1, 25),
            date(2026, 4, 25),
            {},
            multa_pct,
            juros_am,
        )


def test_calc_atualizacao_rejects_negative_inputs():
    with pytest.raises(ValueError):
        calc_atualizacao(
            Decimal("1000.00"),
            date(2026, 1, 25),
            date(2026, 4, 25),
            {},
            Decimal("-1"),
            Decimal("0"),
        )
