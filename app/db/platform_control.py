"""Transaction-local PostgreSQL context for generic Blackbox control-plane access.

The application RBAC decision remains authoritative. This module carries the
already-authorized actor/module/action into PostgreSQL so forced-RLS policies
can reject queries that did not pass through an attributable Blackbox request.
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PLATFORM_ROOT_SCOPE = "platform_root"
_COMMERCIAL_PORTABILITY_SCOPE = "commercial_portability"
_COMMERCIAL_PORTABILITY_ACTION = "rebind"
_COMMERCIAL_PORTABILITY_RESTORE_ACTION = "restore_rebind"
_COMMERCIAL_PROVIDER_SCOPE = "billing_provider"
_COMMERCIAL_PROVIDER_ACTION = "reconcile"
_RESERVED_COMMERCIAL_PORTABILITY_ACTIONS = frozenset(
    {
        _COMMERCIAL_PORTABILITY_ACTION,
        _COMMERCIAL_PORTABILITY_RESTORE_ACTION,
    }
)
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

SET_PLATFORM_CONTROL_CONTEXT_SQL = text(
    """
    SELECT set_config('app.current_platform_scope', :scope, true),
           set_config('app.current_platform_actor_id', :actor_user_id, true),
           set_config('app.current_platform_action', :action, true)
    """
)

CLEAR_PLATFORM_CONTROL_CONTEXT_SQL = text(
    """
    SELECT set_config('app.current_platform_scope', '', true),
           set_config('app.current_platform_actor_id', '', true),
           set_config('app.current_platform_action', '', true)
    """
)


class PlatformControlContextError(RuntimeError):
    """Raised when an invalid control-plane context is requested."""


def _normalized_actor_id(actor_user_id: UUID | str) -> str:
    try:
        return str(UUID(str(actor_user_id)))
    except (TypeError, ValueError) as error:
        raise PlatformControlContextError("Ator Blackbox invalido.") from error


async def _write_platform_control_context(
    session: AsyncSession,
    *,
    actor_user_id: str,
    scope: str,
    action: str,
) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    await session.execute(
        SET_PLATFORM_CONTROL_CONTEXT_SQL,
        {
            "scope": scope,
            "actor_user_id": actor_user_id,
            "action": action,
        },
    )


async def set_platform_control_context(
    session: AsyncSession,
    *,
    actor_user_id: UUID | str,
    module: str,
    action: str,
    is_platform_root: bool = False,
) -> None:
    """Persist a narrow and attributable RLS context for the transaction."""

    normalized_actor = _normalized_actor_id(actor_user_id)

    normalized_module = module.strip().lower()
    normalized_action = action.strip().lower()
    if not _IDENTIFIER.fullmatch(normalized_module) or not _IDENTIFIER.fullmatch(
        normalized_action
    ):
        raise PlatformControlContextError("Modulo ou acao Blackbox invalidos.")
    if (
        normalized_module == _COMMERCIAL_PORTABILITY_SCOPE
        and normalized_action in _RESERVED_COMMERCIAL_PORTABILITY_ACTIONS
    ):
        raise PlatformControlContextError(
            "Contexto interno de portabilidade nao pode ser solicitado diretamente."
        )
    if normalized_module == _COMMERCIAL_PROVIDER_SCOPE:
        raise PlatformControlContextError(
            "Contexto interno do provedor não pode ser solicitado diretamente."
        )

    await _write_platform_control_context(
        session,
        actor_user_id=normalized_actor,
        scope=PLATFORM_ROOT_SCOPE if is_platform_root else normalized_module,
        action=f"{normalized_module}:{normalized_action}",
    )


async def clear_platform_control_context(session: AsyncSession) -> None:
    """Remove every control-plane capability from the current transaction.

    This is intentionally different from rebinding to a low-privilege module:
    an empty context cannot accidentally match a future RLS capability.
    Tenant context, when present, remains untouched.
    """

    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    await session.execute(CLEAR_PLATFORM_CONTROL_CONTEXT_SQL)


async def set_commercial_provider_reconcile_context(session: AsyncSession) -> None:
    """Install the narrow context used only after a provider signature is verified."""

    await _write_platform_control_context(
        session,
        actor_user_id="00000000-0000-0000-0000-000000000000",
        scope=_COMMERCIAL_PROVIDER_SCOPE,
        action=f"{_COMMERCIAL_PROVIDER_SCOPE}:{_COMMERCIAL_PROVIDER_ACTION}",
    )


async def _set_commercial_portability_rebind_context(
    session: AsyncSession,
    *,
    actor_user_id: UUID | str,
    is_platform_root: bool,
) -> None:
    """Bind the command-only portability phase after outer RBAC authorization.

    Even a root contract command uses the bounded scope during this phase.
    Root authority is restored by the command after the automatic updates.
    This prevents an implementation error in the canonical revision workflow
    from turning root authority into an unbounded adjustment or bonus update.
    """

    if not isinstance(is_platform_root, bool):
        raise PlatformControlContextError("Estado root Blackbox invalido.")
    await _write_platform_control_context(
        session,
        actor_user_id=_normalized_actor_id(actor_user_id),
        scope=_COMMERCIAL_PORTABILITY_SCOPE,
        action=(
            f"{_COMMERCIAL_PORTABILITY_SCOPE}:"
            f"{_COMMERCIAL_PORTABILITY_ACTION}"
        ),
    )


async def _set_commercial_portability_restore_rebind_context(
    session: AsyncSession,
    *,
    actor_user_id: UUID | str,
    is_platform_root: bool,
) -> None:
    """Bind the restore-only line-rebind phase after outer authorization.

    The separate action lets PostgreSQL preserve line-bound exceptions during
    an explicit version restoration without widening ordinary plan changes.
    """

    if not isinstance(is_platform_root, bool):
        raise PlatformControlContextError("Estado root Blackbox invalido.")
    await _write_platform_control_context(
        session,
        actor_user_id=_normalized_actor_id(actor_user_id),
        scope=_COMMERCIAL_PORTABILITY_SCOPE,
        action=(
            f"{_COMMERCIAL_PORTABILITY_SCOPE}:"
            f"{_COMMERCIAL_PORTABILITY_RESTORE_ACTION}"
        ),
    )


__all__ = [
    "CLEAR_PLATFORM_CONTROL_CONTEXT_SQL",
    "PLATFORM_ROOT_SCOPE",
    "PlatformControlContextError",
    "SET_PLATFORM_CONTROL_CONTEXT_SQL",
    "clear_platform_control_context",
    "set_commercial_provider_reconcile_context",
    "set_platform_control_context",
]
