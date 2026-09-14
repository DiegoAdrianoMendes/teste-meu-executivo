"""Transaction-local PostgreSQL context for the governed platform SAC."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PLATFORM_SCOPE_SETTING = "app.current_platform_scope"
PLATFORM_ACTOR_SETTING = "app.current_platform_actor_id"
PLATFORM_ACTION_SETTING = "app.current_platform_action"
PLATFORM_SUPPORT_SCOPE = "support"
PLATFORM_SUPPORT_ACTIONS = frozenset(
    {"view", "respond", "triage", "assign", "manage", "export"}
)
PLATFORM_SUPPORT_DB_ACTIONS = tuple(
    f"support:{action}" for action in sorted(PLATFORM_SUPPORT_ACTIONS)
)

SET_PLATFORM_SUPPORT_CONTEXT_SQL = text(
    """
    SELECT set_config('app.current_platform_scope', :scope, true),
           set_config('app.current_platform_actor_id', :actor_user_id, true),
           set_config('app.current_platform_action', :action, true)
    """
)


class PlatformSupportContextError(RuntimeError):
    """Raised when code attempts to open an invalid platform SAC context."""


async def set_platform_support_context(
    session: AsyncSession,
    *,
    actor_user_id: UUID | str,
    action: str,
) -> None:
    """Open a narrow, transaction-local context recognized only by SAC RLS.

    Callers must first authorize the same ``support:<action>`` permission. The
    actor and action settings keep every elevated transaction attributable.
    """

    try:
        normalized_actor = str(UUID(str(actor_user_id)))
    except (TypeError, ValueError) as error:
        raise PlatformSupportContextError("Invalid platform SAC actor.") from error
    if action not in PLATFORM_SUPPORT_ACTIONS:
        raise PlatformSupportContextError("Invalid platform SAC action.")

    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    await session.execute(
        SET_PLATFORM_SUPPORT_CONTEXT_SQL,
        {
            "scope": PLATFORM_SUPPORT_SCOPE,
            "actor_user_id": normalized_actor,
            "action": f"support:{action}",
        },
    )
