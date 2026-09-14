"""Version-pinned AST helpers for Python-based upstream extractors."""

from __future__ import annotations

import ast
import platform
import sys
from collections.abc import Mapping

from scripts.oss_census.common import ExtractionError

REQUIRED_PYTHON = (3, 12)
REQUIRED_RUNTIME_VERSION = "3.12.13"
PARSER_ID = "cpython-stdlib-ast-pep701"
PARSER_VERSION = "3.12"


def require_python_toolchain() -> None:
    actual = platform.python_version()
    if actual != REQUIRED_RUNTIME_VERSION or platform.python_implementation() != "CPython":
        raise ExtractionError(
            f"Canonical OSS census requires CPython {REQUIRED_RUNTIME_VERSION} for "
            "deterministic PEP 701 parsing; "
            f"found {platform.python_implementation()} {platform.python_version()}"
        )


def parser_receipt() -> dict[str, object]:
    require_python_toolchain()
    return {
        "implementation": "CPython",
        "runtimeVersion": platform.python_version(),
        "requiredRuntimeVersion": REQUIRED_RUNTIME_VERSION,
        "requiredMajorMinor": "3.12",
        "parser": PARSER_ID,
        "grammarVersion": PARSER_VERSION,
        "featureVersion": [3, 12],
        "pep701": True,
        "fallbackParser": None,
    }


def parse_python(text: str, path: str = "<unknown>") -> ast.Module:
    require_python_toolchain()
    try:
        return ast.parse(text, filename=path, feature_version=REQUIRED_PYTHON)
    except (SyntaxError, ValueError) as exc:
        location = ""
        if isinstance(exc, SyntaxError):
            location = f" at line {exc.lineno}, column {exc.offset}"
        detail = exc.msg if isinstance(exc, SyntaxError) else str(exc)
        raise ExtractionError(f"Python 3.12 parser rejected {path}{location}: {detail}") from exc


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def static_string(
    node: ast.AST | None,
    constants: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve a literal/f-string without executing upstream code."""

    constants = constants or {}
    literal = literal_string(node)
    if literal is not None:
        return literal
    if node is None:
        return None
    name = call_name(node)
    if name in constants:
        return constants[name]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = static_string(node.left, constants)
        right = static_string(node.right, constants)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if isinstance(value, ast.FormattedValue):
                resolved = static_string(value.value, constants)
                if resolved is not None:
                    parts.append(resolved)
                    continue
            return None
        return "".join(parts)
    return None


def string_list(node: ast.AST | None) -> list[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    values = [literal_string(item) for item in node.elts]
    if any(value is None for value in values):
        return None
    return [value for value in values if value is not None]


def module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Resolve simple module-level string constants in declaration order."""

    constants: dict[str, str] = {}
    for node in tree.body:
        target: ast.AST | None = None
        value: ast.AST | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        if not isinstance(target, ast.Name):
            continue
        resolved = static_string(value, constants)
        if resolved is not None:
            constants[target.id] = resolved
    return constants


def line_offset(text: str, lineno: int) -> int:
    if lineno <= 1:
        return 0
    cursor = 0
    for _ in range(lineno - 1):
        newline = text.find("\n", cursor)
        if newline == -1:
            return len(text)
        cursor = newline + 1
    return cursor


def call_keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None
