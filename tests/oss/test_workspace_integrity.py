"""Protege a fonte versionada contra duplicatas silenciosas de sincronização."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CRITICAL_ROOTS = (
    ROOT / ".vendor" / "upstreams",
    ROOT / "app",
    ROOT / "frontend" / "src",
    ROOT / "tests",
)


def _versioned_critical_files() -> list[Path]:
    relative_roots = [str(tree.relative_to(ROOT)) for tree in CRITICAL_ROOTS]
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", *relative_roots],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        return [
            ROOT / item.decode("utf-8")
            for item in result.stdout.split(b"\0")
            if item
        ]

    # Pacotes sem metadados Git ainda recebem a verificação conservadora.
    return [
        path
        for tree in CRITICAL_ROOTS
        if tree.exists()
        for path in tree.rglob("*")
        if path.is_file()
    ]


def test_critical_trees_have_no_duplicate_suffix_artifacts() -> None:
    duplicates = sorted(
        str(path.relative_to(ROOT))
        for path in _versioned_critical_files()
        if " 2" in path.name
    )

    assert duplicates == []
