"""Deterministic, tenant-scoped context compilation for model requests."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from app.ai.base import AIMessage
from app.ai.errors import (
    ContextBudgetExceededError,
    ContextCompilationError,
    ContextScopeError,
)
from app.ai.token_budget import DeterministicTokenEstimator

CONTEXT_COMPILER_VERSION = "1.0.0"
CONTEXT_SCHEMA_VERSION = "klm.context.v1"
DEFAULT_CONTEXT_TOKEN_BUDGET = 4_096
DEFAULT_RECENT_WINDOW_SIZE = 12
_TRUNCATION_MARKER = "...[truncated]"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CARD_FIELD_PRIORITY = (
    "id",
    "case_number",
    "practice_area",
    "demand_type",
    "lead_source",
    "acquisition_context",
    "manual_context_revision",
    "column_id",
    "micro_status",
    "priority",
    "urgency",
    "executive_summary",
    "next_action",
    "next_deadline",
    "is_blocked",
    "needs_approval",
)
_CARD_FIELD_PRIORITY_INDEX = {
    field_name: index for index, field_name in enumerate(_CARD_FIELD_PRIORITY)
}


def _required_text(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ContextCompilationError(f"{label} is required.")
    return normalized


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_timestamp(value: datetime | str) -> str:
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ContextCompilationError("Recent message occurred_at is required.")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContextCompilationError("Recent message occurred_at must be ISO-8601.") from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise ContextCompilationError("Recent message occurred_at must be ISO-8601.")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContextCompilationError("Recent message occurred_at must include a timezone.")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _normalize_json_value(value: Any, *, path: str = "card") -> Any:
    if isinstance(value, Enum):
        return _normalize_json_value(value.value, path=path)
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContextCompilationError(f"{path} contains a non-finite number.")
        return 0.0 if value == 0 else value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ContextCompilationError(f"{path} contains a non-finite decimal.")
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ContextCompilationError(f"{path} contains a timezone-naive datetime.")
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ContextCompilationError(f"{path} contains a non-string object key.")
        normalized: dict[str, Any] = {}
        for key in sorted(value):
            normalized[key] = _normalize_json_value(value[key], path=f"{path}.{key}")
        return normalized
    if isinstance(value, (set, frozenset)):
        items = [_normalize_json_value(item, path=f"{path}[]") for item in value]
        return sorted(items, key=_canonical_json)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _normalize_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)
        ]
    raise ContextCompilationError(f"{path} contains unsupported value {type(value).__name__}.")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ContextProvenance:
    workspace_id: str
    matter_id: str
    source_type: str
    source_id: str
    source_version: str | None = None
    source_uri: str | None = None
    content_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_id", _required_text(self.workspace_id, "workspace_id"))
        object.__setattr__(self, "matter_id", _required_text(self.matter_id, "matter_id"))
        object.__setattr__(self, "source_type", _required_text(self.source_type, "source_type"))
        object.__setattr__(self, "source_id", _required_text(self.source_id, "source_id"))
        object.__setattr__(self, "source_version", _optional_text(self.source_version))
        object.__setattr__(self, "source_uri", _optional_text(self.source_uri))
        digest = _optional_text(self.content_sha256)
        if digest is not None:
            digest = digest.lower()
            if not _SHA256_PATTERN.fullmatch(digest):
                raise ContextCompilationError("content_sha256 must be a lowercase SHA-256 digest.")
        object.__setattr__(self, "content_sha256", digest)


@dataclass(frozen=True, slots=True)
class ContextMessage:
    role: str
    content: str
    occurred_at: datetime | str
    provenance: ContextProvenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _required_text(self.role, "message role"))
        object.__setattr__(self, "content", _required_text(self.content, "message content"))
        object.__setattr__(self, "occurred_at", _normalize_timestamp(self.occurred_at))
        if not isinstance(self.provenance, ContextProvenance):
            raise ContextCompilationError("Recent messages require ContextProvenance.")


@dataclass(frozen=True, slots=True)
class ContextSummary:
    content: str
    provenance: ContextProvenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", _required_text(self.content, "summary content"))
        if not isinstance(self.provenance, ContextProvenance):
            raise ContextCompilationError("Summary requires ContextProvenance.")


@dataclass(frozen=True, slots=True)
class ContextEvidence:
    content: str
    provenance: ContextProvenance
    title: str = ""
    rank: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", _required_text(self.content, "evidence content"))
        object.__setattr__(self, "title", str(self.title or "").strip())
        if not isinstance(self.provenance, ContextProvenance):
            raise ContextCompilationError("Evidence requires ContextProvenance.")
        if isinstance(self.rank, bool) or not isinstance(self.rank, int):
            raise ContextCompilationError("Evidence rank must be an integer.")


@dataclass(frozen=True, slots=True)
class CompiledContext:
    compiler_version: str
    schema_version: str
    tokenizer_version: str
    workspace_id: str
    matter_id: str
    content: str
    digest: str
    estimated_tokens: int
    token_budget: int
    recent_items_included: int
    recent_items_omitted: int
    evidence_items_included: int
    evidence_items_omitted: int
    truncated: bool

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.content)

    def as_message(self, role: str = "system") -> AIMessage:
        return AIMessage(role=_required_text(role, "message role"), content=self.content)


class ContextCompiler:
    """Compile canonical context without database, network, or clock access."""

    def __init__(
        self,
        *,
        version: str = CONTEXT_COMPILER_VERSION,
        recent_window_size: int = DEFAULT_RECENT_WINDOW_SIZE,
        default_token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET,
        token_estimator: DeterministicTokenEstimator | None = None,
    ) -> None:
        self.version = _required_text(version, "compiler version")
        if recent_window_size <= 0:
            raise ValueError("recent_window_size must be positive.")
        if default_token_budget <= 0:
            raise ValueError("default_token_budget must be positive.")
        self.recent_window_size = recent_window_size
        self.default_token_budget = default_token_budget
        self.token_estimator = token_estimator or DeterministicTokenEstimator()

    @staticmethod
    def _require_scope(
        provenance: ContextProvenance,
        *,
        workspace_id: str,
        matter_id: str,
        label: str,
    ) -> None:
        if provenance.workspace_id != workspace_id or provenance.matter_id != matter_id:
            raise ContextScopeError(f"{label} provenance is outside workspace_id/matter_id scope.")

    @staticmethod
    def _render_provenance(
        provenance: ContextProvenance,
        *,
        source_content: str,
    ) -> dict[str, Any]:
        computed_digest = _sha256(source_content)
        if provenance.content_sha256 and provenance.content_sha256 != computed_digest:
            raise ContextCompilationError(
                f"Provenance digest mismatch for {provenance.source_type}:{provenance.source_id}."
            )
        return {
            "content_sha256": computed_digest,
            "matter_id": provenance.matter_id,
            "source_id": provenance.source_id,
            "source_type": provenance.source_type,
            "source_uri": provenance.source_uri,
            "source_version": provenance.source_version,
            "workspace_id": provenance.workspace_id,
        }

    @staticmethod
    def _card_key(key: str) -> tuple[int, str]:
        return (_CARD_FIELD_PRIORITY_INDEX.get(key, len(_CARD_FIELD_PRIORITY)), key)

    def _truncate_text_to_fit(
        self,
        *,
        container: dict[str, Any],
        key: str,
        render: Callable[[], str],
        token_budget: int,
        mark_truncated: Callable[[], None],
    ) -> bool:
        original = container.get(key)
        if not isinstance(original, str) or not original:
            return False

        mark_truncated()
        container[key] = ""
        if self.token_estimator.count_text(render()) > token_budget:
            return True

        low = 0
        high = len(original)
        best = ""
        while low <= high:
            midpoint = (low + high) // 2
            candidate = (
                original
                if midpoint == len(original)
                else (original[:midpoint] + _TRUNCATION_MARKER)
            )
            container[key] = candidate
            if self.token_estimator.count_text(render()) <= token_budget:
                best = candidate
                low = midpoint + 1
            else:
                high = midpoint - 1
        container[key] = best
        return best != original

    def compile(
        self,
        *,
        workspace_id: str,
        matter_id: str,
        card: Mapping[str, Any],
        recent_messages: Sequence[ContextMessage] = (),
        summary: ContextSummary | str | None = None,
        evidence: Sequence[ContextEvidence] = (),
        token_budget: int | None = None,
        card_provenance: ContextProvenance | None = None,
        summary_provenance: ContextProvenance | None = None,
    ) -> CompiledContext:
        workspace = _required_text(workspace_id, "workspace_id")
        matter = _required_text(matter_id, "matter_id")
        budget = self.default_token_budget if token_budget is None else token_budget
        if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
            raise ContextBudgetExceededError("token_budget must be a positive integer.")
        if not isinstance(card, Mapping):
            raise ContextCompilationError("card must be a mapping.")

        normalized_card = _normalize_json_value(card)
        effective_card_provenance = card_provenance or ContextProvenance(
            workspace_id=workspace,
            matter_id=matter,
            source_type="matter_card",
            source_id=matter,
        )
        self._require_scope(
            effective_card_provenance,
            workspace_id=workspace,
            matter_id=matter,
            label="Card",
        )
        card_source = _canonical_json(normalized_card)

        normalized_summary: ContextSummary | None
        if isinstance(summary, str):
            normalized_summary = ContextSummary(
                content=summary,
                provenance=summary_provenance
                or ContextProvenance(
                    workspace_id=workspace,
                    matter_id=matter,
                    source_type="rolling_summary",
                    source_id="current",
                ),
            )
        else:
            normalized_summary = summary
            if summary_provenance is not None:
                raise ContextCompilationError(
                    "summary_provenance is only valid when summary is plain text."
                )
        if normalized_summary is not None:
            if not isinstance(normalized_summary, ContextSummary):
                raise ContextCompilationError("summary must be ContextSummary, text, or None.")
            self._require_scope(
                normalized_summary.provenance,
                workspace_id=workspace,
                matter_id=matter,
                label="Summary",
            )

        normalized_messages: list[ContextMessage] = []
        for item in recent_messages:
            if not isinstance(item, ContextMessage):
                raise ContextCompilationError("recent_messages must contain ContextMessage.")
            self._require_scope(
                item.provenance,
                workspace_id=workspace,
                matter_id=matter,
                label="Recent message",
            )
            normalized_messages.append(item)
        normalized_messages.sort(
            key=lambda item: (
                str(item.occurred_at),
                item.provenance.source_type,
                item.provenance.source_id,
                _sha256(item.content),
            )
        )
        windowed_messages = normalized_messages[-self.recent_window_size :]

        normalized_evidence: list[ContextEvidence] = []
        for item in evidence:
            if not isinstance(item, ContextEvidence):
                raise ContextCompilationError("evidence must contain ContextEvidence.")
            self._require_scope(
                item.provenance,
                workspace_id=workspace,
                matter_id=matter,
                label="Evidence",
            )
            normalized_evidence.append(item)
        normalized_evidence.sort(
            key=lambda item: (
                -item.rank,
                item.provenance.source_type,
                item.provenance.source_id,
                item.title,
                _sha256(item.content),
            )
        )

        card_entry: dict[str, Any] = {
            "content_truncated": False,
            "data": dict(normalized_card),
            "provenance": self._render_provenance(
                effective_card_provenance,
                source_content=card_source,
            ),
        }
        summary_entry = (
            {
                "content": normalized_summary.content,
                "content_truncated": False,
                "provenance": self._render_provenance(
                    normalized_summary.provenance,
                    source_content=normalized_summary.content,
                ),
            }
            if normalized_summary is not None
            else None
        )
        recent_entries = [
            {
                "content": item.content,
                "content_truncated": False,
                "occurred_at": item.occurred_at,
                "provenance": self._render_provenance(
                    item.provenance,
                    source_content=item.content,
                ),
                "role": item.role,
            }
            for item in windowed_messages
        ]
        evidence_entries = [
            {
                "content": item.content,
                "content_truncated": False,
                "provenance": self._render_provenance(
                    item.provenance,
                    source_content=item.content,
                ),
                "rank": item.rank,
                "title": item.title,
            }
            for item in normalized_evidence
        ]
        payload: dict[str, Any] = {
            "card": card_entry,
            "compiler_version": self.version,
            "evidence": evidence_entries,
            "recent_messages": recent_entries,
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "scope": {"matter_id": matter, "workspace_id": workspace},
            "selection": {},
            "summary": summary_entry,
            "tokenizer_version": self.token_estimator.version,
        }

        card_fields_available = len(normalized_card)
        recent_available = len(normalized_messages)
        recent_windowed = len(windowed_messages)
        evidence_available = len(normalized_evidence)

        def refresh_selection() -> None:
            content_truncations = int(bool(card_entry["content_truncated"]))
            content_truncations += sum(
                int(bool(entry["content_truncated"])) for entry in payload["recent_messages"]
            )
            content_truncations += sum(
                int(bool(entry["content_truncated"])) for entry in payload["evidence"]
            )
            if payload["summary"] is not None:
                content_truncations += int(bool(payload["summary"]["content_truncated"]))
            payload["selection"] = {
                "card_fields_available": card_fields_available,
                "card_fields_included": len(card_entry["data"]),
                "card_fields_omitted_for_budget": (card_fields_available - len(card_entry["data"])),
                "content_truncations": content_truncations,
                "evidence_available": evidence_available,
                "evidence_included": len(payload["evidence"]),
                "evidence_omitted_for_budget": evidence_available - len(payload["evidence"]),
                "recent_available": recent_available,
                "recent_included": len(payload["recent_messages"]),
                "recent_omitted_by_window": recent_available - recent_windowed,
                "recent_omitted_for_budget": recent_windowed - len(payload["recent_messages"]),
                "recent_window_limit": self.recent_window_size,
            }

        def render() -> str:
            refresh_selection()
            return _canonical_json(payload)

        def fits() -> bool:
            return self.token_estimator.count_text(render()) <= budget

        while len(payload["evidence"]) > 1 and not fits():
            payload["evidence"].pop()
        while len(payload["recent_messages"]) > 1 and not fits():
            payload["recent_messages"].pop(0)

        for entry in reversed(payload["evidence"]):
            if fits():
                break
            self._truncate_text_to_fit(
                container=entry,
                key="content",
                render=render,
                token_budget=budget,
                mark_truncated=lambda entry=entry: entry.__setitem__("content_truncated", True),
            )
        for entry in payload["recent_messages"]:
            if fits():
                break
            self._truncate_text_to_fit(
                container=entry,
                key="content",
                render=render,
                token_budget=budget,
                mark_truncated=lambda entry=entry: entry.__setitem__("content_truncated", True),
            )
        if payload["summary"] is not None and not fits():
            summary_payload = payload["summary"]
            self._truncate_text_to_fit(
                container=summary_payload,
                key="content",
                render=render,
                token_budget=budget,
                mark_truncated=lambda: summary_payload.__setitem__("content_truncated", True),
            )

        card_keys = sorted(card_entry["data"], key=self._card_key)
        for key in reversed(card_keys):
            if fits():
                break
            value = card_entry["data"].get(key)
            if isinstance(value, str) and value:
                self._truncate_text_to_fit(
                    container=card_entry["data"],
                    key=key,
                    render=render,
                    token_budget=budget,
                    mark_truncated=lambda: card_entry.__setitem__("content_truncated", True),
                )
            if not fits():
                card_entry["data"].pop(key, None)
                card_entry["content_truncated"] = True

        while payload["evidence"] and not fits():
            payload["evidence"].pop()
        while payload["recent_messages"] and not fits():
            payload["recent_messages"].pop(0)
        if payload["summary"] is not None and not fits():
            payload["summary"] = None
        for key in reversed(sorted(card_entry["data"], key=self._card_key)):
            if fits():
                break
            card_entry["data"].pop(key, None)
            card_entry["content_truncated"] = True

        content = render()
        estimated_tokens = self.token_estimator.count_text(content)
        if estimated_tokens > budget:
            raise ContextBudgetExceededError(
                "token_budget is smaller than the mandatory scoped context envelope "
                f"({estimated_tokens} tokens required)."
            )

        selection = payload["selection"]
        recent_omitted = (
            selection["recent_omitted_by_window"] + selection["recent_omitted_for_budget"]
        )
        evidence_omitted = selection["evidence_omitted_for_budget"]
        truncated = bool(
            recent_omitted
            or evidence_omitted
            or selection["card_fields_omitted_for_budget"]
            or selection["content_truncations"]
            or (normalized_summary is not None and payload["summary"] is None)
        )
        return CompiledContext(
            compiler_version=self.version,
            schema_version=CONTEXT_SCHEMA_VERSION,
            tokenizer_version=self.token_estimator.version,
            workspace_id=workspace,
            matter_id=matter,
            content=content,
            digest=_sha256(content),
            estimated_tokens=estimated_tokens,
            token_budget=budget,
            recent_items_included=selection["recent_included"],
            recent_items_omitted=recent_omitted,
            evidence_items_included=selection["evidence_included"],
            evidence_items_omitted=evidence_omitted,
            truncated=truncated,
        )
