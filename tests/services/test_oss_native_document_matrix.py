"""Exhaustive facade contract for all 135 native OSS capability routes."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models.oss_native import (
    OSSNativeAuditAction,
    OSSNativeAuditEvent,
    OSSNativeCommand,
    OSSNativeCommandStatus,
    OSSNativeExecutionMode,
)
from app.schemas.oss_native import OSSNativeDocumentActionWrite
from app.services.oss_capability_registry import oss_capability_registry
from app.services.oss_native_document_service import (
    OSSNativeDocumentService,
    OSSNativeExecutionError,
    _actions,
    _document_tabs,
    _field_for,
    _fields_for_required,
    _format_for_key,
    _health,
    _humanize,
    _local_only_result,
    _reject_transient_secret_echo,
    _validate_action_values,
    _without_redacted_fields,
    native_capability,
    required_capability_manage_permissions,
    required_capability_read_permissions,
)
from app.services.oss_native_state_service import OSSNativeStateService
from scripts.verify_oss_native_contracts import (
    _action_payload as verifier_action_payload,
)
from scripts.verify_oss_native_contracts import _external_network_marker


def test_native_labels_are_localized_without_changing_technical_action_ids() -> None:
    assert _humanize("records") == "Registros"
    assert _humanize("sections") == "Seções"
    assert _humanize("threadId") == "ID da conversa"
    assert _humanize("update_routing") == "Atualizar roteamento"
    assert _humanize("sync_workspace") == "Sincronizar ambiente"
    assert _humanize("upsert_integration_config") == "Salvar configuração da integração"
    assert _humanize("dry-run") == "Simular"
    assert _humanize("avatar_url") == "Foto"
    assert _humanize("channel_id") == "ID do canal"
    assert _humanize("greeting_enabled") == "Saudação ativa"
    assert _humanize("remoteResources") == "Recursos no motor"
    assert _humanize("create_custom_view") == "Criar visão personalizada"
    assert _humanize("add_inbox_members") == "Adicionar membros da caixa de entrada"
    assert _humanize("set_order_status") == "Definir status do pedido"
    assert _humanize("display_name") == "Nome de exibição"
    assert _humanize("total_sent_messages_count") == "Pesquisas enviadas"
    assert _humanize("username") == "Nome de usuário"


def test_contract_verifier_supplies_valid_local_staging_rows_for_bulk_import() -> None:
    values, transient = verifier_action_payload(
        {
            "id": "commit_import",
            "fields": [
                {
                    "name": "payload",
                    "type": "json",
                    "required": False,
                }
            ],
        }
    )

    assert values == {
        "payload": {"rows": [{"external_id": "qa-local-1", "name": "QA local"}]}
    }
    assert transient == {}


def test_contract_verifier_requires_explicit_external_network_marker() -> None:
    assert _external_network_marker({"externalNetworkExecuted": False}) is False
    assert _external_network_marker({"externalNetworkExecuted": True}) is True
    with pytest.raises(ValueError, match="deve ser booleano e explícito"):
        _external_network_marker({})
    with pytest.raises(ValueError, match="evidência de rede estruturada"):
        _external_network_marker(None)


def test_native_action_expands_alternative_required_fields() -> None:
    fields = _fields_for_required(["username|roomId", "text"])

    assert [field["name"] for field in fields] == ["username", "roomId", "text"]
    assert fields[0]["oneOfGroup"] == "username|roomId"
    assert fields[1]["oneOfGroup"] == "username|roomId"
    assert fields[0]["required"] is False
    assert fields[2]["required"] is True


def test_native_action_field_types_do_not_confuse_ids_with_long_content() -> None:
    assert _field_for("messageId")["type"] == "text"
    assert _field_for("threadMessageId")["type"] == "text"
    assert _field_for("contentType")["type"] == "text"
    assert _field_for("text")["type"] == "textarea"
    assert _field_for("confirmed")["type"] == "switch"
    assert _field_for("confirmLocalOnly")["type"] == "switch"
    assert _field_for("redirectUri")["type"] == "url"
    assert _field_for("email")["type"] == "email"
    assert _field_for("password")["type"] == "password"
    assert _field_for("otp_code")["type"] == "password"
    assert _field_for("backupCode")["type"] == "password"
    assert _field_for("user_ids")["type"] == "json"


def test_transient_secret_echo_is_rejected_inside_strings_and_mapping_keys() -> None:
    secret = "Transient-Only-42"
    with pytest.raises(ValueError, match="credencial transitória"):
        _reject_transient_secret_echo({"detail": f"adapter rejected {secret}"}, {secret})
    with pytest.raises(ValueError, match="credencial transitória"):
        _reject_transient_secret_echo({f"unsafe-{secret}": "value"}, {secret})


def test_native_action_schema_rejects_missing_one_of_and_invalid_types() -> None:
    action = {
        "fields": [
            *_fields_for_required(["username|roomId", "confirmed", "capacity"]),
        ]
    }

    with pytest.raises(ValueError, match="ao menos um"):
        _validate_action_values(action, {"confirmed": True, "capacity": 4})
    with pytest.raises(ValueError, match="verdadeiro ou falso"):
        _validate_action_values(
            action,
            {"username": "advogada", "confirmed": "yes", "capacity": 4},
        )
    with pytest.raises(ValueError, match="número inteiro"):
        _validate_action_values(
            action,
            {"roomId": "sala-1", "confirmed": False, "capacity": 4.5},
        )

    _validate_action_values(
        action,
        {"roomId": "sala-1", "confirmed": False, "capacity": 4},
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"externalDispatchExecuted": True},
        {"externalEffects": 2},
        {"remoteWritePerformed": "yes"},
        {"nested": {"networkRequestSent": 1}},
        {"external": {"effects": 2}},
        {"external": {"dispatchExecuted": True}},
        {"events": ({"external": {"effects": 1}},)},
    ],
)
def test_local_only_result_rejects_known_and_unknown_external_effect_markers(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(RuntimeError, match="forbidden external effect"):
        _local_only_result(payload, source="test.adapter")


def test_native_document_omits_redacted_credentials_from_visible_data() -> None:
    visible = _without_redacted_fields(
        {
            "name": "Perfil local",
            "pubsubToken": "[REDACTED]",
            "rows": [{"id": "1", "token": "[REDACTED]", "status": "ready"}],
        }
    )

    assert visible == {"name": "Perfil local", "rows": [{"id": "1", "status": "ready"}]}


def test_action_write_accepts_passwords_only_through_the_transient_channel() -> None:
    for field_name in ("password", "otp_code", "backup_code"):
        with pytest.raises(ValidationError):
            OSSNativeDocumentActionWrite(
                action_id="disable_mfa",
                values={field_name: "Never-Persist-42"},
                version=0,
                idempotency_key=f"schema-{field_name}-rejected-0001",
            )

    request = OSSNativeDocumentActionWrite(
        action_id="disable_mfa",
        transient_secrets={"password": "Never-Persist-42"},
        version=0,
        idempotency_key="schema-password-transient-0001",
    )

    assert request.transient_secrets["password"].get_secret_value() == "Never-Persist-42"
    assert "Never-Persist-42" not in request.model_dump_json(by_alias=True)

    oversized_secret = "S" * 1025
    oversized = OSSNativeDocumentActionWrite(
        action_id="disable_mfa",
        transient_secrets={"password": oversized_secret},
        version=0,
        idempotency_key="schema-password-oversized-0001",
    )
    assert oversized_secret not in oversized.model_dump_json(by_alias=True)


def test_native_field_formats_use_tokens_instead_of_substring_collisions() -> None:
    assert _format_for_key("integration_connections") is None
    assert _format_for_key("conversion_rate") == "percent"
    assert _format_for_key("created_at") == "datetime"
    assert _format_for_key("format") is None


def test_native_document_separates_business_data_from_engine_diagnostics() -> None:
    tabs = _document_tabs(
        {
            "view": {
                "sections": [{"key": "calendar", "label": "Calendar"}],
                "summary": {"nativeRecords": 0},
            },
            "canonical": {"counts": {"integration_connections": 0}},
            "nativeState": {"items": []},
            "upstream": {"available": True, "resources": {"api_settings": []}},
            "collection": "external_integrations",
        }
    )

    assert [tab["label"] for tab in tabs] == ["Visão geral", "Diagnóstico do motor"]
    overview_titles = [section["title"] for section in tabs[0]["sections"]]
    diagnostic_titles = [section["title"] for section in tabs[1]["sections"]]
    assert "Recursos disponíveis" in overview_titles
    assert "Dados consolidados no KLM" in overview_titles
    assert "Motor de origem" not in overview_titles
    assert diagnostic_titles == ["Motor de origem", "Coleção técnica"]


def test_all_135_capabilities_require_integration_and_domain_permissions() -> None:
    for capability in oss_capability_registry()["capabilities"]:
        read = required_capability_read_permissions(
            capability["provider"], capability["key"]
        )
        manage = required_capability_manage_permissions(
            capability["provider"], capability["key"]
        )
        assert ("integrations", "view") in read, capability["id"]
        assert ("integrations", "manage") in manage, capability["id"]
        assert read, capability["id"]
        assert manage, capability["id"]


def test_chattermate_agents_rbac_sync_requires_manage_even_if_adapter_marks_read_only() -> None:
    capability = native_capability("chattermate", "agents-rbac")
    raw = {"actions": [{"command": "sync", "mutates": False}]}

    view_only = required_capability_read_permissions("chattermate", "agents-rbac")
    denied = _actions(raw, capability, permission_keys=view_only)[0]
    manage = required_capability_manage_permissions("chattermate", "agents-rbac")
    allowed = _actions(raw, capability, permission_keys=manage)[0]

    assert denied["permission"]["allowed"] is False
    assert denied["permission"]["required"] == [
        {"module": "agents", "action": "manage"},
        {"module": "integrations", "action": "manage"},
    ]
    assert allowed["permission"]["allowed"] is True


@pytest.mark.parametrize(
    "raw",
    [
        {"health": {"status": "healthy", "children": {"worker": {"status": "unavailable"}}}},
        {"health": {"status": "healthy"}, "errors": [{"code": "sidecar_down"}]},
        {},
    ],
)
def test_native_document_health_fails_closed_for_errors_unavailable_children_or_no_signal(
    raw: dict[str, Any],
) -> None:
    capability = native_capability("chattermate", "agents-rbac")

    health = _health(raw, capability)

    assert health["state"] == "degraded"


class MatrixAdapter:
    def __init__(self, capabilities: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.capabilities = capabilities
        self.executions: list[tuple[str, str]] = []
        self.validations: list[tuple[str, str, str]] = []

    async def snapshot(self, _workspace_id, capability_key, query):
        provider = str(query.get("matrix_provider"))
        capability = self.capabilities[(provider, capability_key)]
        gated = capability["nativeMode"] == "gated_configuration"
        return {
            "records": [
                {
                    "id": f"{provider}:{capability_key}",
                    "name": capability["label"],
                    "status": "ready",
                }
            ],
            "actions": [
                {
                    "command": "dispatch" if gated else "refresh",
                    "externalDispatch": gated,
                    "mutates": gated,
                }
            ],
            "health": {"status": "healthy"},
        }

    async def execute(
        self,
        _workspace_id,
        _actor_user_id,
        capability_key,
        command,
        _payload,
        **_kwargs,
    ):
        self.executions.append((capability_key, command))
        return {"status": "executed", "capability": capability_key, "command": command}

    async def validate(
        self,
        _workspace_id,
        _actor_user_id,
        capability_key,
        command,
        _payload,
        *,
        mode,
    ):
        self.validations.append((capability_key, command, mode))
        return {
            "status": "validated",
            "capability": capability_key,
            "command": command,
            "externalNetworkExecuted": False,
        }


@pytest.mark.asyncio
async def test_all_135_capabilities_render_and_accept_their_safe_native_action(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    capabilities = {
        (row["provider"], row["key"]): row for row in oss_capability_registry()["capabilities"]
    }
    adapter = MatrixAdapter(capabilities)
    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: adapter,
    )
    service = OSSNativeDocumentService(db_session)
    executed = 0
    dry_runs = 0

    for index, ((provider, capability_key), capability) in enumerate(capabilities.items()):
        context = {"matrix_provider": provider, "matter_id": f"matter-{index}"}
        document = await service.document(
            auth_context.workspace_id,
            provider,
            capability_key,
            context,
        )
        assert document["provider"]["id"] == {
            "chatwoot": "communications",
            "rocketchat": "collaboration",
            "chattermate": "automation",
            "bottlecrm": "crm",
        }[provider]
        assert document["capability"]["id"] == capability_key
        assert document["capability"]["label"]
        assert all(
            brand not in document["capability"]["label"].casefold()
            for brand in ("chatwoot", "rocket.chat", "chattermate", "bottlecrm")
        )
        assert document["native"]["mode"] == capability["nativeMode"]
        assert document["tabs"][0]["sections"]
        assert document["actions"]
        assert "iframe" not in str(document).lower()

        gated = capability["nativeMode"] == "gated_configuration"
        mode = "dry-run" if gated else "execute"
        action = document["actions"][0]
        response = await service.execute(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            provider=provider,
            capability_key=capability_key,
            request=OSSNativeDocumentActionWrite(
                action_id=action["id"],
                values={},
                mode=mode,
                version=document["version"],
                idempotency_key=f"matrix-{provider}-{capability_key}-0001",
                context=context,
            ),
        )
        assert response["status"] == "succeeded"
        assert response["document"]["capability"]["id"] == capability_key
        assert response["result"]["externalNetworkExecuted"] is False
        if gated:
            dry_runs += 1
            assert response["result"]["status"] == "dry_run"
        else:
            executed += 1
            assert response["result"]["status"] == "executed"

    assert executed + dry_runs == 135
    assert dry_runs == sum(
        row["nativeMode"] == "gated_configuration" for row in capabilities.values()
    )
    assert len(adapter.executions) == executed
    assert len(adapter.validations) == 135


class ConfigurationAdapter:
    def __init__(
        self,
        *,
        reject: bool = False,
        report_external: bool = False,
        external_dispatch: bool = True,
    ) -> None:
        self.reject = reject
        self.report_external = report_external
        self.external_dispatch = external_dispatch
        self.validations = 0
        self.executions = 0

    async def snapshot(self, _workspace_id, _capability_key, _query):
        return {
            "records": [],
            "actions": [
                {
                    "command": "upsert_integration_config",
                    "externalDispatch": self.external_dispatch,
                    "mutates": True,
                    "required": ["provider"],
                }
            ],
        }

    async def validate(
        self,
        _workspace_id,
        _actor_user_id,
        _capability_key,
        _command,
        payload,
        *,
        mode,
    ):
        self.validations += 1
        if self.reject or payload.get("provider") == "invalid":
            raise ValueError("Provedor não suportado pelo contrato local.")
        return {
            "status": "validated",
            "mode": mode,
            "externalNetworkExecuted": False,
        }

    async def execute(self, *_args, **_kwargs):
        self.executions += 1
        return {
            "status": "completed",
            "externalNetworkExecuted": self.report_external,
        }


@pytest.mark.asyncio
async def test_invalid_configuration_is_not_persisted_before_adapter_validation(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    adapter = ConfigurationAdapter(reject=True)
    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: adapter,
    )
    service = OSSNativeDocumentService(db_session)
    document = await service.document(
        auth_context.workspace_id,
        "bottlecrm",
        "external-integrations",
    )

    with pytest.raises(ValueError, match="não suportado"):
        await service.execute(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            provider="bottlecrm",
            capability_key="external-integrations",
            request=OSSNativeDocumentActionWrite(
                action_id="upsert_integration_config",
                values={"provider": "invalid"},
                mode="configure",
                version=document["version"],
                idempotency_key="invalid-configuration-0001",
            ),
        )

    snapshot = await OSSNativeStateService(db_session).get_snapshot(
        auth_context.workspace_id,
        "bottlecrm",
        "external-integrations",
    )
    commands = list(
        (
            await db_session.scalars(
                select(OSSNativeCommand).where(
                    OSSNativeCommand.workspace_id == auth_context.workspace_id,
                    OSSNativeCommand.provider == "bottlecrm",
                    OSSNativeCommand.capability == "external-integrations",
                )
            )
        ).all()
    )
    assert snapshot.state.config_version == 0
    assert snapshot.state.configuration == {}
    assert commands == []
    assert adapter.validations == 1
    assert adapter.executions == 0


@pytest.mark.asyncio
async def test_invalid_execute_does_not_change_execution_mode_before_validation(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    adapter = ConfigurationAdapter(reject=True, external_dispatch=False)
    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: adapter,
    )
    service = OSSNativeDocumentService(db_session)
    document = await service.document(
        auth_context.workspace_id,
        "bottlecrm",
        "accounts-contacts-leads",
    )

    with pytest.raises(ValueError, match="não suportado"):
        await service.execute(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            provider="bottlecrm",
            capability_key="accounts-contacts-leads",
            request=OSSNativeDocumentActionWrite(
                action_id="upsert_integration_config",
                values={"provider": "invalid"},
                mode="execute",
                version=document["version"],
                idempotency_key="invalid-execute-0001",
            ),
        )

    snapshot = await OSSNativeStateService(db_session).get_snapshot(
        auth_context.workspace_id,
        "bottlecrm",
        "accounts-contacts-leads",
    )
    commands = list(
        (
            await db_session.scalars(
                select(OSSNativeCommand).where(
                    OSSNativeCommand.workspace_id == auth_context.workspace_id,
                    OSSNativeCommand.provider == "bottlecrm",
                    OSSNativeCommand.capability == "accounts-contacts-leads",
                )
            )
        ).all()
    )
    assert snapshot.state.config_version == 0
    assert snapshot.state.execution_mode == OSSNativeExecutionMode.disabled
    assert commands == []
    assert adapter.validations == 1
    assert adapter.executions == 0


@pytest.mark.asyncio
async def test_configure_and_dry_run_use_real_validation_without_adapter_effect(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    adapter = ConfigurationAdapter()
    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: adapter,
    )
    service = OSSNativeDocumentService(db_session)
    first_document = await service.document(
        auth_context.workspace_id,
        "bottlecrm",
        "external-integrations",
    )
    configured = await service.execute(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="bottlecrm",
        capability_key="external-integrations",
        request=OSSNativeDocumentActionWrite(
            action_id="upsert_integration_config",
            values={"provider": "calendar-local"},
            mode="configure",
            version=first_document["version"],
            idempotency_key="valid-configuration-0001",
        ),
    )
    second_document = configured["document"]
    dry_run = await service.execute(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="bottlecrm",
        capability_key="external-integrations",
        request=OSSNativeDocumentActionWrite(
            action_id="upsert_integration_config",
            values={"provider": "calendar-local"},
            mode="dry-run",
            version=second_document["version"],
            idempotency_key="valid-dry-run-0001",
        ),
    )

    snapshot = await OSSNativeStateService(db_session).get_snapshot(
        auth_context.workspace_id,
        "bottlecrm",
        "external-integrations",
    )
    assert configured["result"]["persistedLocally"] is True
    assert dry_run["result"]["validated"] is True
    assert snapshot.state.configuration["action:upsert_integration_config"] == {
        "provider": "calendar-local"
    }
    assert adapter.validations == 2
    assert adapter.executions == 0


@pytest.mark.asyncio
async def test_configure_replay_repairs_persistence_without_redispatch(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    adapter = ConfigurationAdapter()
    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: adapter,
    )
    service = OSSNativeDocumentService(db_session)
    document = await service.document(
        auth_context.workspace_id,
        "bottlecrm",
        "external-integrations",
    )
    request = OSSNativeDocumentActionWrite(
        action_id="upsert_integration_config",
        values={"provider": "calendar-local"},
        mode="configure",
        version=document["version"],
        idempotency_key="repair-configuration-0001",
    )
    persist = service._persist_configured_action

    async def simulate_crash(**_kwargs):
        raise RuntimeError("simulated crash after terminal command")

    monkeypatch.setattr(service, "_persist_configured_action", simulate_crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await service.execute(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            provider="bottlecrm",
            capability_key="external-integrations",
            request=request,
        )

    command = await db_session.scalar(
        select(OSSNativeCommand).where(
            OSSNativeCommand.workspace_id == auth_context.workspace_id,
            OSSNativeCommand.idempotency_key == request.idempotency_key,
        )
    )
    assert command is not None
    assert command.status == OSSNativeCommandStatus.succeeded
    assert command.command_result["persistedLocally"] is False

    monkeypatch.setattr(service, "_persist_configured_action", persist)
    repaired = await service.execute(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="bottlecrm",
        capability_key="external-integrations",
        request=request,
    )
    snapshot = await OSSNativeStateService(db_session).get_snapshot(
        auth_context.workspace_id,
        "bottlecrm",
        "external-integrations",
    )
    repaired_version = snapshot.state.config_version
    third = await service.execute(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="bottlecrm",
        capability_key="external-integrations",
        request=request,
    )
    final_snapshot = await OSSNativeStateService(db_session).get_snapshot(
        auth_context.workspace_id,
        "bottlecrm",
        "external-integrations",
    )

    assert repaired["idempotentReplay"] is True
    assert repaired["result"]["persistedLocally"] is True
    assert snapshot.state.configuration["action:upsert_integration_config"] == {
        "provider": "calendar-local"
    }
    assert third["result"]["persistedLocally"] is True
    assert final_snapshot.state.config_version == repaired_version
    assert adapter.validations == 1
    assert adapter.executions == 0


@pytest.mark.asyncio
async def test_positive_external_effect_marker_fails_closed_instead_of_being_rewritten(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    adapter = ConfigurationAdapter(report_external=True, external_dispatch=False)
    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: adapter,
    )
    service = OSSNativeDocumentService(db_session)
    document = await service.document(
        auth_context.workspace_id,
        "bottlecrm",
        "accounts-contacts-leads",
    )

    with pytest.raises(OSSNativeExecutionError):
        await service.execute(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            provider="bottlecrm",
            capability_key="accounts-contacts-leads",
            request=OSSNativeDocumentActionWrite(
                action_id="upsert_integration_config",
                values={"provider": "local"},
                mode="execute",
                version=document["version"],
                idempotency_key="external-marker-violation-0001",
            ),
        )

    command = await db_session.scalar(
        select(OSSNativeCommand).where(
            OSSNativeCommand.workspace_id == auth_context.workspace_id,
            OSSNativeCommand.idempotency_key == "external-marker-violation-0001",
        )
    )
    assert command is not None
    assert command.status == OSSNativeCommandStatus.reconciliation_required
    assert command.command_result == {
        "adapterEffectReport": {
            "markerPath": "externalNetworkExecuted",
            "state": "positive",
        }
    }
    assert command.error_code == "external_effect_reported"


class CredentialActionAdapter:
    def __init__(self) -> None:
        self.validated_payloads: list[dict[str, Any]] = []

    async def snapshot(self, _workspace_id, capability_key, _query):
        if capability_key == "profile-mfa":
            return {
                "records": [],
                "actions": [
                    {
                        "command": "disable_mfa",
                        "externalDispatch": False,
                        "mutates": True,
                        "required": ["password"],
                    }
                ],
            }
        return {
            "records": [],
            "actions": [
                {
                    "command": "create-user",
                    "externalDispatch": False,
                    "mutates": True,
                    "required": ["email", "name", "username", "password"],
                }
            ],
        }

    async def validate(
        self,
        _workspace_id,
        _actor_user_id,
        _capability_key,
        _command,
        payload,
        *,
        mode,
    ):
        self.validated_payloads.append(dict(payload))
        return {
            "status": "validated",
            "mode": mode,
            "externalNetworkExecuted": False,
        }

    async def execute(self, *_args, **_kwargs):
        raise AssertionError("A prova usa dry-run e não pode executar o adaptador.")


class SecretEchoAndEffectAdapter(CredentialActionAdapter):
    async def validate(self, *_args, **_kwargs):
        payload = _args[4]
        secret = payload["password"]
        return {
            "external": {"dispatchExecuted": True},
            "detail": f"adapter rejected {secret}",
        }


@pytest.mark.asyncio
async def test_password_actions_receive_transient_secret_without_persisting_it(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    adapter = CredentialActionAdapter()
    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: adapter,
    )
    service = OSSNativeDocumentService(db_session)
    scenarios = [
        (
            "chatwoot",
            "profile-mfa",
            "disable_mfa",
            {},
            "Transient-Chatwoot-42",
            "transient-chatwoot-password-0001",
        ),
        (
            "rocketchat",
            "admin-users",
            "create-user",
            {
                "email": "qa@klm.local",
                "name": "QA KLM",
                "username": "qa-klm",
            },
            "Transient-Rocket-42",
            "transient-rocketchat-password-0001",
        ),
    ]

    for provider, capability, action_id, values, secret, idempotency_key in scenarios:
        document = await service.document(
            auth_context.workspace_id,
            provider,
            capability,
        )
        response = await service.execute(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            provider=provider,
            capability_key=capability,
            request=OSSNativeDocumentActionWrite(
                action_id=action_id,
                values=values,
                transient_secrets={"password": secret},
                mode="dry-run",
                version=document["version"],
                idempotency_key=idempotency_key,
            ),
        )
        assert response["status"] == "succeeded"
        assert secret not in json.dumps(response, default=str)

    assert [payload["password"] for payload in adapter.validated_payloads] == [
        "Transient-Chatwoot-42",
        "Transient-Rocket-42",
    ]
    commands = list(
        (
            await db_session.scalars(
                select(OSSNativeCommand).where(
                    OSSNativeCommand.workspace_id == auth_context.workspace_id,
                    OSSNativeCommand.idempotency_key.in_(
                        [scenario[-1] for scenario in scenarios]
                    ),
                )
            )
        ).all()
    )
    audits = list(
        (
            await db_session.scalars(
                select(OSSNativeAuditEvent).where(
                    OSSNativeAuditEvent.command_id.in_([command.id for command in commands])
                )
            )
        ).all()
    )
    durable = json.dumps(
        {
            "commands": [
                {
                    "payload": command.command_payload,
                    "result": command.command_result,
                    "error": command.error_message,
                }
                for command in commands
            ],
            "audit": [event.audit_metadata for event in audits],
        },
        default=str,
    )
    assert "Transient-Chatwoot-42" not in durable
    assert "Transient-Rocket-42" not in durable
    assert all("password" not in command.command_payload for command in commands)


class PreflightEffectAdapter(ConfigurationAdapter):
    async def validate(self, *args, **kwargs):
        await super().validate(*args, **kwargs)
        return {"external": {"dispatchExecuted": True}}


@pytest.mark.asyncio
async def test_validation_effect_report_is_durable_without_mutating_configuration(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    adapter = PreflightEffectAdapter(external_dispatch=False)
    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: adapter,
    )
    service = OSSNativeDocumentService(db_session)
    document = await service.document(
        auth_context.workspace_id,
        "bottlecrm",
        "accounts-contacts-leads",
    )

    with pytest.raises(OSSNativeExecutionError) as failure:
        await service.execute(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            provider="bottlecrm",
            capability_key="accounts-contacts-leads",
            request=OSSNativeDocumentActionWrite(
                action_id="upsert_integration_config",
                values={"provider": "local"},
                mode="execute",
                version=document["version"],
                idempotency_key="preflight-external-marker-0001",
            ),
        )

    command = await db_session.get(OSSNativeCommand, failure.value.command_id)
    assert command is not None
    assert command.status == OSSNativeCommandStatus.reconciliation_required
    assert command.dispatcher_invoked is False
    assert command.command_result == {
        "adapterEffectReport": {
            "markerPath": "external.dispatchExecuted",
            "state": "positive",
        }
    }
    snapshot = await OSSNativeStateService(db_session).get_snapshot(
        auth_context.workspace_id,
        "bottlecrm",
        "accounts-contacts-leads",
    )
    assert snapshot.state.config_version == 0
    assert snapshot.state.execution_mode == OSSNativeExecutionMode.disabled
    actions = list(
        (
            await db_session.scalars(
                select(OSSNativeAuditEvent.action)
                .where(OSSNativeAuditEvent.command_id == command.id)
                .order_by(OSSNativeAuditEvent.occurred_at, OSSNativeAuditEvent.id)
            )
        ).all()
    )
    assert len(actions) == 2
    assert set(actions) == {
        OSSNativeAuditAction.command_received,
        OSSNativeAuditAction.command_reconciliation_required,
    }


@pytest.mark.asyncio
async def test_external_effect_takes_precedence_over_secret_echo_and_is_reconciled(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    secret = "Transient-Reconciliation-42"
    adapter = SecretEchoAndEffectAdapter()
    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter",
        lambda _self, _provider: adapter,
    )
    service = OSSNativeDocumentService(db_session)
    document = await service.document(
        auth_context.workspace_id,
        "chatwoot",
        "profile-mfa",
    )

    with pytest.raises(OSSNativeExecutionError) as failure:
        await service.execute(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            provider="chatwoot",
            capability_key="profile-mfa",
            request=OSSNativeDocumentActionWrite(
                action_id="disable_mfa",
                transient_secrets={"password": secret},
                mode="execute",
                version=document["version"],
                idempotency_key="preflight-effect-secret-echo-0001",
            ),
        )

    command = await db_session.get(OSSNativeCommand, failure.value.command_id)
    assert command is not None
    assert command.status == OSSNativeCommandStatus.reconciliation_required
    assert command.error_code == "external_effect_reported"
    durable = json.dumps(
        {
            "payload": command.command_payload,
            "result": command.command_result,
            "error": command.error_message,
        },
        default=str,
    )
    assert secret not in durable
