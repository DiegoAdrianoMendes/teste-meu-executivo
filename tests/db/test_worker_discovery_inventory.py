"""O inventário de descoberta dos beats anda em lockstep com as policies do banco."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.db import rls
from app.db.worker_discovery import (
    WORKER_DISCOVERY_FAMILIES,
    WorkerDiscoveryContextError,
    set_worker_discovery_context,
    worker_discovery_table_families,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "e3f1a7c9d2b5_add_worker_discovery_select_policies.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_descoberta", MIGRATION)
    modulo = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo


def test_toda_tabela_descoberta_esta_sob_rls_e_ganha_a_policy_de_select() -> None:
    mapa = worker_discovery_table_families()
    assert mapa, "inventário vazio"
    for tabela, familias in mapa.items():
        assert tabela in rls.RLS_TENANT_TABLES, tabela
        specs = rls.RLS_POLICY_SPECS[tabela]
        descoberta = [s for s in specs if s.name == rls.WORKER_DISCOVERY_POLICY_NAME]
        assert len(descoberta) == 1, tabela
        (spec,) = descoberta
        assert spec.command == "SELECT"
        assert spec.with_check is None  # só leitura: gravar continua exigindo o workspace
        assert spec.using == rls.worker_discovery_policy_predicate(familias)
        for familia in familias:
            assert f"discovery:{familia}" in spec.using


def test_o_retrato_da_migration_e_igual_ao_inventario() -> None:
    """O guard compara as policies do banco com `rls.py` a cada conexão: se a
    migration e o inventário divergirem, a plataforma inteira recusa conexão."""
    modulo = _migration()
    assert modulo.TABLE_FAMILIES == worker_discovery_table_families()
    for tabela, familias in modulo.TABLE_FAMILIES.items():
        assert rls.policy_spec_expression_matches(
            rls.worker_discovery_policy_predicate(familias), modulo.predicate(familias)
        ), tabela


def test_toda_familia_tem_tabela_e_familia_desconhecida_e_recusada() -> None:
    for familia, tabelas in WORKER_DISCOVERY_FAMILIES.items():
        assert tabelas, familia


@pytest.mark.asyncio
async def test_familia_desconhecida_e_recusada(db_session) -> None:
    with pytest.raises(WorkerDiscoveryContextError):
        await set_worker_discovery_context(db_session, family="nao_existe")
    await set_worker_discovery_context(db_session, family="follow_up")  # no-op em SQLite
