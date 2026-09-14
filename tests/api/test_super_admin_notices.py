"""Testes focados do backend isolado da Central de Avisos."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - registra os relacionamentos ORM usados por User/Workspace
import app.services.super_admin_notice_service as notice_service_module
from app.api.v1.endpoints.super_admin_notices import router as notice_router
from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_audit import audit_platform_mutation
from app.db.base import Base
from app.main import app as main_app
from app.models.auth_security import AuthSecurityEvent
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.super_admin_notice import (
    SuperAdminNotice,
    SuperAdminNoticeAuditAction,
    SuperAdminNoticeAuditEvent,
    SuperAdminNoticeIdempotencyRecord,
    SuperAdminNoticeReceipt,
    SuperAdminNoticeReceiptState,
    SuperAdminNoticeReceiptWorkspace,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.services.platform_audit_event_service import PlatformAuditEventService

ISOLATED_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
isolated_engine = create_async_engine(
    ISOLATED_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
IsolatedSession = async_sessionmaker(
    isolated_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
NOTICE_TABLES = [
    User.__table__,
    Workspace.__table__,
    WorkspaceMembership.__table__,
    SuperAdminNotice.__table__,
    SuperAdminNoticeReceipt.__table__,
    SuperAdminNoticeReceiptWorkspace.__table__,
    SuperAdminNoticeAuditEvent.__table__,
    SuperAdminNoticeIdempotencyRecord.__table__,
    PlatformAuditEvent.__table__,
    AuthSecurityEvent.__table__,
]


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def dispose_isolated_engine():
    yield
    await isolated_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def isolated_schema():
    async with isolated_engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=NOTICE_TABLES,
            )
        )
    yield
    async with isolated_engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.drop_all(
                sync_connection,
                tables=list(reversed(NOTICE_TABLES)),
            )
        )


@pytest_asyncio.fixture
async def db_session(isolated_schema):
    async with IsolatedSession() as session:
        yield session


@pytest_asyncio.fixture
async def platform_auth_context(db_session: AsyncSession) -> AuthContext:
    user = await _user(
        db_session,
        name="Platform Admin",
        email="platform-notices@example.com",
        role=UserRole.admin,
        internal=True,
        access_active=True,
    )
    user.is_platform_admin = True
    user.platform_role_package = "platform_owner"
    await db_session.flush()
    return _auth_context(user)


@pytest_asyncio.fixture
async def auth_context(db_session: AsyncSession) -> AuthContext:
    workspace = await _workspace(db_session, "tenant-auth")
    user = await _user(
        db_session,
        name="Usuario Tenant",
        email="tenant-notices@example.com",
        role=UserRole.admin,
    )
    membership = await _membership(
        db_session,
        user=user,
        workspace=workspace,
        role=WorkspaceRole.workspace_owner,
    )
    return AuthContext(
        user_id=user.id,
        workspace_id=workspace.id,
        membership_id=membership.id,
        role=WorkspaceRole.workspace_owner,
        is_internal_staff=False,
    )


@pytest_asyncio.fixture
async def notice_api(db_session, platform_auth_context):
    state = {"context": platform_auth_context}
    app = FastAPI()
    app.include_router(
        notice_router,
        prefix="/api/v1",
        dependencies=[Depends(audit_platform_mutation)],
    )

    async def override_get_db():
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    async def override_auth_context():
        return state["context"]

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_auth_context] = override_auth_context
    await db_session.commit()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client, state


def _auth_context(user: User, workspace_id: UUID | None = None) -> AuthContext:
    return AuthContext(
        user_id=user.id,
        workspace_id=workspace_id,
        membership_id=None,
        role=None,
        is_internal_staff=user.is_internal_staff,
        surface="platform" if user.is_internal_staff else "workspace",
    )


async def _user(
    db_session,
    *,
    name: str,
    email: str | None,
    role: UserRole = UserRole.lawyer,
    internal: bool = False,
    access_active: bool = False,
    role_package: str | None = None,
    grants: list[dict[str, str]] | None = None,
    denials: list[dict[str, str]] | None = None,
) -> User:
    user = User(
        name=name,
        email=email,
        agent_type=AgentType.human,
        role=role,
        initials="".join(part[0] for part in name.split()[:2]).upper(),
        color="#123456",
        is_active=True,
        is_internal_staff=internal,
        platform_access_active=access_active,
        platform_role_package=role_package,
        platform_permission_grants=grants or [],
        platform_permission_denials=denials or [],
        email_verified_at=datetime.now(UTC) if email else None,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _workspace(db_session, suffix: str) -> Workspace:
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


async def _membership(
    db_session,
    *,
    user: User,
    workspace: Workspace,
    role: WorkspaceRole,
) -> WorkspaceMembership:
    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=user.id,
        role=role,
        status=MembershipStatus.active,
        is_default=False,
    )
    db_session.add(membership)
    await db_session.flush()
    return membership


def _notice_payload(
    *,
    audience: dict,
    channels: list[str] | None = None,
    title: str = "Manutencao programada",
    acknowledgement: bool = False,
) -> dict:
    return {
        "title": title,
        "body": "A plataforma ficara indisponivel por alguns minutos.",
        "audience": audience,
        "channels": channels or ["in_app"],
        "requiresAcknowledgement": acknowledgement,
    }


def test_notice_commands_publish_canonical_snapshot_contracts() -> None:
    operations = main_app.openapi()["paths"]
    commands = {
        ("/api/v1/platform/notices", "post"): "platform_notice",
        ("/api/v1/platform/notices/dispatch-due", "post"): "platform_notice_dispatch",
        ("/api/v1/platform/notices/{notice_id}", "patch"): "platform_notice",
        ("/api/v1/platform/notices/{notice_id}/schedule", "post"): "platform_notice",
        ("/api/v1/platform/notices/{notice_id}/publish", "post"): "platform_notice",
        ("/api/v1/platform/notices/{notice_id}/cancel", "post"): "platform_notice",
    }
    for (path, method), expected_adapter in commands.items():
        descriptor = operations[path][method]["x-platform-mutation"]
        assert descriptor["mutationKind"] == "command", path
        assert descriptor["snapshotAdapter"] == expected_adapter, path


def test_platform_tasks_and_notices_use_transversal_audit_in_production_router() -> None:
    for path in ("/api/v1/platform/tasks", "/api/v1/platform/notices"):
        route = next(
            candidate
            for candidate in main_app.routes
            if isinstance(candidate, APIRoute)
            and candidate.path == path
            and "POST" in candidate.methods
        )
        assert any(
            dependency.call is audit_platform_mutation
            for dependency in route.dependant.dependencies
        )


async def _create_notice(
    client: AsyncClient,
    *,
    audience: dict,
    key: str,
    channels: list[str] | None = None,
    title: str = "Manutencao programada",
    acknowledgement: bool = False,
):
    return await client.post(
        "/api/v1/platform/notices",
        json=_notice_payload(
            audience=audience,
            channels=channels,
            title=title,
            acknowledgement=acknowledgement,
        ),
        headers={"Idempotency-Key": key},
    )


@pytest.mark.asyncio
async def test_management_uses_canonical_rbac_and_reserves_bypass_for_root(
    notice_api,
    db_session,
    auth_context,
):
    client, state = notice_api
    root_context = state["context"]
    state["context"] = auth_context
    payload = _notice_payload(audience={"kind": "global"})

    tenant_denied = await client.post(
        "/api/v1/platform/notices",
        json=payload,
        headers={"Idempotency-Key": "tenant-denied-001"},
    )
    assert tenant_denied.status_code == 403
    assert tenant_denied.json()["detail"]["code"] == "BLACKBOX-IDENTITY-REQUIRED"

    staff = await _user(
        db_session,
        name="Equipe Interna",
        email="staff-notices@example.com",
        internal=True,
        access_active=True,
    )
    await db_session.commit()
    state["context"] = _auth_context(staff)
    denied_correlation_id = f"notice-denied:{uuid4()}"
    package_only_denied = await client.post(
        "/api/v1/platform/notices",
        json=payload,
        headers={
            "Idempotency-Key": "staff-denied-001",
            "X-Correlation-ID": denied_correlation_id,
        },
    )
    assert package_only_denied.status_code == 403
    assert package_only_denied.json()["detail"]["code"] == "PLATFORM-PERMISSION-DENIED"
    denied_audit = await db_session.scalar(
        select(PlatformAuditEvent).where(PlatformAuditEvent.correlation_id == denied_correlation_id)
    )
    assert denied_audit is not None
    assert denied_audit.result == "denied"
    assert denied_audit.error_code == "PLATFORM-PERMISSION-DENIED"

    community_manager = await _user(
        db_session,
        name="Gestao de Comunidade",
        email="community-notices@example.com",
        internal=True,
        access_active=True,
        role_package="community_manager",
    )
    await db_session.commit()
    state["context"] = _auth_context(community_manager)
    community_allowed = await client.post(
        "/api/v1/platform/notices",
        json=payload,
        headers={"Idempotency-Key": "community-allowed-001"},
    )
    assert community_allowed.status_code == 201, community_allowed.text

    marketing_authorized = await _user(
        db_session,
        name="Marketing Autorizado",
        email="marketing-notices@example.com",
        internal=True,
        access_active=True,
        role_package="marketing_content",
        grants=[{"module": "announcements", "action": "manage"}],
    )
    platform_admin_denied = await _user(
        db_session,
        name="Admin Operacional Negado",
        email="admin-denied-notices@example.com",
        internal=True,
        access_active=True,
        role_package="platform_admin",
        denials=[{"module": "announcements", "action": "manage"}],
    )
    await db_session.commit()

    state["context"] = _auth_context(marketing_authorized)
    marketing_allowed = await client.post(
        "/api/v1/platform/notices",
        json=payload,
        headers={"Idempotency-Key": "marketing-allowed-001"},
    )
    state["context"] = _auth_context(platform_admin_denied)
    operational_admin_denied = await client.post(
        "/api/v1/platform/notices",
        json=payload,
        headers={"Idempotency-Key": "operational-admin-denied-001"},
    )
    root = await db_session.get(User, root_context.user_id)
    root.platform_permission_denials = [{"module": "announcements", "action": "manage"}]
    await db_session.commit()
    state["context"] = root_context
    root_allowed = await client.post(
        "/api/v1/platform/notices",
        json=payload,
        headers={"Idempotency-Key": "root-bypass-001"},
    )

    assert marketing_allowed.status_code == 201, marketing_allowed.text
    assert marketing_allowed.json()["status"] == "draft"
    assert operational_admin_denied.status_code == 403
    assert operational_admin_denied.json()["detail"]["code"] == "PLATFORM-PERMISSION-DENIED"
    assert root_allowed.status_code == 201, root_allowed.text


@pytest.mark.asyncio
async def test_create_is_idempotent_and_rejects_key_reuse_with_other_content(
    notice_api,
    db_session,
    monkeypatch,
):
    client, _ = notice_api
    payload = _notice_payload(audience={"kind": "global"})
    headers = {"Idempotency-Key": "notice-create-idempotent-001"}

    correlation_id = f"notice-create-{uuid4()}"
    first = await client.post(
        "/api/v1/platform/notices",
        json=payload,
        headers={**headers, "X-Correlation-ID": correlation_id},
    )
    replay = await client.post("/api/v1/platform/notices", json=payload, headers=headers)
    conflict_payload = {**payload, "title": "Outro conteudo"}
    conflict_correlation_id = f"notice-conflict:{uuid4()}"
    conflict = await client.post(
        "/api/v1/platform/notices",
        json=conflict_payload,
        headers={**headers, "X-Correlation-ID": conflict_correlation_id},
    )

    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "NOTICE-IDEMPOTENCY-CONFLICT"
    assert await db_session.scalar(select(func.count(SuperAdminNotice.id))) == 1
    assert (
        await db_session.scalar(
            select(func.count(SuperAdminNoticeAuditEvent.id)).where(
                SuperAdminNoticeAuditEvent.action == SuperAdminNoticeAuditAction.created
            )
        )
        == 1
    )
    canonical = list(
        await db_session.scalars(
            select(PlatformAuditEvent).where(
                PlatformAuditEvent.resource_type == "platform_notice",
                PlatformAuditEvent.action == "notice.create",
            )
        )
    )
    assert len(canonical) == 1
    assert canonical[0].correlation_id == correlation_id
    assert canonical[0].before is None
    assert canonical[0].after["bodyLength"] == len(payload["body"])
    assert canonical[0].after["bodySha256"]
    assert payload["body"] not in str(canonical[0].after)
    failed = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.correlation_id == conflict_correlation_id
        )
    )
    assert failed is not None
    assert failed.result == "failed"
    assert failed.error_code == "NOTICE-IDEMPOTENCY-CONFLICT"

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    before_count = int((await db_session.scalar(select(func.count(SuperAdminNotice.id)))) or 0)
    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await client.post(
            "/api/v1/platform/notices",
            json=_notice_payload(
                audience={"kind": "global"},
                title="Comunicado que precisa de auditoria",
            ),
            headers={"Idempotency-Key": "notice-audit-failure-001"},
        )
    after_count = int((await db_session.scalar(select(func.count(SuperAdminNotice.id)))) or 0)
    assert after_count == before_count


@pytest.mark.asyncio
async def test_preview_supports_global_workspace_user_and_simple_rule_audiences(
    notice_api,
    db_session,
):
    client, _ = notice_api
    owner = await _user(
        db_session,
        name="Owner Alvo",
        email="owner-target@example.com",
    )
    operator = await _user(
        db_session,
        name="Operador Alvo",
        email="operator-target@example.com",
    )
    first_workspace = await _workspace(db_session, "preview-a")
    second_workspace = await _workspace(db_session, "preview-b")
    await _membership(
        db_session,
        user=owner,
        workspace=first_workspace,
        role=WorkspaceRole.workspace_owner,
    )
    await _membership(
        db_session,
        user=owner,
        workspace=second_workspace,
        role=WorkspaceRole.workspace_owner,
    )
    await _membership(
        db_session,
        user=operator,
        workspace=first_workspace,
        role=WorkspaceRole.operator,
    )
    await db_session.commit()

    audiences = {
        "global": {"kind": "global"},
        "workspaces": {
            "kind": "workspaces",
            "workspaceIds": [str(first_workspace.id), str(second_workspace.id)],
        },
        "users": {"kind": "users", "userIds": [str(operator.id)]},
        "rule": {
            "kind": "rule",
            "rule": {
                "field": "workspace_role",
                "operator": "in",
                "values": ["workspace_owner"],
            },
        },
    }
    previews = {}
    for index, (kind, audience) in enumerate(audiences.items()):
        created = await _create_notice(
            client,
            audience=audience,
            key=f"preview-create-{index:02d}",
            title=f"Preview {kind}",
        )
        assert created.status_code == 201, created.text
        preview = await client.get(
            f"/api/v1/platform/notices/{created.json()['id']}/recipients/preview"
        )
        assert preview.status_code == 200, preview.text
        previews[kind] = preview.json()

    assert previews["global"]["totalRecipients"] >= 3
    assert previews["workspaces"]["totalRecipients"] == 2
    owner_preview = next(
        item for item in previews["workspaces"]["sample"] if item["userId"] == str(owner.id)
    )
    assert set(owner_preview["workspaceIds"]) == {
        str(first_workspace.id),
        str(second_workspace.id),
    }
    assert previews["users"]["totalRecipients"] == 1
    assert previews["users"]["sample"][0]["userId"] == str(operator.id)
    assert previews["rule"]["totalRecipients"] == 1
    assert previews["rule"]["sample"][0]["matchedBy"] == "rule:workspace_role"
    assert previews["users"]["sample"][0]["emailHint"] != operator.email


@pytest.mark.asyncio
async def test_publish_registers_local_receipts_and_read_ack_are_idempotent(
    notice_api,
    db_session,
):
    client, state = notice_api
    with_email = await _user(
        db_session,
        name="Destinataria Com Email",
        email="recipient@example.com",
    )
    without_email = await _user(
        db_session,
        name="Destinatario Sem Email",
        email=None,
    )
    await db_session.commit()
    created = await _create_notice(
        client,
        audience={
            "kind": "users",
            "userIds": [str(with_email.id), str(without_email.id)],
        },
        channels=["in_app", "email"],
        key="publish-create-001",
        acknowledgement=True,
    )
    notice_id = created.json()["id"]
    headers = {"Idempotency-Key": "publish-command-001"}

    published = await client.post(
        f"/api/v1/platform/notices/{notice_id}/publish",
        headers=headers,
    )
    replay = await client.post(
        f"/api/v1/platform/notices/{notice_id}/publish",
        headers=headers,
    )

    assert published.status_code == replay.status_code == 200
    assert replay.json() == published.json()
    publication = published.json()
    assert publication["notice"]["status"] == "published"
    assert publication["recipientCount"] == 2
    assert publication["receiptCount"] == 4
    assert publication["receiptsByState"] == {
        "email_blocked_missing_address": 1,
        "email_queued_local": 1,
        "in_app_available": 2,
    }
    assert publication["emailDeliveryMode"] == "local_queue_only"
    assert await db_session.scalar(select(func.count(SuperAdminNoticeReceipt.id))) == 4

    receipts_response = await client.get(f"/api/v1/platform/notices/{notice_id}/receipts")
    assert receipts_response.status_code == 200
    receipt_page = receipts_response.json()
    assert receipt_page["total"] == 4
    assert receipt_page["limit"] == 100
    assert receipt_page["offset"] == 0
    assert receipt_page["hasMore"] is False
    receipts = receipt_page["items"]
    assert all(item["externalDeliveryAttempted"] is False for item in receipts)
    email_receipts = [item for item in receipts if item["channel"] == "email"]
    assert {item["state"] for item in email_receipts} == {
        "email_queued_local",
        "email_blocked_missing_address",
    }
    assert all(item["deliveryMode"] == "local_queue_only" for item in email_receipts)

    bounded_receipts = await client.get(
        f"/api/v1/platform/notices/{notice_id}/receipts",
        params={"limit": 2, "offset": 0},
    )
    assert bounded_receipts.status_code == 200
    assert bounded_receipts.json()["hasMore"] is True
    assert len(bounded_receipts.json()["items"]) == 2

    audit_response = await client.get(
        f"/api/v1/platform/notices/{notice_id}/audit/page",
        params={"limit": 1, "offset": 0},
    )
    assert audit_response.status_code == 200, audit_response.text
    audit_page = audit_response.json()
    assert audit_page["total"] >= 2
    assert audit_page["limit"] == 1
    assert audit_page["offset"] == 0
    assert audit_page["hasMore"] is True
    assert len(audit_page["items"]) == 1

    state["context"] = _auth_context(with_email)
    inbox = await client.get("/api/v1/notices/inbox")
    assert inbox.status_code == 200
    assert inbox.json()["unreadCount"] == 1
    assert inbox.json()["pendingAcknowledgementCount"] == 1
    receipt_id = inbox.json()["items"][0]["receiptId"]

    read = await client.put(f"/api/v1/notices/inbox/{receipt_id}/read")
    read_replay = await client.put(f"/api/v1/notices/inbox/{receipt_id}/read")
    assert read.status_code == read_replay.status_code == 200
    assert read.json()["readAt"] == read_replay.json()["readAt"]
    acknowledged = await client.put(f"/api/v1/notices/inbox/{receipt_id}/acknowledgement")
    acknowledgement_replay = await client.put(f"/api/v1/notices/inbox/{receipt_id}/acknowledgement")
    assert acknowledged.status_code == acknowledgement_replay.status_code == 200
    assert acknowledged.json()["acknowledgedAt"] == acknowledgement_replay.json()["acknowledgedAt"]
    actions = list(
        (
            await db_session.scalars(
                select(SuperAdminNoticeAuditEvent.action).where(
                    SuperAdminNoticeAuditEvent.notice_id == UUID(notice_id)
                )
            )
        ).all()
    )
    assert actions.count(SuperAdminNoticeAuditAction.read) == 1
    assert actions.count(SuperAdminNoticeAuditAction.acknowledged) == 1

    state["context"] = _auth_context(without_email)
    foreign_receipt = await client.put(f"/api/v1/notices/inbox/{receipt_id}/read")
    assert foreign_receipt.status_code == 404


@pytest.mark.asyncio
async def test_workspace_notice_is_visible_only_in_a_matched_workspace(
    notice_api,
    db_session,
):
    client, state = notice_api
    root_context = state["context"]
    recipient = await _user(
        db_session,
        name="Destinatario Multitenant",
        email="multitenant-notice@example.com",
    )
    target_workspace = await _workspace(db_session, "notice-target")
    other_workspace = await _workspace(db_session, "notice-other")
    await _membership(
        db_session,
        user=recipient,
        workspace=target_workspace,
        role=WorkspaceRole.operator,
    )
    await _membership(
        db_session,
        user=recipient,
        workspace=other_workspace,
        role=WorkspaceRole.operator,
    )
    await db_session.commit()
    created = await _create_notice(
        client,
        audience={
            "kind": "workspaces",
            "workspaceIds": [str(target_workspace.id)],
        },
        key="workspace-scope-create-001",
    )
    notice_id = created.json()["id"]
    published = await client.post(
        f"/api/v1/platform/notices/{notice_id}/publish",
        headers={"Idempotency-Key": "workspace-scope-publish-001"},
    )
    assert published.status_code == 200, published.text

    state["context"] = _auth_context(recipient, other_workspace.id)
    hidden = await client.get("/api/v1/notices/inbox")
    assert hidden.status_code == 200
    assert hidden.json()["total"] == 0

    state["context"] = _auth_context(recipient, target_workspace.id)
    visible = await client.get("/api/v1/notices/inbox")
    assert visible.status_code == 200
    assert visible.json()["total"] == 1
    assert visible.json()["items"][0]["noticeId"] == notice_id
    state["context"] = root_context


@pytest.mark.asyncio
async def test_draft_update_uses_version_and_idempotency_guards(
    notice_api,
):
    client, _ = notice_api
    created = await _create_notice(
        client,
        audience={"kind": "global"},
        key="update-create-001",
    )
    notice_id = created.json()["id"]
    update_payload = {"expectedVersion": 1, "title": "Titulo revisado"}
    headers = {"Idempotency-Key": "update-command-001"}

    updated = await client.patch(
        f"/api/v1/platform/notices/{notice_id}",
        json=update_payload,
        headers=headers,
    )
    replay = await client.patch(
        f"/api/v1/platform/notices/{notice_id}",
        json=update_payload,
        headers=headers,
    )
    stale = await client.patch(
        f"/api/v1/platform/notices/{notice_id}",
        json={"expectedVersion": 1, "title": "Sobrescrita tardia"},
        headers={"Idempotency-Key": "update-stale-001"},
    )

    assert updated.status_code == replay.status_code == 200
    assert replay.json() == updated.json()
    assert updated.json()["title"] == "Titulo revisado"
    assert updated.json()["version"] == 2
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "NOTICE-VERSION-CONFLICT"


@pytest.mark.asyncio
async def test_scheduled_dispatch_and_cancel_withdraw_local_delivery(
    notice_api,
    db_session,
    monkeypatch,
    platform_auth_context,
):
    client, state = notice_api
    state["context"] = platform_auth_context
    recipient = await _user(
        db_session,
        name="Destinatario Agendado",
        email="scheduled@example.com",
    )
    await db_session.commit()
    base_time = datetime.now(UTC).replace(microsecond=0)
    monkeypatch.setattr(notice_service_module, "utc_now", lambda: base_time)
    created = await _create_notice(
        client,
        audience={"kind": "users", "userIds": [str(recipient.id)]},
        channels=["in_app", "email"],
        key="scheduled-create-001",
    )
    notice_id = created.json()["id"]
    scheduled_at = base_time + timedelta(hours=1)
    scheduled = await client.post(
        f"/api/v1/platform/notices/{notice_id}/schedule",
        json={"scheduledAt": scheduled_at.isoformat()},
        headers={"Idempotency-Key": "scheduled-command-001"},
    )
    assert scheduled.status_code == 200, scheduled.text
    assert scheduled.json()["status"] == "scheduled"

    too_early = await client.post(
        "/api/v1/platform/notices/dispatch-due",
        headers={"Idempotency-Key": "dispatch-before-due-001"},
    )
    assert too_early.status_code == 200
    assert too_early.json()["processedCount"] == 0

    monkeypatch.setattr(
        notice_service_module,
        "utc_now",
        lambda: base_time + timedelta(hours=2),
    )
    dispatched = await client.post(
        "/api/v1/platform/notices/dispatch-due",
        headers={"Idempotency-Key": "dispatch-after-due-001"},
    )
    dispatch_replay = await client.post(
        "/api/v1/platform/notices/dispatch-due",
        headers={"Idempotency-Key": "dispatch-after-due-001"},
    )
    assert dispatched.status_code == dispatch_replay.status_code == 200
    assert dispatch_replay.json() == dispatched.json()
    assert dispatched.json()["processedCount"] == 1
    assert dispatched.json()["publications"][0]["notice"]["status"] == "published"

    cancelled = await client.post(
        f"/api/v1/platform/notices/{notice_id}/cancel",
        headers={"Idempotency-Key": "cancel-published-001"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    states = set(
        (
            await db_session.scalars(
                select(SuperAdminNoticeReceipt.state).where(
                    SuperAdminNoticeReceipt.notice_id == UUID(notice_id)
                )
            )
        ).all()
    )
    assert states == {SuperAdminNoticeReceiptState.cancelled}
    state["context"] = _auth_context(recipient)
    inbox = await client.get("/api/v1/notices/inbox")
    assert inbox.status_code == 200
    assert inbox.json()["total"] == 0
