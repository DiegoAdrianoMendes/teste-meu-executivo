"""Composition root for tenant-safe KLM MCP tools."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.grants import GrantClaims
from app.mcp.identity import IdentityGuard, McpActor
from app.mcp.revocations import GrantRevocationStore
from app.mcp.tools import KlmCrmTools


class KlmMcpRuntime:
    def __init__(
        self,
        claims: GrantClaims,
        session_factory: Callable[[], AsyncSession],
        *,
        state_dir: str | Path,
    ) -> None:
        resolved_state_dir = Path(state_dir).expanduser().resolve()
        self.identity = IdentityGuard(
            claims,
            session_factory,
            revocations=GrantRevocationStore(resolved_state_dir),
        )
        self.tools = KlmCrmTools(self.identity, state_dir=str(resolved_state_dir))

    @property
    def claims(self) -> GrantClaims:
        return self.identity.claims

    async def preflight(self) -> McpActor:
        return await self.identity.preflight()

    def tool_definitions(self) -> list[dict[str, Any]]:
        return self.tools.definitions()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self.tools.call(name, arguments)
