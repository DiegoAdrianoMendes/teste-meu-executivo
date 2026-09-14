"""Regressões estruturais do atualizador de esquema SQLite local."""

from __future__ import annotations

import ast
from pathlib import Path


LOCAL_SCHEMA_PATH = Path(__file__).parents[2] / "app" / "db" / "local_schema.py"


def test_runtime_hardening_does_not_declare_duplicate_table_keys() -> None:
    module = ast.parse(LOCAL_SCHEMA_PATH.read_text(encoding="utf-8"))
    target: ast.Dict | None = None

    for node in ast.walk(module):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "_ensure_runtime_hardening_columns":
            continue
        for child in ast.walk(node):
            if (
                isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Name)
                and child.target.id == "table_definitions"
                and isinstance(child.value, ast.Dict)
            ):
                target = child.value
                break

    assert target is not None, "table_definitions não foi encontrado"
    keys = [
        key.value
        for key in target.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    ]
    assert len(keys) == len(set(keys)), "há tabelas duplicadas em table_definitions"


def test_whatsapp_runtime_hardening_keeps_all_required_columns() -> None:
    source = LOCAL_SCHEMA_PATH.read_text(encoding="utf-8")
    module = ast.parse(source)
    whatsapp_columns: set[str] = set()

    for node in ast.walk(module):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if not (
                isinstance(key, ast.Constant)
                and key.value == "whatsapp_connections"
                and isinstance(value, (ast.Tuple, ast.List))
            ):
                continue
            for item in value.elts:
                if (
                    isinstance(item, (ast.Tuple, ast.List))
                    and item.elts
                    and isinstance(item.elts[0], ast.Constant)
                ):
                    whatsapp_columns.add(str(item.elts[0].value))

    assert {
        "transport_mode",
        "app_secret_encrypted",
        "webhook_verify_token_encrypted",
    }.issubset(whatsapp_columns)
