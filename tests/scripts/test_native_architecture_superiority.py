from __future__ import annotations

import json
from pathlib import Path

from scripts import verify_native_architecture_superiority as verifier

ROOT = Path(__file__).resolve().parents[2]


def test_native_architecture_report_is_complete_and_honest() -> None:
    report = verifier.build_report(ROOT)

    assert report["schemaVersion"] == 2
    assert report["globalBehavioralSuperiorityClaimAllowed"] is False
    assert report["functionMaturity"]["native_shadow"] > 0
    assert report["functionMaturity"]["native_verified"] == 0
    assert tuple(criterion["id"] for criterion in report["criteria"]) == (
        verifier.REQUIRED_CRITERION_IDS
    )
    source_bound = next(
        criterion
        for criterion in report["criteria"]
        if criterion["id"] == "source_bound_release_verification"
    )
    assert all(
        criterion["passed"]
        for criterion in report["criteria"]
        if criterion["id"] != "source_bound_release_verification"
    )
    assert report["architectureGatePassed"] is source_bound["passed"]
    assert report["structuralPreflightPassed"] is True
    assert report["releaseProofBinding"]["current"] is source_bound["passed"]
    assert len(report["sourceBinding"]["sha256"]) == 64
    assert report["sourceBinding"]["fileCount"] == len(report["sourceBinding"]["files"])
    assert ".local/qa-evidence/oss-native/native_architecture_superiority.json" not in {
        item["path"] for item in report["sourceBinding"]["files"]
    }


def test_generated_scorecard_matches_the_bound_source(tmp_path: Path) -> None:
    report = verifier.build_report(ROOT)
    output = tmp_path / "native_architecture_superiority.json"
    verifier._write_atomic(output, report)
    generated = json.loads(
        output.read_text(encoding="utf-8")
    )

    assert generated == report


def test_report_digest_changes_when_a_criterion_changes() -> None:
    report = verifier.build_report(ROOT)
    changed = {**report, "architectureGatePassed": not report["architectureGatePassed"]}
    changed.pop("reportSha256")

    assert verifier.hashlib.sha256(verifier._canonical_json(changed)).hexdigest() != report[
        "reportSha256"
    ]
