"""Contexto PostgreSQL restrito para beneficios comerciais no Blackbox."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


PLATFORM_BENEFITS_SCOPE = "saas_benefits"
ALLOWED_MODULES = frozenset({"benefits", "referrals", "partners", "coupons"})

SET_PLATFORM_BENEFITS_CONTEXT_SQL = text(
    """
    SELECT set_config('app.current_platform_scope', :scope, true),
           set_config('app.current_platform_actor_id', :actor_user_id, true),
           set_config('app.current_platform_action', :action, true)
    """
)


class PlatformBenefitsContextError(RuntimeError):
    """Indica tentativa de abrir um escopo comercial invalido."""


async def set_platform_benefits_context(
    session: AsyncSession,
    *,
    actor_user_id: UUID | str,
    module: str,
    action: str,
) -> None:
    try:
        normalized_actor = str(UUID(str(actor_user_id)))
    except (TypeError, ValueError) as error:
        raise PlatformBenefitsContextError("Ator Blackbox invalido.") from error
    if module not in ALLOWED_MODULES or not action.strip():
        raise PlatformBenefitsContextError("Acao comercial Blackbox invalida.")
    if session.get_bind().dialect.name != "postgresql":
        return
    await session.execute(
        SET_PLATFORM_BENEFITS_CONTEXT_SQL,
        {
            "scope": PLATFORM_BENEFITS_SCOPE,
            "actor_user_id": normalized_actor,
            "action": f"{module}:{action}",
        },
    )
