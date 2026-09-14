"""Deterministic, fail-closed helpers used by provider extractors."""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from scripts.oss_census.model import (
    BOUNDARY_TYPES,
    CANDIDATE_STATES,
    CAPABILITIES,
    Candidate,
    ExaminedFile,
    Observation,
    ParseResult,
    ScopeResult,
    canonical_contract_key,
)

TextParser = Callable[[str, str], ParseResult]

DEFAULT_EXCLUDES = (
    ".git",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__snapshots__",
    "enterprise",
    "ee",
)


class ExtractionError(RuntimeError):
    """Raised when a candidate source cannot be parsed without losing evidence."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def evidence_line(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    if end == -1:
        end = len(text)
    return " ".join(text[start:end].strip().split())[:300]


def normalize_route(*parts: str) -> str:
    """Join already-resolved route fragments into one absolute route."""

    components = [part.strip("/") for part in parts if part and part.strip("/")]
    return "/" + "/".join(components) if components else "/"


def http_capability(route: str, *, explicit: str | None = None) -> str:
    """Classify only exact route segments; never infer from arbitrary substrings."""

    if explicit is not None:
        if explicit not in {"api", "webhook", "import", "export"}:
            raise ExtractionError(f"Invalid explicit HTTP capability: {explicit}")
        return explicit
    segments = {
        segment.lower()
        for segment in route.strip("/").split("/")
        if segment and not segment.startswith(":") and not segment.startswith("{")
    }
    if segments & {"webhook", "webhooks", "hooks"}:
        return "webhook"
    if "import" in segments:
        return "import"
    if "export" in segments:
        return "export"
    return "api"


def specialized_category(default: str, *values: str) -> str:
    """Compatibility wrapper using exact path-token semantics only."""

    if default not in {"api", "job"}:
        return default
    if default == "job":
        return "job"
    return http_capability("/".join(values))


def _candidate_id(path: str, line: int, kind: str, declaration: str) -> str:
    identity = "\0".join((path, str(line), kind, declaration))
    return f"candidate:{sha256_text(identity)[:20]}"


def observation(
    *,
    upstream: str,
    category: str,
    boundary_type: str,
    family: str,
    transport: str,
    operation: str,
    route: str,
    handler: str,
    name: str,
    path: str,
    text: str,
    offset: int,
    extractor: str,
    candidate_kind: str,
    declaration: str | None = None,
    aliases: Iterable[str] = (),
    facets: Iterable[str] = (),
    details: dict[str, object] | None = None,
) -> Observation:
    if category not in CAPABILITIES:
        raise ExtractionError(f"Invalid capability {category!r} for {path}")
    if boundary_type not in BOUNDARY_TYPES:
        raise ExtractionError(f"Invalid boundary type {boundary_type!r} for {path}")
    line = line_number(text, offset)
    declaration_key = declaration or evidence_line(text, offset)
    candidate_id = _candidate_id(path, line, candidate_kind, declaration_key)
    key = canonical_contract_key(
        transport=transport,
        operation=operation,
        route=route,
        handler=handler,
    )
    return Observation(
        upstream=upstream,
        category=category,
        boundary_type=boundary_type,
        family=family,
        transport=transport,
        operation=operation,
        route=route,
        handler=handler,
        key=key,
        name=name,
        path=path,
        line=line,
        evidence=evidence_line(text, offset),
        extractor=extractor,
        candidate_id=candidate_id,
        candidate_kind=candidate_kind,
        aliases=tuple(sorted(set(alias for alias in aliases if alias))),
        facets=tuple(sorted(set(facets))),
        details=dict(details or {}),
    )


def excluded_candidate(
    *,
    path: str,
    text: str,
    offset: int,
    kind: str,
    reason: str,
    declaration: str | None = None,
    details: dict[str, Any] | None = None,
) -> Candidate:
    line = line_number(text, offset)
    declaration_key = declaration or evidence_line(text, offset)
    return Candidate(
        id=_candidate_id(path, line, kind, declaration_key),
        kind=kind,
        path=path,
        line=line,
        evidence=evidence_line(text, offset),
        status="excluded",
        reason=reason,
        details=dict(details or {}),
    )


def manual_candidate(
    *,
    path: str,
    text: str,
    offset: int,
    kind: str,
    reason: str,
    declaration: str | None = None,
    details: dict[str, Any] | None = None,
) -> Candidate:
    candidate = excluded_candidate(
        path=path,
        text=text,
        offset=offset,
        kind=kind,
        reason=reason,
        declaration=declaration,
        details=details,
    )
    return replace(candidate, status="manual_review")


def parse_result(
    observations: Iterable[Observation],
    other_candidates: Iterable[Candidate] = (),
) -> ParseResult:
    items = tuple(observations)
    grouped: dict[str, list[Observation]] = defaultdict(list)
    for item in items:
        grouped[item.candidate_id].append(item)
    candidates = list(other_candidates)
    for candidate_id, emitted in sorted(grouped.items()):
        first = emitted[0]
        candidates.append(
            Candidate(
                id=candidate_id,
                kind=first.candidate_kind,
                path=first.path,
                line=first.line,
                evidence=first.evidence,
                status="accepted",
                emitted_contract_keys=tuple(sorted({item.key for item in emitted})),
            )
        )
    candidate_ids = [candidate.id for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        duplicates = sorted(
            item for item, count in Counter(candidate_ids).items() if count > 1
        )
        raise ExtractionError(f"Duplicate parser candidate IDs: {duplicates[:5]}")
    for candidate in candidates:
        if candidate.status not in CANDIDATE_STATES:
            raise ExtractionError(f"Invalid candidate status: {candidate.status}")
        if candidate.status == "accepted" and not candidate.emitted_contract_keys:
            raise ExtractionError(f"Accepted candidate emitted no contracts: {candidate.id}")
        if candidate.status != "accepted" and not candidate.reason:
            raise ExtractionError(f"Non-accepted candidate lacks a reason: {candidate.id}")
    return ParseResult(observations=items, candidates=tuple(candidates))


def _is_excluded(path: Path, root: Path, excluded_parts: Iterable[str]) -> bool:
    relative_parts = path.relative_to(root).parts
    excluded = set(excluded_parts)
    return any(part in excluded for part in relative_parts)


def matching_files(
    root: Path,
    includes: Iterable[str],
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
) -> list[Path]:
    files: set[Path] = set()
    for pattern in includes:
        files.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(
        (path for path in files if not _is_excluded(path, root, excludes)),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def text_scope(
    *,
    upstream: str,
    root: Path,
    scope_id: str,
    extractor: str,
    includes: tuple[str, ...],
    parser: TextParser,
    excludes: tuple[str, ...] = DEFAULT_EXCLUDES,
) -> ScopeResult:
    examined: list[ExaminedFile] = []
    observations: list[Observation] = []
    candidates: list[Candidate] = []
    for source_path in matching_files(root, includes, excludes):
        raw = source_path.read_bytes()
        relative_path = source_path.relative_to(root).as_posix()
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ExtractionError(f"Non-UTF-8 candidate source {relative_path}: {exc}") from exc
        try:
            result = parser(relative_path, text)
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError(
                f"Parser {extractor} failed for {relative_path}: {type(exc).__name__}: {exc}"
            ) from exc
        categories = tuple(sorted({item.category for item in result.observations}))
        statuses = Counter(candidate.status for candidate in result.candidates)
        classification = "unit_source" if result.observations else "no_observable_contract"
        examined.append(
            ExaminedFile(
                path=relative_path,
                sha256=sha256_bytes(raw),
                classification=classification,
                observation_count=len(result.observations),
                candidate_count=len(result.candidates),
                categories=categories,
                candidate_statuses=dict(sorted(statuses.items())),
            )
        )
        observations.extend(result.observations)
        candidates.extend(result.candidates)
    return ScopeResult(
        upstream=upstream,
        scope_id=scope_id,
        extractor=extractor,
        includes=includes,
        excludes=excludes,
        files=tuple(examined),
        observations=tuple(observations),
        candidates=tuple(candidates),
    )


def inventory_scope(
    *,
    upstream: str,
    root: Path,
    scope_id: str,
    includes: tuple[str, ...],
    classification: str,
    excludes: tuple[str, ...] = DEFAULT_EXCLUDES,
) -> ScopeResult:
    files = []
    for source_path in matching_files(root, includes, excludes):
        raw = source_path.read_bytes()
        files.append(
            ExaminedFile(
                path=source_path.relative_to(root).as_posix(),
                sha256=sha256_bytes(raw),
                classification=classification,
                observation_count=0,
            )
        )
    return ScopeResult(
        upstream=upstream,
        scope_id=scope_id,
        extractor="supporting-source-inventory-v2",
        includes=includes,
        excludes=excludes,
        files=tuple(files),
        observations=(),
        candidates=(),
    )


def find_balanced(text: str, opening: int, open_char: str = "{", close_char: str = "}") -> int:
    """Find a balanced JS/Ruby-style region while ignoring strings and comments."""

    if opening >= len(text) or text[opening] != open_char:
        raise ExtractionError(f"Expected {open_char!r} at offset {opening}")
    depth = 0
    index = opening
    quote: str | None = None
    escaped = False
    line_comment = False
    block_comment = False
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and nxt == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "/" and nxt == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and nxt == "*":
            block_comment = True
            index += 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return index
            if depth < 0:
                break
        index += 1
    raise ExtractionError(f"Unbalanced {open_char}{close_char} region at offset {opening}")


def javascript_brace_regions(text: str) -> tuple[tuple[int, int], ...]:
    """Return every balanced JavaScript brace region, excluding strings/comments."""

    stack: list[int] = []
    regions: list[tuple[int, int]] = []
    index = 0
    quote: str | None = None
    escaped = False
    line_comment = False
    block_comment = False
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and nxt == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "/" and nxt == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and nxt == "*":
            block_comment = True
            index += 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "{":
            stack.append(index)
        elif char == "}":
            if not stack:
                raise ExtractionError(f"Unexpected JavaScript closing brace at offset {index}")
            regions.append((stack.pop(), index))
        index += 1

    if quote is not None or block_comment or stack:
        location = stack[-1] if stack else len(text)
        raise ExtractionError(f"Unbalanced JavaScript source near offset {location}")
    return tuple(sorted(regions))


def javascript_object_members(text: str, opening: int) -> tuple[list[tuple[str, int]], int]:
    """Return top-level static member names from a JavaScript object literal."""

    closing = find_balanced(text, opening)
    members: list[tuple[str, int]] = []
    index = opening + 1
    depth = 1
    parentheses = 0
    brackets = 0
    quote: str | None = None
    escaped = False
    line_comment = False
    block_comment = False
    expect_member = True

    while index < closing:
        char = text[index]
        nxt = text[index + 1] if index + 1 < closing else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and nxt == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "/" and nxt == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and nxt == "*":
            block_comment = True
            index += 2
            continue
        member_position = depth == 1 and parentheses == 0 and brackets == 0 and expect_member
        if char in {"'", '"', "`"} and not member_position:
            quote = char
            index += 1
            continue
        if char == "{":
            depth += 1
            index += 1
            continue
        if char == "}":
            depth -= 1
            index += 1
            continue
        if char == "(":
            parentheses += 1
            index += 1
            continue
        if char == ")":
            parentheses -= 1
            index += 1
            continue
        if char == "[":
            brackets += 1
            index += 1
            continue
        if char == "]":
            brackets -= 1
            index += 1
            continue
        if depth == 1 and parentheses == 0 and brackets == 0 and char == ",":
            expect_member = True
            index += 1
            continue
        if not member_position:
            index += 1
            continue
        if char.isspace():
            index += 1
            continue
        if text.startswith("async", index) and (
            index + 5 == closing or not (text[index + 5].isalnum() or text[index + 5] in "_$")
        ):
            index += 5
            continue
        if text.startswith("...", index) or char == "[":
            expect_member = False
            index += 1
            continue

        member_offset = index
        if char in {"'", '"'}:
            member_quote = char
            index += 1
            value_start = index
            member_escaped = False
            while index < closing:
                current = text[index]
                if member_escaped:
                    member_escaped = False
                elif current == "\\":
                    member_escaped = True
                elif current == member_quote:
                    break
                index += 1
            if index >= closing:
                raise ExtractionError(f"Unterminated JavaScript object key at offset {member_offset}")
            name = text[value_start:index]
            index += 1
        else:
            match = re.match(r"[A-Za-z_$][\w$.-]*", text[index:])
            if not match:
                expect_member = False
                index += 1
                continue
            name = match.group(0)
            index += len(name)

        probe = index
        while probe < closing and text[probe].isspace():
            probe += 1
        if probe < closing and text[probe] in {":", "("}:
            members.append((name, member_offset))
        expect_member = False

    if depth != 1 or parentheses != 0 or brackets != 0 or quote is not None:
        raise ExtractionError(f"Unbalanced JavaScript object members at offset {opening}")
    return members, closing


def unique_observations(items: list[Observation]) -> list[Observation]:
    """Return exact observations unchanged; global reconciliation handles duplicates."""

    return items
