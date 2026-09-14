from __future__ import annotations

import hashlib
import io
import json
import subprocess
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest

from scripts import verify_local_stack_live as verifier

FIXED_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
HEAD = "f4a6c8e0b209"
INTERNAL_NETWORK = "klm-hub-local_klm_internal"
LOOPBACK_NETWORK = "klm-hub-local_klm_loopback"
HOST_PORTS = {
    ("fake-provider", "8080/tcp"): 8089,
    ("mailpit", "1025/tcp"): 1025,
    ("mailpit", "8025/tcp"): 8025,
    ("minio", "9000/tcp"): 9000,
    ("minio", "9001/tcp"): 9001,
    ("proxy", "8080/tcp"): 8080,
}


def _env_list(values: dict[str, str]) -> list[str]:
    return [f"{key}={value}" for key, value in sorted(values.items())]


def _app_environment() -> dict[str, str]:
    values = {key: "false" for key in verifier.FALSE_EXTERNAL_GATES}
    values.update({key: "" for key in verifier.EMPTY_EXTERNAL_KEYS})
    values.update(verifier.PROXY_ENV)
    values.update(
        {
            "AI_PROVIDER": "fake",
            "AWS_EC2_METADATA_DISABLED": "true",
            "DATABASE_URL": "postgresql://klm_app:db-password-secret@postgres/klm_legal_hub",
            "INTEGRATION_SECRET_ENCRYPTION_KEY": "integration-secret-value",
            "KLM_AI_ANTHROPIC_BASE_URL": "http://fake-provider:8080/v1",
            "KLM_AI_GEMINI_BASE_URL": "http://fake-provider:8080/v1",
            "KLM_AI_OPENAI_BASE_URL": "http://fake-provider:8080/v1",
            "KLM_RUNTIME_MODE": "local_simulation",
            "OLLAMA_BASE_URL": "http://fake-provider:8080",
            "SECRET_KEY": "application-secret-value",
        }
    )
    return values


def _migrate_environment() -> dict[str, str]:
    values = {key: "false" for key in verifier.MIGRATE_FALSE_GATES}
    values.update(verifier.PROXY_ENV)
    values["AWS_EC2_METADATA_DISABLED"] = "true"
    values["DATABASE_URL"] = "postgresql://klm_migrator:migrator-secret@postgres/klm_legal_hub"
    return values


def _service_environment(service: str) -> dict[str, str]:
    if service in verifier.APP_SERVICES:
        return _app_environment()
    if service == "migrate":
        return _migrate_environment()
    if service == "fake-provider":
        return dict(verifier.PROXY_ENV)
    if service == "minio":
        return {
            "MINIO_ROOT_USER": "klmminio",
            "MINIO_ROOT_PASSWORD": "minio-super-secret",
        }
    if service == "redis":
        return {"KLM_REDIS_PASSWORD": "redis-super-secret"}
    return {}


def _containers() -> dict[str, dict[str, Any]]:
    containers: dict[str, dict[str, Any]] = {}
    for index, service in enumerate(sorted(verifier.EXPECTED_SERVICES), start=1):
        persistent = service in verifier.PERSISTENT_SERVICES
        network_names = [INTERNAL_NETWORK]
        if service in verifier.LOOPBACK_SERVICES:
            network_names.append(LOOPBACK_NETWORK)
        ports: dict[str, list[dict[str, str]]] = {}
        for (binding_service, target), host_port in HOST_PORTS.items():
            if binding_service == service:
                ports[target] = [{"HostIp": "127.0.0.1", "HostPort": str(host_port)}]
        containers[service] = {
            "Id": f"{index:064x}",
            "Config": {
                "Image": f"klm/test-{service}:pinned",
                "Labels": {
                    "com.docker.compose.project": verifier.PROJECT_NAME,
                    "com.docker.compose.service": service,
                },
                "Env": _env_list(_service_environment(service)),
            },
            "State": (
                {"Status": "running", "Health": {"Status": "healthy"}}
                if persistent
                else {"Status": "exited", "ExitCode": 0}
            ),
            "NetworkSettings": {
                "Networks": {name: {} for name in network_names},
                "Ports": ports,
            },
        }
    return containers


def _network_documents() -> list[dict[str, Any]]:
    return [
        {
            "Name": INTERNAL_NETWORK,
            "Driver": "bridge",
            "Internal": True,
            "Options": {"com.docker.network.bridge.enable_ip_masquerade": "false"},
            "Labels": {
                "com.docker.compose.project": verifier.PROJECT_NAME,
                "com.docker.compose.network": "klm_internal",
            },
        },
        {
            "Name": LOOPBACK_NETWORK,
            "Driver": "bridge",
            "Internal": False,
            "Options": {"com.docker.network.bridge.enable_ip_masquerade": "false"},
            "Labels": {
                "com.docker.compose.project": verifier.PROJECT_NAME,
                "com.docker.compose.network": "klm_loopback",
            },
        },
    ]


def _postgres_catalog(**overrides: Any) -> dict[str, Any]:
    catalog: dict[str, Any] = {
        "alembicRevisions": [HEAD],
        "vectorVersion": "0.8.1",
        "indexes": [
            {
                "name": "ix_knowledge_chunks_embedding_hnsw",
                "method": "hnsw",
                "table": "knowledge_chunks",
                "definition": (
                    "CREATE INDEX ix_knowledge_chunks_embedding_hnsw ON "
                    "knowledge_chunks USING hnsw (embedding_vector vector_cosine_ops)"
                ),
            },
            {
                "name": "ix_knowledge_chunks_search_document_gin",
                "method": "gin",
                "table": "knowledge_chunks",
                "definition": (
                    "CREATE INDEX ix_knowledge_chunks_search_document_gin ON "
                    "knowledge_chunks USING gin (search_document)"
                ),
            },
        ],
        "compositeForeignKey": {
            "name": "fk_knowledge_chunks_matter_workspace",
            "table": "knowledge_chunks",
            "referencedTable": "matters",
            "columns": ["matter_id", "workspace_id"],
            "referencedColumns": ["id", "workspace_id"],
        },
        "runtimeRole": {
            "name": "klm_app",
            "canLogin": True,
            "superuser": False,
            "bypassRls": False,
            "createRole": False,
            "createDatabase": False,
            "replication": False,
            "inheritsPrivileges": False,
            "canAssumePrivilegedRole": False,
        },
        "rls": {
            "enabledCount": 73,
            "forcedCount": 73,
            "enabledNotForced": [],
            "policyCount": 73,
        },
    }
    catalog.update(overrides)
    return catalog


class FakeRunner:
    def __init__(
        self,
        containers: dict[str, dict[str, Any]] | None = None,
        *,
        catalog: dict[str, Any] | None = None,
        networks: list[dict[str, Any]] | None = None,
        redis_info: str | None = None,
    ) -> None:
        self.containers = containers or _containers()
        self.catalog = catalog or _postgres_catalog()
        self.networks = networks or _network_documents()
        self.redis_info = redis_info or (
            "# Persistence\r\n"
            "aof_enabled:1\r\n"
            "aof_last_bgrewrite_status:ok\r\n"
            "aof_last_write_status:ok\r\n"
            "aof_current_size:128\r\n"
        )
        self.commands: list[dict[str, Any]] = []

    def run(self, args, *, label: str, timeout: int = 30) -> str:
        del timeout
        normalized = [str(item) for item in args]
        verifier._validate_read_only_command(normalized)
        self.commands.append(
            {
                "id": len(self.commands) + 1,
                "kind": "subprocess",
                "label": label,
                "argv": verifier._display_argv(normalized),
                "status": "passed",
                "exitCode": 0,
                "durationMs": 0,
            }
        )
        responses = {
            "containers-discovery": "\n".join(
                str(container["Id"]) for container in self.containers.values()
            ),
            "containers-inspect": json.dumps(list(self.containers.values())),
            "networks-inspect": json.dumps(self.networks),
            "egress-denial-canary": json.dumps(
                {
                    "connected": False,
                    "defaultRoutePresent": False,
                    "errno": 101,
                    "routeCount": 1,
                    "target": "198.51.100.1:443",
                }
            ),
            "alembic-heads": f"{HEAD} (head)\n",
            "alembic-current": f"{HEAD} (head)\n",
            "postgres-contract": json.dumps(self.catalog) + "\n",
            "rls-runtime-audit": json.dumps(
                {
                    "roleName": "klm_app",
                    "protectedTableCount": 73,
                    "protectedTablesDigest": "a" * 64,
                }
            ),
            "redis-ping": "PONG\n",
            "redis-persistence": self.redis_info,
        }
        return responses[label]


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def getcode(self) -> int:
        return self.status

    def read(self, _limit: int = -1) -> bytes:
        return self.body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class FakeOpener:
    def __init__(self, *, public_bucket: str | None = None) -> None:
        self.public_bucket = public_bucket
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def open(self, request, *, timeout: float):
        del timeout
        url = request.full_url
        method = request.get_method()
        headers = dict(request.header_items())
        self.calls.append((method, url, headers))
        parsed = urlsplit(url)

        if parsed.path == "/healthz":
            return FakeResponse(200, b"ok\n")
        if parsed.path == "/health":
            if parsed.port == HOST_PORTS[("fake-provider", "8080/tcp")]:
                return FakeResponse(200, b'{"status":"ok","provider":"fake"}')
            return FakeResponse(200, b'{"status":"ok","version":"1.0","environment":"local"}')
        if parsed.path == "/api/v1/info":
            return FakeResponse(200, b'{"version":"1.27.0"}')
        if parsed.path == "/minio/health/live":
            return FakeResponse(200, b"")

        bucket = parsed.path.lstrip("/")
        if bucket in verifier.MINIO_BUCKETS and method == "HEAD":
            assert any(key.lower() == "authorization" for key in headers)
            return FakeResponse(200, b"")
        if bucket in verifier.MINIO_BUCKETS and parsed.query == "policy=":
            assert any(key.lower() == "authorization" for key in headers)
            raise HTTPError(
                url,
                404,
                "Not Found",
                {},
                io.BytesIO(b"<Error><Code>NoSuchBucketPolicy</Code></Error>"),
            )
        if bucket in verifier.MINIO_BUCKETS and parsed.query == "list-type=2&max-keys=1":
            if bucket == self.public_bucket:
                return FakeResponse(200, b"<ListBucketResult />")
            raise HTTPError(
                url,
                403,
                "Forbidden",
                {},
                io.BytesIO(b"<Error><Code>AccessDenied</Code></Error>"),
            )
        raise AssertionError(f"unexpected HTTP request: {method} {url}")


def _resolved_bindings(containers: dict[str, dict[str, Any]]) -> dict[tuple[str, str], int]:
    _, bindings = verifier.verify_bindings(containers, {})
    return bindings


def test_command_runner_mocks_subprocess_and_records_only_sanitized_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="sensitive-command-output", stderr="")

    monkeypatch.setenv("UNRELATED_SECRET", "must-not-propagate")
    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    runner = verifier.CommandRunner()

    output = runner.run(
        ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=klm-hub-local"],
        label="containers-discovery",
    )

    assert output == "sensitive-command-output"
    assert "UNRELATED_SECRET" not in captured["env"]
    assert "sensitive-command-output" not in json.dumps(runner.commands)
    assert runner.commands[0]["status"] == "passed"


@pytest.mark.parametrize("subcommand", ["build", "up", "pull", "down"])
def test_command_policy_rejects_stack_mutations(subcommand: str) -> None:
    with pytest.raises(verifier.VerificationError, match="not read-only allowlisted"):
        verifier._validate_read_only_command(["docker", subcommand])


def test_http_client_mocks_http_and_rejects_external_hosts_before_opening() -> None:
    opener = FakeOpener()
    client = verifier.LocalHttpClient([], opener=opener)

    with pytest.raises(verifier.VerificationError, match="127.0.0.1"):
        client.request("http://example.com:80/health", label="external")

    assert opener.calls == []


def test_optional_env_file_only_exposes_safe_port_values(tmp_path: Path) -> None:
    env_file = tmp_path / "stack.env"
    env_file.write_text(
        "KLM_PROXY_PORT=18080\nKLM_MINIO_PORT='19000'\nKLM_POSTGRES_PASSWORD=do-not-return\n",
        encoding="utf-8",
    )

    path, ports = verifier.load_port_overrides(env_file)

    assert path == env_file
    assert ports == {"KLM_PROXY_PORT": 18080, "KLM_MINIO_PORT": 19000}
    assert verifier.load_port_overrides(None) == (None, {})
    assert "do-not-return" not in json.dumps(ports)


@pytest.mark.parametrize(
    ("service", "state"),
    [
        ("api", {"Status": "running", "Health": {"Status": "unhealthy"}}),
        ("migrate", {"Status": "exited", "ExitCode": 1}),
    ],
)
def test_service_state_failures_are_closed(service: str, state: dict[str, Any]) -> None:
    containers = _containers()
    containers[service]["State"] = state

    with pytest.raises(verifier.VerificationError, match=service):
        verifier.verify_service_states(containers)


def test_bindings_require_exact_loopback_and_match_env_file() -> None:
    containers = _containers()
    result, bindings = verifier.verify_bindings(containers, {"KLM_PROXY_PORT": 8080})

    assert result["loopbackOnly"] is True
    assert result["publishedCount"] == 6
    assert bindings[("proxy", "8080/tcp")] == 8080

    containers["proxy"]["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostIp"] = "0.0.0.0"
    with pytest.raises(verifier.VerificationError, match="127.0.0.1"):
        verifier.verify_bindings(containers, {})


@pytest.mark.parametrize(
    ("key", "unsafe_value"),
    [("KLM_ONLINE_APPROVED", "true"), ("OPENAI_API_KEY", "external-key")],
)
def test_runtime_environment_fails_on_open_gate_or_nonempty_key(
    key: str,
    unsafe_value: str,
) -> None:
    containers = _containers()
    environment = _service_environment("api")
    environment[key] = unsafe_value
    containers["api"]["Config"]["Env"] = _env_list(environment)

    with pytest.raises(verifier.VerificationError, match=key):
        verifier.verify_runtime_environment(containers)


def test_networks_require_internal_and_no_masquerade() -> None:
    containers = _containers()
    runner = FakeRunner(containers)

    result = verifier.verify_networks(runner, containers)

    assert result["externalEgressAllowed"] is False
    assert result["egressProbe"] == {
        "status": "passed",
        "container": "api",
        "target": "TEST-NET-2 198.51.100.1:443",
        "defaultRoutePresent": False,
        "connected": False,
        "errno": 101,
        "kernelRouteTableInspected": True,
    }
    assert {network["logicalName"] for network in result["networks"]} == {
        "klm_internal",
        "klm_loopback",
    }

    unsafe_networks = _network_documents()
    unsafe_networks[1]["Options"]["com.docker.network.bridge.enable_ip_masquerade"] = "true"
    with pytest.raises(verifier.VerificationError, match="masquerade"):
        verifier.verify_networks(FakeRunner(containers, networks=unsafe_networks), containers)


def test_http_health_contracts_are_all_local_and_mocked() -> None:
    containers = _containers()
    opener = FakeOpener()
    commands: list[dict[str, Any]] = []
    client = verifier.LocalHttpClient(commands, opener=opener)

    result = verifier.verify_http_endpoints(client, _resolved_bindings(containers))

    assert result["status"] == "passed"
    assert len(result["checks"]) == 5
    assert all(urlsplit(url).hostname == "127.0.0.1" for _, url, _ in opener.calls)
    assert all(command["kind"] == "http" for command in commands)


@pytest.mark.parametrize(
    ("catalog_change", "message"),
    [
        ({"vectorVersion": "0.8.0"}, "pgvector"),
        (
            {
                "runtimeRole": {
                    **_postgres_catalog()["runtimeRole"],
                    "bypassRls": True,
                }
            },
            "bypassRls",
        ),
        (
            {
                "rls": {
                    "enabledCount": 73,
                    "forcedCount": 72,
                    "enabledNotForced": ["clients"],
                    "policyCount": 73,
                }
            },
            "not forced",
        ),
    ],
)
def test_postgres_contract_fails_closed(
    catalog_change: dict[str, Any],
    message: str,
) -> None:
    containers = _containers()
    catalog = _postgres_catalog(**catalog_change)

    with pytest.raises(verifier.VerificationError, match=message):
        verifier.verify_postgres(FakeRunner(containers, catalog=catalog), containers)


def test_redis_requires_ping_aof_and_healthy_persistence() -> None:
    containers = _containers()
    passed = verifier.verify_redis(FakeRunner(containers), containers)

    assert passed["ping"] == "PONG"
    assert passed["aof"]["enabled"] is True

    disabled = (
        "aof_enabled:0\n"
        "aof_last_write_status:ok\n"
        "aof_last_bgrewrite_status:ok\n"
        "aof_current_size:0\n"
    )
    with pytest.raises(verifier.VerificationError, match="AOF is disabled"):
        verifier.verify_redis(FakeRunner(containers, redis_info=disabled), containers)


def test_minio_buckets_require_authenticated_existence_and_anonymous_denial() -> None:
    containers = _containers()
    bindings = _resolved_bindings(containers)
    opener = FakeOpener()
    client = verifier.LocalHttpClient([], opener=opener)

    result = verifier.verify_minio_buckets(
        client,
        containers,
        bindings,
        now=lambda: FIXED_NOW,
    )

    assert [item["bucket"] for item in result["buckets"]] == list(verifier.MINIO_BUCKETS)
    assert all(item["private"] for item in result["buckets"])
    assert "minio-super-secret" not in json.dumps(result)

    public_opener = FakeOpener(public_bucket=verifier.MINIO_BUCKETS[0])
    with pytest.raises(verifier.VerificationError, match="unexpected status 200"):
        verifier.verify_minio_buckets(
            verifier.LocalHttpClient([], opener=public_opener),
            containers,
            bindings,
            now=lambda: FIXED_NOW,
        )


def test_verify_emits_complete_secret_free_digest_report() -> None:
    runner = FakeRunner()
    report, exit_code = verifier.verify(
        runner=runner,
        http_opener=FakeOpener(),
        now=lambda: FIXED_NOW,
    )

    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["results"]["services"]["persistentHealthy"] == 9
    assert report["results"]["services"]["oneShotsExitedZero"] == 3
    assert report["results"]["postgres"]["alembic"]["atHead"] is True
    assert report["results"]["redis"]["aof"]["enabled"] is True
    assert report["environment"]["externalNetworkRequests"] == 0
    assert report["environment"]["stackMutations"] == 0

    serialized = json.dumps(report, sort_keys=True)
    for secret in (
        "application-secret-value",
        "integration-secret-value",
        "db-password-secret",
        "migrator-secret",
        "minio-super-secret",
        "redis-super-secret",
    ):
        assert secret not in serialized
    forbidden = {"build", "up", "pull", "down"}
    for command in report["commands"]:
        argv = command["argv"]
        if argv[0] == "docker":
            assert argv[1] not in forbidden

    digest = report["digest"]
    without_digest = deepcopy(report)
    without_digest.pop("digest")
    expected = hashlib.sha256(
        json.dumps(
            without_digest,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    assert digest == {"algorithm": "sha256", "value": expected}


def test_verify_failure_still_returns_json_digest_without_secrets() -> None:
    containers = _containers()
    containers["api"]["State"]["Health"]["Status"] = "unhealthy"

    report, exit_code = verifier.verify(
        runner=FakeRunner(containers),
        http_opener=FakeOpener(),
        now=lambda: FIXED_NOW,
    )

    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["results"]["failure"]["check"] == "service-state"
    assert report["results"]["failure"]["failClosed"] is True
    assert len(report["digest"]["value"]) == 64
    assert "application-secret-value" not in json.dumps(report)


def test_verify_malformed_container_env_still_fails_with_json() -> None:
    containers = _containers()
    containers["api"]["Config"]["Env"].append("MALFORMED_ENTRY")

    report, exit_code = verifier.verify(
        runner=FakeRunner(containers),
        http_opener=FakeOpener(),
        now=lambda: FIXED_NOW,
    )

    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["results"]["failure"]["check"] == "environment"
    assert len(report["digest"]["value"]) == 64


def test_main_writes_full_json_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "live.json"
    expected_report = {
        "status": "passed",
        "digest": {"algorithm": "sha256", "value": "b" * 64},
    }
    monkeypatch.setattr(verifier, "verify", lambda **_kwargs: (expected_report, 0))

    exit_code = verifier.main(["--output", str(output)])

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8")) == expected_report
    assert json.loads(capsys.readouterr().out)["output"] == str(output)
    assert output.stat().st_mode & 0o777 == 0o600
