from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts import verify_saas_v5_p5 as verifier


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_junit(path: Path, *, tests: int = 12, skipped: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<testsuites tests="{tests}" failures="0" errors="0" skipped="{skipped}">'
        f'<testsuite name="p5" tests="{tests}" failures="0" errors="0" skipped="{skipped}" />'
        "</testsuites>",
        encoding="utf-8",
    )


def _write_react_router_lock(
    root: Path,
    *,
    root_version: str = "7.18.2",
    router_version: str = "7.18.2",
    router_dom_version: str = "7.18.2",
    router_dom_edge: str = "7.18.2",
) -> None:
    _write_json(
        root / "frontend/package-lock.json",
        {
            "packages": {
                "": {
                    "dependencies": {
                        "react-router-dom": root_version,
                    }
                },
                "node_modules/react-router": {"version": router_version},
                "node_modules/react-router-dom": {
                    "version": router_dom_version,
                    "dependencies": {
                        "react-router": router_dom_edge,
                    },
                },
            }
        },
    )


def _react_router_audit_payload(*, high: int = 2) -> str:
    vulnerabilities: dict[str, Any] = {
        "react-router": {
            "name": "react-router",
            "severity": "high",
            "via": [
                {
                    "name": "react-router",
                    "dependency": "react-router",
                    "url": verifier.REACT_ROUTER_RSC_ADVISORY_URL,
                    "severity": "high",
                }
            ],
            "nodes": ["node_modules/react-router"],
        },
        "react-router-dom": {
            "name": "react-router-dom",
            "severity": "high",
            "via": ["react-router"],
            "nodes": ["node_modules/react-router-dom"],
        },
    }
    if high > 2:
        vulnerabilities["unexpected-package"] = {
            "name": "unexpected-package",
            "severity": "high",
            "via": [],
        }
    return json.dumps(
        {
            "vulnerabilities": vulnerabilities,
            "metadata": {
                "vulnerabilities": {
                    "info": 0,
                    "low": 0,
                    "moderate": 0,
                    "high": high,
                    "critical": 0,
                    "total": high,
                }
            },
        }
    )


class FixedRawExecutor:
    def __init__(
        self,
        *,
        exit_code: int,
        output: str,
        error_type: str | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.output = output
        self.error_type = error_type

    def execute(
        self,
        task: verifier.TaskSpec,
        environment: Mapping[str, str],
    ) -> verifier.RawExecution:
        return verifier.RawExecution(
            self.exit_code,
            5,
            self.output,
            error_type=self.error_type,
        )


def _npm_audit_task(root: Path) -> verifier.TaskSpec:
    return verifier.TaskSpec(
        name="npm-audit-offline",
        category="dependency-audit",
        command=("npm", "audit", "--offline", "--audit-level=high", "--json"),
        cwd=root / "frontend",
        validator="npm-audit",
    )


def _write_oss_contracts(root: Path) -> None:
    _write_json(
        root / "docs/oss-function-units.lock.json",
        {
            "coverage": {
                "denominator": 3,
                "allPinsVerified": True,
                "allUnitsHaveDisposition": True,
                "validatedFamilyBaseline": 135,
                "byDisposition": {
                    "discovered": 0,
                    "mapped": 0,
                    "native_shadow": 1,
                    "native_primary": 1,
                    "native_verified": 0,
                    "blocked_proprietary": 0,
                    "blocked_license": 1,
                },
                "candidates": {"manual_review": 0},
            },
            "units": [
                {"id": "primary"},
                {"id": "shadow"},
                {"id": "blocked"},
            ],
        },
    )
    _write_json(
        root / "app/data/oss_function_unit_dispositions.json",
        {
            "generator": {
                "mappedLicensedUnits": 2,
                "primaryLicensedUnits": 1,
                "shadowLicensedUnits": 1,
                "verifiedLicensedUnits": 0,
                "verification": {
                    "state": "per_unit_oracle_only",
                    "aggregateManifestIsInput": False,
                },
            },
            "rules": [
                {"match": {"unitIds": ["primary"]}, "state": "native_primary"},
                {"match": {"unitIds": ["shadow"]}, "state": "native_shadow"},
                {
                    "id": "embedded-chat-root-license-review",
                    "match": {"licenseStatuses": ["package-metadata-only"]},
                    "state": "blocked_license",
                },
            ],
        },
    )
    _write_json(
        root / verifier.FUNCTION_UNIT_MANIFEST_RELATIVE,
        {
            "schemaVersion": 3,
            "state": "passed",
            "mappedLicensedUnits": 2,
            "nativePrimaryUnits": 1,
            "nativeVerifiedUnits": 0,
            "blockedLicenseUnits": 1,
            "externalEffectsExecuted": 0,
            "sidecarOff": {
                "proofPassed": True,
                "sourceAdaptersConstructed": 0,
            },
            "mappedUnitIds": ["primary", "shadow"],
            "nativePrimaryUnitIds": ["primary"],
            "nativeVerifiedBindings": [],
            "mappedRuntimeContracts": [
                {"unitId": "primary"},
                {"unitId": "shadow"},
            ],
            "architectureQuality": {
                "nativePrimaryUnits": 1,
                "nativeShadowUnits": 1,
                "violations": 0,
                "externalNetworkAllowances": 0,
                "journalPrimaryUnits": 0,
                "ambiguousMutationExecutionAllowed": 0,
            },
        },
    )
    _write_json(
        root / verifier.NATIVE_ARCHITECTURE_SCORECARD_RELATIVE,
        {
            "schemaVersion": 2,
            "architectureGatePassed": True,
            "structuralPreflightPassed": True,
            "globalBehavioralSuperiorityClaimAllowed": False,
            "releaseProofBinding": {
                "current": True,
                "sourceDigest": "a" * 64,
                "sourceFileCount": 100,
            },
            "functionMaturity": {
                "licensed": 2,
                "native_shadow": 1,
                "native_primary": 1,
                "native_verified": 0,
                "blocked_license": 1,
            },
            "criteria": [
                {"id": criterion_id, "passed": True}
                for criterion_id in verifier.REQUIRED_NATIVE_ARCHITECTURE_CRITERIA
            ],
        },
    )
    _write_json(
        root / verifier.OSS_NATIVE_MANIFEST_RELATIVE,
        {
            "nativeCount": 135,
            "contractCount": 121,
            "actionCount": 511,
            "externalEffectsExecuted": 0,
            "externalNetworkAssertionsValidated": 511,
            "externalEffectIndependentlyObserved": False,
        },
    )


class FakeExecutor:
    def __init__(
        self,
        root: Path,
        *,
        after_digest: str = "source-digest",
        fail_task: str | None = None,
        simulation_count: int = 2_800,
        simulation_messages: int = 0,
    ) -> None:
        self.root = root
        self.after_digest = after_digest
        self.fail_task = fail_task
        self.simulation_count = simulation_count
        self.simulation_messages = simulation_messages
        self.calls: list[str] = []

    def execute(
        self,
        task: verifier.TaskSpec,
        environment: Mapping[str, str],
    ) -> verifier.RawExecution:
        self.calls.append(task.name)
        assert environment["KLM_EXTERNAL_ACTIONS_ENABLED"] == "false"
        assert environment["KLM_MODEL_APIS_ENABLED"] == "false"
        assert environment["AWS_ACCESS_KEY_ID"] == ""
        assert environment["NPM_CONFIG_OFFLINE"] == "true"
        if task.name == self.fail_task:
            return verifier.RawExecution(1, 3, "deliberate failure")
        if task.validator == "frontend-build":
            index = self.root / "frontend/dist/index.html"
            index.parent.mkdir(parents=True, exist_ok=True)
            index.write_text("<main>KLM</main>", encoding="utf-8")
        if task.artifact is not None:
            self._write_artifact(task)
        output = "ok"
        if task.validator == "npm-audit":
            output = json.dumps(
                {
                    "metadata": {
                        "vulnerabilities": {
                            "info": 0,
                            "low": 0,
                            "moderate": 0,
                            "high": 0,
                            "critical": 0,
                            "total": 0,
                        }
                    }
                }
            )
        return verifier.RawExecution(0, 7, output)

    def _write_artifact(self, task: verifier.TaskSpec) -> None:
        assert task.artifact is not None
        if task.validator.startswith("junit-"):
            _write_junit(task.artifact, tests=2_017 if task.validator == "junit-backend" else 25)
            return
        if task.validator == "vitest-json":
            _write_json(
                task.artifact,
                {
                    "success": True,
                    "numTotalTests": 321,
                    "numPassedTests": 321,
                    "numFailedTests": 0,
                    "numPendingTests": 0,
                },
            )
            return
        if task.validator == "ruff-ratchet":
            _write_json(
                task.artifact,
                {
                    "kind": "klm-ruff-debt-ratchet",
                    "status": "passed",
                    "baselineFindingCount": 5_144,
                    "currentFindingCount": 5_140,
                    "newViolationCount": 0,
                    "resolvedViolationCount": 4,
                    "baselineDigest": "ruff-baseline-digest",
                },
            )
            return
        if task.validator == "visual-evidence":
            visual_dir = task.artifact.parent
            visual_dir.mkdir(parents=True, exist_ok=True)
            screenshots = []
            videos = []
            for index in range(16):
                evidence = visual_dir / f"screen-{index}.png"
                evidence.write_bytes(f"screen-{index}".encode())
                screenshots.append(
                    {
                        "path": evidence.relative_to(self.root).as_posix(),
                        "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                    }
                )
            for index in range(2):
                evidence = visual_dir / f"video-{index}.webm"
                evidence.write_bytes(f"video-{index}".encode())
                videos.append(
                    {
                        "path": evidence.relative_to(self.root).as_posix(),
                        "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                    }
                )
            _write_json(
                task.artifact,
                {
                    "kind": "klm-saas-v5-p5-visual-evidence",
                    "status": "passed",
                    "localOnly": True,
                    "profiles": [{"name": "desktop"}, {"name": "mobile"}],
                    "routes": [f"/app/{index}" for index in range(8)],
                    "screenshots": screenshots,
                    "videos": videos,
                    "runtimeFailures": {
                        "externalNetworkRequests": 0,
                        "consoleErrors": [],
                        "localRequestFailures": [],
                        "pageErrors": [],
                        "serverErrors": [],
                    },
                    "manifestDigest": "visual-manifest-digest",
                },
            )
            return
        if task.validator == "baseline":
            digest = self.after_digest if task.name == "baseline-after" else "source-digest"
            _write_json(
                task.artifact,
                {
                    "kind": "klm-saas-v5-baseline",
                    "sourceDigest": digest,
                    "sourceFileCount": 999,
                    "git": {
                        "head": "0123456789abcdef",
                        "branch": "codex/p5",
                        "dirty": True,
                    },
                    "secretScan": {"findingCount": 0},
                    "upstreams": {"allMatchLock": True},
                    "snapshotReady": True,
                },
            )
            return
        if task.validator == "local-stack-live":
            _write_json(
                task.artifact,
                {
                    "verifier": "verify_local_stack_live",
                    "status": "passed",
                    "digest": "stack-digest",
                    "environment": {
                        "externalNetworkRequests": 0,
                        "stackMutations": 0,
                        "secretValuesIncluded": False,
                    },
                    "results": {
                        "networks": {
                            "egressProbe": {
                                "status": "passed",
                                "defaultRoutePresent": False,
                                "connected": False,
                                "kernelRouteTableInspected": True,
                            }
                        }
                    },
                },
            )
            return
        if task.validator == "local-load-performance":
            _write_json(
                task.artifact,
                {
                    "kind": "klm-saas-v5-local-hot-endpoint-benchmark",
                    "status": "passed",
                    "localOnly": True,
                    "aggregate": {
                        "p95Ms": 42.0,
                        "thresholdP95Ms": 300.0,
                        "samples": 560,
                        "passed": True,
                    },
                    "endpoints": [
                        {
                            "path": "/health",
                            "p95Ms": 10.0,
                            "thresholdP95Ms": 300.0,
                            "passed": True,
                        }
                    ],
                    "profile": {"readOnlyMeasuredRequests": True},
                    "network": {"externalNetworkRequests": 0},
                    "claims": {
                        "localLoadProfileVerified": True,
                        "externalProviderVerified": False,
                        "productionVerified": False,
                    },
                },
            )
            return
        if task.validator == "conversation-simulation":
            _write_json(
                task.artifact,
                {
                    "kind": "klm-saas-v5-p5-conversation-simulation",
                    "status": "passed",
                    "conversationCount": self.simulation_count,
                    "digest": "simulation-digest",
                    "externalEffects": {
                        "offlineNetworkGuard": True,
                        "networkAttempts": 0,
                        "networkCallsExecuted": 0,
                        "providerCallsExecuted": 0,
                        "messagesSent": self.simulation_messages,
                        "chargesExecuted": 0,
                        "modelApiGate": "blocked",
                    },
                    "data": {
                        "classification": "synthetic_only",
                        "realDataRecordsRead": 0,
                        "realDataRecordsWritten": 0,
                    },
                },
            )
            return
        if task.validator == "sbom":
            _write_json(
                task.artifact,
                {
                    "bomFormat": "CycloneDX",
                    "specVersion": "1.6",
                    "components": [{"name": "klm", "version": "local"}],
                },
            )
            return
        if task.validator == "p4":
            _write_json(
                task.artifact,
                {
                    "kind": "klm-saas-v5-p4-verification",
                    "status": "passed",
                    "claims": {
                        "offlineP4Verified": True,
                        "externalProviderVerified": False,
                        "productionVerified": False,
                    },
                    "source": {"stableDuringVerification": True},
                    "gates": {"all": True},
                },
            )
            return
        raise AssertionError(f"Unexpected generated artifact validator: {task.validator}")


def _fixed_now() -> datetime:
    return datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def _fake_node(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_frontend_node_preflight_prefers_supported_project_toolchain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KLM_NODE_BIN", raising=False)
    monkeypatch.setattr(verifier.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(verifier.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(verifier.shutil, "which", lambda _name: "/usr/local/bin/node")
    bundled = _fake_node(verifier._bundled_node_candidate(tmp_path), "v24.18.0")

    runtime = verifier._resolve_frontend_node(tmp_path)
    environment = verifier._offline_environment(
        verifier.TaskSpec("frontend-build", "frontend-build", ("npm",), tmp_path),
        node_runtime=runtime,
    )

    assert runtime == {
        "path": bundled.resolve(),
        "version": "24.18.0",
        "source": "project-local",
        "supported": True,
    }
    assert environment["PATH"].split(os.pathsep, maxsplit=1)[0] == str(bundled.parent)
    assert environment["KLM_E2E_TRACE"] == "0"


def test_frontend_node_preflight_rejects_unsupported_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsupported = _fake_node(tmp_path / "node", "v24.14.1")
    monkeypatch.setenv("KLM_NODE_BIN", str(unsupported))
    monkeypatch.setattr(verifier.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(verifier.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(verifier.shutil, "which", lambda _name: None)

    with pytest.raises(verifier.VerificationError, match="Node >=24.18.0 <25"):
        verifier._resolve_frontend_node(tmp_path)


def test_plan_is_complete_safe_and_does_not_require_sibling_scripts(tmp_path: Path) -> None:
    plan = verifier.build_plan(root=tmp_path, evidence_dir=tmp_path / ".local/p5")
    contract = verifier.validate_plan_contract(plan)

    assert contract["passed"] is True
    assert set(contract["actualCategories"]) == verifier.REQUIRED_CATEGORIES
    assert not contract["unsafeCommands"]
    assert {task.name for task in plan} >= {
        "backend-pytest-full",
        "playwright-desktop-1440",
        "playwright-tablet-834",
        "playwright-mobile-393",
        "visual-evidence",
        "local-stack-live",
        "local-hot-endpoints",
        "conversation-simulation",
        "sbom",
        "p4",
        "native-architecture-gate",
        "npm-audit-offline",
    }
    stack = next(task for task in plan if task.name == "local-stack-live")
    assert "up" not in stack.command
    assert "pull" not in stack.command
    assert "stop" not in stack.command


def test_junit_validator_rejects_skipped_tests(tmp_path: Path) -> None:
    report = tmp_path / "backend.xml"
    _write_junit(report, tests=12, skipped=1)

    with pytest.raises(verifier.VerificationError, match="skipped tests"):
        verifier._parse_junit(report)


def test_final_selection_cannot_omit_tasks_and_quick_has_safety_envelope(
    tmp_path: Path,
) -> None:
    plan = verifier.build_plan(root=tmp_path, evidence_dir=tmp_path / ".local/p5")

    with pytest.raises(verifier.VerificationError, match="Final mode"):
        verifier.select_plan(plan, mode="final", selectors=["ruff"])
    with pytest.raises(verifier.VerificationError, match="requires"):
        verifier.select_plan(plan, mode="quick", selectors=[])
    with pytest.raises(verifier.VerificationError, match="Unknown"):
        verifier.select_plan(plan, mode="quick", selectors=["not-a-suite"])

    selected = verifier.select_plan(plan, mode="quick", selectors=["ruff"])
    assert [task.name for task in selected] == [
        "baseline-before",
        "ruff-full",
        "baseline-after",
    ]


def test_contract_fails_closed_when_required_category_is_missing(tmp_path: Path) -> None:
    plan = verifier.build_plan(root=tmp_path, evidence_dir=tmp_path / ".local/p5")
    incomplete = tuple(task for task in plan if task.category != "sbom")

    contract = verifier.validate_plan_contract(incomplete)

    assert contract["passed"] is False
    assert contract["missingCategories"] == ["sbom"]


def test_run_task_redacts_command_and_log_and_hashes_exact_log(tmp_path: Path) -> None:
    secret = "".join(("sk", "-", "abcdefghijklmnopqrstuvwxyz123456"))
    task = verifier.TaskSpec(
        name="redaction",
        category="ruff",
        command=("tool", "--api-key", secret),
        cwd=tmp_path,
    )

    class SecretExecutor:
        def execute(
            self,
            task: verifier.TaskSpec,
            environment: Mapping[str, str],
        ) -> verifier.RawExecution:
            return verifier.RawExecution(0, 4, f"token={secret}\n")

    result = verifier.run_task(
        task,
        root=tmp_path,
        evidence_dir=tmp_path / ".local/p5",
        executor=SecretExecutor(),
    )
    log = tmp_path / result["log"]["path"]

    assert result["status"] == "passed"
    assert result["command"][-1] == "[REDACTED]"
    assert secret not in log.read_text(encoding="utf-8")
    assert result["log"]["sha256"] == hashlib.sha256(log.read_bytes()).hexdigest()


def test_missing_executable_is_a_normal_fail_closed_result(tmp_path: Path) -> None:
    task = verifier.TaskSpec(
        name="missing",
        category="sbom",
        command=(str(tmp_path / "does-not-exist"),),
        cwd=tmp_path,
    )

    result = verifier.run_task(
        task,
        root=tmp_path,
        evidence_dir=tmp_path / ".local/p5",
    )

    assert result["status"] == "failed"
    assert result["exitCode"] == 127
    assert result["executionErrorType"] == "FileNotFoundError"


def test_semantic_simulation_gate_rejects_too_few_or_external_effects(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / ".local/p5"
    task = next(
        task
        for task in verifier.build_plan(root=tmp_path, evidence_dir=evidence)
        if task.name == "conversation-simulation"
    )

    too_few = verifier.run_task(
        task,
        root=tmp_path,
        evidence_dir=evidence,
        executor=FakeExecutor(tmp_path, simulation_count=999),
    )
    with_effect = verifier.run_task(
        task,
        root=tmp_path,
        evidence_dir=evidence,
        executor=FakeExecutor(tmp_path, simulation_messages=1),
    )

    assert too_few["status"] == "failed"
    assert "fewer than 1000" in too_few["validation"]["reason"]
    assert with_effect["status"] == "failed"
    assert "messagesSent" in with_effect["validation"]["reason"]


def test_complete_fake_run_writes_honest_reproducible_final_manifest(
    tmp_path: Path,
) -> None:
    _write_oss_contracts(tmp_path)
    evidence = tmp_path / ".local/qa-evidence/saas-v5-p5"
    output = tmp_path / "app/data/saas_v5/p5-verification.json"
    executor = FakeExecutor(tmp_path)

    manifest, exit_code = verifier.verify(
        root=tmp_path,
        evidence_dir=evidence,
        output_path=output,
        executor=executor,
        now=_fixed_now,
    )

    assert exit_code == 0
    assert manifest["status"] == "passed"
    assert all(manifest["gates"].values())
    assert manifest["claims"] == {
        "localP5Verified": True,
        "localSaasApprovalReadyV5": True,
        "nativeArchitectureGatePassed": True,
        "globalBehavioralSuperiorityClaimAllowed": False,
        "externalProviderVerified": False,
        "productionVerified": False,
        "awsVerified": False,
    }
    assert manifest["source"]["digestBefore"] == "source-digest"
    assert manifest["source"]["digestAfter"] == "source-digest"
    assert manifest["source"]["stableDuringVerification"] is True
    assert manifest["selection"]["omittedTaskNames"] == []
    assert manifest["generatedHarnesses"]["playwright"]["projects"] == [
        "desktop-1440",
        "tablet-834",
        "mobile-393",
    ]
    assert output.is_file()
    without_digest = dict(manifest)
    digest = without_digest.pop("manifestDigest")
    assert digest == verifier._digest(without_digest)
    assert all(task["log"]["sha256"] for task in manifest["tasks"])


def test_source_drift_fails_final_even_when_every_subprocess_passes(
    tmp_path: Path,
) -> None:
    _write_oss_contracts(tmp_path)
    manifest, exit_code = verifier.verify(
        root=tmp_path,
        evidence_dir=tmp_path / ".local/p5",
        output_path=tmp_path / "app/data/saas_v5/p5-verification.json",
        executor=FakeExecutor(tmp_path, after_digest="changed-source"),
        now=_fixed_now,
    )

    assert exit_code == 1
    assert manifest["status"] == "failed"
    assert manifest["gates"]["sourceStable"] is False
    assert manifest["claims"]["localP5Verified"] is False


def test_fail_fast_still_captures_after_snapshot_and_marks_omitted_work(
    tmp_path: Path,
) -> None:
    _write_oss_contracts(tmp_path)
    executor = FakeExecutor(tmp_path, fail_task="ruff-full")

    manifest, exit_code = verifier.verify(
        root=tmp_path,
        evidence_dir=tmp_path / ".local/p5",
        output_path=tmp_path / "app/data/saas_v5/p5-verification.json",
        executor=executor,
        fail_fast=True,
        now=_fixed_now,
    )

    assert exit_code == 1
    assert executor.calls == [
        "baseline-before",
        "backend-pytest-full",
        "ruff-full",
        "baseline-after",
    ]
    assert manifest["source"]["stableDuringVerification"] is True
    assert any(task["status"] == "skipped_fail_fast" for task in manifest["tasks"])
    assert manifest["gates"]["allRequiredTasksPassed"] is False


def test_quick_run_is_partial_and_cannot_overwrite_canonical_manifest(
    tmp_path: Path,
) -> None:
    _write_oss_contracts(tmp_path)
    canonical = tmp_path / "app/data/saas_v5/p5-verification.json"

    with pytest.raises(verifier.VerificationError, match="cannot overwrite"):
        verifier.verify(
            mode="quick",
            selectors=["ruff"],
            root=tmp_path,
            evidence_dir=tmp_path / ".local/p5",
            output_path=canonical,
            executor=FakeExecutor(tmp_path),
        )

    manifest, exit_code = verifier.verify(
        mode="quick",
        selectors=["ruff"],
        root=tmp_path,
        evidence_dir=tmp_path / ".local/p5",
        output_path=tmp_path / ".local/p5/quick.json",
        executor=FakeExecutor(tmp_path),
        now=_fixed_now,
    )

    assert exit_code == 0
    assert manifest["status"] == "partial_passed"
    assert manifest["claims"]["localP5Verified"] is False
    assert manifest["claims"]["localSaasApprovalReadyV5"] is False
    assert manifest["selection"]["selectedTaskNames"] == [
        "baseline-before",
        "ruff-full",
        "baseline-after",
    ]
    assert manifest["gates"]["allMandatoryTasksSelected"] is False


def test_npm_audit_accepts_only_reviewed_patched_react_router_finding(
    tmp_path: Path,
) -> None:
    _write_react_router_lock(tmp_path)

    result = verifier._validate_npm_audit(
        _react_router_audit_payload(),
        root=tmp_path,
        today=date(2026, 7, 31),
    )

    assert result["rawHigh"] == 2
    assert result["high"] == 0
    assert result["critical"] == 0
    assert result["reviewedExceptions"] == [
        {
            "advisory": verifier.REACT_ROUTER_RSC_ADVISORY,
            "packages": ["react-router", "react-router-dom"],
            "installedVersion": "7.18.2",
            "upstreamAdvisory": verifier.REACT_ROUTER_RSC_UPSTREAM_URL,
            "upstreamBackport": verifier.REACT_ROUTER_RSC_BACKPORT_URL,
            "upstreamRelease": verifier.REACT_ROUTER_RSC_RELEASE_URL,
            "reviewBefore": "2026-08-15",
            "rscSources": [],
        }
    ]


def test_npm_audit_rejects_unreviewed_additional_high_finding(
    tmp_path: Path,
) -> None:
    _write_react_router_lock(tmp_path)

    with pytest.raises(verifier.VerificationError, match="high or critical"):
        verifier._validate_npm_audit(
            _react_router_audit_payload(high=3),
            root=tmp_path,
            today=date(2026, 7, 31),
        )


def test_npm_audit_rejects_second_advisory_on_reviewed_package(
    tmp_path: Path,
) -> None:
    _write_react_router_lock(tmp_path)
    payload = json.loads(_react_router_audit_payload())
    payload["vulnerabilities"]["react-router"]["via"].append(
        {
            "name": "react-router",
            "dependency": "react-router",
            "url": "https://github.com/advisories/GHSA-unreviewed",
            "severity": "high",
        }
    )

    with pytest.raises(verifier.VerificationError, match="reviewed advisory"):
        verifier._validate_npm_audit(
            json.dumps(payload),
            root=tmp_path,
            today=date(2026, 7, 31),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "different-package"),
        ("dependency", "different-package"),
        ("url", "https://github.com/advisories/GHSA-different"),
        ("severity", "moderate"),
    ],
)
def test_npm_audit_rejects_mismatched_advisory_identity(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    _write_react_router_lock(tmp_path)
    payload = json.loads(_react_router_audit_payload())
    payload["vulnerabilities"]["react-router"]["via"][0][field] = value

    with pytest.raises(verifier.VerificationError, match="dependency chain"):
        verifier._validate_npm_audit(
            json.dumps(payload),
            root=tmp_path,
            today=date(2026, 7, 31),
        )


def test_npm_audit_rejects_react_router_exception_after_review_deadline(
    tmp_path: Path,
) -> None:
    _write_react_router_lock(tmp_path)

    with pytest.raises(verifier.VerificationError, match="expired"):
        verifier._validate_npm_audit(
            _react_router_audit_payload(),
            root=tmp_path,
            today=date(2026, 8, 15),
        )


def test_npm_audit_accepts_exception_on_day_before_review_deadline(
    tmp_path: Path,
) -> None:
    _write_react_router_lock(tmp_path)

    result = verifier._validate_npm_audit(
        _react_router_audit_payload(),
        root=tmp_path,
        today=date(2026, 8, 14),
    )

    assert result["high"] == 0


def test_npm_audit_rejects_unreviewed_react_router_version(tmp_path: Path) -> None:
    _write_react_router_lock(tmp_path, router_version="7.18.3")

    with pytest.raises(verifier.VerificationError, match="reviewed patched version"):
        verifier._validate_npm_audit(
            _react_router_audit_payload(),
            root=tmp_path,
            today=date(2026, 7, 31),
        )


@pytest.mark.parametrize(
    "lock_kwargs",
    [
        {"root_version": "7.18.1"},
        {"router_version": "7.18.1"},
        {"router_dom_version": "7.18.1"},
        {"router_dom_edge": "7.18.1"},
    ],
)
def test_npm_audit_rejects_unreviewed_lock_graph(
    tmp_path: Path,
    lock_kwargs: dict[str, str],
) -> None:
    _write_react_router_lock(tmp_path, **lock_kwargs)

    with pytest.raises(verifier.VerificationError, match="reviewed patched version"):
        verifier._validate_npm_audit(
            _react_router_audit_payload(),
            root=tmp_path,
            today=date(2026, 7, 31),
        )


def test_npm_audit_rejects_exception_when_unstable_rsc_api_is_used(
    tmp_path: Path,
) -> None:
    _write_react_router_lock(tmp_path)
    source = tmp_path / "frontend/src/rsc.mts"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        'import { unstable_routeRSCServerRequest } from "react-router";\n',
        encoding="utf-8",
    )

    with pytest.raises(verifier.VerificationError, match="unstable RSC"):
        verifier._validate_npm_audit(
            _react_router_audit_payload(),
            root=tmp_path,
            today=date(2026, 7, 31),
        )


def test_run_task_accepts_exit_one_only_for_reviewed_npm_audit_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_react_router_lock(tmp_path)
    monkeypatch.setattr(
        verifier,
        "REACT_ROUTER_RSC_EXCEPTION_REVIEW_BEFORE",
        date(2099, 1, 1),
    )

    result = verifier.run_task(
        _npm_audit_task(tmp_path),
        root=tmp_path,
        evidence_dir=tmp_path / ".local/p5",
        executor=FixedRawExecutor(
            exit_code=1,
            output=_react_router_audit_payload(),
        ),
    )

    assert result["status"] == "passed"
    assert result["exitCode"] == 1
    assert len(result["validation"]["reviewedExceptions"]) == 1


@pytest.mark.parametrize(
    "output",
    [
        json.dumps(
            {
                "metadata": {
                    "vulnerabilities": {
                        "high": 0,
                        "critical": 0,
                        "total": 0,
                    }
                }
            }
        ),
        "not-json",
        _react_router_audit_payload(high=3),
    ],
)
def test_run_task_rejects_exit_one_without_exact_reviewed_audit_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: str,
) -> None:
    _write_react_router_lock(tmp_path)
    monkeypatch.setattr(
        verifier,
        "REACT_ROUTER_RSC_EXCEPTION_REVIEW_BEFORE",
        date(2099, 1, 1),
    )

    result = verifier.run_task(
        _npm_audit_task(tmp_path),
        root=tmp_path,
        evidence_dir=tmp_path / ".local/p5",
        executor=FixedRawExecutor(exit_code=1, output=output),
    )

    assert result["status"] == "failed"


def test_run_task_rejects_exit_two_even_for_reviewed_audit_payload(
    tmp_path: Path,
) -> None:
    _write_react_router_lock(tmp_path)

    result = verifier.run_task(
        _npm_audit_task(tmp_path),
        root=tmp_path,
        evidence_dir=tmp_path / ".local/p5",
        executor=FixedRawExecutor(
            exit_code=2,
            output=_react_router_audit_payload(),
        ),
    )

    assert result["status"] == "failed"
    assert result["validation"]["reason"] == "non_zero_exit"


def test_run_task_accepts_clean_npm_audit_exit_zero(tmp_path: Path) -> None:
    output = json.dumps(
        {
            "metadata": {
                "vulnerabilities": {
                    "high": 0,
                    "critical": 0,
                    "total": 0,
                }
            }
        }
    )

    result = verifier.run_task(
        _npm_audit_task(tmp_path),
        root=tmp_path,
        evidence_dir=tmp_path / ".local/p5",
        executor=FixedRawExecutor(exit_code=0, output=output),
    )

    assert result["status"] == "passed"
    assert result["validation"]["reviewedExceptions"] == []


def test_run_task_never_accepts_nonzero_for_a_non_audit_task(tmp_path: Path) -> None:
    task = verifier.TaskSpec(
        name="regular-task",
        category="ruff",
        command=("tool",),
        cwd=tmp_path,
    )

    result = verifier.run_task(
        task,
        root=tmp_path,
        evidence_dir=tmp_path / ".local/p5",
        executor=FixedRawExecutor(
            exit_code=1,
            output=_react_router_audit_payload(),
        ),
    )

    assert result["status"] == "failed"
    assert result["validation"]["reason"] == "non_zero_exit"
