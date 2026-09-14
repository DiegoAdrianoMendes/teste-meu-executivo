"""Testes do Sprint B2 (UX coerente de permissões) — camada backend:
  - GET /api/v1/workspaces/me/access-package (self-service, sem exigir permissão admin)
  - POST /api/v1/notifications/request-access (empty-state "Solicitar acesso")
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models.notification import Notification
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _add_member(db_session, *, workspace_id, role: WorkspaceRole, access_package: str | None = None, email: str):
    user = User(
        name=f"Membro {email}",
        email=email,
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="MB",
        color="#111827",
        is_active=True,
        is_internal_staff=False,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()
    membership = WorkspaceMembership(
        workspace_id=workspace_id,
        user_id=user.id,
        role=role,
        status=MembershipStatus.active,
        is_default=True,
        access_package=access_package,
    )
    db_session.add(membership)
    await db_session.flush()
    return user, membership


@pytest.mark.asyncio
async def test_my_access_package_returns_catalog_entry_for_known_package(client, db_session, auth_context):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.access_package = "lawyer"
    await db_session.flush()
    await db_session.commit()

    response = await client.get("/api/v1/workspaces/me/access-package")

    assert response.status_code == 200
    payload = response.json()
    assert payload["key"] == "lawyer"
    assert payload["label"] == "Advogado"
    assert any(p["module"] == "lead_data" and p["action"] == "edit" for p in payload["permissions"])


@pytest.mark.asyncio
async def test_my_access_package_synthesizes_custom_package_when_no_catalog_match(client, db_session, auth_context):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.access_package = None
    await db_session.flush()
    await db_session.commit()

    response = await client.get("/api/v1/workspaces/me/access-package")

    assert response.status_code == 200
    payload = response.json()
    assert payload["key"] == "custom"
    assert payload["workspaceRole"] == WorkspaceRole.workspace_owner.value


@pytest.mark.asyncio
async def test_request_access_notifies_active_admins_not_requester(client, db_session, auth_context):
    # auth_context já é workspace_owner (não deve ser notificado); cria um segundo
    # admin ativo e um membro comum (não deve ser notificado).
    admin_user, _ = await _add_member(
        db_session,
        workspace_id=auth_context.workspace_id,
        role=WorkspaceRole.workspace_admin,
        email="admin2@example.com",
    )
    _, secretary_membership = await _add_member(
        db_session,
        workspace_id=auth_context.workspace_id,
        role=WorkspaceRole.operator,
        access_package="secretary",
        email="secretaria@example.com",
    )
    await db_session.commit()

    response = await client.post(
        "/api/v1/notifications/request-access",
        json={"module": "kanban", "moduleLabel": "Funil de vendas"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["notifiedAdmins"] == 1

    result = await db_session.execute(select(Notification).where(Notification.user_id == admin_user.id))
    notifications = result.scalars().all()
    assert len(notifications) == 1
    assert "Funil de vendas" in notifications[0].message

    # o próprio solicitante (workspace_owner) não recebe notificação de si mesmo
    result = await db_session.execute(
        select(Notification).where(Notification.user_id == auth_context.user_id)
    )
    assert result.scalars().all() == []

    # o membro comum (secretaria, não-admin) também não recebe
    result = await db_session.execute(
        select(Notification).where(Notification.user_id == secretary_membership.user_id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_request_access_404_when_no_admin_available(client, db_session, auth_context):
    # Rebaixa o único membership (o próprio solicitante) para um papel não-admin,
    # de forma que nenhum administrador ativo exista para receber o pedido.
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.role = WorkspaceRole.operator
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        "/api/v1/notifications/request-access",
        json={"module": "kanban"},
    )

    assert response.status_code == 404
