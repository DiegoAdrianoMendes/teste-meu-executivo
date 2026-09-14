from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.knowledge_feedback import KnowledgeArticleFeedback, KnowledgeFeedbackValue
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
async def test_submit_feedback_persists_and_returns_aggregate_counts(client, db_session, auth_context):
    response = await client.post(
        "/api/v1/knowledge/articles/conectar-whatsapp-manual/feedback",
        json={"value": "up"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["articleId"] == "conectar-whatsapp-manual"
    assert payload["upCount"] == 1
    assert payload["downCount"] == 0
    assert payload["myFeedback"] == "up"

    stored = await db_session.execute(
        select(KnowledgeArticleFeedback).where(
            KnowledgeArticleFeedback.workspace_id == auth_context.workspace_id,
            KnowledgeArticleFeedback.user_id == auth_context.user_id,
            KnowledgeArticleFeedback.article_id == "conectar-whatsapp-manual",
        )
    )
    row = stored.scalar_one()
    assert row.value == KnowledgeFeedbackValue.up


@pytest.mark.asyncio
async def test_submit_feedback_twice_by_same_user_updates_instead_of_duplicating(
    client, db_session, auth_context
):
    first = await client.post(
        "/api/v1/knowledge/articles/disparo-seguro/feedback",
        json={"value": "up"},
    )
    second = await client.post(
        "/api/v1/knowledge/articles/disparo-seguro/feedback",
        json={"value": "down"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    payload = second.json()
    assert payload["upCount"] == 0
    assert payload["downCount"] == 1
    assert payload["myFeedback"] == "down"

    count = await db_session.scalar(
        select(func.count())
        .select_from(KnowledgeArticleFeedback)
        .where(
            KnowledgeArticleFeedback.workspace_id == auth_context.workspace_id,
            KnowledgeArticleFeedback.user_id == auth_context.user_id,
            KnowledgeArticleFeedback.article_id == "disparo-seguro",
        )
    )
    assert count == 1


@pytest.mark.asyncio
async def test_feedback_summary_is_isolated_per_workspace(client, db_session, auth_context):
    other_workspace = Workspace(
        name="Workspace Conhecimento Externo",
        slug=f"knowledge-other-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()

    other_user = User(
        name="Usuário de outro workspace",
        email=f"outro-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="OW",
        color="#111827",
        is_active=True,
        is_internal_staff=False,
    )
    db_session.add(other_user)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=other_workspace.id,
            user_id=other_user.id,
            role=WorkspaceRole.workspace_owner,
            status=MembershipStatus.active,
            is_default=True,
        )
    )
    db_session.add(
        KnowledgeArticleFeedback(
            workspace_id=other_workspace.id,
            user_id=other_user.id,
            article_id="funil-mover-casos",
            value=KnowledgeFeedbackValue.up,
        )
    )
    await db_session.commit()

    response = await client.post(
        "/api/v1/knowledge/articles/funil-mover-casos/feedback",
        json={"value": "down"},
    )

    assert response.status_code == 200
    payload = response.json()
    # Só enxerga o próprio workspace: o "up" do outro tenant não entra na conta.
    assert payload["upCount"] == 0
    assert payload["downCount"] == 1
