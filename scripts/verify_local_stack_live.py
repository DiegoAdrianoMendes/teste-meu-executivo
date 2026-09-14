#!/usr/bin/env python3
"""Fail-closed, read-only smoke verification for the running local Docker stack."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from urllib.request import OpenerDirector, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
PROJECT_NAME = "klm-hub-local"
SCRIPT_VERSION = "1.0"

PERSISTENT_SERVICES = {
    "api",
    "beat",
    "fake-provider",
    "mailpit",
    "minio",
    "postgres",
    "proxy",
    "redis",
    "worker",
}
ONE_SHOT_SERVICES = {"migrate", "minio-init", "rls-role-bootstrap"}
EXPECTED_SERVICES = PERSISTENT_SERVICES | ONE_SHOT_SERVICES
APP_SERVICES = {"api", "beat", "worker"}
LOOPBACK_SERVICES = {"fake-provider", "mailpit", "minio", "proxy"}

EXPECTED_BINDINGS = {
    "fake-provider": {"8080/tcp": "KLM_FAKE_PROVIDER_PORT"},
    "mailpit": {
        "1025/tcp": "KLM_MAILPIT_SMTP_PORT",
        "8025/tcp": "KLM_MAILPIT_UI_PORT",
    },
    "minio": {
        "9000/tcp": "KLM_MINIO_PORT",
        "9001/tcp": "KLM_MINIO_CONSOLE_PORT",
    },
    "proxy": {"8080/tcp": "KLM_PROXY_PORT"},
}
PORT_ENV_KEYS = {
    env_key
    for service_bindings in EXPECTED_BINDINGS.values()
    for env_key in service_bindings.values()
}

FALSE_EXTERNAL_GATES = (
    "GEMMA_ENABLED",
    "KLM_AI_LEGACY_DIRECT_CALLS_ENABLED",
    "KLM_AWS_ENABLED",
    "KLM_EXTERNAL_ACTIONS_ENABLED",
    "KLM_LOCAL_BOOTSTRAP_ENABLED",
    "KLM_LOCAL_MODELS_ENABLED",
    "KLM_MODEL_APIS_ENABLED",
    "KLM_ONLINE_APPROVED",
    "KLM_OSS_LOCAL_BRIDGES_ENABLED",
    "LANGCHAIN_TRACING_V2",
)
EMPTY_EXTERNAL_KEYS = (
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "LANGCHAIN_API_KEY",
    "META_ADS_ACCESS_TOKEN",
    "OPENAI_API_KEY",
    "RESEND_API_KEY",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "SAAS_ASAAS_ACCESS_TOKEN",
    "SAAS_ASAAS_WEBHOOK_TOKEN",
    "MERCADO_PAGO_ACCESS_TOKEN",
    "MERCADO_PAGO_WEBHOOK_SECRET",
    "ZAPSIGN_API_TOKEN",
)
PROXY_ENV = {
    "ALL_PROXY": "",
    "HTTP_PROXY": "",
    "HTTPS_PROXY": "",
    "NO_PROXY": "*",
}
MIGRATE_FALSE_GATES = (
    "KLM_AWS_ENABLED",
    "KLM_EXTERNAL_ACTIONS_ENABLED",
    "KLM_MODEL_APIS_ENABLED",
    "KLM_ONLINE_APPROVED",
)
MINIO_BUCKETS = ("klm-hub-documents", "klm-hub-public-assets")

POSTGRES_CONTRACT_SQL = r"""
WITH required_indexes AS (
    SELECT index_relation.relname AS name,
           access_method.amname AS method,
           table_relation.relname AS table_name,
           pg_get_indexdef(index_relation.oid) AS definition
    FROM pg_class AS index_relation
    JOIN pg_index AS index_catalog ON index_catalog.indexrelid = index_relation.oid
    JOIN pg_class AS table_relation ON table_relation.oid = index_catalog.indrelid
    JOIN pg_namespace AS namespace ON namespace.oid = table_relation.relnamespace
    JOIN pg_am AS access_method ON access_method.oid = index_relation.relam
    WHERE namespace.nspname = 'public'
      AND index_relation.relname IN (
          'ix_knowledge_chunks_embedding_hnsw',
          'ix_knowledge_chunks_search_document_gin'
      )
), required_fk AS (
    SELECT constraint_record.conname AS name,
           source_relation.relname AS table_name,
           target_relation.relname AS referenced_table,
           (
               SELECT jsonb_agg(attribute.attname ORDER BY key_position.ordinality)
               FROM unnest(constraint_record.conkey) WITH ORDINALITY
                    AS key_position(attnum, ordinality)
               JOIN pg_attribute AS attribute
                 ON attribute.attrelid = constraint_record.conrelid
                AND attribute.attnum = key_position.attnum
           ) AS columns,
           (
               SELECT jsonb_agg(attribute.attname ORDER BY key_position.ordinality)
               FROM unnest(constraint_record.confkey) WITH ORDINALITY
                    AS key_position(attnum, ordinality)
               JOIN pg_attribute AS attribute
                 ON attribute.attrelid = constraint_record.confrelid
                AND attribute.attnum = key_position.attnum
           ) AS referenced_columns
    FROM pg_constraint AS constraint_record
    JOIN pg_class AS source_relation ON source_relation.oid = constraint_record.conrelid
    JOIN pg_class AS target_relation ON target_relation.oid = constraint_record.confrelid
    JOIN pg_namespace AS namespace ON namespace.oid = source_relation.relnamespace
    WHERE namespace.nspname = 'public'
      AND constraint_record.contype = 'f'
      AND constraint_record.conname = 'fk_knowledge_chunks_matter_workspace'
)
SELECT jsonb_build_object(
    'alembicRevisions', COALESCE(
        (SELECT jsonb_agg(version_num ORDER BY version_num) FROM alembic_version),
        '[]'::jsonb
    ),
    'vectorVersion', (
        SELECT extversion FROM pg_extension WHERE extname = 'vector'
    ),
    'indexes', COALESCE(
        (
            SELECT jsonb_agg(
                jsonb_build_object(
                    'name', name,
                    'method', method,
                    'table', table_name,
                    'definition', definition
                ) ORDER BY name
            )
            FROM required_indexes
        ),
        '[]'::jsonb
    ),
    'compositeForeignKey', (
        SELECT jsonb_build_object(
            'name', name,
            'table', table_name,
            'referencedTable', referenced_table,
            'columns', columns,
            'referencedColumns', referenced_columns
        )
        FROM required_fk
    ),
    'runtimeRole', (
        SELECT jsonb_build_object(
            'name', runtime_role.rolname,
            'canLogin', runtime_role.rolcanlogin,
            'superuser', runtime_role.rolsuper,
            'bypassRls', runtime_role.rolbypassrls,
            'createRole', runtime_role.rolcreaterole,
            'createDatabase', runtime_role.rolcreatedb,
            'replication', runtime_role.rolreplication,
            'inheritsPrivileges', runtime_role.rolinherit,
            'canAssumePrivilegedRole', EXISTS (
                SELECT 1
                FROM pg_roles AS privileged_role
                WHERE (privileged_role.rolsuper OR privileged_role.rolbypassrls)
                  AND pg_has_role(runtime_role.rolname, privileged_role.oid, 'MEMBER')
            )
        )
        FROM pg_roles AS runtime_role
        WHERE runtime_role.rolname = 'klm_app'
    ),
    'rls', (
        SELECT jsonb_build_object(
            'enabledCount', count(*) FILTER (WHERE relation.relrowsecurity),
            'forcedCount', count(*) FILTER (
                WHERE relation.relrowsecurity AND relation.relforcerowsecurity
            ),
            'enabledNotForced', COALESCE(
                jsonb_agg(relation.relname ORDER BY relation.relname) FILTER (
                    WHERE relation.relrowsecurity AND NOT relation.relforcerowsecurity
                ),
                '[]'::jsonb
            ),
            'policyCount', (
                SELECT count(*) FROM pg_policies WHERE schemaname = 'public'
            )
        )
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p')
    )
)::text;
""".strip()

RLS_AUDIT_PYTHON = """
import hashlib
import json
import psycopg2
from app.core.config import settings
from app.db.rls import audit_postgresql_runtime_connection

connection = psycopg2.connect(str(settings.DATABASE_URL_SYNC))
audit = audit_postgresql_runtime_connection(connection)
tables = list(audit.protected_tables)
print(json.dumps({
    "roleName": audit.role_name,
    "protectedTableCount": len(tables),
    "protectedTablesDigest": hashlib.sha256(
        json.dumps(tables, separators=(",", ":")).encode("utf-8")
    ).hexdigest(),
}, sort_keys=True))
connection.close()
""".strip()

EGRESS_DENIAL_PYTHON = """
import json
import socket
from pathlib import Path

routes = Path("/proc/net/route").read_text(encoding="ascii").splitlines()[1:]
default_route_present = any(
    len(fields := line.split()) >= 4
    and fields[1] == "00000000"
    and int(fields[3], 16) & 0x1
    for line in routes
)
target = ("198.51.100.1", 443)
connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
connection.settimeout(1.0)
try:
    errno = connection.connect_ex(target)
finally:
    connection.close()
print(json.dumps({
    "connected": errno == 0,
    "defaultRoutePresent": default_route_present,
    "errno": errno,
    "routeCount": len(routes),
    "target": "198.51.100.1:443",
}, sort_keys=True))
""".strip()

REDIS_PING_SCRIPT = """
export REDISCLI_AUTH="${KLM_REDIS_PASSWORD:?KLM_REDIS_PASSWORD is required}"
exec redis-cli --no-auth-warning --raw PING
""".strip()
REDIS_PERSISTENCE_SCRIPT = """
export REDISCLI_AUTH="${KLM_REDIS_PASSWORD:?KLM_REDIS_PASSWORD is required}"
exec redis-cli --no-auth-warning --raw INFO persistence
""".strip()

_SENSITIVE_KEY = re.compile(
    r"(?:PASSWORD|SECRET|TOKEN|API_KEY|ACCESS_KEY|DATABASE_URL|REDIS_URL|BROKER_URL|FERNET)",
    re.IGNORECASE,
)
_SAFE_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
_REVISION_AT_HEAD = re.compile(r"(?m)^([0-9a-z]+)\s+\(head\)\s*$")


class VerificationError(RuntimeError):
    """A fail-closed verification failure with a stable check identifier."""

    def __init__(self, check: str, message: str) -> None:
        super().__init__(message)
        self.check = check
        self.message = message


def require(condition: bool, check: str, message: str) -> None:
    if not condition:
        raise VerificationError(check, message)


def _docker_environment() -> dict[str, str]:
    allowed = {"DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_HOST", "HOME", "PATH", "TMPDIR"}
    return {key: value for key, value in os.environ.items() if key in allowed}


def _sanitize_text(value: str) -> str:
    value = re.sub(r"(://[^:/\s]+:)[^@/\s]+@", r"\1[REDACTED]@", value)
    return re.sub(
        r"(?i)\b([A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_KEY|ACCESS_KEY)[A-Z0-9_]*)=\S+",
        r"\1=[REDACTED]",
        value,
    )


def _display_argv(args: Sequence[str]) -> list[str]:
    displayed = [_sanitize_text(str(item)) for item in args]
    if len(args) >= 4 and args[0:2] == ["docker", "exec"]:
        executable = args[3]
        if executable == "psql" and "--command" in args:
            displayed[displayed.index("--command") + 1] = "<postgres-live-contract-query>"
        elif executable == "python" and "-c" in args:
            script = args[displayed.index("-c") + 1]
            label = "runtime-rls-audit" if script == RLS_AUDIT_PYTHON else "egress-denial-canary"
            displayed[displayed.index("-c") + 1] = f"<{label}>"
        elif executable == "sh" and "-ec" in args:
            script = args[displayed.index("-ec") + 1]
            operation = "PING" if "PING" in script else "INFO persistence"
            displayed[displayed.index("-ec") + 1] = (
                f"<redis-cli {operation}; auth from container env>"
            )
    return displayed


def _validate_read_only_command(args: Sequence[str]) -> None:
    require(bool(args), "command-policy", "empty commands are forbidden")
    require(args[0] == "docker", "command-policy", "only Docker inspection is allowed")
    require(len(args) >= 2, "command-policy", "incomplete Docker command")

    subcommand = args[1]
    require(
        subcommand in {"ps", "inspect", "network", "exec"},
        "command-policy",
        f"Docker subcommand {subcommand!r} is not read-only allowlisted",
    )
    if subcommand == "network":
        require(
            len(args) >= 4 and args[2] == "inspect",
            "command-policy",
            "only docker network inspect is allowed",
        )
    if subcommand != "exec":
        return

    require(len(args) >= 4, "command-policy", "incomplete docker exec command")
    executable = args[3]
    require(
        executable in {"alembic", "psql", "python", "sh"},
        "command-policy",
        f"docker exec executable {executable!r} is not allowlisted",
    )
    if executable == "alembic":
        require(
            tuple(args[4:]) in {("heads",), ("current",)},
            "command-policy",
            "only Alembic heads/current are allowed",
        )
    elif executable == "psql":
        require("--command" in args, "command-policy", "psql must use a fixed query")
        query = args[args.index("--command") + 1]
        require(
            query == POSTGRES_CONTRACT_SQL,
            "command-policy",
            "only the fixed PostgreSQL contract query is allowed",
        )
    elif executable == "python":
        require(
            len(args) == 6
            and args[4] == "-c"
            and args[5] in {RLS_AUDIT_PYTHON, EGRESS_DENIAL_PYTHON},
            "command-policy",
            "only fixed runtime audits are allowed",
        )
    else:
        require(
            len(args) == 6
            and args[4] == "-ec"
            and args[5] in {REDIS_PING_SCRIPT, REDIS_PERSISTENCE_SCRIPT},
            "command-policy",
            "only fixed read-only Redis probes are allowed",
        )


class Runner(Protocol):
    commands: list[dict[str, Any]]

    def run(self, args: Sequence[str], *, label: str, timeout: int = 30) -> str: ...


class CommandRunner:
    def __init__(self) -> None:
        self.commands: list[dict[str, Any]] = []

    def run(self, args: Sequence[str], *, label: str, timeout: int = 30) -> str:
        normalized = [str(item) for item in args]
        _validate_read_only_command(normalized)
        started = time.monotonic()
        record: dict[str, Any] = {
            "id": len(self.commands) + 1,
            "kind": "subprocess",
            "label": label,
            "argv": _display_argv(normalized),
        }
        try:
            completed = subprocess.run(
                normalized,
                cwd=ROOT,
                env=_docker_environment(),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as error:
            record.update(
                {
                    "status": "failed",
                    "exitCode": None,
                    "durationMs": round((time.monotonic() - started) * 1000),
                    "errorType": type(error).__name__,
                }
            )
            self.commands.append(record)
            raise VerificationError(
                label,
                f"command unavailable: {type(error).__name__}",
            ) from error

        record.update(
            {
                "status": "passed" if completed.returncode == 0 else "failed",
                "exitCode": completed.returncode,
                "durationMs": round((time.monotonic() - started) * 1000),
            }
        )
        self.commands.append(record)
        if completed.returncode != 0:
            raise VerificationError(label, f"read-only command exited {completed.returncode}")
        return completed.stdout


class LocalHttpClient:
    def __init__(
        self,
        commands: list[dict[str, Any]],
        *,
        opener: OpenerDirector | None = None,
    ) -> None:
        self.commands = commands
        self.opener = opener or build_opener(ProxyHandler({}))

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlsplit(url)
        require(parsed.scheme == "http", "http-policy", "only plain local HTTP is allowed")
        require(parsed.hostname == "127.0.0.1", "http-policy", "HTTP host must be 127.0.0.1")
        require(parsed.username is None, "http-policy", "credentials in URLs are forbidden")
        require(parsed.password is None, "http-policy", "credentials in URLs are forbidden")
        require(parsed.fragment == "", "http-policy", "URL fragments are forbidden")
        try:
            port = parsed.port
        except ValueError as error:
            raise VerificationError("http-policy", "invalid loopback port") from error
        require(port is not None and 1 <= port <= 65535, "http-policy", "loopback port is required")

    def request(
        self,
        url: str,
        *,
        label: str,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        expected_statuses: set[int] | None = None,
        timeout: float = 3.0,
        max_bytes: int = 65_536,
    ) -> tuple[int, bytes]:
        self._validate_url(url)
        expected = expected_statuses or {200}
        request = Request(url, headers=dict(headers or {}), method=method)
        started = time.monotonic()
        record: dict[str, Any] = {
            "id": len(self.commands) + 1,
            "kind": "http",
            "label": label,
            "argv": ["HTTP", method, url],
        }
        try:
            try:
                response = self.opener.open(request, timeout=timeout)
                with response:
                    status = int(getattr(response, "status", response.getcode()))
                    body = response.read(max_bytes + 1)
            except HTTPError as error:
                status = int(error.code)
                body = error.read(max_bytes + 1)
        except (OSError, TimeoutError, URLError) as error:
            record.update(
                {
                    "status": "failed",
                    "httpStatus": None,
                    "durationMs": round((time.monotonic() - started) * 1000),
                    "errorType": type(error).__name__,
                }
            )
            self.commands.append(record)
            raise VerificationError(label, f"local HTTP failed: {type(error).__name__}") from error

        record.update(
            {
                "status": "passed" if status in expected and len(body) <= max_bytes else "failed",
                "httpStatus": status,
                "durationMs": round((time.monotonic() - started) * 1000),
            }
        )
        self.commands.append(record)
        require(len(body) <= max_bytes, label, "local HTTP response exceeded the size limit")
        require(status in expected, label, f"local HTTP returned unexpected status {status}")
        return status, body


def _normalize_path(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def load_port_overrides(env_file: Path | None) -> tuple[Path | None, dict[str, int]]:
    if env_file is None:
        return None, {}
    path = _normalize_path(env_file)
    require(path.is_file(), "env-file", "the provided env file does not exist")
    values: dict[str, int] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        require("=" in line, "env-file", f"invalid env assignment at line {line_number}")
        raw_key, raw_value = line.split("=", 1)
        key = raw_key.strip()
        require(
            bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)),
            "env-file",
            f"invalid env key at line {line_number}",
        )
        if key not in PORT_ENV_KEYS:
            continue
        require(key not in values, "env-file", f"duplicate port key {key}")
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        require(value.isdigit(), "env-file", f"{key} must be a numeric port")
        port = int(value)
        require(1 <= port <= 65535, "env-file", f"{key} is outside the valid port range")
        values[key] = port
    return path, values


def _parse_json_document(raw: str, *, check: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise VerificationError(check, "command returned invalid JSON") from error


def _parse_last_json_line(raw: str, *, check: str) -> dict[str, Any]:
    for line in reversed(raw.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        parsed = _parse_json_document(candidate, check=check)
        require(isinstance(parsed, dict), check, "command JSON must be an object")
        return parsed
    raise VerificationError(check, "command did not return a JSON object")


def collect_containers(runner: Runner) -> dict[str, dict[str, Any]]:
    raw_ids = runner.run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={PROJECT_NAME}",
        ],
        label="containers-discovery",
    )
    container_ids = [line.strip() for line in raw_ids.splitlines() if line.strip()]
    require(container_ids, "containers-discovery", "no containers found for the local project")
    require(
        len(container_ids) == len(set(container_ids)),
        "containers-discovery",
        "Docker returned duplicate container identifiers",
    )
    require(
        all(_SAFE_CONTAINER_ID.fullmatch(container_id) for container_id in container_ids),
        "containers-discovery",
        "Docker returned an invalid container identifier",
    )

    raw_inspections = runner.run(
        ["docker", "inspect", *container_ids],
        label="containers-inspect",
    )
    inspections = _parse_json_document(raw_inspections, check="containers-inspect")
    require(
        isinstance(inspections, list),
        "containers-inspect",
        "docker inspect must return a list",
    )
    require(
        len(inspections) == len(container_ids),
        "containers-inspect",
        "not every discovered container could be inspected",
    )

    by_service: dict[str, dict[str, Any]] = {}
    for inspection in inspections:
        require(isinstance(inspection, dict), "containers-inspect", "invalid inspection record")
        config = inspection.get("Config") or {}
        labels = config.get("Labels") or {}
        require(
            labels.get("com.docker.compose.project") == PROJECT_NAME,
            "containers-inspect",
            "container project label differs",
        )
        service = str(labels.get("com.docker.compose.service") or "")
        require(
            service in EXPECTED_SERVICES,
            "containers-inspect",
            f"unexpected service {service!r}",
        )
        require(service not in by_service, "containers-inspect", f"duplicate service {service}")
        by_service[service] = inspection

    missing = sorted(EXPECTED_SERVICES - set(by_service))
    require(not missing, "containers-inspect", f"missing services: {', '.join(missing)}")
    require(
        set(by_service) == EXPECTED_SERVICES,
        "containers-inspect",
        "the project service set differs from the live contract",
    )
    return by_service


def verify_service_states(containers: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    service_results: list[dict[str, Any]] = []
    for service in sorted(PERSISTENT_SERVICES):
        container = containers[service]
        state = container.get("State") or {}
        health = state.get("Health") or {}
        require(state.get("Status") == "running", "service-state", f"{service} is not running")
        require(health.get("Status") == "healthy", "service-state", f"{service} is not healthy")
        service_results.append(
            {
                "service": service,
                "kind": "persistent",
                "containerId": str(container.get("Id") or "")[:12],
                "image": str((container.get("Config") or {}).get("Image") or ""),
                "state": "running",
                "health": "healthy",
                "passed": True,
            }
        )

    for service in sorted(ONE_SHOT_SERVICES):
        container = containers[service]
        state = container.get("State") or {}
        require(state.get("Status") == "exited", "service-state", f"{service} has not exited")
        require(state.get("ExitCode") == 0, "service-state", f"{service} did not exit zero")
        service_results.append(
            {
                "service": service,
                "kind": "one-shot",
                "containerId": str(container.get("Id") or "")[:12],
                "image": str((container.get("Config") or {}).get("Image") or ""),
                "state": "exited",
                "exitCode": 0,
                "passed": True,
            }
        )

    return {
        "status": "passed",
        "persistentExpected": len(PERSISTENT_SERVICES),
        "persistentHealthy": len(PERSISTENT_SERVICES),
        "oneShotsExpected": len(ONE_SHOT_SERVICES),
        "oneShotsExitedZero": len(ONE_SHOT_SERVICES),
        "services": service_results,
    }


def verify_bindings(
    containers: Mapping[str, dict[str, Any]],
    port_overrides: Mapping[str, int],
) -> tuple[dict[str, Any], dict[tuple[str, str], int]]:
    bindings: list[dict[str, Any]] = []
    resolved: dict[tuple[str, str], int] = {}
    for service in sorted(EXPECTED_SERVICES):
        ports = (containers[service].get("NetworkSettings") or {}).get("Ports") or {}
        require(isinstance(ports, dict), "bindings", f"{service} has invalid port metadata")
        actual_targets: set[str] = set()
        for target, raw_bindings in ports.items():
            if not raw_bindings:
                continue
            require(
                service in EXPECTED_BINDINGS and target in EXPECTED_BINDINGS[service],
                "bindings",
                f"{service} publishes unexpected target {target}",
            )
            require(
                isinstance(raw_bindings, list) and len(raw_bindings) == 1,
                "bindings",
                f"{service} target {target} must have one binding",
            )
            binding = raw_bindings[0]
            require(isinstance(binding, dict), "bindings", f"{service} has invalid binding data")
            require(
                binding.get("HostIp") == "127.0.0.1",
                "bindings",
                f"{service} target {target} is not bound to 127.0.0.1",
            )
            raw_host_port = str(binding.get("HostPort") or "")
            require(raw_host_port.isdigit(), "bindings", f"{service} has an invalid host port")
            host_port = int(raw_host_port)
            require(1 <= host_port <= 65535, "bindings", f"{service} host port is invalid")
            env_key = EXPECTED_BINDINGS[service][target]
            if env_key in port_overrides:
                require(
                    host_port == port_overrides[env_key],
                    "bindings",
                    f"{service} binding differs from {env_key}",
                )
            actual_targets.add(str(target))
            resolved[(service, str(target))] = host_port
            bindings.append(
                {
                    "service": service,
                    "target": str(target),
                    "hostIp": "127.0.0.1",
                    "hostPort": host_port,
                    "envKey": env_key,
                    "matchesEnvFile": (
                        env_key not in port_overrides or host_port == port_overrides[env_key]
                    ),
                }
            )

        expected_targets = set(EXPECTED_BINDINGS.get(service, {}))
        require(
            actual_targets == expected_targets,
            "bindings",
            f"{service} published target set differs",
        )

    return (
        {
            "status": "passed",
            "loopbackOnly": True,
            "publishedCount": len(bindings),
            "bindings": bindings,
        },
        resolved,
    )


def _container_environment(container: Mapping[str, Any]) -> dict[str, str]:
    raw_environment = (container.get("Config") or {}).get("Env") or []
    require(isinstance(raw_environment, list), "environment", "container Env must be a list")
    environment: dict[str, str] = {}
    for item in raw_environment:
        key, separator, value = str(item).partition("=")
        require(bool(separator) and bool(key), "environment", "invalid container env entry")
        require(key not in environment, "environment", f"duplicate container env key {key}")
        environment[key] = value
    return environment


def verify_runtime_environment(containers: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    for service in sorted(APP_SERVICES):
        environment = _container_environment(containers[service])
        for key in FALSE_EXTERNAL_GATES:
            require(
                environment.get(key) == "false",
                "environment",
                f"{service} does not keep {key} false",
            )
        for key in EMPTY_EXTERNAL_KEYS:
            require(
                key in environment and environment[key] == "",
                "environment",
                f"{service} does not keep {key} empty",
            )
        for key, expected in PROXY_ENV.items():
            require(
                environment.get(key) == expected,
                "environment",
                f"{service} has unsafe proxy setting {key}",
            )
        require(environment.get("AI_PROVIDER") == "fake", "environment", f"{service} is not fake")
        require(
            environment.get("KLM_RUNTIME_MODE") == "local_simulation",
            "environment",
            f"{service} is not in local_simulation",
        )
        require(
            environment.get("AWS_EC2_METADATA_DISABLED") == "true",
            "environment",
            f"{service} permits EC2 metadata lookup",
        )
        for key in (
            "KLM_AI_OPENAI_BASE_URL",
            "KLM_AI_ANTHROPIC_BASE_URL",
            "KLM_AI_GEMINI_BASE_URL",
            "OLLAMA_BASE_URL",
        ):
            parsed = urlsplit(environment.get(key, ""))
            require(
                parsed.scheme == "http" and parsed.hostname == "fake-provider",
                "environment",
                f"{service} {key} is not routed to fake-provider",
            )

    migrate_environment = _container_environment(containers["migrate"])
    for key in MIGRATE_FALSE_GATES:
        require(
            migrate_environment.get(key) == "false",
            "environment",
            f"migrate does not keep {key} false",
        )
    for key, expected in PROXY_ENV.items():
        require(
            migrate_environment.get(key) == expected,
            "environment",
            f"migrate has unsafe proxy setting {key}",
        )
    require(
        migrate_environment.get("AWS_EC2_METADATA_DISABLED") == "true",
        "environment",
        "migrate permits EC2 metadata lookup",
    )

    fake_environment = _container_environment(containers["fake-provider"])
    for key, expected in PROXY_ENV.items():
        require(
            fake_environment.get(key) == expected,
            "environment",
            f"fake-provider has unsafe proxy setting {key}",
        )

    return {
        "status": "passed",
        "servicesChecked": sorted(APP_SERVICES | {"fake-provider", "migrate"}),
        "runtimeMode": "local_simulation",
        "providerMode": "fake",
        "falseGateKeys": list(FALSE_EXTERNAL_GATES),
        "emptyCredentialKeys": list(EMPTY_EXTERNAL_KEYS),
        "outboundProxyDisabled": True,
        "ec2MetadataDisabled": True,
        "secretValuesReported": False,
    }


def verify_networks(
    runner: Runner,
    containers: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    service_networks: dict[str, set[str]] = {}
    all_network_names: set[str] = set()
    for service, container in containers.items():
        networks = (container.get("NetworkSettings") or {}).get("Networks") or {}
        require(isinstance(networks, dict), "networks", f"{service} network metadata is invalid")
        names = {str(name) for name in networks}
        service_networks[service] = names
        all_network_names.update(names)

    require(len(all_network_names) == 2, "networks", "the project must use exactly two networks")
    raw_networks = runner.run(
        ["docker", "network", "inspect", *sorted(all_network_names)],
        label="networks-inspect",
    )
    inspections = _parse_json_document(raw_networks, check="networks-inspect")
    require(isinstance(inspections, list), "networks-inspect", "network inspect must return a list")
    require(len(inspections) == 2, "networks-inspect", "both project networks are required")

    by_logical_name: dict[str, dict[str, Any]] = {}
    for inspection in inspections:
        require(isinstance(inspection, dict), "networks-inspect", "invalid network inspection")
        labels = inspection.get("Labels") or {}
        require(
            labels.get("com.docker.compose.project") == PROJECT_NAME,
            "networks-inspect",
            "network project label differs",
        )
        logical_name = str(labels.get("com.docker.compose.network") or "")
        require(
            logical_name in {"klm_internal", "klm_loopback"},
            "networks-inspect",
            f"unexpected network {logical_name!r}",
        )
        require(logical_name not in by_logical_name, "networks-inspect", "duplicate network")
        by_logical_name[logical_name] = inspection

    require(
        set(by_logical_name) == {"klm_internal", "klm_loopback"},
        "networks-inspect",
        "required network names are missing",
    )
    logical_to_docker = {
        logical: str(inspection.get("Name") or "")
        for logical, inspection in by_logical_name.items()
    }
    for logical, inspection in by_logical_name.items():
        require(inspection.get("Driver") == "bridge", "networks", f"{logical} is not bridge")
        require(
            (inspection.get("Options") or {}).get("com.docker.network.bridge.enable_ip_masquerade")
            == "false",
            "networks",
            f"{logical} enables IP masquerade",
        )
    require(
        by_logical_name["klm_internal"].get("Internal") is True,
        "networks",
        "klm_internal is not internal",
    )
    require(
        by_logical_name["klm_loopback"].get("Internal") is False,
        "networks",
        "klm_loopback Internal flag differs",
    )

    for service, actual in service_networks.items():
        expected = {logical_to_docker["klm_internal"]}
        if service in LOOPBACK_SERVICES:
            expected.add(logical_to_docker["klm_loopback"])
        require(actual == expected, "networks", f"{service} network attachment differs")

    api_id = str(containers["api"].get("Id") or "")
    egress_probe = _parse_last_json_line(
        runner.run(
            ["docker", "exec", api_id, "python", "-c", EGRESS_DENIAL_PYTHON],
            label="egress-denial-canary",
            timeout=5,
        ),
        check="egress-denial-canary",
    )
    require(
        egress_probe.get("defaultRoutePresent") is False,
        "egress-denial-canary",
        "the API container has a default route",
    )
    require(
        egress_probe.get("connected") is False and int(egress_probe.get("errno", 0)) != 0,
        "egress-denial-canary",
        "the API container connected to the TEST-NET egress canary",
    )

    return {
        "status": "passed",
        "externalEgressAllowed": False,
        "egressProbe": {
            "status": "passed",
            "container": "api",
            "target": "TEST-NET-2 198.51.100.1:443",
            "defaultRoutePresent": False,
            "connected": False,
            "errno": int(egress_probe["errno"]),
            "kernelRouteTableInspected": True,
        },
        "networks": [
            {
                "logicalName": logical,
                "dockerName": logical_to_docker[logical],
                "driver": "bridge",
                "internal": logical == "klm_internal",
                "ipMasquerade": False,
                "attachedServices": sorted(
                    service
                    for service, names in service_networks.items()
                    if logical_to_docker[logical] in names
                ),
            }
            for logical in ("klm_internal", "klm_loopback")
        ],
    }


def _json_body(body: bytes, *, check: str) -> dict[str, Any]:
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise VerificationError(check, "local HTTP response was not valid JSON") from error
    require(isinstance(parsed, dict), check, "local HTTP JSON must be an object")
    return parsed


def verify_http_endpoints(
    http: LocalHttpClient,
    bindings: Mapping[tuple[str, str], int],
) -> dict[str, Any]:
    proxy_port = bindings[("proxy", "8080/tcp")]
    _, proxy_body = http.request(
        f"http://127.0.0.1:{proxy_port}/healthz",
        label="http-proxy-health",
    )
    require(proxy_body.strip() == b"ok", "http-proxy-health", "proxy health body differs")

    _, api_body = http.request(
        f"http://127.0.0.1:{proxy_port}/health",
        label="http-api-health",
    )
    api_payload = _json_body(api_body, check="http-api-health")
    require(api_payload.get("status") == "ok", "http-api-health", "API is not healthy")
    require(bool(api_payload.get("version")), "http-api-health", "API version is missing")

    fake_port = bindings[("fake-provider", "8080/tcp")]
    _, fake_body = http.request(
        f"http://127.0.0.1:{fake_port}/health",
        label="http-fake-provider-health",
    )
    fake_payload = _json_body(fake_body, check="http-fake-provider-health")
    require(
        fake_payload.get("status") == "ok" and fake_payload.get("provider") == "fake",
        "http-fake-provider-health",
        "fake provider health contract differs",
    )

    mailpit_port = bindings[("mailpit", "8025/tcp")]
    _, mailpit_body = http.request(
        f"http://127.0.0.1:{mailpit_port}/api/v1/info",
        label="http-mailpit-health",
    )
    require(
        bool(_json_body(mailpit_body, check="http-mailpit-health")),
        "http-mailpit-health",
        "Mailpit info is empty",
    )

    minio_port = bindings[("minio", "9000/tcp")]
    http.request(
        f"http://127.0.0.1:{minio_port}/minio/health/live",
        label="http-minio-health",
    )

    return {
        "status": "passed",
        "localOnly": True,
        "checks": [
            {"name": "proxy", "path": "/healthz", "httpStatus": 200, "passed": True},
            {"name": "api-via-proxy", "path": "/health", "httpStatus": 200, "passed": True},
            {"name": "fake-provider", "path": "/health", "httpStatus": 200, "passed": True},
            {"name": "mailpit", "path": "/api/v1/info", "httpStatus": 200, "passed": True},
            {"name": "minio", "path": "/minio/health/live", "httpStatus": 200, "passed": True},
        ],
    }


def _head_revisions(raw: str, *, check: str) -> list[str]:
    revisions = _REVISION_AT_HEAD.findall(raw)
    require(len(revisions) == 1, check, "exactly one Alembic head is required")
    return revisions


def _index_by_name(catalog: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    indexes = catalog.get("indexes")
    require(isinstance(indexes, list), "postgres-contract", "index catalog is invalid")
    result: dict[str, dict[str, Any]] = {}
    for index in indexes:
        require(isinstance(index, dict), "postgres-contract", "index record is invalid")
        name = str(index.get("name") or "")
        require(name and name not in result, "postgres-contract", "duplicate required index")
        result[name] = index
    return result


def verify_postgres(runner: Runner, containers: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    api_id = str(containers["api"].get("Id") or "")
    postgres_id = str(containers["postgres"].get("Id") or "")
    heads = _head_revisions(
        runner.run(
            ["docker", "exec", api_id, "alembic", "heads"],
            label="alembic-heads",
        ),
        check="alembic-heads",
    )
    current = _head_revisions(
        runner.run(
            ["docker", "exec", api_id, "alembic", "current"],
            label="alembic-current",
        ),
        check="alembic-current",
    )
    require(current == heads, "alembic-current", "database revision is not at Alembic head")

    raw_catalog = runner.run(
        [
            "docker",
            "exec",
            postgres_id,
            "psql",
            "--no-psqlrc",
            "--quiet",
            "--tuples-only",
            "--no-align",
            "--set",
            "ON_ERROR_STOP=1",
            "--username",
            "klm_migrator",
            "--dbname",
            "klm_legal_hub",
            "--command",
            POSTGRES_CONTRACT_SQL,
        ],
        label="postgres-contract",
    )
    catalog = _parse_last_json_line(raw_catalog, check="postgres-contract")
    revisions = catalog.get("alembicRevisions")
    require(revisions == heads, "postgres-contract", "alembic_version differs from runtime head")
    require(
        catalog.get("vectorVersion") == "0.8.1",
        "postgres-contract",
        "pgvector extension version is not 0.8.1",
    )

    indexes = _index_by_name(catalog)
    require(
        set(indexes)
        == {
            "ix_knowledge_chunks_embedding_hnsw",
            "ix_knowledge_chunks_search_document_gin",
        },
        "postgres-contract",
        "required hybrid RAG indexes are missing",
    )
    hnsw = indexes["ix_knowledge_chunks_embedding_hnsw"]
    gin = indexes["ix_knowledge_chunks_search_document_gin"]
    require(
        hnsw.get("method") == "hnsw"
        and hnsw.get("table") == "knowledge_chunks"
        and "vector_cosine_ops" in str(hnsw.get("definition") or ""),
        "postgres-contract",
        "HNSW index contract differs",
    )
    require(
        gin.get("method") == "gin"
        and gin.get("table") == "knowledge_chunks"
        and "search_document" in str(gin.get("definition") or ""),
        "postgres-contract",
        "GIN index contract differs",
    )

    foreign_key = catalog.get("compositeForeignKey")
    require(isinstance(foreign_key, dict), "postgres-contract", "composite FK is missing")
    require(
        foreign_key.get("name") == "fk_knowledge_chunks_matter_workspace"
        and foreign_key.get("table") == "knowledge_chunks"
        and foreign_key.get("referencedTable") == "matters"
        and foreign_key.get("columns") == ["matter_id", "workspace_id"]
        and foreign_key.get("referencedColumns") == ["id", "workspace_id"],
        "postgres-contract",
        "composite workspace FK contract differs",
    )

    role = catalog.get("runtimeRole")
    require(isinstance(role, dict), "postgres-contract", "klm_app role is missing")
    require(role.get("name") == "klm_app", "postgres-contract", "runtime role is not klm_app")
    require(role.get("canLogin") is True, "postgres-contract", "klm_app cannot login")
    for unsafe_attribute in (
        "superuser",
        "bypassRls",
        "createRole",
        "createDatabase",
        "replication",
        "inheritsPrivileges",
        "canAssumePrivilegedRole",
    ):
        require(
            role.get(unsafe_attribute) is False,
            "postgres-contract",
            f"klm_app has unsafe attribute {unsafe_attribute}",
        )

    raw_audit = runner.run(
        ["docker", "exec", api_id, "python", "-c", RLS_AUDIT_PYTHON],
        label="rls-runtime-audit",
    )
    audit = _parse_last_json_line(raw_audit, check="rls-runtime-audit")
    require(audit.get("roleName") == "klm_app", "rls-runtime-audit", "RLS audit role differs")
    protected_count = audit.get("protectedTableCount")
    protected_digest = audit.get("protectedTablesDigest")
    require(
        isinstance(protected_count, int) and protected_count > 0,
        "rls-runtime-audit",
        "RLS audit did not protect any table",
    )
    require(
        isinstance(protected_digest, str) and bool(re.fullmatch(r"[0-9a-f]{64}", protected_digest)),
        "rls-runtime-audit",
        "RLS inventory digest is invalid",
    )
    rls = catalog.get("rls")
    require(isinstance(rls, dict), "postgres-contract", "RLS catalog is missing")
    require(
        rls.get("enabledNotForced") == [],
        "postgres-contract",
        "some RLS tables are not forced",
    )
    require(
        isinstance(rls.get("enabledCount"), int)
        and rls["enabledCount"] >= protected_count
        and rls.get("forcedCount") == rls.get("enabledCount"),
        "postgres-contract",
        "RLS enabled/forced counts differ",
    )
    require(
        isinstance(rls.get("policyCount"), int) and rls["policyCount"] >= protected_count,
        "postgres-contract",
        "RLS policy inventory is incomplete",
    )

    return {
        "status": "passed",
        "alembic": {"singleHead": heads[0], "current": current[0], "atHead": True},
        "pgvector": {"extensionVersion": "0.8.1", "passed": True},
        "indexes": {
            "hnsw": "ix_knowledge_chunks_embedding_hnsw",
            "gin": "ix_knowledge_chunks_search_document_gin",
            "passed": True,
        },
        "compositeForeignKey": {
            "name": "fk_knowledge_chunks_matter_workspace",
            "columns": ["matter_id", "workspace_id"],
            "referencedColumns": ["id", "workspace_id"],
            "passed": True,
        },
        "runtimeRole": {
            "name": "klm_app",
            "superuser": False,
            "bypassRls": False,
            "canAssumePrivilegedRole": False,
            "passed": True,
        },
        "rls": {
            "protectedTableCount": protected_count,
            "protectedTablesDigest": protected_digest,
            "enabledCount": rls["enabledCount"],
            "forcedCount": rls["forcedCount"],
            "policyCount": rls["policyCount"],
            "passed": True,
        },
    }


def _parse_redis_info(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key] = value
    return result


def verify_redis(runner: Runner, containers: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    redis_id = str(containers["redis"].get("Id") or "")
    ping = runner.run(
        ["docker", "exec", redis_id, "sh", "-ec", REDIS_PING_SCRIPT],
        label="redis-ping",
    ).strip()
    require(ping == "PONG", "redis-ping", "Redis did not return PONG")
    info = _parse_redis_info(
        runner.run(
            ["docker", "exec", redis_id, "sh", "-ec", REDIS_PERSISTENCE_SCRIPT],
            label="redis-persistence",
        )
    )
    require(info.get("aof_enabled") == "1", "redis-persistence", "Redis AOF is disabled")
    require(
        info.get("aof_last_write_status") == "ok",
        "redis-persistence",
        "Redis AOF last write failed",
    )
    require(
        info.get("aof_last_bgrewrite_status") == "ok",
        "redis-persistence",
        "Redis AOF rewrite status is not healthy",
    )
    raw_size = info.get("aof_current_size", "")
    require(raw_size.isdigit(), "redis-persistence", "Redis AOF size is unavailable")
    return {
        "status": "passed",
        "ping": "PONG",
        "available": True,
        "aof": {
            "enabled": True,
            "lastWriteStatus": "ok",
            "lastRewriteStatus": "ok",
            "currentSizeBytes": int(raw_size),
        },
    }


def _s3_headers(
    *,
    method: str,
    url: str,
    access_key: str,
    secret_key: str,
    region: str,
    now: datetime,
) -> dict[str, str]:
    parsed = urlsplit(url)
    require(parsed.hostname == "127.0.0.1", "minio-buckets", "S3 target must be loopback")
    require(bool(access_key) and bool(secret_key), "minio-buckets", "MinIO credentials are absent")
    timestamp = now.astimezone(UTC)
    amz_date = timestamp.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = timestamp.strftime("%Y%m%d")
    host = parsed.netloc
    payload_hash = hashlib.sha256(b"").hexdigest()
    canonical_uri = quote(parsed.path or "/", safe="/-_.~")
    canonical_query = urlencode(
        sorted(parse_qsl(parsed.query, keep_blank_values=True)),
        doseq=True,
        quote_via=quote,
        safe="-_.~",
    )
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        [
            method,
            canonical_uri,
            canonical_query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    def sign(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    date_key = sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    region_key = sign(date_key, region)
    service_key = sign(region_key, "s3")
    signing_key = sign(service_key, "aws4_request")
    signature = hmac.new(
        signing_key,
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {
        "Authorization": authorization,
        "Host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }


def _xml_error_code(body: bytes) -> str:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as error:
        raise VerificationError("minio-buckets", "MinIO error response was not XML") from error
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "Code":
            return str(element.text or "")
    raise VerificationError("minio-buckets", "MinIO error response omitted its code")


def verify_minio_buckets(
    http: LocalHttpClient,
    containers: Mapping[str, dict[str, Any]],
    bindings: Mapping[tuple[str, str], int],
    *,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    environment = _container_environment(containers["minio"])
    access_key = environment.get("MINIO_ROOT_USER", "")
    secret_key = environment.get("MINIO_ROOT_PASSWORD", "")
    region = environment.get("MINIO_REGION_NAME") or "us-east-1"
    require(bool(access_key), "minio-buckets", "MinIO root user is absent")
    require(bool(secret_key), "minio-buckets", "MinIO root password is absent")
    require(
        bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,62}", region)),
        "minio-buckets",
        "MinIO region is invalid",
    )
    clock = now or (lambda: datetime.now(UTC))
    api_port = bindings[("minio", "9000/tcp")]
    results: list[dict[str, Any]] = []
    for bucket in MINIO_BUCKETS:
        bucket_url = f"http://127.0.0.1:{api_port}/{quote(bucket, safe='-_.~')}"
        request_time = clock()
        http.request(
            bucket_url,
            method="HEAD",
            headers=_s3_headers(
                method="HEAD",
                url=bucket_url,
                access_key=access_key,
                secret_key=secret_key,
                region=region,
                now=request_time,
            ),
            label=f"minio-{bucket}-exists",
        )

        policy_url = f"{bucket_url}?policy="
        _, policy_body = http.request(
            policy_url,
            headers=_s3_headers(
                method="GET",
                url=policy_url,
                access_key=access_key,
                secret_key=secret_key,
                region=region,
                now=clock(),
            ),
            expected_statuses={404},
            label=f"minio-{bucket}-policy",
        )
        require(
            _xml_error_code(policy_body) == "NoSuchBucketPolicy",
            "minio-buckets",
            f"{bucket} has an explicit bucket policy",
        )

        anonymous_url = f"{bucket_url}?list-type=2&max-keys=1"
        _, anonymous_body = http.request(
            anonymous_url,
            expected_statuses={403},
            label=f"minio-{bucket}-anonymous",
        )
        require(
            _xml_error_code(anonymous_body) == "AccessDenied",
            "minio-buckets",
            f"{bucket} did not deny anonymous access",
        )
        results.append(
            {
                "bucket": bucket,
                "exists": True,
                "policy": "none",
                "anonymousAccess": "denied",
                "private": True,
            }
        )

    return {
        "status": "passed",
        "credentialsPresent": True,
        "credentialsReported": False,
        "buckets": results,
    }


def _sensitive_values(containers: Mapping[str, dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for container in containers.values():
        raw_environment = (container.get("Config") or {}).get("Env") or []
        if not isinstance(raw_environment, list):
            continue
        for item in raw_environment:
            key, separator, value = str(item).partition("=")
            if separator and _SENSITIVE_KEY.search(key) and len(value) >= 8:
                values.add(value)
    return values


def _redact_structure(value: Any, sensitive_values: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _redact_structure(child, sensitive_values) for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_structure(child, sensitive_values) for child in value]
    if isinstance(value, str):
        redacted = _sanitize_text(value)
        for secret in sorted(sensitive_values, key=len, reverse=True):
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    return value


def _finalize_report(report: dict[str, Any], sensitive_values: set[str]) -> dict[str, Any]:
    redacted = _redact_structure(report, sensitive_values)
    redacted["environment"]["secretValuesIncluded"] = False
    digest_input = json.dumps(
        redacted,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    redacted["digest"] = {
        "algorithm": "sha256",
        "value": hashlib.sha256(digest_input).hexdigest(),
    }
    return redacted


def verify(
    *,
    env_file: Path | None = None,
    runner: Runner | None = None,
    http_opener: OpenerDirector | None = None,
    now: Callable[[], datetime] | None = None,
) -> tuple[dict[str, Any], int]:
    command_runner = runner or CommandRunner()
    resolved_env_file: Path | None = None
    port_overrides: dict[str, int] = {}
    containers: dict[str, dict[str, Any]] = {}
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "verifier": "verify_local_stack_live",
        "verifierVersion": SCRIPT_VERSION,
        "project": PROJECT_NAME,
        "generatedAt": (now or (lambda: datetime.now(UTC)))().astimezone(UTC).isoformat(),
        "status": "running",
        "environment": {
            "scope": "local-docker-live",
            "envFile": {"provided": env_file is not None, "path": None, "portOverrides": {}},
            "allowedHttpHosts": ["127.0.0.1"],
            "httpProxyUsed": False,
            "externalNetworkRequests": 0,
            "stackMutations": 0,
            "secretValuesIncluded": False,
        },
        "results": {},
        "commands": command_runner.commands,
    }
    try:
        resolved_env_file, port_overrides = load_port_overrides(env_file)
        report["environment"]["envFile"] = {
            "provided": resolved_env_file is not None,
            "path": str(resolved_env_file) if resolved_env_file else None,
            "portOverrides": dict(sorted(port_overrides.items())),
        }
        containers = collect_containers(command_runner)
        report["results"]["services"] = verify_service_states(containers)
        bindings_result, bindings = verify_bindings(containers, port_overrides)
        report["results"]["bindings"] = bindings_result
        report["results"]["runtimeEnvironment"] = verify_runtime_environment(containers)
        report["results"]["networks"] = verify_networks(command_runner, containers)
        http = LocalHttpClient(command_runner.commands, opener=http_opener)
        report["results"]["http"] = verify_http_endpoints(http, bindings)
        report["results"]["postgres"] = verify_postgres(command_runner, containers)
        report["results"]["redis"] = verify_redis(command_runner, containers)
        report["results"]["minio"] = verify_minio_buckets(
            http,
            containers,
            bindings,
            now=now,
        )
        report["status"] = "passed"
    except VerificationError as error:
        report["status"] = "failed"
        report["results"]["failure"] = {
            "check": error.check,
            "message": error.message,
            "failClosed": True,
        }
    except Exception as error:  # pragma: no cover - final safety boundary
        report["status"] = "failed"
        report["results"]["failure"] = {
            "check": "unexpected-error",
            "message": f"unexpected verifier error: {type(error).__name__}",
            "failClosed": True,
        }

    report["environment"]["localHttpRequests"] = sum(
        command.get("kind") == "http" for command in command_runner.commands
    )
    secrets = _sensitive_values(containers) if containers else set()
    final_report = _finalize_report(report, secrets)
    return final_report, 0 if final_report["status"] == "passed" else 1


def _atomic_write_json(path: Path, document: Mapping[str, Any]) -> None:
    output = _normalize_path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Optional dotenv file used only to cross-check configured host ports.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path for the complete JSON evidence report; stdout is the default.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report, exit_code = verify(env_file=args.env_file)
    if args.output is None:
        print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
        return exit_code

    try:
        _atomic_write_json(args.output, report)
    except OSError as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "check": "output",
                    "errorType": type(error).__name__,
                    "failClosed": True,
                },
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(_normalize_path(args.output)),
                "digest": report["digest"],
            },
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
