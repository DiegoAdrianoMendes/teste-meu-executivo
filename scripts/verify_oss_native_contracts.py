#!/usr/bin/env python3
"""Exercise every KLM capability document and action through the public local API."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
TRANSIENT_QA_SECRET = "KLM-QA-Transient-Only-2026!"
DOMAIN_TO_INTERNAL_SOURCE = {
    "communications": "chatwoot",
    "collaboration": "rocketchat",
    "automation": "chattermate",
    "crm": "bottlecrm",
}
EXPECTED_CAPABILITIES = 135
EXPECTED_ACTIONS = 511
EXPECTED_CONTRACT_CAPABILITIES = 121
EXPECTED_CAPABILITIES_BY_DOMAIN = {
    "communications": 39,
    "collaboration": 33,
    "automation": 27,
    "crm": 36,
}


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        response = urlopen(request, timeout=60)
        status = response.status
        raw = response.read().decode("utf-8")
    except HTTPError as error:
        status = error.code
        raw = error.read().decode("utf-8")
    except URLError as error:
        raise RuntimeError(f"API local indisponível: {error.reason}") from error
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, raw


def _json_value(name: str) -> Any:
    normalized = name.replace("-", "_").lower()
    if normalized in {"aliases", "attribute_keys", "user_ids"}:
        return ["qa-local"]
    if normalized == "permissions":
        return [{"_id": "view-room-administration", "roles": ["admin"]}]
    if normalized == "actions":
        return [{"type": "add_label", "params": ["qa-local"]}]
    if normalized in {"graph", "workflow_graph"}:
        return {"nodes": [], "edges": []}
    if normalized == "manifest":
        return {"id": "klm.qa.local", "name": "KLM QA Local", "version": "1.0.0"}
    return {"qa": True, "source": "klm-native-contract-verifier"}


def _text_value(name: str) -> str:
    normalized = name.replace("-", "_").lower()
    values = {
        "checksum": "a" * 64,
        "content_type": "application/pdf",
        "contenttype": "application/pdf",
        "email": "qa@klm.local",
        "emoji": ":white_check_mark:",
        "event": '{"type":"qa.local"}',
        "filename": "qa-document.pdf",
        "file_name": "qa-document.pdf",
        "roomtype": "c",
        "room_type": "c",
        "status": "active",
        "type": "webhook",
    }
    if normalized in values:
        return values[normalized]
    if normalized.endswith("email"):
        return "qa@klm.local"
    if normalized.endswith(("url", "uri", "endpoint")):
        return "http://127.0.0.1:4173/local-qa"
    if normalized.endswith("id") or normalized.endswith("_id"):
        return "qa-local-id"
    return f"qa-{normalized or 'value'}"


def _field_value(field: dict[str, Any]) -> Any:
    default = field.get("defaultValue")
    if default not in (None, "", [], {}):
        return default
    options = field.get("options")
    field_type = str(field.get("type") or "text")
    if isinstance(options, list) and options:
        first = options[0]
        option_value = first.get("value") if isinstance(first, dict) else first
        return [option_value] if field_type == "multiselect" else option_value
    name = str(field.get("name") or "value")
    if field_type == "email":
        return "qa@klm.local"
    if field_type == "url":
        return "http://127.0.0.1:4173/local-qa"
    if field_type in {"checkbox", "switch"}:
        return True
    if field_type in {"integer", "number"}:
        minimum = field.get("min")
        return max(1, int(minimum)) if isinstance(minimum, int | float) else 1
    if field_type == "date":
        return "2026-07-18"
    if field_type == "datetime":
        return "2026-07-18T12:00:00+00:00"
    if field_type == "json":
        return _json_value(name)
    if field_type == "multiselect":
        return ["qa-local"]
    return _text_value(name)


def _action_payload(action: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    values: dict[str, Any] = {}
    transient: dict[str, str] = {}
    fields = action.get("fields")
    for field in fields if isinstance(fields, list) else []:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        if str(field.get("type") or "") == "password":
            transient[name] = TRANSIENT_QA_SECRET
        else:
            values[name] = _field_value(field)
    if str(action.get("id") or "") == "commit_import":
        staging_rows = [{"external_id": "qa-local-1", "name": "QA local"}]
        if "rows" in values:
            values["rows"] = staging_rows
        else:
            values["payload"] = {"rows": staging_rows}
    return values, transient


def _compact_error(value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False, default=str)
    rendered = rendered.replace(TRANSIENT_QA_SECRET, "[REDACTED]")
    return rendered[:700]


def _external_network_marker(action_result: Any) -> bool:
    if not isinstance(action_result, dict):
        raise ValueError("resultado da ação não contém evidência de rede estruturada")
    marker = action_result.get("externalNetworkExecuted")
    if type(marker) is not bool:
        raise ValueError("externalNetworkExecuted deve ser booleano e explícito")
    return marker


def run(base_url: str) -> dict[str, Any]:
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
        raise ValueError("A prova aceita somente uma API HTTP em loopback.")
    api = f"{base_url.rstrip('/')}/api/v1"
    status, session = _request(api, "POST", "/klm/local/session")
    if status != 200 or not isinstance(session, dict):
        raise RuntimeError("Não foi possível criar a sessão local de QA.")
    token = session.get("accessToken") or session.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("A sessão local não retornou token.")

    status, source_binding = _request(
        api,
        "GET",
        "/klm/local/source-binding",
        token=token,
    )
    if status != 200 or not isinstance(source_binding, dict):
        raise RuntimeError("A identidade do processo local não respondeu.")
    server_runtime_source_digest = source_binding.get("runtimeSourceDigest")
    server_runtime_source_file_count = source_binding.get("runtimeSourceFileCount")
    server_runtime_source_matches_current = source_binding.get(
        "runtimeSourceMatchesCurrent"
    )
    if (
        source_binding.get("scope") != "klm-local-source-binding"
        or not isinstance(server_runtime_source_digest, str)
        or len(server_runtime_source_digest) != 64
        or not isinstance(server_runtime_source_file_count, int)
        or server_runtime_source_matches_current is not True
    ):
        raise RuntimeError(
            "O processo local não está vinculado à versão atual do código; reinicie o KLM."
        )

    status, registry = _request(api, "GET", "/capabilities", token=token)
    if status != 200 or not isinstance(registry, dict):
        raise RuntimeError("O catálogo nativo KLM não respondeu.")
    if (
        registry.get("schemaVersion") != "3.0"
        or registry.get("scope") != "klm-native-capabilities"
    ):
        raise RuntimeError("O catálogo nativo KLM retornou uma identidade inválida.")
    capabilities = registry.get("capabilities")
    if not isinstance(capabilities, list):
        raise RuntimeError("O catálogo nativo KLM não contém capacidades.")
    summary = registry.get("summary")
    if (
        len(capabilities) != EXPECTED_CAPABILITIES
        or not isinstance(summary, dict)
        or summary.get("total") != EXPECTED_CAPABILITIES
        or summary.get("byDomain") != EXPECTED_CAPABILITIES_BY_DOMAIN
    ):
        raise RuntimeError("O censo público das capacidades KLM está incompleto.")

    run_id = uuid4().hex[:12]
    document_ids: list[str] = []
    contract_ids: list[str] = []
    action_ids: list[str] = []
    failures: list[dict[str, str]] = []
    external_network_executions = 0
    external_network_assertions = 0
    for index, capability in enumerate(capabilities, start=1):
        if not isinstance(capability, dict):
            failures.append({"id": f"row-{index}", "error": "capacidade inválida"})
            continue
        domain = str(capability.get("domain") or "")
        key = str(capability.get("key") or "")
        provider = DOMAIN_TO_INTERNAL_SOURCE.get(domain, "")
        if not provider or capability.get("id") != f"{domain}.{key}":
            failures.append(
                {
                    "id": f"row-{index}",
                    "error": "identidade pública da capacidade inválida",
                }
            )
            continue
        capability_id = f"{provider}.{key}"
        encoded_domain = quote(domain, safe="")
        encoded_key = quote(key, safe="")
        document_path = (
            f"/capabilities/domains/{encoded_domain}/{encoded_key}/document"
        )
        status, document = _request(api, "GET", document_path, token=token)
        if status != 200 or not isinstance(document, dict):
            failures.append(
                {
                    "id": capability_id,
                    "error": f"documento HTTP {status}: {_compact_error(document)}",
                }
            )
            continue
        document_identity = document.get("capability")
        document_domain = document.get("domain")
        if (
            not isinstance(document_identity, dict)
            or document_identity.get("id") != key
            or not isinstance(document_domain, dict)
            or document_domain.get("id") != domain
        ):
            failures.append({"id": capability_id, "error": "identidade do documento divergente"})
            continue
        document_ids.append(capability_id)
        actions = document.get("actions")
        if not isinstance(actions, list) or not actions:
            failures.append({"id": capability_id, "error": "documento sem ações"})
            continue

        capability_passed = True
        for action in actions:
            if not isinstance(action, dict) or not action.get("id"):
                failures.append({"id": capability_id, "error": "ação sem ID"})
                capability_passed = False
                continue
            action_id = str(action["id"])
            contract_id = f"{capability_id}:{action_id}"
            values, transient = _action_payload(action)
            idempotency_hash = hashlib.sha256(contract_id.encode("utf-8")).hexdigest()[:24]
            request_payload = {
                "actionId": action_id,
                "values": values,
                "transientSecrets": transient,
                "mode": "dry-run",
                "version": document.get("version", 0),
                "idempotencyKey": f"qa-contract:{run_id}:{idempotency_hash}",
                "context": {
                    "qaVerification": True,
                    "qaVerificationRun": run_id,
                },
            }
            status, result = _request(
                api,
                "POST",
                f"/capabilities/domains/{encoded_domain}/{encoded_key}/actions",
                token=token,
                payload=request_payload,
            )
            if status != 200 or not isinstance(result, dict):
                failures.append(
                    {
                        "id": contract_id,
                        "error": f"ação HTTP {status}: {_compact_error(result)}",
                    }
                )
                capability_passed = False
                continue
            action_result = result.get("result")
            try:
                external_executed = _external_network_marker(action_result)
            except ValueError as error:
                failures.append({"id": contract_id, "error": str(error)})
                capability_passed = False
                continue
            external_network_assertions += 1
            external_network_executions += int(external_executed)
            if result.get("status") != "succeeded" or external_executed:
                failures.append(
                    {"id": contract_id, "error": f"resultado inseguro: {_compact_error(result)}"}
                )
                capability_passed = False
                continue
            action_ids.append(contract_id)
        if capability_passed and capability.get("contractEligible") is True:
            contract_ids.append(capability_id)
        print(
            f"[{index:03d}/{len(capabilities):03d}] {capability_id}: "
            f"{len(actions)} contratos {'OK' if capability_passed else 'FALHA'}",
            flush=True,
        )

    observed_counts = {
        "documents": len(set(document_ids)),
        "actions": len(set(action_ids)),
        "contractCapabilities": len(set(contract_ids)),
    }
    expected_counts = {
        "documents": EXPECTED_CAPABILITIES,
        "actions": EXPECTED_ACTIONS,
        "contractCapabilities": EXPECTED_CONTRACT_CAPABILITIES,
    }
    if observed_counts != expected_counts:
        failures.append(
            {
                "id": "native-contract-census",
                "error": (
                    f"contagens observadas {observed_counts} divergem de {expected_counts}"
                ),
            }
        )

    evidence = {
        "status": "PASS" if not failures else "FAIL",
        "generatedAt": datetime.now(UTC).isoformat(),
        "verificationRunId": run_id,
        "nativeDocumentsValidated": len(set(document_ids)),
        "actionContractsValidated": len(set(action_ids)),
        "contractCapabilitiesValidated": len(set(contract_ids)),
        "externalNetworkExecutions": external_network_executions,
        "externalNetworkAssertions": external_network_assertions,
        "externalEffectEvidenceScope": "application_reported_dry_run",
        "externalEffectIndependentlyObserved": False,
        "nativeDocumentCapabilityIds": sorted(set(document_ids)),
        "contractCapabilityIds": sorted(set(contract_ids)),
        "actionContractIds": sorted(set(action_ids)),
        "serverRuntimeSourceDigest": server_runtime_source_digest,
        "serverRuntimeSourceFileCount": server_runtime_source_file_count,
        "serverRuntimeSourceMatchesCurrent": server_runtime_source_matches_current,
        "failures": failures,
    }
    print(
        "KLM_NATIVE_CONTRACT_SUMMARY="
        + json.dumps(
            {
                "status": evidence["status"],
                "documents": evidence["nativeDocumentsValidated"],
                "actions": evidence["actionContractsValidated"],
                "contractCapabilities": evidence["contractCapabilitiesValidated"],
                "externalNetworkAssertions": evidence["externalNetworkAssertions"],
                "externalNetworkExecutions": evidence["externalNetworkExecutions"],
                "failures": evidence["failures"],
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    print(
        "KLM_NATIVE_CONTRACT_EVIDENCE="
        + json.dumps(evidence, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        flush=True,
    )
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    arguments = parser.parse_args()
    evidence = run(arguments.base_url)
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error
