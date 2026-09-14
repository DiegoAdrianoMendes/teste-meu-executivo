#!/usr/bin/env python3
"""Fail unless a JUnit artifact proves a non-empty run with zero omissions."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


class JUnitVerificationError(RuntimeError):
    """Raised when a JUnit report cannot support a clean QA claim."""


def summarize(path: Path) -> dict[str, int]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise JUnitVerificationError(f"Invalid JUnit artifact: {path}") from error

    suites = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
    if root.tag == "testsuites" and root.attrib.get("tests") is not None:
        values = root.attrib
    else:
        values = {
            key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")
        }
    summary = {
        key: int(values.get(key, 0))
        for key in ("tests", "failures", "errors", "skipped")
    }
    if summary["tests"] < 1:
        raise JUnitVerificationError("JUnit artifact contains no tests.")
    if summary["failures"] or summary["errors"]:
        raise JUnitVerificationError(
            "JUnit artifact contains failures or collection/runtime errors."
        )
    if summary["skipped"]:
        raise JUnitVerificationError(
            f"JUnit artifact contains {summary['skipped']} skipped test(s)."
        )
    summary["passed"] = summary["tests"]
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit", type=Path)
    arguments = parser.parse_args()
    try:
        summary = summarize(arguments.junit)
    except JUnitVerificationError as error:
        print(f"JUNIT_NOT_CLEAN: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "passed", **summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
