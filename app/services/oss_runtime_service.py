"""Truthful local runtime and integration projection for preserved OSS products."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.services.bottlecrm_client import BottleCRMClient, BottleCRMClientError
from app.services.chattermate_client import ChatterMateClient, ChatterMateClientError
from app.services.rocketchat_client import RocketChatClient, RocketChatClientError

_ROOT = Path(__file__).resolve().parents[2]
_LOCK_PATH = _ROOT / "docs" / "oss-upstreams.lock.json"
_UPSTREAM_ROOT = _ROOT / ".vendor" / "upstreams"
_READY_STATUS_CACHE_TTL_SECONDS = 10.0
_DEGRADED_STATUS_CACHE_TTL_SECONDS = 1.0
_BRIDGE_AUTH_TIMEOUT_FLOOR_SECONDS = {
    # Rocket.Chat may need to replace an expired session before /me can answer.
    # The generic 1.2 s health budget caused truthful runtimes to flap under load.
    "rocketchat": 3.0,
}
_status_cache: tuple[float, dict[str, Any]] | None = None
_status_cache_lock = asyncio.Lock()
_PROVIDERS_WITH_NATIVE_COMMAND_DISPATCHER = frozenset({"chatwoot"})
_UNHEALTHY_PAYLOAD_STATES = frozenset(
    {
        "down",
        "error",
        "failed",
        "failing",
        "offline",
        "stopped",
        "unavailable",
        "unhealthy",
    }
)

_CATALOG: dict[str, dict[str, Any]] = {
    "chatwoot": {
        "name": "Chatwoot",
        "role": "Atendimento omnichannel, operação comercial e suporte",
        "version": "v4.15.1",
        "capabilities": [
            "caixas de entrada",
            "contatos",
            "conversas",
            "notas privadas",
            "equipes",
            "rótulos",
            "macros",
            "automações",
            "campanhas compatíveis",
            "central de ajuda",
            "relatórios",
            "SLA",
            "CSAT",
            "webhooks",
            "REST API",
            "tempo real",
            "anexos",
        ],
        "preserved_features": [
            "Interface Community completa",
            "caixa unificada",
            "contatos e segmentos",
            "atribuição e equipes",
            "respostas prontas e macros",
            "automações comunitárias",
            "relatórios de atendimento",
            "central de ajuda",
            "API pública e webhooks",
        ],
        "offline_capabilities": [
            "Inbox API local",
            "contatos e conversas",
            "notas internas",
            "macros",
            "automações locais",
            "relatórios",
            "webhooks assinados",
            "sincronização KLM",
        ],
        "gated_capabilities": [
            "WhatsApp oficial",
            "e-mail real",
            "Instagram/Facebook",
            "SMS/telefonia",
            "push móvel",
            "armazenamento externo",
            "campanhas por canal externo",
        ],
        "embeddable": False,
    },
    "rocketchat": {
        "name": "Rocket.Chat",
        "role": "Colaboração interna entre pessoas, departamentos e agentes",
        "version": "6.13.1",
        "reference_version": "8.6.1",
        "runtime_policy": "local_compatibility",
        "runtime_notice": (
            "Runtime comunitário local funcional; produção exige build FOSS atual auditado "
            "ou licença aprovada."
        ),
        "capabilities": [
            "canais públicos",
            "grupos privados por caso",
            "mensagens diretas",
            "threads",
            "discussões",
            "menções",
            "busca",
            "presença",
            "arquivos",
            "reações",
            "pins",
            "estrelas",
            "papéis",
            "REST API",
            "Realtime API",
            "webhooks",
            "slash commands",
        ],
        "preserved_features": [
            "Interface Community completa",
            "canais, grupos e mensagens diretas",
            "threads",
            "busca e histórico",
            "reações e menções",
            "uploads locais",
            "papéis básicos",
            "REST, tempo real e webhooks",
            "Custom OAuth comunitário",
        ],
        "offline_capabilities": [
            "dez canais jurídicos",
            "salas privadas por caso",
            "notas internas KLM",
            "webhook bidirecional",
            "busca",
            "arquivos locais",
            "reações e threads",
        ],
        "gated_capabilities": [
            "push em nuvem",
            "videoconferência externa",
            "marketplace",
            "auditoria enterprise",
            "DLP e retenção enterprise",
            "mapeamento avançado de grupos via OAuth",
        ],
        "embeddable": False,
    },
    "chattermate": {
        "name": "ChatterMate",
        "role": "Agentes, handoff e automações visuais com execução em tempo real",
        "version": "v1.0.14",
        "capabilities": [
            "agentes",
            "fluxos visuais",
            "preview",
            "execução em tempo real",
            "handoff humano",
            "inbox",
            "widgets",
            "base de conhecimento",
            "FAQ",
            "help center",
            "analytics",
            "usuários",
            "RBAC",
            "canais",
            "websocket",
            "nós determinísticos e de IA",
        ],
        "preserved_features": [
            "Interface pública completa",
            "editor visual",
            "preview e execução",
            "agentes e RBAC",
            "handoff e inbox",
            "widgets",
            "analytics",
            "conhecimento e FAQ",
            "módulos públicos Apache-2.0; módulos privados excluídos",
        ],
        "offline_capabilities": [
            "editor e preview",
            "nós determinísticos",
            "handoff local",
            "inbox",
            "widgets locais",
            "conhecimento manual",
            "analytics locais",
            "RBAC",
        ],
        "gated_capabilities": [
            "nós LLM",
            "embeddings externos",
            "crawling de internet",
            "canais externos",
            "voz e transcrição",
            "módulos enterprise privados",
        ],
        "embeddable": True,
    },
    "bottlecrm": {
        "name": "BottleCRM",
        "role": "CRM ampliado, operações, relatórios e automações administrativas",
        "version": "0.46",
        "capabilities": [
            "leads",
            "contas",
            "contatos",
            "oportunidades",
            "casos",
            "tickets",
            "tarefas",
            "Kanban",
            "calendário",
            "produtos",
            "orçamentos",
            "faturas",
            "recorrência",
            "templates",
            "relatórios",
            "portais",
            "metas",
            "timesheets",
            "macros",
            "aprovações",
            "roteamento",
            "escalonamento",
            "SLA",
            "CSAT",
            "importação",
            "usuários",
            "auditoria",
            "API tokens",
            "MCP",
        ],
        "preserved_features": [
            "Interface MIT completa",
            "CRM comercial",
            "suporte e tickets",
            "financeiro operacional",
            "relatórios e portais",
            "aprovações e roteamento",
            "metas e timesheets",
            "RLS, auditoria, REST API e MCP",
        ],
        "offline_capabilities": [
            "todos os cadastros e pipelines",
            "Kanban e calendário",
            "tickets e tarefas",
            "produtos, orçamentos e faturas locais",
            "relatórios",
            "RLS",
            "auditoria",
            "MCP local",
        ],
        "gated_capabilities": [
            "e-mail real",
            "calendário externo",
            "pagamentos",
            "assinatura eletrônica",
            "enriquecimento externo",
            "notificações externas",
        ],
        "embeddable": True,
    },
}


def _loopback_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _source_specs() -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(_LOCK_PATH.read_text(encoding="utf-8"))
        return {
            str(item["name"]): {
                "commit": str(item["commit"]),
                "excludePaths": [
                    str(path) for path in item.get("checkout", {}).get("excludePaths", [])
                ],
            }
            for item in payload.get("sources", [])
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _git_output(checkout: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *args],
            capture_output=True,
            check=False,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _source_integrity(
    provider: str,
    specs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compare materialized community checkouts with the versioned source lock."""

    source_names = (
        ("rocketchat", "rocket-embedded-chat") if provider == "rocketchat" else (provider,)
    )
    components: dict[str, str] = {}
    reasons: list[str] = []
    dirty = False

    for name in source_names:
        spec = specs.get(name)
        checkout = _UPSTREAM_ROOT / name
        if not spec or not (checkout / ".git").exists():
            reasons.append(f"{name}:checkout ausente")
            continue
        head = _git_output(checkout, "rev-parse", "HEAD")
        if not head:
            reasons.append(f"{name}:HEAD ilegível")
            continue
        components[name] = head
        if head != spec["commit"]:
            reasons.append(f"{name}:commit divergente")
        worktree = _git_output(
            checkout,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        if worktree is None:
            reasons.append(f"{name}:status ilegível")
        elif worktree:
            dirty = True
            reasons.append(f"{name}:worktree alterada")
        for excluded in spec["excludePaths"]:
            if (checkout / excluded).exists() or (checkout / excluded).is_symlink():
                reasons.append(f"{name}:exclusão proprietária materializada")

    complete = len(components) == len(source_names)
    state = "pinned_clean" if complete and not reasons else "missing" if not complete else "drifted"
    return {
        "state": state,
        "head": components.get(provider),
        "components": components,
        "dirty": dirty,
        "details": reasons,
    }


def _version_matches(key: str, expected: str, reported: str) -> bool:
    def parts(value: str) -> tuple[int, ...] | None:
        match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-+][0-9A-Za-z.-]+)?", value)
        return tuple(int(item) for item in match.groups() if item is not None) if match else None

    expected_parts = parts(expected)
    reported_parts = parts(reported)
    if expected_parts is None or reported_parts is None:
        return False
    if key == "rocketchat" and len(reported_parts) == 2:
        return reported_parts == expected_parts[:2]
    return reported_parts == expected_parts


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any] | None]:
    response: httpx.Response | None = None
    for attempt in range(2):
        try:
            response = await client.get(url, headers=headers)
            break
        except (httpx.HTTPError, TimeoutError):
            if attempt == 1:
                return 0, None
            await asyncio.sleep(0.075)
    if response is None:
        return 0, None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return response.status_code, payload if isinstance(payload, dict) else None


def _health_payload_problem(value: Any, path: str = "health") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            normalized_key = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized_key in {"error", "errors"} and child not in (
                None,
                False,
                "",
                [],
                {},
            ):
                return child_path
            if normalized_key in {"availability", "state", "status"} and str(
                child or ""
            ).strip().lower() in _UNHEALTHY_PAYLOAD_STATES:
                return child_path
            problem = _health_payload_problem(child, child_path)
            if problem is not None:
                return problem
    elif isinstance(value, list | tuple):
        for index, child in enumerate(value):
            problem = _health_payload_problem(child, f"{path}[{index}]")
            if problem is not None:
                return problem
    return None


async def _probe_chatwoot(client: httpx.AsyncClient, base_url: str) -> dict[str, str]:
    health_status, health = await _get_json(client, f"{base_url}/health")
    api_status, api = await _get_json(client, f"{base_url}/api")
    if not health_status and not api_status:
        return {"state": "stopped", "label": "serviço local parado", "auth": "unavailable"}
    if health_status != 200 or api_status != 200 or not api:
        return {"state": "degraded", "label": "readiness incompleto", "auth": "unknown"}
    health_problem = _health_payload_problem(health) if health else None
    api_problem = _health_payload_problem(api, "api")
    if health_problem is not None or api_problem is not None:
        return {
            "state": "degraded",
            "label": f"readiness degradado ({health_problem or api_problem})",
            "auth": "unknown",
        }
    if not _version_matches("chatwoot", "v4.15.1", str(api.get("version") or "")):
        return {"state": "degraded", "label": "versão inesperada", "auth": "unknown"}
    if api.get("queue_services") != "ok":
        return {"state": "degraded", "label": "fila local indisponível", "auth": "unknown"}
    if settings.KLM_CHATWOOT_ACCOUNT_ID and settings.KLM_CHATWOOT_API_ACCESS_TOKEN:
        account_status, _ = await _get_json(
            client,
            f"{base_url}/api/v1/accounts/{settings.KLM_CHATWOOT_ACCOUNT_ID}",
            headers={"api_access_token": settings.KLM_CHATWOOT_API_ACCESS_TOKEN},
        )
        if account_status != 200:
            return {"state": "degraded", "label": "bootstrap ou token inválido", "auth": "failed"}
        label = (
            "saudável, autenticado e consultando dados"
            if api.get("data_services") == "ok"
            else "saudável, dados confirmados pela API autenticada"
        )
        return {
            "state": "ready",
            "label": label,
            "auth": "ready",
        }
    if api.get("data_services") != "ok":
        return {"state": "degraded", "label": "dados locais indisponíveis", "auth": "unknown"}
    return {
        "state": "degraded",
        "label": "saudável, aguardando bootstrap",
        "auth": "not_configured",
    }


async def _probe_versioned(
    client: httpx.AsyncClient,
    key: str,
    base_url: str,
    path: str,
) -> dict[str, str]:
    status, payload = await _get_json(client, f"{base_url}{path}")
    if status == 0:
        return {"state": "stopped", "label": "serviço local parado", "auth": "unavailable"}
    if status != 200 or not payload:
        return {
            "state": "degraded",
            "label": f"readiness rejeitado (HTTP {status})",
            "auth": "unknown",
        }
    health_problem = _health_payload_problem(payload)
    if health_problem is not None:
        return {
            "state": "degraded",
            "label": f"readiness degradado ({health_problem})",
            "auth": "unknown",
        }
    reported_health = str(payload.get("status") or payload.get("state") or "").strip().lower()
    if key in {"chattermate", "bottlecrm"} and reported_health not in {
        "healthy",
        "ok",
        "ready",
    }:
        return {
            "state": "degraded",
            "label": "readiness sem confirmação saudável",
            "auth": "unknown",
        }
    reported = str(payload.get("version") or payload.get("appVersion") or "")
    if key in {"rocketchat"} and not _version_matches(key, str(_CATALOG[key]["version"]), reported):
        return {"state": "degraded", "label": "versão inesperada", "auth": "unknown"}
    return {"state": "ready", "label": "saudável localmente", "auth": "ready"}


async def _probe_service(
    client: httpx.AsyncClient,
    key: str,
    base_url: str,
) -> dict[str, str]:
    if not _loopback_url(base_url):
        return {"state": "blocked", "label": "URL não local bloqueada", "auth": "blocked"}
    base_url = base_url.rstrip("/")
    if key == "chatwoot":
        return await _probe_chatwoot(client, base_url)
    paths = {
        "rocketchat": "/api/info",
        "chattermate": "/health",
        "bottlecrm": "/healthz",
    }
    return await _probe_versioned(client, key, base_url, paths[key])


def _bridge_auth_configured(key: str) -> bool:
    return {
        "chatwoot": bool(
            settings.KLM_CHATWOOT_ACCOUNT_ID and settings.KLM_CHATWOOT_API_ACCESS_TOKEN
        ),
        "rocketchat": bool(
            settings.KLM_ROCKETCHAT_ADMIN_USERNAME and settings.KLM_ROCKETCHAT_ADMIN_PASSWORD
        ),
        "chattermate": bool(
            settings.KLM_CHATTERMATE_ADMIN_EMAIL and settings.KLM_CHATTERMATE_ADMIN_PASSWORD
        ),
        "bottlecrm": len(settings.KLM_BOTTLECRM_BRIDGE_SECRET.strip()) >= 32,
    }[key]


async def _probe_bridge_auth(key: str) -> dict[str, str]:
    """Authenticate the local bridge instead of inferring access from health."""

    if not _bridge_auth_configured(key):
        return {
            "state": "degraded",
            "label": "saudável, aguardando bootstrap autenticado",
            "auth": "not_configured",
        }
    try:
        if key == "rocketchat":
            identity = await RocketChatClient().identity()
            valid = bool(identity.get("_id") and identity.get("username"))
        elif key == "chattermate":
            identity = await ChatterMateClient().identity()
            valid = bool(identity.get("id") and identity.get("organization_id"))
        elif key == "bottlecrm":
            identity = await BottleCRMClient().authenticate()
            valid = bool(identity.get("accessToken") and identity.get("organizationId"))
        else:
            raise RuntimeError(f"Ponte autenticada desconhecida: {key}")
    except (
        BottleCRMClientError,
        ChatterMateClientError,
        RocketChatClientError,
        TimeoutError,
    ):
        return {
            "state": "degraded",
            "label": "serviço saudável, autenticação da ponte falhou",
            "auth": "failed",
        }
    if not valid:
        return {
            "state": "degraded",
            "label": "serviço saudável, identidade da ponte inválida",
            "auth": "failed",
        }
    return {
        "state": "ready",
        "label": "saudável e autenticado pela ponte local",
        "auth": "ready",
    }


async def _require_authenticated_probe(
    key: str,
    probe: dict[str, str],
    *,
    timeout: float,
) -> dict[str, str]:
    if key == "chatwoot" or probe["state"] != "ready":
        return probe
    effective_timeout = max(
        timeout,
        _BRIDGE_AUTH_TIMEOUT_FLOOR_SECONDS.get(key, timeout),
    )
    try:
        return await asyncio.wait_for(
            _probe_bridge_auth(key),
            timeout=effective_timeout,
        )
    except TimeoutError:
        return {
            "state": "degraded",
            "label": "serviço saudável, autenticação da ponte expirou",
            "auth": "failed",
        }


def _integration_state(key: str, probe: dict[str, str]) -> str:
    configured = {
        "chatwoot": bool(
            settings.KLM_CHATWOOT_API_ACCESS_TOKEN and settings.KLM_CHATWOOT_WEBHOOK_SECRET
        ),
        "rocketchat": bool(settings.KLM_ROCKETCHAT_WEBHOOK_TOKEN),
        "chattermate": _bridge_auth_configured("chattermate"),
        "bottlecrm": _bridge_auth_configured("bottlecrm"),
    }[key]
    if probe["auth"] == "not_configured":
        return "needs_bootstrap"
    if probe["auth"] == "failed":
        return "auth_failed"
    if probe["state"] != "ready":
        return "offline"
    if not settings.KLM_OSS_LOCAL_BRIDGES_ENABLED:
        return "bridge_disabled"
    return "connected_local" if configured else "needs_bootstrap"


async def _collect_oss_runtime_status() -> dict[str, Any]:
    checked_at = datetime.now(UTC)
    source_specs = _source_specs()
    urls = {
        "chatwoot": settings.KLM_CHATWOOT_URL,
        "rocketchat": settings.KLM_ROCKETCHAT_URL,
        "chattermate": settings.KLM_CHATTERMATE_URL,
        "bottlecrm": settings.KLM_BOTTLECRM_URL,
    }
    timeout = max(0.2, min(settings.KLM_OSS_HEALTHCHECK_TIMEOUT_SECONDS, 3.0))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        results = await asyncio.gather(
            *(_probe_service(client, key, value) for key, value in urls.items())
        )
    health_probes = dict(zip(urls, results, strict=True))
    authenticated_results = await asyncio.gather(
        *(
            _require_authenticated_probe(
                key,
                health_probes[key],
                timeout=max(1.0, timeout * 2),
            )
            for key in urls
        )
    )
    probes = dict(zip(urls, authenticated_results, strict=True))
    source_results = await asyncio.gather(
        *(asyncio.to_thread(_source_integrity, key, source_specs) for key in _CATALOG)
    )
    source_integrity = dict(zip(_CATALOG, source_results, strict=True))

    services = []
    for key, item in _CATALOG.items():
        probe = probes[key]
        source = source_integrity[key]
        services.append(
            {
                "key": key,
                "name": item["name"],
                "role": item["role"],
                "state": probe["state"],
                "stateLabel": probe["label"],
                "operational": probe["state"] == "ready",
                "authState": probe["auth"],
                "integrationState": _integration_state(key, probe),
                "sourceState": source["state"],
                "sourceHead": source["head"],
                "sourceComponents": source["components"],
                "sourceDirty": source["dirty"],
                "sourceDetails": source["details"],
                "localUrl": urls[key] if _loopback_url(urls[key]) else None,
                "version": item["version"],
                "referenceVersion": item.get("reference_version"),
                "runtimePolicy": item.get("runtime_policy", "pinned"),
                "runtimeNotice": item.get("runtime_notice"),
                "sourceCommit": source_specs.get(key, {}).get("commit"),
                "capabilities": item["capabilities"],
                "preservedFeatures": item["preserved_features"],
                "offlineCapabilities": item["offline_capabilities"],
                "gatedCapabilities": item["gated_capabilities"],
                "embeddable": item["embeddable"],
                "commandsEndpoint": {
                    "enabled": key in _PROVIDERS_WITH_NATIVE_COMMAND_DISPATCHER,
                    "state": (
                        "dispatcher_available"
                        if key in _PROVIDERS_WITH_NATIVE_COMMAND_DISPATCHER
                        else "disabled_no_dispatcher"
                    ),
                    "pathTemplate": f"/api/v1/oss-native/{key}/{{capability}}/commands",
                    "replacementPathTemplate": (
                        f"/api/v1/oss-native/{key}/{{capability}}/actions"
                    ),
                },
                "lastCheckedAt": checked_at,
            }
        )
    return {
        "mode": "local_only",
        "externalActionsEnabled": False,
        "services": services,
    }


def reset_oss_runtime_status_cache() -> None:
    """Invalidate the process-local snapshot after tests or runtime reconfiguration."""

    global _status_cache
    _status_cache = None


def _status_cache_ttl(snapshot: dict[str, Any]) -> float:
    services = snapshot.get("services")
    if isinstance(services, list) and services and all(
        isinstance(service, dict) and service.get("operational") is True
        for service in services
    ):
        return _READY_STATUS_CACHE_TTL_SECONDS
    return _DEGRADED_STATUS_CACHE_TTL_SECONDS


async def oss_runtime_status(*, force_refresh: bool = False) -> dict[str, Any]:
    """Coalesce frequent SPA probes and retain a truthful short-lived snapshot."""

    global _status_cache
    now = time.monotonic()
    if not force_refresh and _status_cache and _status_cache[0] > now:
        return copy.deepcopy(_status_cache[1])

    async with _status_cache_lock:
        now = time.monotonic()
        if not force_refresh and _status_cache and _status_cache[0] > now:
            return copy.deepcopy(_status_cache[1])
        snapshot = await _collect_oss_runtime_status()
        _status_cache = (
            time.monotonic() + _status_cache_ttl(snapshot),
            copy.deepcopy(snapshot),
        )
        return snapshot
