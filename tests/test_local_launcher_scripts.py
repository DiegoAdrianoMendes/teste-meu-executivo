from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = ROOT / "scripts" / "start_klm_local.sh"
STOP_SCRIPT = ROOT / "scripts" / "stop_klm_local.sh"
LEGACY_SEED_SCRIPT = ROOT / "scripts" / "seed_test_users.py"
APP_LEGACY_SEED_SCRIPT = ROOT / "app" / "scripts" / "seed_test_users.py"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
AWS_DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_aws_ecs_v1.sh"
ZSH = shutil.which("zsh")

pytestmark = pytest.mark.skipif(
    ZSH is None,
    reason="os launchers locais são específicos de macOS/zsh",
)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _fake_environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    bin_dir.mkdir()
    state_dir.mkdir()
    run_dir = tmp_path / "run"
    log_dir = tmp_path / "logs"
    run_dir.mkdir()
    log_dir.mkdir()
    command_log = tmp_path / "commands.log"

    _write_executable(
        bin_dir / "launchctl",
        """#!/usr/bin/env zsh
set -u
printf '%s\\n' "$*" >> "$KLM_TEST_COMMAND_LOG"
command="$1"
case "$command" in
  print)
    label="${2##*/}"
    [[ -f "$KLM_TEST_STATE_DIR/$label" ]]
    ;;
  remove)
    label="$2"
    if [[ "${KLM_TEST_FAIL_REMOVE_LABEL:-}" == "$label" ]]; then
      exit 71
    fi
    rm -f "$KLM_TEST_STATE_DIR/$label"
    ;;
  submit)
    shift
    label=""
    while (( $# > 0 )); do
      if [[ "$1" == "-l" ]]; then
        label="$2"
        break
      fi
      shift
    done
    if [[ -z "$label" || "${KLM_TEST_FAIL_SUBMIT_LABEL:-}" == "$label" ]]; then
      exit 72
    fi
    : > "$KLM_TEST_STATE_DIR/$label"
    ;;
  bootstrap)
    plist="$3"
    label="${plist:t:r}"
    if [[ -z "$label" || "${KLM_TEST_FAIL_SUBMIT_LABEL:-}" == "$label" ]]; then
      exit 72
    fi
    : > "$KLM_TEST_STATE_DIR/$label"
    ;;
  bootout)
    label="${2##*/}"
    if [[ "${KLM_TEST_FAIL_REMOVE_LABEL:-}" == "$label" ]]; then
      exit 71
    fi
    rm -f "$KLM_TEST_STATE_DIR/$label"
    ;;
  *)
    exit 73
    ;;
esac
""",
    )
    _write_executable(
        bin_dir / "screen",
        """#!/usr/bin/env zsh
set -u
printf '%s\n' "$*" >> "$KLM_TEST_COMMAND_LOG"
case "$1" in
  -ls)
    for state in "$KLM_TEST_STATE_DIR"/*(N); do
      printf '\t999.%s\t(Detached)\n' "${state:t}"
    done
    ;;
  -dmS)
    label="$2"
    if [[ "${KLM_TEST_FAIL_SUBMIT_LABEL:-}" == "$label" ]]; then
      exit 72
    fi
    : > "$KLM_TEST_STATE_DIR/$label"
    ;;
  -S)
    label="$2"
    if [[ "$3" != "-X" || "$4" != "quit" ]]; then
      exit 73
    fi
    if [[ "${KLM_TEST_FAIL_REMOVE_LABEL:-}" == "$label" ]]; then
      exit 71
    fi
    rm -f "$KLM_TEST_STATE_DIR/$label"
    ;;
  *)
    exit 74
    ;;
esac
""",
    )
    _write_executable(
        bin_dir / "curl",
        """#!/usr/bin/env zsh
[[ "${KLM_TEST_CURL_MODE:-success}" == "success" ]]
""",
    )
    _write_executable(
        bin_dir / "lsof",
        """#!/usr/bin/env zsh
[[ "${KLM_TEST_LSOF_MODE:-released}" == "busy" ]]
""",
    )
    _write_executable(bin_dir / "node", "#!/usr/bin/env zsh\nexit 0\n")
    _write_executable(bin_dir / "vite", "#!/usr/bin/env zsh\nexit 0\n")

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "KLM_TEST_COMMAND_LOG": str(command_log),
            "KLM_TEST_STATE_DIR": str(state_dir),
            "KLM_WAIT_ATTEMPTS": "2",
            "KLM_WAIT_SLEEP_SECONDS": "0",
            "KLM_READY_ATTEMPTS": "2",
            "KLM_READY_SLEEP_SECONDS": "0",
            "KLM_NODE_BIN": str(bin_dir / "node"),
            "KLM_VITE_BIN": str(bin_dir / "vite"),
            "KLM_PYTHON_BIN": sys.executable,
            "KLM_SCREEN_BIN": str(bin_dir / "screen"),
            "KLM_RUN_DIR": str(run_dir),
            "KLM_LOG_DIR": str(log_dir),
        }
    )
    return env, state_dir, command_log


def _run(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [ZSH or "zsh", str(script)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_start_rolls_back_backend_when_frontend_submit_fails(tmp_path: Path) -> None:
    env, state_dir, command_log = _fake_environment(tmp_path)
    env["KLM_TEST_FAIL_SUBMIT_LABEL"] = "klm-hub-frontend-4173"

    result = _run(START_SCRIPT, env)

    assert result.returncode != 0
    assert list(state_dir.iterdir()) == []
    assert "Inicialização incompleta revertida" in result.stderr
    commands = command_log.read_text(encoding="utf-8")
    assert "-dmS klm-hub-backend-8010" in commands
    assert "-S klm-hub-backend-8010 -X quit" in commands


def test_start_fails_before_submit_when_required_runtime_is_missing(tmp_path: Path) -> None:
    env, state_dir, command_log = _fake_environment(tmp_path)
    env["KLM_PYTHON_BIN"] = str(tmp_path / "missing-python")

    result = _run(START_SCRIPT, env)

    assert result.returncode != 0
    assert list(state_dir.iterdir()) == []
    assert "Executável local obrigatório ausente" in result.stderr
    assert not command_log.exists() or "-dmS" not in command_log.read_text(encoding="utf-8")


def test_start_rolls_back_both_jobs_when_readiness_fails(tmp_path: Path) -> None:
    env, state_dir, command_log = _fake_environment(tmp_path)
    env["KLM_TEST_CURL_MODE"] = "failure"

    result = _run(START_SCRIPT, env)

    assert result.returncode != 0
    assert list(state_dir.iterdir()) == []
    assert "não ficaram prontos após 2 verificações" in result.stderr
    commands = command_log.read_text(encoding="utf-8")
    assert "-S klm-hub-frontend-4173 -X quit" in commands
    assert "-S klm-hub-backend-8010 -X quit" in commands


def test_start_keeps_both_jobs_only_after_successful_readiness(tmp_path: Path) -> None:
    env, state_dir, _ = _fake_environment(tmp_path)

    result = _run(START_SCRIPT, env)

    assert result.returncode == 0, result.stderr
    assert {path.name for path in state_dir.iterdir()} == {
        "klm-hub-backend-8010",
        "klm-hub-frontend-4173",
    }
    assert "KLM Hub ativo" in result.stdout
    assert "Inicialização incompleta" not in result.stderr


def test_stop_waits_until_jobs_and_ports_are_released(tmp_path: Path) -> None:
    env, state_dir, _ = _fake_environment(tmp_path)
    (state_dir / "klm-hub-backend-8010").touch()
    (state_dir / "klm-hub-frontend-4173").touch()

    result = _run(STOP_SCRIPT, env)

    assert result.returncode == 0, result.stderr
    assert list(state_dir.iterdir()) == []
    assert result.stdout.strip() == "Serviços locais do KLM Hub encerrados."


def test_stop_reports_failure_instead_of_false_success(tmp_path: Path) -> None:
    env, state_dir, _ = _fake_environment(tmp_path)
    (state_dir / "klm-hub-frontend-4173").touch()
    env["KLM_TEST_FAIL_REMOVE_LABEL"] = "klm-hub-frontend-4173"

    result = _run(STOP_SCRIPT, env)

    assert result.returncode != 0
    assert (state_dir / "klm-hub-frontend-4173").exists()
    assert "não foram encerrados por completo" in result.stderr
    assert "Serviços locais do KLM Hub encerrados." not in result.stdout


def test_stop_fails_when_a_service_still_owns_a_port(tmp_path: Path) -> None:
    env, _, _ = _fake_environment(tmp_path)
    env["KLM_TEST_LSOF_MODE"] = "busy"

    result = _run(STOP_SCRIPT, env)

    assert result.returncode != 0
    assert "ainda ocupa a porta" in result.stderr
    assert "Serviços locais do KLM Hub encerrados." not in result.stdout


def test_legacy_seed_entrypoints_delegate_to_guarded_local_bootstrap() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (LEGACY_SEED_SCRIPT, APP_LEGACY_SEED_SCRIPT)
    )

    assert "KlmLocalSeedService" in combined
    assert "KLM_LOCAL_BOOTSTRAP_ENABLED" in combined
    for fixed_secret in ("demo2026!", "Admin@2025", "Advogada@2025", "Estagio@2025"):
        assert fixed_secret not in combined


def test_production_deploy_does_not_seed_test_users() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    deploy_script = AWS_DEPLOY_SCRIPT.read_text(encoding="utf-8")
    migration_script = (
        ROOT / "scripts" / "run_aws_ecs_migration_v1.sh"
    ).read_text(encoding="utf-8")

    assert "python -m app.scripts.seed_test_users" not in workflow
    assert "python -m app.scripts.seed_test_users" not in deploy_script
    assert "python -m app.scripts.seed_test_users" not in migration_script
    assert "AWS_ECS_MIGRATION_TASK_FAMILY" in migration_script
    assert "app.db.aws_database_roles" in migration_script
    assert "aws ecs update-service" in deploy_script
