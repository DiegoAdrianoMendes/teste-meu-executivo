"""Guardas permanentes do schema local SQLite (Onda 0 do plano mestre).

1. Anti-duplicata: o literal `table_definitions` dentro de
   `_ensure_runtime_hardening_columns`/`ensure_local_sqlite_schema` não pode
   ter a mesma tabela duas vezes — num dict literal a segunda entrada
   sobrescreve a primeira em silêncio e colunas se perdem (foi exatamente o
   bug da `whatsapp_connections` que quebrou a validação de assinatura do
   webhook do WhatsApp).
2. Upgrade legado idempotente: um banco antigo sem as colunas novas de
   `whatsapp_connections` recebe as três colunas, com default de
   `transport_mode` aplicado, sem duplicar nem perder dados, mesmo rodando o
   ensure duas vezes.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from app.db.local_schema import ensure_local_sqlite_schema

LOCAL_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "app" / "db" / "local_schema.py"
)


def _dict_literals_with_string_keys(tree: ast.AST) -> list[ast.Dict]:
    """Coleta dicts literais cujas chaves são strings (candidatos a
    table_definitions)."""
    found: list[ast.Dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        if keys and len(keys) == len(node.keys):
            found.append(node)
    return found


def test_table_definitions_nao_tem_tabela_duplicada():
    """Nenhuma tabela pode aparecer mais de uma vez em qualquer dict literal
    de definição de schema — a segunda entrada apagaria a primeira."""
    tree = ast.parse(LOCAL_SCHEMA_PATH.read_text(encoding="utf-8"))
    problemas: list[str] = []
    for dict_node in _dict_literals_with_string_keys(tree):
        vistos: dict[str, int] = {}
        for key in dict_node.keys:
            assert isinstance(key, ast.Constant)
            nome = key.value
            if nome in vistos:
                problemas.append(
                    f"tabela '{nome}' duplicada (linhas {vistos[nome]} e {key.lineno}) — "
                    "a segunda entrada sobrescreve a primeira em silêncio"
                )
            vistos[nome] = key.lineno
    assert not problemas, "; ".join(problemas)


@pytest.fixture()
def legacy_sqlite_engine(tmp_path):
    """Banco legado: whatsapp_connections SEM as colunas de hardening."""
    engine = create_engine(f"sqlite:///{tmp_path/'legado.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE whatsapp_connections (
                    id CHAR(36) PRIMARY KEY,
                    workspace_id CHAR(36) NOT NULL,
                    label VARCHAR(200)
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO whatsapp_connections (id, workspace_id, label) "
                "VALUES ('c1', 'w1', 'Conexão legada')"
            )
        )
    yield engine
    engine.dispose()


def test_upgrade_legado_cria_colunas_e_e_idempotente(legacy_sqlite_engine):
    for rodada in (1, 2):
        with legacy_sqlite_engine.begin() as conn:
            ensure_local_sqlite_schema(conn)

        colunas = {
            c["name"]
            for c in inspect(legacy_sqlite_engine).get_columns("whatsapp_connections")
        }
        faltando = {
            "transport_mode",
            "app_secret_encrypted",
            "webhook_verify_token_encrypted",
        } - colunas
        assert not faltando, f"rodada {rodada}: colunas ausentes: {sorted(faltando)}"

        with legacy_sqlite_engine.connect() as conn:
            linhas = conn.execute(
                text(
                    "SELECT id, workspace_id, label, transport_mode "
                    "FROM whatsapp_connections"
                )
            ).all()
        assert len(linhas) == 1, f"rodada {rodada}: dados duplicados ou perdidos"
        registro = linhas[0]
        assert registro.id == "c1" and registro.label == "Conexão legada", (
            f"rodada {rodada}: dados legados alterados"
        )
        assert registro.transport_mode, (
            f"rodada {rodada}: default de transport_mode não aplicado"
        )
