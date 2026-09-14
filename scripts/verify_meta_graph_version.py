#!/usr/bin/env python3
"""Fail a release when Meta Graph consumers disagree about the pinned version."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "v26.0"
VERSION_PATTERN = re.compile(r"^v[1-9][0-9]*\.0$")
OLD_VERSION_PATTERN = re.compile(r"v(?:21|23)\.0")


def require(path: Path, fragment: str) -> None:
    source = path.read_text(encoding="utf-8")
    if fragment not in source:
        raise SystemExit(f"{path.relative_to(ROOT)} não fixa {fragment!r}.")


def scan_old_literals() -> None:
    roots = (ROOT / "app", ROOT / "frontend/src")
    suffixes = {".py", ".ts", ".tsx"}
    findings: list[str] = []
    for base in roots:
        for path in base.rglob("*"):
            if path.suffix not in suffixes or not path.is_file():
                continue
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if OLD_VERSION_PATTERN.search(line):
                    findings.append(f"{path.relative_to(ROOT)}:{line_number}")
    if findings:
        raise SystemExit("Versão Meta antiga encontrada: " + ", ".join(findings))


def verify_infra(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    required = (
        "MetaGraphApiVersion:",
        f"Default: {EXPECTED_VERSION}",
        'AllowedPattern: "^v[1-9][0-9]*\\\\.0$"',
        "- Name: META_GRAPH_API_VERSION",
        "Value: !Ref MetaGraphApiVersion",
    )
    missing = [fragment for fragment in required if fragment not in source]
    if missing:
        raise SystemExit(f"Contrato Meta incompleto em {path}: {missing}")
    if source.count("- Name: META_GRAPH_API_VERSION") < 3:
        raise SystemExit("API, worker e scheduler devem receber META_GRAPH_API_VERSION.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--infra-template", type=Path)
    args = parser.parse_args()
    if not VERSION_PATTERN.fullmatch(EXPECTED_VERSION):
        raise SystemExit("Versão esperada inválida.")

    require(
        ROOT / "app/core/config.py",
        f'META_GRAPH_API_VERSION: str = "{EXPECTED_VERSION}"',
    )
    require(ROOT / ".env.example", f"META_GRAPH_API_VERSION={EXPECTED_VERSION}")
    require(
        ROOT / "frontend/.env.production",
        f"VITE_META_GRAPH_API_VERSION={EXPECTED_VERSION}",
    )
    require(
        ROOT / "frontend/.env.development",
        f"VITE_META_GRAPH_API_VERSION={EXPECTED_VERSION}",
    )
    scan_old_literals()
    if args.infra_template:
        verify_infra(args.infra_template.resolve())
    print(f"Meta Graph release contract: {EXPECTED_VERSION}.")


if __name__ == "__main__":
    main()
