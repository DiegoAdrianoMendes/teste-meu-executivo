#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.yml"

SAFE_ENV = {
    "KLM_SECRET_KEY": "local-verifier-secret-key-0123456789abcdef",
    "KLM_INTEGRATION_SECRET_ENCRYPTION_KEY": ("local-verifier-integration-key-fedcba9876543210"),
    "KLM_POSTGRES_PASSWORD": "local-verifier-postgres-password",
    "KLM_APP_DB_PASSWORD": "local-verifier-app-password",
    "KLM_REDIS_PASSWORD": "local-verifier-redis-password",
    "KLM_MINIO_ROOT_PASSWORD": "local-verifier-minio-password",
}

EXPECTED_SERVICES = {
    "api",
    "beat",
    "fake-provider",
    "mailpit",
    "migrate",
    "minio",
    "minio-init",
    "postgres",
    "proxy",
    "redis",
    "rls-role-bootstrap",
    "worker",
}
ONE_SHOT_SERVICES = {"migrate", "minio-init", "rls-role-bootstrap"}
CUSTOM_NON_ROOT_SERVICES = {"api", "beat", "fake-provider", "migrate", "proxy", "worker"}
APP_SERVICES = {"api", "beat", "worker"}
LOOPBACK_PUBLISHED_SERVICES = {"fake-provider", "mailpit", "minio", "proxy"}
OPTIONAL_PROFILES = {
    "beat": {"agents", "full"},
    "fake-provider": {"fake", "full"},
    "mailpit": {"mail", "full"},
    "minio": {"storage", "full"},
    "minio-init": {"storage", "full"},
    "worker": {"agents", "full"},
}
ALLOWED_HTTP_HOSTS = {
    "127.0.0.1",
    "api",
    "fake-provider",
    "localhost",
    "mailpit",
    "minio",
    "proxy",
}
FORBIDDEN_PROVIDER_DOMAINS = {
    "amazonaws.com",
    "anthropic.com",
    "api.openai.com",
    "googleapis.com",
    "resend.com",
}
PINNED_CACHE_IMAGES = (
    "python:3.12.13-slim-bookworm",
    "ghcr.io/astral-sh/uv:0.8.22",
    "node:24.18.0-alpine3.22",
    "nginx:1.29.5-alpine",
    "pgvector/pgvector:0.8.1-pg16",
    "redis:7.4.2-alpine",
    "minio/minio:RELEASE.2025-04-22T22-12-26Z",
    "minio/mc:RELEASE.2025-04-16T18-13-26Z",
    "axllent/mailpit:v1.27.0",
)


def _command_env() -> dict[str, str]:
    allowed_host_keys = {
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        "DOCKER_HOST",
        "HOME",
        "PATH",
        "TMPDIR",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed_host_keys}
    environment.update(SAFE_ENV)
    return environment


def render_compose() -> dict[str, Any]:
    command = [
        "docker",
        "compose",
        "--env-file",
        "/dev/null",
        "--profile",
        "full",
        "-f",
        str(COMPOSE_FILE),
        "config",
        "--format",
        "json",
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=_command_env(),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown compose error"
        raise AssertionError(f"docker compose config failed: {detail}")
    return json.loads(result.stdout)


def _assert_pinned_image(service_name: str, image: str) -> None:
    assert image, f"{service_name}: image is required"
    assert not image.endswith(":latest"), f"{service_name}: latest tag is forbidden"
    final_segment = image.rsplit("/", maxsplit=1)[-1]
    assert ":" in final_segment or "@sha256:" in image, (
        f"{service_name}: image must use an explicit tag or digest"
    )


def _iter_http_urls(value: str):
    yield from re.findall(r"https?://[^\s\"'\],]+", value)


def assert_compose_contract(document: dict[str, Any]) -> None:
    assert document.get("name") == "klm-hub-local"
    services = document.get("services", {})
    assert set(services) == EXPECTED_SERVICES

    networks = document.get("networks", {})
    network = networks.get("klm_internal", {})
    assert network.get("driver") == "bridge"
    assert network.get("internal") is True
    assert (
        network.get("driver_opts", {}).get("com.docker.network.bridge.enable_ip_masquerade")
        == "false"
    )
    loopback = networks.get("klm_loopback", {})
    assert loopback.get("driver") == "bridge"
    assert loopback.get("internal") is not True
    assert (
        loopback.get("driver_opts", {}).get(
            "com.docker.network.bridge.enable_ip_masquerade"
        )
        == "false"
    )

    long_running = EXPECTED_SERVICES - ONE_SHOT_SERVICES
    for service_name, service in services.items():
        attached_networks = service.get("networks", {})
        expected_networks = {"klm_internal"}
        if service_name in LOOPBACK_PUBLISHED_SERVICES:
            expected_networks.add("klm_loopback")
        assert set(attached_networks) == expected_networks, (
            f"{service_name}: unexpected network attachment"
        )
        assert service.get("network_mode") != "host"
        assert not service.get("privileged", False)
        assert not service.get("extra_hosts")
        assert not service.get("external_links")
        assert not service.get("env_file")
        assert service.get("pull_policy") == "never"

        _assert_pinned_image(service_name, str(service.get("image", "")))
        assert float(service.get("cpus", 0)) > 0, f"{service_name}: CPU limit missing"
        assert int(service.get("mem_limit", 0)) > 0, f"{service_name}: memory limit missing"
        assert int(service.get("pids_limit", 0)) > 0, f"{service_name}: PID limit missing"

        logging = service.get("logging", {})
        assert logging.get("driver") == "json-file"
        assert logging.get("options", {}).get("max-size") == "10m"
        assert logging.get("options", {}).get("max-file") == "3"

        for port in service.get("ports", []):
            assert service_name in LOOPBACK_PUBLISHED_SERVICES
            assert port.get("host_ip") == "127.0.0.1", (
                f"{service_name}: published ports must be loopback-only"
            )

        for volume in service.get("volumes", []):
            if volume.get("type") != "bind":
                continue
            source = str(volume.get("source", ""))
            assert source.endswith("/docker/postgres/bootstrap-runtime-role.sh"), (
                f"{service_name}: unexpected host bind mount {source}"
            )
            assert volume.get("read_only") is True

        if service_name in long_running:
            assert service.get("restart") == "unless-stopped"
            assert service.get("healthcheck", {}).get("test"), (
                f"{service_name}: healthcheck missing"
            )
        else:
            assert service.get("restart") == "no"

        environment = service.get("environment", {})
        for gate in (
            "KLM_AWS_ENABLED",
            "KLM_EXTERNAL_ACTIONS_ENABLED",
            "KLM_MODEL_APIS_ENABLED",
            "KLM_ONLINE_APPROVED",
        ):
            if gate in environment:
                assert environment[gate] == "false", f"{service_name}: {gate} must be false"

    for service_name in CUSTOM_NON_ROOT_SERVICES:
        service = services[service_name]
        assert service.get("user") not in {None, "", "0", "0:0", "root"}
        assert service.get("read_only") is True
        assert "ALL" in service.get("cap_drop", [])
        assert "no-new-privileges:true" in service.get("security_opt", [])

    assert not services["postgres"].get("ports")
    assert not services["redis"].get("ports")
    assert not services["api"].get("ports")
    assert services["postgres"]["image"] == "pgvector/pgvector:0.8.1-pg16"

    for service_name, profiles in OPTIONAL_PROFILES.items():
        assert set(services[service_name].get("profiles", [])) == profiles

    assert services["api"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["proxy"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert services["minio-init"]["depends_on"]["minio"]["condition"] == ("service_healthy")
    assert services["minio-init"]["environment"]["MC_CONFIG_DIR"] == "/tmp/mc"

    for service_name in APP_SERVICES:
        environment = services[service_name].get("environment", {})
        assert environment.get("AI_PROVIDER") == "fake"
        assert environment.get("KLM_RUNTIME_MODE") == "local_simulation"
        assert environment.get("KLM_LOCAL_REDIS_ENABLED") == "true"
        assert environment.get("KLM_AI_RESPONSE_CACHE_ENABLED") == "true"
        assert environment.get("KLM_AI_RESPONSE_CACHE_BACKEND") == "redis"
        for credential in (
            "ANTHROPIC_API_KEY",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "GEMINI_API_KEY",
            "OPENAI_API_KEY",
            "RESEND_API_KEY",
        ):
            assert environment.get(credential) == ""

    for service_name, service in services.items():
        for value in service.get("environment", {}).values():
            if not isinstance(value, str):
                continue
            lowered = value.casefold()
            assert not any(domain in lowered for domain in FORBIDDEN_PROVIDER_DOMAINS), (
                f"{service_name}: external provider domain found in environment"
            )
            for raw_url in _iter_http_urls(value):
                hostname = (urlsplit(raw_url).hostname or "").casefold()
                assert hostname in ALLOWED_HTTP_HOSTS, (
                    f"{service_name}: external HTTP endpoint {hostname!r} is forbidden"
                )


def assert_static_files() -> None:
    backend = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    frontend = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")
    fake = (ROOT / "docker/fake-provider/Dockerfile").read_text(encoding="utf-8")
    proxy = (ROOT / "docker/frontend/nginx.conf").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    dependency_lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")

    assert len(re.findall(r"(?m)^FROM ", backend)) >= 3
    assert "uv pip sync" in backend
    assert "--require-hashes" in backend
    assert "COPY requirements.lock" in backend
    assert "docs/oss-function-units.lock.json" in backend
    assert "docs/oss-upstreams.lock.json" in backend
    assert "from scripts." not in (ROOT / "app/services/oss_function_unit_service.py").read_text(
        encoding="utf-8"
    )
    assert "# This file was autogenerated by uv" in dependency_lock
    assert "alembic==1.13.3" in dependency_lock
    assert "celery==5.4.0" in dependency_lock
    assert "uvicorn==0.30.6" in dependency_lock
    assert "USER 10001:10001" in backend
    assert "COPY . ." not in backend
    assert "pip install --upgrade" not in backend

    assert len(re.findall(r"(?m)^FROM ", frontend)) == 2
    assert "npm ci" in frontend
    assert "USER 101:101" in frontend
    assert "docker/frontend/nginx.conf" in frontend
    assert "VITE_API_URL=https://" not in frontend

    assert "USER 10001:10001" in fake
    assert "listen 8080;" in proxy
    assert "proxy_pass http://api:8000;" in proxy
    assert "location = /health" in proxy
    assert "proxy_pass http://api:8000/health;" in proxy
    assert "connect-src 'self'" in proxy

    required_ignores = {
        ".env",
        ".git",
        ".local",
        ".venv",
        ".vendor",
        "**/__pycache__",
        "**/node_modules",
        "docker/oss",
        "tests",
    }
    assert required_ignores.issubset(set(dockerignore.splitlines()))
    assert "!docs/oss-function-units.lock.json" in dockerignore
    assert "!docs/oss-upstreams.lock.json" in dockerignore

    for content, filename in (
        (backend, "Dockerfile"),
        (frontend, "Dockerfile.frontend"),
        (fake, "docker/fake-provider/Dockerfile"),
    ):
        assert ":latest" not in content, f"{filename}: latest tag is forbidden"


def inspect_image_cache() -> tuple[list[str], list[str]]:
    present: list[str] = []
    missing: list[str] = []
    for image in PINNED_CACHE_IMAGES:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            cwd=ROOT,
            env=_command_env(),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        (present if result.returncode == 0 else missing).append(image)
    return present, missing


def main() -> int:
    try:
        assert_static_files()
        document = render_compose()
        assert_compose_contract(document)
    except (AssertionError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"LOCAL_STACK_INVALID: {exc}", file=sys.stderr)
        return 1

    print(f"LOCAL_STACK_OK: {len(document['services'])} services, offline contract valid")
    present, missing = inspect_image_cache()
    print(f"IMAGE_CACHE_PRESENT: {len(present)}/{len(PINNED_CACHE_IMAGES)}")
    if missing:
        print("IMAGE_BUILD_SKIPPED: pinned images are not cached; no pull was attempted")
        for image in missing:
            print(f"  missing: {image}")
    else:
        print("IMAGE_CACHE_READY: validation still did not build or start containers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
