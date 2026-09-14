"""S31: logger do structlog não interpola `%s`; chamadas posicionais viram evento com
`%s` literal e o argumento cai em `positional_args`. Guarda restrita aos módulos
que usam o logger do structlog (os de `logging.getLogger` interpolam normalmente)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NIVEIS = {"info", "warning", "error", "debug", "exception", "critical"}


def _positional_calls(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    if "from app.core.logging import logger" not in source:
        return []
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in NIVEIS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            continue
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and re.search(r"%[sdr]", node.args[0].value)
            and len(node.args) > 1
        ):
            found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def test_structlog_call_sites_have_no_positional_placeholders():
    found: list[str] = []
    for path in (ROOT / "app").rglob("*.py"):
        found.extend(_positional_calls(path))
    assert found == [], found
