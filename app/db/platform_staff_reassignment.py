"""Contexto PostgreSQL estreito para transferência de responsabilidades do SaaS."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PLATFORM_STAFF_REASSIGNMENT_SCOPE = "staff_reassignment"
PLATFORM_STAFF_REASSIGNMENT_ACTIONS = frozenset({"view", "approve"})
PLATFORM_STAFF_REASSIGNMENT_DB_ACTIONS = tuple(
    f"staff_reassignment:{action}"
    for action in sorted(PLATFORM_STAFF_REASSIGNMENT_ACTIONS)
)

_SET_CONTEXT_SQL = text(
    """
    SELECT set_config('app.current_platform_scope', :scope, true),
           set_config('app.current_platform_actor_id', :actor_user_id, true),
           set_config('app.current_platform_action', :action, true)
    """
)


async def set_platform_staff_reassignment_context(
    session: AsyncSession,
    *,
    actor_user_id: UUID | str,
    action: str,
) -> None:
    if action not in PLATFORM_STAFF_REASSIGNMENT_ACTIONS:
        raise ValueError("Ação inválida para continuidade da equipe.")
    normalized_actor = str(UUID(str(actor_user_id)))
    if session.get_bind().dialect.name != "postgresql":
        return
    await session.execute(
        _SET_CONTEXT_SQL,
        {
            "scope": PLATFORM_STAFF_REASSIGNMENT_SCOPE,
            "actor_user_id": normalized_actor,
            "action": f"staff_reassignment:{action}",
        },
    )


__all__ = [
    "PLATFORM_STAFF_REASSIGNMENT_ACTIONS",
    "PLATFORM_STAFF_REASSIGNMENT_DB_ACTIONS",
    "PLATFORM_STAFF_REASSIGNMENT_SCOPE",
    "set_platform_staff_reassignment_context",
]
