from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import uuid4

import pytest
from sqlalchemy.exc import StatementError

from app.schemas.platform_audit_event import PlatformAuditEventCreate
from app.services.platform_audit_event_service import (
    PlatformAuditEventService,
    changed_paths,
    is_audit_event_restorable,
    sanitize_audit_text,
    sanitize_audit_value,
    semantic_field_diff,
)


def test_sanitize_audit_text_redacts_free_text_credentials():
    raw = (
        "Bearer token-value-123 password=hunter22 "
        "postgresql://operator:super-secret@db.internal/app "
        "github_pat_123456789012345678901234567890"
    )

    result = sanitize_audit_text(raw)

    assert "token-value-123" not in result
    assert "hunter22" not in result
    assert "super-secret" not in result
    assert "github_pat_" not in result
    assert "postgresql://[REDACTED]@db.internal/app" in result


def test_sanitize_audit_text_redacts_quoted_json_and_text_tokens():
    json_token = "json-access-token-must-not-survive"
    text_token = "quoted-text-token-must-not-survive"
    raw = (
        f'{{"accessToken": "{json_token}", "status": "configured"}} '
        f"access_token='{text_token}'"
    )

    result = sanitize_audit_text(raw)

    assert json_token not in result
    assert text_token not in result
    assert '"accessToken": [REDACTED]' in result
    assert "access_token=[REDACTED]" in result


def test_sanitize_audit_value_never_keeps_nested_secret_values():
    result = sanitize_audit_value(
        {
            "provider": "meta",
            "credentials": {
                "access_token": "token-super-secreto",
                "client_secret": "client-super-secreto",
            },
            "status": "configured",
        }
    )
    serialized = str(result)
    assert "token-super-secreto" not in serialized
    assert "client-super-secreto" not in serialized
    assert result["provider"] == "meta"
    assert result["status"] == "configured"
    assert result["credentials"]["redacted"] is True


def test_sanitize_keeps_token_metrics_but_redacts_auth_tokens():
    result = sanitize_audit_value(
        {
            "tokenQuantity": 250,
            "tokenUsage": 125,
            "monthlyTokenLimit": 1_000,
            "maxTokensPerMonth": 2_000_000,
            "maxOutputTokens": 4_096,
            "accessToken": "access-secret",
            "refresh_token": "refresh-secret",
        }
    )
    assert result["tokenQuantity"] == 250
    assert result["tokenUsage"] == 125
    assert result["monthlyTokenLimit"] == 1_000
    assert result["maxTokensPerMonth"] == 2_000_000
    assert result["maxOutputTokens"] == 4_096
    assert result["accessToken"]["redacted"] is True
    assert result["refresh_token"]["redacted"] is True
    assert "access-secret" not in str(result)
    assert "refresh-secret" not in str(result)


def test_changed_paths_only_returns_semantically_changed_fields():
    assert changed_paths(
        {"name": "Conta", "limits": {"users": 10, "ai": 100}},
        {"name": "Conta", "limits": {"users": 20, "ai": 100}},
    ) == ["limits.users"]


def test_semantic_field_diff_has_human_labels_and_secret_metadata_only():
    before = sanitize_audit_value({
        "accessPackage": "marketing",
        "credentials": {"accessToken": "old-secret"},
        "status": "active",
    })
    after = sanitize_audit_value({
        "accessPackage": "office_admin",
        "credentials": {"accessToken": "new-secret"},
        "status": "active",
    })

    result = semantic_field_diff(before, after)

    assert [item["path"] for item in result] == [
        "accessPackage",
        "credentials",
    ]
    assert result[0]["label"] == "Pacote de acesso"
    assert result[0]["changeType"] == "changed"
    assert result[1]["sensitive"] is True
    assert "old-secret" not in str(result)
    assert "new-secret" not in str(result)


def test_sanitize_audit_value_normalizes_json_unsafe_domain_values():
    class State(str, Enum):
        ready = "ready"

    identifier = uuid4()
    observed_at = datetime(2026, 7, 28, 13, 44, 43, tzinfo=UTC)
    result = sanitize_audit_value(
        {
            "id": identifier,
            "observedAt": observed_at,
            "state": State.ready,
            "amount": Decimal("12.30"),
            "tags": {"beta", "alpha"},
            "binary": b"never-store-raw",
            "client": object(),
        }
    )

    assert result == {
        "id": str(identifier),
        "observedAt": observed_at.isoformat(),
        "state": "ready",
        "amount": "12.30",
        "tags": ["alpha", "beta"],
        "binary": {"type": "bytes", "length": 15, "redacted": True},
        "client": {"type": "object", "unsupported": True},
    }


@pytest.mark.asyncio
async def test_canonical_event_is_sanitized_and_append_only(db_session):
    event = await PlatformAuditEventService(db_session).record(
        PlatformAuditEventCreate(
            actor_role_snapshot="platform_owner",
            is_platform_root=True,
            module="integrations",
            action="credentials.updated",
            resource_type="integration",
            resource_id="meta",
            result="success",
            before={"accessToken": "old-secret", "status": "inactive"},
            after={"accessToken": "new-secret", "status": "active"},
            reason="Atualização controlada de credenciais",
            reason_source="user",
            correlation_id="corr-test",
            operation_id="op-test",
            route="/api/v1/platform/integrations/meta",
            method="PATCH",
        )
    )
    await db_session.commit()
    assert event.actor_id is None
    assert "old-secret" not in str(event.before)
    assert "new-secret" not in str(event.after)
    assert set(event.changed_paths) == {"accessToken", "status"}

    event.reason = "tentativa de reescrita"
    with pytest.raises((ValueError, StatementError)):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_canonical_event_sanitizes_reason_and_labels(db_session):
    event = await PlatformAuditEventService(db_session).record(
        PlatformAuditEventCreate(
            actor_role_snapshot="platform_owner",
            is_platform_root=True,
            workspace_label_snapshot="Conta password=workspace-secret",
            resource_label_snapshot="Meta Bearer resource-secret",
            module="integrations",
            action="credentials.updated",
            resource_type="integration",
            result="success",
            reason="Rotated client_secret=reason-secret",
            correlation_id="corr-text-redaction",
            operation_id="op-text-redaction",
            route="/test",
            method="PATCH",
        )
    )

    assert "workspace-secret" not in (event.workspace_label_snapshot or "")
    assert "resource-secret" not in (event.resource_label_snapshot or "")
    assert "reason-secret" not in event.reason


@pytest.mark.asyncio
async def test_restorability_requires_snapshot_and_reversible_action(db_session):
    service = PlatformAuditEventService(db_session)
    reversible = await service.record(PlatformAuditEventCreate(
        actor_role_snapshot="platform_owner",
        module="customers",
        action="access_package.changed",
        resource_type="customer_membership",
        resource_id=f"{uuid4()}:{uuid4()}",
        result="success",
        before={"accessPackage": "marketing"},
        after={"accessPackage": "office_admin"},
        reason="Reversible event",
        correlation_id=str(uuid4()),
        operation_id=str(uuid4()),
        route="test",
        method="PATCH",
    ))
    session_revoke = await service.record(PlatformAuditEventCreate(
        actor_role_snapshot="platform_owner",
        module="customers",
        action="sessions.revoked",
        resource_type="customer_membership",
        resource_id=f"{uuid4()}:{uuid4()}",
        result="success",
        before={"activeSessions": 1},
        after={"activeSessions": 0},
        reason="Irreversible session revocation",
        correlation_id=str(uuid4()),
        operation_id=str(uuid4()),
        route="test",
        method="POST",
    ))

    assert is_audit_event_restorable(reversible) is True
    assert is_audit_event_restorable(session_revoke) is False

    items, _ = await service.list_events(restorable=True)
    assert reversible in items
    assert session_revoke not in items


@pytest.mark.asyncio
async def test_canonical_event_cursor_uses_timestamp_and_id(db_session):
    service = PlatformAuditEventService(db_session)
    module = f"test-{uuid4()}"
    for index in range(3):
        await service.record(
            PlatformAuditEventCreate(
                actor_role_snapshot="system",
                module=module,
                action=f"created.{index}",
                resource_type="fixture",
                resource_id=str(index),
                result="success",
                reason="Evento de teste gerado pelo sistema",
                correlation_id=f"corr-{index}",
                operation_id=f"op-{index}",
                route="/test",
                method="POST",
            )
        )
    await db_session.commit()
    first, cursor = await service.list_events(limit=2, module=module)
    assert len(first) == 2
    assert cursor
    second, next_cursor = await service.list_events(limit=2, cursor=cursor, module=module)
    assert len(second) == 1, {
        "first": [(str(item.id), item.occurred_at.isoformat()) for item in first],
        "second": [(str(item.id), item.occurred_at.isoformat()) for item in second],
    }
    assert next_cursor is None
    assert {item.id for item in first}.isdisjoint({item.id for item in second})
