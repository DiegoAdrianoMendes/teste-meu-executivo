from __future__ import annotations

import json

from app.ai.tool_results import ToolResultCompressor


def test_tool_result_compression_is_deterministic_and_redacts_secrets() -> None:
    compressor = ToolResultCompressor(max_output_bytes=2_000)
    payload = {
        "items": [{"id": index, "value": "x" * 20} for index in range(3)],
        "access_token": "must-not-leak",
    }

    first = compressor.compress(payload)
    second = compressor.compress(payload)

    assert first == second
    assert "must-not-leak" not in first.rendered
    assert json.loads(first.rendered)["access_token"] == "[REDACTED]"
    assert first.receipt.redacted_fields == 1


def test_tool_result_compression_falls_back_to_content_addressed_receipt() -> None:
    payload = {"rows": [{"text": "large" * 1_000} for _ in range(20)]}

    result = ToolResultCompressor(
        max_output_bytes=180,
        max_string_chars=10_000,
    ).compress(payload)

    rendered = json.loads(result.rendered)
    assert rendered["_compressed"] is True
    assert rendered["reason"] == "byte_limit"
    assert result.receipt.truncated is True
    assert result.receipt.output_bytes <= 180
