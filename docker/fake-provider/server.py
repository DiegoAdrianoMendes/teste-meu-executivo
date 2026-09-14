from __future__ import annotations

import hashlib
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HOST = "0.0.0.0"
PORT = 8080
MAX_REQUEST_BYTES = 1_048_576
MODEL = os.getenv("KLM_FAKE_PROVIDER_MODEL", "klm-fake-v1")
RESPONSE_TEXT = os.getenv(
    "KLM_FAKE_PROVIDER_RESPONSE",
    "Local deterministic simulation.",
)


def _canonical_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _request_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_payload(payload)).hexdigest()


def _response_text(payload: dict[str, Any]) -> tuple[str, str]:
    digest = _request_digest(payload)
    return f"{RESPONSE_TEXT} request={digest[:12]}", digest


def build_openai_response(payload: dict[str, Any]) -> dict[str, Any]:
    text, digest = _response_text(payload)
    input_units = max(1, len(_canonical_payload(payload)) // 4)
    output_units = max(1, len(text.encode("utf-8")) // 4)
    return {
        "id": f"fake-{digest[:24]}",
        "object": "chat.completion",
        "created": 0,
        "model": str(payload.get("model") or MODEL),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": input_units,
            "completion_tokens": output_units,
            "total_tokens": input_units + output_units,
        },
    }


def build_anthropic_response(payload: dict[str, Any]) -> dict[str, Any]:
    text, digest = _response_text(payload)
    return {
        "id": f"fake-{digest[:24]}",
        "type": "message",
        "role": "assistant",
        "model": str(payload.get("model") or MODEL),
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": max(1, len(_canonical_payload(payload)) // 4),
            "output_tokens": max(1, len(text.encode("utf-8")) // 4),
        },
    }


def build_gemini_response(payload: dict[str, Any]) -> dict[str, Any]:
    text, _digest = _response_text(payload)
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": text}]},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": max(1, len(_canonical_payload(payload)) // 4),
            "candidatesTokenCount": max(1, len(text.encode("utf-8")) // 4),
        },
        "modelVersion": MODEL,
    }


class FakeProviderHandler(BaseHTTPRequestHandler):
    server_version = "KLMFakeProvider/1.0"

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = _canonical_payload(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in {"/health", "/ready"}:
            self._write_json(HTTPStatus.OK, {"status": "ok", "provider": "fake"})
            return
        if self.path == "/v1/models":
            self._write_json(
                HTTPStatus.OK,
                {"object": "list", "data": [{"id": MODEL, "object": "model"}]},
            )
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "route_not_found"})

    def do_POST(self) -> None:
        raw_length = self.headers.get("Content-Length", "")
        try:
            content_length = int(raw_length)
        except ValueError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"})
            return
        if content_length < 0 or content_length > MAX_REQUEST_BYTES:
            self._write_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "payload_too_large"})
            return

        try:
            payload = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return
        if not isinstance(payload, dict):
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "object_required"})
            return

        if self.path == "/v1/chat/completions":
            response = build_openai_response(payload)
        elif self.path == "/v1/messages":
            response = build_anthropic_response(payload)
        elif self.path.startswith("/v1beta/models/") and self.path.endswith(":generateContent"):
            response = build_gemini_response(payload)
        else:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "route_not_found"})
            return
        self._write_json(HTTPStatus.OK, response)

    def log_message(self, message: str, *args: object) -> None:
        super().log_message(message, *args)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), FakeProviderHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
