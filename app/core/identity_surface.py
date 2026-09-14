"""Regras canônicas de separação entre identidades do SaaS e dos clientes."""
from __future__ import annotations

from fastapi import HTTPException, status

from app.models.user import User
from app.core.platform_rbac import is_platform_root


def is_platform_identity(user: User | None) -> bool:
    """Classifica a identidade sem depender de o acesso estar ativo."""
    return bool(
        user
        and (
            user.is_platform_admin
            or user.is_internal_staff
            or user.platform_role_package
        )
    )


def has_active_platform_access(user: User | None) -> bool:
    """Confirma que uma identidade Blackbox ainda pode operar a plataforma."""
    if user is None or not user.is_active or user.deleted_at is not None:
        return False
    if is_platform_root(user):
        return True
    return bool(
        user.is_internal_staff
        and user.platform_access_active
        and user.platform_role_package
    )


def assert_workspace_identity(user: User | None) -> None:
    """Impede que uma identidade da equipe do SaaS seja vinculada a cliente."""
    if is_platform_identity(user):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "IDENTITY-SURFACE-CONFLICT",
                "message": (
                    "Identidades da equipe do SaaS não podem ser vinculadas "
                    "a contas contratantes."
                ),
            },
        )
