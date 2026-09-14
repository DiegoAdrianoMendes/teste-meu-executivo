"""Provas do Estúdio Blackbox sobre os motores nativos da conta."""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from starlette.requests import Request

from app.core.config import settings
from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.main import app
from app.models.configuration import ConfigDocument
from app.models.flow import Flow
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.support_access import (
    SupportAccessGrant,
    SupportAccessScope,
    SupportAccessState,
)
from app.models.user import AgentType, User, UserRole
from app.schemas.configuration import DEFAULT_WORKSPACE_EXPERIENCE
from app.services.agent_governance_service import (
    AgentGovernanceService,
    AgentGovernanceViolation,
)
from app.services.configuration_service import ConfigurationService
from app.services.klm_agent_service import KlmAgentService
from app.services.platform_audit_event_service import PlatformAuditEventService
from app.services.support_access_service import SupportAccessGrantService


@pytest_asyncio.fixture
async def platform_client(db_session, platform_auth_context: AuthContext):
    """Exercise tenant mutations through the same root_direct context as AWS.

    The generic platform fixture intentionally models the ordinary Blackbox
    session.  This studio test module changes tenant configuration, so it must
    not silently normalize that session into permission to mutate an account.
    """
    root_direct_context = replace(
        platform_auth_context,
        administrative_session_id=uuid4(),
        administrative_session_type="root_direct",
    )

    async def override_get_db():
        # A 404/403 path is audited with a rollback.  Commit the test seed at
        # the request boundary just like the shared HTTP fixture so an audit
        # of a deliberately missing workspace cannot roll back the root actor.
        await db_session.commit()
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    async def override_context():
        return root_direct_context

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_auth_context] = override_context
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_root_configuration_mutation_requires_explicit_root_direct_session(
    db_session,
    auth_context,
    platform_auth_context,
) -> None:
    request = Request({"type": "http", "method": "POST", "path": "/studio"})
    service = SupportAccessGrantService(db_session)

    with pytest.raises(HTTPException) as denied:
        await service.authorize_platform_workspace_operation(
            actor_user_id=platform_auth_context.user_id,
            workspace_id=auth_context.workspace_id,
            required_scope=SupportAccessScope.configurations_write,
            request=request,
            require_root_direct=True,
        )
    assert denied.value.status_code == 401
    assert denied.value.detail["code"] == "SUPPORT-ROOT-DIRECT-REQUIRED"

    allowed = await service.authorize_platform_workspace_operation(
        actor_user_id=platform_auth_context.user_id,
        workspace_id=auth_context.workspace_id,
        required_scope=SupportAccessScope.configurations_write,
        request=request,
        administrative_session_type="root_direct",
        administrative_session_id=uuid4(),
        require_root_direct=True,
    )
    assert allowed is None


@pytest_asyncio.fixture
async def onboarding_blackbox_client(
    db_session,
) -> AsyncIterator[tuple[AsyncClient, User]]:
    user = User(
        name="Onboarding sem acesso implícito",
        email="onboarding-studio@blackbox.test",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="ON",
        color="#0e3d5c",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_access_active=True,
        platform_role_package="onboarding",
    )
    db_session.add(user)
    await db_session.flush()
    context = AuthContext(
        user_id=user.id,
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )

    async def override_get_db():
        await db_session.commit()
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            db_session.expunge_all()
            await db_session.rollback()
            raise

    async def override_context():
        return context

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_auth_context] = override_context
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client, user
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_studio_requires_active_workspace_grant_for_non_root_employee(
    auth_context,
    onboarding_blackbox_client,
) -> None:
    client, _ = onboarding_blackbox_client
    workspaces = await client.get(
        "/api/v1/platform/configuration-studio/workspaces"
    )
    assert workspaces.status_code == 200
    assert workspaces.json() == []

    response = await client.get(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/drafts"
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "SUPPORT-WORKSPACE-GRANT-REQUIRED"


@pytest.mark.asyncio
async def test_studio_enforces_read_and_write_scopes_from_active_grant(
    db_session,
    auth_context,
    onboarding_blackbox_client,
) -> None:
    client, user = onboarding_blackbox_client
    now = datetime.now(UTC)
    grant = SupportAccessGrant(
        workspace_id=auth_context.workspace_id,
        requester_user_id=user.id,
        reason="Configuração assistida solicitada pelo titular da conta.",
        ticket_reference="SAC-STUDIO-001",
        requested_scopes=[SupportAccessScope.configurations_read.value],
        requested_duration_minutes=30,
        state=SupportAccessState.active,
        requested_at=now,
        approved_scopes=[SupportAccessScope.configurations_read.value],
        approved_duration_minutes=30,
        approved_at=now,
        activated_by_user_id=user.id,
        activated_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db_session.add(grant)
    await db_session.flush()
    grant_id = grant.id

    workspaces = await client.get(
        "/api/v1/platform/configuration-studio/workspaces"
    )
    assert workspaces.status_code == 200, workspaces.text
    assert len(workspaces.json()) == 1
    option = workspaces.json()[0]
    assert option["id"] == str(auth_context.workspace_id)
    assert option["name"] == "Workspace Teste"
    assert option["slug"] == "workspace-teste"
    assert option["canRead"] is True
    assert option["canWrite"] is False
    assert option["expiresAt"] is not None
    assert option["readExpiresAt"] == option["expiresAt"]
    assert option["writeExpiresAt"] is None

    page = await client.get(
        "/api/v1/platform/configuration-studio/workspaces/page",
        params={"search": "workspace-teste", "limit": 1, "offset": 0},
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1
    assert page.json()["items"][0]["id"] == str(auth_context.workspace_id)
    assert page.json()["items"][0]["canWrite"] is False
    assert page.json()["hasMore"] is False

    exact = await client.get(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/access"
    )
    assert exact.status_code == 200, exact.text
    assert exact.json()["id"] == str(auth_context.workspace_id)
    assert exact.json()["canRead"] is True
    assert exact.json()["canWrite"] is False

    unavailable = await client.get(
        f"/api/v1/platform/configuration-studio/workspaces/{uuid4()}/access"
    )
    assert unavailable.status_code == 404
    assert (
        unavailable.json()["detail"]["code"]
        == "STUDIO-WORKSPACE-ACCESS-NOT-FOUND"
    )

    listed = await client.get(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/drafts"
    )
    assert listed.status_code == 200, listed.text
    listed_page = await client.get(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}"
        "/drafts/page?limit=10&offset=0"
    )
    assert listed_page.status_code == 200, listed_page.text
    assert listed_page.json() == {
        "items": [],
        "total": 0,
        "limit": 10,
        "offset": 0,
        "hasMore": False,
    }

    denied_write = await client.post(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/assist",
        json={
            "kind": "agent",
            "title": "Agente sem escopo",
            "prompt": "Esta proposta não pode ser criada com acesso somente de leitura.",
        },
    )
    assert denied_write.status_code == 403
    assert denied_write.json()["detail"]["code"] == "SUPPORT-SCOPE-DENIED"

    grant = await db_session.get(SupportAccessGrant, grant_id)
    assert grant is not None
    grant.approved_scopes = [
        SupportAccessScope.configurations_read.value,
        SupportAccessScope.configurations_write.value,
    ]
    await db_session.commit()
    allowed_write = await client.post(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/assist",
        json={
            "kind": "agent",
            "title": "Agente autorizado",
            "prompt": "Crie um agente interno com revisão humana obrigatória.",
        },
    )
    assert allowed_write.status_code == 200, allowed_write.text


@pytest.mark.asyncio
async def test_non_root_can_consume_active_grant_outside_local_runtime(
    monkeypatch,
    db_session,
    auth_context,
    onboarding_blackbox_client,
) -> None:
    client, user = onboarding_blackbox_client
    now = datetime.now(UTC)
    db_session.add(
        SupportAccessGrant(
            workspace_id=auth_context.workspace_id,
            requester_user_id=user.id,
            reason="Configuração previamente aprovada para uso no runtime publicado.",
            ticket_reference="SAC-STUDIO-PRODUCTION-001",
            requested_scopes=[SupportAccessScope.configurations_read.value],
            requested_duration_minutes=30,
            state=SupportAccessState.active,
            requested_at=now,
            approved_scopes=[SupportAccessScope.configurations_read.value],
            approved_duration_minutes=30,
            approved_at=now,
            activated_by_user_id=user.id,
            activated_at=now,
            expires_at=now + timedelta(minutes=30),
        )
    )
    await db_session.flush()
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", True)

    response = await client.get(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/drafts"
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_workspace_options_keep_scope_expirations_separate(
    db_session,
    auth_context,
    onboarding_blackbox_client,
) -> None:
    client, user = onboarding_blackbox_client
    now = datetime.now(UTC)
    read_expiry = now + timedelta(minutes=25)
    write_expiry = now + timedelta(minutes=10)
    db_session.add_all(
        [
            SupportAccessGrant(
                workspace_id=auth_context.workspace_id,
                requester_user_id=user.id,
                reason="Leitura temporária de configurações aprovada pela conta.",
                ticket_reference="SAC-STUDIO-READ-EXPIRY",
                requested_scopes=[SupportAccessScope.configurations_read.value],
                requested_duration_minutes=30,
                state=SupportAccessState.active,
                requested_at=now,
                approved_scopes=[SupportAccessScope.configurations_read.value],
                approved_duration_minutes=30,
                approved_at=now,
                activated_by_user_id=user.id,
                activated_at=now,
                expires_at=read_expiry,
            ),
            SupportAccessGrant(
                workspace_id=auth_context.workspace_id,
                requester_user_id=user.id,
                reason="Escrita temporária de configurações aprovada pela conta.",
                ticket_reference="SAC-STUDIO-WRITE-EXPIRY",
                requested_scopes=[SupportAccessScope.configurations_write.value],
                requested_duration_minutes=15,
                state=SupportAccessState.active,
                requested_at=now,
                approved_scopes=[SupportAccessScope.configurations_write.value],
                approved_duration_minutes=15,
                approved_at=now,
                activated_by_user_id=user.id,
                activated_at=now,
                expires_at=write_expiry,
            ),
        ]
    )
    await db_session.flush()

    response = await client.get(
        "/api/v1/platform/configuration-studio/workspaces"
    )
    assert response.status_code == 200, response.text
    option = response.json()[0]
    assert option["canRead"] is True
    assert option["canWrite"] is True
    assert (
        datetime.fromisoformat(option["readExpiresAt"]).replace(tzinfo=UTC)
        == read_expiry
    )
    assert (
        datetime.fromisoformat(option["writeExpiresAt"]).replace(tzinfo=UTC)
        == write_expiry
    )
    assert datetime.fromisoformat(option["expiresAt"]).replace(tzinfo=UTC) == write_expiry


@pytest.mark.asyncio
async def test_root_studio_remains_available_outside_local_runtime(
    monkeypatch,
    auth_context,
    platform_client,
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", True)

    workspaces = await platform_client.get(
        "/api/v1/platform/configuration-studio/workspaces"
    )
    assert workspaces.status_code == 200, workspaces.text
    option = next(
        item
        for item in workspaces.json()
        if item["id"] == str(auth_context.workspace_id)
    )
    assert option["canRead"] is True
    assert option["canWrite"] is True
    assert option["readExpiresAt"] is None
    assert option["writeExpiresAt"] is None

    page = await platform_client.get(
        "/api/v1/platform/configuration-studio/workspaces/page",
        params={"search": "workspace teste", "limit": 20, "offset": 0},
    )
    assert page.status_code == 200, page.text
    assert any(
        item["id"] == str(auth_context.workspace_id)
        for item in page.json()["items"]
    )
    assert page.json()["total"] >= 1

    drafts = await platform_client.get(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/drafts"
    )
    assert drafts.status_code == 200, drafts.text


@pytest.mark.asyncio
async def test_studio_publishes_custom_agent_into_native_registry(
    db_session,
    auth_context,
    platform_client,
) -> None:
    assisted = await platform_client.post(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/assist",
        json={
            "kind": "agent",
            "title": "Especialista em renovação",
            "prompt": (
                "Crie um agente que leia a conversa e o CRM, confira documentos "
                "e prepare tarefas para renovação contratual."
            ),
        },
    )
    assert assisted.status_code == 200, assisted.text
    proposal = assisted.json()
    assert proposal["payload"]["parentAgentKey"] == "orquestrador"
    assert proposal["payload"]["canRequestHuman"] is False

    created = await platform_client.post(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/drafts",
        json={
            "kind": "agent",
            "title": proposal["title"],
            "key": proposal["key"],
            "payload": proposal["payload"],
            "reason": "Configuração assistida para a conta.",
        },
    )
    assert created.status_code == 201, created.text
    draft = created.json()

    published = await platform_client.post(
        (
            f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}"
            f"/drafts/{draft['id']}/publish"
        ),
        json={
            "expectedEtag": draft["etag"],
            "reason": "Publicação aprovada pelo titular.",
        },
    )
    assert published.status_code == 200, published.text
    result = published.json()
    assert result["targetType"] == "agent"
    assert result["alreadyPublished"] is False

    registry = await KlmAgentService(db_session).registry(auth_context.workspace_id)
    custom = {item["key"]: item for item in registry["customAgents"]}
    assert result["targetId"] in custom
    assert custom[result["targetId"]]["parentAgentKey"] == "orquestrador"
    assert custom[result["targetId"]]["custom"] is True

    repeated = await platform_client.post(
        (
            f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}"
            f"/drafts/{draft['id']}/publish"
        ),
        json={},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["alreadyPublished"] is True
    assert repeated.json()["targetId"] == result["targetId"]

    canonical_events = list(await db_session.scalars(
        select(PlatformAuditEvent)
        .where(PlatformAuditEvent.resource_id == draft["id"])
        .order_by(PlatformAuditEvent.occurred_at.asc())
    ))
    assert [event.action for event in canonical_events] == [
        "create_configuration_draft",
        "publish_configuration_draft",
        "publish_configuration_draft",
    ]
    publication_event = canonical_events[1]
    assert publication_event.before["status"] == "draft"
    assert publication_event.after["status"] == "published"
    assert {"status", "publication", "currentVersion", "etag"}.issubset(
        publication_event.changed_paths
    )
    assert canonical_events[2].changed_paths == []


@pytest.mark.asyncio
async def test_studio_lists_and_restores_versions_as_new_draft(
    auth_context,
    platform_client,
) -> None:
    created = await platform_client.post(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/drafts",
        json={
            "kind": "experience",
            "title": "Experiência inicial",
            "key": "experiencia-inicial",
            "payload": {"name": "Versão A", "welcomeMessage": "Olá"},
            "reason": "Primeira versão",
        },
    )
    assert created.status_code == 201, created.text
    first = created.json()
    updated = await platform_client.put(
        (
            f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}"
            f"/drafts/{first['id']}"
        ),
        json={
            "title": "Experiência revisada",
            "payload": {"name": "Versão B", "welcomeMessage": "Bem-vindo"},
            "expectedEtag": first["etag"],
            "reason": "Segunda versão",
        },
    )
    assert updated.status_code == 200, updated.text
    second = updated.json()

    versions = await platform_client.get(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}"
        f"/drafts/{first['id']}/versions"
    )
    assert versions.status_code == 200, versions.text
    assert [item["version"] for item in versions.json()] == [2, 1]

    draft_page = await platform_client.get(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}"
        "/drafts/page?limit=1&offset=0"
    )
    assert draft_page.status_code == 200, draft_page.text
    assert draft_page.json()["total"] >= 1
    assert draft_page.json()["limit"] == 1
    assert len(draft_page.json()["items"]) == 1

    version_page = await platform_client.get(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}"
        f"/drafts/{first['id']}/versions/page?limit=1&offset=1"
    )
    assert version_page.status_code == 200, version_page.text
    assert version_page.json()["total"] == 2
    assert version_page.json()["hasMore"] is False
    assert [item["version"] for item in version_page.json()["items"]] == [1]

    restored = await platform_client.post(
        (
            f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}"
            f"/drafts/{first['id']}/restore"
        ),
        json={
            "targetVersion": 1,
            "expectedEtag": second["etag"],
            "reason": "Restaurar após regressão visual",
        },
    )
    assert restored.status_code == 200, restored.text
    payload = restored.json()
    assert payload["status"] == "draft"
    assert payload["payload"]["name"] == "Versão A"
    assert payload["currentVersion"] > second["currentVersion"]


@pytest.mark.asyncio
async def test_studio_publishes_flow_once_and_keeps_it_inactive(
    db_session,
    auth_context,
    platform_client,
) -> None:
    assisted = await platform_client.post(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/assist",
        json={
            "kind": "flow",
            "title": "Retomar documentos pendentes",
            "prompt": (
                "Crie um fluxo manual para conferir documentos pendentes e pedir "
                "aprovação humana antes de qualquer contato."
            ),
        },
    )
    assert assisted.status_code == 200, assisted.text
    proposal = assisted.json()
    assert proposal["payload"]["is_active"] is False

    created = await platform_client.post(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/drafts",
        json={
            "kind": "flow",
            "title": proposal["title"],
            "key": proposal["key"],
            "payload": proposal["payload"],
        },
    )
    assert created.status_code == 201, created.text
    draft = created.json()

    endpoint = (
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}"
        f"/drafts/{draft['id']}/publish"
    )
    first = await platform_client.post(
        endpoint,
        json={"expectedEtag": draft["etag"]},
    )
    assert first.status_code == 200, first.text
    second = await platform_client.post(endpoint, json={})
    assert second.status_code == 200, second.text
    assert second.json()["alreadyPublished"] is True
    assert second.json()["targetId"] == first.json()["targetId"]

    from app.models.flow import Flow

    flow = await db_session.get(Flow, first.json()["targetId"])
    assert flow is not None
    assert flow.workspace_id == auth_context.workspace_id
    assert flow.is_active is False
    assert flow.trigger_config["studioDraftId"] == draft["id"]


@pytest.mark.asyncio
async def test_studio_draft_is_scoped_to_the_selected_workspace(
    auth_context,
    platform_client,
) -> None:
    missing_workspace = uuid4()
    response = await platform_client.get(
        f"/api/v1/platform/configuration-studio/workspaces/{missing_workspace}/drafts"
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "STUDIO-NOT-FOUND"

    response = await platform_client.get(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/drafts"
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


@pytest.mark.asyncio
async def test_studio_publishes_experience_consumed_by_workspace_runtime(
    db_session,
    auth_context,
    platform_client,
) -> None:
    assisted = await platform_client.post(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/assist",
        json={
            "kind": "experience",
            "title": "Atendimento premium",
            "prompt": "Personalize a entrada de suporte sem ocultar recursos da conta.",
            "currentPayload": {
                "supportLabel": "Falar com o suporte",
                "welcomeMessage": "Conte o que aconteceu. A equipe acompanha por aqui.",
            },
        },
    )
    assert assisted.status_code == 200, assisted.text
    proposal = assisted.json()

    created = await platform_client.post(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/drafts",
        json={
            "kind": "experience",
            "title": proposal["title"],
            "key": proposal["key"],
            "payload": proposal["payload"],
        },
    )
    assert created.status_code == 201, created.text
    draft = created.json()

    published = await platform_client.post(
        (
            f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}"
            f"/drafts/{draft['id']}/publish"
        ),
        json={"expectedEtag": draft["etag"]},
    )
    assert published.status_code == 200, published.text

    resolved = await ConfigurationService(db_session).resolve(
        namespace="workspace.experience",
        workspace_id=auth_context.workspace_id,
        role_key="workspace_owner",
        user_id=auth_context.user_id,
        contract_defaults=DEFAULT_WORKSPACE_EXPERIENCE,
    )
    assert resolved.effective["supportLabel"] == "Falar com o suporte"
    assert resolved.effective["welcomeMessage"].startswith("Conte o que aconteceu")
    assert resolved.effective["featureVisibility"] == {}


@pytest.mark.asyncio
async def test_studio_publishes_agent_package_atomically_and_paused(
    db_session,
    auth_context,
    platform_client,
) -> None:
    assisted = await platform_client.post(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/assist",
        json={
            "kind": "agent_package",
            "title": "Empresarial consultivo",
            "prompt": (
                "Crie um pacote para qualificar empresas, ler CRM e documentos, "
                "preparar nota interna e pedir aprovação ao Orquestrador."
            ),
        },
    )
    assert assisted.status_code == 200, assisted.text
    proposal = assisted.json()
    assert len(proposal["payload"]["agents"]) == 2
    assert proposal["payload"]["governance"]["orchestratorOnly"] is True

    created = await platform_client.post(
        f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}/drafts",
        json={
            "kind": "agent_package",
            "title": proposal["title"],
            "key": proposal["key"],
            "payload": proposal["payload"],
        },
    )
    assert created.status_code == 201, created.text
    draft = created.json()

    published = await platform_client.post(
        (
            f"/api/v1/platform/configuration-studio/workspaces/{auth_context.workspace_id}"
            f"/drafts/{draft['id']}/publish"
        ),
        json={"expectedEtag": draft["etag"]},
    )
    assert published.status_code == 200, published.text
    assert published.json()["targetType"] == "agent_package"
    assert published.json()["targetId"] == proposal["key"]

    registry = await KlmAgentService(db_session).registry(auth_context.workspace_id)
    custom = {item["key"]: item for item in registry["customAgents"]}
    package_keys = {agent["key"] for agent in proposal["payload"]["agents"]}
    assert package_keys <= custom.keys()
    assert all(custom[key]["state"] == "paused" for key in package_keys)
    assert all(custom[key]["parentAgentKey"] == "orquestrador" for key in package_keys)


@pytest.mark.asyncio
async def test_studio_draft_rolls_back_when_canonical_audit_fails(
    db_session,
    auth_context,
    platform_client,
    monkeypatch,
) -> None:
    namespace = "studio.experience.rollback_audit"

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.post(
            (
                "/api/v1/platform/configuration-studio/workspaces/"
                f"{auth_context.workspace_id}/drafts"
            ),
            json={
                "kind": "experience",
                "title": "Rollback audit",
                "key": "rollback-audit",
                "payload": {"name": "Não deve persistir"},
                "reason": "Teste de atomicidade entre configuração e auditoria.",
            },
        )

    db_session.expire_all()
    persisted = await db_session.scalar(
        select(ConfigDocument).where(
            ConfigDocument.workspace_id == auth_context.workspace_id,
            ConfigDocument.namespace == namespace,
        )
    )
    assert persisted is None


@pytest.mark.asyncio
async def test_studio_publish_rolls_back_native_target_when_canonical_audit_fails(
    db_session,
    auth_context,
    platform_client,
    monkeypatch,
) -> None:
    created = await platform_client.post(
        (
            "/api/v1/platform/configuration-studio/workspaces/"
            f"{auth_context.workspace_id}/drafts"
        ),
        json={
            "kind": "flow",
            "title": "Fluxo com rollback de auditoria",
            "key": "flow-rollback-audit",
            "payload": {
                "name": "Fluxo com rollback de auditoria",
                "description": "Não deve persistir sem auditoria.",
                "practice_area": "universal",
                "trigger": "manual",
                "trigger_config": {"source": "blackbox_studio"},
                "nodes": [],
                "edges": [],
                "is_active": False,
            },
            "reason": "Preparação do teste de rollback da publicação.",
        },
    )
    assert created.status_code == 201, created.text
    draft = created.json()

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.post(
            (
                "/api/v1/platform/configuration-studio/workspaces/"
                f"{auth_context.workspace_id}/drafts/{draft['id']}/publish"
            ),
            json={
                "expectedEtag": draft["etag"],
                "reason": "Publicação usada para provar rollback atômico.",
            },
        )

    db_session.expire_all()
    persisted_draft = await db_session.get(ConfigDocument, draft["id"])
    assert persisted_draft is not None
    assert persisted_draft.payload["status"] == "draft"
    flows = list(await db_session.scalars(
        select(Flow).where(Flow.workspace_id == auth_context.workspace_id)
    ))
    assert all(
        str((flow.trigger_config or {}).get("studioDraftId")) != draft["id"]
        for flow in flows
    )


def test_custom_agent_obeys_native_delegation_and_human_queue() -> None:
    scope = {"renovacao_assistida": "orquestrador"}
    decision = AgentGovernanceService.authorize(
        target_agent_key="renovacao_assistida",
        initiator_type="agent",
        requested_by_agent_key="orquestrador",
        delegation_depth=1,
        matter_area_key=None,
        matter_product_key=None,
        run_workspace_id="workspace-1",
        matter_workspace_id="workspace-1",
        context_client_id="client-1",
        matter_client_id="client-1",
        manual_context_revision=4,
        area_enabled=True,
        request_human=False,
        available_tool_keys=("conversation_rag",),
        requested_tool_keys=("conversation_rag",),
        extra_parent_by_key=scope,
    )
    assert decision.human_request_agent_key == "orquestrador"
    assert decision.hierarchy_path == ("orquestrador", "renovacao_assistida")

    with pytest.raises(AgentGovernanceViolation) as invalid_path:
        AgentGovernanceService.authorize(
            target_agent_key="renovacao_assistida",
            initiator_type="agent",
            requested_by_agent_key="hermes",
            delegation_depth=1,
            matter_area_key=None,
            matter_product_key=None,
            run_workspace_id="workspace-1",
            matter_workspace_id="workspace-1",
            context_client_id="client-1",
            matter_client_id="client-1",
            manual_context_revision=4,
            area_enabled=True,
            request_human=True,
            extra_parent_by_key=scope,
        )
    assert invalid_path.value.code == "OWNER_BRIDGE_CANNOT_ORCHESTRATE"
