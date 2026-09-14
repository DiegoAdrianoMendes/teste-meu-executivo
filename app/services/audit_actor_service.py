"""Resolve audit identities from persisted users instead of caller-provided labels."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def resolve_human_actor_label(
    db: AsyncSession,
    actor_user_id: UUID | None,
) -> str:
    """Return the current persisted name for a human audit actor."""

    if actor_user_id is None:
        raise ValueError("Ações humanas auditáveis exigem um usuário responsável.")
    label = await db.scalar(
        select(User.name).where(
            User.id == actor_user_id,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
    )
    if not label or not label.strip():
        raise ValueError("O usuário responsável pela ação não existe ou está inativo.")
    return label.strip()
