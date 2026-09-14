from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.configuration import ConfigScope
from app.models.user import AgentType, User, UserRole
from app.services.configuration_service import (
    ConfigConflictError,
    ConfigurationService,
    ConfigValidationError,
)
pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
async def test_configuration_resolves_platform_workspace_role_and_user_layers(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    workspace_id = UUID(str(auth_context.workspace_id))
    service = ConfigurationService(db_session)
    await service.write_platform(
        namespace="user.preferences",
        payload={"theme": "light", "nested": {"platform": True, "choice": "platform"}},
        actor_user_id=auth_context.user_id,
    )
    await service.write_tenant(
        workspace_id=workspace_id,
        scope=ConfigScope.workspace,
        namespace="user.preferences",
        payload={"density": "compact", "nested": {"choice": "workspace"}},
        actor_user_id=auth_context.user_id,
    )
    await service.write_tenant(
        workspace_id=workspace_id,
        scope=ConfigScope.role,
        namespace="user.preferences",
        role_key="workspace_owner",
        payload={"high_contrast": True},
        actor_user_id=auth_context.user_id,
    )
    await service.write_tenant(
        workspace_id=workspace_id,
        scope=ConfigScope.user,
        namespace="user.preferences",
        user_id=auth_context.user_id,
        payload={"theme": "dark", "nested": {"user": True}},
        actor_user_id=auth_context.user_id,
    )

    resolved = await service.resolve(
        namespace="user.preferences",
        workspace_id=workspace_id,
        role_key="workspace_owner",
        user_id=auth_context.user_id,
        contract_defaults={"theme": "system", "reduced_motion": False},
    )

    assert [layer.scope for layer in resolved.layers] == [
        "contract",
        "platform",
        "workspace",
        "role",
        "user",
    ]
    assert resolved.effective == {
        "theme": "dark",
        "reduced_motion": False,
        "density": "compact",
        "high_contrast": True,
        "nested": {"platform": True, "choice": "workspace", "user": True},
    }


@pytest.mark.asyncio
async def test_configuration_is_idempotent_and_uses_etag_for_concurrency(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    service = ConfigurationService(db_session)
    arguments = {
        "workspace_id": auth_context.workspace_id,
        "scope": ConfigScope.user,
        "namespace": "user.preferences",
        "payload": {"theme": "dark"},
        "actor_user_id": auth_context.user_id,
        "user_id": auth_context.user_id,
    }
    first = await service.write_tenant(**arguments)
    same = await service.write_tenant(**arguments, expected_etag=first.etag)

    assert same.current_version == 1
    assert len(await service.history(first.id)) == 1

    stale_etag = first.etag
    changed = await service.write_tenant(
        **{**arguments, "payload": {"theme": "light"}},
        expected_etag=stale_etag,
    )
    assert changed.current_version == 2
    with pytest.raises(ConfigConflictError):
        await service.write_tenant(
            **{**arguments, "payload": {"theme": "system"}},
            expected_etag=stale_etag,
        )


@pytest.mark.asyncio
async def test_rollback_creates_a_new_auditable_version(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    service = ConfigurationService(db_session)
    first = await service.write_tenant(
        workspace_id=auth_context.workspace_id,
        scope=ConfigScope.user,
        namespace="user.preferences",
        payload={"theme": "dark"},
        actor_user_id=auth_context.user_id,
        user_id=auth_context.user_id,
    )
    first_etag = first.etag
    second = await service.write_tenant(
        workspace_id=auth_context.workspace_id,
        scope=ConfigScope.user,
        namespace="user.preferences",
        payload={"theme": "light"},
        actor_user_id=auth_context.user_id,
        user_id=auth_context.user_id,
        expected_etag=first_etag,
    )
    rolled_back = await service.rollback_tenant(
        document=second,
        target_version=1,
        actor_user_id=auth_context.user_id,
        expected_etag=second.etag,
    )

    history = await service.history(rolled_back.id)
    assert rolled_back.current_version == 3
    assert rolled_back.payload == {"theme": "dark"}
    assert [version.version for version in history] == [3, 2, 1]
    assert history[0].reason == "Rollback to version 1"


@pytest.mark.asyncio
async def test_user_layer_rejects_a_user_without_workspace_membership(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    outsider = User(
        name="Outsider",
        email="outsider@example.test",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="OU",
        color="#000000",
        is_active=True,
    )
    db_session.add(outsider)
    await db_session.flush()

    with pytest.raises(ConfigValidationError):
        await ConfigurationService(db_session).write_tenant(
            workspace_id=auth_context.workspace_id,
            scope=ConfigScope.user,
            namespace="user.preferences",
            payload={"theme": "dark"},
            actor_user_id=auth_context.user_id,
            user_id=outsider.id,
        )


@pytest.mark.asyncio
async def test_user_preferences_api_is_scoped_versioned_and_rollbackable(client) -> None:
    initial = await client.get("/api/v1/users/me/preferences")
    assert initial.status_code == 200
    assert initial.json()["preferences"]["theme"] == "system"
    assert initial.json()["preferences"]["currency"] == "BRL"
    assert initial.json()["preferences"]["accessibility"]["fontScale"] == 1.0
    assert initial.json()["userVersion"] is None

    created = await client.put(
        "/api/v1/users/me/preferences",
        json={
            "theme": "dark",
            "sidebarCollapsed": True,
            "density": "spacious",
            "dailyDigest": {"enabled": True, "hour": 9},
            "accessibility": {
                "reducedMotion": True,
                "highContrast": False,
                "fontScale": 1.1,
            },
        },
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["preferences"]["theme"] == "dark"
    assert payload["preferences"]["sidebarCollapsed"] is True
    assert payload["preferences"]["density"] == "spacious"
    assert payload["preferences"]["dailyDigest"]["hour"] == 9
    assert payload["preferences"]["accessibility"]["reducedMotion"] is True
    assert payload["userVersion"] == 1

    stale = await client.put(
        "/api/v1/users/me/preferences",
        headers={"If-Match": '"stale"'},
        json={"theme": "light"},
    )
    assert stale.status_code == 412

    updated = await client.put(
        "/api/v1/users/me/preferences",
        headers={"If-Match": f'"{payload["userEtag"]}"'},
        json={"theme": "light"},
    )
    assert updated.status_code == 200
    assert updated.json()["userVersion"] == 2

    versions = await client.get("/api/v1/users/me/preferences/versions")
    assert versions.status_code == 200
    assert [row["version"] for row in versions.json()] == [2, 1]

    rolled_back = await client.post(
        "/api/v1/users/me/preferences/rollback/1",
        headers={"If-Match": f'"{updated.json()["userEtag"]}"'},
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["preferences"]["theme"] == "dark"
    assert rolled_back.json()["userVersion"] == 3


@pytest.mark.asyncio
async def test_administrative_configuration_api_exposes_effective_history_and_guards(
    client,
) -> None:
    created = await client.put(
        "/api/v1/configurations/brand.identity/documents/workspace",
        json={
            "payload": {"name": "KLM Hub", "palette": {"primary": "#a6843f"}},
            "reason": "Initial workspace brand",
        },
    )
    assert created.status_code == 200
    first = created.json()
    assert first["currentVersion"] == 1

    effective = await client.get("/api/v1/configurations/brand.identity/effective")
    assert effective.status_code == 200
    assert effective.json()["effective"]["name"] == "KLM Hub"
    assert effective.json()["effective"]["colors"]["primary"] == "#DCA74C"
    assert effective.json()["effective"]["assets"]["faviconUrl"] == "/favicon.svg"
    assert [layer["scope"] for layer in effective.json()["layers"]] == [
        "contract",
        "workspace",
    ]

    changed = await client.put(
        "/api/v1/configurations/brand.identity/documents/workspace",
        headers={"If-Match": f'"{first["etag"]}"'},
        json={
            "payload": {"name": "Marca atualizada"},
            "reason": "Brand revision",
        },
    )
    assert changed.status_code == 200
    assert changed.json()["currentVersion"] == 2

    versions = await client.get(
        "/api/v1/configurations/brand.identity/documents/workspace/versions"
    )
    assert versions.status_code == 200
    assert [row["version"] for row in versions.json()] == [2, 1]

    rolled_back = await client.post(
        "/api/v1/configurations/brand.identity/documents/workspace/rollback/1",
        headers={"If-Match": f'"{changed.json()["etag"]}"'},
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["payload"]["name"] == "KLM Hub"
    assert rolled_back.json()["currentVersion"] == 3

    platform_scope = await client.put(
        "/api/v1/configurations/brand.identity/documents/platform",
        json={"payload": {"name": "forbidden"}},
    )
    assert platform_scope.status_code == 422

    secret = await client.put(
        "/api/v1/configurations/brand.identity/documents/workspace",
        json={"payload": {"apiKey": "must-not-be-stored"}},
    )
    assert secret.status_code == 422


@pytest.mark.asyncio
async def test_current_user_can_version_personal_filter_preferences(client) -> None:
    missing = await client.get("/api/v1/configurations/ui.filters/me")
    assert missing.status_code == 200
    assert missing.json() is None

    created = await client.put(
        "/api/v1/configurations/ui.filters/me",
        json={
            "payload": {
                "views": {
                    "conversations": [
                        {
                            "id": "urgent-unanswered",
                            "name": "Urgentes sem resposta",
                            "match": "all",
                            "conditions": [
                                {"field": "priority", "operator": "eq", "value": "urgent"},
                                {"field": "unread", "operator": "eq", "value": True},
                            ],
                        }
                    ]
                }
            },
            "reason": "Criou uma visualização personalizada",
        },
    )
    assert created.status_code == 200
    first = created.json()
    assert first["scope"] == "user"
    assert first["namespace"] == "ui.filters"
    assert first["currentVersion"] == 1

    loaded = await client.get("/api/v1/configurations/ui.filters/me")
    assert loaded.status_code == 200
    assert loaded.headers["etag"] == f'"{first["etag"]}"'
    assert loaded.json()["payload"] == first["payload"]

    stale = await client.put(
        "/api/v1/configurations/ui.filters/me",
        headers={"If-Match": '"stale-etag"'},
        json={"payload": {"views": {}}},
    )
    assert stale.status_code == 412

    changed = await client.put(
        "/api/v1/configurations/ui.filters/me",
        headers={"If-Match": f'"{first["etag"]}"'},
        json={"payload": {"views": {"conversations": []}}},
    )
    assert changed.status_code == 200
    assert changed.json()["currentVersion"] == 2


@pytest.mark.asyncio
async def test_platform_configuration_api_versions_and_rolls_back(platform_client) -> None:
    created = await platform_client.put(
        "/api/v1/platform/configurations/brand.identity",
        json={"payload": {"name": "KLM Default"}},
    )
    assert created.status_code == 200
    first = created.json()

    changed = await platform_client.put(
        "/api/v1/platform/configurations/brand.identity",
        headers={"If-Match": f'"{first["etag"]}"'},
        json={"payload": {"name": "KLM Platform"}},
    )
    assert changed.status_code == 200
    assert changed.json()["currentVersion"] == 2

    history = await platform_client.get("/api/v1/platform/configurations/brand.identity/versions")
    assert history.status_code == 200
    assert [row["version"] for row in history.json()] == [2, 1]

    page = await platform_client.get(
        "/api/v1/platform/configurations/brand.identity/versions/page",
        params={"limit": 1, "offset": 1},
    )
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert page.json()["limit"] == 1
    assert page.json()["offset"] == 1
    assert page.json()["hasMore"] is False
    assert [row["version"] for row in page.json()["items"]] == [1]

    rolled_back = await platform_client.post(
        "/api/v1/platform/configurations/brand.identity/rollback/1",
        headers={"If-Match": f'"{changed.json()["etag"]}"'},
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["payload"]["name"] == "KLM Default"
    assert rolled_back.json()["currentVersion"] == 3
