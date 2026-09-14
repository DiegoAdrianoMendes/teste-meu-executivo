"""Typed, tenant-scoped registry for agent-backed flow node executors."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class AgentExecutionContext:
    workspace_id: UUID
    context_type: str
    context_id: UUID | None
    permissions: frozenset[str]
    deployment_id: UUID | None = None
    correlation_id: str | None = None
    db: Any | None = None


@dataclass(frozen=True)
class AgentNodeOutput:
    status: str
    data: dict[str, Any]
    schema_version: int = 1
    idempotency_key: str | None = None
    requires_human: bool = False


Executor = Callable[[AgentExecutionContext, dict[str, Any]], Awaitable[AgentNodeOutput]]


class AgentNodeExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, Executor] = {}

    def register(self, node_type: str, executor: Executor) -> None:
        key = node_type.strip()
        if not key or key in self._executors:
            raise ValueError("node executor key must be unique and non-empty")
        self._executors[key] = executor

    def resolve(self, node_type: str) -> Executor:
        try:
            return self._executors[node_type]
        except KeyError as exc:
            raise LookupError(f"executor unavailable: {node_type}") from exc

    def has(self, node_type: str) -> bool:
        return node_type in self._executors


agent_node_executors = AgentNodeExecutorRegistry()
