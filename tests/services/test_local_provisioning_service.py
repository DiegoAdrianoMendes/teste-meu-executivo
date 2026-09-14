from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.configuration import ConfigDocument, ConfigDocumentVersion
from app.models.klm_operations import ExternalActionIntent
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.local_provisioning_service import (
    LOCAL_PROVISIONING_NAMESPACE,
    LocalProvisioningConflictError,
    LocalProvisioningService,
)


@pytest.mark.asyncio
async def test_apply_is_local_idempotent_and_covers_every_pinned_motor(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(
        "socket.socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network forbidden")),
    )
    service = LocalProvisioningService(db_session)

    preview = await service.preview(auth_context.workspace_id)
    created = await service.apply(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        idempotency_key="provisioning-apply-001",
        reason="Provisionamento inicial local",
    )
    replayed = await service.apply(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        idempotency_key="provisioning-apply-001",
        reason="Provisionamento inicial local",
    )

    assert preview.creates == len(preview.resources) == 10
    assert {item.provider for item in preview.resources} == {
        "klm",
        "chatwoot",
        "rocketchat",
        "chattermate",
        "bottlecrm",
        "embeddedchat",
    }
    assert created.replayed is False
    assert created.state.ready is True
    assert created.plan.noops == 10
    assert created.external_effects == 0
    assert replayed.replayed is True
    assert replayed.intent_id == created.intent_id
    assert await db_session.scalar(select(func.count(ExternalActionIntent.id))) == 1
    assert await db_session.scalar(select(func.count(ConfigDocumentVersion.id))) == 1


@pytest.mark.asyncio
async def test_reconcile_repairs_drift_and_keeps_other_workspace_unconfigured(
    db_session,
    auth_context,
):
    service = LocalProvisioningService(db_session)
    await service.apply(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        idempotency_key="provisioning-drift-base",
    )
    document = await db_session.scalar(
        select(ConfigDocument).where(
            ConfigDocument.workspace_id == auth_context.workspace_id,
            ConfigDocument.namespace == LOCAL_PROVISIONING_NAMESPACE,
        )
    )
    assert document is not None
    payload = dict(document.payload)
    resources = {key: dict(value) for key, value in payload["resources"].items()}
    resources["chatwoot:inbox:commercial"]["observedHash"] = "drifted"
    payload["resources"] = resources
    document.payload = payload
    await db_session.flush()

    drifted = await service.preview(auth_context.workspace_id)
    repaired = await service.reconcile(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        idempotency_key="provisioning-reconcile-001",
    )

    assert drifted.updates == 1
    assert repaired.state.ready is True
    assert repaired.plan.updates == 0
    assert repaired.external_effects == 0

    foreign_workspace = Workspace(
        id=uuid4(),
        name="Workspace isolado",
        slug=f"workspace-isolado-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign = await service.state(foreign_workspace.id)
    assert foreign.configured is False
    assert foreign.ready is False
    assert all(item.action == "create" for item in foreign.resources)


@pytest.mark.asyncio
async def test_idempotency_key_cannot_cross_provisioning_commands(db_session, auth_context):
    service = LocalProvisioningService(db_session)
    await service.apply(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        idempotency_key="provisioning-shared-key",
    )

    with pytest.raises(LocalProvisioningConflictError, match="different"):
        await service.reconcile(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            idempotency_key="provisioning-shared-key",
        )
