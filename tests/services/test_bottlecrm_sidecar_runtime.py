"""Focused lifecycle contracts for the local BottleCRM sidecar profile."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
START_SCRIPT = ROOT / "scripts" / "start_oss_sidecars.sh"
STOP_SCRIPT = ROOT / "scripts" / "stop_oss_sidecars.sh"
VERIFY_SCRIPT = ROOT / "scripts" / "verify_oss_sidecars.py"

BOTTLECRM_SERVICES = {
    "bottlecrm-postgres",
    "bottlecrm-redis",
    "bottlecrm-init",
    "bottlecrm-backend",
    "bottlecrm-worker",
    "bottlecrm-beat",
    "bottlecrm-frontend",
    "bottlecrm-gateway",
}


def _load_verifier():
    spec = importlib.util.spec_from_file_location("klm_verify_oss_sidecars", VERIFY_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _fake_runtime(tmp_path: Path) -> tuple[Path, dict[str, str], Path, Path]:
    runtime = tmp_path / "runtime"
    scripts = runtime / "scripts"
    bin_dir = tmp_path / "bin"
    scripts.mkdir(parents=True)
    bin_dir.mkdir()
    shutil.copy2(START_SCRIPT, scripts / START_SCRIPT.name)
    shutil.copy2(STOP_SCRIPT, scripts / STOP_SCRIPT.name)
    (runtime / "docker-compose.oss.yml").write_text("services: {}\n", encoding="ascii")
    verify_log = tmp_path / "verify.log"
    (scripts / "verify_oss_sidecars.py").write_text(
        """import os
import sys

with open(os.environ["KLM_TEST_VERIFY_LOG"], "a", encoding="utf-8") as stream:
    stream.write(" ".join(sys.argv[1:]) + "\\n")
""",
        encoding="ascii",
    )

    command_log = tmp_path / "docker.log"
    _write_executable(
        bin_dir / "docker",
        """#!/bin/sh
printf '%s\n' "$*" >> "$KLM_TEST_DOCKER_LOG"
exit 0
""",
    )
    _write_executable(bin_dir / "git", "#!/bin/sh\nexit 0\n")

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "KLM_TEST_DOCKER_LOG": str(command_log),
            "KLM_TEST_VERIFY_LOG": str(verify_log),
        }
    )
    return runtime, env, command_log, verify_log


def _run(script: Path, runtime: Path, env: dict[str, str], *arguments: str):
    return subprocess.run(
        ["/bin/bash", str(script), *arguments],
        cwd=runtime,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_crm_profile_verifier_covers_complete_bottlecrm_runtime() -> None:
    verifier = _load_verifier()

    assert verifier.PROFILE_SERVICES["crm"] == BOTTLECRM_SERVICES
    assert verifier.selected_services("crm") == BOTTLECRM_SERVICES
    assert verifier.selected_profiles("crm") == ("crm",)
    assert verifier.GATEWAYS["crm"] == (
        "bottlecrm-gateway",
        "KLM_OSS_BOTTLECRM_PORT",
        "/healthz",
    )


def test_bottlecrm_runtime_verification_authenticates_and_reads_accounts(monkeypatch) -> None:
    verifier = _load_verifier()
    calls: list[tuple[str, str, dict[str, str]]] = []

    def fake_verify_http(url: str, *, method="GET", headers=None):
        calls.append((url, method, headers or {}))
        if url.endswith("/api/auth/klm-local-session/"):
            return {"access_token": "local-access-token", "org_id": "organization-1"}
        return {"active_accounts": {"open_accounts": []}}

    monkeypatch.setattr(verifier, "verify_http", fake_verify_http)

    verifier.verify_bottlecrm_api(
        {
            "KLM_OSS_BOTTLECRM_PORT": "3400",
            "KLM_OSS_BOTTLECRM_BRIDGE_SECRET": "local-bridge-secret",
        }
    )

    assert calls == [
        (
            "http://127.0.0.1:3400/api/auth/klm-local-session/",
            "POST",
            {"X-KLM-Local-Secret": "local-bridge-secret"},
        ),
        (
            "http://127.0.0.1:3400/api/accounts/",
            "GET",
            {"Authorization": "Bearer local-access-token"},
        ),
    ]


def test_bottlecrm_runtime_verification_rejects_accounts_contract_drift(monkeypatch) -> None:
    verifier = _load_verifier()

    def fake_verify_http(url: str, *, method="GET", headers=None):
        if url.endswith("/api/auth/klm-local-session/"):
            return {"access_token": "local-access-token", "org_id": "organization-1"}
        return {"active_accounts": {"results": []}}

    monkeypatch.setattr(verifier, "verify_http", fake_verify_http)

    with pytest.raises(verifier.VerificationError, match="open_accounts"):
        verifier.verify_bottlecrm_api(
            {
                "KLM_OSS_BOTTLECRM_PORT": "3400",
                "KLM_OSS_BOTTLECRM_BRIDGE_SECRET": "local-bridge-secret",
            }
        )


def test_start_and_stop_scripts_target_the_crm_profile(tmp_path: Path) -> None:
    runtime, env, command_log, verify_log = _fake_runtime(tmp_path)

    start = _run(runtime / "scripts" / START_SCRIPT.name, runtime, env, "crm")
    assert start.returncode == 0, start.stderr

    stop = _run(runtime / "scripts" / STOP_SCRIPT.name, runtime, env, "crm")
    assert stop.returncode == 0, stop.stderr

    commands = command_log.read_text(encoding="utf-8").splitlines()
    up = next(command for command in commands if " up --detach" in command)
    stopped = next(command for command in commands if " stop --timeout 60" in command)
    assert "--profile crm" in up
    assert "--profile channels" not in up
    assert "--profile collaboration" not in up
    assert "--profile automation" not in up
    assert "--profile crm" in stopped
    assert "--profile channels" not in stopped
    assert "--profile collaboration" not in stopped
    assert "--profile automation" not in stopped
    verifier_calls = verify_log.read_text(encoding="utf-8").splitlines()
    assert any("--profile crm --preflight" in call for call in verifier_calls)
    assert any("--profile crm --runtime" in call for call in verifier_calls)
