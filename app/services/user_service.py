"""
UserService — CRUD de usuários humanos.
"""
from typing import List, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import hash_password_async
from app.core.token_store import revoke_user_tokens
from app.models.entitlement import EntitlementKey
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.user import UserCreate, UserUpdate
from app.services.entitlement_service import EntitlementEnforcementService
from app.websockets.manager import ws_manager

USER_TO_WORKSPACE_ROLE = {
    UserRole.admin: WorkspaceRole.workspace_admin,
    UserRole.senior_lawyer: WorkspaceRole.legal_manager,
    UserRole.lawyer: WorkspaceRole.operator,
    UserRole.intern: WorkspaceRole.viewer,
    UserRole.ai_analyst: WorkspaceRole.operator,
    UserRole.ai_drafter: WorkspaceRole.operator,
}


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _enforce_capacity(
        self,
        *,
        workspace_id: UUID,
        agent_type: AgentType,
    ) -> None:
        entitlement = (
            EntitlementKey.users
            if agent_type == AgentType.human
            else EntitlementKey.agents
        )

        async def load_current_quantity() -> int:
            value = await self.db.scalar(
                select(func.count(WorkspaceMembership.id))
                .join(User, User.id == WorkspaceMembership.user_id)
                .where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.status.in_(
                        [MembershipStatus.active, MembershipStatus.invited]
                    ),
                    User.agent_type == agent_type,
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                )
            )
            return int(value or 0)

        await EntitlementEnforcementService(self.db).enforce_stock(
            workspace_id=workspace_id,
            entitlement=entitlement,
            load_current_quantity=load_current_quantity,
        )

    async def get_by_id(self, user_id: UUID, workspace_id: Optional[UUID] = None) -> Optional[User]:
        query = (
            select(User)
            .options(selectinload(User.memberships).selectinload(WorkspaceMembership.workspace))
            .where(User.id == user_id, User.deleted_at.is_(None))
        )
        if workspace_id is not None:
            query = query.join(WorkspaceMembership, WorkspaceMembership.user_id == User.id).where(
                WorkspaceMembership.workspace_id == workspace_id
            )

        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.db.execute(
            select(User)
            .options(selectinload(User.memberships).selectinload(WorkspaceMembership.workspace))
            .where(User.email == email, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_all_humans(
        self,
        workspace_id: Optional[UUID] = None,
        role: Optional[UserRole] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[User]:
        conditions = [
            User.agent_type == AgentType.human,
            User.deleted_at.is_(None),
        ]
        if role is not None:
            conditions.append(User.role == role)
        if is_active is not None:
            conditions.append(User.is_active == is_active)
        if search:
            pattern = f"%{search}%"
            conditions.append(
                or_(User.name.ilike(pattern), User.email.ilike(pattern))
            )

        query = select(User).options(
            selectinload(User.memberships).selectinload(WorkspaceMembership.workspace)
        )
        if workspace_id is not None:
            query = query.join(WorkspaceMembership, WorkspaceMembership.user_id == User.id).where(
                WorkspaceMembership.workspace_id == workspace_id
            )

        result = await self.db.execute(
            query
            .where(and_(*conditions))
            .order_by(User.name)
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().unique().all())

    async def create(self, data: UserCreate, *, workspace_id: UUID) -> User:
        await self._enforce_capacity(
            workspace_id=workspace_id,
            agent_type=data.agent_type,
        )
        hashed = None
        if data.password:
            hashed = await hash_password_async(data.password)

        user = User(
            name=data.name,
            email=data.email,
            phone=data.phone,
            hashed_password=hashed,
            agent_type=data.agent_type,
            role=data.role,
            initials=data.initials or self._make_initials(data.name),
            color=data.color,
            avatar_url=data.avatar_url,
            is_active=True,
        )
        self.db.add(user)
        await self.db.flush()
        self.db.add(
            WorkspaceMembership(
                workspace_id=workspace_id,
                user_id=user.id,
                role=USER_TO_WORKSPACE_ROLE[data.role],
                status=MembershipStatus.active,
                is_default=False,
                permission_grants=[],
                permission_denials=[],
            )
        )
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def update(
        self,
        user_id: UUID,
        data: UserUpdate,
        *,
        workspace_id: UUID,
    ) -> Optional[User]:
        user = await self.get_by_id(user_id, workspace_id=workspace_id)
        if not user:
            return None

        update_data = data.model_dump(exclude_unset=True)
        if update_data.get("is_active") is True and not user.is_active:
            await self._enforce_capacity(
                workspace_id=workspace_id,
                agent_type=user.agent_type,
            )
        revoke_sessions = update_data.get("is_active") is False

        # Tratar senha separadamente
        if "password" in update_data:
            raw = update_data.pop("password")
            if raw:
                user.hashed_password = await hash_password_async(raw)
                revoke_sessions = True

        for field, value in update_data.items():
            setattr(user, field, value)

        if revoke_sessions:
            await revoke_user_tokens(str(user.id))
        await self.db.commit()
        if revoke_sessions:
            await ws_manager.disconnect_user_sessions(user.id, code=4401)
        await self.db.refresh(user)
        return user

    async def deactivate(self, user_id: UUID, *, workspace_id: UUID) -> bool:
        user = await self.get_by_id(user_id, workspace_id=workspace_id)
        if not user:
            return False
        user.is_active = False
        await revoke_user_tokens(str(user.id))
        await self.db.commit()
        await ws_manager.disconnect_user_sessions(user.id, code=4401)
        return True

    @staticmethod
    def _make_initials(name: str) -> str:
        parts = name.strip().split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return name[:2].upper()
