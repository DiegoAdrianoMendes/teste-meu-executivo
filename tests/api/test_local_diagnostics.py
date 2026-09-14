import json

import pytest
from sqlalchemy import select

from app.models.klm_operations import KlmAuditEvent
from app.services.klm_operations_service import KlmOperationsService


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _healthy_migrations(self):
    return {
        "status": "healthy",
        "state": "current",
        "detail": "Os heads Alembic aplicados correspondem ao código atual.",
        "evidence": {
            "currentHeads": ["96a7b8c9d0e1"],
            "appliedHeads": ["96a7b8c9d0e1"],
            "appliedHeadsAvailable": True,
        },
    }


def _checks_by_key(payload):
    return {check["key"]: check for check in payload["checks"]}


@pytest.mark.asyncio
async def test_sqlite_migration_diagnostics_validate_the_materialized_local_schema(
    db_session,
):
    result = await KlmOperationsService(db_session)._diagnose_migrations()

    assert result["status"] == "healthy"
    assert result["state"] == "local_schema_current"
    assert result["evidence"]["schemaManagedBy"] == "local_bootstrap"
    assert result["evidence"]["appliedHeadsAvailable"] is False
    assert result["evidence"]["missingTables"] == []
    assert result["evidence"]["missingColumns"] == {}


@pytest.mark.asyncio
async def test_local_diagnostics_returns_real_checks_and_audits_run(
    client,
    db_session,
    monkeypatch,
):
    async def healthy_oss_runtime_status(*, force_refresh=False):
        assert force_refresh is True
        return {
            "services": [
                {"state": "ready"},
                {"state": "ready"},
                {"state": "ready"},
                {"state": "ready"},
            ]
        }

    monkeypatch.setattr(
        "app.services.klm_operations_service.oss_runtime_status",
        healthy_oss_runtime_status,
    )
    monkeypatch.setattr(
        KlmOperationsService,
        "_diagnose_migrations",
        _healthy_migrations,
    )

    response = await client.post("/api/v1/klm/diagnostics")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "healthy"
    assert payload["summary"] == {
        "status": "healthy",
        "total": 6,
        "healthy": 6,
        "degraded": 0,
        "unavailable": 0,
    }
    checks = _checks_by_key(payload)
    assert set(checks) == {
        "database",
        "external_effects_gate",
        "aws_gate",
        "model_api_gate",
        "oss_sidecars",
        "migrations",
    }
    assert checks["database"]["state"] == "reachable"
    assert checks["database"]["evidence"] == {"dialect": "sqlite"}
    assert checks["external_effects_gate"]["state"] == "closed"
    assert checks["aws_gate"]["state"] == "closed"
    assert checks["model_api_gate"]["state"] == "closed"
    assert checks["oss_sidecars"]["evidence"] == {
        "total": 4,
        "ready": 4,
        "degraded": 0,
        "stopped": 0,
        "blocked": 0,
        "unknown": 0,
    }

    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.event_type == "local_diagnostics_run"
        )
    )
    assert audit is not None
    assert audit.correlation_id == payload["runId"]
    assert audit.after_data == {
        "runId": payload["runId"],
        "status": "healthy",
        "counts": {
            "total": 6,
            "healthy": 6,
            "degraded": 0,
            "unavailable": 0,
        },
        "degradedChecks": [],
    }


@pytest.mark.asyncio
async def test_local_diagnostics_masks_sidecar_secrets_from_response_and_audit(
    client,
    db_session,
    monkeypatch,
):
    secret = "super-secret-sidecar-token"

    async def sensitive_oss_runtime_status(*, force_refresh=False):
        assert force_refresh is True
        return {
            "services": [
                {
                    "state": "ready",
                    "stateLabel": secret,
                    "localUrl": f"http://owner:{secret}@127.0.0.1:3000",
                    "apiToken": secret,
                    "sourceDetails": [secret],
                }
            ],
            "bridgeSecret": secret,
        }

    monkeypatch.setattr(
        "app.services.klm_operations_service.oss_runtime_status",
        sensitive_oss_runtime_status,
    )
    monkeypatch.setattr(
        KlmOperationsService,
        "_diagnose_migrations",
        _healthy_migrations,
    )

    response = await client.post("/api/v1/klm/diagnostics")

    assert response.status_code == 200, response.text
    assert secret not in response.text
    oss_check = _checks_by_key(response.json())["oss_sidecars"]
    assert oss_check["evidence"] == {
        "total": 1,
        "ready": 1,
        "degraded": 0,
        "stopped": 0,
        "blocked": 0,
        "unknown": 0,
    }

    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.event_type == "local_diagnostics_run"
        )
    )
    audit_payload = json.dumps(
        {"summary": audit.summary, "afterData": audit.after_data},
        ensure_ascii=False,
    )
    assert secret not in audit_payload


@pytest.mark.asyncio
async def test_local_diagnostics_marks_unavailable_check_degraded_without_crashing(
    client,
    db_session,
    monkeypatch,
):
    secret = "do-not-echo-this-runtime-secret"

    async def unavailable_oss_runtime_status(*, force_refresh=False):
        assert force_refresh is True
        raise RuntimeError(f"sidecar unavailable with token {secret}")

    monkeypatch.setattr(
        "app.services.klm_operations_service.oss_runtime_status",
        unavailable_oss_runtime_status,
    )
    monkeypatch.setattr(
        KlmOperationsService,
        "_diagnose_migrations",
        _healthy_migrations,
    )

    response = await client.post("/api/v1/klm/diagnostics")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["summary"] == {
        "status": "degraded",
        "total": 6,
        "healthy": 5,
        "degraded": 1,
        "unavailable": 1,
    }
    oss_check = _checks_by_key(payload)["oss_sidecars"]
    assert oss_check == {
        "key": "oss_sidecars",
        "label": "Sidecars OSS",
        "status": "degraded",
        "state": "unavailable",
        "detail": "Verificação indisponível no runtime local.",
        "checkedAt": oss_check["checkedAt"],
        "evidence": {},
    }
    assert secret not in response.text

    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.event_type == "local_diagnostics_run"
        )
    )
    assert audit.after_data["degradedChecks"] == ["oss_sidecars"]
    assert secret not in json.dumps(audit.after_data)
