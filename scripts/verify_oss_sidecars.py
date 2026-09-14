#!/usr/bin/env python3
"""Verify all four KLM local OSS sidecar profiles and their pinned sources."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.oss.yml"
EXAMPLE_ENV_FILE = ROOT / ".env.oss.example"
LOCAL_ENV_FILE = ROOT / ".local" / "oss.env"
LOCK_FILE = ROOT / "docs" / "oss-upstreams.lock.json"
PROJECT_NAME = "klm-oss-local"

PROFILE_SERVICES = {
    "channels": {
        "chatwoot-postgres",
        "chatwoot-redis",
        "chatwoot-init",
        "chatwoot-bootstrap",
        "chatwoot-web",
        "chatwoot-worker",
        "chatwoot-gateway",
    },
    "collaboration": {
        "rocket-mongo",
        "rocket-mongo-init",
        "rocketchat",
        "rocket-bootstrap",
        "rocket-gateway",
    },
    "automation": {
        "chattermate-postgres",
        "chattermate-redis",
        "chattermate-init",
        "chattermate-api",
        "chattermate-worker",
        "chattermate-frontend",
        "chattermate-bootstrap",
        "chattermate-gateway",
    },
    "crm": {
        "bottlecrm-postgres",
        "bottlecrm-redis",
        "bottlecrm-init",
        "bottlecrm-backend",
        "bottlecrm-worker",
        "bottlecrm-beat",
        "bottlecrm-frontend",
        "bottlecrm-gateway",
    },
}
ALL_SERVICES = set().union(*PROFILE_SERVICES.values())
SERVICE_PROFILE = {
    service: profile for profile, services in PROFILE_SERVICES.items() for service in services
}
ONE_SHOT_SERVICES = {
    "chatwoot-init",
    "chatwoot-bootstrap",
    "rocket-mongo-init",
    "rocket-bootstrap",
    "chattermate-init",
    "chattermate-bootstrap",
    "bottlecrm-init",
}
LONG_RUNNING_SERVICES = ALL_SERVICES - ONE_SHOT_SERVICES
GATEWAYS = {
    "channels": ("chatwoot-gateway", "KLM_OSS_CHATWOOT_PORT", "/health"),
    "collaboration": ("rocket-gateway", "KLM_OSS_ROCKETCHAT_PORT", "/livez"),
    "automation": ("chattermate-gateway", "KLM_OSS_CHATTERMATE_PORT", "/healthz"),
    "crm": ("bottlecrm-gateway", "KLM_OSS_BOTTLECRM_PORT", "/healthz"),
}

SERVICE_IMAGES = {
    "chatwoot-postgres": "pgvector/pgvector:0.8.1-pg16",
    "chatwoot-redis": "redis:7.4.2-alpine",
    "chatwoot-init": "chatwoot/chatwoot:v4.15.1",
    "chatwoot-bootstrap": "chatwoot/chatwoot:v4.15.1",
    "chatwoot-web": "chatwoot/chatwoot:v4.15.1",
    "chatwoot-worker": "chatwoot/chatwoot:v4.15.1",
    "chatwoot-gateway": "nginx:1.29.5-alpine",
    "rocket-mongo": "mongodb/mongodb-community-server:6.0.26-ubi8",
    "rocket-mongo-init": "mongodb/mongodb-community-server:6.0.26-ubi8",
    "rocketchat": "registry.rocket.chat/rocketchat/rocket.chat:6.13.1",
    "rocket-bootstrap": "python:3.12.13-alpine3.22",
    "rocket-gateway": "nginx:1.29.5-alpine",
    "chattermate-postgres": "klm/chattermate-postgres:1.0.14-local",
    "chattermate-redis": "redis:7.4.2-alpine",
    "chattermate-init": "klm/chattermate-backend:1.0.14-local",
    "chattermate-api": "klm/chattermate-backend:1.0.14-local",
    "chattermate-worker": "klm/chattermate-backend:1.0.14-local",
    "chattermate-frontend": "klm/chattermate-frontend:1.0.14-local",
    "chattermate-bootstrap": "python:3.12.13-alpine3.22",
    "chattermate-gateway": "klm/chattermate-gateway:1.0.14-local",
    "bottlecrm-postgres": "postgres:16.8-alpine",
    "bottlecrm-redis": "redis:7.4.2-alpine",
    "bottlecrm-init": "klm/bottlecrm-backend:3ab79d75-local",
    "bottlecrm-backend": "klm/bottlecrm-backend:3ab79d75-local",
    "bottlecrm-worker": "klm/bottlecrm-backend:3ab79d75-local",
    "bottlecrm-beat": "klm/bottlecrm-backend:3ab79d75-local",
    "bottlecrm-frontend": "klm/bottlecrm-frontend:3ab79d75-local",
    "bottlecrm-gateway": "klm/bottlecrm-gateway:3ab79d75-local",
}
CUSTOM_BUILD_SERVICES = {
    "chattermate-postgres",
    "chattermate-init",
    "chattermate-api",
    "chattermate-worker",
    "chattermate-frontend",
    "chattermate-gateway",
    "bottlecrm-init",
    "bottlecrm-backend",
    "bottlecrm-worker",
    "bottlecrm-beat",
    "bottlecrm-frontend",
    "bottlecrm-gateway",
}
EXPECTED_VOLUMES = {
    "chatwoot-postgres-data",
    "chatwoot-redis-data",
    "chatwoot-storage",
    "rocket-mongo-data",
    "chattermate-config",
    "chattermate-model-cache",
    "chattermate-postgres-data",
    "chattermate-redis-data",
    "chattermate-temp",
    "chattermate-uploads",
    "bottlecrm-media",
    "bottlecrm-postgres-data",
    "bottlecrm-redis-data",
    "bottlecrm-static",
}
EXPECTED_DEPENDENCIES = {
    "chatwoot-init": {"chatwoot-postgres", "chatwoot-redis"},
    "chatwoot-bootstrap": {"chatwoot-init", "chatwoot-postgres", "chatwoot-redis"},
    "chatwoot-web": {"chatwoot-bootstrap", "chatwoot-postgres", "chatwoot-redis"},
    "chatwoot-worker": {"chatwoot-bootstrap", "chatwoot-postgres", "chatwoot-redis"},
    "chatwoot-gateway": {"chatwoot-web"},
    "rocket-mongo-init": {"rocket-mongo"},
    "rocketchat": {"rocket-mongo", "rocket-mongo-init"},
    "rocket-bootstrap": {"rocketchat"},
    "rocket-gateway": {"rocket-bootstrap"},
    "chattermate-init": {"chattermate-postgres", "chattermate-redis"},
    "chattermate-api": {"chattermate-init", "chattermate-postgres", "chattermate-redis"},
    "chattermate-worker": {
        "chattermate-api",
        "chattermate-init",
        "chattermate-postgres",
        "chattermate-redis",
    },
    "chattermate-bootstrap": {"chattermate-api"},
    "chattermate-gateway": {
        "chattermate-api",
        "chattermate-bootstrap",
        "chattermate-frontend",
    },
    "bottlecrm-init": {"bottlecrm-postgres", "bottlecrm-redis"},
    "bottlecrm-backend": {"bottlecrm-init", "bottlecrm-postgres", "bottlecrm-redis"},
    "bottlecrm-worker": {"bottlecrm-init", "bottlecrm-postgres", "bottlecrm-redis"},
    "bottlecrm-beat": {"bottlecrm-init", "bottlecrm-postgres", "bottlecrm-redis"},
    "bottlecrm-gateway": {"bottlecrm-backend", "bottlecrm-frontend"},
}

EXPECTED_ENV_KEYS = {
    line.split("=", 1)[0]
    for line in EXAMPLE_ENV_FILE.read_text(encoding="utf-8").splitlines()
    if line and not line.startswith("#") and "=" in line
}
HEX_SECRET_LENGTHS = {
    "KLM_OSS_LAUNCH_SECRET": 64,
    "KLM_OSS_CHATWOOT_POSTGRES_PASSWORD": 64,
    "KLM_OSS_CHATWOOT_REDIS_PASSWORD": 64,
    "KLM_OSS_CHATWOOT_SECRET_KEY_BASE": 128,
    "KLM_OSS_CHATWOOT_ACTIVE_RECORD_ENCRYPTION_PRIMARY_KEY": 64,
    "KLM_OSS_CHATWOOT_ACTIVE_RECORD_ENCRYPTION_DETERMINISTIC_KEY": 64,
    "KLM_OSS_CHATWOOT_ACTIVE_RECORD_ENCRYPTION_KEY_DERIVATION_SALT": 64,
    "KLM_OSS_CHATWOOT_API_ACCESS_TOKEN": 64,
    "KLM_OSS_CHATWOOT_WEBHOOK_SECRET": 64,
    "KLM_OSS_ROCKETCHAT_WEBHOOK_TOKEN": 64,
    "KLM_OSS_CHATTERMATE_POSTGRES_PASSWORD": 64,
    "KLM_OSS_CHATTERMATE_REDIS_PASSWORD": 64,
    "KLM_OSS_CHATTERMATE_CONVERSATION_SECRET": 96,
    "KLM_OSS_CHATTERMATE_JWT_SECRET": 96,
    "KLM_OSS_BOTTLECRM_POSTGRES_PASSWORD": 64,
    "KLM_OSS_BOTTLECRM_POSTGRES_SUPERUSER_PASSWORD": 64,
    "KLM_OSS_BOTTLECRM_REDIS_PASSWORD": 64,
    "KLM_OSS_BOTTLECRM_SECRET_KEY": 128,
    "KLM_OSS_BOTTLECRM_BRIDGE_SECRET": 96,
}
PASSWORD_KEYS = {
    "KLM_OSS_CHATWOOT_ADMIN_PASSWORD",
    "KLM_OSS_ROCKETCHAT_ADMIN_PASSWORD",
    "KLM_OSS_CHATTERMATE_ADMIN_PASSWORD",
    "KLM_OSS_BOTTLECRM_ADMIN_PASSWORD",
}
FERNET_KEY = "KLM_OSS_CHATTERMATE_ENCRYPTION_KEY"
SECRET_KEYS = set(HEX_SECRET_LENGTHS) | PASSWORD_KEYS | {FERNET_KEY}

UPSTREAM_PINS = {
    "chatwoot": ("v4.15.1", "97bb8ecd326299c46eacea74863976427c96fe94"),
    "rocketchat": ("8.6.1", "bfd782302d20cbd672c785ac8c0d072d7cc6b0db"),
    "chattermate": ("v1.0.14", "9770df5b4d4909084a3b3a00ff2404d25724de90"),
    "bottlecrm": ("0.46", "3ab79d75ece005a414d9d3ef729b7240545d1b41"),
}


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def run(command: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VerificationError(f"command failed: {' '.join(command)}: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise VerificationError(f"command failed: {' '.join(command)}\n{detail}")
    return result


def parse_dotenv(path: Path) -> dict[str, str]:
    require(path.is_file(), f"environment file is missing: {path}")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        require("=" in line, f"invalid dotenv line {line_number} in {path}")
        key, value = line.split("=", 1)
        require(re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is not None, f"invalid key: {key}")
        require(key not in values, f"duplicate dotenv key: {key}")
        values[key] = value.strip().strip("\"'")
    return values


def verify_env_file(path: Path) -> dict[str, str]:
    require(not path.is_symlink(), f"environment file must not be a symlink: {path}")
    values = parse_dotenv(path)
    require(set(values) == EXPECTED_ENV_KEYS, f"unexpected key set in {path}")
    ports: list[int] = []
    for _, port_key, _ in GATEWAYS.values():
        require(values[port_key].isdigit(), f"{port_key} must be numeric")
        port = int(values[port_key])
        require(1024 <= port <= 65535, f"{port_key} must be between 1024 and 65535")
        ports.append(port)
    require(len(set(ports)) == len(ports), "OSS gateway ports must be unique")
    require(values["KLM_OSS_START_TIMEOUT"].isdigit(), "start timeout must be numeric")
    require(30 <= int(values["KLM_OSS_START_TIMEOUT"]) <= 3600, "invalid start timeout")
    require(bool(values["KLM_OSS_TIMEZONE"]), "timezone must not be empty")

    is_example = path.resolve() == EXAMPLE_ENV_FILE.resolve()
    if is_example:
        for key in SECRET_KEYS:
            require(values[key] == "not-a-secret-run-start-script", f"{key} must be a sentinel")
    else:
        for key, length in HEX_SECRET_LENGTHS.items():
            require(
                len(values[key]) == length and re.fullmatch(r"[0-9a-f]+", values[key]) is not None,
                f"{key} must be generated lowercase hex ({length} chars)",
            )
        for key in PASSWORD_KEYS:
            require(len(values[key]) >= 24, f"{key} is too short")
        require(
            re.fullmatch(r"[A-Za-z0-9_-]{43}=", values[FERNET_KEY]) is not None,
            f"{FERNET_KEY} is not a valid generated Fernet key",
        )
        mode = stat.S_IMODE(path.stat().st_mode)
        require(mode & 0o077 == 0, f"{path} grants group/world access")
        run(["git", "-C", str(ROOT), "check-ignore", "--quiet", "--", ".local/oss.env"])
    return values


def verify_required_files() -> None:
    files = [
        COMPOSE_FILE,
        EXAMPLE_ENV_FILE,
        LOCK_FILE,
        ROOT / "scripts" / "start_oss_sidecars.sh",
        ROOT / "scripts" / "stop_oss_sidecars.sh",
        Path(__file__).resolve(),
        ROOT / "docs" / "OSS_LOCAL_RUNTIME.md",
        ROOT / "docker" / "oss" / "chatwoot-nginx.conf",
        ROOT / "docker" / "oss" / "rocket-nginx.conf",
        ROOT / "docker" / "oss" / "chattermate" / "Dockerfile.backend",
        ROOT / "docker" / "oss" / "chattermate" / "gateway" / "nginx.conf",
        ROOT / "docker" / "oss" / "bottlecrm" / "Dockerfile.backend",
    ]
    for path in files:
        require(path.is_file(), f"required file is missing: {path}")
    for path in files[3:6]:
        require(os.access(path, os.X_OK), f"script is not executable: {path}")


def verify_upstreams() -> None:
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    require(lock.get("schemaVersion") == 2, "unsupported upstream lock schema")
    sources = {str(item["name"]): item for item in lock.get("sources", [])}
    for name, (expected_ref, expected_commit) in UPSTREAM_PINS.items():
        require(name in sources, f"{name} is missing from upstream lock")
        source = sources[name]
        require(source.get("ref") == expected_ref, f"{name} ref differs from pin")
        require(source.get("commit") == expected_commit, f"{name} commit differs from pin")
        checkout = ROOT / ".vendor" / "upstreams" / name
        require((checkout / ".git").exists(), f"{name} source is not materialized")
        head = run(["git", "-C", str(checkout), "rev-parse", "HEAD"]).stdout.strip()
        tag = run(
            ["git", "-C", str(checkout), "rev-parse", f"refs/tags/{expected_ref}^{{commit}}"]
        ).stdout.strip()
        require(head == expected_commit and tag == expected_commit, f"{name} Git pin mismatch")
        for sentinel in source.get("sentinelFiles", []):
            require((checkout / sentinel).is_file(), f"{name} sentinel is missing: {sentinel}")
        for excluded in source.get("checkout", {}).get("excludePaths", []):
            require(
                not (checkout / excluded).exists(),
                f"proprietary path materialized: {name}/{excluded}",
            )
        for evidence in source.get("license", {}).get("evidence", []):
            path = evidence.get("path")
            blob = evidence.get("gitBlob")
            if path and blob:
                actual = run(
                    ["git", "-C", str(checkout), "rev-parse", f"HEAD:{path}"]
                ).stdout.strip()
                require(actual == blob, f"{name} license evidence differs: {path}")


def compose_base_command(env_file: Path) -> list[str]:
    command = [
        "docker",
        "compose",
        "--project-name",
        PROJECT_NAME,
        "--env-file",
        str(env_file),
        "--file",
        str(COMPOSE_FILE),
    ]
    for profile in PROFILE_SERVICES:
        command.extend(["--profile", profile])
    return command


def render_compose(env_file: Path) -> dict[str, Any]:
    require(shutil.which("docker") is not None, "docker command is required")
    run(["docker", "compose", "version"])
    result = run(compose_base_command(env_file) + ["config", "--format", "json"])
    payload = json.loads(result.stdout)
    require(isinstance(payload, dict), "docker compose returned an unexpected document")
    return payload


def environment_map(service: dict[str, Any]) -> dict[str, str]:
    environment = service.get("environment", {})
    if isinstance(environment, dict):
        return {str(key): str(value) for key, value in environment.items()}
    return dict(str(item).partition("=")[::2] for item in environment)


def network_names(service: dict[str, Any]) -> set[str]:
    networks = service.get("networks", {})
    return set(networks) if isinstance(networks, dict | list) else set()


def verify_raw_compose() -> None:
    source = COMPOSE_FILE.read_text(encoding="utf-8")
    chattermate_gateway = (
        ROOT / "docker" / "oss" / "chattermate" / "gateway" / "nginx.conf"
    ).read_text(encoding="utf-8")
    require(":latest" not in source, "floating latest image tag is forbidden")
    require("/var/run/docker.sock" not in source, "Docker socket mounts are forbidden")
    require("network_mode: host" not in source, "host networking is forbidden")
    for external_origin in (
        "firebaseio.com",
        "googleapis.com",
        "googletagmanager.com",
        "jsdelivr.net",
        "shopify.com",
    ):
        require(
            external_origin not in chattermate_gateway,
            f"ChatterMate local CSP allows external origin: {external_origin}",
        )
    for image in set(SERVICE_IMAGES.values()):
        require(f"image: {image}" in source, f"pinned image is missing: {image}")
    for key in SECRET_KEYS - {"KLM_OSS_LAUNCH_SECRET"}:
        require(f"${{{key}:?" in source, f"{key} must fail closed in Compose")


def verify_rendered_compose(rendered: dict[str, Any], env: dict[str, str]) -> None:
    require(rendered.get("name") == PROJECT_NAME, "Compose project name differs")
    services = rendered.get("services", {})
    require(
        set(services) == ALL_SERVICES, "Compose service set differs from the four-profile contract"
    )
    actual_builds = {name for name, service in services.items() if service.get("build")}
    require(actual_builds == CUSTOM_BUILD_SERVICES, "custom overlay build set differs")

    for name, service in services.items():
        profile = SERVICE_PROFILE[name]
        require(service.get("profiles") == [profile], f"{name} profile differs")
        require(service.get("image") == SERVICE_IMAGES[name], f"{name} image differs from pin")
        require(not service.get("privileged", False), f"{name} must not be privileged")
        require(service.get("network_mode") != "host", f"{name} must not use host networking")
        expected_restart = "no" if name in ONE_SHOT_SERVICES else "unless-stopped"
        require(service.get("restart") == expected_restart, f"{name} restart policy differs")
        if name in LONG_RUNNING_SERVICES:
            require(
                bool(service.get("healthcheck", {}).get("test")), f"{name} healthcheck is missing"
            )
        expected_networks = (
            {"klm-oss-local", "klm-oss-loopback"}
            if name in {item[0] for item in GATEWAYS.values()}
            else {"klm-oss-local"}
        )
        require(network_names(service) == expected_networks, f"{name} network set differs")

        dependencies = service.get("depends_on", {})
        require(
            set(dependencies) == EXPECTED_DEPENDENCIES.get(name, set()),
            f"{name} dependencies differ",
        )
        for dependency, config in dependencies.items():
            require(SERVICE_PROFILE[dependency] == profile, f"{name} crosses profile boundaries")
            expected_condition = (
                "service_started"
                if name == "rocket-mongo-init"
                else "service_completed_successfully"
                if dependency in ONE_SHOT_SERVICES
                else "service_healthy"
            )
            require(
                config.get("condition") == expected_condition,
                f"{name}->{dependency} condition differs",
            )

        for mount in service.get("volumes", []):
            mount_type = mount.get("type")
            require(mount_type in {"volume", "bind"}, f"{name} has unsupported mount type")
            if mount_type == "bind":
                source = Path(str(mount.get("source", ""))).resolve()
                require(
                    source.is_relative_to(ROOT / "docker" / "oss"),
                    f"{name} bind escapes docker/oss",
                )
                require(mount.get("read_only") is True, f"{name} bind mount must be read-only")

    expected_gateways = {item[0] for item in GATEWAYS.values()}
    published = {name for name, service in services.items() if service.get("ports")}
    require(published == expected_gateways, "only loopback gateways may publish ports")
    for _, (name, port_key, _) in GATEWAYS.items():
        ports = services[name]["ports"]
        require(len(ports) == 1, f"{name} must publish one port")
        port = ports[0]
        require(port.get("host_ip") == "127.0.0.1", f"{name} must bind loopback")
        require(int(port.get("target")) == 8080, f"{name} target port differs")
        require(str(port.get("published")) == env[port_key], f"{name} published port differs")
        require(services[name].get("read_only") is True, f"{name} gateway must be read-only")
        require("ALL" in services[name].get("cap_drop", []), f"{name} must drop all capabilities")

    require(set(rendered.get("volumes", {})) == EXPECTED_VOLUMES, "named volume set differs")
    local_network = rendered.get("networks", {}).get("klm-oss-local", {})
    loopback_network = rendered.get("networks", {}).get("klm-oss-loopback", {})
    require(local_network.get("internal") is True, "internal sidecar network must block egress")
    require(loopback_network.get("internal") is not True, "gateway network must publish to host")
    require(local_network.get("attachable") is not True, "internal network must not be attachable")

    chatwoot = environment_map(services["chatwoot-web"])
    require(chatwoot.get("DISABLE_TELEMETRY") == "true", "Chatwoot telemetry must be disabled")
    require(chatwoot.get("ENABLE_ACCOUNT_SIGNUP") == "false", "Chatwoot signup must be disabled")
    require(
        chatwoot.get("SAFE_FETCH_ALLOW_PRIVATE_NETWORK") == "false", "Chatwoot SSRF guard differs"
    )
    rocket = environment_map(services["rocketchat"])
    require(
        services["rocketchat"].get("platform") == "linux/amd64",
        "Rocket compatibility platform differs",
    )
    rocket_labels = services["rocketchat"].get("labels", {})
    require(
        rocket_labels.get("com.klm.runtime.policy") == "local-compatibility",
        "Rocket runtime policy differs",
    )
    require(
        rocket_labels.get("com.klm.runtime.version") == "6.13.1",
        "Rocket runtime label differs",
    )
    require(
        rocket_labels.get("com.klm.reference.version") == "8.6.1",
        "Rocket reference label differs",
    )
    require(
        rocket.get("OVERWRITE_SETTING_Register_Server") == "false",
        "Rocket registration must be off",
    )
    require(
        rocket.get("OVERWRITE_SETTING_Statistics_reporting") == "false",
        "Rocket telemetry must be off",
    )
    require("replicaSet=rs0" in rocket.get("MONGO_URL", ""), "Rocket replica set is missing")
    chattermate = environment_map(services["chattermate-api"])
    require(
        chattermate.get("HF_HUB_DISABLE_TELEMETRY") == "1", "ChatterMate telemetry guard differs"
    )
    require(
        chattermate.get("S3_FILE_STORAGE") == "false", "ChatterMate external storage must be off"
    )
    bottle = environment_map(services["bottlecrm-backend"])
    require(bottle.get("KLM_BOTTLECRM_LOCAL_MODE") == "true", "BottleCRM local mode is required")
    require(
        "console.EmailBackend" in bottle.get("EMAIL_BACKEND", ""), "BottleCRM email must stay local"
    )

    external_credentials = {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "FIREBASE_CREDENTIALS",
        "GOOGLE_CLIENT_SECRET",
        "JIRA_CLIENT_SECRET",
        "META_APP_SECRET",
        "META_WEBHOOK_VERIFY_TOKEN",
        "OPENAI_API_KEY",
        "SHOPIFY_API_SECRET",
        "SLACK_CLIENT_SECRET",
        "STRIPE_SECRET_KEY",
    }
    for name, service in services.items():
        service_env = environment_map(service)
        populated = {key for key in external_credentials if service_env.get(key)}
        require(not populated, f"{name} has external credentials enabled: {sorted(populated)}")


def parse_compose_ps(output: str) -> dict[str, dict[str, Any]]:
    payload = output.strip()
    if not payload:
        return {}
    try:
        decoded = json.loads(payload)
        records = decoded if isinstance(decoded, list) else [decoded]
    except json.JSONDecodeError:
        records = [json.loads(line) for line in payload.splitlines() if line.strip()]
    return {str(record.get("Service")): record for record in records if record.get("Service")}


def compose_ps(env_file: Path) -> dict[str, dict[str, Any]]:
    result = run(compose_base_command(env_file) + ["ps", "--all", "--format", "json"])
    return parse_compose_ps(result.stdout)


def selected_services(profile: str) -> set[str]:
    return ALL_SERVICES if profile == "all" else PROFILE_SERVICES[profile]


def selected_profiles(profile: str) -> tuple[str, ...]:
    return tuple(PROFILE_SERVICES) if profile == "all" else (profile,)


def verify_ports_available(env_file: Path, env: dict[str, str], profile: str) -> None:
    running = compose_ps(env_file)
    for selected in selected_profiles(profile):
        service_name, port_key, _ = GATEWAYS[selected]
        port = int(env[port_key])
        record = running.get(service_name, {})
        publishers = record.get("Publishers", [])
        owns_port = any(
            str(item.get("URL", "")) == "127.0.0.1"
            and str(item.get("PublishedPort", "")) == str(port)
            for item in publishers
            if isinstance(item, dict)
        )
        if str(record.get("State", "")).lower() == "running" and owns_port:
            continue
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as error:
            raise VerificationError(
                f"127.0.0.1:{port} is unavailable for {service_name}: {error}"
            ) from error
        finally:
            probe.close()


def runtime_state_ready(records: dict[str, dict[str, Any]], profile: str) -> tuple[bool, str]:
    ready = True
    messages: list[str] = []
    for name in sorted(selected_services(profile)):
        record = records.get(name)
        if record is None:
            ready = False
            messages.append(f"{name}=missing")
            continue
        state = str(record.get("State", "")).lower()
        health = str(record.get("Health", "")).lower()
        if name in ONE_SHOT_SERVICES:
            exit_code = int(record.get("ExitCode", -1))
            if state == "exited" and exit_code == 0:
                messages.append(f"{name}=completed")
            elif state == "exited":
                raise VerificationError(f"{name} exited with code {exit_code}")
            else:
                ready = False
                messages.append(f"{name}={state or 'pending'}")
        elif state in {"dead", "exited"}:
            raise VerificationError(f"{name} entered terminal state {state}")
        elif state == "running" and health == "healthy":
            messages.append(f"{name}=healthy")
        else:
            ready = False
            messages.append(f"{name}={state or 'pending'}/{health or 'starting'}")
    return ready, ", ".join(messages)


def verify_http(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> Any:
    opener = build_opener(ProxyHandler({}))
    request = Request(
        url,
        data=body if body is not None else b"" if method == "POST" else None,
        headers=headers or {},
        method=method,
    )
    raw = b""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with opener.open(request, timeout=5) as response:
                require(200 <= response.status < 300, f"unexpected HTTP status from {url}")
                raw = response.read()
                last_error = None
                break
        except (HTTPError, URLError, TimeoutError, OSError, VerificationError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(0.5)
    if last_error is not None:
        raise VerificationError(f"HTTP health check failed for {url}: {last_error}") from last_error
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload


def verify_chattermate_gateway_policy(env: dict[str, str]) -> None:
    url = f"http://127.0.0.1:{env['KLM_OSS_CHATTERMATE_PORT']}/"
    opener = build_opener(ProxyHandler({}))
    with opener.open(Request(url, method="HEAD"), timeout=5) as response:
        require(200 <= response.status < 300, "ChatterMate gateway is unavailable")
        policy = response.headers.get("Content-Security-Policy", "")
    require("default-src 'self'" in policy, "ChatterMate local CSP is missing")
    for external_origin in (
        "firebaseio.com",
        "googleapis.com",
        "googletagmanager.com",
        "jsdelivr.net",
        "shopify.com",
        "https:",
    ):
        require(
            external_origin not in policy,
            f"ChatterMate live CSP allows external origin: {external_origin}",
        )


def verify_rocketchat_api(env: dict[str, str]) -> None:
    base_url = f"http://127.0.0.1:{env['KLM_OSS_ROCKETCHAT_PORT']}"
    login = verify_http(
        f"{base_url}/api/v1/login",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            {
                "user": env["KLM_OSS_ROCKETCHAT_ADMIN_USERNAME"],
                "password": env["KLM_OSS_ROCKETCHAT_ADMIN_PASSWORD"],
            }
        ).encode("utf-8"),
    )
    require(isinstance(login, dict), "Rocket.Chat login contract differs")
    session = login.get("data") or {}
    token = str(session.get("authToken") or "")
    user_id = str(session.get("userId") or "")
    require(bool(token and user_id), "Rocket.Chat login did not return a session")
    identity = verify_http(
        f"{base_url}/api/v1/me",
        headers={"X-Auth-Token": token, "X-User-Id": user_id},
    )
    require(isinstance(identity, dict), "Rocket.Chat identity contract differs")
    require(identity.get("_id") == user_id, "Rocket.Chat authenticated identity differs")
    require(bool(identity.get("username")), "Rocket.Chat identity has no username")


def verify_chattermate_api(env: dict[str, str]) -> None:
    base_url = f"http://127.0.0.1:{env['KLM_OSS_CHATTERMATE_PORT']}"
    encoded = urlencode(
        {
            "username": env["KLM_OSS_CHATTERMATE_ADMIN_EMAIL"],
            "password": env["KLM_OSS_CHATTERMATE_ADMIN_PASSWORD"],
        }
    ).encode("utf-8")
    login = verify_http(
        f"{base_url}/api/v1/users/login",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=encoded,
    )
    require(isinstance(login, dict), "ChatterMate login contract differs")
    token = str(login.get("access_token") or "")
    user = login.get("user") or {}
    require(bool(token), "ChatterMate login did not return an access token")
    require(
        isinstance(user, dict) and bool(user.get("organization_id")),
        "ChatterMate login did not return an organization identity",
    )
    agents = verify_http(
        f"{base_url}/api/v1/agent/list",
        headers={"Authorization": f"Bearer {token}"},
    )
    require(isinstance(agents, list), "ChatterMate protected agent contract differs")


def verify_chatwoot_api(env: dict[str, str]) -> None:
    """Exercise a database-backed API before reading Chatwoot's lazy health state."""
    base_url = f"http://127.0.0.1:{env['KLM_OSS_CHATWOOT_PORT']}"
    account_id = int(env["KLM_OSS_CHATWOOT_ACCOUNT_ID"])
    account = (
        verify_http(
            f"{base_url}/api/v1/accounts/{account_id}",
            headers={"api_access_token": env["KLM_OSS_CHATWOOT_API_ACCESS_TOKEN"]},
        )
        or {}
    )
    require(account.get("id") == account_id, "Chatwoot account contract differs")
    require(account.get("status") == "active", "Chatwoot account is not active")


def verify_bottlecrm_api(env: dict[str, str]) -> None:
    base_url = f"http://127.0.0.1:{env['KLM_OSS_BOTTLECRM_PORT']}"
    session = (
        verify_http(
            f"{base_url}/api/auth/klm-local-session/",
            method="POST",
            headers={"X-KLM-Local-Secret": env["KLM_OSS_BOTTLECRM_BRIDGE_SECRET"]},
        )
        or {}
    )
    access_token = str(session.get("access_token") or "")
    organization_id = str(session.get("org_id") or "")
    require(bool(access_token), "BottleCRM local session did not return an access token")
    require(bool(organization_id), "BottleCRM local session did not return an organization")

    accounts = (
        verify_http(
            f"{base_url}/api/accounts/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        or {}
    )
    active_accounts = accounts.get("active_accounts")
    require(isinstance(active_accounts, dict), "BottleCRM active_accounts contract differs")
    require(
        isinstance(active_accounts.get("open_accounts"), list),
        "BottleCRM active_accounts.open_accounts contract differs",
    )


def verify_runtime(env_file: Path, env: dict[str, str], profile: str, wait_seconds: int) -> None:
    deadline = time.monotonic() + wait_seconds
    last_status = "no containers reported"
    while time.monotonic() < deadline:
        ready, last_status = runtime_state_ready(compose_ps(env_file), profile)
        if ready:
            break
        time.sleep(3)
    else:
        raise VerificationError(
            f"runtime did not become healthy within {wait_seconds}s: {last_status}"
        )

    for selected in selected_profiles(profile):
        _, port_key, path = GATEWAYS[selected]
        verify_http(f"http://127.0.0.1:{env[port_key]}{path}")
    if profile in {"channels", "all"}:
        verify_chatwoot_api(env)
        payload = verify_http(f"http://127.0.0.1:{env['KLM_OSS_CHATWOOT_PORT']}/api") or {}
        require(payload.get("version") == "4.15.1", "Chatwoot runtime version differs")
        require(payload.get("queue_services") == "ok", "Chatwoot queue is unavailable")
        require(payload.get("data_services") == "ok", "Chatwoot data service is unavailable")
    if profile in {"collaboration", "all"}:
        payload = verify_http(f"http://127.0.0.1:{env['KLM_OSS_ROCKETCHAT_PORT']}/api/info") or {}
        require(
            str(payload.get("version") or "").startswith("6.13"),
            "Rocket.Chat runtime version differs",
        )
        verify_rocketchat_api(env)
    if profile in {"automation", "all"}:
        verify_chattermate_gateway_policy(env)
        verify_chattermate_api(env)
    if profile in {"crm", "all"}:
        verify_bottlecrm_api(env)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--profile",
        choices=("channels", "collaboration", "automation", "crm", "all"),
        default="all",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--runtime", action="store_true")
    parser.add_argument("--wait-seconds", type=int)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    candidate = args.env_file or (LOCAL_ENV_FILE if LOCAL_ENV_FILE.is_file() else EXAMPLE_ENV_FILE)
    env_file = Path(os.path.abspath(candidate.expanduser()))
    try:
        verify_required_files()
        env = verify_env_file(env_file)
        verify_upstreams()
        verify_raw_compose()
        verify_rendered_compose(render_compose(env_file), env)
        print(
            f"OK static: four profiles, pins, licenses, secrets, networks and volumes ({env_file})"
        )
        if args.preflight:
            run(["docker", "info", "--format", "{{.ServerVersion}}"])
            verify_ports_available(env_file, env, args.profile)
            print(f"OK preflight: Docker daemon and {args.profile} ports are ready")
        if args.runtime:
            run(["docker", "info", "--format", "{{.ServerVersion}}"])
            wait_seconds = args.wait_seconds or int(env["KLM_OSS_START_TIMEOUT"])
            require(1 <= wait_seconds <= 3600, "invalid runtime wait")
            verify_runtime(env_file, env, args.profile, wait_seconds)
            print(f"OK runtime: {args.profile} services and HTTP endpoints are healthy")
    except (VerificationError, json.JSONDecodeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
