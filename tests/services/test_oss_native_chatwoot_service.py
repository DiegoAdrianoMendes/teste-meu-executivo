"""Contracts for all native Chatwoot capabilities and their execution gates."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.models.oss_bridge import ExternalEntitySyncState, OSSBridgeProvider
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.oss_bridge import ExternalEntityLinkUpsert
from app.services.chatwoot_bridge_service import ChatwootAccountBindingError
from app.services.chatwoot_client import ChatwootClientError
from app.services.oss_bridge_service import OSSBridgeService
from app.services.oss_capability_registry import oss_capability_registry
from app.services.oss_native_chatwoot_service import (
    _CAPABILITIES,
    CHATWOOT_CAPABILITY_KEYS,
    ChatwootNativeGateError,
    ChatwootNativeService,
)

WORKSPACE_ID = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
ACTOR_ID = UUID("fbdcc4ce-63d3-450d-b85d-13ffb08ea82d")
THREAD_ID = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")

SECRET_VALUES = {
    "API-SECRET-MUST-NOT-LEAK",
    "PASSWORD-MUST-NOT-LEAK",
    "MUTATION-SECRET-MUST-NOT-LEAK",
    "STATE-SECRET-MUST-NOT-LEAK",
    "URL-SECRET-MUST-NOT-LEAK",
    "url-user-must-not-leak",
    "url-password-must-not-leak",
    "SIGNED-BLOB-MUST-NOT-LEAK",
    "MFA-PROVISIONING-MUST-NOT-LEAK",
    "MFA-BACKUP-MUST-NOT-LEAK",
}


class FakeDB:
    def __init__(self, account_id: str = "100001") -> None:
        self.added: list[Any] = []
        self.flush_count = 0
        self.bindings = [
            SimpleNamespace(
                id=UUID("cc76bb6f-2b26-4ea2-b4d0-4b4a41cfa168"),
                external_account_id=account_id,
                external_entity_id=account_id,
            )
        ]

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1

    async def execute(self, _statement: Any) -> Any:
        rows = list(self.bindings)
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: rows),
        )

    async def scalar(self, _statement: Any) -> UUID | str | None:
        for description in getattr(_statement, "column_descriptions", ()):
            if (
                description.get("entity") is User
                and description.get("name") == "name"
            ):
                return "Usuário Teste"
        return self.bindings[0].id if len(self.bindings) == 1 else None


class FakeChatwootClient:
    account_id = "100001"
    inbox_id = "7"

    def __init__(self, account_id: str = "100001") -> None:
        self.account_id = account_id
        self.requests: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

    @staticmethod
    def _row(kind: str = "sidecar") -> dict[str, Any]:
        return {
            "id": 1,
            "name": kind,
            "api_access_token": "API-SECRET-MUST-NOT-LEAK",
            "nested": {"password": "PASSWORD-MUST-NOT-LEAK"},
            "signed_id": "SIGNED-BLOB-MUST-NOT-LEAK",
            "url": (
                "https://url-user-must-not-leak:url-password-must-not-leak@"
                "example.test/path?token=URL-SECRET-MUST-NOT-LEAK"
            ),
        }

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> dict[str, Any] | list[Any]:
        self.requests.append(
            {
                "method": method,
                "path": path,
                "params": params,
                "json": json,
                "expected": expected,
            }
        )
        if method == "GET":
            if path == "/api/v1/profile/mfa":
                return {
                    "enabled": False,
                    "otp_secret": "API-SECRET-MUST-NOT-LEAK",
                    "provisioning_uri": (
                        "otpauth://totp/KLM?secret=MFA-PROVISIONING-MUST-NOT-LEAK"
                    ),
                    "backup_codes": ["MFA-BACKUP-MUST-NOT-LEAK"],
                }
            return {"payload": [self._row(path.rsplit("/", 1)[-1])]}
        return {
            "id": 91,
            "status": "ok",
            "secret": "MUTATION-SECRET-MUST-NOT-LEAK",
        }

    async def account(self) -> dict[str, Any]:
        return {
            "id": 100001,
            "name": "Conta local",
            "features": ["conversation_workflow"],
            "settings": {"conversation_required_attributes": ["case_number"]},
            "access_token": "API-SECRET-MUST-NOT-LEAK",
        }

    async def list_inboxes(self) -> list[dict[str, Any]]:
        return [
            {
                **self._row("inbox"),
                "id": 7,
                "channel_type": "Channel::Api",
            }
        ]

    async def list_teams(self) -> list[dict[str, Any]]:
        return [self._row("team")]

    async def list_labels(self) -> list[dict[str, Any]]:
        return [{**self._row("label"), "title": "urgente"}]

    async def list_agents(self) -> list[dict[str, Any]]:
        return [self._row("agent")]

    async def list_canned_responses(self) -> list[dict[str, Any]]:
        return [{**self._row("canned"), "short_code": "docs", "content": "Envie os PDFs."}]

    async def conversation(self, conversation_id: str) -> dict[str, Any]:
        return {
            "id": int(conversation_id),
            "status": "open",
            "inbox": {"id": 7, "channel_type": "Channel::Api"},
            "custom_attributes": {"klm_managed": True},
            "access_token": "API-SECRET-MUST-NOT-LEAK",
        }

    async def list_messages(
        self, conversation_id: str, maximum: int = 2000
    ) -> list[dict[str, Any]]:
        del conversation_id, maximum
        return [
            {
                "id": 10,
                "private": True,
                "content": "Nota interna",
                "sender": {"api_token": "API-SECRET-MUST-NOT-LEAK"},
            },
            {"id": 11, "private": False, "content": "Mensagem pública"},
        ]

    async def create_message(self, **values: Any) -> dict[str, Any]:
        self.messages.append(values)
        return {
            "id": 99,
            "private": values["private"],
            "content": values["content"],
            "access_token": "MUTATION-SECRET-MUST-NOT-LEAK",
        }


class FakeOperations:
    def __init__(self) -> None:
        self.routing_updates: list[dict[str, Any]] = []

    async def context(self, workspace_id: UUID, thread_id: UUID) -> dict[str, Any]:
        return {
            "workspaceId": str(workspace_id),
            "threadId": str(thread_id),
            "conversationId": "73",
            "inboxes": [{"id": 7, "channel_type": "Channel::Api"}],
            "access_token": "API-SECRET-MUST-NOT-LEAK",
        }

    async def conversation_id(self, workspace_id: UUID, thread_id: UUID) -> str:
        del workspace_id, thread_id
        return "73"

    async def projection(self, workspace_id: UUID, thread_id: UUID) -> dict[str, Any]:
        del workspace_id, thread_id
        return {
            "totalMessages": 2,
            "externalChannelDeliveryEnabled": False,
        }

    async def update_routing(self, **values: Any) -> dict[str, Any]:
        self.routing_updates.append(values)
        return {
            "conversationId": "73",
            "changes": values["changes"],
            "api_token": "MUTATION-SECRET-MUST-NOT-LEAK",
        }


class FakeSyncService:
    def __init__(self) -> None:
        self.workspace_calls: list[UUID] = []
        self.thread_calls: list[tuple[UUID, UUID, UUID]] = []

    async def sync_workspace(self, workspace_id: UUID) -> dict[str, int]:
        self.workspace_calls.append(workspace_id)
        return {"threads": 1, "messages": 2, "pendingMessages": 0}

    async def sync_thread_by_id(
        self, workspace_id: UUID, thread_id: UUID, actor_user_id: UUID
    ) -> dict[str, Any]:
        self.thread_calls.append((workspace_id, thread_id, actor_user_id))
        return {"status": "synced", "threadId": str(thread_id)}


class FakeStateStore:
    def __init__(self) -> None:
        self.executions: list[dict[str, Any]] = []

    async def snapshot(
        self,
        workspace_id: UUID,
        provider: str,
        capability_key: str,
        query: dict[str, Any],
    ) -> dict[str, Any]:
        del query
        return {
            "records": [
                {
                    "id": "native-state",
                    "workspaceId": str(workspace_id),
                    "provider": provider,
                    "capability": capability_key,
                    "secret": "STATE-SECRET-MUST-NOT-LEAK",
                }
            ]
        }

    async def execute(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        provider: str,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        call = {
            "workspaceId": str(workspace_id),
            "actorUserId": str(actor_user_id),
            "provider": provider,
            "capabilityKey": capability_key,
            "command": command,
            "payload": payload,
        }
        self.executions.append(call)
        return {**call, "secret": "STATE-SECRET-MUST-NOT-LEAK"}


def native_service(db: Any | None = None, *, account_id: str = "100001") -> ChatwootNativeService:
    service = ChatwootNativeService(db if db is not None else FakeDB(account_id))
    service.client = FakeChatwootClient(account_id)
    service.operations = FakeOperations()
    service.sync_service = FakeSyncService()
    service.state_store = FakeStateStore()
    return service


async def _workspace(db_session, label: str) -> Workspace:
    workspace = Workspace(
        name=f"Chatwoot Native {label}",
        slug=f"chatwoot-native-{label}-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


async def _bind_chatwoot_account(
    db_session,
    workspace_id: UUID,
    account_id: str,
    *,
    external_entity_id: str | None = None,
) -> None:
    await OSSBridgeService(db_session).upsert_link(
        workspace_id,
        ExternalEntityLinkUpsert(
            provider=OSSBridgeProvider.chatwoot,
            klm_entity_type="workspace",
            klm_entity_id=workspace_id,
            external_account_id=account_id,
            external_entity_type="account",
            external_entity_id=external_entity_id or account_id,
            sync_state=ExternalEntitySyncState.synced,
            metadata={"managedBy": "native-adapter-test"},
        ),
    )
    await db_session.flush()


SNAPSHOT_QUERY = {
    "threadId": str(THREAD_ID),
    "conversationId": 73,
    "contactId": 4,
    "portalId": 1,
    "teamId": 2,
    "inboxId": 7,
    "q": "cliente",
}


def assert_secret_free(value: Any) -> None:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=True)
    for secret in SECRET_VALUES:
        assert secret not in serialized
    assert "?token=" not in serialized
    assert "https://url-user" not in serialized


def test_adapter_keys_match_all_39_chatwoot_registry_capabilities() -> None:
    registry_keys = {
        capability["key"]
        for capability in oss_capability_registry()["capabilities"]
        if capability["provider"] == "chatwoot"
    }

    assert len(CHATWOOT_CAPABILITY_KEYS) == 39
    assert len(set(CHATWOOT_CAPABILITY_KEYS)) == 39
    assert set(CHATWOOT_CAPABILITY_KEYS) == registry_keys


def _validation_value(name: str) -> Any:
    normalized = name.casefold()
    if normalized in {"active", "confirm", "confirmed", "enabled", "private"}:
        return True
    if any(token in normalized for token in ("size", "count", "capacity")):
        return 1
    if "email" in normalized:
        return "local@example.test"
    if "url" in normalized or "uri" in normalized:
        return "https://local.test/callback"
    if normalized in {"rows", "records", "actions", "items", "user_ids", "permissions"}:
        return []
    if normalized in {"data", "body", "payload", "manifest", "event", "conditions"}:
        return {}
    return "local-value"


@pytest.mark.asyncio
async def test_every_advertised_chatwoot_action_has_a_domain_validation_contract() -> None:
    service = ChatwootNativeService.__new__(ChatwootNativeService)
    validated = 0

    for capability_key, spec in _CAPABILITIES.items():
        for command in (*spec.actions, *spec.blocked_actions):
            payload = {
                required.split("|")[0]: _validation_value(required.split("|")[0])
                for required in service._action_required(command)
            }
            result = await service.validate(
                WORKSPACE_ID,
                ACTOR_ID,
                capability_key,
                command,
                payload,
                mode="dry-run",
            )
            assert result["status"] == "validated"
            assert result["externalNetworkExecuted"] is False
            validated += 1

    assert validated == 211


@pytest.mark.asyncio
async def test_remote_reads_and_actions_reject_a_foreign_workspace_account(
    db_session,
    auth_context,
) -> None:
    foreign_workspace = await _workspace(db_session, "foreign-account")
    await _bind_chatwoot_account(db_session, auth_context.workspace_id, "100001")
    await _bind_chatwoot_account(db_session, foreign_workspace.id, "200002")
    service = native_service(db_session, account_id="100001")

    current = await service.snapshot(auth_context.workspace_id, "contacts", {})

    assert current["records"]
    assert service.client.requests[-1]["path"] == "/api/v1/accounts/100001/contacts"
    request_count = len(service.client.requests)

    with pytest.raises(ChatwootAccountBindingError, match="outro workspace"):
        await service.snapshot(foreign_workspace.id, "contacts", {})
    with pytest.raises(ChatwootAccountBindingError, match="outro workspace"):
        await service.execute(
            foreign_workspace.id,
            auth_context.user_id,
            "contacts",
            "create_contact",
            {"name": "Conta estrangeira"},
        )

    assert len(service.client.requests) == request_count
    assert all(
        request["path"] not in {"/api/v1/accounts", "/api/v1/accounts/"}
        for request in service.client.requests
    )


@pytest.mark.asyncio
async def test_remote_access_fails_closed_when_configured_account_belongs_to_other_workspace(
    db_session,
    auth_context,
) -> None:
    account_owner = await _workspace(db_session, "account-owner")
    await _bind_chatwoot_account(db_session, account_owner.id, "100001")
    service = native_service(db_session, account_id="100001")

    with pytest.raises(ChatwootAccountBindingError, match="sem conta"):
        await service.snapshot(auth_context.workspace_id, "contacts", {})
    with pytest.raises(ChatwootAccountBindingError, match="sem conta"):
        await service.execute(
            auth_context.workspace_id,
            auth_context.user_id,
            "contacts",
            "create_contact",
            {"name": "Sem vínculo"},
        )

    assert service.client.requests == []


@pytest.mark.asyncio
async def test_remote_access_fails_closed_on_conflicting_workspace_account_bindings(
    db_session,
    auth_context,
) -> None:
    await _bind_chatwoot_account(db_session, auth_context.workspace_id, "100001")
    await _bind_chatwoot_account(db_session, auth_context.workspace_id, "200002")
    service = native_service(db_session, account_id="100001")

    with pytest.raises(ChatwootAccountBindingError, match="conflitantes"):
        await service.snapshot(auth_context.workspace_id, "contacts", {})

    assert service.client.requests == []


@pytest.mark.parametrize("capability_key", CHATWOOT_CAPABILITY_KEYS)
@pytest.mark.asyncio
async def test_every_capability_has_a_typed_nonempty_sanitized_snapshot(
    capability_key: str,
) -> None:
    result = await native_service().snapshot(WORKSPACE_ID, capability_key, SNAPSHOT_QUERY)

    assert set(result) == {
        "sections",
        "widgets",
        "records",
        "actions",
        "source",
        "health",
    }
    assert result["sections"]
    assert result["widgets"]
    assert result["records"]
    assert result["actions"]
    assert all(record.get("recordType") for record in result["records"])
    assert all(record.get("reasonCode") != "unsupported" for record in result["records"])
    assert result["source"]["transport"] == "loopback_only"
    assert result["source"]["upstreamPin"] == "v4.15.1@97bb8ec"
    assert result["health"]["externalDispatchAllowed"] is False
    assert result["health"]["secretsExposed"] is False
    assert_secret_free(result)


@pytest.mark.parametrize("capability_key", CHATWOOT_CAPABILITY_KEYS)
@pytest.mark.asyncio
async def test_refresh_command_is_complete_for_every_capability(
    capability_key: str,
) -> None:
    result = await native_service().execute(
        WORKSPACE_ID,
        ACTOR_ID,
        capability_key,
        "refresh",
        SNAPSHOT_QUERY,
    )

    assert result["records"]
    assert result["actions"]
    assert_secret_free(result)


SIDE_CAR_MUTATIONS = (
    (
        "contacts",
        "create_contact",
        {"name": "Ana", "email": "ana@example.test"},
        "/contacts",
    ),
    (
        "companies-segments",
        "create_segment",
        {"name": "Ativos", "query": {"status": "active"}},
        "/custom_filters",
    ),
    (
        "canned-responses-macros",
        "create_macro",
        {"name": "Priorizar", "actions": [{"action_name": "add_label"}]},
        "/macros",
    ),
    (
        "automation-rules",
        "create_automation_rule",
        {
            "name": "Triagem",
            "eventName": "conversation_created",
            "conditions": [
                {"attribute_key": "status", "query_operator": "equal_to", "values": ["open"]}
            ],
            "actions": [{"action_name": "add_label", "action_params": ["triagem"]}],
        },
        "/automation_rules",
    ),
    (
        "custom-attributes-contact-operations",
        "create_custom_attribute",
        {
            "attributeDisplayName": "Número do caso",
            "attributeDisplayType": "text",
            "attributeKey": "case_number",
            "attributeModel": "conversation_attribute",
        },
        "/custom_attribute_definitions",
    ),
    (
        "agents-teams-availability",
        "create_agent",
        {"name": "Operador", "email": "operador@example.test"},
        "/agents",
    ),
    (
        "agent-bots-integrations",
        "create_agent_bot",
        {"name": "Bot local", "description": "Sem transporte externo"},
        "/agent_bots",
    ),
    (
        "help-center",
        "create_article",
        {"portalId": 1, "title": "Documentos", "content": "Lista de documentos."},
        "/portals/1/articles",
    ),
    (
        "conversation-search-custom-views",
        "create_custom_view",
        {"name": "Urgentes", "query": {"priority": "urgent"}},
        "/custom_filters",
    ),
    (
        "teams",
        "add_team_members",
        {"teamId": 2, "userIds": [3]},
        "/teams/2/team_members",
    ),
    (
        "inbox-administration",
        "create_inbox",
        {"name": "KLM API", "channel": {"type": "api"}},
        "/inboxes",
    ),
    (
        "assignment-policy",
        "create_assignment_policy",
        {"name": "Round robin"},
        "/assignment_policies",
    ),
    (
        "labels-administration",
        "create_label",
        {"title": "prioritário", "color": "#ff0000"},
        "/labels",
    ),
    (
        "dashboard-apps",
        "create_dashboard_app",
        {"title": "Caso 360", "url": "https://app.example.test/case"},
        "/dashboard_apps",
    ),
    (
        "contact-notes-import-export",
        "add_contact_note",
        {"contactId": 4, "content": "Contato validado."},
        "/contacts/4/notes",
    ),
)


@pytest.mark.parametrize(
    ("capability_key", "command", "payload", "path_suffix"), SIDE_CAR_MUTATIONS
)
@pytest.mark.asyncio
async def test_reviewed_sidecar_mutations_use_specific_upstream_routes(
    capability_key: str,
    command: str,
    payload: dict[str, Any],
    path_suffix: str,
) -> None:
    service = native_service()

    result = await service.execute(WORKSPACE_ID, ACTOR_ID, capability_key, command, payload)

    request = service.client.requests[-1]
    assert request["method"] in {"POST", "PATCH", "DELETE"}
    assert request["path"].endswith(path_suffix)
    assert result["status"] == "completed"
    assert result["externalActionsExecuted"] == 0
    assert result["auditRecorded"] is True
    assert_secret_free(result)


ROOTED_MUTATIONS = (
    (
        "companies-segments",
        "create_segment",
        {"name": "Ativos", "query": {"status": "active"}},
        "custom_filter",
    ),
    (
        "canned-responses",
        "create_canned_response",
        {"shortCode": "docs", "content": "Envie os documentos."},
        "canned_response",
    ),
    (
        "custom-attributes-contact-operations",
        "create_custom_attribute",
        {
            "attributeDisplayName": "Número do caso",
            "attributeDisplayType": "text",
            "attributeKey": "case_number",
            "attributeModel": "conversation_attribute",
        },
        "custom_attribute_definition",
    ),
    (
        "agents-teams-availability",
        "create_agent",
        {"name": "Operador", "email": "operador@example.test"},
        "agent",
    ),
    ("teams", "create_team", {"name": "Triagem"}, "team"),
    (
        "dashboard-apps",
        "create_dashboard_app",
        {"title": "Caso 360", "url": "https://app.example.test/case"},
        "dashboard_app",
    ),
    (
        "help-center",
        "create_article",
        {"portalSlug": "ajuda-klm", "title": "Prazos", "content": "Conteúdo."},
        "article",
    ),
    (
        "assignment-policy",
        "create_assignment_policy",
        {"name": "Round robin"},
        "assignment_policy",
    ),
    (
        "labels-administration",
        "create_label",
        {"title": "prioritário"},
        "label",
    ),
    (
        "contact-notes-import-export",
        "add_contact_note",
        {"contactId": 4, "content": "Contato validado."},
        "note",
    ),
)


@pytest.mark.parametrize(("capability_key", "command", "payload", "root"), ROOTED_MUTATIONS)
@pytest.mark.asyncio
async def test_rails_strong_parameter_envelopes_match_the_pinned_controllers(
    capability_key: str,
    command: str,
    payload: dict[str, Any],
    root: str,
) -> None:
    service = native_service()

    await service.execute(WORKSPACE_ID, ACTOR_ID, capability_key, command, payload)

    request = service.client.requests[-1]
    assert set(request["json"]) == {root}
    if root == "custom_filter":
        assert request["json"][root]["filter_type"] == "contact"
    if root == "dashboard_app":
        assert request["json"][root]["content"] == {
            "url": "https://app.example.test/case",
            "type": "frame",
        }
    if root == "article":
        assert request["path"].endswith("/portals/ajuda-klm/articles")


@pytest.mark.asyncio
async def test_conversation_notes_messages_routing_and_sync_use_typed_services() -> None:
    service = native_service()

    note = await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "private-notes",
        "create_private_note",
        {"conversationId": 73, "content": "Observação interna"},
    )
    message = await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "conversation-composer",
        "send_local_message",
        {"conversationId": 73, "content": "Projeção local"},
    )
    routing = await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "labels-status-assignment",
        "update_routing",
        {
            "threadId": str(THREAD_ID),
            "changes": {
                "status": "pending",
                "priority": "high",
                "assignee_id": 3,
                "team_id": 2,
                "labels": ["urgente"],
            },
        },
    )
    synced = await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "api-webhooks-realtime",
        "sync_thread",
        {"threadId": str(THREAD_ID)},
    )

    assert service.client.messages[0]["private"] is True
    assert service.client.messages[1]["private"] is False
    assert service.operations.routing_updates[0]["changes"]["team_id"] == 2
    assert service.sync_service.thread_calls == [(WORKSPACE_ID, THREAD_ID, ACTOR_ID)]
    for result in (note, message, routing, synced):
        assert result["health"]["externalDispatchAllowed"] is False
        assert_secret_free(result)


@pytest.mark.asyncio
async def test_profile_availability_and_mfa_follow_the_real_profile_contracts() -> None:
    service = native_service()

    await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "profile-mfa",
        "update_profile",
        {"displayName": "Operador KLM"},
    )
    assert service.client.requests[-1]["json"] == {"profile": {"display_name": "Operador KLM"}}

    await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "agents-teams-availability",
        "set_availability",
        {"availability": "online"},
    )
    assert service.client.requests[-1]["json"] == {
        "profile": {"availability": "online", "account_id": 100001}
    }

    await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "profile-mfa",
        "verify_mfa",
        {"otpCode": "123456"},
    )
    assert service.client.requests[-1]["json"] == {"otp_code": "123456"}

    disabled = await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "profile-mfa",
        "disable_mfa",
        {
            "confirm": True,
            "password": "transient-password",
            "otpCode": "123456",
        },
    )
    assert service.client.requests[-1]["json"] == {
        "password": "transient-password",
        "otp_code": "123456",
    }
    assert "transient-password" not in json.dumps(disabled)
    assert_secret_free(disabled)


@pytest.mark.asyncio
async def test_automation_clone_requires_an_inactive_source_rule() -> None:
    service = native_service()
    original_request = service.client._request_json

    async def request(method: str, path: str, **kwargs: Any):
        if method == "GET" and path.endswith("/automation_rules/4"):
            service.client.requests.append({"method": method, "path": path})
            return {"id": 4, "active": False}
        return await original_request(method, path, **kwargs)

    service.client._request_json = request

    result = await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "automation-rules",
        "clone_automation_rule",
        {"ruleId": 4},
    )

    assert service.client.requests[-1]["method"] == "POST"
    assert service.client.requests[-1]["path"].endswith("/automation_rules/4/clone")
    assert result["externalActionsExecuted"] == 0


@pytest.mark.parametrize(
    ("capability_key", "command", "payload"),
    (
        ("reports-csat", "query_reports", {"since": 1, "until": 2}),
        ("csat-report", "query_csat", {"rating": 5}),
        ("conversation-search-custom-views", "search_conversations", {"q": "prazo"}),
        ("custom-attributes-contact-operations", "dry_run_import", {"rows": [{"name": "Ana"}]}),
        ("contact-notes-import-export", "dry_run_export", {"records": [1, 2]}),
        ("agent-bots", "dry_run_bot", {"actions": [{"kind": "classify"}]}),
        ("dashboard-apps", "dry_run_dashboard_app", {"url": "https://app.example.test"}),
        (
            "attachments-media",
            "dry_run_upload",
            {
                "filename": "contrato.pdf",
                "byteSize": 2048,
                "contentType": "application/pdf",
                "checksum": "sha256-local-checksum",
            },
        ),
    ),
)
@pytest.mark.asyncio
async def test_read_and_dry_run_commands_return_real_models_without_dispatch(
    capability_key: str,
    command: str,
    payload: dict[str, Any],
) -> None:
    service = native_service()

    result = await service.execute(WORKSPACE_ID, ACTOR_ID, capability_key, command, payload)

    assert result["health"]["externalDispatchAllowed"] is False
    if "dry_run" in command:
        assert result["status"] == "dry_run"
        assert result["result"]["externalActionsExecuted"] == 0
        if command == "dry_run_upload":
            assert result["result"]["upload"]["uploadPrepared"] is False
            assert (
                result["result"]["upload"]["reasonCode"]
                == "direct_upload_client_contract_unavailable"
            )
    else:
        assert result["records"]
    assert_secret_free(result)


@pytest.mark.parametrize(
    ("capability_key", "command", "payload"),
    (
        ("campaigns", "dispatch", {}),
        ("external-channels", "connect", {}),
        ("whatsapp-campaigns", "send", {}),
        ("captain-ai", "execute", {}),
        ("agent-bots", "execute_bot", {}),
        ("api-webhooks-realtime", "activate_webhook", {}),
        ("attachments-media", "prepare_upload", {}),
        ("canned-responses-macros", "execute_macro", {}),
        ("assignment-policy", "assign_policy_inbox", {}),
    ),
)
@pytest.mark.asyncio
async def test_external_or_unreviewed_effects_are_hard_blocked_without_sidecar_calls(
    capability_key: str,
    command: str,
    payload: dict[str, Any],
) -> None:
    service = native_service()

    with pytest.raises(ChatwootNativeGateError):
        await service.execute(WORKSPACE_ID, ACTOR_ID, capability_key, command, payload)

    assert service.client.requests == []
    assert service.client.messages == []
    assert service.state_store.executions == []


@pytest.mark.parametrize(
    ("capability_key", "command", "payload"),
    (
        ("campaigns", "dry_run", {"audience": [{"id": 1}]}),
        ("external-channels", "save_config", {"channel": "email", "credentialRef": "vault:mail"}),
        ("whatsapp-campaigns", "dry_run", {"recipients": [{"id": 1}]}),
        ("captain-ai", "dry_run", {"task": "summarize"}),
        ("sla", "save_policy", {"policy": {"id": "initial", "targetMinutes": 60}}),
        ("audit-log", "record_event", {"event": {"kind": "reviewed"}}),
        ("companies", "create_company", {"company": {"id": "acme", "name": "Acme"}}),
        ("agent-capacity", "save_capacity", {"capacity": {"id": "default", "limit": 8}}),
        (
            "custom-roles-security",
            "save_role",
            {"role": {"id": "reviewer", "permissions": ["read"]}},
        ),
    ),
)
@pytest.mark.asyncio
async def test_gated_and_enterprise_equivalents_use_only_the_klm_state_store(
    capability_key: str,
    command: str,
    payload: dict[str, Any],
) -> None:
    service = native_service()

    result = await service.execute(WORKSPACE_ID, ACTOR_ID, capability_key, command, payload)

    assert service.state_store.executions[-1]["capabilityKey"] == capability_key
    assert service.state_store.executions[-1]["command"] == command
    assert service.client.requests == []
    assert result["source"]["authority"] == "klm_native"
    assert result["health"]["externalDispatchAllowed"] is False
    assert_secret_free(result)


@pytest.mark.asyncio
async def test_destructive_commands_require_confirmation_before_any_mutation() -> None:
    service = native_service()

    with pytest.raises(ChatwootNativeGateError, match="confirm=true"):
        await service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "contacts",
            "delete_contact",
            {"contactId": 4},
        )

    assert service.client.requests == []

    result = await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "contacts",
        "delete_contact",
        {"contactId": 4, "confirm": True},
    )

    assert service.client.requests[-1]["method"] == "DELETE"
    assert service.client.requests[-1]["path"].endswith("/contacts/4")
    assert result["auditRecorded"] is True

    orchestrated = native_service()
    outer_result = await orchestrated.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "contacts",
        "delete_contact",
        {
            "contactId": 5,
            "operationId": "outer-delete-contact",
            "idempotencyKey": "outer-delete-contact",
        },
    )
    assert orchestrated.client.requests[-1]["path"].endswith("/contacts/5")
    assert outer_result["auditRecorded"] is True


@pytest.mark.asyncio
async def test_actions_publish_specific_required_fields_instead_of_generic_links() -> None:
    service = native_service()

    routing = await service.snapshot(WORKSPACE_ID, "inboxes-routing", {})
    labels = await service.snapshot(WORKSPACE_ID, "labels-administration", {})

    routing_action = next(
        action for action in routing["actions"] if action["command"] == "update_routing"
    )
    delete_label = next(
        action for action in labels["actions"] if action["command"] == "delete_label"
    )
    assert routing_action["required"] == ["thread_id", "changes"]
    assert delete_label["required"] == ["label_id"]
    assert delete_label["requiresConfirmation"] is True


@pytest.mark.asyncio
async def test_private_note_delete_cannot_target_a_public_message() -> None:
    service = native_service()

    with pytest.raises(ChatwootNativeGateError, match="mensagem pública"):
        await service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "private-notes",
            "delete_private_note",
            {"conversationId": 73, "messageId": 11, "confirm": True},
        )

    assert service.client.requests == []

    result = await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "private-notes",
        "delete_private_note",
        {"conversationId": 73, "messageId": 10, "confirm": True},
    )
    assert service.client.requests[-1]["path"].endswith("/conversations/73/messages/10")
    assert result["auditRecorded"] is True


@pytest.mark.asyncio
async def test_successful_empty_delete_response_is_acknowledged() -> None:
    service = native_service()

    async def empty_response(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise ChatwootClientError("Chatwoot retornou JSON inválido.")

    service.client._request_json = empty_response

    result = await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "labels-administration",
        "delete_label",
        {"labelId": 4, "confirm": True},
    )

    assert result["result"] == {"acknowledged": True, "emptyResponse": True}
    assert result["auditRecorded"] is True


@pytest.mark.parametrize(
    "payload",
    (
        {"apiAccessToken": "raw-value"},
        {"nested": {"password": "raw-value"}},
        {"authorization": "Bearer abc.def.ghi"},
        {"url": "https://user:password@example.test/path"},
    ),
)
@pytest.mark.asyncio
async def test_raw_secret_inputs_fail_closed(payload: dict[str, Any]) -> None:
    service = native_service()

    with pytest.raises(ChatwootNativeGateError):
        await service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "external-channels",
            "save_config",
            payload,
        )

    assert service.client.requests == []
    assert service.state_store.executions == []


@pytest.mark.asyncio
async def test_non_api_or_non_klm_conversation_cannot_send_a_message() -> None:
    service = native_service()
    service.client.conversation = lambda _conversation_id: _async_value(
        {
            "id": 73,
            "inbox": {"channel_type": "Channel::Email"},
            "custom_attributes": {"klm_managed": True},
        }
    )

    with pytest.raises(ChatwootNativeGateError, match="Channel::Api"):
        await service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "conversation-composer",
            "send_local_message",
            {"conversationId": 73, "content": "Não enviar"},
        )

    assert service.client.messages == []


@pytest.mark.asyncio
async def test_sync_cannot_use_an_external_transport_inbox() -> None:
    service = native_service()
    service.client.list_inboxes = lambda: _async_value(
        [{"id": 7, "channel_type": "Channel::Whatsapp"}]
    )

    with pytest.raises(ChatwootNativeGateError, match="Channel::Api local"):
        await service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "api-webhooks-realtime",
            "sync_workspace",
            {},
        )

    assert service.sync_service.workspace_calls == []


async def _async_value(value: Any) -> Any:
    return value


@pytest.mark.asyncio
async def test_real_state_store_persists_paid_equivalent_and_runs_local_evaluation(
    db_session,
    auth_context,
) -> None:
    service = ChatwootNativeService(db_session)

    saved = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "sla",
        "save_policy",
        {
            "expectedVersion": 0,
            "policy": {
                "id": "legal-intake",
                "name": "Triagem jurídica",
                "targetMinutes": 60,
            },
        },
    )
    snapshot = await service.snapshot(auth_context.workspace_id, "sla", {})
    evaluated = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "sla",
        "evaluate",
        {
            "idempotencyKey": "sla-evaluate-legal-intake",
            "conversation": {"id": "local-73", "elapsedMinutes": 15},
        },
    )
    workflow = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "conversation-workflow",
        "set_required_attributes",
        {"attributeKeys": ["case_number", "practice_area"]},
    )
    workflow_snapshot = await service.snapshot(
        auth_context.workspace_id, "conversation-workflow", {}
    )
    before_orchestrated = await service.snapshot(auth_context.workspace_id, "sla", {})
    command_count = sum(
        record.get("recordType") == "klmNativeCommand" for record in before_orchestrated["records"]
    )
    orchestrated = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "sla",
        "evaluate",
        {
            "operationId": "outer-document-command",
            "idempotencyKey": "outer-document-command",
            "conversation": {"id": "local-74", "elapsedMinutes": 70},
        },
    )
    after_orchestrated = await service.snapshot(auth_context.workspace_id, "sla", {})

    assert saved["source"]["authority"] == "klm_native"
    assert any(record.get("id") == "legal-intake" for record in snapshot["records"])
    assert evaluated["result"]["status"] == "succeeded"
    assert evaluated["result"]["dispatcherInvoked"] is True
    assert evaluated["result"]["externalNetworkAllowed"] is False
    assert evaluated["externalActionsExecuted"] == 0
    assert workflow["source"]["authority"] == "klm_native"
    assert any(
        record.get("configuration", {}).get("requiredAttributeKeys")
        == ["case_number", "practice_area"]
        for record in workflow_snapshot["records"]
    )
    assert service.client is None
    assert orchestrated["result"]["status"] == "evaluated"
    assert (
        sum(
            record.get("recordType") == "klmNativeCommand"
            for record in after_orchestrated["records"]
        )
        == command_count
    )
    assert_secret_free(
        (
            saved,
            snapshot,
            evaluated,
            workflow,
            workflow_snapshot,
            orchestrated,
        )
    )
