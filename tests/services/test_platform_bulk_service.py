from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.platform_audit_event import PlatformAuditEvent
from app.models.platform_operations import PlatformBulkJobItem, PlatformSelectionItem
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.services.platform_bulk_service import PlatformBulkError, PlatformBulkService


async def _workspace(db_session, suffix: str, *, status: WorkspaceStatus = WorkspaceStatus.active):
    workspace = Workspace(
        name=f"Bulk workspace {suffix}",
        slug=f"bulk-workspace-{suffix}-{uuid4().hex[:8]}",
        status=status,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=status == WorkspaceStatus.active,
        onboarding_completed=False,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


@pytest.mark.asyncio
async def test_filter_selection_materializes_a_frozen_target_set(db_session, platform_auth_context):
    selected = await _workspace(db_session, "selected")
    await _workspace(db_session, "ignored", status=WorkspaceStatus.suspended)
    service = PlatformBulkService(db_session)

    selection = await service.create_selection(
        actor_user_id=platform_auth_context.user_id,
        resource_type="workspace",
        mode="filter",
        ids=[],
        filters={"status": "active"},
        sort=[{"field": "name", "direction": "asc"}],
        excluded_ids=[],
    )
    created_later = await _workspace(db_session, "created-later")

    items = list(
        (
            await db_session.scalars(
                select(PlatformSelectionItem)
                .where(PlatformSelectionItem.selection_set_id == selection.id)
                .order_by(PlatformSelectionItem.ordinal.asc())
            )
        ).all()
    )
    assert selection.target_count == 1
    assert [item.resource_id for item in items] == [selected.id]
    assert created_later.id not in {item.resource_id for item in items}
    assert selection.filter_hash


@pytest.mark.asyncio
async def test_preview_reports_eligible_noop_and_missing_targets(db_session, platform_auth_context):
    eligible = await _workspace(db_session, "eligible")
    noop = await _workspace(db_session, "noop", status=WorkspaceStatus.suspended)
    missing_id = uuid4()
    service = PlatformBulkService(db_session)
    selection = await service.create_selection(
        actor_user_id=platform_auth_context.user_id,
        resource_type="workspace",
        mode="explicit",
        ids=[eligible.id, noop.id, missing_id, eligible.id],
        filters={},
        sort=[],
        excluded_ids=[],
    )

    preview = await service.create_preview(
        actor_user_id=platform_auth_context.user_id,
        selection_set_id=selection.id,
        action="suspend",
        parameters={},
    )

    assert preview.target_count == 3
    assert preview.eligible_count == 1
    assert preview.no_op_count == 1
    assert preview.blocked_count == 1
    assert preview.eligible_targets == [
        {
            "resourceId": str(eligible.id),
            "resourceVersion": PlatformBulkService._workspace_version(eligible),
        }
    ]
    assert preview.confirmation_phrase == "CONFIRMAR 1 SUSPEND"
    assert preview.impact_summary["missing"] == 1


@pytest.mark.asyncio
async def test_job_preserves_success_and_reports_version_conflict(
    db_session, platform_auth_context
):
    first = await _workspace(db_session, "first")
    changed = await _workspace(db_session, "changed")
    service = PlatformBulkService(db_session)
    selection = await service.create_selection(
        actor_user_id=platform_auth_context.user_id,
        resource_type="workspace",
        mode="explicit",
        ids=[first.id, changed.id],
        filters={},
        sort=[],
        excluded_ids=[],
    )
    preview = await service.create_preview(
        actor_user_id=platform_auth_context.user_id,
        selection_set_id=selection.id,
        action="suspend",
        parameters={},
    )
    job = await service.create_job(
        actor_user_id=platform_auth_context.user_id,
        preview_id=preview.id,
        reason="Suspend accounts after reviewed preview",
        confirmation_phrase=preview.confirmation_phrase,
        idempotency_key="job-success-conflict",
    )
    changed.name = "Changed after preview"
    await db_session.commit()

    result = await service.run_job(job.id)
    items = await service.list_job_items(job.id)
    by_resource = {item.resource_id: item for item in items}

    assert result.status == "partial"
    assert result.succeeded_count == 1
    assert result.conflicted_count == 1
    assert by_resource[first.id].status == "succeeded"
    assert by_resource[first.id].before["status"] == "active"
    assert by_resource[first.id].after["status"] == "suspended"
    assert by_resource[changed.id].status == "conflict"
    await db_session.refresh(first)
    await db_session.refresh(changed)
    assert first.status == WorkspaceStatus.suspended
    assert changed.status == WorkspaceStatus.active

    retry_preview = await service.create_retry_preview(
        actor_user_id=platform_auth_context.user_id,
        original_job_id=result.id,
    )
    assert retry_preview.target_count == 1
    assert retry_preview.eligible_count == 1
    assert retry_preview.parameters["retryOfJobId"] == str(result.id)
    assert any(
        warning["code"] == "BULK_RETRY_REVALIDATED"
        for warning in retry_preview.warnings
    )


@pytest.mark.asyncio
async def test_running_job_cancels_cooperatively_between_items(db_session, platform_auth_context):
    first = await _workspace(db_session, "cancel-first")
    second = await _workspace(db_session, "cancel-second")
    service = PlatformBulkService(db_session)
    selection = await service.create_selection(
        actor_user_id=platform_auth_context.user_id,
        resource_type="workspace",
        mode="explicit",
        ids=[first.id, second.id],
        filters={},
        sort=[],
        excluded_ids=[],
    )
    preview = await service.create_preview(
        actor_user_id=platform_auth_context.user_id,
        selection_set_id=selection.id,
        action="complete_onboarding",
        parameters={},
    )
    job = await service.create_job(
        actor_user_id=platform_auth_context.user_id,
        preview_id=preview.id,
        reason="Validate cooperative cancellation boundary",
        confirmation_phrase=None,
        idempotency_key="job-cancel-running",
    )

    running = await service.run_job(job.id, max_items=1)
    assert running.status == "running"
    assert running.processed_count == 1
    cancelling = await service.request_cancel(job.id)
    assert cancelling.status == "cancelling"
    await db_session.commit()
    terminal = await service.run_job(job.id)
    items = await service.list_job_items(job.id)

    assert terminal.status == "partial"
    assert terminal.succeeded_count == 1
    assert terminal.cancelled_count == 1
    assert {item.status for item in items} == {"succeeded", "cancelled"}
    assert PlatformBulkService.job_payload(terminal)["progress_percent"] == 100.0


@pytest.mark.asyncio
async def test_queued_job_cancel_is_idempotent(db_session, platform_auth_context):
    workspace = await _workspace(db_session, "queued-cancel")
    service = PlatformBulkService(db_session)
    selection = await service.create_selection(
        actor_user_id=platform_auth_context.user_id,
        resource_type="workspace",
        mode="explicit",
        ids=[workspace.id],
        filters={},
        sort=[],
        excluded_ids=[],
    )
    preview = await service.create_preview(
        actor_user_id=platform_auth_context.user_id,
        selection_set_id=selection.id,
        action="complete_onboarding",
        parameters={},
    )
    job = await service.create_job(
        actor_user_id=platform_auth_context.user_id,
        preview_id=preview.id,
        reason="Cancel queued operation safely",
        confirmation_phrase=None,
        idempotency_key="job-cancel-queued",
    )

    first_cancel = await service.request_cancel(job.id)
    second_cancel = await service.request_cancel(job.id)
    item = await db_session.scalar(
        select(PlatformBulkJobItem).where(PlatformBulkJobItem.job_id == job.id)
    )

    assert first_cancel.status == "cancelled"
    assert second_cancel.status == "cancelled"
    assert second_cancel.cancelled_count == 1
    assert item is not None and item.status == "cancelled"


@pytest.mark.asyncio
async def test_customer_user_bulk_block_revalidates_and_revokes_sessions(
    db_session, platform_auth_context
):
    customer = User(
        name="Cliente de lote",
        email=f"cliente-lote-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="CL",
        color="#112233",
        is_active=True,
        is_internal_staff=False,
        auth_epoch=3,
    )
    db_session.add(customer)
    await db_session.flush()
    service = PlatformBulkService(db_session)
    selection = await service.create_selection(
        actor_user_id=platform_auth_context.user_id,
        resource_type="customer_user",
        mode="explicit",
        ids=[customer.id],
        filters={},
        sort=[],
        excluded_ids=[],
    )
    preview = await service.create_preview(
        actor_user_id=platform_auth_context.user_id,
        selection_set_id=selection.id,
        action="block",
        parameters={},
    )
    job = await service.create_job(
        actor_user_id=platform_auth_context.user_id,
        preview_id=preview.id,
        reason="Bloqueio operacional aprovado no teste",
        confirmation_phrase=None,
        idempotency_key="customer-user-block",
    )

    result = await service.run_job(job.id)
    await db_session.refresh(customer)

    assert result.resource_type == "customer_user"
    assert result.status == "succeeded"
    assert customer.is_active is False
    assert customer.auth_epoch == 4


@pytest.mark.asyncio
async def test_staff_bulk_never_operates_platform_owner(db_session, platform_auth_context):
    service = PlatformBulkService(db_session)
    selection = await service.create_selection(
        actor_user_id=platform_auth_context.user_id,
        resource_type="staff",
        mode="explicit",
        ids=[platform_auth_context.user_id],
        filters={},
        sort=[],
        excluded_ids=[],
    )

    preview = await service.create_preview(
        actor_user_id=platform_auth_context.user_id,
        selection_set_id=selection.id,
        action="suspend",
        parameters={},
    )

    assert preview.eligible_count == 0
    assert preview.blocked_count == 1
    assert preview.impact_summary["protected"] == 1
    assert preview.blockers[0]["reason"] == "protected_staff_authority"


@pytest.mark.asyncio
async def test_deferred_preview_reuses_the_persisted_preview_identifier(
    db_session, platform_auth_context
):
    workspace = await _workspace(db_session, "deferred-preview")
    service = PlatformBulkService(db_session)
    selection = await service.create_selection(
        actor_user_id=platform_auth_context.user_id,
        resource_type="workspace",
        mode="explicit",
        ids=[workspace.id],
        filters={},
        sort=[],
        excluded_ids=[],
    )
    requested = await service.create_deferred_preview(
        actor_user_id=platform_auth_context.user_id,
        selection_set_id=selection.id,
        action="complete_onboarding",
        parameters={},
    )
    requested_id = requested.id

    completed = await service.build_deferred_preview(requested_id)

    assert completed.id == requested_id
    assert completed.status == "ready"
    assert completed.resource_type == "workspace"
    assert completed.eligible_count == 1


@pytest.mark.asyncio
async def test_customer_user_bulk_never_blocks_the_last_active_workspace_owner(
    db_session, platform_auth_context
):
    workspace = await _workspace(db_session, "sole-owner")
    owner = User(
        name="Titular único",
        email=f"titular-unico-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="TU",
        color="#334455",
        is_active=True,
        is_internal_staff=False,
    )
    db_session.add(owner)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=owner.id,
            role=WorkspaceRole.workspace_owner,
            status=MembershipStatus.active,
            is_default=True,
        )
    )
    await db_session.flush()
    service = PlatformBulkService(db_session)
    selection = await service.create_selection(
        actor_user_id=platform_auth_context.user_id,
        resource_type="customer_user",
        mode="explicit",
        ids=[owner.id],
        filters={},
        sort=[],
        excluded_ids=[],
    )

    preview = await service.create_preview(
        actor_user_id=platform_auth_context.user_id,
        selection_set_id=selection.id,
        action="block",
        parameters={},
    )

    assert preview.eligible_count == 0
    assert preview.blocked_count == 1
    assert preview.blockers[0]["reason"] == "last_active_workspace_owner"


@pytest.mark.asyncio
async def test_bulk_restoration_reapplies_the_frozen_before_snapshot(
    db_session, platform_auth_context
):
    workspace = await _workspace(db_session, "restore-compatible")
    service = PlatformBulkService(db_session)
    selection = await service.create_selection(
        actor_user_id=platform_auth_context.user_id,
        resource_type="workspace",
        mode="explicit",
        ids=[workspace.id],
        filters={},
        sort=[],
        excluded_ids=[],
    )
    preview = await service.create_preview(
        actor_user_id=platform_auth_context.user_id,
        selection_set_id=selection.id,
        action="suspend",
        parameters={},
    )
    original = await service.create_job(
        actor_user_id=platform_auth_context.user_id,
        preview_id=preview.id,
        reason="Suspend before testing contextual restoration",
        confirmation_phrase=preview.confirmation_phrase,
        idempotency_key="restore-compatible-original",
    )
    await db_session.commit()
    original = await service.run_job(original.id)
    await db_session.refresh(workspace)
    assert original.status == "succeeded"
    assert workspace.status == WorkspaceStatus.suspended

    restore_preview = await service.create_restoration_preview(
        actor_user_id=platform_auth_context.user_id,
        original_job_id=original.id,
    )
    assert restore_preview.eligible_count == 1
    assert restore_preview.blocked_count == 0
    restore_job = await service.create_job(
        actor_user_id=platform_auth_context.user_id,
        preview_id=restore_preview.id,
        reason="Restore the reviewed account state from the original job",
        confirmation_phrase=restore_preview.confirmation_phrase,
        idempotency_key="restore-compatible-job",
    )
    await db_session.commit()

    restored = await service.run_job(restore_job.id)
    restored_items = await service.list_job_items(restored.id)
    await db_session.refresh(workspace)

    assert restored.action == "restore"
    assert restored.status == "succeeded"
    assert workspace.status == WorkspaceStatus.active
    assert workspace.is_active is True
    assert restored_items[0].before["status"] == "suspended"
    assert restored_items[0].after["status"] == "active"


@pytest.mark.asyncio
async def test_workspace_restoration_requires_commercial_contract_permission_at_preview(
    db_session, platform_auth_context
):
    workspace = await _workspace(db_session, "restore-commercial-permission")
    service = PlatformBulkService(db_session)
    selection = await service.create_selection(
        actor_user_id=platform_auth_context.user_id,
        resource_type="workspace",
        mode="explicit",
        ids=[workspace.id],
        filters={},
        sort=[],
        excluded_ids=[],
    )
    preview = await service.create_preview(
        actor_user_id=platform_auth_context.user_id,
        selection_set_id=selection.id,
        action="suspend",
        parameters={},
    )
    original = await service.create_job(
        actor_user_id=platform_auth_context.user_id,
        preview_id=preview.id,
        reason="Preparar restauração que poderá tocar estado comercial.",
        confirmation_phrase=preview.confirmation_phrase,
        idempotency_key="restore-commercial-permission-original",
    )
    await db_session.commit()
    original = await service.run_job(original.id)
    assert original.status == "succeeded"

    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    actor.platform_role_package = "onboarding"
    await db_session.flush()
    assert PlatformBulkService._requires_commercial_contract_management(
        "workspace",
        "restore",
    )
    assert PlatformBulkService._requires_commercial_contract_management(
        "subscription",
        "suspend",
    )
    assert PlatformBulkService._requires_commercial_contract_management(
        "subscription",
        "reactivate",
    )

    with pytest.raises(PlatformBulkError) as blocked:
        await service.create_restoration_preview(
            actor_user_id=actor.id,
            original_job_id=original.id,
        )
    assert blocked.value.code == "BULK_COMMERCIAL_PERMISSION_REQUIRED"
    assert blocked.value.status_code == 403

    actor.platform_role_package = "platform_owner"
    await db_session.flush()
    restore_preview = await service.create_restoration_preview(
        actor_user_id=actor.id,
        original_job_id=original.id,
    )

    actor.platform_role_package = "onboarding"
    await db_session.flush()
    restore_successes_before_create = int(
        (
            await db_session.scalar(
                select(func.count(PlatformAuditEvent.id)).where(
                    PlatformAuditEvent.action == "restore.execute",
                    PlatformAuditEvent.result == "success",
                )
            )
        )
        or 0
    )
    with pytest.raises(PlatformBulkError) as revoked_before_create:
        await service.create_job(
            actor_user_id=actor.id,
            preview_id=restore_preview.id,
            reason="Permissão removida entre a prévia e a criação.",
            confirmation_phrase=restore_preview.confirmation_phrase,
            idempotency_key="restore-revoked-before-create",
        )
    assert revoked_before_create.value.code == "BULK_COMMERCIAL_PERMISSION_REQUIRED"
    await db_session.refresh(workspace)
    assert workspace.status == WorkspaceStatus.suspended
    assert (
        int(
            (
                await db_session.scalar(
                    select(func.count(PlatformAuditEvent.id)).where(
                        PlatformAuditEvent.action == "restore.execute",
                        PlatformAuditEvent.result == "success",
                    )
                )
            )
            or 0
        )
        == restore_successes_before_create
    )

    actor.platform_role_package = "platform_owner"
    await db_session.flush()
    queued = await service.create_job(
        actor_user_id=actor.id,
        preview_id=restore_preview.id,
        reason="Preparar revalidação de permissão no worker.",
        confirmation_phrase=restore_preview.confirmation_phrase,
        idempotency_key="restore-revoked-before-worker",
    )
    await db_session.commit()

    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    actor.platform_role_package = "onboarding"
    await db_session.commit()
    denied_job = await service.run_job(queued.id)
    denied_items = await service.list_job_items(denied_job.id)
    await db_session.refresh(workspace)
    assert denied_job.status == "failed"
    assert [item.status for item in denied_items] == ["skipped"]
    assert workspace.status == WorkspaceStatus.suspended
    denied_audits = list(
        (
            await db_session.scalars(
                select(PlatformAuditEvent).where(
                    PlatformAuditEvent.bulk_job_id == denied_job.id
                )
            )
        ).all()
    )
    assert [event.result for event in denied_audits] == ["denied"]

    actor.platform_role_package = "platform_owner"
    await db_session.flush()
    root_preview = await service.create_restoration_preview(
        actor_user_id=actor.id,
        original_job_id=original.id,
    )
    root_job = await service.create_job(
        actor_user_id=actor.id,
        preview_id=root_preview.id,
        reason="Titular restaura após revalidação integral.",
        confirmation_phrase=root_preview.confirmation_phrase,
        idempotency_key="restore-root-success-after-revocation",
    )
    await db_session.commit()
    root_result = await service.run_job(root_job.id)
    await db_session.refresh(workspace)
    assert root_result.status == "succeeded"
    assert workspace.status == WorkspaceStatus.active
    root_audits = list(
        (
            await db_session.scalars(
                select(PlatformAuditEvent).where(
                    PlatformAuditEvent.bulk_job_id == root_result.id,
                    PlatformAuditEvent.result == "success",
                )
            )
        ).all()
    )
    assert len(root_audits) == 2


@pytest.mark.asyncio
async def test_bulk_restoration_preview_blocks_a_resource_changed_after_the_job(
    db_session, platform_auth_context
):
    workspace = await _workspace(db_session, "restore-stale")
    service = PlatformBulkService(db_session)
    selection = await service.create_selection(
        actor_user_id=platform_auth_context.user_id,
        resource_type="workspace",
        mode="explicit",
        ids=[workspace.id],
        filters={},
        sort=[],
        excluded_ids=[],
    )
    preview = await service.create_preview(
        actor_user_id=platform_auth_context.user_id,
        selection_set_id=selection.id,
        action="suspend",
        parameters={},
    )
    original = await service.create_job(
        actor_user_id=platform_auth_context.user_id,
        preview_id=preview.id,
        reason="Suspend before testing stale restoration protection",
        confirmation_phrase=preview.confirmation_phrase,
        idempotency_key="restore-stale-original",
    )
    await db_session.commit()
    original = await service.run_job(original.id)
    workspace.name = "Changed after bulk completion"
    await db_session.commit()

    restore_preview = await service.create_restoration_preview(
        actor_user_id=platform_auth_context.user_id,
        original_job_id=original.id,
    )

    assert restore_preview.eligible_count == 0
    assert restore_preview.blocked_count == 1
    assert restore_preview.blockers[0]["reason"] == "changed_after_bulk"


@pytest.mark.asyncio
async def test_bulk_restoration_rejects_irreversible_session_revocation(
    db_session, platform_auth_context
):
    customer = User(
        name="Cliente de revogação irreversível",
        email=f"cliente-revogacao-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="CR",
        color="#334455",
        is_active=True,
        is_internal_staff=False,
        auth_epoch=2,
    )
    db_session.add(customer)
    await db_session.flush()
    service = PlatformBulkService(db_session)
    selection = await service.create_selection(
        actor_user_id=platform_auth_context.user_id,
        resource_type="customer_user",
        mode="explicit",
        ids=[customer.id],
        filters={},
        sort=[],
        excluded_ids=[],
    )
    preview = await service.create_preview(
        actor_user_id=platform_auth_context.user_id,
        selection_set_id=selection.id,
        action="revoke_sessions",
        parameters={},
    )
    original = await service.create_job(
        actor_user_id=platform_auth_context.user_id,
        preview_id=preview.id,
        reason="Revoke sessions before testing irreversible protection",
        confirmation_phrase=preview.confirmation_phrase,
        idempotency_key="restore-irreversible-original",
    )
    await db_session.commit()
    original = await service.run_job(original.id)

    with pytest.raises(PlatformBulkError) as error:
        await service.create_restoration_preview(
            actor_user_id=platform_auth_context.user_id,
            original_job_id=original.id,
        )

    assert error.value.code == "BULK_RESTORE_IRREVERSIBLE"
