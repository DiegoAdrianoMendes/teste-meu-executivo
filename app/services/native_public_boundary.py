"""Projection layer between internal capability runtimes and the public KLM API."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any, cast
from urllib.parse import unquote

from pydantic import BaseModel

from app.schemas.native_public import KLMNativeDomain
from app.services.oss_capability_registry import native_capability_registry
from app.services.oss_function_unit_service import function_unit_catalog

_DOMAIN_BY_INTERNAL_SOURCE: dict[str, KLMNativeDomain] = {
    "chatwoot": "communications",
    "rocket-embedded-chat": "communications",
    "rocketchat": "collaboration",
    "chattermate": "automation",
    "bottlecrm": "crm",
}
_DOMAIN_LABELS: dict[KLMNativeDomain, str] = {
    "communications": "Comunicações",
    "collaboration": "Colaboração",
    "automation": "Automação",
    "crm": "CRM jurídico",
}
_DOMAIN_ORDER = tuple(_DOMAIN_LABELS)
_FUNCTION_ID_PATTERN = re.compile(r"^fu_[0-9a-f]{24}$")
_FUNCTION_STATE_ORDER = (
    "discovered",
    "mapped",
    "native_shadow",
    "native_primary",
    "native_verified",
    "blocked_proprietary",
    "blocked_license",
)
_FUNCTION_STATES = frozenset(_FUNCTION_STATE_ORDER)
_CAPABILITY_DISPOSITIONS = {
    "discovered",
    "approved_native",
    "approved_embedded",
    "approved_full_app",
    "implemented",
    "not_applicable",
    "blocked_license",
    "blocked_dependency",
    "blocked_external_gate",
}
_PUBLIC_MODE_BY_INTERNAL_MODE = {
    "klm_canonical": "local_canonical",
    "klm_equivalent": "local_equivalent",
    "sidecar_round_trip": "local_reconciled",
    "sidecar_command": "local_command",
    "gated_configuration": "controlled_configuration",
    "responsive_substitution": "responsive_substitution",
}
_TEXT_REPLACEMENTS = (
    (re.compile(r"chatwoot", re.IGNORECASE), "comunicações"),
    (re.compile(r"rocket(?:[._ -]?chat)", re.IGNORECASE), "colaboração"),
    (re.compile(r"rocket-embedded-chat", re.IGNORECASE), "comunicações"),
    (re.compile(r"chattermate", re.IGNORECASE), "automação"),
    (re.compile(r"bottlecrm", re.IGNORECASE), "CRM jurídico"),
    (re.compile(r"sidecars?", re.IGNORECASE), "módulos locais"),
    (re.compile(r"upstreams?", re.IGNORECASE), "referências técnicas"),
    (re.compile(r"(?<![a-z0-9])oss(?![a-z0-9])", re.IGNORECASE), "tecnologia incorporada"),
)
_PUBLIC_JSON_MAX_DEPTH = 20
_PUBLIC_JSON_MAX_NODES = 10_000
_PUBLIC_REDACTED_VALUE = "Detalhe técnico interno ocultado."
_PRIVATE_RUNTIME_VALUE_PATTERNS = (
    re.compile(
        r"(?<![a-z0-9+.-])(?:redis|rediss|postgres(?:ql)?|mysql|mariadb|"
        r"mongodb(?:\+srv)?|amqp|amqps|file|unix)://",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![a-z0-9_.-])(?:[a-z0-9-]+\.)*(?:[a-z0-9-]+-)?"
        r"(?:redis|postgres(?:ql)?|mysql|mariadb|mongo(?:db)?|minio|mailpit)"
        r"\s*:\s*\d{1,5}(?!\d)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![a-z0-9_.-])(?:localhost|host\.docker\.internal)"
        r"(?::\d{1,5})?(?![a-z0-9_.-])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![a-z0-9_.-])(?:[a-z0-9-]+\.)+"
        r"(?:internal|local|localhost|svc|cluster\.local)"
        r"(?::\d{1,5})?(?![a-z0-9_.-])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![a-z0-9_.-])https?://[a-z0-9-]+:\d{1,5}(?:[/\s]|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<!\d)(?:10(?:\.\d{1,3}){3}|127(?:\.\d{1,3}){3}|"
        r"169\.254(?:\.\d{1,3}){2}|192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})"
        r"(?::\d{1,5})?(?!\d)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![a-z0-9])(?:/(?:srv|etc|var|opt|home|users|root|tmp|private|"
        r"workspace|workspaces|proc|sys|dev|mnt|run|usr|lib|bin|sbin|volumes)"
        r"(?:[/\\]|$)|"
        r"[a-z]:[\\/](?:users|windows|programdata|program files)(?:[\\/]|$)|"
        r"\\\\[^\\/\s]+\\[^\\/\s]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![a-z0-9])/(?:[^/\s]+/)*(?:\.vendor|\.venv|\.git|node_modules)"
        r"(?:[/\s]|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![a-z0-9_])(?:app|tests|scripts)\."
        r"(?:services|api|models|schemas|core|workers|agents|db)"
        r"(?:\.[a-z_][a-z0-9_]*)*(?![a-z0-9_])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![a-z0-9_])(?:app|tests|scripts)[\\/]"
        r"(?:services|api|models|schemas|core|workers|agents|db)(?:[\\/][^\s]+)?",
        re.IGNORECASE,
    ),
)
_ALWAYS_PRIVATE_KEY_MARKERS = (
    "chatwoot",
    "rocketchat",
    "chattermate",
    "bottlecrm",
    "sidecar",
    "upstream",
)
_DOCUMENT_PRIVATE_KEY_MARKERS = (
    "provider",
    "engine",
    "motor",
    "topology",
    "topologia",
    "adapter",
    "binding",
    "contract",
    "fixture",
    "handler",
    "dispatcher",
    "proof",
    "applicationservice",
)
_AUDIT_SUMMARIES = {
    "config_created": "Configuração da capacidade criada.",
    "config_updated": "Configuração da capacidade atualizada.",
    "command_received": "Comando recebido.",
    "command_started": "Execução do comando iniciada.",
    "command_succeeded": "Comando concluído.",
    "command_failed": "Comando encerrado com falha segura.",
    "command_blocked": "Comando registrado sem execução.",
    "command_reconciliation_required": "Comando aguardando reconciliação humana.",
}


class NativePublicNotFoundError(LookupError):
    """Raised when a public opaque identifier has no internal catalog match."""


class NativePublicPayloadError(ValueError):
    """Raised when internal data cannot be projected into a safe public JSON value."""


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _contains_private_runtime_reference(value: str) -> bool:
    candidates = [value]
    for _ in range(2):
        decoded = unquote(candidates[-1])
        if decoded == candidates[-1]:
            break
        candidates.append(decoded)
    return any(
        pattern.search(candidate)
        for candidate in candidates
        for pattern in _PRIVATE_RUNTIME_VALUE_PATTERNS
    )


def public_text(value: Any) -> str:
    text = str(value or "")
    if _contains_private_runtime_reference(text):
        return _PUBLIC_REDACTED_VALUE
    text = re.sub(r"/api/v1/oss-[^\s\"']*", "/api/v1/capabilities", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<![a-z0-9])oss[_-]", "native_", text, flags=re.IGNORECASE)
    for pattern, replacement in _TEXT_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    if _contains_private_runtime_reference(text):
        raise NativePublicPayloadError("O texto público contém uma referência técnica interna.")
    return text


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python", by_alias=True)
    if isinstance(value, Mapping):
        return {str(key): child for key, child in value.items()}
    raise TypeError("A resposta nativa não é um objeto.")


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_mapping(item) for item in value if isinstance(item, (Mapping, BaseModel))]


def _integer(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _private_key(key: str, *, drop_internal_metadata: bool) -> bool:
    normalized = _normalized_key(key)
    if normalized.startswith("oss"):
        return True
    if any(marker in normalized for marker in _ALWAYS_PRIVATE_KEY_MARKERS):
        return True
    if not drop_internal_metadata:
        return False
    return normalized.startswith("source") or any(
        marker in normalized for marker in _DOCUMENT_PRIVATE_KEY_MARKERS
    )


def sanitize_public_payload(
    value: Any,
    *,
    drop_internal_metadata: bool = True,
) -> Any:
    """Project JSON-like data without exposing private runtime references."""

    node_count = 0
    active_containers: set[int] = set()

    def visit(item: Any, depth: int) -> Any:
        nonlocal node_count
        node_count += 1
        if node_count > _PUBLIC_JSON_MAX_NODES:
            raise NativePublicPayloadError("O payload público excede o limite de valores.")
        if depth > _PUBLIC_JSON_MAX_DEPTH:
            raise NativePublicPayloadError("O payload público excede o limite de aninhamento.")

        if isinstance(item, BaseModel):
            item = item.model_dump(mode="python", by_alias=True)
        if isinstance(item, Mapping):
            container_id = id(item)
            if container_id in active_containers:
                raise NativePublicPayloadError("O payload público contém uma referência cíclica.")
            active_containers.add(container_id)
            try:
                copied: dict[str, Any] = {}
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise NativePublicPayloadError(
                            "O payload público contém uma chave JSON inválida."
                        )
                    if _private_key(key, drop_internal_metadata=drop_internal_metadata):
                        continue
                    if _contains_private_runtime_reference(key):
                        raise NativePublicPayloadError(
                            "O payload público contém uma chave técnica interna."
                        )
                    copied[key] = visit(child, depth + 1)
                return copied
            finally:
                active_containers.remove(container_id)
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            container_id = id(item)
            if container_id in active_containers:
                raise NativePublicPayloadError("O payload público contém uma referência cíclica.")
            active_containers.add(container_id)
            try:
                return [visit(child, depth + 1) for child in item]
            finally:
                active_containers.remove(container_id)
        if isinstance(item, str):
            if _contains_private_runtime_reference(item):
                return _PUBLIC_REDACTED_VALUE
            sanitized = public_text(item)
            if _contains_private_runtime_reference(sanitized):
                raise NativePublicPayloadError(
                    "O payload público contém uma referência técnica interna."
                )
            return sanitized
        if isinstance(item, float) and not math.isfinite(item):
            raise NativePublicPayloadError("O payload público contém um número JSON inválido.")
        if item is None or isinstance(item, (bool, int, float)):
            return item
        raise NativePublicPayloadError("O payload público contém um valor JSON inválido.")

    return visit(value, 0)


def _opaque_function_id(raw_id: str) -> str:
    digest = hashlib.sha256(f"klm-capability-function:v1:{raw_id}".encode()).hexdigest()
    return f"fu_{digest[:24]}"


def _humanize(value: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    text = re.sub(r"\.(?:as_view|getProvider|search|suggest)$", "", text)
    text = re.sub(r"[^A-Za-zÀ-ÿ0-9]+", " ", text).strip()
    return text[:1].upper() + text[1:] if text else "Função KLM"


@lru_cache(maxsize=2)
def _capability_labels(catalog_marker: str) -> dict[str, str]:
    del catalog_marker
    return {
        str(item["id"]): public_text(item.get("label") or item.get("key") or "Capacidade KLM")
        for item in native_capability_registry()["capabilities"]
    }


def _catalog_marker() -> str:
    document = function_unit_catalog()
    return str(document.get("reproducibility", {}).get("payloadSha256") or "catalog")


def _public_function(raw: Mapping[str, Any], labels: Mapping[str, str]) -> dict[str, Any]:
    internal_source = str(raw.get("upstream") or "")
    domain = _DOMAIN_BY_INTERNAL_SOURCE.get(internal_source)
    if domain is None:
        raise ValueError("O catálogo contém uma origem sem domínio KLM.")
    mapping = cast(Mapping[str, Any], raw.get("klmMapping") or {})
    disposition_data = cast(Mapping[str, Any], raw.get("disposition") or {})
    disposition = str(disposition_data.get("state") or "discovered")
    if disposition not in _FUNCTION_STATES:
        disposition = "discovered"
    maturity = str(mapping.get("maturity") or disposition)
    if maturity not in _FUNCTION_STATES:
        maturity = disposition
    internal_capability = str(mapping.get("capabilityId") or "")
    capability = (
        internal_capability.split(".", 1)[1] if "." in internal_capability else "unavailable"
    )
    capability_id = f"{domain}.{capability}"
    capability_label = labels.get(capability_id, _humanize(capability))
    raw_name = str(raw.get("name") or "")
    granular_name = public_text(_humanize(raw_name))
    if any(pattern.search(raw_name) for pattern, _ in _TEXT_REPLACEMENTS):
        name = capability_label
    elif granular_name.casefold() == capability_label.casefold():
        name = capability_label
    else:
        name = f"{capability_label}: {granular_name}"
    state_summary = {
        "native_verified": "Função verificada na operação KLM.",
        "native_primary": "Função disponível na operação KLM controlada.",
        "native_shadow": "Função disponível para inspeção e simulação.",
        "mapped": "Função mapeada para uma capacidade KLM.",
        "discovered": "Função catalogada para avaliação.",
        "blocked_proprietary": "Função indisponível por limite de incorporação.",
        "blocked_license": "Função indisponível por limite de licença.",
    }[disposition]
    return {
        "id": _opaque_function_id(str(raw["id"])),
        "domain": domain,
        "name": name,
        "category": public_text(raw.get("category") or "geral"),
        "capability": capability,
        "maturity": maturity,
        "disposition": disposition,
        "summary": f"{state_summary} Domínio: {_DOMAIN_LABELS[domain]}.",
    }


@lru_cache(maxsize=2)
def _function_indexes(catalog_marker: str) -> tuple[tuple[dict[str, Any], ...], dict[str, str]]:
    document = function_unit_catalog()
    current_marker = str(document.get("reproducibility", {}).get("payloadSha256") or "catalog")
    if current_marker != catalog_marker:
        return _function_indexes(current_marker)
    labels = _capability_labels(catalog_marker)
    projected: list[dict[str, Any]] = []
    reverse: dict[str, str] = {}
    for raw in document["units"]:
        item = _public_function(raw, labels)
        if item["id"] in reverse:
            raise RuntimeError("Colisão no identificador público de função KLM.")
        projected.append(item)
        reverse[item["id"]] = str(raw["id"])
    return tuple(projected), reverse


def _indexes() -> tuple[tuple[dict[str, Any], ...], dict[str, str]]:
    return _function_indexes(_catalog_marker())


def _function_summary(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    dispositions = Counter(str(item["disposition"]) for item in items)
    by_domain = Counter(str(item["domain"]) for item in items)
    by_category = Counter(str(item["category"]) for item in items)
    blocked = dispositions["blocked_license"] + dispositions["blocked_proprietary"]
    verified = dispositions["native_verified"]
    available = len(items) - blocked
    return {
        "total": len(items),
        "available": available,
        "blocked": blocked,
        "verified": verified,
        "remaining": max(available - verified, 0),
        "by_domain": {domain: by_domain[domain] for domain in _DOMAIN_ORDER},
        "by_category": dict(sorted(by_category.items())),
        "by_disposition": dict(sorted(dispositions.items())),
    }


def list_public_functions(
    *,
    page: int,
    page_size: int,
    search: str | None,
    domain: str | None,
    category: str | None,
    disposition: str | None,
    capability: str | None,
) -> dict[str, Any]:
    if page < 1 or not 1 <= page_size <= 200:
        raise ValueError("Paginação de funções inválida.")
    if domain is not None and domain not in _DOMAIN_LABELS:
        raise ValueError("Domínio KLM inválido.")
    if disposition is not None and disposition not in _FUNCTION_STATES:
        raise ValueError("Estado de função inválido.")
    if capability is not None and not capability.strip():
        raise ValueError("Capacidade KLM inválida.")
    all_items, _ = _indexes()
    normalized_search = search.strip().casefold() if search else ""
    filtered = [
        item
        for item in all_items
        if (domain is None or item["domain"] == domain)
        and (category is None or item["category"] == category)
        and (disposition is None or item["disposition"] == disposition)
        and (capability is None or item["capability"] == capability)
        and (
            not normalized_search
            or normalized_search
            in " ".join(
                str(item[field])
                for field in ("id", "domain", "name", "category", "capability", "summary")
            ).casefold()
        )
    ]
    total = len(filtered)
    start = (page - 1) * page_size
    return {
        "scope": "klm-capability-functions",
        "summary": _function_summary(all_items),
        "filters": {
            "domains": list(_DOMAIN_ORDER),
            "categories": sorted({str(item["category"]) for item in all_items}),
            "dispositions": [
                state
                for state in _FUNCTION_STATE_ORDER
                if any(item["disposition"] == state for item in all_items)
            ],
            "capabilities": sorted({str(item["capability"]) for item in all_items}),
        },
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size) if total else 0,
        "items": list(filtered[start : start + page_size]),
    }


def get_public_function(public_id: str) -> dict[str, Any]:
    if not _FUNCTION_ID_PATTERN.fullmatch(public_id):
        raise NativePublicNotFoundError(public_id)
    items, _ = _indexes()
    for item in items:
        if item["id"] == public_id:
            return dict(item)
    raise NativePublicNotFoundError(public_id)


def resolve_internal_function_id(public_id: str) -> str:
    if not _FUNCTION_ID_PATTERN.fullmatch(public_id):
        raise NativePublicNotFoundError(public_id)
    _, reverse = _indexes()
    try:
        return reverse[public_id]
    except KeyError as error:
        raise NativePublicNotFoundError(public_id) from error


def public_function_runtime(public_id: str, value: Any) -> dict[str, Any]:
    payload = _mapping(value)
    runtime = _mapping(payload.get("runtime") or {})
    state = _mapping(payload.get("state") or {})
    item = get_public_function(public_id)
    modes = [
        mode
        for mode in runtime.get("availableModes", [])
        if mode in {"dry-run", "execute", "configure"}
    ]
    return {
        "function": item,
        "state": {
            "maturity": item["maturity"],
            "available_modes": modes,
            "audit_event_count": max(int(state.get("auditEventCount") or 0), 0),
            "last_activity_at": state.get("lastEventAt"),
            "external_effects_enabled": False,
        },
    }


def public_function_action(value: Any) -> dict[str, Any]:
    payload = _mapping(value)
    status = str(payload.get("status") or "simulated")
    return {
        "status": status,
        "replayed": bool(payload.get("idempotentReplay")),
        "external_effect_executed": False,
        "summary": {
            "succeeded": "Operação KLM concluída.",
            "configured": "Configuração KLM registrada.",
            "simulated": "Simulação KLM concluída sem efeito externo.",
            "blocked_external": "Operação mantida sob controle externo.",
        }.get(status, "Operação KLM processada."),
    }


@lru_cache(maxsize=1)
def public_capability_catalog() -> dict[str, Any]:
    payload = native_capability_registry()
    capabilities: list[dict[str, Any]] = []
    for raw in payload["capabilities"]:
        domain = str(raw.get("domain") or "")
        if domain not in _DOMAIN_LABELS:
            continue
        disposition = str(raw.get("disposition") or "discovered")
        if disposition not in _CAPABILITY_DISPOSITIONS:
            disposition = "discovered"
        mode = _PUBLIC_MODE_BY_INTERNAL_MODE.get(
            str(raw.get("nativeMode") or ""),
            "local_equivalent",
        )
        related_routes: list[str] = []
        for key in ("route", "targetRoute", "sourceRoute", "sourceTargetRoute", "nativeRoute"):
            route = str(raw.get(key) or "")
            if route.startswith("/app/") and route not in related_routes:
                related_routes.append(route)
        capabilities.append(
            {
                "id": str(raw.get("id") or f"{domain}.{raw.get('key') or 'capability'}"),
                "domain": domain,
                "key": str(raw.get("key") or "capability"),
                "label": public_text(raw.get("label") or raw.get("key") or "Capacidade KLM"),
                "category": public_text(raw.get("category") or "geral"),
                "description": public_text(raw.get("description") or "Capacidade nativa KLM."),
                "disposition": disposition,
                "exposure": str(raw.get("currentExposure") or "none"),
                "mode": mode,
                "surface": public_text(raw.get("surface") or "Meu Executivo"),
                "related_routes": related_routes,
                "native_route": str(
                    raw.get("nativeRoute") or f"/app/capacidades/{domain}/{raw.get('key')}"
                ),
                "contract_eligible": bool(raw.get("contractEligible")),
                "verified": bool(
                    raw.get("contractVerified") or raw.get("nativeExperienceVerified")
                ),
                "external_effect_verified": bool(raw.get("externalEffectVerified")),
                "reason": public_text(raw.get("reason") or "Capacidade controlada pelo KLM."),
            }
        )
    by_domain = Counter(item["domain"] for item in capabilities)
    by_disposition = Counter(item["disposition"] for item in capabilities)
    unavailable = {"not_applicable", "blocked_license", "blocked_dependency"}
    qa = cast(Mapping[str, Any], payload.get("qaVerification") or {})
    checks = len(qa.get("proofRuns") or [])
    return {
        "schema_version": str(payload.get("schemaVersion") or "1.0"),
        "scope": "klm-native-capabilities",
        "capabilities": capabilities,
        "summary": {
            "total": len(capabilities),
            "available": sum(item["disposition"] not in unavailable for item in capabilities),
            "verified": sum(bool(item["verified"]) for item in capabilities),
            "external_effect_verified": sum(
                bool(item["external_effect_verified"]) for item in capabilities
            ),
            "by_domain": {domain: by_domain[domain] for domain in _DOMAIN_ORDER},
            "by_disposition": dict(sorted(by_disposition.items())),
        },
        "verification": {
            "state": str(qa.get("state") or "unverified"),
            "reason": public_text(qa.get("reason") or "Verificação técnica ainda não concluída."),
            "checks": checks,
        },
    }


def public_capability_snapshot(
    domain: KLMNativeDomain,
    capability: str,
    value: Any,
) -> dict[str, Any]:
    payload = _mapping(value)
    state = _mapping(payload.get("state") or {})
    commands = [_mapping(item) for item in payload.get("commands") or []]
    audit = [
        _mapping(item) for item in payload.get("auditTrail") or payload.get("audit_trail") or []
    ]
    return {
        "state": {
            "domain": domain,
            "capability": capability,
            "configured": bool(state.get("configured")),
            "configuration": sanitize_public_payload(state.get("configuration") or {}),
            "config_version": max(int(state.get("configVersion") or 0), 0),
            "execution_mode": str(state.get("executionMode") or "disabled"),
            "updated_at": state.get("updatedAt"),
        },
        "commands": [public_capability_command(domain, capability, item) for item in commands],
        "audit_trail": [
            {
                "id": str(item.get("id") or ""),
                "action": str(item.get("action") or "event"),
                "summary": _AUDIT_SUMMARIES.get(
                    str(item.get("action") or ""),
                    public_text(item.get("summary") or "Evento da capacidade registrado."),
                ),
                "occurred_at": item.get("occurredAt") or item.get("occurred_at"),
            }
            for item in audit
        ],
    }


def public_capability_command(
    domain: KLMNativeDomain,
    capability: str,
    value: Any,
) -> dict[str, Any]:
    payload = _mapping(value)
    return {
        "id": str(payload.get("id") or ""),
        "domain": domain,
        "capability": capability,
        "command_type": str(payload.get("commandType") or payload.get("command_type") or "command"),
        "status": str(payload.get("status") or "received"),
        "effect_state": str(
            payload.get("effectState") or payload.get("effect_state") or "not_invoked"
        ),
        "is_success": bool(payload.get("isSuccess") or payload.get("is_success")),
        "is_terminal": bool(payload.get("isTerminal") or payload.get("is_terminal")),
        "requires_reconciliation": bool(
            payload.get("requiresReconciliation") or payload.get("requires_reconciliation")
        ),
        "deduplicated": bool(payload.get("deduplicated")),
        "result": sanitize_public_payload(payload.get("result") or {}),
        "error_code": public_text(payload.get("errorCode") or payload.get("error_code")) or None,
        "error_message": public_text(payload.get("errorMessage") or payload.get("error_message"))
        or None,
        "external_network_allowed": bool(
            payload.get("externalNetworkAllowed") or payload.get("external_network_allowed")
        ),
        "created_at": payload.get("createdAt") or payload.get("created_at"),
        "updated_at": payload.get("updatedAt") or payload.get("updated_at"),
    }


def public_capability_document(
    domain: KLMNativeDomain,
    capability: str,
    value: Any,
) -> dict[str, Any]:
    payload = _mapping(value)
    domain_data = _mapping(payload.pop("provider", {}) or {})
    payload.pop("source", None)
    navigation = _mapping(payload.pop("motor", {}) or {})
    runtime = _mapping(payload.pop("native", {}) or {})
    payload["domain"] = {
        "id": domain,
        "label": public_text(domain_data.get("label") or _DOMAIN_LABELS[domain]),
    }
    if navigation:
        payload["navigation"] = {
            "label": public_text(navigation.get("label") or "Abrir no KLM"),
            "url": str(navigation.get("url") or f"/app/capacidades/{domain}/{capability}"),
            "available": bool(navigation.get("available", True)),
        }
    if runtime:
        payload["availability"] = {
            "state": public_text(runtime.get("externalExecutionState") or "local_only"),
            "reconciled": bool(runtime.get("roundTrip")),
            "diagnosticsAvailable": bool(runtime.get("diagnosticsAvailable")),
        }
    sanitized = sanitize_public_payload(payload)
    if not isinstance(sanitized, dict):
        raise TypeError("O documento público da capacidade é inválido.")
    health = _mapping(sanitized.get("health") or {})
    access = _mapping(sanitized.get("access") or {})
    domain_row = _mapping(sanitized.get("domain") or {})
    capability_row = _mapping(sanitized.get("capability") or {})
    return {
        "schema_version": str(sanitized.get("schemaVersion") or "2.0"),
        "domain": {
            "id": domain,
            "label": public_text(domain_row.get("label") or _DOMAIN_LABELS[domain]),
        },
        "capability": {
            "id": capability,
            "label": public_text(capability_row.get("label") or _humanize(capability)),
            "description": public_text(capability_row.get("description")) or None,
            "category": public_text(capability_row.get("category")) or None,
        },
        "health": {
            "state": str(health.get("state") or health.get("status") or "unknown"),
            "label": public_text(health.get("label")) or None,
            "message": public_text(health.get("message") or health.get("detail")) or None,
            "last_sync_at": health.get("lastSyncAt") or health.get("last_sync_at"),
            "latency_ms": health.get("latencyMs") or health.get("latency_ms"),
        },
        "access": {
            "state": str(access.get("state") or "allowed"),
            "reason": public_text(access.get("reason")) or None,
        },
        "version": sanitized.get("version") or "0",
        "updated_at": sanitized.get("updatedAt") or sanitized.get("updated_at"),
        "kpis": [_public_document_kpi(item) for item in _rows(sanitized.get("kpis"))],
        "tabs": [_public_document_tab(item) for item in _rows(sanitized.get("tabs"))],
        "actions": [_public_document_action(item) for item in _rows(sanitized.get("actions"))],
        "audit": [_public_document_audit(item) for item in _rows(sanitized.get("audit"))],
        "navigation": sanitized.get("navigation"),
        "availability": sanitized.get("availability"),
    }


def _public_document_column(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "key": str(value.get("key") or "value"),
        "label": public_text(value.get("label") or value.get("key") or "Valor"),
        "format": str(value.get("format")) if value.get("format") else None,
        "width": str(value.get("width")) if value.get("width") else None,
        "hidden_on_mobile": bool(value.get("hiddenOnMobile") or value.get("hidden_on_mobile")),
    }


def _public_document_section(value: Mapping[str, Any], index: int) -> dict[str, Any]:
    sanitized = sanitize_public_payload(value)
    row = _mapping(sanitized)
    columns = [_public_document_column(item) for item in _rows(row.get("columns"))]
    fields = [_public_document_column(item) for item in _rows(row.get("fields"))]
    points = [
        {
            "id": str(item.get("id") or f"point-{point_index}"),
            "label": public_text(item.get("label") or item.get("id") or "Valor"),
            "value": _number(item.get("value")),
            "display_value": public_text(item.get("displayValue") or item.get("display_value"))
            or None,
            "format": str(item.get("format")) if item.get("format") else None,
            "detail": public_text(item.get("detail")) or None,
        }
        for point_index, item in enumerate(_rows(row.get("points")))
    ]
    return {
        "id": str(row.get("id") or f"section-{index}"),
        "type": str(row.get("type") or "structured"),
        "title": public_text(row.get("title") or row.get("label") or "Dados KLM"),
        "description": public_text(row.get("description")) or None,
        "action_ids": [str(item) for item in row.get("actionIds") or row.get("action_ids") or []],
        "columns": columns,
        "rows": row.get("rows") if isinstance(row.get("rows"), list) else [],
        "data": row.get("data"),
        "fields": fields,
        "items": row.get("items") if isinstance(row.get("items"), list) else [],
        "points": points,
        "tone": str(row.get("tone")) if row.get("tone") else None,
        "body": public_text(row.get("body")) or None,
        "id_field": str(row.get("idField") or row.get("id_field")) or None,
        "searchable": bool(row.get("searchable")),
        "search_keys": [
            str(item) for item in row.get("searchKeys") or row.get("search_keys") or []
        ],
        "page_size": _integer(row.get("pageSize") or row.get("page_size")) or None,
        "row_action_ids": [
            str(item) for item in row.get("rowActionIds") or row.get("row_action_ids") or []
        ],
        "title_field": str(row.get("titleField") or row.get("title_field")) or None,
        "description_field": str(row.get("descriptionField") or row.get("description_field"))
        or None,
        "status_field": str(row.get("statusField") or row.get("status_field")) or None,
    }


def _public_document_tab(value: Mapping[str, Any]) -> dict[str, Any]:
    tab_id = str(value.get("id") or "overview")
    return {
        "id": tab_id,
        "label": public_text(value.get("label") or value.get("title") or "Visão geral"),
        "description": public_text(value.get("description")) or None,
        "count": _integer(value.get("count")) if value.get("count") is not None else None,
        "sections": [
            _public_document_section(item, index)
            for index, item in enumerate(_rows(value.get("sections")))
        ],
    }


def _public_document_kpi(value: Mapping[str, Any]) -> dict[str, Any]:
    key = str(value.get("id") or value.get("key") or "metric")
    trend = str(value.get("trend") or "neutral")
    return {
        "id": key,
        "label": public_text(value.get("label") or value.get("title") or _humanize(key)),
        "value": sanitize_public_payload(value.get("value")),
        "display_value": public_text(value.get("displayValue") or value.get("display_value"))
        or None,
        "format": str(value.get("format")) if value.get("format") else None,
        "delta": value.get("delta") if isinstance(value.get("delta"), (str, int, float)) else None,
        "trend": trend if trend in {"up", "down", "neutral"} else "neutral",
        "description": public_text(value.get("description")) or None,
    }


def _public_document_action(value: Mapping[str, Any]) -> dict[str, Any]:
    action_id = str(value.get("id") or value.get("command") or "action")
    permission = _mapping(value.get("permission") or {}) if value.get("permission") else None
    gate = _mapping(value.get("gate") or {}) if value.get("gate") else None
    confirmation = _mapping(value.get("confirmation") or {}) if value.get("confirmation") else None
    fields = []
    for field in _rows(value.get("fields")):
        condition = _mapping(field.get("visibleWhen") or field.get("visible_when") or {})
        fields.append(
            {
                "name": str(field.get("name") or "value"),
                "label": public_text(field.get("label") or field.get("name") or "Valor"),
                "type": str(field.get("type") or "text"),
                "description": public_text(field.get("description")) or None,
                "placeholder": public_text(field.get("placeholder")) or None,
                "required": bool(field.get("required")),
                "read_only": bool(field.get("readOnly") or field.get("read_only")),
                "default_value": sanitize_public_payload(
                    field.get("defaultValue") or field.get("default_value")
                ),
                "options": [
                    {
                        "value": sanitize_public_payload(option.get("value")),
                        "label": public_text(option.get("label") or option.get("value")),
                        "description": public_text(option.get("description")) or None,
                    }
                    for option in _rows(field.get("options"))
                ],
                "min": field.get("min"),
                "max": field.get("max"),
                "min_length": field.get("minLength") or field.get("min_length"),
                "max_length": field.get("maxLength") or field.get("max_length"),
                "pattern": str(field.get("pattern")) if field.get("pattern") else None,
                "context_key": str(field.get("sourceKey") or field.get("contextKey") or "") or None,
                "one_of_group": str(field.get("oneOfGroup") or field.get("one_of_group") or "")
                or None,
                "visible_when": {
                    "field": str(condition.get("field")),
                    "equals": sanitize_public_payload(condition.get("equals")),
                    "not_equals": sanitize_public_payload(
                        condition.get("notEquals") or condition.get("not_equals")
                    ),
                }
                if condition.get("field")
                else None,
            }
        )
    return {
        "id": action_id,
        "label": public_text(value.get("label") or _humanize(action_id)),
        "description": public_text(value.get("description")) or None,
        "intent": str(value.get("intent") or "secondary"),
        "method": str(value.get("method") or "POST"),
        "execution": str(value.get("execution") or "local"),
        "fields": fields,
        "permission": {
            "allowed": bool(permission.get("allowed")),
            "reason": public_text(permission.get("reason")) or None,
            "required": [
                {"module": str(item.get("module")), "action": str(item.get("action"))}
                for item in _rows(permission.get("required"))
            ],
        }
        if permission is not None
        else None,
        "gate": {
            "state": str(gate.get("state") or "open"),
            "reason": public_text(gate.get("reason")) or None,
            "allow_dry_run": bool(gate.get("allowDryRun") or gate.get("allow_dry_run")),
            "allow_configure": bool(gate.get("allowConfigure") or gate.get("allow_configure")),
        }
        if gate is not None
        else None,
        "supports_dry_run": bool(value.get("supportsDryRun") or value.get("supports_dry_run")),
        "confirmation": {
            "title": public_text(confirmation.get("title")) or None,
            "body": public_text(confirmation.get("body")) or None,
            "confirm_label": public_text(
                confirmation.get("confirmLabel") or confirmation.get("confirm_label")
            )
            or None,
        }
        if confirmation is not None
        else None,
    }


def _public_document_audit(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(value.get("id") or "event"),
        "title": public_text(value.get("title") or "Evento KLM"),
        "description": public_text(value.get("description")) or None,
        "timestamp": value.get("timestamp"),
        "actor": public_text(value.get("actor")) or None,
        "status": public_text(value.get("status")) or None,
    }


def public_capability_action(
    domain: KLMNativeDomain,
    capability: str,
    value: Any,
) -> dict[str, Any]:
    payload = _mapping(value)
    document = payload.get("document")
    return {
        "status": str(payload.get("status") or "processed"),
        "message": public_text(payload.get("message") or "Operação KLM processada."),
        "version": payload.get("version") or "0",
        "document": public_capability_document(domain, capability, document) if document else None,
        "result": sanitize_public_payload(payload.get("result") or {}),
        "replayed": bool(payload.get("idempotentReplay")),
        "refresh_pending": bool(payload.get("refreshPending")),
    }


def _communication_projection(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    return {
        "total_messages": _integer(row.get("totalMessages")),
        "projected_messages": _integer(row.get("projectedMessages")),
        "received_messages": _integer(row.get("receivedMessages")),
        "pending_messages": _integer(row.get("pendingMessages")),
        "failed_messages": _integer(row.get("failedMessages")),
        "last_synced_at": row.get("lastSyncedAt"),
        "external_channel_delivery_enabled": False,
    }


def _named_numeric(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    row = _mapping(value)
    return {"id": _integer(row.get("id")), "name": public_text(row.get("name"))}


def public_communication_thread(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    conversation = _mapping(row.get("conversation") or {}) if row.get("conversation") else None
    return {
        "linked": bool(row.get("linked")),
        "thread_id": str(row.get("threadId") or ""),
        "conversation_id": str(row.get("conversationId")) if row.get("conversationId") else None,
        "conversation": {
            "id": str(conversation.get("id") or ""),
            "status": str(conversation.get("status") or "open"),
            "priority": str(conversation.get("priority")) if conversation.get("priority") else None,
            "labels": [public_text(item) for item in conversation.get("labels") or []],
            "created_at": conversation.get("created_at") or conversation.get("createdAt"),
            "updated_at": conversation.get("updated_at") or conversation.get("updatedAt"),
            "assignee": _named_numeric(conversation.get("assignee")),
            "team": _named_numeric(conversation.get("team")),
            "inbox": _named_numeric(conversation.get("inbox")),
            "external_network_executed": False,
        }
        if conversation is not None
        else None,
        "inboxes": [
            item for item in (_named_numeric(child) for child in row.get("inboxes") or []) if item
        ],
        "teams": [
            item for item in (_named_numeric(child) for child in row.get("teams") or []) if item
        ],
        "labels": [
            {"id": _integer(item.get("id")), "title": public_text(item.get("title"))}
            for item in _rows(row.get("labels"))
        ],
        "agents": [
            {
                "id": _integer(item.get("id")),
                "name": public_text(item.get("name")),
                "role": public_text(item.get("role")),
                "availability": str(item.get("availability") or "offline"),
            }
            for item in _rows(row.get("agents"))
        ],
        "canned_responses": [
            {
                "id": _integer(item.get("id")),
                "short_code": public_text(item.get("short_code") or item.get("shortCode")),
                "content": public_text(item.get("content")),
            }
            for item in _rows(row.get("cannedResponses"))
        ],
        "projection": _communication_projection(row.get("projection") or {}),
    }


def public_communication_sync(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    return {
        **_communication_projection(row),
        "status": "synced",
        "thread_id": str(row.get("threadId") or ""),
        "conversation_id": str(row.get("conversationId") or ""),
        "projected_now": _integer(row.get("projectedNow")),
        "contact_created": bool(row.get("contactCreated")),
        "rate_limited": bool(row.get("rateLimited")),
    }


def public_collaboration_thread(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    room = _mapping(row.get("room") or {}) if row.get("room") else None
    messages = []
    for item in _rows(row.get("messages")):
        author = _mapping(item.get("user") or {})
        messages.append(
            {
                "id": str(item.get("_id") or item.get("id") or ""),
                "body": public_text(item.get("msg") or item.get("body")),
                "author": {
                    "id": str(author.get("_id") or author.get("id") or "system"),
                    "name": public_text(author.get("name") or "Sistema KLM"),
                    "username": public_text(author.get("username") or "sistema"),
                },
                "occurred_at": item.get("ts") or item.get("occurredAt"),
                "attachments": [
                    {
                        "name": public_text(child.get("title") or child.get("name") or "Arquivo"),
                        "media_type": public_text(
                            child.get("type") or child.get("format") or "arquivo"
                        ),
                    }
                    for child in _rows(item.get("attachments"))
                ],
                "reactions": [
                    {
                        "emoji": public_text(child.get("emoji") or "reação"),
                        "count": _integer(child.get("count")),
                    }
                    for child in _rows(item.get("reactions"))
                ],
                "reply_count": _integer(item.get("tcount") or item.get("replyCount")),
            }
        )
    return {
        "linked": bool(row.get("linked")),
        "thread_id": str(row.get("threadId") or ""),
        "room_id": str(row.get("roomId")) if row.get("roomId") else None,
        "room_type": str(row.get("roomType")) if row.get("roomType") else None,
        "room": {
            "id": str(room.get("_id") or room.get("id") or ""),
            "name": public_text(room.get("name")),
            "display_name": public_text(
                room.get("fname") or room.get("displayName") or room.get("name")
            ),
        }
        if room is not None
        else None,
        "members": [
            {
                "id": str(item.get("_id") or item.get("id") or ""),
                "name": public_text(item.get("name")),
                "username": public_text(item.get("username")),
                "status": str(item.get("status") or "offline"),
                "role": public_text(item.get("role")),
            }
            for item in _rows(row.get("members"))
        ],
        "messages": messages,
        "discussions": [
            {
                "id": str(item.get("_id") or item.get("id") or ""),
                "name": public_text(item.get("fname") or item.get("name") or "Discussão"),
                "message_count": _integer(item.get("msgs") or item.get("messageCount")),
                "created_at": item.get("ts") or item.get("createdAt"),
                "updated_at": item.get("lm") or item.get("updatedAt"),
            }
            for item in _rows(row.get("discussions"))
        ],
        "sensitive_data_hidden": bool(row.get("sensitiveDataHidden")),
    }


def public_collaboration_sync(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    return {
        "status": "synced",
        "thread_id": str(row.get("threadId") or ""),
        "room_id": str(row.get("roomId") or ""),
        "messages_seen": _integer(row.get("messagesSeen")),
        "messages_projected": _integer(row.get("messagesProjected")),
        "external_network_executed": False,
    }


def public_automation_context(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    performance = _mapping(row.get("performance") or {})
    people = _mapping(row.get("people") or {})
    people_stats = _mapping(row.get("peopleStats") or {})
    analytics = _mapping(row.get("analytics") or {})
    knowledge = _mapping(row.get("knowledge") or {})
    knowledge_rows = _rows(knowledge.get("knowledge"))
    hidden = bool(row.get("sensitiveDataHidden"))
    return {
        "linked": bool(row.get("linked")),
        "agents": [
            {
                "id": str(item.get("id") or ""),
                "name": public_text(item.get("name")),
                "display_name": public_text(item.get("display_name") or item.get("displayName")),
                "description": public_text(item.get("description")),
                "is_active": bool(item.get("is_active") or item.get("isActive")),
                "state": str(item.get("state") or "unknown"),
                "quality_score": _number(item.get("quality_score") or item.get("qualityScore")),
                "queue_size": _integer(item.get("queue_size") or item.get("queueSize")),
                "transfer_to_human": bool(
                    item.get("transfer_to_human") or item.get("transferToHuman")
                ),
            }
            for item in _rows(row.get("agents"))
        ],
        "workflows": [
            {
                "id": str(item.get("id") or ""),
                "name": public_text(item.get("name")),
                "description": public_text(item.get("description")) or None,
                "status": str(item.get("status") or "draft"),
                "revision": _integer(item.get("revision")),
                "node_count": _integer(item.get("node_count") or item.get("nodeCount")),
                "connection_count": _integer(
                    item.get("connection_count") or item.get("connectionCount")
                ),
                "run_count": _integer(item.get("run_count") or item.get("runCount")),
            }
            for item in _rows(row.get("workflows"))
        ],
        "analytics": {
            "runs": _integer(analytics.get("runs")),
            "by_status": {
                str(key): _integer(child)
                for key, child in _mapping(analytics.get("by_status") or {}).items()
            },
            "external_actions_executed": 0,
        },
        "performance": [
            {
                "id": str(item.get("id") or ""),
                "name": public_text(item.get("name")),
                "quality_score": _number(item.get("quality_score") or item.get("qualityScore")),
                "queue_size": _integer(item.get("queue_size") or item.get("queueSize")),
                "total_chats": _integer(item.get("total_chats") or item.get("totalChats")),
                "completed_runs": _integer(item.get("completed_runs") or item.get("completedRuns")),
                "failed_runs": _integer(item.get("failed_runs") or item.get("failedRuns")),
                "waiting_human_runs": _integer(
                    item.get("waiting_human_runs") or item.get("waitingHumanRuns")
                ),
            }
            for item in _rows(performance.get("bot_agents"))
        ],
        "people": {
            "items": [
                {
                    "id": str(item.get("id") or ""),
                    "name": "Pessoa restrita" if hidden else public_text(item.get("name")),
                    "stage": str(item.get("stage") or "sem_caso"),
                    "active_matters": _integer(
                        item.get("active_matters") or item.get("activeMatters")
                    ),
                }
                for item in _rows(people.get("items"))
            ],
            "total": _integer(people.get("total")),
        },
        "people_stats": {
            "total_people": _integer(people_stats.get("total_people")),
            "active_matters": _integer(people_stats.get("active_matters")),
            "new_leads_7d": _integer(people_stats.get("new_leads_7d")),
        },
        "tools": [
            {
                "id": _integer(item.get("id")),
                "name": public_text(item.get("name")),
                "status": str(item.get("status") or "local_ready"),
                "external": False,
            }
            for item in _rows(row.get("mcpTools"))
        ],
        "groups": [
            {
                "id": str(item.get("id") or ""),
                "name": public_text(item.get("name")),
                "member_count": len(item.get("agents") or []),
            }
            for item in _rows(row.get("groups"))
        ],
        "notifications": [
            {
                "id": str(item.get("id") or ""),
                "title": public_text(item.get("title")),
                "message": "Execução requer acompanhamento.",
                "created_at": item.get("created_at") or item.get("createdAt"),
            }
            for item in _rows(row.get("notifications"))
        ],
        "knowledge": {
            "items": [
                {
                    "id": str(item.get("id") or ""),
                    "name": "Documento restrito" if hidden else public_text(item.get("name")),
                    "document_type": public_text(
                        item.get("type") or item.get("source_type") or "documento"
                    ),
                    "title": "Documento restrito"
                    if hidden
                    else public_text(item.get("title") or item.get("name")),
                    "summary": "Conteúdo oculto pela permissão de dados."
                    if hidden
                    else public_text(item.get("summary")),
                    "matter_id": str(item.get("matter_id")) if item.get("matter_id") else None,
                    "tags": [public_text(tag) for tag in item.get("tags") or []],
                }
                for item in knowledge_rows
            ],
            "total": len(knowledge_rows),
            "queue_size": len(knowledge.get("queueItems") or []),
        },
        "time_range": str(row.get("timeRange") or "7d"),
        "sensitive_data_hidden": hidden,
        "external_execution_enabled": False,
    }


def public_automation_sync(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    return {
        key: _integer(row.get(key))
        for key in ("created", "updated", "adopted", "conflicts", "seen")
    } | {"status": "synced"}


def public_flow_context(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    return {
        "linked": bool(row.get("linked")),
        "flow_id": str(row.get("flowId") or ""),
        "revision": _integer(row.get("revision")),
        "workflow_status": str(row.get("workflowStatus")) if row.get("workflowStatus") else None,
        "node_count": _integer(row.get("nodeCount")),
        "connection_count": _integer(row.get("connectionCount")),
        "digest_matches": bool(row.get("digestMatches")),
        "graph_matches": bool(row.get("graphMatches")),
        "stale_link": bool(row.get("staleLink")),
        "external_execution_enabled": False,
    }


def public_flow_sync(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    action = str(row.get("graphAction") or "unchanged")
    return {
        "status": "synced",
        "flow_id": str(row.get("flowId") or ""),
        "graph_action": action if action in {"replaced", "unchanged"} else "unchanged",
        "node_count": _integer(row.get("nodeCount")),
        "connection_count": _integer(row.get("connectionCount")),
        "revision": _integer(row.get("revision")),
        "external_execution_enabled": False,
    }


def _crm_projection(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    counts = _mapping(row.get("byEntityType") or {})
    return {
        "linked": bool(row.get("linked")),
        "total_records": _integer(row.get("totalLinks")),
        "available_records": _integer(row.get("syncedLinks")),
        "failed_records": _integer(row.get("failedLinks")),
        "stale_records": _integer(row.get("staleLinks")),
        "by_entity_type": {
            "contacts": _integer(counts.get("contact")),
            "leads": _integer(counts.get("lead")),
            "opportunities": _integer(counts.get("opportunity")),
            "cases": _integer(counts.get("case")),
            "tasks": _integer(counts.get("task")),
            "finance_entries": _integer(counts.get("finance_entry")),
        },
        "updated_at": row.get("lastSyncedAt"),
        "external_execution_enabled": False,
    }


def _crm_item(value: Mapping[str, Any], *, record: bool = False) -> dict[str, Any]:
    item = {
        "id": str(value.get("id") or ""),
        "entity_type": str(value.get("entityType") or "record"),
        "name": public_text(value.get("name") or "Registro KLM"),
        "stage": str(value.get("stage")) if value.get("stage") else None,
        "status": str(value.get("status")) if value.get("status") else None,
        "priority": str(value.get("priority")) if value.get("priority") else None,
        "amount": _number(value.get("amount")) if value.get("amount") is not None else None,
        "currency": str(value.get("currency") or "BRL"),
        "probability": _integer(value.get("probability"))
        if value.get("probability") is not None
        else None,
        "days_in_stage": _integer(value.get("daysInStage"))
        if value.get("daysInStage") is not None
        else None,
        "aging_status": str(value.get("agingStatus")) if value.get("agingStatus") else None,
        "due_date": str(value.get("dueDate")) if value.get("dueDate") else None,
        "next_follow_up": public_text(value.get("nextFollowUp")) or None,
        "overdue": bool(value.get("overdue")),
        "sla_breached": bool(value.get("slaBreached")),
        "escalation_count": _integer(value.get("escalationCount")),
    }
    if record:
        item |= {
            "sync_state": str(value.get("syncState") or "synced"),
            "last_synced_at": value.get("lastSyncedAt"),
        }
    return item


def _crm_columns(value: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": str(row.get("id") or "column"),
            "name": public_text(row.get("name") or "Coluna"),
            "order": _integer(row.get("order")),
            "color": str(row.get("color")) if row.get("color") else None,
            "stage_type": str(row.get("stageType")) if row.get("stageType") else None,
            "wip_limit": _integer(row.get("wipLimit")) if row.get("wipLimit") is not None else None,
            "item_count": _integer(row.get("itemCount")),
            "items": [_crm_item(item) for item in _rows(row.get("items"))],
        }
        for row in _rows(value)
    ]


def public_crm_funnel(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    revenue = _mapping(row.get("revenue") or {})
    return {
        "projection": _crm_projection(row.get("projection") or {}),
        "counts": {
            key: _integer(_mapping(row.get("counts") or {}).get(key))
            for key in ("accounts", "contacts", "leads", "opportunities")
        },
        "urgent_counts": {
            key: _integer(_mapping(row.get("urgentCounts") or {}).get(key))
            for key in ("overdue_tasks", "followups_today", "hot_leads")
        },
        "pipeline": [
            {
                "id": str(item.get("id")),
                "label": public_text(item.get("label")),
                "count": _integer(item.get("count")),
                "value": _number(item.get("value")),
            }
            for item in _rows(row.get("pipeline"))
        ],
        "revenue": {
            "total_pipeline": _number(revenue.get("total_pipeline")),
            "weighted_pipeline": _number(revenue.get("weighted_pipeline")),
            "conversion_rate": _number(revenue.get("conversion_rate")),
            "currency": str(revenue.get("currency") or "BRL"),
        },
        "opportunity_columns": _crm_columns(row.get("opportunityColumns")),
        "lead_columns": _crm_columns(row.get("leadColumns")),
        "case_columns": _crm_columns(row.get("caseColumns")),
        "goals": [
            {
                "id": str(item.get("id")),
                "name": public_text(item.get("name")),
                "target": _number(item.get("target")),
                "achieved": _number(item.get("achieved")),
                "percent": _number(item.get("percent")),
                "active": bool(item.get("active")),
            }
            for item in _rows(row.get("goals"))
        ],
        "aging_config": [
            {
                "stage": str(item.get("stage")),
                "expected_days": _integer(item.get("expectedDays")),
                "warning_days": _integer(item.get("warningDays"))
                if item.get("warningDays") is not None
                else None,
            }
            for item in _rows(row.get("agingConfig"))
        ],
        "notifications_unread": _integer(row.get("notificationsUnread")),
        "sensitive_data_hidden": bool(row.get("sensitiveDataHidden")),
    }


def public_crm_matter(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    return {
        "projection": _crm_projection(row.get("projection") or {}),
        "matter_id": str(row.get("matterId") or ""),
        "records": [_crm_item(item, record=True) for item in _rows(row.get("records"))],
        "sensitive_data_hidden": bool(row.get("sensitiveDataHidden")),
    }


def public_crm_work(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    return {
        "projection": _crm_projection(row.get("projection") or {}),
        "task_columns": _crm_columns(row.get("taskColumns")),
        "tasks": [_crm_item(item) for item in _rows(row.get("tasks"))],
        "boards": [
            {
                "id": str(item.get("id")),
                "name": public_text(item.get("name")),
                "description": public_text(item.get("description")) or None,
                "column_count": _integer(item.get("columnCount")),
            }
            for item in _rows(row.get("boards"))
        ],
        "notifications_unread": _integer(row.get("notificationsUnread")),
        "sensitive_data_hidden": bool(row.get("sensitiveDataHidden")),
    }


def _crm_finance_item(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(value.get("id") or ""),
        "entity_type": str(value.get("entityType") or "record"),
        "name": public_text(value.get("name")),
        "status": str(value.get("status")) if value.get("status") else None,
        "amount": _number(value.get("amount")) if value.get("amount") is not None else None,
        "amount_due": _number(value.get("amountDue"))
        if value.get("amountDue") is not None
        else None,
        "currency": str(value.get("currency") or "BRL"),
        "issue_date": value.get("issueDate"),
        "due_date": value.get("dueDate"),
        "valid_until": value.get("validUntil"),
        "category": public_text(value.get("category")) or None,
        "active": bool(value.get("active")),
    }


def public_crm_finance(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    summary = _mapping(row.get("summary") or {})
    overdue = _mapping(row.get("overdue") or {})
    activity = _mapping(row.get("recentActivity") or {})
    estimates = _mapping(row.get("estimateSummary") or {})
    return {
        "projection": _crm_projection(row.get("projection") or {}),
        "summary": {
            "total_invoiced": _number(summary.get("total_invoiced")),
            "total_paid": _number(summary.get("total_paid")),
            "total_due": _number(summary.get("total_due")),
            "currency": str(summary.get("currency") or "BRL"),
        },
        "status_counts": {
            str(key): _integer(child)
            for key, child in _mapping(row.get("statusCounts") or {}).items()
        },
        "overdue": {
            "count": _integer(overdue.get("count")),
            "amount": _number(overdue.get("amount")),
        },
        "recent_activity": {
            "entries": _integer(activity.get("entries")),
            "paid": _integer(activity.get("paid")),
            "pending": _integer(activity.get("pending")),
        },
        "estimate_summary": {
            "count": _integer(estimates.get("count")),
            "active": _integer(estimates.get("active")),
        },
        "invoices": [_crm_finance_item(item) for item in _rows(row.get("invoices"))],
        "estimates": [_crm_finance_item(item) for item in _rows(row.get("estimates"))],
        "products": [_crm_finance_item(item) for item in _rows(row.get("products"))],
        "aging": [
            {
                "bucket": str(item.get("bucket")),
                "count": _integer(item.get("count")),
                "amount": _number(item.get("amount")),
            }
            for item in _rows(row.get("aging"))
        ],
        "revenue_series": [
            {
                "period": str(item.get("period")) if item.get("period") else None,
                "revenue": _number(item.get("revenue")),
                "count": _integer(item.get("count")),
            }
            for item in _rows(row.get("revenueSeries"))
        ],
        "notifications_unread": _integer(row.get("notificationsUnread")),
        "sensitive_data_hidden": bool(row.get("sensitiveDataHidden")),
    }


def validate_public_query(value: Mapping[str, Any]) -> dict[str, Any]:
    for key in value:
        if _private_key(str(key), drop_internal_metadata=True):
            raise ValueError(f"Filtro público não suportado: {key}.")
    return dict(value)


__all__ = [
    "NativePublicNotFoundError",
    "NativePublicPayloadError",
    "get_public_function",
    "list_public_functions",
    "public_capability_action",
    "public_capability_catalog",
    "public_capability_command",
    "public_capability_document",
    "public_capability_snapshot",
    "public_automation_context",
    "public_automation_sync",
    "public_collaboration_sync",
    "public_collaboration_thread",
    "public_communication_sync",
    "public_communication_thread",
    "public_crm_finance",
    "public_crm_funnel",
    "public_crm_matter",
    "public_crm_work",
    "public_flow_context",
    "public_flow_sync",
    "public_function_action",
    "public_function_runtime",
    "public_text",
    "resolve_internal_function_id",
    "sanitize_public_payload",
    "validate_public_query",
]
