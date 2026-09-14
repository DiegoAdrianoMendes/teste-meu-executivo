from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.executive import ExecutiveThread, ExecutiveThreadMode
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)


@pytest.mark.asyncio
async def test_executive_catalog_thread_and_honest_unavailable_round(
    client,
    active_subscription,
) -> None:
    profiles = await client.get("/api/v1/executives/profiles")
    assert profiles.status_code == 200, profiles.text
    assert [item["key"] for item in profiles.json()] == [
        "ceo",
        "coo",
        "cfo",
        "cro",
        "cpo",
        "cto",
        "cio",
        "chro",
    ]
    assert all(item["consultativeOnly"] for item in profiles.json())

    created = await client.post(
        "/api/v1/executives/threads", json={"mode": "direct", "selectedExecutives": ["cfo"]}
    )
    assert created.status_code == 201, created.text
    thread = created.json()
    assert thread["selectedExecutives"] == ["ceo", "cfo"]

    sent = await client.post(
        f"/api/v1/executives/threads/{thread['id']}/messages",
        json={"body": "@CFO como está o caixa?"},
    )
    assert sent.status_code == 202, sent.text
    round_payload = sent.json()
    assert round_payload["status"] == "failed"
    assert [item["messageKind"] for item in round_payload["messages"]] == ["question", "error"]
    assert "nenhuma resposta" in round_payload["messages"][1]["body"].lower()

    messages = await client.get(f"/api/v1/executives/threads/{thread['id']}/messages")
    assert messages.status_code == 200
    assert len(messages.json()) == 2


@pytest.mark.asyncio
async def test_executives_require_owner_or_admin(
    client,
    db_session: AsyncSession,
    auth_context,
    active_subscription,
) -> None:
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    assert membership is not None
    membership.role = WorkspaceRole.operator
    auth_context.role = WorkspaceRole.operator
    await db_session.flush()
    denied = await client.get("/api/v1/executives/profiles")
    assert denied.status_code == 403

    membership.role = WorkspaceRole.workspace_admin
    auth_context.role = WorkspaceRole.workspace_admin
    await db_session.flush()
    allowed = await client.get("/api/v1/executives/profiles")
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_thread_lookup_is_tenant_isolated(
    client,
    db_session: AsyncSession,
    auth_context,
    active_subscription,
) -> None:
    other = Workspace(
        name="Outro tenant",
        slug=f"outro-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other)
    await db_session.flush()
    foreign_thread = ExecutiveThread(
        workspace_id=other.id,
        created_by_user_id=auth_context.user_id,
        title="Thread de outro tenant",
        mode=ExecutiveThreadMode.direct,
        selected_executives=["ceo"],
    )
    db_session.add(foreign_thread)
    await db_session.flush()

    hidden = await client.get(f"/api/v1/executives/threads/{foreign_thread.id}")
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_direct_thread_rejects_multiple_specialists(
    client,
    active_subscription,
) -> None:
    response = await client.post(
        "/api/v1/executives/threads", json={"mode": "direct", "selectedExecutives": ["cfo", "coo"]}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "DIRECT_THREAD_PARTICIPANTS_INVALID"


@pytest.mark.asyncio
async def test_share_candidates_and_visibility_follow_multiple_active_acls(
    client,
    db_session: AsyncSession,
    auth_context,
    active_subscription,
) -> None:
    recipients: list[User] = []
    for index, role in enumerate(
        [WorkspaceRole.workspace_admin, WorkspaceRole.workspace_owner], start=1
    ):
        user = User(
            name=f"Destinatário {index}",
            email=f"destinatario-{index}@example.com",
            agent_type=AgentType.human,
            role=UserRole.admin,
            initials=f"D{index}",
            color="#111827",
            is_active=True,
            is_internal_staff=False,
        )
        db_session.add(user)
        await db_session.flush()
        db_session.add(
            WorkspaceMembership(
                workspace_id=auth_context.workspace_id,
                user_id=user.id,
                role=role,
                status=MembershipStatus.active,
                is_default=False,
            )
        )
        recipients.append(user)
    await db_session.flush()

    candidates = await client.get("/api/v1/executives/share-candidates")
    assert candidates.status_code == 200, candidates.text
    assert {item["id"] for item in candidates.json()} == {
        str(user.id) for user in recipients
    }

    created = await client.post(
        "/api/v1/executives/threads",
        json={"mode": "direct", "selectedExecutives": ["cfo"]},
    )
    thread_id = created.json()["id"]
    for user in recipients:
        shared = await client.post(
            f"/api/v1/executives/threads/{thread_id}/shares",
            json={"userId": str(user.id)},
        )
        assert shared.status_code == 201, shared.text

    after_shares = await client.get(f"/api/v1/executives/threads/{thread_id}")
    assert after_shares.json()["visibility"] == "shared"

    first_revoke = await client.delete(
        f"/api/v1/executives/threads/{thread_id}/shares/{recipients[0].id}"
    )
    assert first_revoke.status_code == 204
    one_remaining = await client.get(f"/api/v1/executives/threads/{thread_id}")
    assert one_remaining.json()["visibility"] == "shared"

    second_revoke = await client.delete(
        f"/api/v1/executives/threads/{thread_id}/shares/{recipients[1].id}"
    )
    assert second_revoke.status_code == 204
    none_remaining = await client.get(f"/api/v1/executives/threads/{thread_id}")
    assert none_remaining.json()["visibility"] == "private"
