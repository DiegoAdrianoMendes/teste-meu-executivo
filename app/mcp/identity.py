"""Tenant and RBAC revalidation for every MCP tool call."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import Permission, get_effective_permissions
from app.db.tenant import set_workspace_context
from app.mcp.errors import McpToolError
from app.mcp.grants import GrantClaims
from app.mcp.revocations import GrantRevocationStore
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole

CAPABILITY_PERMISSIONS: dict[str, frozenset[tuple[str, str]]] = {
    "crm.read": frozenset({("kanban", "view")}),
    "crm.sensitive.read": frozenset({("kanban", "view"), ("lead_data", "view")}),
    "crm.write": frozenset({("kanban", "edit")}),
    "handoff.request": frozenset({("kanban", "edit"), ("aprovacoes", "view")}),
    "approval.request": frozenset({("kanban", "edit"), ("aprovacoes", "view")}),
}


@dataclass(frozen=True)
class McpActor:
    workspace_id: UUID
    user_id: UUID
    membership_id: UUID
    role: WorkspaceRole
    label: str
    permissions: frozenset[tuple[str, str]]
    sensitive_data_allowed: bool


class IdentityGuard:
    """Resolve a signed grant to one live membership and never trust tool identity input."""

    def __init__(
        self,
        claims: GrantClaims,
        session_factory: Callable[[], AsyncSession],
        *,
        revocations: GrantRevocationStore,
    ) -> None:
        self.claims = claims
        self.session_factory = session_factory
        self.revocations = revocations

    def _ensure_grant_active(self) -> None:
        if self.claims.expires_at <= datetime.now(UTC):
            raise McpToolError("MCP_GRANT_EXPIRED", "The MCP grant has expired.")
        if self.revocations.is_revoked(self.claims.grant_id):
            raise McpToolError(
                "MCP_GRANT_REVOKED",
                "The MCP grant was revoked by the local operator.",
            )

    @asynccontextmanager
    async def session(
        self,
        capability: str,
        *,
        extra_permissions: frozenset[tuple[str, str]] = frozenset(),
    ) -> AsyncIterator[tuple[AsyncSession, McpActor]]:
        self._ensure_grant_active()
        if capability not in self.claims.capabilities:
            raise McpToolError(
                "MCP_CAPABILITY_DENIED",
                "This local MCP grant does not expose the requested capability.",
            )

        async with self.session_factory() as db:
            try:
                await set_workspace_context(db, self.claims.workspace_id)
                row = (
                    await db.execute(
                        select(WorkspaceMembership, User, Workspace)
                        .join(User, User.id == WorkspaceMembership.user_id)
                        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
                        .where(
                            WorkspaceMembership.id == self.claims.membership_id,
                            WorkspaceMembership.workspace_id == self.claims.workspace_id,
                            WorkspaceMembership.user_id == self.claims.user_id,
                            WorkspaceMembership.status == MembershipStatus.active,
                            User.id == self.claims.user_id,
                            User.is_active.is_(True),
                            User.deleted_at.is_(None),
                            Workspace.id == self.claims.workspace_id,
                            Workspace.status == WorkspaceStatus.active,
                            Workspace.is_active.is_(True),
                            Workspace.deleted_at.is_(None),
                        )
                    )
                ).one_or_none()
                if row is None:
                    raise McpToolError(
                        "MCP_IDENTITY_REVOKED",
                        "The configured user, membership, or workspace is no longer active.",
                    )

                membership, user, _workspace = row
                effective = get_effective_permissions(
                    membership.role,
                    membership.permission_grants,
                    membership.permission_denials,
                )
                permission_keys = frozenset((item.module, item.action) for item in effective)
                required = CAPABILITY_PERMISSIONS.get(capability, frozenset()) | extra_permissions
                missing = sorted(required - permission_keys)
                if missing:
                    raise McpToolError(
                        "MCP_PERMISSION_DENIED",
                        "The configured user no longer has permission for this operation.",
                        details={
                            "missingPermissions": [
                                f"{module}.{action}" for module, action in missing
                            ]
                        },
                    )

                sensitive_allowed = (
                    "crm.sensitive.read" in self.claims.capabilities
                    and CAPABILITY_PERMISSIONS["crm.sensitive.read"].issubset(permission_keys)
                )
                yield db, McpActor(
                    workspace_id=self.claims.workspace_id,
                    user_id=self.claims.user_id,
                    membership_id=self.claims.membership_id,
                    role=membership.role,
                    label=user.name,
                    permissions=permission_keys,
                    sensitive_data_allowed=sensitive_allowed,
                )
            except Exception:
                await db.rollback()
                raise

    async def preflight(self) -> McpActor:
        async with self.session("crm.read") as (_db, actor):
            return actor


def capabilities_allowed_by_permissions(
    role: WorkspaceRole,
    grants: list[Permission | dict] | None,
    denials: list[Permission | dict] | None,
) -> frozenset[str]:
    """Return capabilities that may be issued for a live membership."""

    effective = get_effective_permissions(role, grants, denials)
    keys = frozenset((item.module, item.action) for item in effective)
    return frozenset(
        capability
        for capability, requirements in CAPABILITY_PERMISSIONS.items()
        if requirements.issubset(keys)
    )
