"""FastAPI dependencies para o RBAC global da equipe do SaaS."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_rbac import has_platform_permission, is_platform_root
from app.db.platform_benefits import set_platform_benefits_context
from app.db.platform_control import set_platform_control_context
from app.db.platform_support import set_platform_support_context
from app.models.user import User


async def require_platform_root(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> User:
    if context.surface != "platform":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "BLACKBOX-IDENTITY-REQUIRED",
                "message": "Esta rota pertence à Central Blackbox.",
            },
        )
    user = await db.get(User, context.user_id)
    if not is_platform_root(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PLATFORM-ROOT-REQUIRED",
                "message": "A operação exige o titular da plataforma.",
            },
        )
    await set_platform_control_context(
        db,
        actor_user_id=user.id,
        module="platform_root",
        action="manage",
        is_platform_root=True,
    )
    return user


def require_platform_permission(module: str, action: str) -> Callable:
    async def dependency(
        context: AuthContext = Depends(get_current_auth_context),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if context.surface != "platform":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "BLACKBOX-IDENTITY-REQUIRED",
                    "message": "Esta rota pertence à Central Blackbox.",
                },
            )
        user = await db.get(User, context.user_id)
        if not has_platform_permission(user, module, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PLATFORM-PERMISSION-DENIED",
                    "message": "Seu pacote de acesso não autoriza esta operação.",
                    "module": module,
                    "action": action,
                },
            )
        await set_platform_control_context(
            db,
            actor_user_id=user.id,
            module=module,
            action=action,
            is_platform_root=is_platform_root(user),
        )
        return user

    return dependency


def require_platform_benefits_permission(module: str, action: str) -> Callable:
    """Autoriza o Blackbox e abre o escopo RLS comercial da transacao."""

    async def dependency(
        context: AuthContext = Depends(get_current_auth_context),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if context.surface != "platform":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "BLACKBOX-IDENTITY-REQUIRED",
                    "message": "Esta rota pertence a Central Blackbox.",
                },
            )
        user = await db.get(User, context.user_id)
        if not has_platform_permission(user, module, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PLATFORM-PERMISSION-DENIED",
                    "message": "Seu pacote de acesso nao autoriza esta operacao.",
                    "module": module,
                    "action": action,
                },
            )
        await set_platform_benefits_context(
            db,
            actor_user_id=user.id,
            module=module,
            action=action,
        )
        return user

    return dependency


def require_platform_support_permission(action: str) -> Callable:
    """Authorize SAC RBAC and open its narrow transaction-local RLS context."""

    async def dependency(
        context: AuthContext = Depends(get_current_auth_context),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if context.surface != "platform":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "BLACKBOX-IDENTITY-REQUIRED",
                    "message": "Esta rota pertence à Central Blackbox.",
                },
            )
        user = await db.get(User, context.user_id)
        if not has_platform_permission(user, "support", action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PLATFORM-PERMISSION-DENIED",
                    "message": "Seu pacote de acesso não autoriza esta operação.",
                    "module": "support",
                    "action": action,
                },
            )
        await set_platform_support_context(
            db,
            actor_user_id=user.id,
            action=action,
        )
        return user

    return dependency


def require_platform_module(module: str) -> Callable:
    """GET exige view; mutações exigem manage."""

    async def dependency(
        request: Request,
        context: AuthContext = Depends(get_current_auth_context),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if context.surface != "platform":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "BLACKBOX-IDENTITY-REQUIRED",
                    "message": "Esta rota pertence à Central Blackbox.",
                },
            )
        action = "view" if request.method.upper() in {"GET", "HEAD", "OPTIONS"} else "manage"
        user = await db.get(User, context.user_id)
        if not has_platform_permission(user, module, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PLATFORM-PERMISSION-DENIED",
                    "message": "Seu pacote de acesso não autoriza esta operação.",
                    "module": module,
                    "action": action,
                },
            )
        await set_platform_control_context(
            db,
            actor_user_id=user.id,
            module=module,
            action=action,
            is_platform_root=is_platform_root(user),
        )
        return user

    return dependency
