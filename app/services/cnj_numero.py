"""Validação do Número Único de Processo (CNJ, Resolução CNJ 65/2008 Anexo V).

Formato NNNNNNN-DD.AAAA.J.TR.OOOO — dígito verificador DD calculado por
módulo 97 base 10 (ISO 7064 mod 97-10), independente de rede.
"""
from __future__ import annotations

import re

_CNJ_PATTERN = re.compile(
    r"^(\d{7})-?(\d{2})\.?(\d{4})\.?(\d{1})\.?(\d{2})\.?(\d{4})$"
)


class InvalidCnjNumberError(ValueError):
    pass


def _digito_verificador(sequencial: str, ano: str, justica: str, tribunal: str, origem: str) -> str:
    numero = int(f"{sequencial}{ano}{justica}{tribunal}{origem}")
    resto = (numero * 100) % 97
    return f"{98 - resto:02d}"


def normalizar_numero_cnj(valor: str) -> str:
    """Valida formato + dígito verificador e retorna a forma canônica.

    Levanta `InvalidCnjNumberError` com uma mensagem específica quando o
    formato ou o dígito verificador não conferem.
    """
    if not isinstance(valor, str) or not valor.strip():
        raise InvalidCnjNumberError("Número CNJ é obrigatório.")
    compacto = re.sub(r"[^0-9]", "", valor)
    if len(compacto) != 20:
        raise InvalidCnjNumberError(
            "Número CNJ deve ter 20 dígitos no formato NNNNNNN-DD.AAAA.J.TR.OOOO."
        )
    sequencial, digito_informado, ano, justica, tribunal, origem = (
        compacto[0:7],
        compacto[7:9],
        compacto[9:13],
        compacto[13:14],
        compacto[14:16],
        compacto[16:20],
    )
    digito_esperado = _digito_verificador(sequencial, ano, justica, tribunal, origem)
    if digito_informado != digito_esperado:
        raise InvalidCnjNumberError(
            f"Dígito verificador do número CNJ inválido: informado {digito_informado}, "
            f"esperado {digito_esperado}."
        )
    return f"{sequencial}-{digito_informado}.{ano}.{justica}.{tribunal}.{origem}"


def numero_cnj_digitos(valor: str) -> str:
    """Os 20 dígitos do número, validados. É a chave de casamento com as fontes.

    O Comunica (DJEN) devolve `numero_processo` sem máscara, com 20 dígitos, e o
    DataJud devolve `_source.numeroProcesso` do mesmo jeito. Casar máscara contra
    dígitos, como o worker fazia, é zero garantido em produção.
    """
    return normalizar_numero_cnj(valor).replace("-", "").replace(".", "")


def numero_cnj_valido(valor: str) -> bool:
    try:
        normalizar_numero_cnj(valor)
        return True
    except InvalidCnjNumberError:
        return False
