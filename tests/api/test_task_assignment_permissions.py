from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.workspace_membership import WorkspaceMembership


async def _matter(client) -> dict:
    lead = await client.post(
        "/api/v1/clients",
        json={
            "name": "Cliente RBAC Task",
            "email": f"task-rbac-{uuid4().hex[:10]}@example.com",
        },
    )
    assert lead.status_code == 201, lead.text
    matter = await client.post(
        "/api/v1/matters",
        json={
            "clientId": lead.json()["id"],
            "practiceArea": "civel",
            "demandType": "Demanda de RBAC",
            "columnId": "novo_lead",
            "microStatus": "lead_novo",
        },
    )
    assert matter.status_code == 201, matter.text
    return matter.json()


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_explicit_assignment_requires_tasks_assign_on_create_and_update(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    matter = await _matter(client)
    membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.id == auth_context.membership_id,
            WorkspaceMembership.workspace_id == auth_context.workspace_id,
        )
    )
    assert membership is not None
    membership.permission_denials = [{"module": "tasks", "action": "assign", "scope": "workspace"}]
    await db_session.commit()

    # Criar a própria tarefa continua autorizado; owner implícito é o ator.
    created = await client.post(
        f"/api/v1/matters/{matter['id']}/tasks",
        json={"title": "Owner implícito permitido"},
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]

    for payload in (
        {
            "title": "Owner explícito",
            "ownerId": str(auth_context.user_id),
        },
        {
            "title": "Assigned explícito",
            "assignedTo": str(auth_context.user_id),
        },
        {
            "title": "Participantes explícitos",
            "participants": [{"userId": str(auth_context.user_id), "role": "owner"}],
        },
    ):
        denied = await client.post(
            f"/api/v1/matters/{matter['id']}/tasks",
            json=payload,
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"]["code"] == "AUTH-012"

    edited = await client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"expectedVersion": 1, "title": "Edição sem reatribuição"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 2

    denied_update = await client.patch(
        f"/api/v1/tasks/{task_id}",
        json={
            "expectedVersion": 2,
            "assignedTo": str(auth_context.user_id),
        },
    )
    assert denied_update.status_code == 403, denied_update.text
    assert denied_update.json()["detail"]["code"] == "AUTH-012"
