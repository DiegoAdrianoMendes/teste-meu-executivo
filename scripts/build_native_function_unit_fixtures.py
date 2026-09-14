#!/usr/bin/env python3
"""Build deterministic per-FunctionUnit fixtures for the KLM native binding layer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.native_function_unit_binding_service import (  # noqa: E402
    native_function_unit_bindings,
)
from app.services.oss_function_unit_service import function_unit_catalog  # noqa: E402

DEFAULT_OUTPUT = (
    ROOT / ".local" / "qa-evidence" / "oss" / "oss_function_unit_fixtures.json"
)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _example(field: str) -> Any:
    normalized = field.casefold()
    if normalized.endswith(("count", "version", "limit", "offset")):
        return 1
    if normalized.startswith(("is", "has", "enable", "active")):
        return True
    if normalized.endswith(("ids", "items", "records")):
        return []
    if normalized.endswith(("data", "payload", "config", "configuration")):
        return {}
    return f"fixture-{field}"


def _success_output(operation_id: str, group: str) -> dict[str, Any]:
    output: dict[str, Any] = {
        "status": "succeeded",
        "operationId": operation_id,
        "externalNetworkExecuted": False,
    }
    if group in {"entity.query", "mcp.query", "webhook.inspect", "realtime.query"}:
        output["records"] = []
    elif group in {"entity.upsert", "boundary.invoke"}:
        output["record"] = {"state": "active", "data": {}, "version": 1}
    elif group == "entity.archive":
        output["record"] = {"state": "archived", "data": {}, "version": 1}
    elif group == "ui.navigate":
        output["destination"] = "/app"
    elif group == "authorization.evaluate":
        output["decision"] = {"allowed": False}
    elif group == "job.enqueue":
        output["job"] = {"id": "fixture-job", "status": "queued"}
    elif group == "realtime.subscribe":
        output["subscription"] = {
            "scope": "fixture-scope",
            "transport": "klm_realtime",
            "externalNetworkRequired": False,
        }
    elif group.startswith("data."):
        output["artifact"] = {"id": "fixture-artifact"}
    return output


def build_document() -> dict[str, Any]:
    catalog = function_unit_catalog()
    units = {unit["id"]: unit for unit in catalog["units"]}
    fixtures = []
    for unit_id, binding in sorted(native_function_unit_bindings().items()):
        required = list(binding.input_schema.get("required") or [])
        fixture = {
            "unitId": unit_id,
            "bindingId": binding.binding_id,
            "sourceCommit": units[unit_id]["commit"],
            "nativeOperationId": binding.native_operation_id,
            "equivalenceGroup": binding.equivalence_group,
            "input": {field: _example(field) for field in required},
            "simulatedOutput": {
                "status": "simulated",
                "operationId": binding.native_operation_id,
                "externalNetworkExecuted": False,
            },
            "successOutput": _success_output(
                binding.native_operation_id,
                binding.equivalence_group,
            ),
            "expected": {
                "externalNetworkExecuted": False,
                "workspaceScoped": True,
                "journalIsPrimary": False,
                "recordStore": binding.state_transition["recordStore"],
                "errorContract": list(binding.error_contract),
            },
        }
        fixtures.append(fixture)
    return {
        "schemaVersion": 1,
        "scope": "klm-native-primary-contract-fixtures",
        "catalogDigest": catalog["reproducibility"]["payloadSha256"],
        "fixtureCount": len(fixtures),
        "fixturesDigest": _digest(fixtures),
        "fixtures": fixtures,
    }


def _render(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=True, indent=2) + "\n"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    rendered = _render(build_document())
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            print("Native FunctionUnit fixtures are stale.")
            return 1
        print("Native FunctionUnit fixtures verified.")
        return 0
    _atomic_write(args.output, rendered)
    document = json.loads(rendered)
    print(f"Wrote {args.output}: {document['fixtureCount']} exact fixtures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
