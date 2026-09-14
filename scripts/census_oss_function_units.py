#!/usr/bin/env python3
"""Generate or check the deterministic V5 OSS FunctionUnit lock."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.oss_census.census import CensusError, build_census, render_census  # noqa: E402
from scripts.oss_census.common import ExtractionError  # noqa: E402
from scripts.oss_census.dispositions import DispositionError  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="KLM repository root (default: inferred from this script)",
    )
    parser.add_argument("--upstreams-root", type=Path, help="Materialized read-only upstream root")
    parser.add_argument("--upstream-lock", type=Path, help="Override oss-upstreams.lock.json")
    parser.add_argument("--dispositions", type=Path, help="Override disposition policy JSON")
    parser.add_argument("--output", type=Path, help="Override generated lock path")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when the committed lock differs; never write output",
    )
    parser.add_argument(
        "--skip-pin-verification",
        action="store_true",
        help="Skip Git/sentinel/license verification (intended only for synthetic extractor tests)",
    )
    return parser


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    repo_root = arguments.repo_root.resolve()
    output = arguments.output or repo_root / "docs" / "oss-function-units.lock.json"
    try:
        document = build_census(
            repo_root=repo_root,
            upstreams_root=arguments.upstreams_root,
            upstream_lock_path=arguments.upstream_lock,
            disposition_path=arguments.dispositions,
            verify_pins=not arguments.skip_pin_verification,
        )
    except (CensusError, DispositionError, ExtractionError, ValueError) as exc:
        print(f"OSS census failed: {exc}", file=sys.stderr)
        return 2

    rendered = render_census(document)
    if arguments.check:
        try:
            current = output.read_text(encoding="utf-8")
        except FileNotFoundError:
            print(f"OSS census lock is missing: {output}", file=sys.stderr)
            return 1
        if current != rendered:
            print(
                "OSS census lock is stale. Run with CPython 3.12.13: "
                "python scripts/census_oss_function_units.py",
                file=sys.stderr,
            )
            return 1
        print(
            f"OSS census lock verified: {document['coverage']['denominator']} units, "
            f"digest {document['reproducibility']['payloadSha256']}"
        )
        return 0

    _atomic_write(output, rendered)
    coverage = document["coverage"]
    print(
        f"Wrote {output}: {coverage['denominator']} units, "
        f"{coverage['sourceFilesExamined']} examined source files, "
        f"digest {document['reproducibility']['payloadSha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
