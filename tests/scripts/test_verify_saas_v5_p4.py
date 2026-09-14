from __future__ import annotations

from pathlib import Path

import pytest

from scripts import verify_saas_v5_p4 as verifier


def _baseline(digest: str) -> dict[str, object]:
    return {
        "sourceDigest": digest,
        "sourceFileCount": 100,
        "git": {"head": "a" * 40, "dirty": True},
    }


def _fake_suites(root: Path) -> tuple[verifier.Suite, ...]:
    return tuple(
        verifier.Suite(
            name=f"suite-{index}",
            category=category,
            command=("true",),
            cwd=root,
        )
        for index, category in enumerate(sorted(verifier.REQUIRED_CATEGORIES), start=1)
    )


def _passed_result(suite: verifier.Suite) -> dict[str, object]:
    return {
        "name": suite.name,
        "category": suite.category,
        "required": suite.required,
        "status": "passed",
        "exitCode": 0,
        "durationMs": 1,
        "cwd": ".",
        "command": list(suite.command),
        "output": {"path": ".local/pass.log", "sha256": "b" * 64},
    }


def _passed_attribution() -> dict[str, object]:
    return {
        "requiredCoverage": 1.0,
        "observedCoverage": 1.0,
        "calls": {"coverage": 1.0, "passed": True},
        "telemetry": {"passed": True},
        "passed": True,
    }


def _passed_stack() -> dict[str, object]:
    return {
        "staticContract": {"status": "passed"},
        "liveRuntime": {
            "status": "blocked",
            "reason": "containers_not_built_or_started_by_p4_verifier",
        },
    }


def test_attribution_audit_finds_every_unattributed_callsite(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "calls.py").write_text(
        """
def good(provider, messages, attribution):
    provider.complete(
        messages,
        workspace_id='workspace',
        agent_name='agent',
        attribution=attribution,
    )
    ModelRequest(
        messages=messages,
        workspace_id='workspace',
        agent_name='agent',
        attribution=attribution,
    )

def bad(provider, messages):
    provider.complete(messages, workspace_id='workspace', agent_name='agent')
    ModelRequest(messages=messages, workspace_id='workspace', agent_name='agent')
""".strip(),
        encoding="utf-8",
    )

    result = verifier.audit_model_calls(tmp_path)

    assert result["totalCallsites"] == 4
    assert result["compliantCallsites"] == 2
    assert result["coverage"] == 0.5
    assert result["passed"] is False
    assert {item["line"] for item in result["missing"]} == {16, 17}
    assert all(item["missingKeywords"] == ["attribution"] for item in result["missing"])


def test_attribution_audit_does_not_double_count_provider_adapter_compatibility(
    tmp_path: Path,
) -> None:
    providers = tmp_path / "app/ai/providers"
    services = tmp_path / "app/services"
    providers.mkdir(parents=True)
    services.mkdir(parents=True)
    (providers / "adapter.py").write_text(
        "async def generate(provider, request):\n"
        "    return await provider.complete(request.messages)\n",
        encoding="utf-8",
    )
    (services / "caller.py").write_text(
        "async def run(provider, messages, attribution):\n"
        "    return await provider.complete(\n"
        "        messages, workspace_id='w', agent_name='a', attribution=attribution\n"
        "    )\n",
        encoding="utf-8",
    )

    result = verifier.audit_model_calls(tmp_path)

    assert result["totalCallsites"] == 1
    assert result["coverage"] == 1.0
    assert result["passed"] is True


def test_stack_contract_never_claims_live_docker_from_static_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = tmp_path / ".local/stack.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "LOCAL_STACK_OK: 12 services, offline contract valid\n"
        "IMAGE_CACHE_PRESENT: 4/9\n"
        "IMAGE_BUILD_SKIPPED: pinned images are not cached; no pull was attempted\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    suites = [
        {
            "category": "stack-contract",
            "status": "passed",
            "output": {"path": ".local/stack.log"},
        }
    ]

    result = verifier.stack_snapshot(suites)

    assert result["staticContract"]["status"] == "passed"
    assert result["staticContract"]["imageCachePresent"] == 4
    assert result["liveRuntime"]["status"] == "blocked"
    assert result["liveRuntime"]["containersStartedByVerifier"] is False
    assert result["liveRuntime"]["networkPullAttempted"] is False


def test_verify_passes_offline_gates_but_keeps_live_and_production_unverified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suites = _fake_suites(tmp_path)
    captures = iter((_baseline("c" * 64), _baseline("c" * 64)))
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    monkeypatch.setattr(verifier, "SUITES", suites)
    monkeypatch.setattr(verifier, "capture", lambda _root: next(captures))
    monkeypatch.setattr(verifier, "attribution_snapshot", lambda _root: _passed_attribution())
    monkeypatch.setattr(verifier, "_run_suite", _passed_result)
    monkeypatch.setattr(
        verifier,
        "_read_benchmark",
        lambda _path: {"status": "passed", "path": "benchmark.json", "result": {}},
    )
    monkeypatch.setattr(verifier, "stack_snapshot", lambda _results: _passed_stack())

    manifest, exit_code = verifier.verify(manifest_path=tmp_path / "p4.json")

    assert exit_code == 0
    assert manifest["status"] == "passed"
    assert manifest["claims"]["offlineP4Verified"] is True
    assert manifest["claims"]["dockerLiveRuntimeVerified"] is False
    assert manifest["claims"]["productionVerified"] is False
    assert manifest["stack"]["liveRuntime"]["status"] == "blocked"


def test_verify_fails_closed_on_source_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suites = _fake_suites(tmp_path)
    captures = iter((_baseline("d" * 64), _baseline("e" * 64)))
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    monkeypatch.setattr(verifier, "SUITES", suites)
    monkeypatch.setattr(verifier, "capture", lambda _root: next(captures))
    monkeypatch.setattr(verifier, "attribution_snapshot", lambda _root: _passed_attribution())
    monkeypatch.setattr(verifier, "_run_suite", _passed_result)
    monkeypatch.setattr(
        verifier,
        "_read_benchmark",
        lambda _path: {"status": "passed", "path": "benchmark.json", "result": {}},
    )
    monkeypatch.setattr(verifier, "stack_snapshot", lambda _results: _passed_stack())

    manifest, exit_code = verifier.verify(manifest_path=tmp_path / "p4.json")

    assert exit_code == 1
    assert manifest["status"] == "failed"
    assert manifest["gates"]["sourceStable"] is False


def test_real_suite_contract_cannot_omit_required_p4_proofs() -> None:
    assert {suite.category for suite in verifier.SUITES if suite.required} == (
        verifier.REQUIRED_CATEGORIES
    )
    commands = {suite.category: suite.command for suite in verifier.SUITES}
    assert "tests/ai/test_limits.py" in commands["budget-race-and-attribution-runtime"]
    assert "tests/services/test_knowledge_service.py" in commands["rag-isolation"]
    assert any(
        "test_restart_after_request_checkpoint_never_redispatches" in item
        for item in commands["restart-and-idempotency"]
    )
    assert commands["stack-contract"] == (
        ".venv/bin/python",
        "scripts/verify_local_stack.py",
    )
