"""Contrato HTTP do Audit Center canônico."""

from uuid import uuid4

import pytest

from app.models.platform_audit_event import PlatformAuditEvent
from app.models.user import User
from app.schemas.platform_audit_event import PlatformAuditEventCreate
from app.services.platform_audit_event_service import PlatformAuditEventService


@pytest.mark.asyncio
async def test_audit_center_returns_semantic_sanitized_field_diffs(
    platform_client,
    db_session,
    platform_auth_context,
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    correlation_id = f"audit-field-diff-{uuid4()}"
    await PlatformAuditEventService(db_session).record(
        PlatformAuditEventCreate(
            actor_id=actor.id,
            actor_role_snapshot=actor.platform_role_package or "platform_owner",
            is_platform_root=True,
            module="customers",
            action="access_package.changed",
            resource_type="customer_membership",
            resource_id=f"{uuid4()}:{uuid4()}",
            result="success",
            before={"accessPackage": "marketing", "accessToken": "old-secret"},
            after={"accessPackage": "office_admin", "accessToken": "new-secret"},
            reason="Validate the Audit Center semantic diff contract",
            reason_source="user",
            correlation_id=correlation_id,
            operation_id=str(uuid4()),
            route="test",
            method="PATCH",
        )
    )
    await db_session.commit()

    response = await platform_client.get(
        "/api/v1/platform/audit-events",
        params={
            "correlationId": correlation_id,
            "module": "customers",
            "resourceType": "customer_membership",
            "actorId": str(actor.id),
            "actorRole": actor.platform_role_package or "platform_owner",
            "action": "access_package.changed",
            "restorable": "true",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    event = payload["items"][0]
    assert [item["path"] for item in event["fieldDiffs"]] == [
        "accessPackage",
        "accessToken",
    ]
    assert event["fieldDiffs"][0]["label"] == "Pacote de acesso"
    assert event["fieldDiffs"][1]["sensitive"] is True
    assert event["restorable"] is True
    assert "old-secret" not in response.text
    assert "new-secret" not in response.text


@pytest.mark.asyncio
async def test_audit_center_redacts_legacy_rows_at_read_time(
    platform_client,
    db_session,
):
    correlation_id = f"legacy-audit-redaction-{uuid4()}"
    legacy = PlatformAuditEvent(
        actor_role_snapshot="platform_owner",
        is_platform_root=True,
        session_type="normal",
        module="integrations",
        action="legacy.imported",
        resource_type="integration",
        result="success",
        before={"note": "Bearer legacy-before-secret"},
        after={"url": "postgresql://user:legacy-after-secret@db/app"},
        changed_paths=["note", "url"],
        reason="Imported password=legacy-reason-secret",
        reason_source="system",
        correlation_id=correlation_id,
        operation_id=str(uuid4()),
        route="/legacy",
        method="POST",
    )
    db_session.add(legacy)
    await db_session.commit()

    response = await platform_client.get(
        "/api/v1/platform/audit-events",
        params={"correlationId": correlation_id},
    )

    assert response.status_code == 200
    assert "legacy-before-secret" not in response.text
    assert "legacy-after-secret" not in response.text
    assert "legacy-reason-secret" not in response.text
