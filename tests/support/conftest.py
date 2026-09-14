from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_user_id,
    get_current_workspace_id,
    get_db,
)
from app.main import app as fastapi_app
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.services.s3_service import S3Service


@dataclass
class SupportTestFactory:
    db: AsyncSession

    async def workspace(self, *, name: str = "Conta SAC") -> Workspace:
        suffix = uuid4().hex[:10]
        workspace = Workspace(
            name=name,
            slug=f"support-{suffix}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        self.db.add(workspace)
        await self.db.flush()
        return workspace

    async def user(
        self,
        *,
        workspace: Workspace | None = None,
        name: str = "Pessoa SAC",
        role: WorkspaceRole = WorkspaceRole.operator,
        membership_status: MembershipStatus = MembershipStatus.active,
        is_platform_admin: bool = False,
        platform_role_package: str | None = None,
        platform_access_active: bool | None = None,
    ) -> tuple[User, WorkspaceMembership | None]:
        suffix = uuid4().hex[:10]
        user = User(
            name=name,
            email=f"support-{suffix}@example.com",
            agent_type=AgentType.human,
            role=UserRole.admin if role in {
                WorkspaceRole.workspace_owner,
                WorkspaceRole.workspace_admin,
            } else UserRole.lawyer,
            initials="SC",
            color="#164e63",
            is_active=True,
            is_internal_staff=is_platform_admin or platform_role_package is not None,
            is_platform_admin=is_platform_admin,
            platform_role_package=(
                platform_role_package or ("platform_owner" if is_platform_admin else None)
            ),
            platform_access_active=(
                is_platform_admin or platform_role_package is not None
                if platform_access_active is None
                else platform_access_active
            ),
            email_verified_at=datetime.now(UTC),
        )
        self.db.add(user)
        await self.db.flush()

        membership = None
        if workspace is not None:
            membership = WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=role,
                status=membership_status,
                is_default=True,
            )
            self.db.add(membership)
            await self.db.flush()
        return user, membership

    @staticmethod
    def context(
        user: User,
        *,
        workspace: Workspace | None = None,
        membership: WorkspaceMembership | None = None,
    ) -> AuthContext:
        return AuthContext(
            user_id=user.id,
            workspace_id=workspace.id if workspace is not None else None,
            membership_id=membership.id if membership is not None else None,
            role=membership.role if membership is not None else None,
            is_internal_staff=user.is_internal_staff,
            surface=(
                "platform"
                if workspace is None and user.is_internal_staff
                else "workspace"
            ),
            auth_epoch=user.auth_epoch,
        )

    @asynccontextmanager
    async def client(self, context: AuthContext) -> AsyncIterator[AsyncClient]:
        previous = dict(fastapi_app.dependency_overrides)

        async def override_get_db():
            # Match the production request-scoped transaction even though this
            # factory deliberately reuses one session across HTTP requests.
            await self.db.commit()
            try:
                yield self.db
                await self.db.commit()
            except Exception:
                self.db.expunge_all()
                await self.db.rollback()
                raise

        async def override_get_current_auth_context():
            return context

        async def override_get_current_user_id():
            return context.user_id

        async def override_get_current_workspace_id():
            if context.workspace_id is None:
                raise AssertionError("This test context has no workspace.")
            return context.workspace_id

        fastapi_app.dependency_overrides[get_db] = override_get_db
        fastapi_app.dependency_overrides[
            get_current_auth_context
        ] = override_get_current_auth_context
        fastapi_app.dependency_overrides[
            get_current_user_id
        ] = override_get_current_user_id
        fastapi_app.dependency_overrides[
            get_current_workspace_id
        ] = override_get_current_workspace_id
        try:
            async with AsyncClient(
                transport=ASGITransport(app=fastapi_app),
                base_url="http://test",
            ) as client:
                yield client
        finally:
            fastapi_app.dependency_overrides.clear()
            fastapi_app.dependency_overrides.update(previous)


@pytest_asyncio.fixture
async def support_factory(db_session: AsyncSession) -> SupportTestFactory:
    return SupportTestFactory(db_session)


@pytest.fixture
def support_ticket_payload():
    def build(**overrides):
        payload = {
            "ticketType": "problem",
            "subject": "Falha ao concluir cadastro",
            "description": "A tela nao conclui o cadastro depois da confirmacao.",
            "priority": "normal",
            "severity": "medium",
            "technicalContext": {
                "page": "/app/clientes/novo",
                "action": "Salvar cadastro",
                "frontendVersion": "2026.07.24",
                "browser": "Chrome 138",
                "device": "desktop",
                "viewport": "1440x900",
                "realtimeStatus": "connected",
                "failedRequestIds": ["req:save-1"],
            },
        }
        payload.update(overrides)
        return payload

    return build


@pytest.fixture(autouse=True)
def support_local_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "support-storage")
    monkeypatch.setattr(settings, "APP_ENV", "test")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_LOCAL_REDIS_ENABLED", False)
    monkeypatch.setattr(settings, "PAPERCLIP_PUBLIC_URL", "http://test")
