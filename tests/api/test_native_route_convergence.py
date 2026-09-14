"""The mounted application and OpenAPI expose only KLM-native vocabulary."""

from __future__ import annotations

import json
import re

import pytest
from pydantic import BaseModel, RootModel, ValidationError

import app.schemas.native_public as native_public
from app.main import app
from app.schemas.native_public import KLMCommunicationSyncRead
from app.services.native_public_boundary import (
    NativePublicPayloadError,
    public_capability_document,
    public_text,
    sanitize_public_payload,
)

LEGACY_ROUTE_PREFIXES = (
    "/api/v1/oss-function-units",
    "/api/v1/oss-native",
    "/api/v1/oss-experiences",
)
FORBIDDEN_PUBLIC_NAMES = re.compile(
    r"chatwoot|rocket(?:[. _-]?chat)|chatter(?:[. _-]?mate)|"
    r"bottle(?:[. _-]?crm)|sidecar|(?:^|[^a-z0-9])oss(?:[^a-z0-9]|$)|"
    r"/api/v1/oss-",
    re.IGNORECASE,
)


def test_mounted_routes_expose_only_canonical_native_paths() -> None:
    route_paths = {getattr(route, "path", "") for route in app.routes}

    assert {
        "/api/v1/capabilities",
        "/api/v1/capabilities/domains/{domain}/{capability}",
        "/api/v1/capabilities/domains/{domain}/{capability}/document",
        "/api/v1/capabilities/domains/{domain}/{capability}/actions",
        "/api/v1/capabilities/functions",
        "/api/v1/capabilities/functions/{unit_id}",
        "/api/v1/capabilities/functions/{unit_id}/runtime",
        "/api/v1/communications/threads/{thread_id}",
        "/api/v1/collaboration/threads/{thread_id}",
        "/api/v1/automation/agents",
        "/api/v1/automation/flows/{flow_id}",
        "/api/v1/crm/funnel",
    } <= route_paths
    assert not any(
        path.startswith(prefix) for path in route_paths for prefix in LEGACY_ROUTE_PREFIXES
    )


def test_openapi_has_no_legacy_alias_external_name_or_branded_schema() -> None:
    document = app.openapi()
    paths = set(document["paths"])
    operation_ids = {
        operation["operationId"]
        for path_item in document["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    component_names = set(document.get("components", {}).get("schemas", {}))

    assert not any(path.startswith(prefix) for path in paths for prefix in LEGACY_ROUTE_PREFIXES)
    native_paths = {path for path in paths if path.startswith("/api/v1/capabilities/")}
    assert all("{provider}" not in path for path in native_paths)
    assert not any(FORBIDDEN_PUBLIC_NAMES.search(name) for name in operation_ids)
    assert not any(FORBIDDEN_PUBLIC_NAMES.search(name) for name in component_names)
    assert (
        FORBIDDEN_PUBLIC_NAMES.search(json.dumps(document, ensure_ascii=False, sort_keys=True))
        is None
    )


def test_public_native_models_are_explicit_strict_dtos() -> None:
    models = {
        name: value
        for name, value in vars(native_public).items()
        if isinstance(value, type)
        and issubclass(value, BaseModel)
        and value.__module__ == native_public.__name__
    }

    assert models
    assert not any(issubclass(model, RootModel) for model in models.values())
    assert all(model.model_config.get("extra") == "forbid" for model in models.values())

    valid = {
        "status": "synced",
        "threadId": "thread-public",
        "conversationId": "conversation-public",
        "projectedNow": 1,
        "contactCreated": False,
        "rateLimited": False,
        "totalMessages": 1,
        "projectedMessages": 1,
        "receivedMessages": 0,
        "pendingMessages": 0,
        "failedMessages": 0,
        "lastSyncedAt": None,
        "externalChannelDeliveryEnabled": False,
    }
    serialized = KLMCommunicationSyncRead.model_validate(valid).model_dump(
        by_alias=True,
        mode="json",
    )
    assert serialized == valid
    with pytest.raises(ValidationError):
        KLMCommunicationSyncRead.model_validate({**valid, "provider": "internal"})


def test_openapi_native_components_forbid_unknown_properties() -> None:
    components = app.openapi().get("components", {}).get("schemas", {})
    public_components = {
        name: schema for name, schema in components.items() if name.startswith("KLM")
    }

    assert public_components
    assert all(
        schema.get("additionalProperties") is False
        for schema in public_components.values()
        if schema.get("type") == "object" and "properties" in schema
    )


def test_public_json_values_recursively_redact_private_runtime_references() -> None:
    document = public_capability_document(
        "crm",
        "pipeline",
        {
            "capability": {"label": "Funil jurídico"},
            "kpis": [{"id": "queue", "label": "Fila", "value": "redis:6379"}],
            "tabs": [
                {
                    "id": "overview",
                    "sections": [
                        {
                            "id": "records",
                            "rows": [
                                {
                                    "path": "/srv/klm/.vendor/runtime.json",
                                    "trace": "app.services.native_runtime.Dispatcher",
                                    "encodedPath": "%252Fsrv%252Fklm%252F.vendor",
                                    "nested": [
                                        {"endpoint": "redis://redis:6379/0"},
                                        {"privateAddress": "http://10.0.0.8:8000/health"},
                                    ],
                                }
                            ],
                            "data": {"worker": "chattermate-redis:6379"},
                            "items": ["C:\\Users\\klm\\runtime.json"],
                        }
                    ],
                }
            ],
        },
    )

    serialized = json.dumps(document, ensure_ascii=False, sort_keys=True)
    assert serialized.count("Detalhe técnico interno ocultado.") == 8
    assert (
        re.search(
            r"redis\s*:\s*6379|/srv/klm|app\.services|10\.0\.0\.8|c:\\users",
            serialized,
            re.IGNORECASE,
        )
        is None
    )


@pytest.mark.parametrize(
    "value",
    [
        "Falha em redis:6379.",
        "Arquivo /srv/klm/.vendor/runtime.json.",
        "Handler app.services.native_runtime.Dispatcher.",
        "Health http://backend:8000/health.",
    ],
)
def test_public_text_redacts_private_runtime_references(value: str) -> None:
    assert public_text(value) == "Detalhe técnico interno ocultado."


def test_public_json_sanitizer_preserves_legitimate_nested_values() -> None:
    payload = {
        "caseNumber": "1000123-45.2026.8.26.0100",
        "hearingTime": "14:30",
        "publicUrl": "https://klmadvogados.com.br/clientes/123",
        "route": "/app/capacidades/crm/pipeline",
        "apiRoute": "/api/v1/capabilities/domains/crm/pipeline",
        "summary": "API: 200 respostas válidas",
        "filters": [None, True, 3, 4.5, "Redistribuição concluída"],
    }

    assert sanitize_public_payload(payload) == payload


def test_public_json_sanitizer_rejects_values_it_cannot_project_safely() -> None:
    cyclic: dict[str, object] = {}
    cyclic["child"] = cyclic

    with pytest.raises(NativePublicPayloadError, match="referência cíclica"):
        sanitize_public_payload(cyclic)
    with pytest.raises(NativePublicPayloadError, match="chave técnica interna"):
        sanitize_public_payload({"app.services.runtime": "Dispatcher"})
    with pytest.raises(NativePublicPayloadError, match="número JSON inválido"):
        sanitize_public_payload({"value": float("nan")})
    with pytest.raises(NativePublicPayloadError, match="valor JSON inválido"):
        sanitize_public_payload({"value": object()})
