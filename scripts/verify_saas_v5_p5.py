#!/usr/bin/env python3
"""Orchestrate the complete local-only KLM SaaS V5 P5 verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_VERSION = "klm-saas-v5-p5-verifier-v4"
MANIFEST_PATH = ROOT / "app/data/saas_v5/p5-verification.json"
EVIDENCE_DIR = ROOT / ".local/qa-evidence/saas-v5-p5"
FUNCTION_UNIT_MANIFEST_RELATIVE = Path(
    ".local/qa-evidence/oss/oss_function_unit_verification.json"
)
OSS_NATIVE_MANIFEST_RELATIVE = Path(
    ".local/qa-evidence/oss-native/oss_native_verification.json"
)
NATIVE_ARCHITECTURE_SCORECARD_RELATIVE = Path(
    ".local/qa-evidence/oss-native/native_architecture_superiority.json"
)
MINIMUM_FRONTEND_TESTS = 321
MINIMUM_CONVERSATIONS = 1_000
FRONTEND_NODE_VERSION = "24.18.0"
MINIMUM_FRONTEND_NODE = (24, 18, 0)
REACT_ROUTER_RSC_ADVISORY = "GHSA-qwww-vcr4-c8h2"
REACT_ROUTER_RSC_ADVISORY_URL = (
    "https://github.com/advisories/GHSA-qwww-vcr4-c8h2"
)
REACT_ROUTER_RSC_UPSTREAM_URL = (
    "https://github.com/remix-run/react-router/security/advisories/"
    "GHSA-qwww-vcr4-c8h2"
)
REACT_ROUTER_RSC_BACKPORT_URL = (
    "https://github.com/remix-run/react-router/pull/15353"
)
REACT_ROUTER_RSC_RELEASE_URL = (
    "https://github.com/remix-run/react-router/releases/tag/react-router%407.18.2"
)
REACT_ROUTER_RSC_EXCEPTION_VERSION = "7.18.2"
REACT_ROUTER_RSC_EXCEPTION_REVIEW_BEFORE = date(2026, 8, 15)
REACT_ROUTER_RSC_FINDINGS = frozenset({"react-router", "react-router-dom"})
REACT_ROUTER_RSC_MODULE_MARKERS = (
    "react-router/internal/react-server-client",
    "react-server-dom",
)
REACT_ROUTER_RSC_API_PATTERN = re.compile(
    r"\b(?:unstable|UNSAFE)_[A-Za-z0-9_]*RSC[A-Za-z0-9_]*\b"
)
REACT_ROUTER_RSC_SCAN_EXTENSIONS = frozenset(
    {".cjs", ".cts", ".js", ".jsx", ".mjs", ".mts", ".ts", ".tsx"}
)
REACT_ROUTER_RSC_IGNORED_DIRECTORIES = frozenset(
    {
        "coverage",
        "dist",
        "dist-app",
        "dist-blackbox",
        "node_modules",
        "playwright-report",
        "test-results",
    }
)
REQUIRED_NATIVE_ARCHITECTURE_CRITERIA = (
    "provider_neutral_native_boundary",
    "strict_typed_contracts",
    "classified_tenant_isolation",
    "transactional_rls_role_hardening",
    "tenant_reference_integrity",
    "optimistic_and_locked_concurrency",
    "intent_idempotency",
    "exact_single_use_human_veto",
    "immutable_evidence",
    "durable_post_commit_effects",
    "explicit_external_dependency_gates",
    "source_bound_release_verification",
)

RunMode = Literal["final", "quick"]

REQUIRED_CATEGORIES = frozenset(
    {
        "backend-tests",
        "baseline-secrets",
        "conversation-simulation",
        "dependency-audit",
        "frontend-build",
        "frontend-playwright-desktop",
        "frontend-playwright-mobile",
        "frontend-playwright-tablet",
        "frontend-typecheck",
        "frontend-unit",
        "git-diff-check",
        "local-load-performance",
        "local-stack-live",
        "oss-census",
        "oss-manifests",
        "p4",
        "ruff",
        "sbom",
        "visual-evidence",
    }
)

_REQUIRED_TASK_CONTRACTS = {
    "baseline-before": ("baseline-secrets", "baseline"),
    "backend-pytest-full": ("backend-tests", "junit-backend"),
    "ruff-full": ("ruff", "ruff-ratchet"),
    "git-diff-check": ("git-diff-check", "exit-code"),
    "frontend-unit": ("frontend-unit", "vitest-json"),
    "frontend-typecheck": ("frontend-typecheck", "exit-code"),
    "frontend-build": ("frontend-build", "frontend-build"),
    "playwright-desktop-1440": ("frontend-playwright-desktop", "junit-playwright"),
    "playwright-tablet-834": ("frontend-playwright-tablet", "junit-playwright"),
    "playwright-mobile-393": ("frontend-playwright-mobile", "junit-playwright"),
    "visual-evidence": ("visual-evidence", "visual-evidence"),
    "local-stack-live": ("local-stack-live", "local-stack-live"),
    "local-hot-endpoints": ("local-load-performance", "local-load-performance"),
    "conversation-simulation": ("conversation-simulation", "conversation-simulation"),
    "sbom": ("sbom", "sbom"),
    "p4": ("p4", "p4"),
    "oss-census": ("oss-census", "oss-census"),
    "oss-dispositions": ("oss-manifests", "oss-dispositions"),
    "oss-function-unit-manifest": ("oss-manifests", "oss-function-unit"),
    "native-architecture-gate": ("oss-manifests", "native-architecture"),
    "oss-native-manifest": ("oss-manifests", "oss-native"),
    "npm-audit-offline": ("dependency-audit", "npm-audit"),
    "baseline-after": ("baseline-secrets", "baseline"),
}
_ALLOWED_TASK_ENVIRONMENT = frozenset(
    {
        "KLM_FRONTEND_URL",
        "PLAYWRIGHT_JUNIT_OUTPUT_NAME",
    }
)

_FORBIDDEN_EXECUTABLES = frozenset(
    {
        "aws",
        "az",
        "gcloud",
        "gh",
        "scp",
        "ssh",
        "terraform",
    }
)
_FORBIDDEN_COMMAND_FRAGMENTS = (
    "docker compose build",
    "docker compose create",
    "docker compose down",
    "docker compose pull",
    "docker compose restart",
    "docker compose start",
    "docker compose stop",
    "docker compose up",
    "docker-compose build",
    "docker-compose down",
    "docker-compose pull",
    "docker-compose start",
    "docker-compose stop",
    "docker-compose up",
)
_SENSITIVE_OPTION = re.compile(
    r"(?i)(?:api[-_]?key|authorization|credential|password|private[-_]?key|secret|token)"
)
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
)


class VerificationError(RuntimeError):
    """Raised when a P5 orchestration contract cannot be satisfied."""


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """One isolated, reviewable P5 subprocess contract."""

    name: str
    category: str
    command: tuple[str, ...]
    cwd: Path
    validator: str = "exit-code"
    artifact: Path | None = None
    artifact_is_generated: bool = False
    required: bool = True
    timeout_seconds: int = 1_800
    env: tuple[tuple[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RawExecution:
    exit_code: int
    duration_ms: int
    output: str
    error_type: str | None = None


class Executor(Protocol):
    def execute(self, task: TaskSpec, environment: Mapping[str, str]) -> RawExecution:
        """Run one task without interpreting its domain artifact."""


class SubprocessExecutor:
    """Default executor; it never invokes a shell."""

    def execute(self, task: TaskSpec, environment: Mapping[str, str]) -> RawExecution:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                task.command,
                cwd=task.cwd,
                env=dict(environment),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=task.timeout_seconds,
            )
            return RawExecution(
                exit_code=completed.returncode,
                duration_ms=round((time.monotonic() - started) * 1_000),
                output=completed.stdout,
            )
        except subprocess.TimeoutExpired as error:
            output = error.stdout or ""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            return RawExecution(
                exit_code=124,
                duration_ms=round((time.monotonic() - started) * 1_000),
                output=f"{output}\nP5_EXECUTION_TIMEOUT: {task.timeout_seconds}s\n",
                error_type=type(error).__name__,
            )
        except OSError as error:
            return RawExecution(
                exit_code=127,
                duration_ms=round((time.monotonic() - started) * 1_000),
                output=f"P5_EXECUTION_ERROR: {type(error).__name__}\n",
                error_type=type(error).__name__,
            )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix() or "."
    except ValueError:
        return str(path)


def _redact_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    redacted = re.sub(
        r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@",
        r"\1[REDACTED]@",
        redacted,
    )
    return redacted


def sanitize_command(command: Sequence[str]) -> list[str]:
    """Return a manifest-safe argv without changing the executed argv."""

    sanitized: list[str] = []
    redact_next = False
    for raw_part in command:
        part = str(raw_part)
        if redact_next:
            sanitized.append("[REDACTED]")
            redact_next = False
            continue
        if part.startswith("--") and "=" in part:
            option, value = part.split("=", 1)
            sanitized.append(
                f"{option}=[REDACTED]" if _SENSITIVE_OPTION.search(option) else _redact_text(part)
            )
            continue
        sanitized.append(_redact_text(part))
        if part.startswith("-") and _SENSITIVE_OPTION.search(part):
            redact_next = True
    return sanitized


def _parse_node_version(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def _bundled_node_candidate(root: Path) -> Path:
    system = platform.system().lower()
    machine = platform.machine().lower()
    architecture = "arm64" if machine in {"aarch64", "arm64"} else "x64"
    suffix = "darwin" if system == "darwin" else system
    executable = "node.exe" if system == "windows" else "node"
    return (
        root
        / ".local/toolchains"
        / f"node-v{FRONTEND_NODE_VERSION}-{suffix}-{architecture}"
        / "bin"
        / executable
    )


def _resolve_frontend_node(root: Path) -> dict[str, Any]:
    override = os.environ.get("KLM_NODE_BIN", "").strip()
    candidates: list[tuple[str, Path]] = []
    if override:
        candidates.append(("KLM_NODE_BIN", Path(override).expanduser()))
    candidates.append(("project-local", _bundled_node_candidate(root)))
    system_node = shutil.which("node")
    if system_node:
        candidates.append(("PATH", Path(system_node)))

    rejected: list[str] = []
    for source, candidate in candidates:
        if not candidate.is_file():
            rejected.append(f"{source}:missing")
            continue
        try:
            completed = subprocess.run(
                (str(candidate), "--version"),
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            rejected.append(f"{source}:unreadable")
            continue
        version = _parse_node_version(completed.stdout)
        if completed.returncode != 0 or version is None:
            rejected.append(f"{source}:invalid-version")
            continue
        if version[0] == 24 and version >= MINIMUM_FRONTEND_NODE:
            return {
                "path": candidate.resolve(),
                "version": ".".join(str(part) for part in version),
                "source": source,
                "supported": True,
            }
        rejected.append(f"{source}:unsupported-v{'.'.join(str(part) for part in version)}")

    details = ", ".join(rejected) if rejected else "no candidates"
    raise VerificationError(
        "P5 requires Node >=24.18.0 <25 for the frontend; "
        f"set KLM_NODE_BIN or provision the project-local toolchain ({details})."
    )


def _offline_environment(
    task: TaskSpec,
    *,
    node_runtime: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "AI_PROVIDER": "fake",
            "ALL_PROXY": "http://127.0.0.1:9",
            "ANTHROPIC_API_KEY": "",
            "AWS_ACCESS_KEY_ID": "",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_PROFILE": "",
            "AWS_SECRET_ACCESS_KEY": "",
            "AWS_SESSION_TOKEN": "",
            "GEMINI_API_KEY": "",
            "GIT_TERMINAL_PROMPT": "0",
            "HF_HUB_OFFLINE": "1",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "KLM_AWS_ENABLED": "false",
            "KLM_EXTERNAL_ACTIONS_ENABLED": "false",
            "KLM_MODEL_APIS_ENABLED": "false",
            "KLM_ONLINE_APPROVED": "false",
            "KLM_RUNTIME_MODE": "local_simulation",
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "NPM_CONFIG_OFFLINE": "true",
            "NPM_CONFIG_USERCONFIG": os.devnull,
            "OPENAI_API_KEY": "",
            "PIP_NO_INDEX": "1",
            "KLM_E2E_TRACE": "0",
            "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
            "RESEND_API_KEY": "",
            "TRANSFORMERS_OFFLINE": "1",
            "all_proxy": "http://127.0.0.1:9",
            "http_proxy": "http://127.0.0.1:9",
            "https_proxy": "http://127.0.0.1:9",
            "no_proxy": "127.0.0.1,localhost,::1",
            "npm_config_offline": "true",
            "npm_config_userconfig": os.devnull,
        }
    )
    if node_runtime is not None:
        node_path = Path(str(node_runtime["path"]))
        environment["PATH"] = os.pathsep.join(
            (str(node_path.parent), environment.get("PATH", ""))
        ).rstrip(os.pathsep)
    environment.update(dict(task.env))
    return environment


def _assert_local_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise VerificationError("The P5 frontend URL must be loopback-only.")


def _write_playwright_config(path: Path, *, root: Path, frontend_url: str) -> dict[str, Any]:
    _assert_local_url(frontend_url)
    frontend = root / "frontend"
    payload = f"""const baseURL = process.env.KLM_FRONTEND_URL ?? {json.dumps(frontend_url)};
const desktopOnly = /@desktop-only/;
const mobileOnly = /@mobile-only/;
const viewportSpecific = /@(?:desktop|mobile)-only/;

export default {{
  testDir: {json.dumps(str(frontend / "e2e"))},
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30000,
  expect: {{ timeout: 10000 }},
  use: {{
    baseURL,
    screenshot: 'only-on-failure',
    trace: 'off',
    video: 'off',
  }},
  projects: [
    {{
      name: 'desktop-1440',
      grepInvert: mobileOnly,
      use: {{ viewport: {{ width: 1440, height: 1000 }} }},
    }},
    {{
      name: 'tablet-834',
      grepInvert: viewportSpecific,
      use: {{ viewport: {{ width: 834, height: 1194 }}, hasTouch: true }},
    }},
    {{
      name: 'mobile-393',
      grepInvert: desktopOnly,
      use: {{
        viewport: {{ width: 393, height: 852 }},
        hasTouch: true,
        isMobile: true,
      }},
    }},
  ],
  webServer: {{
    command: 'npm run dev -- --host 127.0.0.1 --port 4173',
    cwd: {json.dumps(str(frontend))},
    url: `${{baseURL}}/app`,
    reuseExistingServer: true,
    timeout: 30000,
  }},
}};
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return {
        "path": _safe_relative(path, root),
        "sha256": _file_sha256(path),
        "projects": ["desktop-1440", "tablet-834", "mobile-393"],
        "baseUrl": frontend_url,
    }


def _evidence_path(evidence_dir: Path, name: str) -> Path:
    return evidence_dir / name


def build_plan(
    *,
    root: Path = ROOT,
    evidence_dir: Path = EVIDENCE_DIR,
    stack_env_file: Path | None = None,
    frontend_url: str = "http://127.0.0.1:4173",
) -> tuple[TaskSpec, ...]:
    """Build the full P5 plan without importing or reading sibling scripts."""

    _assert_local_url(frontend_url)
    python = root / ".venv/bin/python"
    playwright_config = _evidence_path(evidence_dir, "playwright.p5.config.mjs")
    relative_pw_config = os.path.relpath(playwright_config, root / "frontend")
    stack_command = [
        str(python),
        "scripts/verify_local_stack_live.py",
        "--output",
        str(_evidence_path(evidence_dir, "local-stack-live.json")),
    ]
    if stack_env_file is not None:
        stack_command.extend(("--env-file", str(stack_env_file)))

    def task(
        name: str,
        category: str,
        command: Sequence[str],
        *,
        cwd: Path = root,
        validator: str = "exit-code",
        artifact: Path | None = None,
        generated: bool = False,
        timeout: int = 1_800,
        env: Mapping[str, str] | None = None,
    ) -> TaskSpec:
        return TaskSpec(
            name=name,
            category=category,
            command=tuple(str(part) for part in command),
            cwd=cwd,
            validator=validator,
            artifact=artifact,
            artifact_is_generated=generated,
            timeout_seconds=timeout,
            env=tuple(sorted((env or {}).items())),
        )

    backend_junit = _evidence_path(evidence_dir, "backend-pytest.junit.xml")
    frontend_unit = _evidence_path(evidence_dir, "frontend-unit.json")
    baseline_before = _evidence_path(evidence_dir, "baseline-before.json")
    baseline_after = _evidence_path(evidence_dir, "baseline-after.json")
    p4_output = _evidence_path(evidence_dir, "p4-verification.json")
    simulation_output = _evidence_path(evidence_dir, "conversation-simulation.json")
    load_output = _evidence_path(evidence_dir, "hot-endpoints.json")
    sbom_output = _evidence_path(evidence_dir, "sbom.json")
    ruff_output = _evidence_path(evidence_dir, "ruff-ratchet.json")
    visual_output = _evidence_path(evidence_dir, "visual/visual-evidence.json")
    playwright_outputs = {
        project: _evidence_path(evidence_dir, f"playwright-{project}.junit.xml")
        for project in ("desktop-1440", "tablet-834", "mobile-393")
    }

    plan: list[TaskSpec] = [
        task(
            "baseline-before",
            "baseline-secrets",
            (
                python,
                "scripts/capture_klm_baseline.py",
                "--output",
                baseline_before,
                "--assert-ready",
            ),
            validator="baseline",
            artifact=baseline_before,
            generated=True,
        ),
        task(
            "backend-pytest-full",
            "backend-tests",
            (
                "bash",
                "scripts/verify_postgresql_qa.sh",
                "--all",
                f"--junitxml={backend_junit}",
            ),
            validator="junit-backend",
            artifact=backend_junit,
            generated=True,
            timeout=3_600,
        ),
        task(
            "ruff-full",
            "ruff",
            (
                python,
                "scripts/verify_ruff_debt.py",
                "--baseline",
                "app/data/saas_v5/ruff-debt-baseline.json",
                "--output",
                ruff_output,
            ),
            validator="ruff-ratchet",
            artifact=ruff_output,
            generated=True,
        ),
        task("git-diff-check", "git-diff-check", ("git", "diff", "--check")),
        task(
            "frontend-unit",
            "frontend-unit",
            (
                "node_modules/.bin/vitest",
                "run",
                "--reporter=json",
                f"--outputFile={frontend_unit}",
            ),
            cwd=root / "frontend",
            validator="vitest-json",
            artifact=frontend_unit,
            generated=True,
        ),
        task(
            "frontend-typecheck",
            "frontend-typecheck",
            ("npm", "run", "typecheck"),
            cwd=root / "frontend",
        ),
        task(
            "frontend-build",
            "frontend-build",
            ("npm", "run", "build"),
            cwd=root / "frontend",
            validator="frontend-build",
        ),
    ]
    for project, category in (
        ("desktop-1440", "frontend-playwright-desktop"),
        ("tablet-834", "frontend-playwright-tablet"),
        ("mobile-393", "frontend-playwright-mobile"),
    ):
        plan.append(
            task(
                f"playwright-{project}",
                category,
                (
                    "node_modules/.bin/playwright",
                    "test",
                    f"--config={relative_pw_config}",
                    f"--project={project}",
                    "--reporter=line,junit",
                ),
                cwd=root / "frontend",
                validator="junit-playwright",
                artifact=playwright_outputs[project],
                generated=True,
                timeout=3_600,
                env={
                    "KLM_FRONTEND_URL": frontend_url,
                    "PLAYWRIGHT_JUNIT_OUTPUT_NAME": str(playwright_outputs[project]),
                },
            )
        )
    plan.append(
        task(
            "visual-evidence",
            "visual-evidence",
            (
                "node",
                "scripts/capture-p5-visual-evidence.mjs",
                "--base-url",
                frontend_url,
                "--output-dir",
                _evidence_path(evidence_dir, "visual"),
                "--manifest",
                visual_output,
            ),
            cwd=root / "frontend",
            validator="visual-evidence",
            artifact=visual_output,
            generated=True,
            timeout=900,
        )
    )
    plan.extend(
        (
            task(
                "local-stack-live",
                "local-stack-live",
                stack_command,
                validator="local-stack-live",
                artifact=_evidence_path(evidence_dir, "local-stack-live.json"),
                generated=True,
            ),
            task(
                "local-hot-endpoints",
                "local-load-performance",
                (
                    python,
                    "scripts/benchmark_local_hot_endpoints.py",
                    "--output",
                    load_output,
                    "--threshold-p95-ms",
                    "300",
                    "--requests",
                    "80",
                    "--workers",
                    "8",
                ),
                validator="local-load-performance",
                artifact=load_output,
                generated=True,
            ),
            task(
                "conversation-simulation",
                "conversation-simulation",
                (
                    python,
                    "scripts/simulate_saas_v5_conversations.py",
                    "--output",
                    simulation_output,
                ),
                validator="conversation-simulation",
                artifact=simulation_output,
                generated=True,
            ),
            task(
                "sbom",
                "sbom",
                (
                    python,
                    "scripts/generate_saas_v5_sbom.py",
                    "--output",
                    sbom_output,
                ),
                validator="sbom",
                artifact=sbom_output,
                generated=True,
            ),
            task(
                "p4",
                "p4",
                (
                    python,
                    "scripts/verify_saas_v5_p4.py",
                    "--output",
                    p4_output,
                    "--fail-fast",
                ),
                validator="p4",
                artifact=p4_output,
                generated=True,
                timeout=3_600,
            ),
            task(
                "oss-census",
                "oss-census",
                (python, "scripts/census_oss_function_units.py", "--check"),
                validator="oss-census",
            ),
            task(
                "oss-dispositions",
                "oss-manifests",
                (python, "scripts/build_oss_function_unit_dispositions.py", "--check"),
                validator="oss-dispositions",
            ),
            task(
                "oss-function-unit-manifest",
                "oss-manifests",
                (python, "scripts/verify_oss_function_unit_release.py", "--check"),
                validator="oss-function-unit",
            ),
            task(
                "native-architecture-gate",
                "oss-manifests",
                (
                    python,
                    "scripts/verify_native_architecture_superiority.py",
                    "--check",
                ),
                validator="native-architecture",
            ),
            task(
                "oss-native-manifest",
                "oss-manifests",
                (python, "scripts/verify_oss_native_release.py", "--finalize-only"),
                validator="oss-native",
                timeout=3_600,
            ),
            task(
                "npm-audit-offline",
                "dependency-audit",
                ("npm", "audit", "--offline", "--audit-level=high", "--json"),
                cwd=root / "frontend",
                validator="npm-audit",
            ),
            task(
                "baseline-after",
                "baseline-secrets",
                (
                    python,
                    "scripts/capture_klm_baseline.py",
                    "--output",
                    baseline_after,
                    "--assert-ready",
                ),
                validator="baseline",
                artifact=baseline_after,
                generated=True,
            ),
        )
    )
    return tuple(plan)


def _task_command_contract_error(task: TaskSpec) -> str | None:
    command = task.command
    executable = Path(command[0]).name if command else ""
    exact_contracts: dict[str, tuple[str, ...]] = {
        "git-diff-check": ("git", "diff", "--check"),
        "frontend-typecheck": ("npm", "run", "typecheck"),
        "frontend-build": ("npm", "run", "build"),
        "npm-audit-offline": (
            "npm",
            "audit",
            "--offline",
            "--audit-level=high",
            "--json",
        ),
    }
    expected_exact = exact_contracts.get(task.name)
    normalized = (executable, *command[1:]) if command else ()
    if expected_exact is not None and normalized != expected_exact:
        return "exact_command_mismatch"

    required_fragments: dict[str, tuple[str, ...]] = {
        "baseline-before": ("scripts/capture_klm_baseline.py", "--assert-ready"),
        "baseline-after": ("scripts/capture_klm_baseline.py", "--assert-ready"),
        "backend-pytest-full": (
            "scripts/verify_postgresql_qa.sh",
            "--all",
            "--junitxml=",
        ),
        "ruff-full": (
            "scripts/verify_ruff_debt.py",
            "--baseline",
            "ruff-debt-baseline.json",
            "--output",
        ),
        "frontend-unit": ("vitest", "run", "--reporter=json", "--outputFile="),
        "playwright-desktop-1440": (
            "playwright",
            "test",
            "--project=desktop-1440",
            "--reporter=line,junit",
        ),
        "playwright-tablet-834": (
            "playwright",
            "test",
            "--project=tablet-834",
            "--reporter=line,junit",
        ),
        "playwright-mobile-393": (
            "playwright",
            "test",
            "--project=mobile-393",
            "--reporter=line,junit",
        ),
        "visual-evidence": (
            "capture-p5-visual-evidence.mjs",
            "--base-url",
            "--output-dir",
            "--manifest",
        ),
        "local-stack-live": ("scripts/verify_local_stack_live.py", "--output"),
        "local-hot-endpoints": (
            "scripts/benchmark_local_hot_endpoints.py",
            "--output",
            "--threshold-p95-ms",
            "300",
            "--requests",
            "80",
            "--workers",
            "8",
        ),
        "conversation-simulation": (
            "scripts/simulate_saas_v5_conversations.py",
            "--output",
        ),
        "sbom": ("scripts/generate_saas_v5_sbom.py", "--output"),
        "p4": ("scripts/verify_saas_v5_p4.py", "--output", "--fail-fast"),
        "oss-census": ("scripts/census_oss_function_units.py", "--check"),
        "oss-dispositions": (
            "scripts/build_oss_function_unit_dispositions.py",
            "--check",
        ),
        "oss-function-unit-manifest": (
            "scripts/verify_oss_function_unit_release.py",
            "--check",
        ),
        "native-architecture-gate": (
            "scripts/verify_native_architecture_superiority.py",
            "--check",
        ),
        "oss-native-manifest": (
            "scripts/verify_oss_native_release.py",
            "--finalize-only",
        ),
    }
    rendered = "\n".join(command)
    if any(fragment not in rendered for fragment in required_fragments.get(task.name, ())):
        return "required_command_fragment_missing"
    if task.name == "backend-pytest-full" and any(
        argument.endswith(".py") or "::" in argument for argument in command
    ):
        return "backend_suite_is_filtered"
    return None


def validate_plan_contract(plan: Sequence[TaskSpec]) -> dict[str, Any]:
    names = [task.name for task in plan]
    categories = {task.category for task in plan if task.required}
    missing = sorted(REQUIRED_CATEGORIES - categories)
    missing_tasks = sorted(set(_REQUIRED_TASK_CONTRACTS) - set(names))
    unexpected_tasks = sorted(set(names) - set(_REQUIRED_TASK_CONTRACTS))
    duplicate_names = sorted({name for name in names if names.count(name) > 1})
    unsafe: list[dict[str, str]] = []
    task_contract_mismatches: list[dict[str, str]] = []
    unsafe_environment_overrides: list[dict[str, str]] = []
    for task in plan:
        executable = Path(task.command[0]).name.lower() if task.command else ""
        rendered = " ".join(task.command).lower()
        reason: str | None = None
        if not task.command:
            reason = "empty_command"
        elif executable in _FORBIDDEN_EXECUTABLES:
            reason = f"forbidden_executable:{executable}"
        elif any(fragment in rendered for fragment in _FORBIDDEN_COMMAND_FRAGMENTS):
            reason = "stack_mutation_command"
        if reason:
            unsafe.append({"task": task.name, "reason": reason})
        expected = _REQUIRED_TASK_CONTRACTS.get(task.name)
        if expected is not None and (
            (task.category, task.validator) != expected or task.required is not True
        ):
            task_contract_mismatches.append(
                {"task": task.name, "reason": "category_validator_or_required_mismatch"}
            )
        command_error = _task_command_contract_error(task)
        if expected is not None and command_error is not None:
            task_contract_mismatches.append({"task": task.name, "reason": command_error})
        for key, _value in task.env:
            if key not in _ALLOWED_TASK_ENVIRONMENT:
                unsafe_environment_overrides.append({"task": task.name, "key": key})
    passed = not any(
        (
            missing,
            missing_tasks,
            unexpected_tasks,
            duplicate_names,
            unsafe,
            task_contract_mismatches,
            unsafe_environment_overrides,
        )
    )
    return {
        "requiredCategories": sorted(REQUIRED_CATEGORIES),
        "actualCategories": sorted(categories),
        "missingCategories": missing,
        "requiredTaskNames": sorted(_REQUIRED_TASK_CONTRACTS),
        "missingRequiredTaskNames": missing_tasks,
        "unexpectedTaskNames": unexpected_tasks,
        "duplicateTaskNames": duplicate_names,
        "unsafeCommands": unsafe,
        "taskContractMismatches": task_contract_mismatches,
        "unsafeEnvironmentOverrides": unsafe_environment_overrides,
        "passed": passed,
    }


def select_plan(
    plan: Sequence[TaskSpec], *, mode: RunMode, selectors: Sequence[str]
) -> tuple[TaskSpec, ...]:
    normalized = tuple(dict.fromkeys(item.strip() for item in selectors if item.strip()))
    if mode == "final" and normalized:
        raise VerificationError("Final mode cannot omit tasks or use --select.")
    if mode == "final":
        return tuple(plan)
    if not normalized:
        raise VerificationError("Quick mode requires at least one --select value.")
    known = {task.name for task in plan} | {task.category for task in plan}
    unknown = sorted(set(normalized) - known)
    if unknown:
        raise VerificationError(f"Unknown P5 selector(s): {', '.join(unknown)}")
    selected = [task for task in plan if task.name in normalized or task.category in normalized]
    safety_envelope = [task for task in plan if task.name in {"baseline-before", "baseline-after"}]
    by_name = {task.name: task for task in (*safety_envelope, *selected)}
    return tuple(task for task in plan if task.name in by_name)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise VerificationError(f"Invalid or missing JSON artifact: {path.name}") from error
    if not isinstance(payload, dict):
        raise VerificationError(f"JSON artifact must be an object: {path.name}")
    return payload


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def _parse_junit(path: Path) -> dict[str, Any]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise VerificationError(f"Invalid or missing JUnit artifact: {path.name}") from error
    suites = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
    if root.tag == "testsuites" and root.attrib.get("tests") is not None:
        values = root.attrib
    else:
        values = {
            key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")
        }
    summary = {key: int(values.get(key, 0)) for key in ("tests", "failures", "errors", "skipped")}
    _require(summary["tests"] > 0, "JUnit evidence contains no tests.")
    _require(summary["failures"] == 0, "JUnit evidence contains failures.")
    _require(summary["errors"] == 0, "JUnit evidence contains errors.")
    _require(summary["skipped"] == 0, "JUnit evidence contains skipped tests.")
    return summary


def _validate_baseline(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    secret_scan = payload.get("secretScan", {})
    upstreams = payload.get("upstreams", {})
    git = payload.get("git", {})
    _require(payload.get("kind") == "klm-saas-v5-baseline", "Unexpected baseline kind.")
    _require(bool(payload.get("sourceDigest")), "Baseline source digest is missing.")
    _require(int(payload.get("sourceFileCount", 0)) > 0, "Baseline source inventory is empty.")
    _require(secret_scan.get("findingCount") == 0, "Baseline secret scan found secrets.")
    _require(upstreams.get("allMatchLock") is True, "OSS upstreams do not match their lock.")
    _require(payload.get("snapshotReady") is True, "Baseline snapshot is not ready.")
    _require(bool(git.get("head")), "Baseline Git commit is missing.")
    return {
        "sourceDigest": payload["sourceDigest"],
        "sourceFileCount": payload["sourceFileCount"],
        "gitHead": git["head"],
        "gitBranch": git.get("branch"),
        "worktreeDirty": git.get("dirty"),
        "secretFindings": secret_scan["findingCount"],
        "upstreamsMatchLock": True,
        "snapshotReady": True,
    }


def _validate_vitest(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    total = int(payload.get("numTotalTests", 0))
    failed = int(payload.get("numFailedTests", 0))
    passed = int(payload.get("numPassedTests", 0))
    _require(
        total >= MINIMUM_FRONTEND_TESTS,
        f"Frontend unit suite has fewer than {MINIMUM_FRONTEND_TESTS} tests.",
    )
    _require(failed == 0, "Frontend unit suite contains failures.")
    _require(payload.get("success") is True, "Frontend unit report is not successful.")
    _require(
        passed + int(payload.get("numPendingTests", 0)) >= total,
        "Frontend unit counts are inconsistent.",
    )
    return {"tests": total, "passed": passed, "failed": failed, "minimum": MINIMUM_FRONTEND_TESTS}


def _validate_stack(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    environment = payload.get("environment", {})
    egress_probe = payload.get("results", {}).get("networks", {}).get("egressProbe", {})
    _require(
        payload.get("verifier") == "verify_local_stack_live", "Unexpected live stack verifier."
    )
    _require(payload.get("status") == "passed", "Live stack verification did not pass.")
    _require(
        environment.get("externalNetworkRequests") == 0,
        "Live stack verifier observed external network requests.",
    )
    _require(environment.get("stackMutations") == 0, "Live stack verifier mutated the stack.")
    _require(
        environment.get("secretValuesIncluded") is False,
        "Live stack evidence contains secret values.",
    )
    _require(
        egress_probe.get("status") == "passed"
        and egress_probe.get("defaultRoutePresent") is False
        and egress_probe.get("connected") is False
        and egress_probe.get("kernelRouteTableInspected") is True,
        "Live stack kernel egress denial was not proved.",
    )
    return {
        "digest": payload.get("digest"),
        "externalNetworkRequests": 0,
        "stackMutations": 0,
        "secretValuesIncluded": False,
        "kernelEgressDenied": True,
    }


def _validate_local_load(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    aggregate = payload.get("aggregate", {})
    network = payload.get("network", {})
    profile = payload.get("profile", {})
    claims = payload.get("claims", {})
    endpoints = payload.get("endpoints", [])
    threshold = float(aggregate.get("thresholdP95Ms", 0))
    p95_ms = float(aggregate.get("p95Ms", threshold or 0))
    _require(
        payload.get("kind") == "klm-saas-v5-local-hot-endpoint-benchmark",
        "Unexpected local load benchmark kind.",
    )
    _require(payload.get("status") == "passed", "Local load benchmark did not pass.")
    _require(payload.get("localOnly") is True, "Local load benchmark is not local-only.")
    _require(threshold == 300.0, "Local load benchmark threshold differs from 300 ms.")
    _require(p95_ms < threshold, "Aggregate local endpoint p95 exceeded 300 ms.")
    _require(aggregate.get("passed") is True, "Aggregate local load gate is not passed.")
    _require(
        int(aggregate.get("samples", 0)) >= 140,
        "Local load benchmark has too few measured requests.",
    )
    _require(isinstance(endpoints, list) and endpoints, "Local load endpoint inventory is empty.")
    for endpoint in endpoints:
        _require(endpoint.get("passed") is True, "At least one hot endpoint exceeded its gate.")
        _require(
            float(endpoint.get("p95Ms", threshold)) < threshold,
            "At least one hot endpoint p95 exceeded 300 ms.",
        )
    _require(
        profile.get("readOnlyMeasuredRequests") is True,
        "Local load benchmark did not use read-only measured requests.",
    )
    _require(
        network.get("externalNetworkRequests") == 0,
        "Local load benchmark attempted an external request.",
    )
    _require(
        claims.get("localLoadProfileVerified") is True,
        "Local load profile claim is not verified.",
    )
    _require(
        claims.get("externalProviderVerified") is False,
        "Local load benchmark made an external-provider claim.",
    )
    _require(
        claims.get("productionVerified") is False,
        "Local load benchmark made a production claim.",
    )
    return {
        "p95Ms": p95_ms,
        "thresholdP95Ms": threshold,
        "samples": aggregate["samples"],
        "endpointCount": len(endpoints),
        "externalNetworkRequests": 0,
    }


def _validate_simulation(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    effects = payload.get("externalEffects", {})
    data = payload.get("data", {})
    count = int(payload.get("conversationCount", 0))
    _require(
        payload.get("kind") == "klm-saas-v5-p5-conversation-simulation",
        "Unexpected simulation kind.",
    )
    _require(payload.get("status") == "passed", "Conversation simulation did not pass.")
    _require(
        count >= MINIMUM_CONVERSATIONS,
        f"Conversation simulation has fewer than {MINIMUM_CONVERSATIONS} journeys.",
    )
    for key in (
        "networkAttempts",
        "networkCallsExecuted",
        "providerCallsExecuted",
        "messagesSent",
        "chargesExecuted",
    ):
        _require(
            effects.get(key) == 0, f"Conversation simulation external effect is non-zero: {key}."
        )
    _require(
        effects.get("offlineNetworkGuard") is True,
        "Conversation simulation network guard was not active.",
    )
    _require(
        effects.get("modelApiGate") == "blocked", "Conversation simulation model API gate was open."
    )
    _require(
        data.get("classification") == "synthetic_only",
        "Conversation simulation used non-synthetic data.",
    )
    _require(data.get("realDataRecordsRead") == 0, "Conversation simulation read real records.")
    _require(data.get("realDataRecordsWritten") == 0, "Conversation simulation wrote real records.")
    return {
        "conversationCount": count,
        "minimum": MINIMUM_CONVERSATIONS,
        "digest": payload.get("digest"),
        "externalEffects": 0,
        "syntheticOnly": True,
    }


def _validate_sbom(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if payload.get("bomFormat") == "CycloneDX":
        components = payload.get("components", [])
        schema = "cyclonedx"
    elif payload.get("spdxVersion"):
        components = payload.get("packages", [])
        schema = "spdx"
    else:
        _require(payload.get("status") == "passed", "Custom SBOM report did not pass.")
        components = payload.get("components", [])
        schema = str(payload.get("format", "klm-sbom"))
        effects = payload.get("externalEffects", {})
        if effects:
            _require(
                effects.get("networkCallsExecuted") == 0, "SBOM generation used network calls."
            )
            _require(effects.get("awsActionsExecuted") == 0, "SBOM generation used AWS actions.")
    _require(isinstance(components, list) and components, "SBOM contains no components.")
    return {"format": schema, "componentCount": len(components), "sha256": _file_sha256(path)}


def _validate_ruff_ratchet(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    _require(payload.get("kind") == "klm-ruff-debt-ratchet", "Unexpected Ruff report kind.")
    _require(payload.get("status") == "passed", "Ruff debt ratchet did not pass.")
    baseline = int(payload.get("baselineFindingCount", -1))
    current = int(payload.get("currentFindingCount", -1))
    _require(baseline > 0, "Ruff debt baseline is empty.")
    _require(current >= 0, "Ruff current finding count is invalid.")
    _require(payload.get("newViolationCount") == 0, "Ruff ratchet found new violations.")
    _require(current <= baseline, "Ruff debt increased beyond the reviewed baseline.")
    _require(bool(payload.get("baselineDigest")), "Ruff baseline digest is missing.")
    return {
        "baselineFindings": baseline,
        "currentFindings": current,
        "newViolations": 0,
        "resolvedViolations": int(payload.get("resolvedViolationCount", 0)),
        "baselineDigest": payload["baselineDigest"],
    }


def _validate_visual_evidence(path: Path, *, root: Path) -> dict[str, Any]:
    payload = _load_json(path)
    failures = payload.get("runtimeFailures", {})
    screenshots = payload.get("screenshots", [])
    videos = payload.get("videos", [])
    _require(
        payload.get("kind") == "klm-saas-v5-p5-visual-evidence",
        "Unexpected visual evidence kind.",
    )
    _require(payload.get("status") == "passed", "Visual evidence capture did not pass.")
    _require(payload.get("localOnly") is True, "Visual evidence is not local-only.")
    _require(len(payload.get("profiles", [])) == 2, "Visual evidence profile matrix is incomplete.")
    _require(len(payload.get("routes", [])) >= 8, "Visual evidence route matrix is incomplete.")
    _require(len(screenshots) >= 16, "Visual evidence has fewer than 16 screenshots.")
    _require(len(videos) >= 2, "Visual evidence has fewer than two videos.")
    for key in ("externalNetworkRequests",):
        _require(failures.get(key) == 0, f"Visual evidence reported {key}.")
    for key in ("consoleErrors", "localRequestFailures", "pageErrors", "serverErrors"):
        _require(failures.get(key) == [], f"Visual evidence reported {key}.")
    for item in (*screenshots, *videos):
        relative = Path(str(item.get("path", "")))
        _require(relative and not relative.is_absolute(), "Visual evidence path is unsafe.")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as error:
            raise VerificationError("Visual evidence path escapes the repository.") from error
        _require(candidate.is_file(), f"Visual evidence file is missing: {relative.as_posix()}")
        _require(_file_sha256(candidate) == item.get("sha256"), "Visual evidence SHA differs.")
    return {
        "profiles": len(payload["profiles"]),
        "routes": len(payload["routes"]),
        "screenshots": len(screenshots),
        "videos": len(videos),
        "externalNetworkRequests": 0,
        "manifestDigest": payload.get("manifestDigest"),
    }


def _validate_p4(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    claims = payload.get("claims", {})
    source = payload.get("source", {})
    _require(payload.get("kind") == "klm-saas-v5-p4-verification", "Unexpected P4 manifest kind.")
    _require(payload.get("status") == "passed", "P4 verification did not pass.")
    _require(claims.get("offlineP4Verified") is True, "P4 offline claim is not verified.")
    _require(claims.get("externalProviderVerified") is False, "P4 made an external-provider claim.")
    _require(claims.get("productionVerified") is False, "P4 made a production claim.")
    _require(
        source.get("stableDuringVerification") is True, "P4 source changed during verification."
    )
    gates = payload.get("gates", {})
    _require(isinstance(gates, dict) and gates, "P4 gate inventory is empty or invalid.")
    _require(all(value is True for value in gates.values()), "At least one P4 gate failed.")
    return {
        "status": "passed",
        "sourceStable": True,
        "externalProviderVerified": False,
        "productionVerified": False,
    }


def _validate_oss_census(root: Path) -> dict[str, Any]:
    payload = _load_json(root / "docs/oss-function-units.lock.json")
    coverage = payload.get("coverage", {})
    dispositions = coverage.get("byDisposition", {})
    denominator = int(coverage.get("denominator", 0))
    _require(denominator > 0, "OSS census denominator is empty.")
    _require(
        len(payload.get("units", [])) == denominator,
        "OSS census unit count differs from denominator.",
    )
    _require(coverage.get("allPinsVerified") is True, "OSS census pins are not all verified.")
    _require(coverage.get("allUnitsHaveDisposition") is True, "OSS census has unclassified units.")
    _require(
        int(coverage.get("validatedFamilyBaseline", 0)) >= 135, "OSS family baseline regressed."
    )
    allowed_states = {
        "discovered",
        "mapped",
        "native_shadow",
        "native_primary",
        "native_verified",
        "blocked_proprietary",
        "blocked_license",
    }
    _require(
        set(dispositions) == allowed_states,
        "OSS census disposition vocabulary is incomplete or unexpected.",
    )
    for state in ("discovered", "mapped", "blocked_proprietary"):
        _require(
            dispositions.get(state) == 0, f"OSS census retains unresolved disposition: {state}."
        )
    _require(
        sum(int(value) for value in dispositions.values()) == denominator,
        "OSS disposition totals do not reconcile.",
    )
    _require(
        coverage.get("candidates", {}).get("manual_review") == 0,
        "OSS census retains manual-review candidates.",
    )
    native_shadow = int(dispositions.get("native_shadow", 0))
    native_primary = int(dispositions.get("native_primary", 0))
    native_verified = int(dispositions.get("native_verified", 0))
    blocked_license = int(dispositions.get("blocked_license", 0))
    mapped_licensed = native_shadow + native_primary + native_verified
    _require(mapped_licensed > 0, "OSS census has no licensed native mappings.")
    _require(
        mapped_licensed + blocked_license == denominator,
        "OSS licensed and license-blocked totals do not reconcile.",
    )
    return {
        "denominator": denominator,
        "mappedLicensed": mapped_licensed,
        "nativeShadow": native_shadow,
        "nativePrimary": native_primary,
        "nativeVerified": native_verified,
        "blockedLicense": blocked_license,
        "familyBaseline": coverage["validatedFamilyBaseline"],
    }


def _validate_oss_dispositions(root: Path) -> dict[str, Any]:
    payload = _load_json(root / "app/data/oss_function_unit_dispositions.json")
    generator = payload.get("generator", {})
    mapped = int(generator.get("mappedLicensedUnits", 0))
    primary = int(generator.get("primaryLicensedUnits", 0))
    shadow = int(generator.get("shadowLicensedUnits", 0))
    verified = int(generator.get("verifiedLicensedUnits", 0))
    _require(
        mapped > 0 and primary + shadow + verified == mapped,
        "OSS disposition maturity totals do not reconcile.",
    )
    rules = payload.get("rules", [])
    exact_rules = [rule for rule in rules if (rule.get("match") or {}).get("unitIds")]
    license_rules = [
        rule
        for rule in rules
        if rule.get("state") == "blocked_license"
        and not (rule.get("match") or {}).get("unitIds")
    ]
    _require(len(exact_rules) == mapped, "OSS dispositions do not cover every licensed unit.")
    exact_state_counts = {
        state: sum(rule.get("state") == state for rule in exact_rules)
        for state in ("native_primary", "native_shadow", "native_verified")
    }
    _require(
        exact_state_counts
        == {
            "native_primary": primary,
            "native_shadow": shadow,
            "native_verified": verified,
        },
        "OSS exact disposition rules disagree with generator maturity totals.",
    )
    _require(
        len(license_rules) == 1
        and license_rules[0].get("id") == "embedded-chat-root-license-review",
        "OSS dispositions lack the explicit repository-license block.",
    )
    _require(len(rules) == mapped + 1, "OSS disposition rule totals do not reconcile.")
    verification = generator.get("verification", {})
    _require(
        verification.get("state") == "per_unit_oracle_only",
        "OSS dispositions do not preserve per-unit oracle authority.",
    )
    _require(
        verification.get("aggregateManifestIsInput") is False,
        "OSS aggregate manifest is incorrectly authoritative.",
    )
    return {
        "mappedLicensedUnits": mapped,
        "nativePrimaryUnits": primary,
        "nativeShadowUnits": shadow,
        "verifiedLicensedUnits": verified,
        "ruleCount": len(rules),
        "licenseBlockRules": 1,
    }


def _validate_oss_function_unit(root: Path) -> dict[str, Any]:
    census = _validate_oss_census(root)
    payload = _load_json(root / FUNCTION_UNIT_MANIFEST_RELATIVE)
    _require(payload.get("schemaVersion") == 3, "FunctionUnit manifest schema is stale.")
    _require(payload.get("state") == "passed", "FunctionUnit manifest state is not passed.")
    _require(
        payload.get("mappedLicensedUnits") == census["mappedLicensed"],
        "FunctionUnit mapped count differs from census.",
    )
    _require(
        payload.get("nativePrimaryUnits") == census["nativePrimary"],
        "FunctionUnit primary count differs from census.",
    )
    _require(
        payload.get("nativeVerifiedUnits") == census["nativeVerified"],
        "FunctionUnit per-unit verified count differs from census.",
    )
    _require(
        payload.get("blockedLicenseUnits") == census["blockedLicense"],
        "FunctionUnit blocked-license count differs from census.",
    )
    _require(
        payload.get("externalEffectsExecuted") == 0,
        "FunctionUnit verification executed external effects.",
    )
    sidecar = payload.get("sidecarOff", {})
    _require(sidecar.get("proofPassed") is True, "FunctionUnit sidecar-off proof did not pass.")
    _require(
        sidecar.get("sourceAdaptersConstructed") == 0,
        "FunctionUnit sidecar-off proof constructed source adapters.",
    )
    for deprecated in (
        "licensedUnitsVerified",
        "runtimeContractsVerified",
        "verifiedUnitIds",
        "verifiedRuntimeContracts",
    ):
        _require(deprecated not in payload, f"FunctionUnit manifest retains {deprecated}.")
    mapped_ids = payload.get("mappedUnitIds", [])
    primary_ids = payload.get("nativePrimaryUnitIds", [])
    verified_bindings = payload.get("nativeVerifiedBindings", [])
    mapped_contracts = payload.get("mappedRuntimeContracts", [])
    _require(
        len(mapped_ids) == len(set(mapped_ids)) == census["mappedLicensed"],
        "FunctionUnit mapped ID inventory is incomplete or duplicated.",
    )
    _require(
        len(primary_ids) == len(set(primary_ids)) == census["nativePrimary"],
        "FunctionUnit primary ID inventory is incomplete or duplicated.",
    )
    _require(
        len(verified_bindings) == census["nativeVerified"],
        "FunctionUnit oracle-bound verification inventory is incomplete.",
    )
    _require(
        len(mapped_contracts) == census["mappedLicensed"],
        "FunctionUnit runtime contract inventory is incomplete.",
    )
    architecture = payload.get("architectureQuality", {})
    _require(
        architecture.get("nativeShadowUnits") == census["nativeShadow"],
        "FunctionUnit shadow count differs from the architecture gate.",
    )
    _require(
        architecture.get("nativePrimaryUnits") == census["nativePrimary"],
        "FunctionUnit primary count differs from the architecture gate.",
    )
    for key in (
        "violations",
        "externalNetworkAllowances",
        "journalPrimaryUnits",
        "ambiguousMutationExecutionAllowed",
    ):
        _require(architecture.get(key) == 0, f"FunctionUnit architecture violation: {key}.")
    return {
        "mappedLicensedUnits": census["mappedLicensed"],
        "nativePrimaryUnits": census["nativePrimary"],
        "nativeShadowUnits": census["nativeShadow"],
        "nativeVerifiedUnits": census["nativeVerified"],
        "blockedLicenseUnits": census["blockedLicense"],
        "externalEffectsExecuted": 0,
        "sidecarOff": True,
    }


def _validate_native_architecture(root: Path) -> dict[str, Any]:
    census = _validate_oss_census(root)
    payload = _load_json(root / NATIVE_ARCHITECTURE_SCORECARD_RELATIVE)
    _require(payload.get("schemaVersion") == 2, "Native architecture scorecard schema is stale.")
    _require(
        payload.get("architectureGatePassed") is True,
        "Native architecture superiority gate did not pass.",
    )
    criteria = payload.get("criteria", [])
    _require(
        isinstance(criteria, list)
        and tuple(criterion.get("id") for criterion in criteria)
        == REQUIRED_NATIVE_ARCHITECTURE_CRITERIA
        and all(criterion.get("passed") is True for criterion in criteria),
        "Native architecture criteria are incomplete or failing.",
    )
    release_binding = payload.get("releaseProofBinding", {})
    _require(
        release_binding.get("current") is True
        and len(str(release_binding.get("sourceDigest", ""))) == 64
        and release_binding.get("sourceFileCount", 0) > 0,
        "Native architecture scorecard is not bound to the current release proofs.",
    )
    maturity = payload.get("functionMaturity", {})
    expected_maturity = {
        "licensed": census["mappedLicensed"],
        "native_shadow": census["nativeShadow"],
        "native_primary": census["nativePrimary"],
        "native_verified": census["nativeVerified"],
        "blocked_license": census["blockedLicense"],
    }
    _require(
        all(maturity.get(key) == value for key, value in expected_maturity.items()),
        "Native architecture maturity differs from the committed census.",
    )
    behavioral_allowed = (
        census["mappedLicensed"] > 0
        and census["nativeShadow"] == 0
        and census["nativePrimary"] == 0
        and census["nativeVerified"] == census["mappedLicensed"]
    )
    _require(
        payload.get("globalBehavioralSuperiorityClaimAllowed") is behavioral_allowed,
        "Native architecture scorecard overstates behavioral superiority.",
    )
    return {
        "architectureGatePassed": True,
        "globalBehavioralSuperiorityClaimAllowed": behavioral_allowed,
        "criteriaPassed": len(criteria),
    }


def _validate_oss_native(root: Path) -> dict[str, Any]:
    payload = _load_json(root / OSS_NATIVE_MANIFEST_RELATIVE)
    native = int(payload.get("nativeCount", 0))
    actions = int(payload.get("actionCount", 0))
    _require(native >= 135, "Native OSS family coverage regressed.")
    _require(int(payload.get("contractCount", 0)) > 0, "Native OSS contract inventory is empty.")
    _require(actions > 0, "Native OSS action inventory is empty.")
    _require(
        payload.get("externalEffectsExecuted") == 0,
        "Native OSS verification executed external effects.",
    )
    _require(
        payload.get("externalNetworkAssertionsValidated") == actions,
        "Native OSS external-network assertions are incomplete.",
    )
    return {
        "nativeCount": native,
        "contractCount": payload["contractCount"],
        "actionCount": actions,
        "externalEffectsExecuted": 0,
        "externalEffectIndependentlyObserved": bool(
            payload.get("externalEffectIndependentlyObserved", False)
        ),
    }


def _extract_json_object(raw: str) -> dict[str, Any]:
    stripped = raw.strip()
    try:
        value = json.loads(stripped)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for match in re.finditer(r"\{", raw):
        try:
            value, _ = decoder.raw_decode(raw[match.start() :])
        except ValueError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    if not candidates:
        raise VerificationError("Command log does not contain a JSON object.")
    return candidates[-1]


def _frontend_uses_react_router_rsc(root: Path) -> list[str]:
    frontend = root / "frontend"
    paths = (
        [
            path
            for path in frontend.rglob("*")
            if path.is_file()
            and path.suffix in REACT_ROUTER_RSC_SCAN_EXTENSIONS
            and not (
                set(path.relative_to(frontend).parts)
                & REACT_ROUTER_RSC_IGNORED_DIRECTORIES
            )
        ]
        if frontend.is_dir()
        else []
    )
    package_json = frontend / "package.json"
    if package_json.is_file():
        paths.append(package_json)
    matches: list[str] = []
    for path in sorted(set(paths)):
        contents = path.read_text(encoding="utf-8", errors="ignore")
        if (
            REACT_ROUTER_RSC_API_PATTERN.search(contents)
            or any(marker in contents for marker in REACT_ROUTER_RSC_MODULE_MARKERS)
        ):
            matches.append(_safe_relative(path, root))
    return matches


def _validate_react_router_rsc_audit_exception(
    payload: Mapping[str, Any],
    *,
    root: Path,
    today: date,
) -> dict[str, Any]:
    _require(
        today < REACT_ROUTER_RSC_EXCEPTION_REVIEW_BEFORE,
        "React Router npm audit exception expired and requires security review.",
    )
    findings = payload.get("vulnerabilities")
    _require(isinstance(findings, dict), "npm audit vulnerability details are missing.")
    severe_findings = {
        name: finding
        for name, finding in findings.items()
        if isinstance(finding, dict)
        and finding.get("severity") in {"high", "critical"}
    }
    _require(
        set(severe_findings) == REACT_ROUTER_RSC_FINDINGS,
        "npm audit has an unapproved high or critical finding.",
    )

    router = severe_findings["react-router"]
    router_dom = severe_findings["react-router-dom"]
    router_advisories = router.get("via")
    _require(
        isinstance(router_advisories, list)
        and len(router_advisories) == 1
        and isinstance(router_advisories[0], dict),
        "React Router audit finding does not match the reviewed advisory.",
    )
    advisory = router_advisories[0]
    _require(
        router.get("name") == "react-router"
        and router.get("severity") == "high"
        and router.get("nodes") == ["node_modules/react-router"]
        and advisory.get("name") == "react-router"
        and advisory.get("dependency") == "react-router"
        and advisory.get("url") == REACT_ROUTER_RSC_ADVISORY_URL
        and advisory.get("severity") == "high"
        and router_dom.get("name") == "react-router-dom"
        and router_dom.get("severity") == "high"
        and router_dom.get("via") == ["react-router"]
        and router_dom.get("nodes") == ["node_modules/react-router-dom"],
        "React Router audit dependency chain differs from the reviewed finding.",
    )

    lock_path = root / "frontend/package-lock.json"
    _require(lock_path.is_file(), "Frontend package lock is missing.")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    packages = lock.get("packages")
    _require(isinstance(packages, dict), "Frontend package lock is invalid.")
    root_package = packages.get("")
    router_package = packages.get("node_modules/react-router")
    router_dom_package = packages.get("node_modules/react-router-dom")
    _require(
        isinstance(root_package, dict)
        and isinstance(root_package.get("dependencies"), dict)
        and root_package["dependencies"].get("react-router-dom")
        == REACT_ROUTER_RSC_EXCEPTION_VERSION
        and isinstance(router_package, dict)
        and router_package.get("version") == REACT_ROUTER_RSC_EXCEPTION_VERSION
        and isinstance(router_dom_package, dict)
        and router_dom_package.get("version") == REACT_ROUTER_RSC_EXCEPTION_VERSION
        and isinstance(router_dom_package.get("dependencies"), dict)
        and router_dom_package["dependencies"].get("react-router")
        == REACT_ROUTER_RSC_EXCEPTION_VERSION,
        "React Router audit exception only applies to the reviewed patched version.",
    )

    rsc_sources = _frontend_uses_react_router_rsc(root)
    _require(
        not rsc_sources,
        "React Router unstable RSC APIs are present; audit exception is not applicable.",
    )
    return {
        "advisory": REACT_ROUTER_RSC_ADVISORY,
        "packages": sorted(REACT_ROUTER_RSC_FINDINGS),
        "installedVersion": REACT_ROUTER_RSC_EXCEPTION_VERSION,
        "upstreamAdvisory": REACT_ROUTER_RSC_UPSTREAM_URL,
        "upstreamBackport": REACT_ROUTER_RSC_BACKPORT_URL,
        "upstreamRelease": REACT_ROUTER_RSC_RELEASE_URL,
        "reviewBefore": REACT_ROUTER_RSC_EXCEPTION_REVIEW_BEFORE.isoformat(),
        "rscSources": [],
    }


def _validate_npm_audit(
    log_text: str,
    *,
    root: Path = ROOT,
    today: date | None = None,
) -> dict[str, Any]:
    payload = _extract_json_object(log_text)
    vulnerabilities = payload.get("metadata", {}).get("vulnerabilities", {})
    _require(isinstance(vulnerabilities, dict), "npm audit metadata is missing.")
    raw_high = int(vulnerabilities.get("high", 0))
    raw_critical = int(vulnerabilities.get("critical", 0))
    exceptions: list[dict[str, Any]] = []
    if raw_high or raw_critical:
        _require(
            raw_high == 2 and raw_critical == 0,
            "npm audit found high or critical vulnerabilities.",
        )
        exceptions.append(
            _validate_react_router_rsc_audit_exception(
                payload,
                root=root,
                today=today or datetime.now(UTC).date(),
            )
        )
    high = raw_high - (2 if exceptions else 0)
    critical = raw_critical
    _require(high == 0 and critical == 0, "npm audit found high or critical vulnerabilities.")
    return {
        "offline": True,
        "rawHigh": raw_high,
        "rawCritical": raw_critical,
        "high": high,
        "critical": critical,
        "total": int(vulnerabilities.get("total", 0)),
        "reviewedExceptions": exceptions,
    }


def _validate_artifact(task: TaskSpec, *, root: Path, log_text: str) -> dict[str, Any]:
    if task.validator == "exit-code":
        return {"status": "passed", "kind": "exit-code"}
    if task.validator == "frontend-build":
        index = root / "frontend/dist/index.html"
        _require(
            index.is_file() and index.stat().st_size > 0, "Frontend build artifact is missing."
        )
        return {
            "status": "passed",
            "index": _safe_relative(index, root),
            "sha256": _file_sha256(index),
        }
    if task.validator == "oss-census":
        return {"status": "passed", **_validate_oss_census(root)}
    if task.validator == "oss-dispositions":
        return {"status": "passed", **_validate_oss_dispositions(root)}
    if task.validator == "oss-function-unit":
        return {"status": "passed", **_validate_oss_function_unit(root)}
    if task.validator == "native-architecture":
        return {"status": "passed", **_validate_native_architecture(root)}
    if task.validator == "oss-native":
        return {"status": "passed", **_validate_oss_native(root)}
    if task.validator == "npm-audit":
        return {"status": "passed", **_validate_npm_audit(log_text, root=root)}
    if task.validator == "visual-evidence":
        if task.artifact is None:
            raise VerificationError("Visual evidence validator requires an artifact.")
        return {"status": "passed", **_validate_visual_evidence(task.artifact, root=root)}
    if task.artifact is None:
        raise VerificationError(f"Validator {task.validator} requires an artifact.")
    validators: dict[str, Callable[[Path], dict[str, Any]]] = {
        "baseline": _validate_baseline,
        "conversation-simulation": _validate_simulation,
        "junit-backend": _parse_junit,
        "junit-playwright": _parse_junit,
        "local-stack-live": _validate_stack,
        "local-load-performance": _validate_local_load,
        "p4": _validate_p4,
        "ruff-ratchet": _validate_ruff_ratchet,
        "sbom": _validate_sbom,
        "vitest-json": _validate_vitest,
    }
    validator = validators.get(task.validator)
    if validator is None:
        raise VerificationError(f"Unknown artifact validator: {task.validator}")
    return {"status": "passed", **validator(task.artifact)}


def _prepare_generated_artifact(task: TaskSpec, evidence_dir: Path) -> None:
    if not task.artifact_is_generated or task.artifact is None:
        return
    artifact = task.artifact.resolve()
    evidence = evidence_dir.resolve()
    try:
        artifact.relative_to(evidence)
    except ValueError as error:
        raise VerificationError(
            "Generated P5 artifacts must stay inside the evidence directory."
        ) from error
    artifact.unlink(missing_ok=True)


def run_task(
    task: TaskSpec,
    *,
    root: Path = ROOT,
    evidence_dir: Path = EVIDENCE_DIR,
    executor: Executor | None = None,
    node_runtime: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute, redact, hash, and semantically validate one task."""

    _prepare_generated_artifact(task, evidence_dir)
    runner = executor or SubprocessExecutor()
    raw = runner.execute(task, _offline_environment(task, node_runtime=node_runtime))
    output = _redact_text(raw.output)
    log_path = evidence_dir / f"{task.name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(output, encoding="utf-8")
    validation: dict[str, Any]
    reviewed_audit_exit = (
        task.validator == "npm-audit"
        and raw.exit_code == 1
        and raw.error_type is None
    )
    if raw.exit_code != 0 and not reviewed_audit_exit:
        validation = {
            "status": "failed",
            "reason": "non_zero_exit",
        }
    else:
        try:
            validation = _validate_artifact(task, root=root, log_text=output)
        except Exception as error:  # noqa: BLE001 - fail-closed artifact boundary
            validation = {
                "status": "failed",
                "reason": str(error),
                "errorType": type(error).__name__,
            }
    if (
        reviewed_audit_exit
        and validation.get("status") == "passed"
        and len(validation.get("reviewedExceptions", [])) != 1
    ):
        validation = {
            "status": "failed",
            "reason": "npm_audit_exit_1_without_reviewed_exception",
        }
    artifact_evidence: dict[str, Any] | None = None
    if task.artifact is not None and task.artifact.is_file():
        artifact_evidence = {
            "path": _safe_relative(task.artifact, root),
            "sha256": _file_sha256(task.artifact),
            "bytes": task.artifact.stat().st_size,
        }
    passed = (
        raw.exit_code == 0 or reviewed_audit_exit
    ) and validation.get("status") == "passed"
    return {
        "name": task.name,
        "category": task.category,
        "required": task.required,
        "status": "passed" if passed else "failed",
        "exitCode": raw.exit_code,
        "durationMs": raw.duration_ms,
        "command": sanitize_command(task.command),
        "commandDisplay": shlex.join(sanitize_command(task.command)),
        "cwd": _safe_relative(task.cwd, root),
        "timeoutSeconds": task.timeout_seconds,
        "executionErrorType": raw.error_type,
        "log": {
            "path": _safe_relative(log_path, root),
            "sha256": _file_sha256(log_path),
            "bytes": log_path.stat().st_size,
        },
        "artifact": artifact_evidence,
        "validation": validation,
    }


def _skipped_result(task: TaskSpec, *, root: Path) -> dict[str, Any]:
    return {
        "name": task.name,
        "category": task.category,
        "required": task.required,
        "status": "skipped_fail_fast",
        "exitCode": None,
        "durationMs": 0,
        "command": sanitize_command(task.command),
        "commandDisplay": shlex.join(sanitize_command(task.command)),
        "cwd": _safe_relative(task.cwd, root),
        "timeoutSeconds": task.timeout_seconds,
        "executionErrorType": None,
        "log": None,
        "artifact": None,
        "validation": {"status": "not_run", "reason": "fail_fast"},
    }


def _result(results: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any] | None:
    return next((item for item in results if item.get("name") == name), None)


def _passed(results: Sequence[Mapping[str, Any]], name: str) -> bool:
    item = _result(results, name)
    return bool(item and item.get("status") == "passed")


def _snapshot_summary(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    before = _result(results, "baseline-before")
    after = _result(results, "baseline-after")
    before_validation = (before or {}).get("validation", {})
    after_validation = (after or {}).get("validation", {})
    before_digest = before_validation.get("sourceDigest")
    after_digest = after_validation.get("sourceDigest")
    before_git_head = before_validation.get("gitHead")
    after_git_head = after_validation.get("gitHead")
    stable = bool(
        before_digest
        and before_digest == after_digest
        and before_git_head
        and before_git_head == after_git_head
    )
    return {
        "digestBefore": before_digest,
        "digestAfter": after_digest,
        "gitHeadBefore": before_git_head,
        "gitHeadAfter": after_git_head,
        "gitBranchBefore": before_validation.get("gitBranch"),
        "gitBranchAfter": after_validation.get("gitBranch"),
        "worktreeDirtyBefore": before_validation.get("worktreeDirty"),
        "worktreeDirtyAfter": after_validation.get("worktreeDirty"),
        "fileCountBefore": before_validation.get("sourceFileCount"),
        "fileCountAfter": after_validation.get("sourceFileCount"),
        "snapshotReadyBefore": before_validation.get("snapshotReady") is True,
        "snapshotReadyAfter": after_validation.get("snapshotReady") is True,
        "secretFindingsBefore": before_validation.get("secretFindings"),
        "secretFindingsAfter": after_validation.get("secretFindings"),
        "stableDuringVerification": stable,
    }


def _final_gates(
    results: Sequence[Mapping[str, Any]],
    *,
    plan_contract: Mapping[str, Any],
    source: Mapping[str, Any],
    selected_names: Sequence[str],
    full_plan: Sequence[TaskSpec],
) -> dict[str, bool]:
    all_tasks_selected = set(selected_names) == {task.name for task in full_plan}
    all_required_passed = all(_passed(results, task.name) for task in full_plan if task.required)
    return {
        "planContractComplete": plan_contract.get("passed") is True,
        "allMandatoryTasksSelected": all_tasks_selected,
        "allRequiredTasksPassed": all_required_passed,
        "snapshotReadyBefore": source.get("snapshotReadyBefore") is True,
        "snapshotReadyAfter": source.get("snapshotReadyAfter") is True,
        "sourceStable": source.get("stableDuringVerification") is True,
        "secretScanClean": source.get("secretFindingsBefore") == 0
        and source.get("secretFindingsAfter") == 0,
        "backendFullPytestWithJUnit": _passed(results, "backend-pytest-full"),
        "ruffPassed": _passed(results, "ruff-full"),
        "gitDiffCheckPassed": _passed(results, "git-diff-check"),
        "frontendUnit321PlusPassed": _passed(results, "frontend-unit"),
        "frontendTypecheckPassed": _passed(results, "frontend-typecheck"),
        "frontendBuildPassed": _passed(results, "frontend-build"),
        "playwrightDesktopPassed": _passed(results, "playwright-desktop-1440"),
        "playwrightTabletPassed": _passed(results, "playwright-tablet-834"),
        "playwrightMobilePassed": _passed(results, "playwright-mobile-393"),
        "liveStackReadOnlyPassed": _passed(results, "local-stack-live"),
        "localHotEndpointP95Passed": _passed(results, "local-hot-endpoints"),
        "conversationSimulation1000NoEffectsPassed": _passed(results, "conversation-simulation"),
        "sbomPassed": _passed(results, "sbom"),
        "p4Passed": _passed(results, "p4"),
        "ossCensusPassed": _passed(results, "oss-census"),
        "ossManifestsPassed": all(
            _passed(results, name)
            for name in (
                "oss-dispositions",
                "oss-function-unit-manifest",
                "native-architecture-gate",
                "oss-native-manifest",
            )
        ),
        "nativeArchitectureGatePassed": _passed(results, "native-architecture-gate"),
        "npmAuditOfflinePassed": _passed(results, "npm-audit-offline"),
    }


def verify(
    *,
    mode: RunMode = "final",
    selectors: Sequence[str] = (),
    fail_fast: bool = False,
    output_path: Path = MANIFEST_PATH,
    evidence_dir: Path = EVIDENCE_DIR,
    stack_env_file: Path | None = None,
    frontend_url: str = "http://127.0.0.1:4173",
    root: Path = ROOT,
    executor: Executor | None = None,
    now: Callable[[], datetime] | None = None,
) -> tuple[dict[str, Any], int]:
    """Run a final or explicitly partial P5 verification."""

    if (
        mode == "quick"
        and output_path.resolve() == (root / "app/data/saas_v5/p5-verification.json").resolve()
    ):
        raise VerificationError("Quick mode cannot overwrite the canonical P5 manifest.")
    node_runtime = (
        _resolve_frontend_node(root)
        if executor is None
        else {
            "path": "custom-executor",
            "version": "not-executed",
            "source": "custom-executor",
            "supported": False,
        }
    )
    full_plan = build_plan(
        root=root,
        evidence_dir=evidence_dir,
        stack_env_file=stack_env_file,
        frontend_url=frontend_url,
    )
    plan_contract = validate_plan_contract(full_plan)
    if plan_contract["passed"] is not True:
        raise VerificationError(
            "The P5 task plan is incomplete or unsafe; no subprocess was executed."
        )
    selected_plan = select_plan(full_plan, mode=mode, selectors=selectors)
    selected_names = [task.name for task in selected_plan]
    evidence_dir.mkdir(parents=True, exist_ok=True)

    playwright_harness: dict[str, Any] | None = None
    if any(task.category.startswith("frontend-playwright-") for task in selected_plan):
        playwright_harness = _write_playwright_config(
            evidence_dir / "playwright.p5.config.mjs",
            root=root,
            frontend_url=frontend_url,
        )

    results: list[dict[str, Any]] = []
    aborted = False
    baseline_after: TaskSpec | None = None
    for task in selected_plan:
        if task.name == "baseline-after":
            baseline_after = task
            continue
        if aborted:
            results.append(_skipped_result(task, root=root))
            continue
        print(f"[p5] {task.name}: {shlex.join(sanitize_command(task.command))}", flush=True)
        result = run_task(
            task,
            root=root,
            evidence_dir=evidence_dir,
            executor=executor,
            node_runtime=node_runtime,
        )
        results.append(result)
        print(f"[p5] {task.name}: {result['status']}", flush=True)
        if fail_fast and result["status"] != "passed":
            aborted = True
    if baseline_after is not None:
        print(f"[p5] {baseline_after.name}: snapshot safety envelope", flush=True)
        results.append(
            run_task(
                baseline_after,
                root=root,
                evidence_dir=evidence_dir,
                executor=executor,
                node_runtime=node_runtime,
            )
        )

    source = _snapshot_summary(results)
    gates = _final_gates(
        results,
        plan_contract=plan_contract,
        source=source,
        selected_names=selected_names,
        full_plan=full_plan,
    )
    final_passed = mode == "final" and all(gates.values())
    quick_passed = (
        mode == "quick"
        and plan_contract["passed"] is True
        and all(item["status"] == "passed" for item in results)
        and source["stableDuringVerification"]
    )
    status = (
        "passed"
        if final_passed
        else "partial_passed"
        if quick_passed
        else "failed"
        if mode == "final"
        else "partial_failed"
    )
    generated_at = (now or (lambda: datetime.now(UTC)))().astimezone(UTC).isoformat()
    manifest: dict[str, Any] = {
        "schemaVersion": "1.0",
        "kind": "klm-saas-v5-p5-verification",
        "verifierVersion": SCRIPT_VERSION,
        "generatedAt": generated_at,
        "status": status,
        "mode": mode,
        "localOnly": True,
        "runtime": {
            "python": platform.python_version(),
            "frontendNode": {
                **node_runtime,
                "path": (
                    _safe_relative(Path(str(node_runtime["path"])), root)
                    if node_runtime["path"] != "custom-executor"
                    else "custom-executor"
                ),
            },
        },
        "claims": {
            "localP5Verified": final_passed,
            "localSaasApprovalReadyV5": final_passed,
            "nativeArchitectureGatePassed": bool(
                _passed(results, "native-architecture-gate")
                and (_result(results, "native-architecture-gate") or {})
                .get("validation", {})
                .get("architectureGatePassed")
                is True
            ),
            "globalBehavioralSuperiorityClaimAllowed": bool(
                _passed(results, "native-architecture-gate")
                and (_result(results, "native-architecture-gate") or {})
                .get("validation", {})
                .get("globalBehavioralSuperiorityClaimAllowed")
                is True
            ),
            "externalProviderVerified": False,
            "productionVerified": False,
            "awsVerified": False,
        },
        "executionPolicy": {
            "shellUsed": False,
            "externalCredentialsCleared": True,
            "externalProxyTarget": "loopback-deny-port-9",
            "loopbackAllowed": True,
            "stackLifecycleCommandsAllowed": False,
            "stackStartedPulledOrStopped": False,
            "awsCommandsAllowed": False,
            "independentPacketCapture": False,
            "networkEvidenceScope": (
                "offline flags, denied proxy, command allowlist, and child artifact assertions"
            ),
        },
        "externalEffects": {
            "orchestratorExternalRequestsIssued": 0,
            "orchestratorAwsActionsIssued": 0,
            "externalProviderCallsObserved": 0,
            "observationIsProductionVerification": False,
        },
        "source": source,
        "planContract": plan_contract,
        "selection": {
            "selectors": list(selectors),
            "selectedTaskNames": selected_names,
            "omittedTaskNames": [
                task.name for task in full_plan if task.name not in set(selected_names)
            ],
            "finalSelectionComplete": set(selected_names) == {task.name for task in full_plan},
        },
        "gates": gates,
        "tasksExpected": [task.name for task in full_plan],
        "tasks": results,
        "generatedHarnesses": {
            "playwright": playwright_harness,
        },
        "blocked": [
            {
                "scope": "external-model-provider-quality",
                "status": "blocked_by_scope",
                "reason": "external providers are intentionally disabled in local P5",
            },
            {
                "scope": "production-and-aws",
                "status": "blocked_by_scope",
                "reason": "production and AWS are explicitly outside local P5",
            },
        ],
    }
    manifest["manifestDigest"] = _digest(manifest)
    _atomic_write_json(output_path, manifest)
    return manifest, 0 if (final_passed or quick_passed) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("final", "quick"), default="final")
    parser.add_argument(
        "--select",
        action="append",
        default=[],
        help="Task name or category; repeatable and accepted only in quick mode.",
    )
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE_DIR)
    parser.add_argument("--stack-env-file", type=Path, default=None)
    parser.add_argument("--frontend-url", default="http://127.0.0.1:4173")
    parser.add_argument("--list", action="store_true", dest="list_tasks")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    evidence_dir = (
        args.evidence_dir if args.evidence_dir.is_absolute() else ROOT / args.evidence_dir
    )
    if args.list_tasks:
        plan = build_plan(
            evidence_dir=evidence_dir,
            stack_env_file=args.stack_env_file,
            frontend_url=args.frontend_url,
        )
        print(
            json.dumps(
                [
                    {"name": task.name, "category": task.category, "required": task.required}
                    for task in plan
                ],
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.output is None:
        output = (
            MANIFEST_PATH if args.mode == "final" else evidence_dir / "p5-quick-verification.json"
        )
    else:
        output = args.output if args.output.is_absolute() else ROOT / args.output
    try:
        manifest, exit_code = verify(
            mode=args.mode,
            selectors=args.select,
            fail_fast=args.fail_fast,
            output_path=output,
            evidence_dir=evidence_dir,
            stack_env_file=args.stack_env_file,
            frontend_url=args.frontend_url,
        )
    except VerificationError as error:
        print(f"P5_VERIFICATION_REFUSED: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "mode": manifest["mode"],
                "sourceStable": manifest["source"]["stableDuringVerification"],
                "tasksPassed": sum(task["status"] == "passed" for task in manifest["tasks"]),
                "tasksSelected": len(manifest["selection"]["selectedTaskNames"]),
                "externalProviderVerified": False,
                "productionVerified": False,
                "manifest": _safe_relative(output, ROOT),
                "manifestDigest": manifest["manifestDigest"],
            },
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
