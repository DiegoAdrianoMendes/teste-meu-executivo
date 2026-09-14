"""Sanitized error types returned by the local MCP adapter."""

from __future__ import annotations

from typing import Any


class McpToolError(Exception):
    """An expected tool failure that is safe to expose to an MCP client."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
            },
        }
        if self.details:
            payload["error"]["details"] = self.details
        return payload
