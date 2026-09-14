"""Deterministic, receipt-backed compression for untrusted tool results."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

TOOL_RESULT_COMPRESSION_VERSION = "klm-tool-result-v1"
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|credential|password|private[_-]?key|secret|token)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ToolResultCompressionReceipt:
    version: str
    input_sha256: str
    output_sha256: str
    input_bytes: int
    output_bytes: int
    truncated: bool
    redacted_fields: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CompressedToolResult:
    rendered: str
    receipt: ToolResultCompressionReceipt


class ToolResultCompressor:
    def __init__(
        self,
        *,
        max_output_bytes: int = 12_000,
        max_depth: int = 6,
        max_items: int = 40,
        max_string_chars: int = 2_000,
    ) -> None:
        if min(max_output_bytes, max_depth, max_items, max_string_chars) <= 0:
            raise ValueError("Tool result compression limits must be positive.")
        self.max_output_bytes = max_output_bytes
        self.max_depth = max_depth
        self.max_items = max_items
        self.max_string_chars = max_string_chars

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )

    def compress(self, payload: Any) -> CompressedToolResult:
        original = self._json(payload)
        original_bytes = original.encode("utf-8")
        redactions = 0
        truncated = False

        def normalize(value: Any, depth: int) -> Any:
            nonlocal redactions, truncated
            if depth >= self.max_depth and isinstance(value, (dict, list, tuple)):
                truncated = True
                return {"_omitted": "depth_limit", "type": type(value).__name__}
            if isinstance(value, dict):
                normalized: dict[str, Any] = {}
                items = sorted(value.items(), key=lambda item: str(item[0]))
                for index, (raw_key, nested) in enumerate(items):
                    if index >= self.max_items:
                        truncated = True
                        normalized["_omitted_fields"] = len(items) - self.max_items
                        break
                    key = str(raw_key)
                    if _SENSITIVE_KEY.search(key):
                        normalized[key] = "[REDACTED]"
                        redactions += 1
                    else:
                        normalized[key] = normalize(nested, depth + 1)
                return normalized
            if isinstance(value, (list, tuple)):
                selected = list(value[: self.max_items])
                normalized_list = [normalize(item, depth + 1) for item in selected]
                if len(value) > self.max_items:
                    truncated = True
                    normalized_list.append({"_omitted_items": len(value) - self.max_items})
                return normalized_list
            if isinstance(value, str) and len(value) > self.max_string_chars:
                truncated = True
                return value[: self.max_string_chars] + "...[truncated]"
            if value is None or isinstance(value, (bool, int, float, str)):
                return value
            return str(value)

        normalized = normalize(payload, 0)
        rendered = self._json(normalized)
        if len(rendered.encode("utf-8")) > self.max_output_bytes:
            truncated = True
            rendered = self._json(
                {
                    "_compressed": True,
                    "input_sha256": hashlib.sha256(original_bytes).hexdigest(),
                    "input_type": type(payload).__name__,
                    "reason": "byte_limit",
                }
            )
        output_bytes = rendered.encode("utf-8")
        return CompressedToolResult(
            rendered=rendered,
            receipt=ToolResultCompressionReceipt(
                version=TOOL_RESULT_COMPRESSION_VERSION,
                input_sha256=hashlib.sha256(original_bytes).hexdigest(),
                output_sha256=hashlib.sha256(output_bytes).hexdigest(),
                input_bytes=len(original_bytes),
                output_bytes=len(output_bytes),
                truncated=truncated,
                redacted_fields=redactions,
            ),
        )
