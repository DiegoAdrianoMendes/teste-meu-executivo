"""HTTP contract tests for the isolated native OSS router."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints.oss_native import router
from app.core.deps import (
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
)
from app.main import safe_request_validation_error
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole
from app.services.oss_native_document_service import OSSNativeDocumentService
from app.services.oss_native_state_service import (
    register_provider_dispatcher,
    unregister_provider_dispatcher,
)

FORBIDDEN_PUBLIC_RESPONSE = re.compile(
    r"chatwoot|rocket(?:[. _-]?chat)|chatter(?:[. _-]?mate)|"
    r"bottle(?:[. _-]?crm)|sidecar|upstream|/api/v1/oss-",
    re.IGNORECASE,
)


pytestmark = pytest.mark.usefixtures("active_subscription")


def assert_public_response(response) -> None:
    assert FORBIDDEN_PUBLIC_RESPONSE.search(response.text) is None

    def visit(value) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
                assert not normalized.startswith("oss")
                assert not normalized.startswith("source")
                assert not any(
                    marker in normalized
                    for marker in (
                        "provider",
                        "engine",
                        "topology",
                        "adapter",
                        "dispatcher",
                    )
                )
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(response.json())


@pytest_asyncio.fixture
async def oss_native_client(db_session, auth_context):
    @asynccontextmanager
    async def lifespan(_app):
        yield

    test_app = FastAPI(lifespan=lifespan)
    test_app.add_exception_handler(RequestValidationError, safe_request_validation_error)
    test_app.include_router(router, prefix="/api/v1")

    async def override_db():
        yield db_session

    async def override_auth():
        return auth_context

    async def override_workspace():
        return auth_context.workspace_id

    test_app.dependency_overrides[get_db] = override_db
    test_app.dependency_overrides[get_current_auth_context] = override_auth
    test_app.dependency_overrides[get_current_workspace_id] = override_workspace
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_snapshot_config_and_idempotent_command_http_contract(oss_native_client) -> None:
    calls = 0

    async def dispatch(context):
        nonlocal calls
        calls += 1
        return {"accepted": True, "commandId": str(context.command_id)}

    register_provider_dispatcher("chatwoot", dispatch)
    try:
        initial = await oss_native_client.get(
            "/api/v1/capabilities/domains/communications/inboxes-routing"
        )
        assert initial.status_code == 200, initial.text
        assert initial.json()["state"]["configured"] is False
        assert initial.json()["state"]["configVersion"] == 0

        configured = await oss_native_client.patch(
            "/api/v1/capabilities/domains/communications/inboxes-routing/config",
            json={
                "expectedVersion": 0,
                "configuration": {"layout": "dense"},
                "executionMode": "local_native",
            },
        )
        assert configured.status_code == 200, configured.text
        assert configured.json()["state"]["configVersion"] == 1

        request = {
            "commandType": "inbox.refresh",
            "idempotencyKey": "inbox-refresh-http-0001",
            "expectedVersion": 1,
            "payload": {"scope": "open"},
        }
        first = await oss_native_client.post(
            "/api/v1/capabilities/domains/communications/inboxes-routing/commands",
            json=request,
        )
        replay = await oss_native_client.post(
            "/api/v1/capabilities/domains/communications/inboxes-routing/commands",
            json=request,
        )
        assert first.status_code == 201, first.text
        assert first.json()["status"] == "succeeded"
        assert replay.status_code == 201, replay.text
        assert replay.json()["id"] == first.json()["id"]
        assert replay.json()["deduplicated"] is True
        assert calls == 1

        snapshot = await oss_native_client.get(
            "/api/v1/capabilities/domains/communications/inboxes-routing"
        )
        assert len(snapshot.json()["commands"]) == 1
        assert {event["action"] for event in snapshot.json()["auditTrail"]} >= {
            "config_created",
            "command_received",
            "command_started",
            "command_succeeded",
        }
        for response in (initial, configured, first, replay, snapshot):
            assert_public_response(response)
    finally:
        unregister_provider_dispatcher("chatwoot")


@pytest.mark.asyncio
async def test_config_endpoint_uses_native_contract_without_source_adapter(
    oss_native_client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter_class",
        lambda _self, _provider: (_ for _ in ()).throw(
            AssertionError("A configuração tentou chamar um adapter de origem.")
        ),
    )

    configured = await oss_native_client.patch(
        "/api/v1/capabilities/domains/communications/inboxes-routing/config",
        json={
            "expectedVersion": 0,
            "configuration": {"layout": "compact"},
            "executionMode": "local_native",
        },
    )
    snapshot = await oss_native_client.get(
        "/api/v1/capabilities/domains/communications/inboxes-routing"
    )

    assert configured.status_code == 200
    assert snapshot.status_code == 200
    assert snapshot.json()["state"]["configVersion"] == 1
    assert snapshot.json()["state"]["configuration"] == {"layout": "compact"}


@pytest.mark.asyncio
async def test_conflicting_http_replay_returns_409(oss_native_client) -> None:
    async def dispatch(_context):
        return {"ok": True}

    register_provider_dispatcher("bottlecrm", dispatch)
    try:
        configured = await oss_native_client.patch(
            "/api/v1/capabilities/domains/crm/accounts-contacts-leads/config",
            json={
                "expectedVersion": 0,
                "configuration": {"projection": "local"},
                "executionMode": "local_native",
            },
        )
        assert configured.status_code == 200, configured.text
        base = {
            "commandType": "card.qualify",
            "idempotencyKey": "card-qualify-http-0001",
            "expectedVersion": 1,
        }
        first = await oss_native_client.post(
            "/api/v1/capabilities/domains/crm/accounts-contacts-leads/commands",
            json={**base, "payload": {"score": 70}},
        )
        conflict = await oss_native_client.post(
            "/api/v1/capabilities/domains/crm/accounts-contacts-leads/commands",
            json={**base, "payload": {"score": 90}},
        )
        assert first.status_code == 201
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "KLM-NATIVE-002"
    finally:
        unregister_provider_dispatcher("bottlecrm")


@pytest.mark.asyncio
async def test_integrations_permissions_protect_read_and_manage(
    oss_native_client,
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    class ReadOnlyAdapter:
        async def snapshot(self, _workspace_id, _capability, _query):
            return {
                "records": [{"id": "inbox-1", "name": "Atendimento"}],
                "actions": [{"command": "create", "mutates": True}],
            }

    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: ReadOnlyAdapter(),
    )
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.role = WorkspaceRole.workspace_admin
    auth_context.role = WorkspaceRole.workspace_admin
    membership.permission_denials = [
        {"module": "integrations", "action": "manage", "scope": "workspace"}
    ]
    await db_session.flush()

    readable = await oss_native_client.get(
        "/api/v1/capabilities/domains/communications/inboxes-routing"
    )
    denied_patch = await oss_native_client.patch(
        "/api/v1/capabilities/domains/communications/inboxes-routing/config",
        json={"expectedVersion": 0, "configuration": {"layout": "dense"}},
    )
    denied_command = await oss_native_client.post(
        "/api/v1/capabilities/domains/communications/inboxes-routing/commands",
        json={
            "commandType": "inbox.refresh",
            "idempotencyKey": "permission-command-0001",
            "expectedVersion": 0,
            "payload": {},
        },
    )
    readable_document = await oss_native_client.get(
        "/api/v1/capabilities/domains/communications/inboxes-routing/document"
    )
    assert readable.status_code == 200
    assert readable_document.status_code == 200
    assert readable_document.json()["actions"][0]["permission"] == {
        "allowed": False,
        "reason": "Seu perfil não possui todas as permissões exigidas por esta ação.",
        "required": [
            {"module": "integrations", "action": "manage"},
            {"module": "mensagens", "action": "send_message"},
        ],
    }
    assert denied_patch.status_code == 403
    assert denied_command.status_code == 403

    membership.permission_denials = [
        {"module": "integrations", "action": "view", "scope": "workspace"},
        {"module": "integrations", "action": "manage", "scope": "workspace"},
    ]
    await db_session.flush()
    denied_read = await oss_native_client.get(
        "/api/v1/capabilities/domains/communications/inboxes-routing"
    )
    assert denied_read.status_code == 403


@pytest.mark.asyncio
async def test_native_permissions_combine_integration_and_business_domain(
    oss_native_client,
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    class DomainAdapter:
        async def snapshot(self, _workspace_id, _capability, _query):
            return {
                "records": [],
                "actions": [{"command": "create", "mutates": True}],
            }

        async def execute(self, *_args, **_kwargs):
            return {"status": "executed"}

    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: DomainAdapter(),
    )
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.role = WorkspaceRole.finance_manager
    auth_context.role = WorkspaceRole.finance_manager
    await db_session.flush()

    denied_crm_read = await oss_native_client.get(
        "/api/v1/capabilities/domains/communications/contacts/document"
    )
    messaging = await oss_native_client.get(
        "/api/v1/capabilities/domains/communications/inboxes-routing/document"
    )
    finance = await oss_native_client.get(
        "/api/v1/capabilities/domains/crm/invoices-recurrence/document"
    )
    denied_low_level_command = await oss_native_client.post(
        "/api/v1/capabilities/domains/communications/inboxes-routing/commands",
        json={
            "commandType": "create",
            "idempotencyKey": "finance-cross-domain-0001",
            "expectedVersion": 0,
            "payload": {},
        },
    )

    assert denied_crm_read.status_code == 403
    assert messaging.status_code == 200
    assert messaging.json()["actions"][0]["permission"]["allowed"] is False
    assert finance.status_code == 200
    assert finance.json()["actions"][0]["permission"]["allowed"] is True
    assert denied_low_level_command.status_code == 403


@pytest.mark.asyncio
async def test_http_rejects_secrets_in_config_and_command(oss_native_client) -> None:
    rejected_config = await oss_native_client.patch(
        "/api/v1/capabilities/domains/communications/api-webhooks-realtime/config",
        json={
            "expectedVersion": 0,
            "configuration": {"apiKey": "forbidden-value"},
        },
    )
    assert rejected_config.status_code == 422
    assert "forbidden credential" in rejected_config.text

    configured = await oss_native_client.patch(
        "/api/v1/capabilities/domains/communications/api-webhooks-realtime/config",
        json={"expectedVersion": 0, "configuration": {"enabled": True}},
    )
    assert configured.status_code == 200
    rejected_command = await oss_native_client.post(
        "/api/v1/capabilities/domains/communications/api-webhooks-realtime/commands",
        json={
            "commandType": "inbox.refresh",
            "idempotencyKey": "secret-command-0001",
            "expectedVersion": 1,
            "payload": {"authorization": "Bearer forbidden-value"},
        },
    )
    assert rejected_command.status_code == 422


@pytest.mark.asyncio
async def test_http_password_action_uses_non_persistent_transient_channel(
    oss_native_client,
    monkeypatch,
) -> None:
    validated_credentials: list[dict[str, str]] = []

    class PasswordAdapter:
        async def snapshot(self, _workspace_id, _capability, _query):
            return {
                "records": [],
                "actions": [
                    {
                        "command": "disable_mfa",
                        "mutates": True,
                        "externalDispatch": False,
                        "required": ["password", "otp_code"],
                    }
                ],
            }

        async def validate(
            self,
            _workspace_id,
            _actor_user_id,
            _capability,
            _command,
            payload,
            *,
            mode,
        ):
            validated_credentials.append(
                {"password": payload["password"], "otp_code": payload["otp_code"]}
            )
            return {
                "status": "validated",
                "mode": mode,
                "externalNetworkExecuted": False,
            }

    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: PasswordAdapter(),
    )
    document = await oss_native_client.get(
        "/api/v1/capabilities/domains/communications/profile-mfa/document"
    )
    version = document.json()["version"]
    valid_secret = "Transient-HTTP-Only-42"
    valid_otp = "731904"
    accepted = await oss_native_client.post(
        "/api/v1/capabilities/domains/communications/profile-mfa/actions",
        json={
            "actionId": "disable_mfa",
            "values": {},
            "transientSecrets": {"password": valid_secret, "otp_code": valid_otp},
            "mode": "dry-run",
            "version": version,
            "idempotencyKey": "http-transient-password-0001",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert validated_credentials == [{"password": valid_secret, "otp_code": valid_otp}]
    assert valid_secret not in accepted.text
    assert valid_otp not in accepted.text

    rejected_durable_otp = await oss_native_client.post(
        "/api/v1/capabilities/domains/communications/profile-mfa/actions",
        json={
            "actionId": "disable_mfa",
            "values": {"otp_code": valid_otp},
            "transientSecrets": {"password": valid_secret},
            "mode": "dry-run",
            "version": version,
            "idempotencyKey": "http-durable-otp-rejected-0001",
        },
    )
    assert rejected_durable_otp.status_code == 422
    assert valid_otp not in rejected_durable_otp.text

    oversized_secret = "S" * 1025
    rejected = await oss_native_client.post(
        "/api/v1/capabilities/domains/communications/profile-mfa/actions",
        json={
            "actionId": "disable_mfa",
            "values": {},
            "transientSecrets": {"password": oversized_secret, "otp_code": valid_otp},
            "mode": "dry-run",
            "version": version,
            "idempotencyKey": "http-transient-password-oversized-0001",
        },
    )
    assert rejected.status_code == 422
    assert oversized_secret not in rejected.text


@pytest.mark.asyncio
async def test_external_equivalent_http_command_is_persisted_and_never_dispatched(
    oss_native_client,
) -> None:
    calls = 0

    async def forbidden_dispatch(_context):
        nonlocal calls
        calls += 1
        return {"unexpected": True}

    register_provider_dispatcher("rocketchat", forbidden_dispatch)
    try:
        configured = await oss_native_client.patch(
            "/api/v1/capabilities/domains/collaboration/enterprise-controls/config",
            json={
                "expectedVersion": 0,
                "configuration": {"retentionDays": 30},
                "executionMode": "external_equivalent",
            },
        )
        assert configured.status_code == 200, configured.text
        command = await oss_native_client.post(
            "/api/v1/capabilities/domains/collaboration/enterprise-controls/commands",
            json={
                "commandType": "retention.apply",
                "idempotencyKey": "retention-http-0001",
                "expectedVersion": 1,
                "payload": {"scope": "workspace"},
            },
        )
        assert command.status_code == 201, command.text
        assert command.json()["status"] == "blocked"
        assert command.json()["result"]["persistedLocally"] is True
        assert "dispatcherInvoked" not in command.json()
        assert command.json()["externalNetworkAllowed"] is False
        assert_public_response(command)
        assert calls == 0
    finally:
        unregister_provider_dispatcher("rocketchat")


@pytest.mark.asyncio
async def test_native_document_executes_once_and_replays_without_duplicate(
    oss_native_client,
    monkeypatch,
) -> None:
    calls = 0
    snapshots = 0

    class FakeAdapter:
        async def snapshot(self, _workspace_id, _capability, _query):
            nonlocal snapshots
            snapshots += 1
            return {
                "records": [{"id": "inbox-1", "name": "Saúde", "status": "ready"}],
                "actions": [{"command": "create", "mutates": True}],
                "health": {"status": "healthy"},
            }

        async def validate(self, *_args, **_kwargs):
            return {"status": "validated", "externalNetworkExecuted": False}

        async def execute(
            self,
            _workspace_id,
            _actor_user_id,
            _capability,
            command,
            payload,
            **_kwargs,
        ):
            nonlocal calls
            calls += 1
            return {
                "command": command,
                "created": payload.get("name"),
                "website_token": "must-not-be-persisted",
            }

    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: FakeAdapter(),
    )
    document = await oss_native_client.get(
        "/api/v1/capabilities/domains/communications/inboxes-routing/document"
    )
    assert document.status_code == 200, document.text
    body = document.json()
    assert body["capability"]["id"] == "inboxes-routing"
    assert body["tabs"][0]["sections"][0]["type"] == "table"
    assert body["actions"][0]["id"] == "create"
    assert body["navigation"]["url"] == "/app/capacidades/communications/inboxes-routing"
    assert "motor" not in body
    assert "native" not in body
    assert_public_response(document)

    request = {
        "actionId": "create",
        "values": {"payload": {"name": "Fila prioritária"}},
        "mode": "execute",
        "version": body["version"],
        "idempotencyKey": "native-document-create-0001",
        "context": {"thread_id": "22222222-2222-4222-8222-222222222222"},
    }
    first = await oss_native_client.post(
        "/api/v1/capabilities/domains/communications/inboxes-routing/actions",
        json=request,
    )
    replay = await oss_native_client.post(
        "/api/v1/capabilities/domains/communications/inboxes-routing/actions",
        json=request,
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "succeeded"
    assert first.json()["result"]["created"] == "Fila prioritária"
    assert "website_token" not in first.json()["result"]
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["document"] is None
    assert replay.json()["refreshPending"] is True
    assert calls == 1
    assert snapshots == 3
    assert_public_response(first)
    assert_public_response(replay)

    divergent = await oss_native_client.post(
        "/api/v1/capabilities/domains/communications/inboxes-routing/actions",
        json={
            **request,
            "values": {"payload": {"name": "Outra fila"}},
        },
    )
    assert divergent.status_code == 409
    assert divergent.json()["detail"]["code"] == "KLM-NATIVE-002"
    assert snapshots == 3


@pytest.mark.asyncio
async def test_native_document_gate_only_allows_safe_dry_run(
    oss_native_client,
    monkeypatch,
) -> None:
    calls = 0

    class FakeAdapter:
        async def snapshot(self, _workspace_id, _capability, _query):
            return {
                "content": {"status": "ready", "items": []},
                "actions": [{"command": "dispatch", "externalDispatch": True}],
            }

        async def validate(self, *_args, **_kwargs):
            return {"status": "validated", "externalNetworkExecuted": False}

        async def execute(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return {"unexpected": True}

    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: FakeAdapter(),
    )
    document = await oss_native_client.get(
        "/api/v1/capabilities/domains/communications/external-channels/document"
    )
    assert document.status_code == 200, document.text
    assert document.json()["health"]["state"] == "gated"
    assert document.json()["actions"][0]["gate"]["state"] == "gated"

    base = {
        "actionId": "dispatch",
        "values": {"payload": {"audience": "preview-only"}},
        "version": document.json()["version"],
        "context": {},
    }
    blocked = await oss_native_client.post(
        "/api/v1/capabilities/domains/communications/external-channels/actions",
        json={**base, "mode": "execute", "idempotencyKey": "external-gate-0001"},
    )
    dry_run = await oss_native_client.post(
        "/api/v1/capabilities/domains/communications/external-channels/actions",
        json={**base, "mode": "dry-run", "idempotencyKey": "external-gate-0002"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "KLM-NATIVE-003"
    assert dry_run.status_code == 200, dry_run.text
    assert dry_run.json()["result"]["externalNetworkExecuted"] is False
    assert calls == 0


@pytest.mark.asyncio
async def test_native_document_adapter_failure_is_not_reported_as_http_success(
    oss_native_client,
    monkeypatch,
) -> None:
    class FailingAdapter:
        async def snapshot(self, _workspace_id, _capability, _query):
            return {
                "records": [],
                "actions": [{"command": "create", "mutates": True}],
            }

        async def validate(self, *_args, **_kwargs):
            return {"status": "validated", "externalNetworkExecuted": False}

        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("private sidecar failure")

    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: FailingAdapter(),
    )
    document = await oss_native_client.get(
        "/api/v1/capabilities/domains/communications/inboxes-routing/document"
    )
    response = await oss_native_client.post(
        "/api/v1/capabilities/domains/communications/inboxes-routing/actions",
        json={
            "actionId": "create",
            "values": {"payload": {"name": "Fila"}},
            "mode": "execute",
            "version": document.json()["version"],
            "idempotencyKey": "native-document-failure-0001",
            "context": {},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "KLM-NATIVE-004"
    assert response.json()["detail"]["commandId"]
    assert "private sidecar failure" not in response.text


@pytest.mark.asyncio
async def test_confirmed_action_survives_refresh_failure_without_duplicate(
    oss_native_client,
    monkeypatch,
) -> None:
    snapshots = 0
    executions = 0

    class RefreshFailingAdapter:
        async def snapshot(self, _workspace_id, _capability, _query):
            nonlocal snapshots
            snapshots += 1
            if snapshots == 3:
                raise RuntimeError("readback unavailable")
            return {
                "records": [{"id": "inbox-1", "name": "Saúde"}],
                "actions": [{"command": "create", "mutates": True}],
            }

        async def validate(self, *_args, **_kwargs):
            return {"status": "validated", "externalNetworkExecuted": False}

        async def execute(self, *_args, **_kwargs):
            nonlocal executions
            executions += 1
            return {"created": True}

    adapter = RefreshFailingAdapter()
    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: adapter,
    )
    document = await oss_native_client.get(
        "/api/v1/capabilities/domains/communications/inboxes-routing/document"
    )
    request = {
        "actionId": "create",
        "values": {"payload": {"name": "Fila"}},
        "mode": "execute",
        "version": document.json()["version"],
        "idempotencyKey": "native-refresh-failure-0001",
        "context": {},
    }

    first = await oss_native_client.post(
        "/api/v1/capabilities/domains/communications/inboxes-routing/actions",
        json=request,
    )
    replay = await oss_native_client.post(
        "/api/v1/capabilities/domains/communications/inboxes-routing/actions",
        json=request,
    )

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "succeeded"
    assert first.json()["refreshPending"] is True
    assert first.json()["document"] is None
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert executions == 1
    assert snapshots == 3
    assert_public_response(first)
    assert_public_response(replay)


@pytest.mark.asyncio
async def test_native_document_rejects_unknown_capability_and_secret_query(
    oss_native_client,
) -> None:
    missing = await oss_native_client.get(
        "/api/v1/capabilities/domains/communications/not-in-registry/document"
    )
    secret = await oss_native_client.get(
        "/api/v1/capabilities/domains/communications/inboxes-routing/document?apiKey=forbidden"
    )
    assert missing.status_code == 404
    assert secret.status_code == 422
    assert "forbidden credential" in secret.text


@pytest.mark.asyncio
async def test_capability_registry_is_native_product_vocabulary_only(client) -> None:
    response = await client.get("/api/v1/capabilities")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["scope"] == "klm-native-capabilities"
    assert set(payload["summary"]["byDomain"]) == {
        "communications",
        "collaboration",
        "automation",
        "crm",
    }
    assert all("provider" not in item for item in payload["capabilities"])
    assert_public_response(response)
