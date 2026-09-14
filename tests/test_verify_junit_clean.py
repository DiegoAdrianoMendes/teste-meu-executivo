from pathlib import Path

import pytest

from scripts.verify_junit_clean import JUnitVerificationError, summarize


def _write_junit(path: Path, *, tests: int, skipped: int = 0) -> None:
    path.write_text(
        f'<testsuites tests="{tests}" failures="0" errors="0" skipped="{skipped}">'
        f'<testsuite tests="{tests}" failures="0" errors="0" skipped="{skipped}" />'
        "</testsuites>",
        encoding="utf-8",
    )


def test_summarize_accepts_only_a_nonempty_zero_skip_run(tmp_path: Path) -> None:
    report = tmp_path / "junit.xml"
    _write_junit(report, tests=27)

    assert summarize(report) == {
        "tests": 27,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "passed": 27,
    }


def test_summarize_rejects_skipped_tests(tmp_path: Path) -> None:
    report = tmp_path / "junit.xml"
    _write_junit(report, tests=27, skipped=1)

    with pytest.raises(JUnitVerificationError, match="1 skipped"):
        summarize(report)
