from __future__ import annotations

import ast
import logging
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.agents.stubs import klm_sdr
from app.ai.base import AICompletionResult
from app.core.config import settings
from app.core.logging_privacy import RedactSensitiveUrlParameters, opaque_ref, safe_error_type

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"
SENSITIVE_LOG_FIELDS = {
    "body",
    "case_number",
    "client_ip",
    "content",
    "cpf",
    "doc_token",
    "email",
    "error",
    "message",
    "name",
    "payload",
    "phone",
    "prompt",
    "response",
    "s3_key",
    "subject",
    "title",
    "to",
    "token",
    "workspace_slug",
}
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}


def _is_logger_call(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Attribute) or node.func.attr not in LOG_METHODS:
        return False
    owner = node.func.value
    if isinstance(owner, ast.Name):
        return owner.id == "logger"
    return (
        isinstance(owner, ast.Attribute)
        and owner.attr == "logger"
        and isinstance(owner.value, ast.Name)
        and owner.value.id == "self"
    )


def _renders_raw_exception(node: ast.AST) -> bool:
    exception_names = {"e", "err", "error", "exc", "exception"}
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in {"opaque_ref", "safe_error_type"}:
            return False
        if isinstance(node.func, ast.Name) and node.func.id in {"repr", "str"}:
            return any(
                isinstance(child, ast.Name) and child.id in exception_names
                for child in ast.walk(node)
            )
    if isinstance(node, ast.Attribute) and node.attr in {"message", "traceback"}:
        return True
    if isinstance(node, ast.Name) and node.id in exception_names:
        return True
    if isinstance(node, ast.JoinedStr):
        return any(_renders_raw_exception(value) for value in node.values)
    if isinstance(node, ast.FormattedValue):
        return _renders_raw_exception(node.value)
    return False


def test_opaque_refs_are_keyed_stable_and_domain_separated(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "unit-test-reference-secret")
    raw = "ana.silva@example.test"

    email_ref = opaque_ref(raw, kind="email")

    assert email_ref == opaque_ref(raw, kind="email")
    assert email_ref != opaque_ref(raw, kind="phone")
    assert raw not in email_ref
    assert email_ref.startswith("email:")
    assert opaque_ref(None, kind="email") is None
    assert opaque_ref(raw, kind="../../unsafe").startswith("value:")


def test_safe_error_type_never_exposes_exception_text():
    secret = "CPF 123.456.789-00 and token sk-sensitive"

    assert safe_error_type(ValueError(secret)) == "ValueError"
    assert secret not in safe_error_type(ValueError(secret))

    unsafe_exception = type("email@example.test", (Exception,), {})
    assert safe_error_type(unsafe_exception(secret)) == "UnknownError"


def test_http_client_log_filter_redacts_oauth_query_credentials():
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        (
            "HTTP Request: GET https://graph.facebook.com/oauth/access_token"
            "?client_secret=super-secret&code=raw-code&access_token=raw-token "
            '"HTTP/1.1 200 OK"'
        ),
        (),
        None,
    )

    assert RedactSensitiveUrlParameters().filter(record) is True
    rendered = record.getMessage()
    assert "super-secret" not in rendered
    assert "raw-code" not in rendered
    assert "raw-token" not in rendered
    assert rendered.count("[redacted]") == 3


def test_allowed_logger_calls_reject_sensitive_fields_and_tracebacks():
    violations: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_logger_call(node):
                continue
            method = node.func.attr
            if method == "exception":
                violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: logger.exception")
            if not node.args or not isinstance(node.args[0], ast.Constant):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: dynamic event")
            for argument in node.args[1:]:
                if _renders_raw_exception(argument):
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: raw exception text"
                    )
            for keyword in node.keywords:
                if keyword.arg in SENSITIVE_LOG_FIELDS:
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {keyword.arg}"
                    )
                if keyword.arg in {"exc_info", "stack_info"}:
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {keyword.arg}"
                    )

    assert violations == []


@pytest.mark.asyncio
async def test_sdr_parse_failure_does_not_log_completion_text(monkeypatch):
    completion_text = "OCR/RAG CPF 123.456.789-00 email ana@example.test"
    provider = Mock()
    provider.complete = AsyncMock(return_value=AICompletionResult(text=completion_text))
    private_logger = Mock()
    monkeypatch.setattr(klm_sdr, "get_provider_for_task", lambda _complexity: provider)
    monkeypatch.setattr(klm_sdr, "logger", private_logger)

    with pytest.raises(klm_sdr.SDRQualificationParseError) as exc_info:
        await klm_sdr._qualify_lead(
            [],
            klm_sdr.SDRQualification(),
            workspace_id=uuid4(),
        )

    private_logger.error.assert_called_once_with(
        "sdr.qualification_parse_error",
        error_type="JSONDecodeError",
    )
    assert completion_text not in repr(private_logger.error.call_args)
    # Fail-closed: a razão exposta ao humano também não vaza o texto do modelo.
    assert completion_text not in str(exc_info.value)
