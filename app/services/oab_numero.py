"""Inscrição na OAB na forma canônica `N/UF`, como o worker consulta o DJEN.

O Comunica (DJEN) consulta por `numeroOab` + `ufOab`. Conferido contra a API em
03/09/2026: o número vem como string sem zero à esquerda (`"68812"`), a API
tolera zero à esquerda na consulta, e uma inscrição com letra (`68812A`,
`68812-A`) **não é recusada: devolve `success` com zero itens**. Por isso a
letra é recusada aqui, na captura: se passasse, o worker receberia lista vazia,
limparia o erro e o processo ficaria verde sem nada monitorado. Não verificado
se inscrição suplementar ("A") ou de estagiário ("E") aparece com a letra em
`destinatarioadvogados`; a recusa é a decisão segura enquanto isso não se sabe.

Canônica: 1 a 7 dígitos sem zero à esquerda, barra, UF entre as 27.
Entrada suja é canonizada por tokens, e não por regex sobre a string limpa:
`OAB/DF nº 123.456` tem um "n" que não é UF nem letra de inscrição.
"""
from __future__ import annotations

import re

UFS: frozenset[str] = frozenset(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SE SP TO".split()
)
_TOKENS_IGNORADOS = {"OAB", "º", "°", "INSCRICAO", "INSCRIÇÃO", "INSC"}
# "nº", "n.º", "no", "n." só valem como prefixo do número. Fora dessa posição,
# um N é letra de inscrição e tem de ser recusado como qualquer outra.
_PREFIXO_NUMERO = re.compile(r"\bN(?:O\b|\.?[º°]|\.)?\s*(?=[0-9])")
_MAX_ENTRADA = 40


class InvalidOabError(ValueError):
    pass


def normalizar_oab(valor: str) -> str:
    """Devolve `N/UF` ou levanta `InvalidOabError` com mensagem para o usuário."""
    if not isinstance(valor, str) or not valor.strip():
        raise InvalidOabError("Informe a OAB no formato 123456/UF.")
    bruto = " ".join(valor.split())  # colapsa espaço antes de qualquer corte
    if len(bruto) > _MAX_ENTRADA:
        raise InvalidOabError("OAB longa demais. Informe só o número e a UF, no formato 123456/UF.")
    bruto = bruto.upper()
    # ponto de milhar dentro do número ("123.456") não separa; espaço separa
    bruto = re.sub(r"(?<=[0-9])\.(?=[0-9])", "", bruto)
    bruto = _PREFIXO_NUMERO.sub(" ", bruto)
    # uma letra colada DEPOIS do número ("123456A") é letra de inscrição, não UF;
    # decidido antes de separar, porque a tokenização perde a adjacência
    letra_colada = re.search(r"[0-9][A-Z](?![A-Z])", bruto) is not None
    # separa dígitos de letras coladas ("SP123456", "123456SP") e troca pontuação por espaço
    bruto = re.sub(r"(?<=[0-9])(?=[A-ZÇº°])|(?<=[A-ZÇº°])(?=[0-9])", " ", bruto)
    tokens = [t for t in re.split(r"[\s/.\-,:;()]+", bruto) if t]
    tokens = [t for t in tokens if t not in _TOKENS_IGNORADOS]

    def _e_numero(t: str) -> bool:
        return re.fullmatch(r"[0-9]+", t) is not None  # só ASCII: "１２３" não é inscrição

    numeros = [t for t in tokens if _e_numero(t)]
    ufs = [t for t in tokens if t in UFS]
    sobras = [t for t in tokens if not _e_numero(t) and t not in UFS]

    parecem_uf = all(re.fullmatch(r"[A-Z]{1,3}", s) for s in sobras)
    if sobras and not ufs and not letra_colada and parecem_uf:
        raise InvalidOabError(
            f"UF desconhecida ou incompleta: {sobras[0]}. Informe a sigla da seccional."
        )
    if sobras and any(not re.fullmatch(r"[A-Z]+", s) for s in sobras):
        raise InvalidOabError(
            "Caracteres não reconhecidos na OAB. Informe só o número e a UF, no formato 123456/UF."
        )
    if sobras:
        raise InvalidOabError(
            "OAB com letra não é aceita (inscrição suplementar ou de estagiário): o DJEN "
            "responde vazio a essa consulta e o processo ficaria sem monitoramento. "
            "Informe só o número e a UF, no formato 123456/UF."
        )
    if len(numeros) != 1:
        raise InvalidOabError("Informe um único número de inscrição, no formato 123456/UF.")
    if len(set(ufs)) != 1:
        raise InvalidOabError("Informe a UF da seccional (uma só), no formato 123456/UF.")
    numero = numeros[0].lstrip("0")
    if not numero:
        raise InvalidOabError("Número de inscrição inválido.")
    if len(numero) > 7:
        raise InvalidOabError("Número de inscrição com mais de 7 dígitos não existe na OAB.")
    return f"{numero}/{ufs[0]}"


__all__ = ["UFS", "InvalidOabError", "normalizar_oab"]
