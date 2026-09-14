"""Shared data structures for fail-closed OSS census extractors."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CAPABILITIES = (
    "api",
    "ui",
    "job",
    "webhook",
    "mcp",
    "realtime",
    "permission",
    "config",
    "import",
    "export",
)

# Kept as a compatibility alias for disposition policies and older callers.
CATEGORIES = CAPABILITIES

BOUNDARY_TYPES = (
    "http_route",
    "ui_route",
    "scheduled_job",
    "realtime_event",
    "mcp_tool",
    "permission_rule",
    "configuration_item",
    "package_api",
    "package_export",
)

CANDIDATE_STATES = ("accepted", "excluded", "manual_review")

DISPOSITION_STATES = (
    "discovered",
    "mapped",
    "native_shadow",
    "native_primary",
    "native_verified",
    "blocked_proprietary",
    "blocked_license",
)

REQUIRED_UPSTREAMS = (
    "chatwoot",
    "chattermate",
    "bottlecrm",
    "rocketchat",
    "rocket-embedded-chat",
)


def canonical_contract_key(
    *, transport: str, operation: str, route: str, handler: str
) -> str:
    """Return an unambiguous, human-inspectable canonical boundary identity."""

    values = (transport, operation, route, handler)
    if not all(isinstance(value, str) and value for value in values):
        raise ValueError("Canonical contract keys require transport, operation, route, and handler")
    return json.dumps(values, ensure_ascii=True, separators=(",", ":"))


@dataclass(frozen=True)
class Observation:
    """One statically observable public behavior in an upstream source tree."""

    upstream: str
    category: str
    boundary_type: str
    family: str
    transport: str
    operation: str
    route: str
    handler: str
    key: str
    name: str
    path: str
    line: int
    evidence: str
    extractor: str
    candidate_id: str
    candidate_kind: str
    aliases: tuple[str, ...] = ()
    facets: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def method(self) -> str | None:
        return self.operation if self.transport == "http" else None


@dataclass(frozen=True)
class Candidate:
    """One parser candidate, including accepted and deliberately excluded declarations."""

    id: str
    kind: str
    path: str
    line: int
    evidence: str
    status: str
    reason: str | None = None
    emitted_contract_keys: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParseResult:
    """Complete parser result for one source file."""

    observations: tuple[Observation, ...]
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True)
class ExaminedFile:
    """A source file classified by one extractor scope."""

    path: str
    sha256: str
    classification: str
    observation_count: int
    candidate_count: int = 0
    categories: tuple[str, ...] = ()
    candidate_statuses: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ScopeResult:
    """Coverage and output for a declared source-selection scope."""

    upstream: str
    scope_id: str
    extractor: str
    includes: tuple[str, ...]
    excludes: tuple[str, ...]
    files: tuple[ExaminedFile, ...]
    observations: tuple[Observation, ...]
    candidates: tuple[Candidate, ...] = ()


@dataclass(frozen=True)
class ExtractionResult:
    """Complete result returned by one provider extractor."""

    upstream: str
    root: Path
    scopes: tuple[ScopeResult, ...]

    @property
    def observations(self) -> tuple[Observation, ...]:
        return tuple(observation for scope in self.scopes for observation in scope.observations)

    @property
    def candidates(self) -> tuple[Candidate, ...]:
        return tuple(candidate for scope in self.scopes for candidate in scope.candidates)
