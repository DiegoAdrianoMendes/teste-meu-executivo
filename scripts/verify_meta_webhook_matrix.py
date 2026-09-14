#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "META_WEBHOOK_IMPLEMENTATION_MATRIX.md"
CATALOG = ROOT / "app" / "services" / "whatsapp" / "meta_webhook_operations_service.py"
HANDLER_FILES = (
    ROOT / "app" / "services" / "whatsapp" / "webhooks" / "account_handlers.py",
    ROOT / "app" / "services" / "whatsapp" / "webhooks" / "group_handlers.py",
    ROOT / "app" / "services" / "whatsapp" / "webhooks" / "message_template_handlers.py",
    ROOT / "app" / "services" / "whatsapp" / "webhooks" / "routing_security_handlers.py",
    ROOT / "app" / "services" / "whatsapp" / "webhooks" / "tracking_preferences_handlers.py",
)


def _dict_keys(path: Path, assignment: str) -> set[str]:
    """Read constant dictionary keys without importing the application runtime."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        target_name: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            target_name = target.id if isinstance(target, ast.Name) else None
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target_name = node.target.id if isinstance(node.target, ast.Name) else None
            value = node.value
        if target_name != assignment or not isinstance(value, ast.Dict):
            continue
        return {
            key.value
            for key in value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
    raise RuntimeError(f"{assignment} not found as a dictionary in {path}")


def main() -> int:
    text = MATRIX.read_text(encoding="utf-8")
    matrix_fields = {
        match.group(1)
        for line in text.splitlines()
        if (match := re.match(r"^\| ([a-z0-9_]+) \| Meta v\d+", line))
    }
    registry_fields = _dict_keys(CATALOG, "META_WEBHOOK_FIELD_CATALOG")
    handler_fields = set().union(*(_dict_keys(path, "HANDLERS") for path in HANDLER_FILES))
    failures: list[str] = []
    if len(registry_fields) != 33:
        failures.append(f"registry has {len(registry_fields)} fields, expected 33")
    if matrix_fields != registry_fields:
        failures.append(
            f"matrix mismatch: missing={sorted(registry_fields - matrix_fields)}, "
            f"extra={sorted(matrix_fields - registry_fields)}"
        )
    if handler_fields != registry_fields:
        failures.append(
            f"handler mismatch: missing={sorted(registry_fields - handler_fields)}, "
            f"extra={sorted(handler_fields - registry_fields)}"
        )
    if failures:
        print("Meta webhook matrix verification FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Meta webhook matrix verification passed: 33 registry fields, handlers and rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
