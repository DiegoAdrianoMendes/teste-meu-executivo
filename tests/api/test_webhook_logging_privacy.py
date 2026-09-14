from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import Mock

import pytest

from app.api.v1.endpoints import webhooks as webhooks_module
from app.core.config import settings


@pytest.fixture(autouse=True)
def _allow_external_webhook_delivery(monkeypatch):
    monkeypatch.setattr(webhooks_module, "external_delivery_allowed", lambda: True)


@pytest.mark.asyncio
async def test_openclaw_missing_identifiers_does_not_log_raw_payload(client, monkeypatch):
    webhook_secret = "openclaw-test-secret"
    payload = {
        "workspace_slug": "workspace-teste",
        "description": "CPF 123.456.789-00",
        "email": "ana@example.test",
        "message": "confidential legal content",
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    delivery_id = "privacy-delivery"
    signature = hmac.new(
        webhook_secret.encode(),
        webhooks_module._openclaw_canonical_message(
            body,
            timestamp=timestamp,
            delivery_id=delivery_id,
            workspace_slug="workspace-teste",
        ),
        hashlib.sha256,
    ).hexdigest()
    private_logger = Mock()
    monkeypatch.setattr(settings, "OPENCLAW_WEBHOOK_SECRET", webhook_secret)
    monkeypatch.setattr(webhooks_module, "logger", private_logger)

    response = await client.post(
        "/api/v1/webhooks/openclaw",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-OpenClaw-Delivery": delivery_id,
            "X-OpenClaw-Signature": f"sha256={signature}",
            "X-OpenClaw-Timestamp": timestamp,
            "X-Workspace-Slug": "workspace-teste",
        },
    )

    assert response.status_code == 200
    private_logger.warning.assert_called_once_with(
        "openclaw_webhook_missing_case_number",
        has_case_number=False,
        has_workspace=True,
    )
    rendered_call = repr(private_logger.warning.call_args)
    assert "123.456.789-00" not in rendered_call
    assert "ana@example.test" not in rendered_call
    assert "confidential legal content" not in rendered_call


@pytest.mark.asyncio
async def test_zapsign_parse_failure_logs_only_safe_error_type(client, monkeypatch):
    private_logger = Mock()
    document_token = "doc-secret-token"
    monkeypatch.setattr(settings, "ZAPSIGN_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setattr(webhooks_module, "logger", private_logger)

    response = await client.post(
        "/api/v1/webhooks/zapsign",
        json={
            "event_type": "doc_signed",
            "token": document_token,
            "name": "Contrato CPF 123.456.789-00",
        },
        headers={"Authorization": "Bearer webhook-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "invalid_payload"}
    private_logger.warning.assert_called_once_with(
        "zapsign_webhook_parse_error",
        error_type="ValidationError",
    )
    assert document_token not in repr(private_logger.warning.call_args)
    assert "123.456.789-00" not in repr(private_logger.warning.call_args)


@pytest.mark.asyncio
async def test_zapsign_rejects_oversized_body_before_json_parsing(client):
    response = await client.post(
        "/api/v1/webhooks/zapsign",
        content=b"{" + (b"x" * (2 * 1024 * 1024)) + b"}",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_zapsign_accepts_only_json_objects(client, monkeypatch):
    private_logger = Mock()
    monkeypatch.setattr(webhooks_module, "logger", private_logger)

    response = await client.post(
        "/api/v1/webhooks/zapsign",
        content=b"[]",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "invalid_payload"}
    private_logger.warning.assert_called_once_with(
        "zapsign_webhook_parse_error",
        error_type="TypeError",
    )
