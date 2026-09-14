"""Identidade e revalidação durável de conexões WebSocket."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.token_store import is_websocket_session_revoked
from app.models.identity import UserSession
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership


@dataclass(frozen=True)
class WebSocketIdentity:
    workspace_id: UUID
    user_id: UUID
    membership_id: UUID
    session_id: UUID
    auth_epoch: int
    token_id: str


async def revalidate_websocket_identity(
    identity: WebSocketIdentity,
    db: AsyncSession,
) -> bool:
    """Revalida revogação de sessão e vínculo persistido sem depender de Pub/Sub."""
    if await is_websocket_session_revoked(identity.token_id):
        return False

    result = await db.execute(
        select(WorkspaceMembership)
        .join(User, WorkspaceMembership.user_id == User.id)
        .join(Workspace, WorkspaceMembership.workspace_id == Workspace.id)
        .join(
            UserSession,
            (UserSession.id == identity.session_id)
            & (UserSession.user_id == identity.user_id),
        )
        .where(
            WorkspaceMembership.id == identity.membership_id,
            WorkspaceMembership.workspace_id == identity.workspace_id,
            WorkspaceMembership.user_id == identity.user_id,
            WorkspaceMembership.status == MembershipStatus.active,
            User.is_active.is_(True),
            User.is_internal_staff.is_(False),
            User.is_platform_admin.is_(False),
            User.platform_access_active.is_(False),
            User.email_verified_at.is_not(None),
            User.auth_epoch == identity.auth_epoch,
            User.deleted_at.is_(None),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
            Workspace.is_active.is_(True),
            Workspace.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none() is not None
