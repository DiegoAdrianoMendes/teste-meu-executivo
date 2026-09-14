from uuid import UUID, uuid4

import pytest

from app.models.workspace import Workspace, WorkspaceStatus


async def _workspace(db_session, suffix: str) -> Workspace:
    workspace = Workspace(
        name=f"Conta em lote {suffix}",
        slug=f"conta-em-lote-{suffix}-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        is_active=True,
        onboarding_completed=False,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


@pytest.mark.asyncio
async def test_legacy_bulk_action_requires_preview_and_does_not_mutate(platform_client, db_session):
    workspace = await _workspace(db_session, "legacy")

    response = await platform_client.post(
        "/api/v1/platform/workspace-bulk-actions",
        json={
            "workspaceIds": [str(workspace.id)],
            "action": "suspend",
            "reason": "Tentativa pelo endpoint legado sem prévia",
        },
    )

    assert response.status_code == 428
    assert response.json()["detail"]["code"] == "PLATFORM-BULK-PREVIEW-REQUIRED"
    persisted = await db_session.get(Workspace, workspace.id)
    assert persisted is not None
    assert persisted.status == WorkspaceStatus.active
    assert persisted.is_active is True


@pytest.mark.asyncio
async def test_operational_platform_admin_never_inherits_root_bulk_bypass(
    platform_client,
    platform_auth_context,
):
    created = await platform_client.post(
        "/api/v1/platform/staff",
        json={
            "name": "Admin operacional do lote",
            "email": f"admin-lote-{uuid4().hex[:8]}@example.com",
            "rolePackage": "platform_admin",
        },
    )
    assert created.status_code == 201
    platform_auth_context.user_id = UUID(created.json()["id"])

    response = await platform_client.post(
        "/api/v1/platform/workspace-bulk-actions",
        json={
            "workspaceIds": [str(uuid4())],
            "action": "suspend",
            "reason": "Tentativa sem autoridade raiz",
        },
    )

    assert response.status_code == 403
