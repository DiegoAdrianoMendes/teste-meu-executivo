#!/usr/bin/env python3
"""Revoke a local KLM MCP grant, including already-running stdio servers."""

# ruff: noqa: E402 - local-only environment gates must be set before app imports.

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOCAL_DATABASE_URL = f"sqlite+aiosqlite:///{ROOT / '.local' / 'klm-hub.db'}"
os.environ.setdefault("DATABASE_URL", os.environ.get("KLM_MCP_DATABASE_URL", LOCAL_DATABASE_URL))
os.environ.setdefault("KLM_RUNTIME_MODE", "local_simulation")
os.environ.setdefault("KLM_EXTERNAL_ACTIONS_ENABLED", "false")
os.environ.setdefault("KLM_AWS_ENABLED", "false")
os.environ.setdefault("KLM_ONLINE_APPROVED", "false")

from app.core.config import settings
from app.mcp.grants import database_fingerprint, load_signed_grant
from app.mcp.revocations import GrantRevocationStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Revoga imediatamente um grant local do MCP do KLM Hub."
    )
    parser.add_argument(
        "--grant-file",
        type=Path,
        default=os.environ.get("KLM_MCP_GRANT_FILE"),
        required=not bool(os.environ.get("KLM_MCP_GRANT_FILE")),
    )
    parser.add_argument(
        "--key-file",
        type=Path,
        default=os.environ.get("KLM_MCP_KEY_FILE"),
        required=not bool(os.environ.get("KLM_MCP_KEY_FILE")),
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    grant_file = args.grant_file.expanduser().resolve()
    key_file = args.key_file.expanduser().resolve()
    claims = load_signed_grant(
        grant_file,
        key_file,
        expected_database_fingerprint=database_fingerprint(settings.DATABASE_URL),
    )
    marker = GrantRevocationStore(grant_file.parent / "state").revoke(claims.grant_id)
    print(f"Grant revogado: {claims.grant_id}")
    print(f"Marcador local: {marker}")
    print("Processos MCP já abertos recusarão a próxima chamada.")


if __name__ == "__main__":
    main()
