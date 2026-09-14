"""Heurística determinística (regex, não-IA) para pré-preencher `dias` e
`tipo_contagem` a partir do texto de uma publicação DJEN.

Isto é um atalho de conveniência para o worker de ingestão — NUNCA é
suficiente para lançar um prazo fatal por si só. Todo resultado aqui
continua exigindo o mesmo cálculo determinístico
(`app/services/prazo_calculator.py`) e a mesma confirmação humana antes de
qualquer compromisso oficial (dossiê regra 10). Se a heurística não
reconhecer o texto, retorna `None` e a publicação é apenas registrada sem
prazo — nunca inventa um número.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.prazo_calculator import TipoContagem

_PRAZO_PATTERN = re.compile(
    r"prazo\s+de\s+(\d{1,3})\s*(?:\([^)]*\))?\s*dias?\s*(úteis|uteis|corridos)?",
    re.IGNORECASE,
)

_TIPO_ATO_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("contestar", "contestacao"),
    ("contestação", "contestacao"),
    ("réplica", "replica"),
    ("replica", "replica"),
    ("embargos de declaração", "embargos_declaracao"),
    ("embargos à execução", "embargos_execucao"),
    ("apelação", "apelacao"),
    ("agravo de instrumento", "agravo_instrumento"),
    ("recurso especial", "recurso_especial"),
    ("recurso extraordinário", "recurso_extraordinario"),
)


@dataclass(frozen=True, slots=True)
class PrazoExtraidoDoTexto:
    dias: int
    tipo_contagem: TipoContagem
    tipo_ato: str | None


def extrair_prazo_do_texto(texto: str) -> PrazoExtraidoDoTexto | None:
    match = _PRAZO_PATTERN.search(texto or "")
    if match is None:
        return None
    dias = int(match.group(1))
    if dias <= 0:
        return None
    contagem_bruta = (match.group(2) or "").lower()
    tipo_contagem = (
        TipoContagem.dias_corridos if contagem_bruta == "corridos" else TipoContagem.dias_uteis
    )
    texto_lower = texto.lower()
    tipo_ato = next(
        (valor for chave, valor in _TIPO_ATO_KEYWORDS if chave in texto_lower), None
    )
    return PrazoExtraidoDoTexto(dias=dias, tipo_contagem=tipo_contagem, tipo_ato=tipo_ato)
