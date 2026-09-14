"""
Script: validate_contract.py
Valida a harmonia entre os tipos TypeScript do frontend e o schema OpenAPI do backend.

Estratégia:
  1. Lê o openapi.json do backend (gerado pelo FastAPI com aliases camelCase).
  2. Lê o src/types/index.ts do frontend.
  3. Para cada interface TypeScript mapeada, verifica se todos os campos existem
     no schema OpenAPI correspondente (comparando camelCase diretamente).
  4. Reporta campos ausentes no backend e campos novos no backend não cobertos pelo frontend.

Uso:
    python3 scripts/validate_contract.py --openapi openapi.json --types src/types/index.ts

Exit codes:
    0 — Contrato válido
    1 — Divergências críticas encontradas
"""
import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Mapeamento: Interface TypeScript → Schema OpenAPI ──
INTERFACE_TO_SCHEMA: Dict[str, str] = {
    "MatterCard":       "MatterRead",
    "ApprovalItem":     "ApprovalRead",
    "Task":             "TaskRead",
    "DocumentItem":     "DocumentRead",
    "TimelineEvent":    "TimelineEventRead",
    "ConversationItem": "ConversationRead",
    "Notification":     "NotificationRead",
    "Agent":            "UserRead",
    "Client":           "ClientRead",
    "ReportMetric":     "KPIMetric",
}

# Mapeamento de campos TypeScript → nome real no OpenAPI (quando o alias é diferente do camelCase automático)
# Formato: { "InterfaceTS.campoTS": "nomeCamelCaseNoOpenAPI" }
FIELD_OVERRIDES: Dict[str, str] = {
    # MatterCard — campos com nome diferente do camelCase automático
    "MatterCard.assignedAgents":   "assignedAgentIds",   # TS: assignedAgents → API: assignedAgentIds
    "MatterCard.currentAgent":     "currentAgentId",     # TS: currentAgent → API: currentAgentId
    "MatterCard.responsibleHuman": "responsibleHumanId", # TS: responsibleHuman → API: responsibleHumanId
    # ApprovalItem — campos com alias explícito
    "ApprovalItem.cardId":         "matterId",           # TS: cardId → API: matterId (serialization_alias)
    "ApprovalItem.type":           "approvalType",       # TS: type → API: approvalType
    "ApprovalItem.requestedAt":    "createdAt",          # TS: requestedAt → API: createdAt
    # Notification — campos com alias explícito
    "Notification.cardId":         "matterId",           # TS: cardId → API: matterId
    "Notification.type":           "notificationType",   # TS: type → API: notificationType
    "Notification.read":           "isRead",             # TS: read → API: isRead
    # TimelineEvent — campos renomeados no backend
    "TimelineEvent.type":          "eventType",          # TS: type → API: eventType
    "TimelineEvent.metadata":      "eventData",          # TS: metadata → API: eventData (reservado SQLAlchemy)
}

# Campos do TypeScript que são intencionalmente ausentes no OpenAPI
# (computados, retornados por outro endpoint, ou opcionais)
SKIP_TS_FIELDS: Set[str] = {
    "MatterCard.auditLogs",        # Retornado via /matters/{id}/audit-logs
    "MatterCard.caseNumber",       # Opcional, pode não existir
}

# Campos do OpenAPI que são intencionalmente ausentes no TypeScript
# (campos internos do backend não expostos ao frontend)
SKIP_API_FIELDS: Set[str] = {
    "id", "createdAt", "updatedAt",  # Campos universais — sempre presentes
    # MatterCard
    "MatterRead.cardOrder",          # Campo novo — frontend ainda não usa
    # Campos internos
    "TaskRead.matterId",
    "DocumentRead.matterId",
    "TimelineEventRead.matterId",
    "ConversationRead.matterId",
    "ConversationRead.isRead",
    "NotificationRead.userId",
}


@dataclass
class ContractIssue:
    severity: str  # "ERROR" | "WARNING"
    interface: str
    field: str
    message: str


@dataclass
class ValidationResult:
    issues: List[ContractIssue] = field(default_factory=list)

    @property
    def errors(self) -> List[ContractIssue]:
        return [i for i in self.issues if i.severity == "ERROR"]

    @property
    def warnings(self) -> List[ContractIssue]:
        return [i for i in self.issues if i.severity == "WARNING"]

    def add(self, severity: str, interface: str, field_name: str, message: str):
        self.issues.append(ContractIssue(severity, interface, field_name, message))


def parse_typescript_interfaces(ts_content: str) -> Dict[str, Set[str]]:
    """Extrai interfaces TypeScript e seus campos do arquivo types/index.ts."""
    interfaces: Dict[str, Set[str]] = {}
    interface_pattern = re.compile(
        r"export\s+interface\s+(\w+)\s*\{([^}]+)\}", re.MULTILINE | re.DOTALL
    )
    for match in interface_pattern.finditer(ts_content):
        name = match.group(1)
        body = match.group(2)
        fields: Set[str] = set()
        field_pattern = re.compile(r"^\s+(\w+)\??:", re.MULTILINE)
        for field_match in field_pattern.finditer(body):
            fields.add(field_match.group(1))
        interfaces[name] = fields
    return interfaces


def extract_openapi_schemas(openapi: dict) -> Dict[str, Set[str]]:
    """Extrai schemas do OpenAPI e seus campos (já em camelCase)."""
    schemas: Dict[str, Set[str]] = {}
    components = openapi.get("components", {}).get("schemas", {})
    for schema_name, schema_def in components.items():
        fields: Set[str] = set()
        properties = schema_def.get("properties", {})
        for prop_name in properties:
            fields.add(prop_name)
        schemas[schema_name] = fields
    return schemas


def validate(openapi_path: Optional[str], types_path: str) -> ValidationResult:
    result = ValidationResult()

    if openapi_path:
        try:
            with open(openapi_path, encoding="utf-8") as f:
                openapi = json.load(f)
        except FileNotFoundError:
            result.add("ERROR", "SETUP", "openapi.json", f"Arquivo não encontrado: {openapi_path}")
            return result
    else:
        from app.main import app

        openapi = app.openapi()

    try:
        with open(types_path, encoding="utf-8") as f:
            ts_content = f.read()
    except FileNotFoundError:
        result.add("ERROR", "SETUP", "types/index.ts", f"Arquivo não encontrado: {types_path}")
        return result

    ts_interfaces = parse_typescript_interfaces(ts_content)
    openapi_schemas = extract_openapi_schemas(openapi)

    for ts_interface, schema_name in INTERFACE_TO_SCHEMA.items():
        ts_fields = ts_interfaces.get(ts_interface, set())
        api_fields = openapi_schemas.get(schema_name, set())

        if not ts_fields:
            result.add("WARNING", ts_interface, "*",
                       f"Interface TypeScript '{ts_interface}' não encontrada em types/index.ts")
            continue

        if not api_fields:
            result.add("ERROR", ts_interface, "*",
                       f"Schema OpenAPI '{schema_name}' não encontrado no openapi.json")
            continue

        # Verificar cada campo TypeScript no schema OpenAPI
        for ts_field in ts_fields:
            full_key = f"{ts_interface}.{ts_field}"

            # Ignorar campos marcados para pular
            if full_key in SKIP_TS_FIELDS:
                continue

            # Verificar se há um override de nome para este campo
            if full_key in FIELD_OVERRIDES:
                expected_api_field = FIELD_OVERRIDES[full_key]
            else:
                # O campo TypeScript já está em camelCase — comparar diretamente
                expected_api_field = ts_field

            if expected_api_field not in api_fields:
                result.add(
                    "ERROR",
                    ts_interface,
                    ts_field,
                    f"Campo '{ts_field}' → esperado '{expected_api_field}' no schema '{schema_name}', mas não encontrado. "
                    f"Campos disponíveis: {sorted(api_fields)[:8]}...",
                )

        # Verificar campos novos no OpenAPI que não existem no TypeScript (avisos)
        # Campos que o frontend deveria considerar adicionar
        skip_global = {"id", "createdAt", "updatedAt"}
        skip_schema = {f.split(".")[-1] for f in SKIP_API_FIELDS if f.startswith(schema_name + ".")}
        skip_schema |= skip_global

        # Construir conjunto de campos cobertos pelo TypeScript (incluindo overrides)
        covered_by_ts: Set[str] = set(ts_fields)
        for full_key, api_name in FIELD_OVERRIDES.items():
            iface, _ = full_key.split(".", 1)
            if iface == ts_interface:
                covered_by_ts.add(api_name)

        for api_field in api_fields:
            if api_field in skip_schema:
                continue
            if api_field not in covered_by_ts:
                result.add(
                    "WARNING",
                    ts_interface,
                    api_field,
                    f"Campo '{api_field}' existe no backend ('{schema_name}') "
                    f"mas não está na interface '{ts_interface}' — considere adicionar ao frontend",
                )

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Valida o contrato entre frontend TypeScript e backend OpenAPI"
    )
    parser.add_argument(
        "--openapi",
        default=None,
        help="Arquivo OpenAPI opcional; sem ele, usa app.openapi() do backend atual.",
    )
    parser.add_argument("--types", default=str(ROOT / "frontend/src/types/index.ts"))
    parser.add_argument("--fail-on-warnings", action="store_true")
    args = parser.parse_args()

    print("=" * 62)
    print("  KLM Legal Hub — Validação de Contrato UI ↔ Backend")
    print("=" * 62)
    print(f"  OpenAPI: {args.openapi or 'app.openapi() atual'}")
    print(f"  Types:   {args.types}")
    print()

    result = validate(args.openapi, args.types)

    # Agrupar por interface
    by_interface: Dict[str, List[ContractIssue]] = {}
    for issue in result.issues:
        by_interface.setdefault(issue.interface, []).append(issue)

    for issue in by_interface.get("SETUP", []):
        print(f"  ❌ Setup [{issue.field}] {issue.message}")
    for interface in sorted(INTERFACE_TO_SCHEMA.keys()):
        issues = by_interface.get(interface, [])
        errors = [i for i in issues if i.severity == "ERROR"]
        warnings = [i for i in issues if i.severity == "WARNING"]

        if errors:
            print(f"  ❌ {interface}")
            for issue in errors:
                print(f"       ERROR  [{issue.field}] {issue.message}")
        elif warnings:
            print(f"  ⚠️  {interface}")
            for issue in warnings:
                print(f"       WARN   [{issue.field}] {issue.message}")
        else:
            print(f"  ✅ {interface}")

    print()
    print("─" * 62)
    print(f"  Interfaces verificadas: {len(INTERFACE_TO_SCHEMA)}")
    print(f"  Erros críticos:  {len(result.errors)}")
    print(f"  Avisos:          {len(result.warnings)}")
    print("─" * 62)

    if result.errors:
        print()
        print("  ❌ CONTRATO INVÁLIDO — corrija os erros antes de fazer merge.")
        sys.exit(1)
    elif args.fail_on_warnings and result.warnings:
        print()
        print("  ⚠️  CONTRATO COM AVISOS — resolva os avisos (--fail-on-warnings ativo).")
        sys.exit(1)
    else:
        print()
        print("  ✅ CONTRATO VÁLIDO — frontend e backend estão em harmonia.")
        sys.exit(0)


if __name__ == "__main__":
    main()
