"""Alias do DataJud derivado do número CNJ (Res. CNJ 65/2008 + wiki do DataJud)."""
from __future__ import annotations

import pytest

from app.services.cnj_numero import _digito_verificador
from app.services.tribunal_datajud import (
    ALIASES_DATAJUD,
    alias_datajud_do_cnj,
    normalizar_alias_tribunal,
)


def _cnj(
    j: str, tr: str, origem: str = "0001", seq: str = "0000001", ano: str = "2026"
) -> str:
    return f"{seq}-{_digito_verificador(seq, ano, j, tr, origem)}.{ano}.{j}.{tr}.{origem}"


def test_a_wiki_lista_91_indices() -> None:
    assert len(ALIASES_DATAJUD) == 91
    assert "tre-dft" in ALIASES_DATAJUD  # com T: a wiki grafa assim, e não tre-df
    assert "tjdft" in ALIASES_DATAJUD


@pytest.mark.parametrize(
    ("j", "tr", "alias"),
    [
        ("8", "07", "tjdft"),   # ancora observada em respostas reais (8.07 = TJDFT)
        ("8", "26", "tjsp"),    # Res. 65/2008 art. 1º §5º VIII fixa SP=26
        ("8", "25", "tjse"),
        ("8", "19", "tjrj"),
        ("8", "13", "tjmg"),
        ("8", "21", "tjrs"),
        ("8", "03", "tjap"),
        ("8", "04", "tjam"),
        ("8", "11", "tjmt"),
        ("8", "12", "tjms"),
        ("6", "07", "tre-dft"),
        ("6", "00", "tse"),
        ("5", "10", "trt10"),
        ("5", "00", "tst"),
        ("4", "01", "trf1"),
        ("4", "06", "trf6"),
        ("3", "00", "stj"),
        ("9", "26", "tjmsp"),
        ("9", "13", "tjmmg"),
        ("7", "01", "stm"),
    ],
)
def test_deriva_o_alias_do_j_tr(j: str, tr: str, alias: str) -> None:
    assert alias_datajud_do_cnj(_cnj(j, tr)) == (alias, None)


@pytest.mark.parametrize(
    ("j", "tr"), [("1", "00"), ("2", "00"), ("4", "90"), ("5", "90"), ("8", "28")]
)
def test_nao_coberto_vem_com_motivo_e_sem_alias(j: str, tr: str) -> None:
    alias, motivo = alias_datajud_do_cnj(_cnj(j, tr, origem="0000"))
    assert alias is None
    assert motivo and "não coberto" in motivo


def test_numero_invalido_devolve_o_motivo_do_validador() -> None:
    alias, motivo = alias_datajud_do_cnj("0000001-00.2026.8.26.0100")
    assert alias is None
    assert motivo and "Dígito verificador" in motivo


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("TJDFT", "tjdft"),
        ("tjdft", "tjdft"),
        ("api_publica_tjsp", "tjsp"),
        ("TRE-DFT", "tre-dft"),
        ("Tribunal de Justiça do DF", None),  # texto livre não vira alias por adivinhação
        ("TJ-DFT", None),
        ("", None),
        (None, None),
    ],
)
def test_normaliza_alias_informado(texto, esperado) -> None:
    assert normalizar_alias_tribunal(texto) == esperado


def test_jmu_fora_da_faixa_de_circunscricoes_nao_e_coberta() -> None:
    alias, motivo = alias_datajud_do_cnj(_cnj("7", "99"))
    assert alias is None and motivo and "7.99" in motivo
