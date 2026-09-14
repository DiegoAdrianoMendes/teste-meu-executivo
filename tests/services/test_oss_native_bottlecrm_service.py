"""Focused contracts for the native BottleCRM adapter."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.deps import AuthContext
from app.db.tenant import WorkspaceContextError
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    OSSBridgeProvider,
)
from app.models.oss_native import OSSNativeCapabilityState
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.services.bottlecrm_client import BottleCRMClientError
from app.services.oss_capability_registry import oss_capability_registry
from app.services.oss_native_bottlecrm_service import (
    _CAPABILITIES,
    CAPABILITY_KEYS,
    BottleCRMNativeService,
    BottleCRMNativeWorkspaceBindingError,
)

_DIGEST = "a" * 64
_ORG_A = "organization-klm-a"

_WRITE_COMMANDS = {
    "accounts-contacts-leads": "upsert_account",
    "opportunities-kanban": "set_probability",
    "cases-tickets": "update_case",
    "case-solutions-kb": "upsert_solution",
    "case-mailboxes-watchers": "upsert_mailbox",
    "case-merge-hierarchy": "link_parent",
    "case-time-billing": "add_time_entry",
    "case-analytics-bulk-import": "commit_import",
    "case-pipelines-reopen": "upsert_pipeline",
    "tasks-calendar": "upsert_task",
    "task-boards-pipelines": "upsert_board",
    "products-estimates-quotes": "upsert_product",
    "invoices-recurrence": "upsert_invoice",
    "orders": "upsert_order",
    "templates-macros": "upsert_macro",
    "opportunity-aging-goals": "upsert_goal",
    "invoice-estimate-portals": "publish_portal",
    "approvals-routing-escalation": "upsert_routing_rule",
    "business-hours-sla-csat": "upsert_calendar",
    "custom-fields-duplicates": "upsert_custom_field",
    "tags-documents": "upsert_tag",
    "notifications-sse": "subscribe",
    "import-export": "commit_import",
    "users-rls-rbac": "upsert_user",
    "audit-suspicious-activity": "acknowledge_alert",
    "api-tokens-mcp": "create_token",
    "invoice-products": "upsert_product",
    "invoice-templates": "upsert_template",
    "invoice-reports": "schedule_report",
    "ticket-approvals": "request_changes",
    "ticket-routing": "upsert_routing_rule",
    "ticket-escalation": "upsert_escalation_policy",
    "organization-settings": "update_organization_settings",
    "organization-switcher": "select_organization",
    "mobile-app": "update_mobile_preferences",
    "external-integrations": "upsert_integration_config",
}


def _remote(*, digest: str = _DIGEST, available: bool = False) -> dict:
    return {
        "available": available,
        "resources": {"fixture": {"count": 1}} if available else {},
        "operations": None,
        "errors": {} if available else {"client": {"code": "offline"}},
        "digest": digest,
    }


def _service(db: AsyncSession, *, remote: dict | None = None) -> BottleCRMNativeService:
    service = BottleCRMNativeService(db)
    service._remote_bundle = AsyncMock(return_value=remote or _remote())
    return service


async def _add_member(
    db: AsyncSession,
    *,
    workspace: Workspace,
    suffix: str,
    role: WorkspaceRole,
    denials: list[dict] | None = None,
) -> User:
    user = User(
        name=f"Usuário {suffix}",
        email=f"{suffix}@example.test",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials=suffix[:2].upper(),
        color="#111827",
        is_active=True,
        is_internal_staff=False,
    )
    db.add(user)
    await db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=role,
            status=MembershipStatus.active,
            is_default=False,
            permission_denials=denials or [],
        )
    )
    await db.flush()
    return user


async def _bind_bottlecrm_organization(
    db: AsyncSession,
    *,
    workspace_id,
    organization_id: str,
) -> ExternalEntityLink:
    binding = ExternalEntityLink(
        workspace_id=workspace_id,
        provider=OSSBridgeProvider.bottlecrm,
        klm_entity_type="workspace",
        klm_entity_id=workspace_id,
        external_account_id=organization_id,
        external_entity_type="organization",
        external_entity_id=organization_id,
        link_metadata={"managedBy": "test"},
        sync_state=ExternalEntitySyncState.synced,
    )
    db.add(binding)
    await db.flush()
    return binding


def test_adapter_keys_match_all_bottlecrm_catalog_capabilities() -> None:
    registry_keys = {
        item["key"]
        for item in oss_capability_registry()["capabilities"]
        if item["provider"] == "bottlecrm"
    }

    assert len(CAPABILITY_KEYS) == 36
    assert set(CAPABILITY_KEYS) == registry_keys
    assert set(_WRITE_COMMANDS) == registry_keys


@pytest.mark.asyncio
async def test_every_advertised_bottlecrm_action_has_a_domain_validation_contract(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    service = _service(db_session)
    validated = 0

    for spec in _CAPABILITIES:
        for command in spec.actions:
            payload = {
                "expectedVersion": 0,
                "recordId": f"validate-{spec.key}",
                "provider": "local-provider",
                "data": {"label": command},
            }
            if command == "commit_import":
                payload["rows"] = [{"externalId": f"row-{spec.key}"}]
            result = await service.validate(
                auth_context.workspace_id,
                auth_context.user_id,
                spec.key,
                command,
                payload,
                mode="dry-run",
            )
            assert result["status"] == "validated"
            assert result["externalNetworkExecuted"] is False
            validated += 1

    assert validated == 118


@pytest.mark.asyncio
async def test_bottlecrm_action_contracts_expose_required_native_form_fields(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    service = _service(db_session)
    archive = await service.snapshot(
        auth_context.workspace_id,
        "accounts-contacts-leads",
        {},
    )
    merge = await service.snapshot(
        auth_context.workspace_id,
        "case-merge-hierarchy",
        {},
    )
    integration = await service.snapshot(
        auth_context.workspace_id,
        "external-integrations",
        {},
    )

    archive_action = next(
        action for action in archive["actions"] if action["command"] == "archive_record"
    )
    merge_action = next(
        action for action in merge["actions"] if action["command"] == "merge_cases"
    )
    test_action = next(
        action
        for action in integration["actions"]
        if action["command"] == "test_integration"
    )
    assert archive_action["required"] == ["recordId"]
    assert merge_action["required"] == ["recordId", "intoId"]
    assert test_action["required"] == ["provider"]


@pytest.mark.asyncio
@pytest.mark.parametrize("capability_key", CAPABILITY_KEYS)
async def test_every_capability_has_a_specific_non_empty_sanitized_snapshot(
    db_session: AsyncSession,
    auth_context: AuthContext,
    capability_key: str,
) -> None:
    service = _service(db_session)

    result = await service.snapshot(
        auth_context.workspace_id,
        capability_key,
        {"limit": 25, "email": "private@example.test", "apiToken": "sk-secret-value"},
    )

    assert result["provider"] == "bottlecrm"
    assert result["capabilityKey"] == capability_key
    assert result["sourceOfTruth"] == "klm"
    assert result["canonical"]["tenantScoped"] is True
    assert result["view"]["sections"]
    assert result["actions"]
    assert all(action["command"] for action in result["actions"])
    assert result["nativeState"]["version"] == 0
    encoded = json.dumps(result, sort_keys=True)
    assert "private@example.test" not in encoded
    assert "sk-secret-value" not in encoded
    assert "apiToken" in result["query"]["redactedFields"]


@pytest.mark.asyncio
@pytest.mark.parametrize("capability_key", CAPABILITY_KEYS)
async def test_every_capability_accepts_a_tenant_scoped_native_write_command(
    db_session: AsyncSession,
    auth_context: AuthContext,
    capability_key: str,
) -> None:
    service = _service(db_session)
    command = _WRITE_COMMANDS[capability_key]
    payload = {
        "expectedVersion": 0,
        "recordId": f"record-{capability_key}",
        "data": {"label": capability_key, "status": "draft"},
    }
    if command == "commit_import":
        payload["rows"] = [{"externalId": f"row-{capability_key}"}]

    result = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        capability_key,
        command,
        payload,
    )

    assert result["status"] == (
        "local_staging" if command == "commit_import" else "completed"
    )
    assert result["version"] == 1
    assert result["record"]["id"] == f"record-{capability_key}"
    assert result["record"]["lastCommand"] == command
    assert result["sourceOfTruth"] == "klm"
    if command == "commit_import":
        assert result["stagedLocally"] is True
        assert result["imported"] is False
        assert result["record"]["sync"]["state"] == "local_staging"
        assert result["record"]["data"]["imported"] is False

    persisted = await db_session.scalar(
        OSSNativeCapabilityState.__table__.select()
        .where(
            OSSNativeCapabilityState.workspace_id == auth_context.workspace_id,
            OSSNativeCapabilityState.provider == "bottlecrm",
            OSSNativeCapabilityState.capability == capability_key,
        )
        .with_only_columns(OSSNativeCapabilityState.id)
    )
    assert persisted is not None


@pytest.mark.asyncio
async def test_bulk_import_validates_every_row_and_only_stages_valid_batches_locally(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    service = _service(db_session)

    preview = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "case-analytics-bulk-import",
        "preview_import",
        {"rows": [{"caseNumber": "CASE-1"}, "invalid-row"]},
    )
    with pytest.raises(ValueError, match="Importação rejeitada"):
        await service.execute(
            auth_context.workspace_id,
            auth_context.user_id,
            "case-analytics-bulk-import",
            "commit_import",
            {
                "expectedVersion": 0,
                "recordId": "import-invalid",
                "rows": [{"caseNumber": "CASE-1"}, "invalid-row"],
            },
        )

    staged = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "case-analytics-bulk-import",
        "commit_import",
        {
            "expectedVersion": 0,
            "recordId": "import-valid",
            "rows": [
                {"caseNumber": "CASE-1", "status": "open"},
                {"caseNumber": "CASE-2", "status": "closed"},
            ],
        },
    )

    assert preview["result"]["preview"] == {
        "total": 2,
        "valid": 1,
        "invalid": 1,
        "errors": [
            {"row": 2, "code": "invalid_row", "message": "Linha deve ser objeto."}
        ],
        "commitAllowed": False,
        "disposition": "validation_only",
        "imported": False,
    }
    assert staged["status"] == "local_staging"
    assert staged["stagedLocally"] is True
    assert staged["imported"] is False
    assert staged["record"]["sync"]["state"] == "local_staging"
    assert staged["record"]["data"]["stagingState"] == "local_staging"
    assert staged["record"]["data"]["rowCount"] == 2
    assert staged["record"]["data"]["imported"] is False


@pytest.mark.asyncio
async def test_optimistic_conflict_preserves_the_first_write(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    service = _service(db_session)
    first = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "invoice-products",
        "upsert_product",
        {
            "expectedVersion": 0,
            "recordId": "product-1",
            "data": {"label": "Consulta jurídica", "price": 1500},
        },
    )

    conflict = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "invoice-products",
        "upsert_product",
        {
            "expectedVersion": 0,
            "recordId": "product-1",
            "data": {"label": "Mudança obsoleta", "price": 1},
        },
    )
    snapshot = await service.snapshot(
        auth_context.workspace_id,
        "invoice-products",
        {},
    )

    assert first["version"] == 1
    assert conflict["status"] == "conflict"
    assert conflict["conflict"]["type"] == "version_mismatch"
    assert conflict["conflict"]["remotePreserved"] is True
    assert snapshot["nativeState"]["version"] == 1
    assert snapshot["nativeState"]["total"] == 1
    assert snapshot["nativeState"]["items"][0]["data"]["price"] == 1500
    assert snapshot["nativeState"]["conflicts"][-1]["type"] == "version_mismatch"


@pytest.mark.asyncio
async def test_delete_creates_tombstone_and_restore_is_versioned(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    service = _service(db_session)
    await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "invoice-products",
        "upsert_product",
        {
            "expectedVersion": 0,
            "recordId": "product-1",
            "data": {"label": "Parecer"},
        },
    )
    removed = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "invoice-products",
        "archive_product",
        {
            "expectedVersion": 1,
            "expectedRecordVersion": 1,
            "recordId": "product-1",
            "reason": "Catálogo revisado",
        },
    )
    deleted_snapshot = await service.snapshot(
        auth_context.workspace_id,
        "invoice-products",
        {"includeTombstones": True},
    )
    restored = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "invoice-products",
        "restore_record",
        {"expectedVersion": 2, "recordId": "product-1"},
    )

    assert removed["version"] == 2
    assert removed["record"]["remotePreserved"] is True
    assert deleted_snapshot["nativeState"]["total"] == 0
    assert deleted_snapshot["nativeState"]["tombstones"][0]["id"] == "product-1"
    assert restored["version"] == 3
    assert restored["record"]["version"] == 2


@pytest.mark.asyncio
async def test_remote_snapshot_requires_binding_before_credentials_or_resource_reads(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    identity = AsyncMock(return_value=_ORG_A)
    read_payload = AsyncMock(return_value={"items": []})
    service = BottleCRMNativeService(db_session)
    service._client_instance = SimpleNamespace(
        organization_id=identity,
        read_payload=read_payload,
    )

    with pytest.raises(BottleCRMNativeWorkspaceBindingError) as raised:
        await service.snapshot(
            auth_context.workspace_id,
            "case-solutions-kb",
            {},
        )

    assert raised.value.code == "workspace_binding_missing"
    identity.assert_not_awaited()
    read_payload.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_workspace_binding_fails_before_credentials(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _bind_bottlecrm_organization(
        db_session,
        workspace_id=auth_context.workspace_id,
        organization_id=_ORG_A,
    )
    await _bind_bottlecrm_organization(
        db_session,
        workspace_id=auth_context.workspace_id,
        organization_id="organization-klm-a-duplicate",
    )
    identity = AsyncMock(return_value=_ORG_A)
    read_payload = AsyncMock(return_value={"items": []})
    service = BottleCRMNativeService(db_session)
    service._client_instance = SimpleNamespace(
        organization_id=identity,
        read_payload=read_payload,
    )

    with pytest.raises(BottleCRMNativeWorkspaceBindingError) as raised:
        await service.snapshot(
            auth_context.workspace_id,
            "case-solutions-kb",
            {},
        )

    assert raised.value.code == "workspace_binding_ambiguous"
    identity.assert_not_awaited()
    read_payload.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_snapshots_keep_two_workspace_organizations_isolated(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    workspace_b = Workspace(
        name="Workspace B BottleCRM",
        slug="workspace-b-bottlecrm-binding",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace_b)
    await db_session.flush()
    await _bind_bottlecrm_organization(
        db_session,
        workspace_id=auth_context.workspace_id,
        organization_id=_ORG_A,
    )
    await _bind_bottlecrm_organization(
        db_session,
        workspace_id=workspace_b.id,
        organization_id="organization-klm-b",
    )
    await db_session.commit()

    session_factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    snapshots = []
    for workspace_id, organization_id in (
        (auth_context.workspace_id, _ORG_A),
        (workspace_b.id, "organization-klm-b"),
    ):
        async with session_factory() as isolated_session:
            identity = AsyncMock(return_value=organization_id)
            read_payload = AsyncMock(return_value={"organizationId": organization_id, "items": []})
            service = BottleCRMNativeService(isolated_session)
            service._client_instance = SimpleNamespace(
                organization_id=identity,
                read_payload=read_payload,
            )

            snapshot = await service.snapshot(workspace_id, "case-solutions-kb", {})
            snapshots.append(snapshot)

            assert snapshot["upstream"]["binding"] == {
                "required": True,
                "validated": True,
                "organizationId": organization_id,
            }
            assert (
                snapshot["upstream"]["resources"]["solutions"]["organizationId"] == organization_id
            )
            identity.assert_awaited_once()
            read_payload.assert_awaited_once()

    assert (
        snapshots[0]["upstream"]["binding"]["organizationId"]
        != snapshots[1]["upstream"]["binding"]["organizationId"]
    )


@pytest.mark.asyncio
async def test_foreign_runtime_organization_blocks_second_workspace_before_listing(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    workspace_b = Workspace(
        name="Workspace B Foreign BottleCRM",
        slug="workspace-b-bottlecrm-foreign",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace_b)
    await db_session.flush()
    await _bind_bottlecrm_organization(
        db_session,
        workspace_id=auth_context.workspace_id,
        organization_id=_ORG_A,
    )
    await _bind_bottlecrm_organization(
        db_session,
        workspace_id=workspace_b.id,
        organization_id="organization-klm-b",
    )
    await db_session.commit()

    session_factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as isolated_session:
        identity = AsyncMock(return_value=_ORG_A)
        read_payload = AsyncMock(return_value={"items": ["foreign"]})
        service = BottleCRMNativeService(isolated_session)
        service._client_instance = SimpleNamespace(
            organization_id=identity,
            read_payload=read_payload,
        )

        with pytest.raises(BottleCRMNativeWorkspaceBindingError) as raised:
            await service.snapshot(workspace_b.id, "case-solutions-kb", {})

        assert raised.value.code == "runtime_organization_mismatch"
        identity.assert_awaited_once()
        read_payload.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_action_requires_binding_before_credentials_or_dispatch(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    service = _service(db_session, remote=_remote(available=True))
    identity = AsyncMock(return_value=_ORG_A)
    request = AsyncMock()
    service._client_instance = SimpleNamespace(
        organization_id=identity,
        _request_payload=request,
    )

    with pytest.raises(BottleCRMNativeWorkspaceBindingError) as raised:
        await service.execute(
            auth_context.workspace_id,
            auth_context.user_id,
            "opportunities-kanban",
            "move_opportunity",
            {
                "expectedVersion": 0,
                "recordId": "opportunity-1",
                "externalId": "opportunity-1",
                "applyRemote": True,
                "confirmRemoteWrite": True,
                "expectedRemoteDigest": _DIGEST,
                "remotePayload": {"stage": "PROPOSAL"},
            },
        )

    assert raised.value.code == "workspace_binding_missing"
    identity.assert_not_awaited()
    request.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_write_rejects_stale_digest_without_calling_bottlecrm(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _bind_bottlecrm_organization(
        db_session,
        workspace_id=auth_context.workspace_id,
        organization_id=_ORG_A,
    )
    service = _service(db_session, remote=_remote(digest="b" * 64, available=True))
    request = AsyncMock()
    service._client_instance = SimpleNamespace(
        organization_id=AsyncMock(return_value=_ORG_A),
        _request_payload=request,
    )

    result = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "opportunities-kanban",
        "move_opportunity",
        {
            "expectedVersion": 0,
            "recordId": "opportunity-1",
            "externalId": "opportunity-1",
            "applyRemote": True,
            "confirmRemoteWrite": True,
            "expectedRemoteDigest": "a" * 64,
            "remotePayload": {"stage": "PROPOSAL"},
        },
    )

    assert result["status"] == "conflict"
    assert result["conflict"]["type"] == "remote_changed"
    assert result["conflict"]["remotePreserved"] is True
    request.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_remote_failure_is_recorded_for_manual_reconciliation(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _bind_bottlecrm_organization(
        db_session,
        workspace_id=auth_context.workspace_id,
        organization_id=_ORG_A,
    )
    service = _service(db_session, remote=_remote(available=True))
    request = AsyncMock(side_effect=BottleCRMClientError("timeout after dispatch"))
    service._client_instance = SimpleNamespace(
        organization_id=AsyncMock(return_value=_ORG_A),
        _request_payload=request,
    )

    result = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "opportunities-kanban",
        "move_opportunity",
        {
            "expectedVersion": 0,
            "recordId": "opportunity-1",
            "externalId": "opportunity-1",
            "applyRemote": True,
            "confirmRemoteWrite": True,
            "expectedRemoteDigest": _DIGEST,
            "remotePayload": {"stage": "PROPOSAL"},
        },
    )

    assert result["status"] == "conflict"
    assert result["conflict"]["type"] == "remote_execution_uncertain"
    assert result["conflict"]["remotePreserved"] is None
    assert result["conflict"]["uncertain"] is True
    request.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_sync_cannot_create_a_missing_workspace_binding(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    service = _service(db_session, remote=_remote(available=True))
    identity = AsyncMock(return_value=_ORG_A)
    sync = AsyncMock(return_value={"sourceOfTruth": "klm"})
    service._client_instance = SimpleNamespace(organization_id=identity)
    service._sync_instance = SimpleNamespace(sync_workspace=sync)

    with pytest.raises(BottleCRMNativeWorkspaceBindingError) as raised:
        await service.execute(
            auth_context.workspace_id,
            auth_context.user_id,
            "accounts-contacts-leads",
            "sync_workspace",
            {
                "expectedVersion": 0,
                "expectedRemoteDigest": _DIGEST,
                "confirmRemoteWrite": True,
            },
        )

    assert raised.value.code == "workspace_binding_missing"
    identity.assert_not_awaited()
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_sync_uses_bottlecrm_sync_after_matching_remote_precondition(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _bind_bottlecrm_organization(
        db_session,
        workspace_id=auth_context.workspace_id,
        organization_id=_ORG_A,
    )
    service = _service(db_session, remote=_remote(available=True))
    sync = AsyncMock(return_value={"sourceOfTruth": "klm", "updated": 2, "stale": 0})
    service._sync_instance = SimpleNamespace(sync_workspace=sync)
    service._client_instance = SimpleNamespace(
        organization_id=AsyncMock(return_value=_ORG_A),
    )

    result = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "accounts-contacts-leads",
        "sync_workspace",
        {
            "expectedVersion": 0,
            "expectedRemoteDigest": _DIGEST,
            "confirmRemoteWrite": True,
        },
    )

    assert result["status"] == "completed"
    assert result["remote"]["sourceOfTruth"] == "klm"
    sync.assert_awaited_once_with(auth_context.workspace_id)


@pytest.mark.asyncio
async def test_remote_action_rejects_foreign_organization_selector(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _bind_bottlecrm_organization(
        db_session,
        workspace_id=auth_context.workspace_id,
        organization_id=_ORG_A,
    )
    service = _service(db_session, remote=_remote(available=True))
    request = AsyncMock()
    service._client_instance = SimpleNamespace(
        organization_id=AsyncMock(return_value=_ORG_A),
        _request_payload=request,
    )

    with pytest.raises(BottleCRMNativeWorkspaceBindingError) as raised:
        await service.execute(
            auth_context.workspace_id,
            auth_context.user_id,
            "organization-switcher",
            "select_organization",
            {
                "expectedVersion": 0,
                "recordId": "organization-klm-b",
                "applyRemote": True,
                "confirmRemoteWrite": True,
                "expectedRemoteDigest": _DIGEST,
                "remotePayload": {"org_id": "organization-klm-b"},
            },
        )

    assert raised.value.code == "remote_organization_mismatch"
    request.assert_not_awaited()


@pytest.mark.asyncio
async def test_credentials_are_removed_from_state_result_and_audit_payloads(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    service = _service(db_session)

    result = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "external-integrations",
        "upsert_integration_config",
        {
            "expectedVersion": 0,
            "recordId": "calendar-google",
            "data": {
                "provider": "google_calendar",
                "apiKey": "sk-forbidden-secret",
                "authorization": "Bearer forbidden-value",
                "enabled": False,
            },
        },
    )
    snapshot = await service.snapshot(
        auth_context.workspace_id,
        "external-integrations",
        {},
    )
    encoded = json.dumps({"result": result, "snapshot": snapshot}, sort_keys=True)

    assert "sk-forbidden-secret" not in encoded
    assert "forbidden-value" not in encoded
    assert result["record"]["data"]["redactedFields"] == ["apiKey", "authorization"]
    assert snapshot["nativeState"]["items"][0]["data"]["enabled"] is False


@pytest.mark.asyncio
async def test_viewer_and_explicit_permission_denial_cannot_write(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    assert workspace is not None
    viewer = await _add_member(
        db_session,
        workspace=workspace,
        suffix="viewer-gate",
        role=WorkspaceRole.viewer,
    )
    denied_admin = await _add_member(
        db_session,
        workspace=workspace,
        suffix="denied-admin",
        role=WorkspaceRole.workspace_admin,
        denials=[{"module": "finance", "action": "manage", "scope": "workspace"}],
    )
    await db_session.commit()
    service = _service(db_session)

    for actor in (viewer, denied_admin):
        with pytest.raises(PermissionError, match="finance:manage"):
            await service.execute(
                auth_context.workspace_id,
                actor.id,
                "invoice-products",
                "upsert_product",
                {"expectedVersion": 0, "recordId": "blocked"},
            )


@pytest.mark.asyncio
async def test_state_and_actor_cannot_cross_workspace_boundaries(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    workspace_b = Workspace(
        name="Workspace B",
        slug="workspace-b-native-bottle",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace_b)
    await db_session.flush()
    actor_b = await _add_member(
        db_session,
        workspace=workspace_b,
        suffix="tenant-b",
        role=WorkspaceRole.workspace_owner,
    )
    await db_session.commit()

    service_a = _service(db_session)
    await service_a.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "invoice-products",
        "upsert_product",
        {"expectedVersion": 0, "recordId": "only-a", "data": {"price": 99}},
    )
    with pytest.raises(PermissionError, match="vínculo ativo"):
        await service_a.execute(
            auth_context.workspace_id,
            actor_b.id,
            "invoice-products",
            "upsert_product",
            {"expectedVersion": 1, "recordId": "cross-tenant"},
        )

    session_factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as isolated_session:
        service_b = _service(isolated_session)
        snapshot_b = await service_b.snapshot(workspace_b.id, "invoice-products", {})
        assert snapshot_b["nativeState"]["total"] == 0
        with pytest.raises(PermissionError, match="vínculo ativo"):
            await service_b.execute(
                workspace_b.id,
                auth_context.user_id,
                "invoice-products",
                "upsert_product",
                {"expectedVersion": 0, "recordId": "cross-tenant"},
            )

    with pytest.raises(WorkspaceContextError):
        await service_a.snapshot(workspace_b.id, "invoice-products", {})


@pytest.mark.asyncio
async def test_unknown_capability_and_command_fail_closed(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    service = _service(db_session)

    with pytest.raises(ValueError, match="desconhecida"):
        await service.snapshot(auth_context.workspace_id, "unknown", {})
    with pytest.raises(ValueError, match="não pertence"):
        await service.execute(
            auth_context.workspace_id,
            auth_context.user_id,
            "invoice-products",
            "delete_everything",
            {"expectedVersion": 0},
        )
