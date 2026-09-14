"""Narrow tenant bootstrap for public identity capabilities.

PostgreSQL implementations are SECURITY DEFINER functions materialized by the
consolidated migration. They return only a workspace UUID after a token or
delivery binding matches; all lifecycle queries then run under normal RLS.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import (
    IdentityOutboxMessage,
    IdentityOutboxPurpose,
    SignupVerification,
    WorkspaceInvitation,
)


async def resolve_signup_verification_workspace(
    session: AsyncSession,
    *,
    token_digest: str | None,
    delivery_id: UUID | None,
) -> UUID | None:
    if bool(token_digest) == bool(delivery_id):
        return None
    if session.get_bind().dialect.name == "postgresql":
        value = await session.scalar(
            text(
                "SELECT public.klm_resolve_signup_verification_workspace("
                ":token_digest, :delivery_id)"
            ),
            {"token_digest": token_digest, "delivery_id": delivery_id},
        )
    elif token_digest is not None:
        value = await session.scalar(
            select(SignupVerification.workspace_id).where(
                SignupVerification.token_digest == token_digest
            )
        )
    else:
        value = await session.scalar(
            select(SignupVerification.workspace_id)
            .join(
                IdentityOutboxMessage,
                IdentityOutboxMessage.related_id == SignupVerification.id,
            )
            .where(
                IdentityOutboxMessage.id == delivery_id,
                IdentityOutboxMessage.purpose
                == IdentityOutboxPurpose.signup_verification,
                IdentityOutboxMessage.related_type == "signup_verification",
            )
        )
    return UUID(str(value)) if value is not None else None


async def resolve_workspace_invitation_workspace(
    session: AsyncSession,
    *,
    invitation_id: UUID,
    token_digest: str | None,
    delivery_id: UUID | None,
) -> UUID | None:
    if bool(token_digest) == bool(delivery_id):
        return None
    if session.get_bind().dialect.name == "postgresql":
        value = await session.scalar(
            text(
                "SELECT public.klm_resolve_workspace_invitation_workspace("
                ":invitation_id, :token_digest, :delivery_id)"
            ),
            {
                "invitation_id": invitation_id,
                "token_digest": token_digest,
                "delivery_id": delivery_id,
            },
        )
    elif token_digest is not None:
        value = await session.scalar(
            select(WorkspaceInvitation.workspace_id).where(
                WorkspaceInvitation.id == invitation_id,
                WorkspaceInvitation.token_digest == token_digest,
            )
        )
    else:
        value = await session.scalar(
            select(WorkspaceInvitation.workspace_id)
            .join(
                IdentityOutboxMessage,
                IdentityOutboxMessage.related_id == WorkspaceInvitation.id,
            )
            .where(
                WorkspaceInvitation.id == invitation_id,
                IdentityOutboxMessage.id == delivery_id,
                IdentityOutboxMessage.purpose
                == IdentityOutboxPurpose.workspace_invitation,
                IdentityOutboxMessage.related_type == "workspace_invitation",
            )
        )
    return UUID(str(value)) if value is not None else None
