"""Alias do índice público do DataJud derivado do número CNJ.

O número único (Res. CNJ 65/2008, art. 1º) traz o segmento (J) e o tribunal (TR).
Isso basta para saber em qual `api_publica_<alias>` consultar, sem pedir ao
advogado que conheça o nome interno do índice do CNJ e sem um campo de texto
livre que monta URL e dá 404.

Fontes, lidas em 03/09/2026:
- aliases: https://datajud-wiki.cnj.jus.br/api-publica/endpoints/ (91 índices);
- J e TR: https://atos.cnj.jus.br/atos/detalhar/119, art. 1º §4º e §5º. TJ e TRE
  numerados de 01 a 27 pela ordem alfabética dos Estados e do DF; o inciso VIII
  fixa os Tribunais Militares em 13 (MG), 21 (RS) e 26 (SP), o que ancora SE=25
  e SP=26 nessa ordem.
"""
from __future__ import annotations

import re

from app.services.cnj_numero import InvalidCnjNumberError, normalizar_numero_cnj

# TR de 01 a 27, na ordem alfabética que a Resolução manda.
_UF_POR_TR: dict[str, str] = {
    "01": "ac", "02": "al", "03": "ap", "04": "am", "05": "ba", "06": "ce", "07": "dft",
    "08": "es", "09": "go", "10": "ma", "11": "mt", "12": "ms", "13": "mg", "14": "pa",
    "15": "pb", "16": "pr", "17": "pe", "18": "pi", "19": "rj", "20": "rn", "21": "rs",
    "22": "ro", "23": "rr", "24": "sc", "25": "se", "26": "sp", "27": "to",
}
_TJM_POR_TR: dict[str, str] = {"13": "tjmmg", "21": "tjmrs", "26": "tjmsp"}

ALIASES_DATAJUD: frozenset[str] = frozenset(
    ["tst", "tse", "stj", "stm"]
    + [f"trf{n}" for n in range(1, 7)]
    + [f"tj{uf}" for uf in _UF_POR_TR.values()]
    + [f"trt{n}" for n in range(1, 25)]
    + [f"tre-{uf}" for uf in _UF_POR_TR.values()]
    + list(_TJM_POR_TR.values())
)
assert len(ALIASES_DATAJUD) == 91, "a wiki do DataJud lista 91 índices"

_NAO_COBERTO = "Tribunal não coberto pelo DataJud"


def alias_datajud_do_cnj(numero_cnj: str) -> tuple[str | None, str | None]:
    """Devolve `(alias, motivo)`: alias quando o DataJud cobre o tribunal; senão
    `(None, motivo)` com o motivo em português, para ir direto ao `last_error`.
    """
    try:
        canonico = normalizar_numero_cnj(numero_cnj)
    except InvalidCnjNumberError as error:
        return None, str(error)
    justica, tr = canonico[16:17], canonico[18:20]

    if justica == "3" and tr == "00":
        return "stj", None
    if justica == "4":
        if tr == "90":
            return None, f"{_NAO_COBERTO} (Conselho da Justiça Federal, 4.90)"
        return (f"trf{int(tr)}", None) if 1 <= int(tr) <= 6 else (None, f"{_NAO_COBERTO} (4.{tr})")
    if justica == "5":
        if tr == "00":
            return "tst", None
        if tr == "90":
            return None, f"{_NAO_COBERTO} (Conselho Superior da Justiça do Trabalho, 5.90)"
        return (f"trt{int(tr)}", None) if 1 <= int(tr) <= 24 else (None, f"{_NAO_COBERTO} (5.{tr})")
    if justica == "6":
        if tr == "00":
            return "tse", None
        uf = _UF_POR_TR.get(tr)
        return (f"tre-{uf}", None) if uf else (None, f"{_NAO_COBERTO} (6.{tr})")
    if justica == "7":
        # A wiki só publica um índice para a Justiça Militar da União (`stm`).
        # As Circunscrições (TR 01 a 12) não têm índice próprio; assume-se que o
        # acervo delas está sob o STM. Não verificado contra resposta real.
        if tr == "00" or 1 <= int(tr) <= 12:
            return "stm", None
        return None, f"{_NAO_COBERTO} (7.{tr})"
    if justica == "8":
        uf = _UF_POR_TR.get(tr)
        return (f"tj{uf}", None) if uf else (None, f"{_NAO_COBERTO} (8.{tr})")
    if justica == "9":
        alias = _TJM_POR_TR.get(tr)
        return (alias, None) if alias else (None, f"{_NAO_COBERTO} (9.{tr})")
    if justica == "1":
        return None, f"{_NAO_COBERTO} (Supremo Tribunal Federal)"
    if justica == "2":
        return None, f"{_NAO_COBERTO} (Conselho Nacional de Justiça)"
    return None, f"{_NAO_COBERTO} ({justica}.{tr})"


def normalizar_alias_tribunal(texto: str | None) -> str | None:
    """`TJDFT`, `tjdft`, `api_publica_tjdft`, `TRE-DFT` → alias; qualquer outra coisa → None.

    Linhas antigas têm texto livre aqui ("Tribunal de Justiça do DF"). Isso não é
    alias e não vira alias por adivinhação: o chamador recalcula pelo CNJ.
    """
    if not texto:
        return None
    candidato = re.sub(r"^api_publica_", "", texto.strip().lower())
    return candidato if candidato in ALIASES_DATAJUD else None


__all__ = ["ALIASES_DATAJUD", "alias_datajud_do_cnj", "normalizar_alias_tribunal"]
