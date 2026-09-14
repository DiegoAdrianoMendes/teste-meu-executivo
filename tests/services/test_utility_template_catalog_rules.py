"""V33: regras de corpo que a Meta aplica na criação de templates, aprendidas em produção.

Em 06/09/2026 a WABA do titular recusou com "Invalid parameter" todos os corpos que terminavam
em variável e aceitou todos os que terminavam em frase fixa; a densidade de variáveis não
separou os casos. As regras abaixo impedem que o catálogo volte a esse padrão.
"""

from __future__ import annotations

import re

import pytest

from app.services.whatsapp.utility_template_catalog import (
    CATALOG_VERSION,
    UTILITY_TEMPLATE_CATALOG,
)

VARIABLE = re.compile(r"\{\{(\d+)\}\}")
TRAILING_PUNCTUATION = re.compile(r"[\s.!?…]+$")
WORD = re.compile(r"[\wÀ-ÿ]+")
SAFEGUARD_FIXED_WORDS_PER_VARIABLE = 3


@pytest.mark.parametrize("definition", UTILITY_TEMPLATE_CATALOG, ids=lambda d: d.key)
def test_body_never_starts_or_ends_with_a_variable(definition) -> None:
    body = definition.body.strip()
    assert not VARIABLE.match(body), f"{definition.key} começa com variável"
    assert not re.search(r"\{\{\d+\}\}$", TRAILING_PUNCTUATION.sub("", body)), (
        f"{definition.key} termina com variável, padrão que a Meta recusa"
    )


@pytest.mark.parametrize("definition", UTILITY_TEMPLATE_CATALOG, ids=lambda d: d.key)
def test_variables_are_not_adjacent(definition) -> None:
    assert not re.search(r"\}\}\s*[,;:]?\s*\{\{", definition.body), (
        f"{definition.key} tem variáveis adjacentes"
    )


@pytest.mark.parametrize("definition", UTILITY_TEMPLATE_CATALOG, ids=lambda d: d.key)
def test_variables_are_sequential_with_examples_and_within_meta_limits(definition) -> None:
    positions = [int(value) for value in VARIABLE.findall(definition.body)]
    assert positions == list(range(1, len(positions) + 1)), f"{definition.key} fora de sequência"
    assert len(definition.examples) == len(positions), (
        f"{definition.key} sem exemplo para cada variável"
    )
    assert all(example.strip() for example in definition.examples)
    assert len(definition.body) <= 1024
    fixed_words = WORD.findall(VARIABLE.sub(" ", definition.body))
    assert len(fixed_words) / max(len(positions), 1) >= SAFEGUARD_FIXED_WORDS_PER_VARIABLE, (
        f"{definition.key} tem texto fixo de menos por variável"
    )


def test_bodies_recused_by_meta_were_reissued_as_version_two() -> None:
    by_key = {definition.key: definition for definition in UTILITY_TEMPLATE_CATALOG}
    for key in (
        "lembrete_audiencia",
        "solicitacao_documentos",
        "atualizacao_processo",
        "encerramento_processo",
    ):
        assert by_key[key].version == 2, key
        assert by_key[key].meta_name == f"meu_executivo_{key}_v2"
    assert by_key["confirmacao_consulta"].version == 1
    assert CATALOG_VERSION == "2026.09.1"
