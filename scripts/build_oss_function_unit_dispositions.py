#!/usr/bin/env python3
"""Materialize exact FunctionUnit-to-KLM mappings for the pinned OSS census."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.oss_capability_registry import _CAPABILITIES  # noqa: E402
from app.services.oss_function_unit_runtime_service import (  # noqa: E402
    build_function_unit_contract,
)

DEFAULT_LOCK = ROOT / "docs" / "oss-function-units.lock.json"
DEFAULT_OUTPUT = ROOT / "app" / "data" / "oss_function_unit_dispositions.json"
DEFAULT_VERIFICATION_MANIFEST = (
    ROOT / ".local" / "qa-evidence" / "oss" / "oss_function_unit_verification.json"
)
ALGORITHM_VERSION = 8

_STOP_WORDS = {
    "account",
    "accounts",
    "admin",
    "administration",
    "agent",
    "agents",
    "api",
    "app",
    "apps",
    "case",
    "chat",
    "custom",
    "external",
    "general",
    "management",
    "native",
    "operations",
    "report",
    "reports",
    "room",
    "rooms",
    "setting",
    "settings",
    "user",
    "users",
    "webhook",
    "webhooks",
}

_DEFAULT_TARGETS = {
    "chatwoot": {
        "api": "api-webhooks-realtime",
        "ui": "conversation-search-custom-views",
        "job": "automation-rules",
        "webhook": "api-webhooks-realtime",
        "config": "account-general",
        "import": "contact-notes-import-export",
        "export": "contact-notes-import-export",
    },
    "chattermate": {
        "api": "api-websocket",
        "ui": "visual-editor",
        "job": "nodes-forms-conditions",
        "webhook": "api-websocket",
        "realtime": "api-websocket",
        "import": "file-upload",
    },
    "bottlecrm": {
        "api": "accounts-contacts-leads",
        "ui": "accounts-contacts-leads",
        "job": "tasks-calendar",
        "mcp": "api-tokens-mcp",
        "permission": "users-rls-rbac",
        "import": "import-export",
        "export": "import-export",
    },
    "rocketchat": {
        "api": "rest-webhooks-bridge",
        "ui": "channels-case-rooms",
        "job": "rest-webhooks-bridge",
        "realtime": "rest-webhooks-bridge",
        "permission": "roles-permissions-admin",
        "config": "roles-permissions-admin",
    },
}

_PROVIDER_ENTITIES = {
    "chatwoot": "omnichannel_capability",
    "chattermate": "agent_runtime_capability",
    "bottlecrm": "legal_crm_capability",
    "rocketchat": "internal_collaboration_capability",
}

_PROVIDER_SERVICES = {
    "chatwoot": "app.services.oss_function_unit_runtime_service.OSSFunctionUnitRuntimeService",
    "chattermate": "app.services.oss_function_unit_runtime_service.OSSFunctionUnitRuntimeService",
    "bottlecrm": "app.services.oss_function_unit_runtime_service.OSSFunctionUnitRuntimeService",
    "rocketchat": "app.services.oss_function_unit_runtime_service.OSSFunctionUnitRuntimeService",
}

_PROVIDER_DOMAINS = {
    "chatwoot": "communications",
    "rocketchat": "collaboration",
    "chattermate": "automation",
    "bottlecrm": "crm",
}

# Phrases are matched against normalized route, handler, name, path, and static details.
# Longer phrases win. The emitted policy contains exact IDs, so this classifier never runs
# in the application and cannot silently remap a future upstream release.
_MANUAL_TERMS: dict[str, tuple[str, ...]] = {
    # Chatwoot
    "inboxes-routing": ("inbox member", "inboxes", "inbox", "channel"),
    "contacts": ("contacts", "contact"),
    "companies-segments": ("segments", "segment"),
    "companies": ("companies", "company"),
    "conversation-composer": ("send message", "messages", "message"),
    "attachments-media": ("attachments", "attachment", "avatar", "media"),
    "private-notes": ("private note", "contact note", "notes", "note"),
    "labels-status-assignment": ("conversation labels", "conversation status", "assignee"),
    "labels-administration": ("labels", "label"),
    "canned-responses-macros": ("macro", "macros"),
    "canned-responses": ("canned responses", "canned response"),
    "automation-rules": ("automation rules", "automation rule", "scheduler"),
    "campaigns": ("campaigns", "campaign"),
    "whatsapp-campaigns": ("whatsapp campaign", "whatsapp campaigns"),
    "reports-csat": ("reports", "report"),
    "csat-report": ("csat", "satisfaction"),
    "sla": ("sla", "resolution scheduler"),
    "custom-attributes-contact-operations": ("custom attribute", "custom attributes"),
    "agents-teams-availability": ("agent capacity", "availability", "agents"),
    "teams": ("team members", "teams", "team"),
    "agent-bots-integrations": ("integration", "integrations"),
    "agent-bots": ("agent bots", "agent bot"),
    "audit-log": ("audit logs", "audit log", "audit"),
    "help-center": ("help center", "portals", "articles", "categories"),
    "api-webhooks-realtime": ("webhooks", "webhook", "action cable", "sidekiq"),
    "external-channels": ("telegram", "line", "twilio", "email channel", "facebook", "instagram"),
    "conversation-search-custom-views": ("custom filters", "custom views", "search", "filter"),
    "notifications": ("notifications", "notification"),
    "inbox-administration": ("inbox administration", "inbox settings"),
    "assignment-policy": ("assignment policies", "assignment policy", "auto assignment"),
    "conversation-workflow": ("conversations", "conversation"),
    "profile-mfa": ("mfa", "2fa", "password", "profile", "auth"),
    "account-general": (
        "installation config",
        "account general",
        "account settings",
        "super admin",
    ),
    "dashboard-apps": ("dashboard apps", "dashboard app"),
    "contact-notes-import-export": ("contacts export", "contacts import", "import", "export"),
    "agent-capacity": ("capacity policies", "capacity policy", "inbox limits"),
    "captain-ai": ("captain", "assistant responses", "assistants"),
    "custom-roles-security": ("custom roles", "custom role", "permissions", "security"),
    "billing-cloud": ("billing", "plan limits", "subscription"),
    # Rocket.Chat
    "channels-case-rooms": ("channels", "channel", "groups", "group", "room"),
    "direct-messages": ("direct messages", "direct message", "dm", "im"),
    "threads-discussions": ("threads", "thread"),
    "mentions-agent-bots": ("mentions", "mention", "bot"),
    "reactions-pins-stars": ("reactions", "reaction"),
    "files-media": ("file upload", "uploads", "upload", "files", "file"),
    "search-history": ("search", "history"),
    "presence-notifications": ("presence", "notifications", "notification", "push", "status"),
    "roles-permissions-admin": ("roles", "role", "permissions", "permission", "abac"),
    "rest-webhooks-bridge": ("webhooks", "webhook", "rest", "methods"),
    "teams-membership": ("team members", "team member", "teams", "team"),
    "native-integrations-webhook-history": ("integrations", "integration", "incoming", "outgoing"),
    "e2ee": ("e2ee", "encrypted", "encryption"),
    "apps-engine-custom-apps": ("marketplace", "apps engine", "custom apps"),
    "message-lifecycle-export-emoji": (
        "delete message",
        "edit message",
        "export",
        "download",
        "retention",
    ),
    "oauth-tokens": ("oauth token", "access token", "tokens", "token"),
    "room-threads": ("thread messages", "thread"),
    "room-discussions": ("discussions", "discussion"),
    "room-mentions": ("mentioned messages", "mentions", "mention"),
    "room-pinned-messages": ("pinned messages", "pin message", "pinned", "pin"),
    "room-starred-messages": ("starred messages", "star message", "starred", "star"),
    "room-files": ("room files", "files", "file"),
    "room-search": ("room search", "search"),
    "admin-users": ("admin users", "users", "user"),
    "admin-permissions": ("admin permissions", "permissions", "permission"),
    "admin-rooms": ("admin rooms", "rooms", "room"),
    "emoji-administration": ("emoji", "emojis"),
    "oauth-providers": ("oauth service", "oauth provider", "saml", "third party login"),
    "third-party-login": ("openid", "ldap", "saml", "cas", "oauth"),
    "personal-access-tokens": ("personal access token", "personal access tokens"),
    "webdav": ("webdav",),
    "enterprise-controls": ("enterprise", "federation", "moderation", "compliance"),
    "external-push-conference": ("video conference", "conference", "call history", "push"),
    # ChatterMate
    "agents-rbac": ("roles", "role", "permissions", "permission", "rbac"),
    "visual-editor": ("workflow", "workflows", "editor"),
    "nodes-forms-conditions": ("condition", "form", "user input", "wait", "message"),
    "nodes-form-action-end-transfer-landing-llm": (
        "human transfer",
        "landing page",
        "llm",
        "action",
        "end",
    ),
    "preview-execution": ("preview", "execute workflow", "execution", "workflow state"),
    "draft-publish-version": ("publish", "draft", "version", "revision"),
    "mcp-tools-agent-association": ("mcp tool", "mcp tools", "agent association"),
    "lead-capture-people": ("contact info", "people", "person", "lead"),
    "notifications-groups": ("notifications", "notification", "groups", "group"),
    "ratings-feedback": ("ratings", "rating", "feedback"),
    "widget-apps": ("widget apps", "widget app", "widgets", "widget"),
    "file-upload": ("file upload", "upload", "files", "file"),
    "jira-shopify": ("jira", "shopify"),
    "handoff-inbox-transfer": (
        "taken over",
        "handoff",
        "transfer",
        "conversations",
        "conversation",
    ),
    "structured-output": ("structured output", "output schema"),
    "knowledge-faq": ("knowledge", "faq search"),
    "widgets-help-center": ("help center", "domain", "widget"),
    "analytics": ("analytics", "performance", "metrics"),
    "api-websocket": ("socket", "websocket", "active sessions", "health"),
    "external-ai-channels": ("email", "line", "meta", "slack", "sms", "telegram"),
    "human-agent-roster": ("human agents", "human agent", "roster"),
    "faq-management": ("faqs", "faq"),
    "user-settings": ("user settings", "profile pic", "profile", "me"),
    "organization-settings": ("organization", "organisation"),
    "integration-settings": ("integrations", "integration"),
    "ai-provider-settings": ("ai config", "ai provider", "provider settings"),
    # BottleCRM
    "accounts-contacts-leads": ("accounts", "account", "contacts", "contact", "leads", "lead"),
    "opportunities-kanban": ("opportunities", "opportunity", "kanban"),
    "cases-tickets": ("cases", "case", "tickets", "ticket"),
    "case-solutions-kb": ("solution suggestions", "solutions", "solution", "knowledge"),
    "case-mailboxes-watchers": ("mailboxes", "mailbox", "watchers", "watch"),
    "case-merge-hierarchy": ("merge", "parent", "hierarchy", "child"),
    "case-time-billing": ("time entries", "time entry", "time summary", "timer", "billing"),
    "case-analytics-bulk-import": ("case analytics", "analytics", "bulk import"),
    "case-pipelines-reopen": ("pipelines", "pipeline", "reopen"),
    "tasks-calendar": ("calendar", "tasks", "task"),
    "task-boards-pipelines": ("task boards", "task board", "task pipelines"),
    "products-estimates-quotes": (
        "estimates",
        "estimate",
        "quotes",
        "quote",
        "products",
        "product",
    ),
    "invoices-recurrence": ("recurring invoices", "recurring", "invoices", "invoice"),
    "orders": ("orders", "order"),
    "templates-macros": ("templates", "template", "macros", "macro"),
    "opportunity-aging-goals": ("aging", "goals", "goal"),
    "invoice-estimate-portals": (
        "invoice portal",
        "estimate portal",
        "public invoice",
        "public estimate",
    ),
    "approvals-routing-escalation": ("approval rules", "approval rule", "approvals", "approval"),
    "business-hours-sla-csat": ("business hours", "sla", "csat"),
    "custom-fields-duplicates": ("custom fields", "custom field", "duplicates", "duplicate"),
    "tags-documents": ("documents", "document", "tags", "tag"),
    "notifications-sse": ("notifications", "notification", "sse"),
    "import-export": ("import", "export"),
    "users-rls-rbac": (
        "org context",
        "org admin",
        "super admin",
        "users",
        "roles",
        "permissions",
        "auth",
    ),
    "audit-suspicious-activity": ("suspicious", "audit", "activity"),
    "api-tokens-mcp": (
        "crm action",
        "crm create",
        "crm delete",
        "crm describe",
        "crm get",
        "crm search",
        "crm update",
        "api token",
        "mcp",
    ),
    "invoice-products": ("invoice products", "invoice product"),
    "invoice-templates": ("invoice templates", "invoice template"),
    "invoice-reports": ("invoice reports", "invoice report"),
    "ticket-approvals": ("ticket approvals", "ticket approval"),
    "ticket-routing": ("ticket routing", "routing rules", "routing"),
    "ticket-escalation": ("ticket escalation", "escalation rules", "escalation"),
    "organization-switcher": ("switch organization", "organization switch", "organisations"),
    "mobile-app": ("mobile", "pwa"),
    "external-integrations": ("integrations", "integration", "api settings", "domain"),
}


def _normalize(value: Any) -> str:
    text = (
        json.dumps(value, ensure_ascii=True, sort_keys=True)
        if not isinstance(value, str)
        else value
    )
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _unit_text(unit: dict[str, Any]) -> str:
    source = unit["source"]
    return _normalize(
        {
            "name": unit["name"],
            "path": source["path"],
            "route": source["route"],
            "handler": source["handler"],
        }
    )


def _derived_terms(capability: dict[str, Any]) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", capability["key"].casefold())
        if len(token) >= 4 and token not in _STOP_WORDS
    }


def _score(text: str, capability: dict[str, Any]) -> tuple[int, int, int]:
    matched_manual = [
        _normalize(term)
        for term in _MANUAL_TERMS.get(capability["id"], _MANUAL_TERMS.get(capability["key"], ()))
        if f" {_normalize(term)} " in f" {text} "
    ]
    manual_score = max((len(term) for term in matched_manual), default=0)
    manual_total = sum(len(term) for term in matched_manual)
    words = set(text.split())
    lexical = sum(len(term) for term in _derived_terms(capability) if term in words)
    return manual_score, manual_total, lexical


def _target_for(
    unit: dict[str, Any],
    by_provider: dict[str, list[dict[str, Any]]],
) -> tuple[str, str]:
    provider = unit["upstream"]
    default = _DEFAULT_TARGETS[provider][unit["category"]]
    text = _unit_text(unit)
    ranked = sorted(
        ((*_score(text, capability), capability["key"]) for capability in by_provider[provider]),
        reverse=True,
    )
    manual, _manual_total, lexical, key = ranked[0]
    if manual > 0:
        return key, "semantic_rule"
    if lexical > 0:
        return key, "lexical_rule"
    return default, "category_default"


def _stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def build_policy(
    lock_path: Path = DEFAULT_LOCK,
    verification_manifest_path: Path = DEFAULT_VERIFICATION_MANIFEST,
) -> dict[str, Any]:
    document = json.loads(lock_path.read_text(encoding="utf-8"))
    units = document["units"]
    by_provider: dict[str, list[dict[str, Any]]] = defaultdict(list)
    capability_ids = set()
    for capability in _CAPABILITIES:
        by_provider[capability["provider"]].append(capability)
        capability_ids.add(capability["id"])

    mapped_units: list[tuple[dict[str, Any], str, str]] = []
    identity_rows = []
    for unit in units:
        identity_rows.append((unit["id"], unit["upstream"], unit["contractKey"]))
        if unit["license"]["status"] != "verified-file":
            continue
        target, confidence = _target_for(unit, by_provider)
        capability_id = f"{unit['upstream']}.{target}"
        if capability_id not in capability_ids:
            raise RuntimeError(f"Unknown target capability {capability_id} for {unit['id']}")
        mapped_units.append((unit, target, confidence))

    rules = []
    confidence_counts: dict[str, int] = defaultdict(int)
    for unit, target, confidence in sorted(
        mapped_units, key=lambda row: row[0]["id"]
    ):
        provider = unit["upstream"]
        unit_id = unit["id"]
        confidence_counts[confidence] += 1
        capability_id = f"{provider}.{target}"
        domain = _PROVIDER_DOMAINS[provider]
        spa_route = f"/app/capacidades/{domain}/{target}"
        classified_unit = {
            **unit,
            "klmMapping": {
                **dict(unit.get("klmMapping") or {}),
                "coverageKind": confidence,
            },
        }
        runtime_contract = build_function_unit_contract(
            classified_unit,
            capability_id=capability_id,
            canonical_entity=_PROVIDER_ENTITIES[provider],
            spa_route=spa_route,
        )
        state = runtime_contract.maturity
        rules.append(
            {
                "id": (
                    f"{provider}-{target}-{confidence}-"
                    f"{runtime_contract.contract_id[:16]}"
                ),
                "priority": 300,
                "match": {
                    "upstreams": [provider],
                    "licenseStatuses": ["verified-file"],
                    "unitIds": [unit_id],
                },
                "state": state,
                "reason": (
                    f"IDs pinados ligados explicitamente à capacidade {provider}.{target} "
                    f"por {confidence}; "
                    + (
                        "oracle independente e binding unitário validados."
                        if state == "native_verified"
                        else (
                            "operação tipada executável no núcleo KLM; paridade com a origem "
                            "permanece não verificada até oracle independente."
                            if state == "native_primary"
                            else "contrato preservado em shadow por vínculo semântico "
                            "insuficiente; execução mutável permanece bloqueada."
                        )
                    )
                ),
                "mapping": {
                    "canonicalEntity": _PROVIDER_ENTITIES[provider],
                    "spaRoute": spa_route,
                    "apiRoute": f"/api/v1/capabilities/functions/{unit_id}/runtime",
                    "applicationService": _PROVIDER_SERVICES[provider],
                    "agentTool": (
                        "klm.capability_function.invoke"
                        if unit["category"] == "mcp"
                        else "klm.capability_function.inspect"
                    ),
                    "auditEvent": "native_capability_function_accessed",
                    "localMode": (
                        "klm_typed_native_primary_v3"
                        if state in {"native_primary", "native_verified"}
                        else "klm_exact_contract_registry_v2"
                    ),
                    "fixture": runtime_contract.fixture_ref,
                    "klmTests": [
                        "tests/services/test_native_function_unit_bindings.py",
                        "tests/services/test_oss_native_local_primary_adapter.py",
                        "tests/services/test_oss_function_unit_runtime_service.py",
                        "tests/oss/test_oss_function_unit_registry.py",
                        "tests/api/test_oss_function_units.py",
                    ],
                    "visualProof": [
                        "frontend/src/test/function-unit-inventory.test.tsx",
                        "frontend/src/test/native-capability-workspace.test.tsx",
                    ],
                    "sidecarOffProof": [
                        "tests/services/test_oss_native_local_primary_adapter.py",
                        "tests/services/test_oss_function_unit_runtime_service.py",
                    ],
                    "capabilityId": f"{provider}.{target}",
                    "coverageKind": confidence,
                    "runtimeContractId": runtime_contract.contract_id,
                    "runtimeHandler": runtime_contract.handler,
                    "semanticProfile": runtime_contract.semantic_profile,
                    "proofProfile": runtime_contract.proof_profile,
                    "bindingId": runtime_contract.binding_id,
                    "nativeOperationId": runtime_contract.native_operation_id,
                    "actionId": runtime_contract.action_id,
                    "capabilityActionId": runtime_contract.capability_action_id,
                    "equivalenceGroup": runtime_contract.equivalence_group,
                    "inputSchema": runtime_contract.input_schema,
                    "outputSchema": runtime_contract.output_schema,
                    "errorContract": list(runtime_contract.error_contract),
                    "authorizationPolicy": runtime_contract.authorization_policy,
                    "stateTransition": runtime_contract.state_transition,
                    "effects": runtime_contract.effects,
                    "maturity": runtime_contract.maturity,
                    "conformanceLedger": "app/data/oss_function_unit_conformance.json",
                    "verificationManifest": (
                        "app/data/oss_function_unit_conformance.json"
                        if state == "native_verified"
                        else None
                    ),
                },
            }
        )

    rules.append(
        {
            "id": "embedded-chat-root-license-review",
            "priority": 1000,
            "match": {
                "upstreams": ["rocket-embedded-chat"],
                "licenseStatuses": ["package-metadata-only"],
            },
            "state": "blocked_license",
            "reason": (
                "Observable package behavior remains in the denominator, but code reuse is "
                "blocked until the missing repository-root license scope is reviewed explicitly."
            ),
            "mapping": {},
        }
    )
    return {
        "schemaVersion": 2,
        "generator": {
            "name": "scripts/build_oss_function_unit_dispositions.py",
            "version": ALGORITHM_VERSION,
            "unitIdentityDigest": _stable_digest(sorted(identity_rows)),
            "capabilityDigest": _stable_digest(
                sorted((item["id"], item["key"]) for item in _CAPABILITIES)
            ),
            "mappedLicensedUnits": sum(confidence_counts.values()),
            "verifiedLicensedUnits": sum(
                1 for rule in rules if rule.get("state") == "native_verified"
            ),
            "primaryLicensedUnits": sum(
                1 for rule in rules if rule.get("state") == "native_primary"
            ),
            "shadowLicensedUnits": sum(
                1 for rule in rules if rule.get("state") == "native_shadow"
            ),
            "byConfidence": dict(sorted(confidence_counts.items())),
            "verification": {
                "state": "per_unit_oracle_only",
                "reason": "aggregate_release_manifest_is_observer_only",
                "authority": "app/data/oss_function_unit_conformance.json",
                "aggregateManifestIsInput": False,
                "manifest": _display_path(verification_manifest_path),
                "sourceDigest": None,
            },
        },
        "policy": (
            "Every licensed community FunctionUnit is bound by exact ID to one reviewed KLM "
            "capability. Classification runs only during policy generation; runtime and census "
            "resolution is exact and fail closed. Native primary means the typed KLM operation "
            "has a specific action binding and is executable against canonical tenant-owned "
            "state. Native shadow preserves an exact contract but only permits inspection and "
            "simulation until its semantics are resolved. Promotion to native_verified "
            "is per unit and requires an independent pinned-source oracle; no global manifest "
            "may promote the whole catalog."
        ),
        "rules": rules,
    }


def _render(policy: dict[str, Any]) -> str:
    return json.dumps(policy, ensure_ascii=True, indent=2, sort_keys=False) + "\n"


def _atomic_write(path: Path, content: str) -> None:
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
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--verification-manifest",
        type=Path,
        default=DEFAULT_VERIFICATION_MANIFEST,
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    rendered = _render(build_policy(args.lock, args.verification_manifest))
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            print("FunctionUnit disposition policy is stale.")
            return 1
        print("FunctionUnit disposition policy verified.")
        return 0
    _atomic_write(args.output, rendered)
    policy = json.loads(rendered)
    print(
        f"Wrote {args.output}: {policy['generator']['mappedLicensedUnits']} exact mappings, "
        f"{policy['generator']['verifiedLicensedUnits']} verified, "
        f"{len(policy['rules'])} rules."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
