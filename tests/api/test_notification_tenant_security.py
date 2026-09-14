from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.notification import Notification, NotificationType
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
async def test_mark_read_rejects_notification_from_another_workspace(
    client,
    db_session,
    auth_context,
):
    other_workspace = Workspace(
        name="Workspace Notificação Externa",
        slug=f"notification-other-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    own = Notification(
        user_id=auth_context.user_id,
        workspace_id=auth_context.workspace_id,
        title="Notificação própria",
        message="Permitida",
        notification_type=NotificationType.info,
    )
    other = Notification(
        user_id=auth_context.user_id,
        workspace_id=other_workspace.id,
        title="Notificação de outro tenant",
        message="Não deve ser alterada",
        notification_type=NotificationType.info,
    )
    db_session.add_all((own, other))
    await db_session.flush()

    rejected = await client.patch(f"/api/v1/notifications/{other.id}/read")
    accepted = await client.patch(f"/api/v1/notifications/{own.id}/read")

    assert rejected.status_code == 404
    assert accepted.status_code == 200
    await db_session.refresh(other)
    await db_session.refresh(own)
    assert other.is_read is False
    assert own.is_read is True
