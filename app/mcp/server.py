"""MCP 2025-11-25 newline-delimited JSON-RPC stdio entry point."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from app.mcp.errors import McpToolError
from app.mcp.grants import database_fingerprint, load_signed_grant
from app.mcp.protocol import McpProtocol
from app.mcp.runtime import KlmMcpRuntime


def _stderr_logger() -> logging.Logger:
    logger = logging.getLogger("klm.mcp")
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


class StdioMcpServer:
    def __init__(
        self,
        protocol: McpProtocol,
        *,
        stdin: BinaryIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        self.protocol = protocol
        self.stdin = stdin or sys.stdin.buffer
        self.stdout = stdout or sys.stdout

    def _write(self, message: dict[str, Any]) -> None:
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"), default=str)
        self.stdout.write(encoded + "\n")
        self.stdout.flush()

    async def run(self) -> None:
        while True:
            raw = await asyncio.to_thread(self.stdin.readline)
            if not raw:
                return
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._write(McpProtocol.error_response(None, -32700, "Parse error"))
                continue
            response = await self.protocol.handle(message)
            if response is not None:
                self._write(response)


async def build_runtime_from_environment() -> KlmMcpRuntime:
    grant_file = os.environ.get("KLM_MCP_GRANT_FILE", "").strip()
    key_file = os.environ.get("KLM_MCP_KEY_FILE", "").strip()
    if not grant_file or not key_file:
        raise McpToolError(
            "MCP_CONFIGURATION_MISSING",
            "KLM_MCP_GRANT_FILE and KLM_MCP_KEY_FILE are required.",
        )

    # Imported lazily so protocol/unit tests can inject an isolated session factory.
    from app.core.config import settings
    from app.db.session import AsyncSessionLocal

    claims = load_signed_grant(
        grant_file,
        key_file,
        expected_database_fingerprint=database_fingerprint(settings.DATABASE_URL),
    )
    state_dir = Path(grant_file).expanduser().resolve().parent / "state"
    return KlmMcpRuntime(claims, AsyncSessionLocal, state_dir=state_dir)


async def async_main() -> None:
    logger = _stderr_logger()
    runtime = await build_runtime_from_environment()
    protocol = McpProtocol(runtime, logger=logger)
    await StdioMcpServer(protocol).run()


def main() -> None:
    try:
        asyncio.run(async_main())
    except McpToolError as exc:
        print(f"KLM MCP refused to start: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from exc
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
