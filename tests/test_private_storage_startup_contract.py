from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_api_startup_verifies_bucket_for_either_aws_storage_gate() -> None:
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    lifespan = next(
        node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name == "lifespan"
    )
    rendered = ast.unparse(lifespan)

    assert "aws_external_access_allowed() or aws_private_storage_allowed()" in rendered
    assert "S3Service().verify_private_bucket" in rendered
