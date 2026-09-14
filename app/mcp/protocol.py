"""Dependency-free MCP JSON-RPC protocol layer for the stdio transport."""

from __future__ import annotations

import logging
from typing import Any

from app.ai.tool_results import ToolResultCompressor
from app.core.logging_privacy import safe_error_type
from app.mcp.errors import McpToolError
from app.mcp.runtime import KlmMcpRuntime

LATEST_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = frozenset(
    {LATEST_PROTOCOL_VERSION, "2025-06-18", "2025-03-26", "2024-11-05"}
)


class McpProtocol:
    def __init__(self, runtime: KlmMcpRuntime, *, logger: logging.Logger | None = None) -> None:
        self.runtime = runtime
        self.logger = logger or logging.getLogger("klm.mcp")
        self.initialize_completed = False
        self.client_initialized = False
        self.protocol_version = LATEST_PROTOCOL_VERSION

    @staticmethod
    def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    @staticmethod
    def success_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
        compressed = ToolResultCompressor().compress(payload)
        return {
            "content": [{"type": "text", "text": compressed.rendered}],
            "structuredContent": payload,
            "isError": is_error,
            "_meta": {"klm/toolResultCompression": compressed.receipt.as_dict()},
        }

    async def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested_version = params.get("protocolVersion")
        if not isinstance(requested_version, str):
            raise ValueError("protocolVersion is required")
        self.protocol_version = (
            requested_version
            if requested_version in SUPPORTED_PROTOCOL_VERSIONS
            else LATEST_PROTOCOL_VERSION
        )
        actor = await self.runtime.preflight()
        self.initialize_completed = True
        return {
            "protocolVersion": self.protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "Meu Executivo Legal CRM", "version": "0.1.0-local"},
            "instructions": (
                "Workspace and acting user are fixed by an operator-issued local grant. "
                "Do not ask for or infer another workspace identity. All writes require "
                "confirm=true and an idempotency_key, remain local, and are audited. "
                f"Active grant profile: {self.runtime.claims.profile}; role: {actor.role.value}."
            ),
        }

    async def handle(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self.error_response(None, -32600, "Invalid Request")
        method = message.get("method")
        if not isinstance(method, str):
            return self.error_response(message.get("id"), -32600, "Invalid Request")

        request_id = message.get("id")
        is_notification = "id" not in message
        params = message.get("params", {})
        if not isinstance(params, dict):
            if is_notification:
                return None
            return self.error_response(request_id, -32602, "Invalid params")

        if is_notification:
            if method == "notifications/initialized":
                self.client_initialized = True
            return None

        try:
            if method == "initialize":
                if self.initialize_completed:
                    return self.error_response(request_id, -32600, "Server already initialized")
                return self.success_response(request_id, await self._initialize(params))
            if method == "ping":
                return self.success_response(request_id, {})
            if not self.initialize_completed:
                return self.error_response(request_id, -32002, "Server not initialized")
            if method == "tools/list":
                return self.success_response(
                    request_id,
                    {"tools": self.runtime.tool_definitions()},
                )
            if method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments", {})
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    return self.error_response(request_id, -32602, "Invalid params")
                try:
                    result = await self.runtime.call_tool(name, arguments)
                    return self.success_response(request_id, self.tool_result(result))
                except McpToolError as exc:
                    return self.success_response(
                        request_id,
                        self.tool_result(exc.as_dict(), is_error=True),
                    )
                except Exception as exc:
                    self.logger.error(
                        "Unhandled KLM MCP tool error; error_type=%s",
                        safe_error_type(exc),
                    )
                    error = McpToolError(
                        "MCP_INTERNAL_ERROR",
                        "The operation failed safely; no internal details were exposed.",
                        retryable=False,
                    )
                    return self.success_response(
                        request_id,
                        self.tool_result(error.as_dict(), is_error=True),
                    )
            return self.error_response(request_id, -32601, "Method not found")
        except McpToolError as exc:
            return self.error_response(request_id, -32001, exc.message)
        except ValueError:
            return self.error_response(request_id, -32602, "Invalid params")
        except Exception as exc:
            self.logger.error(
                "Unhandled KLM MCP protocol error; error_type=%s",
                safe_error_type(exc),
            )
            return self.error_response(request_id, -32603, "Internal error")
