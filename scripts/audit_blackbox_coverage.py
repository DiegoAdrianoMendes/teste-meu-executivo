#!/usr/bin/env python3
"""Audit the real FastAPI platform surface against Blackbox frontend consumers."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.platform_rbac import PLATFORM_PERMISSION_CATALOG  # noqa: E402
from app.main import app  # noqa: E402

FRONTEND_ROOT = ROOT / "frontend" / "src"
ADMIN_PREFIXES = (
    "/api/v1/platform",
    "/api/v1/error-logs",
    "/api/v1/status",
    "/api/v1/super-admin",
    # Control-plane capabilities surfaced inside the authenticated workspace
    # application are audited here as well. This prevents new agent or
    # Finance 360 routes from becoming dormant backend-only capabilities.
    "/api/v1/agent-platform",
    "/api/v1/finance/dimensional",
    "/api/v1/finance/exports",
)
NON_INTERACTIVE_SUFFIXES = ("/webhook", "/callback")
NON_INTERACTIVE_OPERATIONS = {
    ("POST", "/api/v1/platform/plan-policies/workspaces/{workspace_id}/resolve"),
    ("POST", "/api/v1/platform/plan-policies/{plan_id}/materialize-due"),
    # Ferramentas one-shot da migration expand/contract. Elas preservam
    # contratos anuais existentes, mas não fazem parte da venda ou operação
    # recorrente do Blackbox e permanecem root-only.
    ("POST", "/api/v1/platform/commercial/contracts/legacy-preview"),
    ("POST", "/api/v1/platform/commercial/contracts/legacy-import"),
}
SHARED_MODULE_SURFACES = {
    "agent_templates": "workspace_configuration",
    "billing": "finance",
    "community": "announcements",
    "configuration_deployments": "workspace_configuration",
    # Os três módulos comerciais aparecem dentro da Conta 360, cuja
    # superfície de navegação é `customers`; a autorização de cada bloco
    # continua sendo revalidada separadamente no componente e na API.
    "commercial_adjustments": "customers",
    "commercial_contracts": "customers",
    "entitlement_bonuses": "customers",
    "experience_customization": "workspace_configuration",
    "flow_templates": "workspace_configuration",
    "onboarding": "customers",
    "partners": "benefits",
    "referrals": "benefits",
    "success": "customers",
}


def route_fragments(path: str) -> tuple[str, ...]:
    """Return the stable pieces of a FastAPI path.

    Frontend consumers commonly interpolate path parameters, so a normalized
    exact-string search reports false gaps. Requiring every stable fragment in
    one source file is strict enough to avoid matching unrelated consumers.
    """
    stable_path = re.sub(r"\{[^}]+\}", "", path.rstrip("/"))
    return tuple(
        segment
        for segment in stable_path.split("/")
        if len(segment) >= 3 and segment not in {"api", "platform"}
    )


def frontend_sources() -> dict[str, str]:
    return {
        str(path.relative_to(ROOT)): path.read_text(encoding="utf-8", errors="ignore")
        for path in FRONTEND_ROOT.rglob("*")
        if path.suffix in {".ts", ".tsx"} and "test" not in path.parts
    }


def frontend_consumers(path: str, sources: dict[str, str]) -> list[str]:
    fragments = route_fragments(path)
    return [name for name, source in sources.items() if all(item in source for item in fragments)]


def rbac_module_gaps(sources: dict[str, str]) -> list[str]:
    access_source = sources.get("frontend/src/blackbox/access.ts", "")
    surfaced = set(re.findall(r"module:\s*['\"]([^'\"]+)", access_source))
    covered = surfaced | {
        module for module, surface in SHARED_MODULE_SURFACES.items() if surface in surfaced
    }
    catalog = {permission.module for permission in PLATFORM_PERMISSION_CATALOG}
    return sorted(catalog - covered)


def inventory() -> list[dict[str, object]]:
    sources = frontend_sources()
    rows: list[dict[str, object]] = []
    for path, operations in sorted(app.openapi().get("paths", {}).items()):
        if not path.startswith(ADMIN_PREFIXES):
            continue
        consumers = frontend_consumers(path, sources)
        for method, operation in operations.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            non_interactive = (
                path.endswith(NON_INTERACTIVE_SUFFIXES)
                or (method.upper(), path) in NON_INTERACTIVE_OPERATIONS
            )
            deprecated = bool(operation.get("deprecated"))
            consumed = bool(consumers)
            rows.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "operationId": operation.get("operationId"),
                    "summary": operation.get("summary"),
                    "mutationDescriptor": operation.get("x-platform-mutation"),
                    "status": (
                        "nao_aplicavel"
                        if deprecated and not consumed
                        else "nao_interativo"
                        if non_interactive
                        else "parcial"
                        if consumed
                        else "ausente"
                    ),
                    "frontendEvidence": consumers,
                    "note": (
                        "Compatibilidade temporária explicitamente deprecated; "
                        "sem consumidor Blackbox."
                        if deprecated and not consumed
                        else
                        "Rota encontrada no frontend; autorização, estado visual e E2E "
                        "ainda exigem evidência própria."
                        if consumed
                        else None
                    ),
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    rows = inventory()
    module_gaps = rbac_module_gaps(frontend_sources())
    missing = [row for row in rows if row["status"] == "ausente"]
    mutation_descriptor_missing = [
        row for row in rows
        if row["method"] in {"POST", "PUT", "PATCH", "DELETE"}
        and row["status"] not in {"nao_interativo", "nao_aplicavel"}
        and not row["mutationDescriptor"]
    ]
    mutation_snapshot_adapter_missing = [
        row for row in rows
        if row["method"] in {"POST", "PUT", "PATCH", "DELETE"}
        and row["status"] not in {"nao_interativo", "nao_aplicavel"}
        and (row.get("mutationDescriptor") or {}).get("mutationKind") == "command"
        and (row.get("mutationDescriptor") or {}).get("snapshotAdapter") in {None, "none"}
    ]
    if args.json:
        print(json.dumps({
            "items": rows,
            "missing": len(missing),
            "rbacModulesMissing": module_gaps,
            "mutationDescriptorsMissing": mutation_descriptor_missing,
            "mutationSnapshotAdaptersMissing": mutation_snapshot_adapter_missing,
        }, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(f"{row['status']:15} {row['method']:6} {row['path']}")
        print(f"\nTotal: {len(rows)} | ausentes: {len(missing)}")
        if module_gaps:
            print(f"Módulos RBAC sem superfície: {', '.join(module_gaps)}")
        if mutation_descriptor_missing:
            print(f"Mutações sem descritor: {len(mutation_descriptor_missing)}")
        if mutation_snapshot_adapter_missing:
            print(
                "Comandos sem adapter de snapshot: "
                f"{len(mutation_snapshot_adapter_missing)}"
            )
    return 1 if args.strict and (
        missing
        or module_gaps
        or mutation_descriptor_missing
        or mutation_snapshot_adapter_missing
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
