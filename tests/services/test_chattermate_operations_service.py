"""Contracts for the KLM-canonical ChatterMate facade."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    OSSBridgeProvider,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.chattermate_client import ChatterMateClient
from app.services.chattermate_operations_service import (
    ChatterMateOperationsService,
    invalidate_chattermate_context_cache,
)
from app.services.klm_agent_service import AGENT_DEFINITIONS


def _reset_chattermate_session_cache() -> None:
    ChatterMateClient._shared_access_token = None
    ChatterMateClient._shared_identity = None
    ChatterMateClient._shared_cache_key = None
    ChatterMateClient._shared_expires_at = 0.0
    ChatterMateClient._shared_login_tasks.clear()


@pytest.mark.asyncio
async def test_chattermate_launch_prefers_synced_sdr_agent() -> None:
    service = object.__new__(ChatterMateOperationsService)
    service.db = SimpleNamespace(
        scalars=AsyncMock(
            return_value=[
                SimpleNamespace(
                    external_entity_id="agent-hermes",
                    link_metadata={"agentKey": "hermes"},
                ),
                SimpleNamespace(
                    external_entity_id="agent-sdr",
                    link_metadata={"agentKey": "sdr"},
                ),
            ]
        )
    )

    assert (
        await service.launch_agent_id(UUID("645e1341-a1ae-452c-8c99-884104dd06eb"))
        == "agent-sdr"
    )


@pytest.mark.asyncio
async def test_chattermate_binding_rejects_organization_owned_by_another_workspace() -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    foreign = SimpleNamespace(external_entity_id="org-local")
    service = object.__new__(ChatterMateOperationsService)
    service.db = SimpleNamespace(
        scalars=AsyncMock(return_value=[]),
        scalar=AsyncMock(return_value=foreign),
    )
    service.links = SimpleNamespace(upsert_link=AsyncMock())

    with pytest.raises(PermissionError, match="outro workspace"):
        await service.ensure_workspace_binding(workspace_id, "org-local")

    service.links.upsert_link.assert_not_awaited()


@pytest.mark.asyncio
async def test_chattermate_binding_treats_pending_foreign_organization_as_owned(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    target_workspace = Workspace(
        name="Workspace ChatterMate isolado",
        slug="workspace-chattermate-isolado",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(target_workspace)
    await db_session.flush()
    db_session.add(
        ExternalEntityLink(
            workspace_id=auth_context.workspace_id,
            provider=OSSBridgeProvider.chattermate,
            klm_entity_type="workspace",
            klm_entity_id=auth_context.workspace_id,
            external_account_id="org-pending",
            external_entity_type="organization",
            external_entity_id="org-pending",
            link_metadata={"managedBy": "test"},
            sync_state=ExternalEntitySyncState.pending,
        )
    )
    await db_session.flush()
    service = ChatterMateOperationsService(db_session, client=SimpleNamespace())

    with pytest.raises(PermissionError, match="outro workspace"):
        await service.ensure_workspace_binding(target_workspace.id, "org-pending")


@pytest.mark.asyncio
async def test_chattermate_binding_promotes_matching_pending_link_after_exclusivity_check(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    pending = ExternalEntityLink(
        workspace_id=auth_context.workspace_id,
        provider=OSSBridgeProvider.chattermate,
        klm_entity_type="workspace",
        klm_entity_id=auth_context.workspace_id,
        external_account_id="org-local-pending",
        external_entity_type="organization",
        external_entity_id="org-local-pending",
        link_metadata={"managedBy": "test"},
        sync_state=ExternalEntitySyncState.pending,
    )
    db_session.add(pending)
    await db_session.flush()
    service = ChatterMateOperationsService(db_session, client=SimpleNamespace())

    bound = await service.ensure_workspace_binding(
        auth_context.workspace_id,
        "org-local-pending",
    )

    assert bound.id == pending.id
    assert bound.sync_state == ExternalEntitySyncState.synced


@pytest.mark.asyncio
async def test_chattermate_client_login_and_collections_use_bearer_contract() -> None:
    _reset_chattermate_session_cache()
    client = object.__new__(ChatterMateClient)
    client.base_url = "http://127.0.0.1:3300"
    client.username = "admin@local.test"
    client.password = "local-password"
    client._access_token = None
    client._identity = None
    client._raw_request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {
                "access_token": "token",
                "refresh_token": "refresh",
                "user": {"id": "u1", "organization_id": "org1"},
            },
            [{"id": "a1", "name": "KLM · Hermes"}],
            [],
        ]
    )

    assert (await client.identity())["organization_id"] == "org1"
    assert (await client.list_agents())[0]["id"] == "a1"
    assert await client.mcp_tools() == []
    assert client._raw_request.await_args_list[1].kwargs["token"] == "token"
    _reset_chattermate_session_cache()


@pytest.mark.asyncio
async def test_chattermate_clients_coalesce_and_reuse_authenticated_session() -> None:
    _reset_chattermate_session_cache()
    clients = []
    for _ in range(2):
        client = object.__new__(ChatterMateClient)
        client.base_url = "http://127.0.0.1:3300"
        client.username = "admin@local.test"
        client.password = "local-password"
        client._access_token = None
        client._identity = None
        client._raw_request = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "access_token": "shared-token",
                "user": {"id": "u1", "organization_id": "org1"},
            }
        )
        clients.append(client)

    identities = await asyncio.gather(*(client.identity() for client in clients))

    assert [identity["organization_id"] for identity in identities] == ["org1", "org1"]
    assert sum(client._raw_request.await_count for client in clients) == 1
    _reset_chattermate_session_cache()


@pytest.mark.asyncio
async def test_chattermate_login_survives_cancelled_waiter() -> None:
    _reset_chattermate_session_cache()
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_login(*args, **kwargs):
        started.set()
        await release.wait()
        return {
            "access_token": "shared-token",
            "user": {"id": "u1", "organization_id": "org1"},
        }

    clients = []
    for _ in range(2):
        client = object.__new__(ChatterMateClient)
        client.base_url = "http://127.0.0.1:3300"
        client.username = "admin@local.test"
        client.password = "local-password"
        client._access_token = None
        client._identity = None
        client._raw_request = AsyncMock(side_effect=delayed_login)  # type: ignore[method-assign]
        clients.append(client)

    cancelled_waiter = asyncio.create_task(clients[0].identity())
    await started.wait()
    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter

    surviving_waiter = asyncio.create_task(clients[1].identity())
    release.set()

    assert (await surviving_waiter)["organization_id"] == "org1"
    assert sum(client._raw_request.await_count for client in clients) == 1
    _reset_chattermate_session_cache()


@pytest.mark.asyncio
async def test_chattermate_login_tasks_are_isolated_by_credentials() -> None:
    _reset_chattermate_session_cache()
    clients = []
    for suffix in ("one", "two"):
        client = object.__new__(ChatterMateClient)
        client.base_url = "http://127.0.0.1:3300"
        client.username = f"admin-{suffix}@local.test"
        client.password = f"local-password-{suffix}"
        client._access_token = None
        client._identity = None
        client._raw_request = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "access_token": f"token-{suffix}",
                "user": {"id": f"u-{suffix}", "organization_id": f"org-{suffix}"},
            }
        )
        clients.append(client)

    identities = await asyncio.gather(*(client.identity() for client in clients))

    assert [identity["organization_id"] for identity in identities] == ["org-one", "org-two"]
    assert [client._raw_request.await_count for client in clients] == [1, 1]
    _reset_chattermate_session_cache()


@pytest.mark.asyncio
async def test_chattermate_client_workflow_write_contracts() -> None:
    client = object.__new__(ChatterMateClient)
    client._request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"id": "workflow-1"},
            {"id": "workflow-1"},
            {"nodes": [], "connections": []},
            {"nodes": [{"id": "node-1"}], "connections": []},
        ]
    )

    await client.create_workflow({"name": "KLM", "agent_id": "agent-1"})
    await client.update_workflow("workflow-1", {"status": "draft"})
    await client.workflow_nodes("workflow-1")
    await client.replace_workflow_nodes(
        "workflow-1",
        nodes=[{"id": "node-1"}],
        connections=[],
    )

    assert client._request.await_args_list[0].args[:2] == ("POST", "/api/v1/workflow")
    assert client._request.await_args_list[0].kwargs["expected"] == (201,)
    assert client._request.await_args_list[1].args[:2] == (
        "PUT",
        "/api/v1/workflow/workflow-1",
    )
    assert client._request.await_args_list[2].args[:2] == (
        "GET",
        "/api/v1/workflow/workflow-1/nodes",
    )
    assert client._request.await_args_list[3].kwargs["json"] == {
        "nodes": [{"id": "node-1"}],
        "connections": [],
    }


@pytest.mark.asyncio
async def test_chattermate_sync_materializes_all_canonical_agents_without_model_execution(
    monkeypatch,
) -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    actor_id = UUID("fbdcc4ce-63d3-450d-b85d-13ffb08ea82d")
    db = SimpleNamespace(
        add=MagicMock(),
        commit=AsyncMock(),
        scalar=AsyncMock(return_value="Usuário Teste"),
    )
    created_rows: list[dict] = []

    async def create_agent(payload):
        row = {**payload, "id": f"agent-{len(created_rows) + 1}"}
        created_rows.append(row)
        return row

    client = SimpleNamespace(
        identity=AsyncMock(return_value={"organization_id": "org1"}),
        list_agents=AsyncMock(return_value=[]),
        create_agent=AsyncMock(side_effect=create_agent),
        update_agent=AsyncMock(),
    )
    service = object.__new__(ChatterMateOperationsService)
    service.db = db
    service.client = client
    service.links = SimpleNamespace(
        find_link_by_klm_entity=AsyncMock(return_value=None),
        upsert_link=AsyncMock(return_value=SimpleNamespace()),
    )
    service._bind = AsyncMock(return_value=SimpleNamespace())  # type: ignore[method-assign]
    monkeypatch.setattr(
        "app.services.chattermate_operations_service.KlmAgentService.registry",
        AsyncMock(
            return_value={
                "agents": [
                    {"key": definition.key, "state": "active"}
                    for definition in AGENT_DEFINITIONS
                ]
            }
        ),
    )

    result = await service.sync_agents(workspace_id, actor_id)

    agent_count = len(AGENT_DEFINITIONS)
    assert result == {
        "created": agent_count,
        "updated": 0,
        "adopted": 0,
        "conflicts": 0,
        "seen": agent_count,
    }
    assert len(created_rows) == agent_count
    assert all(row["name"].startswith("KLM · ") for row in created_rows)
    assert all(row["require_token_auth"] is True for row in created_rows)
    assert all(row["tools"] == [] for row in created_rows)
    assert all(
        any(str(instruction).startswith("KLM-AGENT-ID:") for instruction in row["instructions"])
        for row in created_rows
    )
    assert (
        next(row for row in created_rows if row["display_name"] == "Hermes")["transfer_to_human"]
        is True
    )
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_chattermate_sync_adopts_renamed_agent_by_canonical_marker(
    monkeypatch,
) -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    actor_id = UUID("fbdcc4ce-63d3-450d-b85d-13ffb08ea82d")
    definition = SimpleNamespace(
        key="sdr",
        name="SDR Saúde",
        description="Triagem inicial",
        gate="revisão jurídica",
        tools=("crm",),
        can_request_human=True,
    )
    marker = f"KLM-AGENT-ID:{workspace_id}:sdr"
    upstream = {
        "id": "agent-existing",
        "name": "Nome alterado no ChatterMate",
        "display_name": definition.name,
        "instructions": [
            definition.description,
            f"Gate obrigatório: {definition.gate}",
            "Ferramentas KLM permitidas: crm",
            marker,
        ],
        "is_active": True,
        "transfer_to_human": True,
        "ask_for_rating": False,
        "handoff_collect_email": True,
        "handoff_collect_name": True,
        "enable_rate_limiting": True,
        "overall_limit_per_ip": 100,
        "requests_per_sec": 1.0,
        "use_workflow": False,
        "allow_attachments": False,
        "require_token_auth": True,
    }
    client = SimpleNamespace(
        identity=AsyncMock(return_value={"organization_id": "org1"}),
        list_agents=AsyncMock(return_value=[upstream]),
        create_agent=AsyncMock(),
        update_agent=AsyncMock(),
    )
    service = object.__new__(ChatterMateOperationsService)
    service.db = SimpleNamespace(
        add=MagicMock(),
        commit=AsyncMock(),
        scalar=AsyncMock(return_value="Usuário Teste"),
    )
    service.client = client
    service.links = SimpleNamespace(
        find_link_by_klm_entity=AsyncMock(return_value=None),
        upsert_link=AsyncMock(return_value=SimpleNamespace()),
    )
    service._bind = AsyncMock(return_value=SimpleNamespace())  # type: ignore[method-assign]
    monkeypatch.setattr(
        "app.services.chattermate_operations_service.AGENT_DEFINITIONS",
        (definition,),
    )
    monkeypatch.setattr(
        "app.services.chattermate_operations_service.KlmAgentService.registry",
        AsyncMock(return_value={"agents": [{"key": "sdr", "state": "active"}]}),
    )

    result = await service.sync_agents(workspace_id, actor_id)

    assert result == {
        "created": 0,
        "updated": 0,
        "adopted": 1,
        "conflicts": 0,
        "seen": 1,
    }
    client.create_agent.assert_not_awaited()
    linked = service.links.upsert_link.await_args.args[1]
    assert linked.external_entity_id == "agent-existing"
    assert linked.metadata["canonicalMarker"] == marker


@pytest.mark.asyncio
async def test_chattermate_context_returns_reviewed_operational_fields_only() -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    invalidate_chattermate_context_cache()
    service = object.__new__(ChatterMateOperationsService)
    service.db = SimpleNamespace()
    service._binding = AsyncMock(return_value=SimpleNamespace())  # type: ignore[method-assign]
    service.client = SimpleNamespace(
        identity=AsyncMock(return_value={"organization_id": "org1"}),
        list_agents=AsyncMock(
            return_value=[
                {
                    "id": "a1",
                    "name": "KLM · Hermes",
                    "is_active": True,
                    "instructions": ["must-not-leak"],
                    "api_key": "must-not-leak",
                }
            ]
        ),
        workflow_for_agent=AsyncMock(return_value=None),
        analytics=AsyncMock(return_value={"conversations": {"total": 0}}),
        agent_performance=AsyncMock(return_value={"bot_agents": []}),
        people=AsyncMock(return_value={"items": [], "total": 0}),
        people_stats=AsyncMock(return_value={"total_people": 0}),
        mcp_tools=AsyncMock(
            return_value=[{"id": 1, "name": "Docs", "headers": {"Authorization": "secret"}}]
        ),
        groups=AsyncMock(return_value=[]),
        notifications=AsyncMock(return_value=[]),
        knowledge=AsyncMock(return_value={"knowledge": [], "pagination": {}}),
        knowledge_queue=AsyncMock(return_value={"queue_items": []}),
    )

    context = await service.context(workspace_id, "7d")

    assert context["linked"] is True
    assert context["agents"] == [{"id": "a1", "name": "KLM · Hermes", "is_active": True}]
    assert context["mcpTools"] == [{"id": 1, "name": "Docs"}]
    assert context["externalExecutionEnabled"] is False
    invalidate_chattermate_context_cache()


@pytest.mark.asyncio
async def test_chattermate_context_coalesces_concurrent_spa_reads() -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    invalidate_chattermate_context_cache()
    client = SimpleNamespace(
        identity=AsyncMock(return_value={"organization_id": "org1"}),
        list_agents=AsyncMock(return_value=[]),
        analytics=AsyncMock(return_value={}),
        agent_performance=AsyncMock(return_value={}),
        people=AsyncMock(return_value={"items": [], "total": 0}),
        people_stats=AsyncMock(return_value={}),
        mcp_tools=AsyncMock(return_value=[]),
        groups=AsyncMock(return_value=[]),
        notifications=AsyncMock(return_value=[]),
        knowledge=AsyncMock(return_value={"knowledge": [], "pagination": {}}),
        knowledge_queue=AsyncMock(return_value={"queue_items": []}),
    )
    service = object.__new__(ChatterMateOperationsService)
    service.db = SimpleNamespace()
    service.client = client
    service._binding = AsyncMock(return_value=SimpleNamespace())  # type: ignore[method-assign]

    first, second = await asyncio.gather(
        service.context(workspace_id, "7d"),
        service.context(workspace_id, "7d"),
    )

    assert first == second
    assert first is not second
    client.identity.assert_awaited_once()
    client.analytics.assert_awaited_once()
    invalidate_chattermate_context_cache()


@pytest.mark.asyncio
async def test_chattermate_invalidation_during_load_cannot_restore_stale_cache() -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    invalidate_chattermate_context_cache()
    started = asyncio.Event()
    release = asyncio.Event()
    load_count = 0

    async def load_context(*args):
        nonlocal load_count
        load_count += 1
        if load_count == 1:
            started.set()
            await release.wait()
        return {"generation": load_count}

    service = object.__new__(ChatterMateOperationsService)
    service._load_context = AsyncMock(side_effect=load_context)  # type: ignore[method-assign]

    pending = asyncio.create_task(service.context(workspace_id, "7d"))
    await started.wait()
    invalidate_chattermate_context_cache(workspace_id)
    release.set()

    assert await pending == {"generation": 2}
    assert await service.context(workspace_id, "7d") == {"generation": 2}
    assert service._load_context.await_count == 2
    invalidate_chattermate_context_cache()
