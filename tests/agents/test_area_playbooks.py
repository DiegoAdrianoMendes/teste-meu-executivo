"""Cobertura de playbooks por área do catálogo jurídico (18 áreas).

Garante que:
1. Toda área de `LEGAL_AREA_CATALOG` tem playbook registrado em
   `AREA_PLAYBOOKS` (app/agents/knowledge/playbooks_index.py) — nenhuma área
   fica sem base de conhecimento de atendimento/conversão.
2. Cada playbook segue o padrão-ouro (`direito_saude_vendas.py`): contém as
   normas de casa "PRIMEIRA MENSAGEM", "FAQ", a regra de publicidade
   (Provimento 205) e a disciplina de "uma pergunta" por mensagem, e tem
   profundidade mínima (>4000 caracteres).
3. Nenhum playbook contém placeholder de preenchimento pendente.
"""
from __future__ import annotations

import re

import pytest

from app.agents.knowledge.playbooks_index import AREA_PLAYBOOKS
from app.data.legal_area_catalog import LEGAL_AREA_CATALOG

_REQUIRED_MARKERS = (
    "PRIMEIRA MENSAGEM",
    "FAQ",
    "Provimento 205",
    "uma pergunta",
)

#
# [PREENCHER (case-insensitive: colchete não ocorre naturalmente em texto
# jurídico) sinaliza campo pendente. XXX e TODO são checados sem
# IGNORECASE global: em maiúsculas são marcador de placeholder; em
# minúsculas ("todo", "toda") são palavras comuns do português e não podem
# disparar falso positivo.
_PLACEHOLDER_PATTERN = re.compile(r"(?i:\[PREENCHER)|\bXXX\b|\bTODO\b")

_MIN_LENGTH = 4000

_CATALOG_AREA_KEYS = tuple(area["areaKey"] for area in LEGAL_AREA_CATALOG)


# ---------------------------------------------------------------------------
# 1. Cobertura: toda área do catálogo tem playbook no índice
# ---------------------------------------------------------------------------


def test_catalog_has_18_areas() -> None:
    assert len(_CATALOG_AREA_KEYS) == 18


@pytest.mark.parametrize("area_key", _CATALOG_AREA_KEYS)
def test_every_catalog_area_has_a_playbook(area_key: str) -> None:
    assert area_key in AREA_PLAYBOOKS, (
        f"Área '{area_key}' do LEGAL_AREA_CATALOG não tem playbook em "
        "AREA_PLAYBOOKS (app/agents/knowledge/playbooks_index.py)."
    )


def test_no_orphan_playbooks_outside_the_catalog() -> None:
    """Todo playbook indexado corresponde a uma área existente no catálogo."""
    catalog_keys = set(_CATALOG_AREA_KEYS)
    for area_key in AREA_PLAYBOOKS:
        assert area_key in catalog_keys, (
            f"AREA_PLAYBOOKS tem entrada '{area_key}' que não existe em "
            "LEGAL_AREA_CATALOG."
        )


# ---------------------------------------------------------------------------
# 2. Conteúdo mínimo de cada playbook (padrão do template direito_saude_vendas)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("area_key", _CATALOG_AREA_KEYS)
def test_playbook_has_version_and_text(area_key: str) -> None:
    version, playbook = AREA_PLAYBOOKS[area_key]
    assert isinstance(version, str) and version.strip()
    assert isinstance(playbook, str) and playbook.strip()


@pytest.mark.parametrize("area_key", _CATALOG_AREA_KEYS)
def test_playbook_contains_required_markers(area_key: str) -> None:
    _, playbook = AREA_PLAYBOOKS[area_key]
    lowered = playbook.lower()
    for marker in _REQUIRED_MARKERS:
        assert marker.lower() in lowered, (
            f"Playbook da área '{area_key}' não contém o marcador obrigatório "
            f"'{marker}'."
        )


@pytest.mark.parametrize("area_key", _CATALOG_AREA_KEYS)
def test_playbook_has_minimum_depth(area_key: str) -> None:
    _, playbook = AREA_PLAYBOOKS[area_key]
    assert len(playbook) > _MIN_LENGTH, (
        f"Playbook da área '{area_key}' tem {len(playbook)} caracteres; "
        f"mínimo exigido é {_MIN_LENGTH}."
    )


# ---------------------------------------------------------------------------
# 3. Nenhum placeholder de preenchimento pendente
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("area_key", _CATALOG_AREA_KEYS)
def test_playbook_has_no_placeholders(area_key: str) -> None:
    _, playbook = AREA_PLAYBOOKS[area_key]
    matches = _PLACEHOLDER_PATTERN.findall(playbook)
    assert not matches, (
        f"Playbook da área '{area_key}' contém placeholder de preenchimento "
        f"pendente: {matches}."
    )
