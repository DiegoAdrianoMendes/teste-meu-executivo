from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.v1.endpoints.platform_tasks import router
from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_audit import audit_platform_mutation
from app.models.identity import UserSession
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.platform_task import PlatformTask, PlatformTaskEvent
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus


def _application() -> FastAPI:
    application = FastAPI()
    application.include_router(
        router,
        prefix="/api/v1",
        dependencies=[Depends(audit_platform_mutation)],
    )
    return application


async def _client(db_session, context: AuthContext):
    application = _application()

    async def override_db():
        try:
            yield db_session
        except Exception:
            await db_session.rollback()
            raise

    async def override_context():
        return context

    application.dependency_overrides[get_db] = override_db
    application.dependency_overrides[get_current_auth_context] = override_context
    return AsyncClient(transport=ASGITransport(app=application), base_url="http://test")


@pytest.mark.asyncio
async def test_platform_task_api_commits_canonical_audit_and_returns_409_for_stale_write(
    db_session,
    platform_auth_context,
) -> None:
    worker = User(
        name="Executor API",
        email=f"executor-api-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="EA",
        color="#172554",
        is_active=True,
        is_internal_staff=True,
        platform_role_package="support",
        platform_access_active=True,
    )
    workspace = Workspace(
        name="Conta vinculada à tarefa API",
        slug=f"platform-task-api-{uuid4().hex[:12]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add_all((worker, workspace))
    normal_session = UserSession(
        user_id=platform_auth_context.user_id,
        refresh_jti_digest=uuid4().hex + uuid4().hex,
        device_label="Blackbox em teste",
        device_type="desktop",
        browser="Chromium",
        operating_system="Linux",
        last_seen_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(normal_session)
    await db_session.commit()
    await db_session.refresh(normal_session)
    workspace_id = workspace.id
    # Reproduz o token real: esse ID pertence a user_sessions e jamais pode
    # preencher uma FK para administrative_sessions.
    platform_auth_context.session_id = normal_session.id
    correlation_id = f"platform-task-api:{uuid4()}"

    async with await _client(db_session, platform_auth_context) as client:
        created = await client.post(
            "/api/v1/platform/tasks",
            headers={"X-Correlation-ID": correlation_id},
            json={
                "title": "Validar operação interna",
                "ownerUserId": str(platform_auth_context.user_id),
                "workspaceId": str(workspace_id),
                "participants": [
                    {
                        "userId": str(worker.id),
                        "role": "assignee",
                        "requiredForCompletion": True,
                    }
                ],
                "reason": "Tarefa interna criada pela API do Blackbox.",
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        task_id = UUID(body["id"])
        assert body["version"] == 1
        assert body["participants"][0]["labelSnapshot"] == "Executor API"
        assert set(body["allowedCommands"]) == {"start", "block", "cancel"}

        started = await client.post(
            f"/api/v1/platform/tasks/{task_id}/commands/start",
            headers={"X-Correlation-ID": f"{correlation_id}:start"},
            json={"expectedVersion": 1},
        )
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "in_progress"
        assert started.json()["version"] == 2
        assert set(started.json()["allowedCommands"]) == {"block", "cancel"}

        stale_correlation_id = f"{correlation_id}:stale"
        stale = await client.patch(
            f"/api/v1/platform/tasks/{task_id}",
            headers={"X-Correlation-ID": stale_correlation_id},
            json={
                "expectedVersion": 999,
                "title": "Não pode persistir",
                "reason": "Versão obsoleta precisa falhar fechada.",
            },
        )
        assert stale.status_code == 409, stale.text
        assert stale.json()["detail"]["code"] == "PLATFORM-TASK-VERSION-CONFLICT"

        for required_field in ("title", "priority"):
            invalid_null = await client.patch(
                f"/api/v1/platform/tasks/{task_id}",
                json={
                    "expectedVersion": 2,
                    required_field: None,
                    "reason": "Campo obrigatório não pode ser apagado.",
                },
            )
            assert invalid_null.status_code == 422, invalid_null.text

        clear_workspace_correlation_id = f"{correlation_id}:clear-workspace"
        cleared = await client.patch(
            f"/api/v1/platform/tasks/{task_id}",
            headers={"X-Correlation-ID": clear_workspace_correlation_id},
            json={
                "expectedVersion": 2,
                "workspaceId": None,
                "reason": "Remover vínculo mantendo atribuição histórica da conta.",
            },
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["workspaceId"] is None
        assert cleared.json()["version"] == 3

    task = await db_session.get(PlatformTask, task_id)
    assert task is not None
    assert task.title == "Validar operação interna"
    assert task.workspace_id is None
    event = await db_session.scalar(
        select(PlatformTaskEvent).where(PlatformTaskEvent.task_id == task_id)
    )
    audit = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "platform_task",
            PlatformAuditEvent.resource_id == str(task_id),
            PlatformAuditEvent.action == "platform_task.created",
        )
    )
    assert event is not None
    assert audit is not None
    assert event.administrative_session_id is None
    assert event.session_type == "normal"
    assert audit.administrative_session_id is None
    assert audit.session_type == "normal"
    assert audit.correlation_id == correlation_id
    assert audit.is_platform_root is True
    assert audit.workspace_id == workspace_id
    assert audit.before is None
    assert audit.after["title"] == "Validar operação interna"
    assert (
        await db_session.scalar(
            select(func.count(PlatformAuditEvent.id)).where(
                PlatformAuditEvent.correlation_id == correlation_id
            )
        )
        == 1
    )
    command_audit = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_id == str(task_id),
            PlatformAuditEvent.action == "platform_task.start",
        )
    )
    assert command_audit is not None
    assert command_audit.before["status"] == "pending"
    assert command_audit.after["status"] == "in_progress"
    failed_audit = await db_session.scalar(
        select(PlatformAuditEvent).where(PlatformAuditEvent.correlation_id == stale_correlation_id)
    )
    assert failed_audit is not None
    assert failed_audit.result == "failed"
    assert failed_audit.error_code == "PLATFORM-TASK-VERSION-CONFLICT"
    cleared_audit = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.correlation_id == clear_workspace_correlation_id
        )
    )
    assert cleared_audit is not None
    assert cleared_audit.workspace_id == workspace_id
    assert cleared_audit.before["workspaceId"] == str(workspace_id)
    assert cleared_audit.after["workspaceId"] is None


@pytest.mark.asyncio
async def test_platform_task_api_denies_customer_and_honors_explicit_non_root_denial(
    db_session,
) -> None:
    customer = User(
        name="Cliente sem Blackbox",
        email=f"cliente-blackbox-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="CB",
        color="#111827",
        is_active=True,
        is_internal_staff=False,
        platform_access_active=False,
    )
    staff = User(
        name="Funcionário com leitura",
        email=f"staff-read-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="FL",
        color="#172554",
        is_active=True,
        is_internal_staff=True,
        platform_role_package="support",
        platform_access_active=True,
        platform_permission_denials=[{"module": "platform_tasks", "action": "create"}],
    )
    db_session.add_all([customer, staff])
    await db_session.commit()
    customer_context = AuthContext(
        user_id=customer.id,
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=False,
        surface="platform",
    )
    staff_context = AuthContext(
        user_id=staff.id,
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )

    async with await _client(db_session, customer_context) as client:
        denied = await client.get("/api/v1/platform/tasks")
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"]["code"] == "PLATFORM-TASK-PERMISSION-DENIED"

    async with await _client(db_session, staff_context) as client:
        allowed = await client.get("/api/v1/platform/tasks")
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["total"] == 0
        denied_correlation_id = f"platform-task-denied:{uuid4()}"
        denied_create = await client.post(
            "/api/v1/platform/tasks",
            headers={"X-Correlation-ID": denied_correlation_id},
            json={
                "title": "Não autorizado",
                "ownerUserId": str(staff.id),
                "reason": "Permissão de leitura não permite criar.",
            },
        )
        assert denied_create.status_code == 403, denied_create.text

    denied_audit = await db_session.scalar(
        select(PlatformAuditEvent).where(PlatformAuditEvent.correlation_id == denied_correlation_id)
    )
    assert denied_audit is not None
    assert denied_audit.result == "denied"
    assert denied_audit.error_code == "PLATFORM-TASK-PERMISSION-DENIED"


@pytest.mark.asyncio
async def test_platform_task_api_defaults_to_mine_and_rejects_cross_team_access(
    db_session,
    platform_auth_context,
) -> None:
    support = User(
        name="Suporte escopado",
        email=f"support-scope-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="SE",
        color="#172554",
        is_active=True,
        is_internal_staff=True,
        platform_role_package="support",
        platform_access_active=True,
    )
    other = User(
        name="Engenharia alheia",
        email=f"engineering-scope-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="EA",
        color="#172554",
        is_active=True,
        is_internal_staff=True,
        platform_role_package="engineering",
        platform_access_active=True,
    )
    db_session.add_all([support, other])
    await db_session.commit()
    support_id = support.id
    other_id = other.id

    async with await _client(db_session, platform_auth_context) as root_client:
        unrelated = await root_client.post(
            "/api/v1/platform/tasks",
            json={
                "title": "Incidente reservado à engenharia",
                "ownerUserId": str(other_id),
                "reason": "Incidente criado para testar isolamento por linha.",
            },
        )
        assert unrelated.status_code == 201, unrelated.text
        unrelated_id = unrelated.json()["id"]

    support_context = AuthContext(
        user_id=support_id,
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )
    async with await _client(db_session, support_context) as support_client:
        own = await support_client.post(
            "/api/v1/platform/tasks",
            json={
                "title": "Retorno ao cliente",
                "ownerUserId": str(support_id),
                "reason": "Tarefa pessoal criada pelo próprio funcionário.",
            },
        )
        assert own.status_code == 201, own.text
        assert set(own.json()["allowedCommands"]) == {
            "start",
            "block",
            "complete",
            "cancel",
        }

        mine = await support_client.get("/api/v1/platform/tasks")
        assert mine.status_code == 200, mine.text
        assert [item["id"] for item in mine.json()["items"]] == [own.json()["id"]]

        platform = await support_client.get("/api/v1/platform/tasks?scope=platform")
        assert platform.status_code == 403, platform.text
        assert platform.json()["detail"]["code"] == "PLATFORM-TASK-PLATFORM-SCOPE-DENIED"

        forbidden_get = await support_client.get(f"/api/v1/platform/tasks/{unrelated_id}")
        assert forbidden_get.status_code == 403, forbidden_get.text
        assert forbidden_get.json()["detail"]["code"] == "PLATFORM-TASK-ROW-DENIED"

        forbidden_assignment = await support_client.post(
            "/api/v1/platform/tasks",
            json={
                "title": "Tentativa de atribuição",
                "ownerUserId": str(other_id),
                "reason": "Funcionário sem assign não pode escolher terceiros.",
            },
        )
        assert forbidden_assignment.status_code == 403, forbidden_assignment.text


def test_platform_task_mutations_declare_snapshot_contracts() -> None:
    operations = _application().openapi()["paths"]
    mutations = {
        "/api/v1/platform/tasks": ["post"],
        "/api/v1/platform/tasks/{task_id}": ["patch"],
        "/api/v1/platform/tasks/{task_id}/assignments": ["put"],
        "/api/v1/platform/tasks/{task_id}/participants/{user_id}/state": ["put"],
        "/api/v1/platform/tasks/{task_id}/commands/{command}": ["post"],
        "/api/v1/platform/tasks/{task_id}/comments": ["post"],
    }
    for path, methods in mutations.items():
        for method in methods:
            descriptor = operations[path][method]["x-platform-mutation"]
            assert descriptor["module"] == "platform_tasks"
            assert descriptor["snapshotAdapter"] == "platform_task"
            assert descriptor["mutationKind"] == "command"
            assert descriptor["externalEffect"] is False
            assert descriptor["auditSchemaVersion"] == 1
