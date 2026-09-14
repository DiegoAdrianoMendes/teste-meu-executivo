"""Canonical Git-object source reader and worktree integrity verifier."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from scripts.oss_census.common import ExtractionError, sha256_bytes


@dataclass(frozen=True)
class GitTreeEntry:
    mode: str
    object_type: str
    oid: str
    path: str


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Translate the limited pathlib-style globs used by census scopes."""

    output = ["^"]
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    output.append("(?:.*/)?")
                    index += 1
                else:
                    output.append(".*")
                continue
            output.append("[^/]*")
        elif char == "?":
            output.append("[^/]")
        else:
            output.append(re.escape(char))
        index += 1
    output.append("$")
    return re.compile("".join(output))


def matches_glob(path: str, pattern: str) -> bool:
    return _glob_regex(pattern).fullmatch(path) is not None


class CanonicalGitSource:
    """Verify one pinned checkout and expose only bytes from its commit tree."""

    def __init__(self, root: Path, pin: dict[str, Any]):
        self.root = root.resolve()
        self.pin = pin
        self.name = str(pin["name"])
        self.commit = str(pin["commit"])
        self.entries: dict[str, GitTreeEntry] = {}
        self.receipt: dict[str, Any] = {}

    @property
    def _environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "LC_ALL": "C",
        }

    def _run(
        self,
        *arguments: str,
        check: bool = True,
        text: bool = True,
    ) -> subprocess.CompletedProcess[Any]:
        result = subprocess.run(
            ["git", "-C", str(self.root), *arguments],
            check=False,
            capture_output=True,
            text=text,
            env=self._environment,
        )
        if check and result.returncode != 0:
            stderr = result.stderr if text else result.stderr.decode("utf-8", "replace")
            stdout = result.stdout if text else result.stdout.decode("utf-8", "replace")
            detail = (stderr or stdout).strip()
            raise ExtractionError(
                f"Git verification failed for {self.name} ({' '.join(arguments)}): {detail}"
            )
        return result

    def _verify_identity(self) -> dict[str, Any]:
        if not self.root.is_dir():
            raise ExtractionError(f"Pinned source tree is not materialized: {self.root}")
        if self._run("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            raise ExtractionError(f"Pinned source is not a Git worktree: {self.root}")

        origin = self._run("remote", "get-url", "origin").stdout.strip()
        if origin != self.pin["repository"]:
            raise ExtractionError(
                f"Origin mismatch for {self.name}: expected {self.pin['repository']}, found {origin}"
            )

        ref = str(self.pin["ref"])
        ref_name = f"refs/tags/{ref}"
        ref_object = self._run("rev-parse", ref_name).stdout.strip()
        ref_type = self._run("cat-file", "-t", ref_name).stdout.strip()
        ref_commit = self._run("rev-parse", f"{ref_name}^{{commit}}").stdout.strip()
        head = self._run("rev-parse", "HEAD").stdout.strip()
        if ref_object != self.pin["refObject"] or ref_type != self.pin["refObjectType"]:
            raise ExtractionError(f"Pinned ref object mismatch for {self.name}:{ref}")
        if ref_commit != self.commit or head != self.commit:
            raise ExtractionError(
                f"Commit mismatch for {self.name}: ref={ref_commit}, HEAD={head}, expected={self.commit}"
            )
        if self._run("symbolic-ref", "--quiet", "HEAD", check=False).returncode == 0:
            raise ExtractionError(f"Pinned source HEAD must be detached: {self.name}")

        promisor = self._run(
            "config", "--bool", "--get", "remote.origin.promisor", check=False
        ).stdout.strip()
        partial_filter = self._run(
            "config", "--get", "remote.origin.partialclonefilter", check=False
        ).stdout.strip()
        if promisor != "true" or partial_filter != "blob:none":
            raise ExtractionError(
                f"Pinned source {self.name} must retain the locked blob:none promisor configuration"
            )

        tree = self._run("rev-parse", f"{self.commit}^{{tree}}").stdout.strip()
        return {
            "origin": origin,
            "ref": ref,
            "refObject": ref_object,
            "refObjectType": ref_type,
            "commit": self.commit,
            "tree": tree,
            "headDetached": True,
            "partialClone": {"promisor": True, "filter": "blob:none"},
        }

    def _read_tree(self) -> None:
        raw = self._run(
            "ls-tree", "-r", "-z", "--full-tree", self.commit, text=False
        ).stdout
        entries: dict[str, GitTreeEntry] = {}
        for record in raw.split(b"\0"):
            if not record:
                continue
            metadata, separator, raw_path = record.partition(b"\t")
            if not separator:
                raise ExtractionError(f"Malformed Git tree record for {self.name}")
            try:
                mode, object_type, oid = metadata.decode("ascii").split(" ")
                path = raw_path.decode("utf-8", errors="strict")
            except (UnicodeDecodeError, ValueError) as exc:
                raise ExtractionError(f"Malformed Git tree metadata for {self.name}") from exc
            if path in entries:
                raise ExtractionError(f"Duplicate path in Git tree for {self.name}: {path}")
            entries[path] = GitTreeEntry(mode, object_type, oid, path)
        self.entries = entries

    @staticmethod
    def _under(path: str, prefix: str) -> bool:
        normalized = prefix.rstrip("/")
        return path == normalized or path.startswith(normalized + "/")

    def _verify_checkout(self) -> dict[str, Any]:
        # Census extraction never reads worktree bytes: ``materialize`` selects
        # objects from the pinned commit into a fresh temporary directory.  A
        # full ``git status --untracked-files=all``/``git clean -ndx`` scan is
        # therefore neither an integrity control nor a bounded operation for
        # the large, partial upstream clones.  Identity, tree OIDs, sparse
        # exclusions and every selected blob are verified below; unrelated
        # worktree caches cannot enter the denominator.

        excluded = tuple(self.pin["checkout"].get("excludePaths", []))
        expected_mode = "sparse-excluding-proprietary" if excluded else "full"
        if self.pin["checkout"].get("mode") != expected_mode:
            raise ExtractionError(
                f"Checkout mode mismatch for {self.name}: expected {expected_mode}, "
                f"found {self.pin['checkout'].get('mode')}"
            )
        sparse_enabled = self._run(
            "config", "--bool", "--get", "core.sparseCheckout", check=False
        ).stdout.strip() == "true"
        expected_sparse = ["/*", *(f"!/{path}/" for path in excluded)]
        if excluded:
            if not sparse_enabled:
                raise ExtractionError(f"Sparse checkout is required for {self.name}")
            actual_sparse = [
                line
                for line in self._run("sparse-checkout", "list").stdout.splitlines()
                if line.strip()
            ]
            if actual_sparse != expected_sparse:
                raise ExtractionError(
                    f"Sparse checkout patterns differ for {self.name}: {actual_sparse}"
                )
        elif sparse_enabled:
            raise ExtractionError(f"Full checkout required for {self.name}, but sparse mode is on")

        skipped = []
        for line in self._run("ls-files", "-t").stdout.splitlines():
            if line.startswith("S "):
                skipped.append(line[2:])
        unexpected_skips = [
            path for path in skipped if not any(self._under(path, item) for item in excluded)
        ]
        if unexpected_skips:
            raise ExtractionError(
                f"Unexpected skip-worktree paths for {self.name}: {unexpected_skips[:5]}"
            )
        for prefix in excluded:
            tree_paths = [path for path in self.entries if self._under(path, prefix)]
            if not tree_paths:
                raise ExtractionError(
                    f"Locked proprietary exclusion is absent from {self.name} tree: {prefix}"
                )
            if (self.root / prefix).exists() or (self.root / prefix).is_symlink():
                raise ExtractionError(
                    f"Excluded proprietary path is materialized for {self.name}: {prefix}"
                )
            tracked_blobs = [
                path for path in tree_paths if self.entries[path].object_type == "blob"
            ]
            missing_skips = [path for path in tracked_blobs if path not in skipped]
            if missing_skips:
                raise ExtractionError(
                    f"Excluded paths are not skip-worktree for {self.name}: {missing_skips[:5]}"
                )

        return {
            "mode": self.pin["checkout"]["mode"],
            "sparseEnabled": sparse_enabled,
            "sparsePatterns": expected_sparse if excluded else [],
            "excludedPrefixes": list(excluded),
            "skippedTrackedFiles": len(skipped),
            "status": "canonical_commit_tree",
            "inputIsolation": "pinned_git_objects_materialized_to_temporary_directory",
            "worktreeInspected": False,
            "trackedChanges": None,
            "untrackedOrIgnored": None,
        }

    def _verify_submodules_and_evidence(self) -> dict[str, Any]:
        actual_gitlinks = sorted(
            entry.path for entry in self.entries.values() if entry.mode == "160000"
        )
        expected_gitlinks = sorted(self.pin["submodules"]["expectedGitlinks"])
        if actual_gitlinks != expected_gitlinks:
            raise ExtractionError(
                f"Gitlinks differ for {self.name}: expected {expected_gitlinks}, found {actual_gitlinks}"
            )
        for gitlink in actual_gitlinks:
            if (self.root / gitlink / ".git").exists():
                raise ExtractionError(f"Submodule must remain uninitialized: {self.name}/{gitlink}")

        for sentinel in self.pin["sentinelFiles"]:
            entry = self.entries.get(sentinel)
            if entry is None or entry.object_type != "blob":
                raise ExtractionError(f"Missing sentinel at pin for {self.name}: {sentinel}")
            if not (self.root / sentinel).is_file():
                raise ExtractionError(f"Sentinel is not materialized for {self.name}: {sentinel}")

        evidence_receipts = []
        for evidence in self.pin["license"]["evidence"]:
            path = evidence["path"]
            entry = self.entries.get(path)
            if entry is None or entry.oid != evidence["gitBlob"]:
                raise ExtractionError(f"License evidence blob mismatch for {self.name}: {path}")
            raw = self.read_blob(path)
            evidence_receipts.append(
                {
                    **evidence,
                    "sha256": sha256_bytes(raw),
                    "verifiedFrom": "git_commit_tree",
                }
            )
        if self.pin["license"].get("rootLicenseAbsent"):
            root_license = [
                path
                for path in self.entries
                if "/" not in path and path.lower() in {"license", "license.md", "license.txt"}
            ]
            if root_license:
                raise ExtractionError(
                    f"Root license absence assertion is false for {self.name}: {root_license}"
                )
        return {
            "policy": self.pin["submodules"]["policy"],
            "gitlinks": actual_gitlinks,
            "initialized": [],
            "licenseEvidence": evidence_receipts,
        }

    def verify(self) -> dict[str, Any]:
        identity = self._verify_identity()
        self._read_tree()
        checkout = self._verify_checkout()
        evidence = self._verify_submodules_and_evidence()
        self.receipt = {
            "sourceMode": "git_commit_tree",
            "canonical": True,
            **identity,
            "checkout": checkout,
            "submodules": {
                "policy": evidence["policy"],
                "gitlinks": evidence["gitlinks"],
                "initialized": evidence["initialized"],
            },
            "licenseEvidence": evidence["licenseEvidence"],
            "treeEntries": len(self.entries),
        }
        return self.receipt

    def read_blob(self, path: str) -> bytes:
        entry = self.entries.get(path)
        if entry is None or entry.object_type != "blob" or entry.mode == "120000":
            raise ExtractionError(f"Expected regular Git blob for {self.name}: {path}")
        result = self._run("cat-file", "blob", entry.oid, text=False)
        return bytes(result.stdout)

    def selected_entries(self, patterns: Iterable[str]) -> dict[str, GitTreeEntry]:
        compiled = tuple(_glob_regex(pattern) for pattern in sorted(set(patterns)))
        excluded = tuple(self.pin["checkout"].get("excludePaths", []))
        selected = {
            path: entry
            for path, entry in self.entries.items()
            if entry.object_type == "blob"
            and any(pattern.fullmatch(path) for pattern in compiled)
            and not any(self._under(path, prefix) for prefix in excluded)
        }
        return dict(sorted(selected.items()))

    def _batch_blobs(
        self, entries: dict[str, GitTreeEntry]
    ) -> Iterable[tuple[GitTreeEntry, bytes]]:
        requests = b"".join(entry.oid.encode("ascii") + b"\n" for entry in entries.values())
        result = subprocess.run(
            ["git", "-C", str(self.root), "cat-file", "--batch"],
            input=requests,
            check=False,
            capture_output=True,
            env=self._environment,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise ExtractionError(f"Git batch reader failed for {self.name}: {stderr}")
        output = BytesIO(result.stdout)
        for entry in entries.values():
            try:
                header = output.readline().decode("ascii", errors="strict").strip()
            except UnicodeDecodeError as exc:
                raise ExtractionError(
                    f"Git batch reader returned a non-ASCII header for {self.name}/{entry.path}"
                ) from exc
            fields = header.split(" ")
            if len(fields) != 3 or fields[0] != entry.oid or fields[1] != "blob":
                raise ExtractionError(
                    f"Git batch reader returned unexpected object for {self.name}/{entry.path}: {header}"
                )
            try:
                size = int(fields[2])
            except ValueError as exc:
                raise ExtractionError(
                    f"Git batch reader returned an invalid size for {self.name}/{entry.path}: {header}"
                ) from exc
            raw = output.read(size)
            terminator = output.read(1)
            if len(raw) != size or terminator != b"\n":
                raise ExtractionError(f"Truncated Git blob for {self.name}/{entry.path}")
            yield entry, raw
        if output.read(1):
            raise ExtractionError(f"Git batch reader returned trailing output for {self.name}")

    def materialize(self, destination: Path, patterns: Iterable[str]) -> dict[str, GitTreeEntry]:
        selected = self.selected_entries(patterns)
        content_records: list[str] = []
        selected_bytes = 0
        for entry, raw in self._batch_blobs(selected):
            target = destination / entry.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            if entry.mode == "100755":
                target.chmod(0o755)
            selected_bytes += len(raw)
            content_records.append(
                f"{entry.path}\0{entry.oid}\0{len(raw)}\0{sha256_bytes(raw)}"
            )
        self.receipt["selectedBlobs"] = len(selected)
        self.receipt["selectedBytes"] = selected_bytes
        self.receipt["selectedManifestSha256"] = sha256_bytes(
            "\n".join(
                f"{entry.path}\0{entry.oid}" for entry in selected.values()
            ).encode("utf-8")
        )
        self.receipt["selectedContentManifestSha256"] = sha256_bytes(
            "\n".join(content_records).encode("utf-8")
        )
        return selected
