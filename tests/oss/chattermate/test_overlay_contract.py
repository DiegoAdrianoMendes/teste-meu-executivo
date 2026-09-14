from __future__ import annotations

import ast
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
OVERLAY = ROOT / "docker" / "oss" / "chattermate"
UPSTREAM = ROOT / ".vendor" / "upstreams" / "chattermate"
PINNED_COMMIT = "9770df5b4d4909084a3b3a00ff2404d25724de90"


def test_overlay_targets_the_materialized_public_pin() -> None:
    head = subprocess.run(
        ["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tag = subprocess.run(
        ["git", "-C", str(UPSTREAM), "describe", "--tags", "--exact-match", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == PINNED_COMMIT
    assert tag == "v1.0.14"


def test_frontend_runtime_patch_applies_without_touching_upstream() -> None:
    result = subprocess.run(
        [
            "patch",
            "--dry-run",
            "--batch",
            "--forward",
            "--strip=1",
            f"--directory={UPSTREAM / 'frontend'}",
        ],
        input=(OVERLAY / "frontend" / "local-runtime.patch").read_text(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_backend_runtime_patch_applies_without_touching_upstream() -> None:
    result = subprocess.run(
        [
            "patch",
            "--dry-run",
            "--batch",
            "--forward",
            "--strip=1",
            f"--directory={UPSTREAM / 'backend' / 'app'}",
        ],
        input=(OVERLAY / "backend" / "local-runtime.patch").read_text(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "relative_path",
    [
        "backend/entrypoint.sh",
        "frontend/build.sh",
        "frontend/entrypoint.sh",
    ],
)
def test_shell_scripts_parse(relative_path: str) -> None:
    subprocess.run(["sh", "-n", str(OVERLAY / relative_path)], check=True)


def test_python_preflight_parses_without_creating_bytecode() -> None:
    source = (OVERLAY / "backend" / "runtime_preflight.py").read_text()
    ast.parse(source)


def test_preflight_accepts_password_only_redis_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    path = OVERLAY / "backend" / "runtime_preflight.py"
    spec = importlib.util.spec_from_file_location("chattermate_runtime_preflight", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setenv("REDIS_URL", "redis://:a-real-password@chattermate-redis:6379/0")
    assert module.validate_url(
        "REDIS_URL", {"redis", "rediss"}, require_password=True
    ).startswith("redis://:")


def test_build_contexts_exclude_both_private_gitlinks() -> None:
    backend_ignore = (OVERLAY / "Dockerfile.backend.dockerignore").read_text()
    frontend_ignore = (OVERLAY / "Dockerfile.frontend.dockerignore").read_text()
    assert ".vendor/upstreams/chattermate/backend/app/enterprise/**" in backend_ignore
    assert ".vendor/upstreams/chattermate/frontend/src/modules/enterprise/**" in frontend_ignore
    assert "rm -rf /app/app/enterprise" in (OVERLAY / "Dockerfile.backend").read_text()


def test_no_runtime_model_preload_command() -> None:
    entrypoint = (OVERLAY / "backend" / "entrypoint.sh").read_text()
    dockerfile = (OVERLAY / "Dockerfile.backend").read_text()
    assert "preload_models.py" not in entrypoint
    assert "RUN python" not in "\n".join(
        line for line in dockerfile.splitlines() if "preload" in line.lower()
    )
    assert "CRAWL4_AI_BASE_DIRECTORY=/app/config" in dockerfile


def test_gateway_preserves_api_path_and_normalizes_local_http_cookies() -> None:
    nginx = (OVERLAY / "gateway" / "nginx.conf").read_text()
    assert "location /api/" in nginx
    assert "proxy_pass http://chattermate_api_upstream;" in nginx
    assert "proxy_pass http://chattermate_api_upstream/;" not in nginx
    assert "proxy_cookie_flags ~ nosecure samesite=lax;" in nginx
    assert "location /socket.io/" in nginx
    assert "proxy_set_header Upgrade $http_upgrade;" in nginx
    assert "server_name localhost 127.0.0.1;" in nginx
    assert "listen 8080 default_server;" in nginx


def test_compose_contract_exposes_only_the_loopback_gateway() -> None:
    yaml = pytest.importorskip("yaml")
    contract = yaml.safe_load((OVERLAY / "compose.contract.yml").read_text())
    services = contract["services"]
    published = {name for name, service in services.items() if service.get("ports")}
    assert published == {"chattermate-gateway"}
    gateway_port = services["chattermate-gateway"]["ports"][0]
    assert gateway_port["host_ip"] == "127.0.0.1"
    assert services["chattermate-worker"]["command"] == ["worker"]
    assert services["chattermate-api"]["command"] == ["api"]
    assert services["chattermate-init"]["command"] == ["migrate"]
    worker_api_dependency = services["chattermate-worker"]["depends_on"][
        "chattermate-api"
    ]
    assert worker_api_dependency["condition"] == "service_healthy"
    for name in ("chattermate-init", "chattermate-api", "chattermate-worker"):
        assert "chattermate-config:/app/config" in services[name]["volumes"]
        assert any(item.startswith("/app/logs:") for item in services[name]["tmpfs"])


def test_frontend_service_receives_no_backend_secrets() -> None:
    yaml = pytest.importorskip("yaml")
    contract = yaml.safe_load((OVERLAY / "compose.contract.yml").read_text())
    frontend_env = contract["services"]["chattermate-frontend"]["environment"]
    forbidden = {
        "DATABASE_URL",
        "REDIS_URL",
        "JWT_SECRET_KEY",
        "CONVERSATION_SECRET_KEY",
        "ENCRYPTION_KEY",
    }
    assert forbidden.isdisjoint(frontend_env)


def test_public_env_example_is_deliberately_non_runnable() -> None:
    env_example = (OVERLAY / "env.example").read_text()
    assert env_example.count("not-a-secret-run-start-script") == 5
    assert "KLM_OSS_CHATTERMATE_ENCRYPTION_KEY=not-a-secret" in env_example
