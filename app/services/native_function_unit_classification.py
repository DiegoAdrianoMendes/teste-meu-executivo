"""Single source of truth for adapted FunctionUnit execution semantics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

RuntimeMode = Literal["read_only", "event_sourced", "configuration_gate"]

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_WRITE_METHODS = {"POST", "PUT", "PATCH"}
_DELETE_METHODS = {"DELETE"}
_READ_PREFIXES = (
    "get",
    "list",
    "read",
    "search",
    "show",
    "view",
    "check",
    "fetch",
    "find",
    "history",
    "statistics",
    "analytics",
    "report",
    "suggest",
    "preview",
    "unread",
)
_ARCHIVE_PREFIXES = ("delete", "remove", "destroy", "unlink")
_CONFIG_MARKERS = {
    "cloud",
    "oauth",
    "saml",
    "smtp",
    "webdav",
    "stripe",
    "twilio",
    "shopify",
    "jira",
    "federation",
    "push",
    "provider",
    "integration",
    "external",
}
_MCP_READ_WORDS = {"describe", "get", "list", "read", "search"}
_MCP_ARCHIVE_WORDS = {"delete", "remove"}
_MCP_UPSERT_WORDS = {"create", "update", "upsert"}


@dataclass(frozen=True)
class NativeFunctionUnitClassification:
    semantic_profile: str
    runtime_mode: RuntimeMode
    local_effect: str
    mutating: bool
    equivalence_group: str


def _normalized_tokens(unit: dict[str, Any]) -> set[str]:
    source = unit["source"]
    text = " ".join(
        str(value or "")
        for value in (
            unit.get("name"),
            source.get("operation"),
            source.get("route"),
            source.get("handler"),
        )
    ).casefold()
    return set(re.findall(r"[a-z0-9]+", text))


def _starts_with(value: str, prefixes: tuple[str, ...]) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    return any(normalized.startswith(prefix) for prefix in prefixes)


def classify_native_function_unit(
    unit: dict[str, Any],
) -> NativeFunctionUnitClassification:
    """Classify one source boundary into one internally consistent KLM contract."""

    category = str(unit["category"])
    source = unit["source"]
    method = str(source.get("method") or "").upper()
    operation = str(source.get("operation") or "")
    name = str(unit.get("name") or "")
    external_gate = bool(_normalized_tokens(unit) & _CONFIG_MARKERS)

    if category == "config":
        return NativeFunctionUnitClassification(
            "configuration.write",
            "configuration_gate",
            "configure",
            True,
            "configuration.apply",
        )
    if category == "permission":
        return NativeFunctionUnitClassification(
            "authorization.evaluate",
            "read_only",
            "evaluate",
            False,
            "authorization.evaluate",
        )
    if category == "ui":
        return NativeFunctionUnitClassification(
            "ui.navigate", "read_only", "navigate", False, "ui.navigate"
        )
    if category == "job":
        return NativeFunctionUnitClassification(
            "job.enqueue", "event_sourced", "enqueue", True, "job.enqueue"
        )
    if category == "webhook":
        if method in _READ_METHODS:
            return NativeFunctionUnitClassification(
                "webhook.inspect", "read_only", "read", False, "webhook.inspect"
            )
        return NativeFunctionUnitClassification(
            "webhook.ingest", "event_sourced", "ingest", True, "webhook.ingest"
        )
    if category == "mcp":
        mcp_tokens = set(
            re.findall(
                r"[a-z0-9]+",
                " ".join(
                    (
                        name,
                        operation,
                        str(source.get("handler") or ""),
                    )
                ).casefold(),
            )
        )
        if mcp_tokens & _MCP_READ_WORDS:
            return NativeFunctionUnitClassification(
                "mcp.query", "read_only", "read", False, "mcp.query"
            )
        if mcp_tokens & _MCP_ARCHIVE_WORDS:
            return NativeFunctionUnitClassification(
                "mcp.invoke", "event_sourced", "archive", True, "entity.archive"
            )
        if mcp_tokens & _MCP_UPSERT_WORDS:
            return NativeFunctionUnitClassification(
                "mcp.invoke", "event_sourced", "upsert", True, "entity.upsert"
            )
        return NativeFunctionUnitClassification(
            "mcp.invoke", "event_sourced", "invoke", True, "mcp.invoke"
        )
    if category == "import":
        return NativeFunctionUnitClassification(
            "data.import", "event_sourced", "import", True, "data.import.stage"
        )
    if category == "export":
        return NativeFunctionUnitClassification(
            "data.export", "read_only", "export", False, "data.export.generate"
        )
    if category == "realtime":
        normalized_operation = operation.casefold()
        if normalized_operation in {"connect", "subscribe"}:
            return NativeFunctionUnitClassification(
                "realtime.subscribe",
                "read_only",
                "subscribe",
                False,
                "realtime.subscribe",
            )
        if _starts_with(operation, _READ_PREFIXES):
            return NativeFunctionUnitClassification(
                "realtime.read", "read_only", "read", False, "realtime.query"
            )
        if _starts_with(operation, _ARCHIVE_PREFIXES):
            return NativeFunctionUnitClassification(
                "realtime.archive",
                "event_sourced",
                "archive",
                True,
                "realtime.publish",
            )
        if external_gate:
            return NativeFunctionUnitClassification(
                "realtime.configure",
                "configuration_gate",
                "configure",
                True,
                "configuration.apply",
            )
        return NativeFunctionUnitClassification(
            "realtime.command",
            "event_sourced",
            "invoke",
            True,
            "realtime.publish",
        )

    if method in {"ANY", "*"}:
        return NativeFunctionUnitClassification(
            "http.ambiguous",
            "read_only",
            "read",
            False,
            "boundary.inspect",
        )
    if method in _READ_METHODS:
        return NativeFunctionUnitClassification(
            "http.read", "read_only", "read", False, "entity.query"
        )
    if method in _DELETE_METHODS:
        return NativeFunctionUnitClassification(
            "http.archive", "event_sourced", "archive", True, "entity.archive"
        )
    if method == "POST":
        return NativeFunctionUnitClassification(
            "http.create", "event_sourced", "upsert", True, "entity.upsert"
        )
    if method in {"PUT", "PATCH"}:
        return NativeFunctionUnitClassification(
            "http.update", "event_sourced", "upsert", True, "entity.upsert"
        )
    if method in _WRITE_METHODS:
        return NativeFunctionUnitClassification(
            "http.command", "event_sourced", "invoke", True, "boundary.invoke"
        )
    if _starts_with(name, _ARCHIVE_PREFIXES) or _starts_with(
        operation, _ARCHIVE_PREFIXES
    ):
        return NativeFunctionUnitClassification(
            "http.archive", "event_sourced", "archive", True, "entity.archive"
        )
    if _starts_with(name, _READ_PREFIXES) or _starts_with(operation, _READ_PREFIXES):
        return NativeFunctionUnitClassification(
            "http.read", "read_only", "read", False, "entity.query"
        )
    if external_gate:
        return NativeFunctionUnitClassification(
            "http.configure",
            "configuration_gate",
            "configure",
            True,
            "configuration.apply",
        )
    return NativeFunctionUnitClassification(
        "http.command", "event_sourced", "invoke", True, "boundary.invoke"
    )


__all__ = [
    "NativeFunctionUnitClassification",
    "RuntimeMode",
    "classify_native_function_unit",
]
