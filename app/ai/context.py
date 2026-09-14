"""Explicit request context for compatibility callers of ``AIProvider.complete``."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from app.ai.models import ModelAttribution


@dataclass(frozen=True, slots=True)
class AIExecutionContext:
    workspace_id: str
    agent_name: str
    attribution: ModelAttribution


_context: ContextVar[AIExecutionContext | None] = ContextVar(
    "klm_ai_execution_context",
    default=None,
)


def current_ai_context() -> AIExecutionContext | None:
    return _context.get()


@contextmanager
def use_ai_context(
    workspace_id: str,
    agent_name: str,
    *,
    attribution: ModelAttribution | None = None,
) -> Iterator[AIExecutionContext]:
    context = AIExecutionContext(
        workspace_id=str(workspace_id),
        agent_name=agent_name.strip(),
        attribution=attribution or ModelAttribution(operation=agent_name),
    )
    token = _context.set(context)
    try:
        yield context
    finally:
        _context.reset(token)
