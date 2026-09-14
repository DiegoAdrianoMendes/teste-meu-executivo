from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.error_log import ErrorLog, ErrorSeverity, ErrorStatus
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus


async def _other_workspace(db_session, suffix: str = "other") -> Workspace:
    workspace = Workspace(
        name=f"Workspace {suffix}",
        slug=f"workspace-{suffix}-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


async def _error_log(db_session, workspace_id) -> ErrorLog:
    error = ErrorLog(
        error_code=f"ERR-{uuid4().hex[:6]}",
        error_hash=uuid4().hex + uuid4().hex,
        severity=ErrorSeverity.high,
        status=ErrorStatus.open,
        exception_type="RuntimeError",
        message="details redacted",
        workspace_id=workspace_id,
    )
    db_session.add(error)
    await db_session.flush()
    return error


@pytest.mark.asyncio
async def test_tenant_admin_can_patch_only_the_active_workspace(
    client,
    db_session,
    auth_context,
    active_subscription,
):
    other = await _other_workspace(db_session)

    denied = await client.patch(
        f"/api/v1/admin/workspaces/{other.id}",
        json={"name": "IDOR"},
    )
    allowed = await client.patch(
        f"/api/v1/admin/workspaces/{auth_context.workspace_id}",
        json={"name": "Workspace Ativo Atualizado"},
    )

    await db_session.refresh(other)
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "AUTH-013"
    assert other.name != "IDOR"
    assert allowed.status_code == 200
    assert allowed.json()["name"] == "Workspace Ativo Atualizado"


@pytest.mark.asyncio
async def test_internal_platform_context_cannot_bypass_workspace_boundary(
    platform_client,
    db_session,
):
    workspace = await _other_workspace(db_session, "platform")

    response = await platform_client.patch(
        f"/api/v1/admin/workspaces/{workspace.id}",
        json={"name": "Atualizado pela Plataforma"},
    )

    await db_session.refresh(workspace)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AUTH-SURFACE-MISMATCH"
    assert workspace.name != "Atualizado pela Plataforma"


@pytest.mark.asyncio
async def test_tenant_admin_cannot_read_summarize_or_mutate_global_error_logs(
    client,
    db_session,
    auth_context,
):
    error = await _error_log(db_session, auth_context.workspace_id)

    listed = await client.get("/api/v1/error-logs")
    summarized = await client.get("/api/v1/error-logs/summary")
    mutated = await client.patch(
        f"/api/v1/error-logs/{error.id}/status",
        json={"status": "resolved"},
    )

    await db_session.refresh(error)
    assert listed.status_code == 403
    assert summarized.status_code == 403
    assert mutated.status_code == 403
    assert error.status == ErrorStatus.open


@pytest.mark.asyncio
async def test_internal_staff_without_platform_role_cannot_access_global_error_logs(
    platform_client,
    db_session,
    platform_auth_context,
):
    user = await db_session.get(User, platform_auth_context.user_id)
    user.is_platform_admin = False
    error = await _error_log(db_session, None)
    error_id = error.id

    listed = await platform_client.get("/api/v1/error-logs")
    mutated = await platform_client.patch(
        f"/api/v1/error-logs/{error_id}/status",
        json={"status": "resolved", "resolved_by": "staff"},
    )

    error = await db_session.get(ErrorLog, error_id)
    assert error is not None
    assert listed.status_code == 403
    assert mutated.status_code == 403
    assert error.status == ErrorStatus.open


@pytest.mark.asyncio
async def test_platform_admin_flag_does_not_bypass_root_identity_requirements(
    platform_client,
    db_session,
    platform_auth_context,
):
    user = await db_session.get(User, platform_auth_context.user_id)
    user.is_internal_staff = False
    platform_auth_context.is_internal_staff = False
    await _error_log(db_session, None)

    response = await platform_client.get("/api/v1/error-logs")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "PLATFORM-PERMISSION-DENIED"


@pytest.mark.asyncio
async def test_error_status_is_atomic_and_records_canonical_diff(
    platform_client,
    db_session,
):
    error = await _error_log(db_session, None)
    response = await platform_client.patch(
        f"/api/v1/error-logs/{error.id}/status",
        json={
            "status": "resolved",
            "resolution_notes": "Correção validada no ambiente local integrado.",
            "reason": "Correção validada no ambiente local integrado.",
        },
    )
    assert response.status_code == 200, response.text
    await db_session.refresh(error)
    assert error.status == ErrorStatus.resolved

    event = await db_session.scalar(
        select(PlatformAuditEvent)
        .where(
            PlatformAuditEvent.resource_type == "error_log",
            PlatformAuditEvent.resource_id == str(error.id),
            PlatformAuditEvent.action == "update_error_log_status",
        )
        .order_by(PlatformAuditEvent.occurred_at.desc())
    )
    assert event is not None
    assert event.before["status"] == "open"
    assert event.after["status"] == "resolved"
    assert "status" in event.changed_paths
    assert event.reason == "Correção validada no ambiente local integrado."


@pytest.mark.asyncio
async def test_error_logs_have_server_total_and_stable_pages(
    platform_client,
    db_session,
):
    for _ in range(55):
        await _error_log(db_session, None)
    first = await platform_client.get(
        "/api/v1/error-logs",
        params={"status": "open", "limit": 25, "skip": 0},
    )
    second = await platform_client.get(
        "/api/v1/error-logs",
        params={"status": "open", "limit": 25, "skip": 25},
    )
    third = await platform_client.get(
        "/api/v1/error-logs",
        params={"status": "open", "limit": 25, "skip": 50},
    )
    assert first.status_code == second.status_code == third.status_code == 200
    assert first.json()["total"] == 55
    assert [
        len(first.json()["items"]),
        len(second.json()["items"]),
        len(third.json()["items"]),
    ] == [25, 25, 5]
    ids = [
        item["id"]
        for response in (first, second, third)
        for item in response.json()["items"]
    ]
    assert len(ids) == len(set(ids)) == 55
