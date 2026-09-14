from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.main import app
from app.models.auth_security import AdministrativeSession, AuthSecurityEvent, MfaVerificationMethod
from app.models.error_log import ErrorLog, ErrorSeverity, ErrorStatus
from app.models.identity import UserSession
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.platform_incident import PlatformIncident
from app.services.platform_audit_event_service import PlatformAuditEventService


def test_governance_mutations_publish_canonical_snapshot_contracts() -> None:
    operations = app.openapi()["paths"]
    expected = {
        "/api/v1/platform/governance/feature-flags/{key}": "platform_configuration",
        "/api/v1/platform/governance/security/sessions/{session_id}": "user_session",
        "/api/v1/platform/governance/security/incidents": "platform_incident",
        "/api/v1/platform/governance/releases/records": "platform_release",
        "/api/v1/platform/governance/{module}": "platform_configuration",
        "/api/v1/platform/governance/{module}/rollback/{version}": "platform_configuration",
    }
    for path, adapter in expected.items():
        method = next(
            candidate
            for candidate in ("post", "put", "patch", "delete")
            if candidate in operations[path]
        )
        assert operations[path][method]["x-platform-mutation"]["snapshotAdapter"] == adapter


@pytest.mark.asyncio
async def test_session_manifest_exposes_only_safe_root_direct_metadata(
    db_session,
    platform_client,
    platform_auth_context,
) -> None:
    administrative_session = AdministrativeSession(
        user_id=platform_auth_context.user_id,
        token_jti=f"manifest-root-direct:{uuid4()}",
        mfa_method=MfaVerificationMethod.challenge,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    db_session.add(administrative_session)
    await db_session.flush()
    platform_auth_context.workspace_id = uuid4()
    platform_auth_context.administrative_session_id = administrative_session.id
    platform_auth_context.administrative_session_type = "root_direct"

    response = await platform_client.get("/api/v1/platform/session-manifest")
    assert response.status_code == 200, response.text
    session = response.json()["supportSession"]
    assert session == {
        "id": str(administrative_session.id),
        "type": "root_direct",
        "workspaceId": str(platform_auth_context.workspace_id),
        "expiresAt": administrative_session.expires_at.isoformat().replace("+00:00", "Z"),
        "scopes": [],
    }
    serialized = str(session).lower()
    assert "token" not in serialized
    assert "mfa" not in serialized


@pytest.mark.asyncio
async def test_release_audit_preserves_request_correlation(platform_client, db_session) -> None:
    correlation = f"release-create-{uuid4()}"
    created = await platform_client.post(
        "/api/v1/platform/governance/releases/records",
        headers={"X-Correlation-ID": correlation},
        json={
            "version": f"2026.07.{uuid4().hex[:6]}",
            "environment": "local",
            "status": "active",
            "source": "blackbox-test",
            "reason": "Registro local da release para auditoria.",
        },
    )
    assert created.status_code == 201, created.text
    event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "platform_release",
            PlatformAuditEvent.resource_id == created.json()["id"],
        )
    )
    assert event is not None
    assert event.correlation_id == correlation
    assert event.route == "/api/v1/platform/governance/releases/records"
    assert event.method == "POST"
    assert event.before is None
    assert event.after["revision"] == 1

    page = await platform_client.get(
        "/api/v1/platform/governance/releases/records/page",
        params={"limit": 1, "offset": 0, "environment": "local"},
    )
    assert page.status_code == 200, page.text
    payload = page.json()
    assert payload["limit"] == 1
    assert payload["offset"] == 0
    assert payload["total"] >= 1
    assert len(payload["items"]) == 1
    assert isinstance(payload["hasMore"], bool)
    assert payload["sort"][-1] == {"field": "id", "direction": "desc"}


@pytest.mark.asyncio
async def test_root_versions_governance_controls(platform_client, db_session):
    created = await platform_client.put(
        "/api/v1/platform/governance/feature_flags",
        json={"payload": {"flags": {"newInbox": False}}, "reason": "Estado inicial"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["namespace"] == "blackbox.feature_flags"
    assert created.json()["currentVersion"] == 1

    updated = await platform_client.put(
        "/api/v1/platform/governance/feature_flags",
        headers={"If-Match": f'"{created.json()["etag"]}"'},
        json={"payload": {"flags": {"newInbox": True}}, "reason": "Liberação controlada"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["currentVersion"] == 2

    manifest = await platform_client.get("/api/v1/platform/session-manifest")
    assert manifest.status_code == 200
    assert manifest.json()["featureFlags"] == {"newInbox": True}

    catalog = await platform_client.get(
        "/api/v1/platform/governance/feature-flags/catalog"
    )
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["flags"] == {"newInbox": True}
    toggled = await platform_client.patch(
        "/api/v1/platform/governance/feature-flags/newInbox",
        json={
            "enabled": False,
            "reason": "Rollback operacional",
            "expectedEtag": catalog.json()["etag"],
        },
    )
    assert toggled.status_code == 200, toggled.text
    assert toggled.json()["flags"] == {"newInbox": False}
    assert toggled.json()["currentVersion"] == 3

    stale = await platform_client.patch(
        "/api/v1/platform/governance/feature-flags/newInbox",
        json={
            "enabled": True,
            "reason": "Concorrência simulada",
            "expectedEtag": catalog.json()["etag"],
        },
    )
    assert stale.status_code == 412

    history = await platform_client.get(
        "/api/v1/platform/governance/feature_flags/versions"
    )
    assert history.status_code == 200
    assert [item["version"] for item in history.json()] == [3, 2, 1]

    page = await platform_client.get(
        "/api/v1/platform/governance/feature_flags/versions/page",
        params={"limit": 2, "offset": 1},
    )
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert page.json()["hasMore"] is False
    assert [item["version"] for item in page.json()["items"]] == [2, 1]

    audit = await platform_client.get(
        "/api/v1/platform/governance/audit-events?search=platform_mutation"
    )
    assert audit.status_code == 200
    assert audit.json()["count"] >= 2
    assert all(item["details"]["correlation_id"] for item in audit.json()["items"])
    assert all(item["details"]["is_platform_root"] is True for item in audit.json()["items"])
    canonical = list(await db_session.scalars(
        select(PlatformAuditEvent)
        .where(PlatformAuditEvent.resource_id == "blackbox.feature_flags")
        .order_by(PlatformAuditEvent.occurred_at.asc())
    ))
    assert [event.action for event in canonical] == [
        "put_governance_document",
        "put_governance_document",
        "update_platform_feature_flag",
    ]
    assert canonical[-1].before["payload"]["flags"]["newInbox"] is True
    assert canonical[-1].after["payload"]["flags"]["newInbox"] is False
    assert "payload.flags.newInbox" in canonical[-1].changed_paths


@pytest.mark.asyncio
async def test_governance_uses_module_specific_rbac(
    platform_client,
    platform_auth_context,
):
    created = await platform_client.post(
        "/api/v1/platform/staff",
        json={
            "name": "Compliance SaaS",
            "email": f"compliance-{uuid4().hex[:8]}@example.com",
            "rolePackage": "compliance_security",
        },
    )
    assert created.status_code == 201, created.text
    platform_auth_context.user_id = UUID(created.json()["id"])

    security = await platform_client.get("/api/v1/platform/governance/security")
    integrations = await platform_client.get("/api/v1/platform/governance/integrations")

    assert security.status_code == 404
    assert security.json()["detail"]["code"] == "GOV-004"
    assert integrations.status_code == 403
    assert integrations.json()["detail"]["code"] == "PLATFORM-PERMISSION-DENIED"

    denied_mutation = await platform_client.put(
        "/api/v1/platform/governance/integrations",
        json={"payload": {"enabled": True}, "reason": "Tentativa sem autoridade"},
    )
    assert denied_mutation.status_code == 403
    denied_events = await platform_client.get(
        "/api/v1/platform/governance/audit-events?outcome=denied"
    )
    assert denied_events.status_code == 200
    assert any(
        item["details"]["error_code"] == "PLATFORM-PERMISSION-DENIED"
        for item in denied_events.json()["items"]
    )


@pytest.mark.asyncio
async def test_unknown_governance_module_is_not_a_permission_escape(platform_client):
    response = await platform_client.get("/api/v1/platform/governance/not-real")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "GOV-001"

    invalid_flags = await platform_client.put(
        "/api/v1/platform/governance/feature_flags",
        json={"payload": {"flags": {"unsafe": "yes"}}, "reason": "Tipo inválido"},
    )
    assert invalid_flags.status_code == 422
    assert invalid_flags.json()["detail"]["code"] == "GOV-FLAG-003"


@pytest.mark.asyncio
async def test_security_center_lists_masks_and_revokes_sessions(
    platform_client,
    platform_auth_context,
    db_session,
):
    created = await platform_client.post(
        "/api/v1/platform/staff",
        json={
            "name": "Compliance Operacional",
            "email": f"security-{uuid4().hex[:8]}@example.com",
            "rolePackage": "compliance_security",
        },
    )
    assert created.status_code == 201, created.text
    user_id = UUID(created.json()["id"])
    session = UserSession(
        user_id=user_id,
        refresh_jti_digest=uuid4().hex + uuid4().hex,
        device_label="Chrome em macOS",
        device_type="desktop",
        browser="Chrome",
        operating_system="macOS",
        last_seen_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    root_view = await platform_client.get("/api/v1/platform/governance/security/overview")
    assert root_view.status_code == 200, root_view.text
    root_item = next(item for item in root_view.json()["sessions"] if item["id"] == str(session.id))
    assert root_item["userEmail"] == created.json()["email"]

    platform_auth_context.user_id = user_id
    staff_view = await platform_client.get("/api/v1/platform/governance/security/overview")
    assert staff_view.status_code == 200, staff_view.text
    staff_item = next(item for item in staff_view.json()["sessions"] if item["id"] == str(session.id))
    assert staff_item["userEmail"] != created.json()["email"]
    assert "***@" in staff_item["userEmail"]

    revoked = await platform_client.delete(
        f"/api/v1/platform/governance/security/sessions/{session.id}"
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json() == {"ok": True, "revoked": True}
    await db_session.refresh(session)
    assert session.revoked_at is not None
    canonical = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.action == "revoke_platform_security_session",
            PlatformAuditEvent.resource_id == str(session.id),
        )
    )
    assert canonical is not None
    assert canonical.before["revokedAt"] is None
    assert canonical.after["revokedAt"] is not None
    assert "revokedAt" in canonical.changed_paths


@pytest.mark.asyncio
async def test_security_sessions_cursor_preserves_equal_timestamps(
    platform_client,
    db_session,
):
    created = await platform_client.post(
        "/api/v1/platform/staff",
        json={
            "name": "Operador de sessões paginadas",
            "email": f"sessions-{uuid4().hex[:8]}@example.com",
            "rolePackage": "compliance_security",
        },
    )
    assert created.status_code == 201, created.text
    user_id = UUID(created.json()["id"])
    shared_timestamp = datetime.now(UTC)
    inserted_sessions: list[UserSession] = []
    for index in range(55):
        session = UserSession(
            user_id=user_id,
            refresh_jti_digest=uuid4().hex + uuid4().hex,
            device_label=f"Dispositivo {index}",
            device_type="desktop",
            browser="Chrome",
            operating_system="macOS",
            last_seen_at=shared_timestamp,
            expires_at=shared_timestamp + timedelta(days=7),
        )
        db_session.add(session)
        inserted_sessions.append(session)
    await db_session.flush()
    session_ids = {str(session.id) for session in inserted_sessions}
    await db_session.commit()

    seen: set[str] = set()
    cursor = None
    page_sizes = []
    while True:
        response = await platform_client.get(
            "/api/v1/platform/governance/security/overview",
            params={"limit": 25, **({"cursor": cursor} if cursor else {})},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        ids = {item["id"] for item in payload["sessions"]}
        assert not seen.intersection(ids)
        seen.update(ids)
        page_sizes.append(len(ids))
        if not payload["hasMore"]:
            break
        cursor = payload["nextCursor"]
        assert cursor

    assert session_ids.issubset(seen)
    assert all(size <= 25 for size in page_sizes)

    invalid = await platform_client.get(
        "/api/v1/platform/governance/security/overview",
        params={"cursor": "cursor-invalido"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "SECURITY-SESSION-CURSOR-INVALID"


@pytest.mark.asyncio
async def test_integrations_overview_distinguishes_runtime_from_credentials(
    platform_client,
    monkeypatch,
):
    from app.api.v1.endpoints import platform_governance

    monkeypatch.setattr(platform_governance.settings, "EMAIL_TRANSPORT", "ses")
    monkeypatch.setattr(
        platform_governance.settings,
        "EMAIL_FROM",
        "noreply@meuexecutivo.com",
    )
    response = await platform_client.get(
        "/api/v1/platform/governance/integrations/overview"
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    providers = {item["key"]: item for item in payload["providers"]}
    assert providers["webchat"]["state"] == "available"
    assert providers["webchat"]["evidenceLevel"] == "local_runtime"
    assert providers["instagram"]["state"] == "not_configured"
    assert providers["instagram"]["evidenceLevel"] == "none"
    assert (
        providers["transactional_email"]["label"]
        == "E-mail transacional (Amazon SES)"
    )
    assert providers["transactional_email"]["state"] == "credentials_configured"
    assert providers["transactional_email"]["evidenceLevel"] == "runtime_configuration"
    assert "sandbox" in providers["transactional_email"]["detail"]
    assert payload["summary"]["total"] == len(payload["providers"])


@pytest.mark.asyncio
async def test_integrations_overview_reports_resend_only_when_selected_and_configured(
    platform_client,
    monkeypatch,
):
    from app.api.v1.endpoints import platform_governance

    monkeypatch.setattr(
        platform_governance.settings,
        "EMAIL_TRANSPORT",
        "resend",
    )
    monkeypatch.setattr(platform_governance.settings, "RESEND_API_KEY", "re_test_key")

    response = await platform_client.get(
        "/api/v1/platform/governance/integrations/overview"
    )

    assert response.status_code == 200, response.text
    providers = {item["key"]: item for item in response.json()["providers"]}
    assert providers["transactional_email"] == {
        "key": "transactional_email",
        "label": "E-mail transacional (Resend)",
        "state": "credentials_configured",
        "evidenceLevel": "credential_presence",
        "detail": "Credencial Resend presente; envio externo não verificado por esta leitura.",
    }


@pytest.mark.asyncio
async def test_operational_governance_overviews_separate_facts_from_policy(platform_client):
    compliance = await platform_client.get(
        "/api/v1/platform/governance/compliance/overview"
    )
    assert compliance.status_code == 200, compliance.text
    assert compliance.json()["module"] == "compliance"
    assert {item["key"] for item in compliance.json()["metrics"]} >= {
        "audit_events", "denied_failed", "policy_versions"
    }

    data = await platform_client.get(
        "/api/v1/platform/governance/data_governance/overview"
    )
    assert data.status_code == 200, data.text
    assert data.json()["state"] == "insufficient_data"
    assert next(item for item in data.json()["metrics"] if item["key"] == "objects")["value"] == 0

    releases = await platform_client.get(
        "/api/v1/platform/governance/releases/overview"
    )
    assert releases.status_code == 200, releases.text
    assert releases.json()["state"] == "not_configured"
    assert next(item for item in releases.json()["metrics"] if item["key"] == "rollback")["value"] == "Ausente"

    unsupported = await platform_client.get(
        "/api/v1/platform/governance/audit/overview"
    )
    assert unsupported.status_code == 404
    assert unsupported.json()["detail"]["code"] == "GOV-OVERVIEW-001"


@pytest.mark.asyncio
async def test_audit_center_sanitizes_secrets_for_root_and_non_root(
    platform_client,
    platform_auth_context,
    db_session,
):
    db_session.add(AuthSecurityEvent(
        event_type="platform_test_mutation",
        outcome="success",
        actor_user_id=platform_auth_context.user_id,
        details={
            "workspace_id": str(uuid4()),
            "access_token": "never-expose",
            "status": "completed",
            "fields": ["status"],
            "nested": {
                "password": "also-never-expose",
                "cookies": ["session-cookie", "refresh-cookie"],
                "mfa": {
                    "enabled": True,
                    "method": "totp",
                    "secret": "totp-secret",
                    "recovery_codes": ["recovery-1", "recovery-2"],
                },
                "credentials": {
                    "provider": "meta",
                    "configured": True,
                    "client_secret": "client-secret",
                    "api_key": "api-key",
                },
            },
        },
    ))
    await db_session.commit()

    root_view = await platform_client.get(
        "/api/v1/platform/governance/audit-events?search=platform_test_mutation"
    )
    assert root_view.status_code == 200, root_view.text
    assert root_view.json()["count"] == 1
    assert root_view.json()["items"][0]["isPlatformRoot"] is True
    root_details = root_view.json()["items"][0]["details"]
    assert root_details["access_token"] == "[mascarado]"
    assert root_details["status"] == "completed"
    assert root_details["fields"] == ["status"]
    assert root_details["nested"]["password"] == "[mascarado]"
    assert root_details["nested"]["cookies"] == ["[mascarado]", "[mascarado]"]
    assert root_details["nested"]["mfa"] == {
        "enabled": True,
        "method": "totp",
        "secret": "[mascarado]",
        "recovery_codes": ["[mascarado]", "[mascarado]"],
    }
    assert root_details["nested"]["credentials"] == {
        "provider": "meta",
        "configured": True,
        "client_secret": "[mascarado]",
        "api_key": "[mascarado]",
    }

    created = await platform_client.post(
        "/api/v1/platform/staff",
        json={
            "name": "Auditora SaaS",
            "email": f"audit-{uuid4().hex[:8]}@example.com",
            "rolePackage": "compliance_security",
        },
    )
    assert created.status_code == 201, created.text
    platform_auth_context.user_id = UUID(created.json()["id"])
    staff_view = await platform_client.get(
        "/api/v1/platform/governance/audit-events?eventType=platform_test_mutation"
    )
    assert staff_view.status_code == 200
    staff_details = staff_view.json()["items"][0]["details"]
    assert staff_details == root_details


@pytest.mark.asyncio
async def test_logs_center_reads_persisted_operational_failures(platform_client, db_session):
    db_session.add(ErrorLog(
        error_code="ERR-BBX-001",
        error_hash="a" * 64,
        severity=ErrorSeverity.high,
        status=ErrorStatus.open,
        exception_type="ProviderTimeout",
        message="Provedor não respondeu dentro do prazo.",
        endpoint="/api/v1/platform/inbox/messages",
        method="POST",
        request_id="req-blackbox-1",
        occurrences=3,
    ))
    await db_session.commit()

    response = await platform_client.get(
        "/api/v1/platform/governance/operational-logs?severity=high&search=req-blackbox-1"
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    assert response.json()["items"][0]["errorCode"] == "ERR-BBX-001"
    assert response.json()["items"][0]["occurrences"] == 3


@pytest.mark.asyncio
async def test_operational_logs_use_collision_safe_cursor(platform_client, db_session):
    timestamp = datetime(2026, 7, 28, 13, 0, tzinfo=UTC)
    for index in range(55):
        db_session.add(ErrorLog(
            error_code=f"ERR-PAGE-{index:03d}",
            error_hash=f"{index:064x}",
            severity=ErrorSeverity.medium,
            status=ErrorStatus.open,
            exception_type="CursorExercise",
            message=f"Falha paginada {index}",
            last_seen_at=timestamp,
        ))
    await db_session.commit()

    first = await platform_client.get(
        "/api/v1/platform/governance/operational-logs?limit=25&search=CursorExercise"
    )
    second = await platform_client.get(
        "/api/v1/platform/governance/operational-logs",
        params={"limit": 25, "search": "CursorExercise", "cursor": first.json()["nextCursor"]},
    )
    third = await platform_client.get(
        "/api/v1/platform/governance/operational-logs",
        params={"limit": 25, "search": "CursorExercise", "cursor": second.json()["nextCursor"]},
    )
    assert first.status_code == second.status_code == third.status_code == 200
    ids = [
        item["id"]
        for page in (first.json(), second.json(), third.json())
        for item in page["items"]
    ]
    assert len(ids) == 55
    assert len(set(ids)) == 55
    assert third.json()["hasMore"] is False

    invalid = await platform_client.get(
        "/api/v1/platform/governance/operational-logs?cursor=not-a-cursor"
    )
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_root_opens_tracks_and_resolves_platform_incident(platform_client, db_session):
    created = await platform_client.post(
        "/api/v1/platform/governance/security/incidents",
        json={
            "title": "Fila de mensagens acumulando",
            "summary": "Mensagens aguardando worker acima da janela operacional.",
            "severity": "sev2",
            "components": ["workers", "inbox", "workers"],
        },
    )
    assert created.status_code == 201, created.text
    incident_id = created.json()["id"]
    assert created.json()["status"] == "investigating"
    assert created.json()["components"] == ["inbox", "workers"]

    listed = await platform_client.get(
        "/api/v1/platform/governance/security/incidents"
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["openCount"] == 1
    assert listed.json()["items"][0]["id"] == incident_id
    assert listed.json()["hasMore"] is False

    monitoring = await platform_client.patch(
        f"/api/v1/platform/governance/security/incidents/{incident_id}",
        json={"status": "monitoring", "note": "Worker reiniciado; drenagem em observação."},
    )
    assert monitoring.status_code == 200, monitoring.text
    assert monitoring.json()["status"] == "monitoring"
    assert len(monitoring.json()["updates"]) == 2

    resolved = await platform_client.patch(
        f"/api/v1/platform/governance/security/incidents/{incident_id}",
        json={"status": "resolved", "note": "Fila drenada e alerta confirmado."},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["resolvedAt"] is not None

    audit = await platform_client.get(
        "/api/v1/platform/governance/audit-events?search=security%2Fincidents"
    )
    assert audit.status_code == 200
    assert audit.json()["count"] >= 3
    assert all(item["isPlatformRoot"] is True for item in audit.json()["items"])
    canonical = list(await db_session.scalars(
        select(PlatformAuditEvent)
        .where(PlatformAuditEvent.resource_id == incident_id)
        .order_by(PlatformAuditEvent.occurred_at.asc())
    ))
    assert [event.action for event in canonical] == [
        "create_platform_incident",
        "update_platform_incident",
        "update_platform_incident",
    ]
    assert canonical[1].before["status"] == "investigating"
    assert canonical[1].after["status"] == "monitoring"
    assert canonical[2].after["status"] == "resolved"


@pytest.mark.asyncio
async def test_incidents_use_collision_safe_cursor_pages(
    platform_client,
    db_session,
    platform_auth_context,
):
    started_at = datetime.now(UTC)
    db_session.add_all([
        PlatformIncident(
            title=f"Incidente paginado {index:02d}",
            summary="Evidência de paginação cursor.",
            severity="sev3",
            status="investigating",
            components=[],
            created_by_user_id=platform_auth_context.user_id,
            updates=[],
            started_at=started_at,
        )
        for index in range(55)
    ])
    await db_session.commit()

    first = await platform_client.get(
        "/api/v1/platform/governance/security/incidents",
        params={"limit": 25},
    )
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert first_payload["openCount"] == 55
    assert first_payload["hasMore"] is True
    assert len(first_payload["items"]) == 25

    second = await platform_client.get(
        "/api/v1/platform/governance/security/incidents",
        params={"limit": 25, "cursor": first_payload["nextCursor"]},
    )
    second_payload = second.json()
    assert second.status_code == 200, second.text
    assert second_payload["hasMore"] is True
    assert len(second_payload["items"]) == 25
    assert {item["id"] for item in first_payload["items"]}.isdisjoint(
        {item["id"] for item in second_payload["items"]}
    )

    last = await platform_client.get(
        "/api/v1/platform/governance/security/incidents",
        params={"limit": 25, "cursor": second_payload["nextCursor"]},
    )
    assert last.status_code == 200, last.text
    assert len(last.json()["items"]) == 5
    assert last.json()["hasMore"] is False
    invalid = await platform_client.get(
        "/api/v1/platform/governance/security/incidents",
        params={"cursor": "not-a-cursor"},
    )
    assert invalid.status_code == 422

@pytest.mark.asyncio
async def test_incident_update_rolls_back_when_canonical_audit_fails(
    platform_client,
    db_session,
    monkeypatch,
):
    created = await platform_client.post(
        "/api/v1/platform/governance/security/incidents",
        json={
            "title": "Incidente para rollback",
            "summary": "Estado inicial preservado quando a auditoria falhar.",
            "severity": "sev3",
        },
    )
    assert created.status_code == 201, created.text
    incident_id = created.json()["id"]

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.patch(
            f"/api/v1/platform/governance/security/incidents/{incident_id}",
            json={
                "status": "resolved",
                "note": "Mudança que deve ser revertida integralmente.",
            },
        )

    db_session.expire_all()
    incident = await db_session.get(PlatformIncident, incident_id)
    assert incident is not None
    assert incident.status == "investigating"
    assert incident.resolved_at is None
