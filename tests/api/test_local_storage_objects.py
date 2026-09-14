from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.client import Client
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.matter import Matter
from app.models.storage_object import StorageObject, StorageObjectState
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole
from app.services.local_object_storage_service import LocalObjectStorageService
from app.services.s3_service import S3Service

PDF = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


@pytest.fixture(autouse=True)
def local_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "object-storage")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    monkeypatch.setattr(settings, "PAPERCLIP_PUBLIC_URL", "http://test")


def _request_target(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.path}?{parsed.query}"


async def _seed_storage_entitlements(
    db_session,
    workspace_id,
    *,
    files: int,
    storage_bytes: int,
) -> None:
    plan = Plan(
        name="Plano quota storage",
        tier=PlanTier.enterprise,
        price_monthly_cents=0,
        price_annually_cents=0,
        max_users=100,
        max_matters=None,
        max_tokens_per_month=1_000_000,
        max_ai_agents=100,
        features={},
        is_active=True,
        is_public=False,
        sort_order=1,
    )
    db_session.add(plan)
    await db_session.flush()
    db_session.add(
        Subscription(
            workspace_id=workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            amount_cents=0,
            currency="BRL",
        )
    )
    db_session.add_all(
        [
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.files,
                enabled=True,
                limit_quantity=files,
                hard_limit=True,
            ),
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.storage_bytes,
                enabled=True,
                limit_quantity=storage_bytes,
                hard_limit=True,
            ),
        ]
    )
    await db_session.commit()


async def _create_and_upload(client, *, payload: bytes = PDF, **overrides):
    body = {
        "filename": "peticao.pdf",
        "contentType": "application/pdf",
        "byteSize": len(payload),
        "checksumSha256": hashlib.sha256(payload).hexdigest(),
        **overrides,
    }
    created = await client.post("/api/v1/local-storage/objects", json=body)
    assert created.status_code == 201, created.text
    uploaded = await client.put(
        _request_target(created.json()["uploadUrl"]),
        content=payload,
        headers={"Content-Type": body["contentType"]},
    )
    assert uploaded.status_code == 200, uploaded.text
    return created, uploaded


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_full_http_lifecycle_preserves_versions_and_legacy_s3_semantics(
    client,
    monkeypatch,
):
    created, uploaded = await _create_and_upload(client)
    object_id = created.json()["object"]["objectId"]

    assert uploaded.json()["state"] == "available"
    assert uploaded.json()["avStatus"] == "simulated_unverified"
    assert uploaded.json()["checksumSha256"] == hashlib.sha256(PDF).hexdigest()

    def reject_replay_staging(*_args, **_kwargs):
        raise AssertionError("consumed upload ticket must fail before staging")

    with monkeypatch.context() as replay_patch:
        replay_patch.setattr(S3Service, "create_local_object", reject_replay_staging)
        replay = await client.put(
            _request_target(created.json()["uploadUrl"]),
            content=PDF,
            headers={"Content-Type": "application/pdf"},
        )
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "STORAGE-008"

    second_payload = PDF.replace(b"1 0 obj", b"2 0 obj")
    second = await client.post(
        f"/api/v1/local-storage/objects/{object_id}/versions",
        json={
            "filename": "peticao-v2.pdf",
            "contentType": "application/pdf",
            "byteSize": len(second_payload),
            "checksumSha256": hashlib.sha256(second_payload).hexdigest(),
        },
    )
    assert second.status_code == 201, second.text
    second_upload = await client.put(
        _request_target(second.json()["uploadUrl"]),
        content=second_payload,
        headers={"Content-Type": "application/pdf"},
    )
    assert second_upload.status_code == 200
    assert second_upload.json()["version"] == 2

    versions = await client.get(f"/api/v1/local-storage/objects/{object_id}/versions")
    assert versions.status_code == 200
    assert [item["version"] for item in versions.json()] == [2, 1]

    download_ticket = await client.post(
        f"/api/v1/local-storage/objects/{object_id}/download-ticket?version=1"
    )
    assert download_ticket.status_code == 200
    download = await client.get(_request_target(download_ticket.json()["downloadUrl"]))
    assert download.status_code == 200
    assert download.content == PDF
    assert download.headers["cache-control"] == "private, no-store"
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["x-checksum-sha256"] == hashlib.sha256(PDF).hexdigest()

    exported = await client.get(f"/api/v1/local-storage/objects/{object_id}/export")
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert "manifest.json" in archive.namelist()
        assert "versions/00000001/peticao.pdf" in archive.namelist()
        assert "versions/00000002/peticao-v2.pdf" in archive.namelist()

    deleted = await client.delete(f"/api/v1/local-storage/objects/{object_id}")
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/local-storage/objects/{object_id}")).status_code == 404
    hidden_download = await client.get(_request_target(download_ticket.json()["downloadUrl"]))
    assert hidden_download.status_code == 401
    assert hidden_download.json()["detail"]["code"] == "STORAGE-005"

    restored = await client.post(f"/api/v1/local-storage/objects/{object_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["deletedAt"] is None
    assert restored.json()["restoredAt"] is not None


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_quarantine_and_retention_are_visible_and_enforced_over_http(client):
    disguised = b"this is not a PDF"
    _, quarantined = await _create_and_upload(client, payload=disguised)
    assert quarantined.json()["state"] == "quarantined"
    assert quarantined.json()["quarantineReason"] == "mime_magic_mismatch"

    retained_until = datetime.now(UTC) + timedelta(days=7)
    created, uploaded = await _create_and_upload(
        client,
        retentionUntil=retained_until.isoformat(),
    )
    assert uploaded.json()["state"] == "available"
    object_id = created.json()["object"]["objectId"]
    blocked = await client.delete(f"/api/v1/local-storage/objects/{object_id}")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "STORAGE-007"

    shorter = await client.put(
        f"/api/v1/local-storage/objects/{object_id}/retention",
        json={"retentionUntil": (retained_until - timedelta(days=1)).isoformat()},
    )
    assert shorter.status_code == 409
    assert shorter.json()["detail"]["code"] == "STORAGE-007"


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_upload_contract_rejects_paths_urls_extra_fields_and_tampered_tickets(client):
    traversal = await client.post(
        "/api/v1/local-storage/objects",
        json={"filename": "../secret.pdf", "contentType": "application/pdf"},
    )
    assert traversal.status_code == 422

    remote_source = await client.post(
        "/api/v1/local-storage/objects",
        json={
            "filename": "safe.pdf",
            "contentType": "application/pdf",
            "sourceUrl": "http://169.254.169.254/latest/meta-data",
        },
    )
    assert remote_source.status_code == 422

    created = await client.post(
        "/api/v1/local-storage/objects",
        json={"filename": "safe.pdf", "contentType": "application/pdf"},
    )
    parsed = urlsplit(created.json()["uploadUrl"])
    ticket = parse_qs(parsed.query)["ticket"][0]
    midpoint = len(ticket) // 2
    replacement = "A" if ticket[midpoint] != "A" else "B"
    tampered = f"{ticket[:midpoint]}{replacement}{ticket[midpoint + 1:]}"
    rejected = await client.put(
        f"{parsed.path}?ticket={tampered}",
        content=PDF,
        headers={"Content-Type": "application/pdf"},
    )
    assert rejected.status_code == 401
    assert rejected.json()["detail"]["code"] == "STORAGE-005"


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_object_metadata_and_ticket_issuance_are_cross_tenant_invisible(
    client,
    db_session,
    auth_context,
):
    other = Workspace(
        name="Outro Workspace",
        slug="outro-storage-workspace",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other)
    await db_session.flush()
    service = LocalObjectStorageService(db_session)
    authorization = await service.create_upload_authorization(
        workspace_id=other.id,
        actor_user_id=auth_context.user_id,
        filename="outro.pdf",
        content_type="application/pdf",
        byte_size=len(PDF),
        checksum_sha256=hashlib.sha256(PDF).hexdigest(),
        retention_until=None,
        expires_in=60,
    )
    object_id = authorization.storage_object.object_id

    metadata = await client.get(f"/api/v1/local-storage/objects/{object_id}")
    ticket = await client.post(
        f"/api/v1/local-storage/objects/{object_id}/download-ticket"
    )
    versions = await client.get(f"/api/v1/local-storage/objects/{object_id}/versions")

    assert metadata.status_code == 404
    assert ticket.status_code == 404
    assert versions.status_code == 404


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_storage_rbac_and_bound_acl_adversarial_matrix(
    client,
    db_session,
    auth_context,
):
    bound_client = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente ACL",
    )
    db_session.add(bound_client)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=bound_client.id,
        demand_type="Demanda ACL",
    )
    db_session.add(matter)
    await db_session.flush()

    unbound, _ = await _create_and_upload(client)
    bound_body = {
        "filename": "vinculado.pdf",
        "contentType": "application/pdf",
        "byteSize": len(PDF),
        "checksumSha256": hashlib.sha256(PDF).hexdigest(),
    }
    bound = await client.post(
        f"/api/v1/local-storage/objects?matterId={matter.id}",
        json=bound_body,
    )
    assert bound.status_code == 201, bound.text
    bound_upload = await client.put(
        _request_target(bound.json()["uploadUrl"]),
        content=PDF,
        headers={"Content-Type": "application/pdf"},
    )
    assert bound_upload.status_code == 200
    unbound_id = unbound.json()["object"]["objectId"]
    bound_id = bound.json()["object"]["objectId"]
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    assert membership is not None

    membership.role = WorkspaceRole.viewer
    auth_context.role = WorkspaceRole.viewer
    await db_session.flush()
    listed = await client.get("/api/v1/local-storage/objects")
    assert listed.status_code == 200
    assert {item["objectId"] for item in listed.json()} == {unbound_id}
    assert (
        await client.get(f"/api/v1/local-storage/objects/{bound_id}")
    ).status_code == 404
    assert (
        await client.post("/api/v1/local-storage/objects", json=bound_body)
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/local-storage/objects/{unbound_id}/download-ticket"
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/api/v1/local-storage/objects/{bound_id}/download-ticket"
        )
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/local-storage/objects/{unbound_id}/export")
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/local-storage/objects/{unbound_id}")
    ).status_code == 403
    assert (await client.post("/api/v1/local-storage/reconcile")).status_code == 403

    membership.role = WorkspaceRole.operator
    auth_context.role = WorkspaceRole.operator
    await db_session.flush()
    operator_create = await client.post(
        "/api/v1/local-storage/objects",
        json=bound_body,
    )
    assert operator_create.status_code == 201
    assert (
        await client.post(
            f"/api/v1/local-storage/objects?matterId={matter.id}",
            json=bound_body,
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/local-storage/objects/{bound_id}/versions",
            json=bound_body,
        )
    ).status_code == 404
    assert (
        await client.put(
            f"/api/v1/local-storage/objects/{unbound_id}/retention",
            json={
                "retentionUntil": (datetime.now(UTC) + timedelta(days=1)).isoformat()
            },
        )
    ).status_code == 403

    membership.role = WorkspaceRole.legal_manager
    auth_context.role = WorkspaceRole.legal_manager
    await db_session.flush()
    assert (
        await client.get(f"/api/v1/local-storage/objects/{bound_id}")
    ).status_code == 200
    assert (
        await client.post(
            f"/api/v1/local-storage/objects/{bound_id}/download-ticket"
        )
    ).status_code == 200
    exported = await client.get(f"/api/v1/local-storage/objects/{bound_id}/export")
    assert exported.status_code == 200
    assert (
        await client.delete(f"/api/v1/local-storage/objects/{bound_id}")
    ).status_code == 403


@pytest.mark.asyncio
async def test_file_quota_is_atomic_at_finalize_and_replay_leaves_no_counted_ghost(
    client,
    db_session,
    auth_context,
):
    await _seed_storage_entitlements(
        db_session,
        auth_context.workspace_id,
        files=1,
        storage_bytes=len(PDF) * 10,
    )
    await _create_and_upload(client)

    blocked_auth = await client.post(
        "/api/v1/local-storage/objects",
        json={
            "filename": "quota-files.pdf",
            "contentType": "application/pdf",
            "byteSize": len(PDF),
            "checksumSha256": hashlib.sha256(PDF).hexdigest(),
        },
    )
    assert blocked_auth.status_code == 201, blocked_auth.text
    target = _request_target(blocked_auth.json()["uploadUrl"])

    for _ in range(2):
        blocked = await client.put(
            target,
            content=PDF,
            headers={"Content-Type": "application/pdf"},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == "ENT-003"
        assert blocked.json()["detail"]["entitlement"] == "files"

    records = list(
        (
            await db_session.scalars(
                select(StorageObject).where(
                    StorageObject.workspace_id == auth_context.workspace_id
                )
            )
        ).all()
    )
    counted = [record for record in records if record.byte_size is not None]
    pending = [record for record in records if record.byte_size is None]
    assert len(counted) == 1
    assert len(pending) == 1
    assert pending[0].state is StorageObjectState.pending
    assert not S3Service.local_object_exists(pending[0].storage_key)
    assert pending[0].quarantine_key is not None
    assert not S3Service.local_object_exists(pending[0].quarantine_key)


@pytest.mark.asyncio
async def test_storage_bytes_quota_uses_actual_streamed_size(client, db_session, auth_context):
    await _seed_storage_entitlements(
        db_session,
        auth_context.workspace_id,
        files=10,
        storage_bytes=len(PDF),
    )
    await _create_and_upload(client)

    blocked_auth = await client.post(
        "/api/v1/local-storage/objects",
        json={
            "filename": "quota-bytes.pdf",
            "contentType": "application/pdf",
            "byteSize": 1,
        },
    )
    assert blocked_auth.status_code == 201, blocked_auth.text
    blocked = await client.put(
        _request_target(blocked_auth.json()["uploadUrl"]),
        content=PDF,
        headers={"Content-Type": "application/pdf"},
    )
    assert blocked.status_code == 409, blocked.text
    detail = blocked.json()["detail"]
    assert detail["code"] == "ENT-003"
    assert detail["entitlement"] == "storage_bytes"
    assert detail["currentQuantity"] == len(PDF)
    assert detail["requestedQuantity"] == len(PDF)


@pytest.mark.asyncio
async def test_restore_rechecks_stock_and_is_idempotent_after_capacity_is_released(
    client,
    db_session,
    auth_context,
):
    await _seed_storage_entitlements(
        db_session,
        auth_context.workspace_id,
        files=1,
        storage_bytes=len(PDF) * 2,
    )
    first, _ = await _create_and_upload(client)
    first_id = first.json()["object"]["objectId"]
    assert (
        await client.delete(f"/api/v1/local-storage/objects/{first_id}")
    ).status_code == 204

    second, _ = await _create_and_upload(client)
    second_id = second.json()["object"]["objectId"]
    blocked = await client.post(f"/api/v1/local-storage/objects/{first_id}/restore")
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["entitlement"] == "files"
    first_record = await db_session.scalar(
        select(StorageObject).where(StorageObject.object_id == first_id)
    )
    assert first_record is not None
    assert first_record.deleted_at is not None

    assert (
        await client.delete(f"/api/v1/local-storage/objects/{second_id}")
    ).status_code == 204
    restored = await client.post(f"/api/v1/local-storage/objects/{first_id}/restore")
    replay = await client.post(f"/api/v1/local-storage/objects/{first_id}/restore")
    assert restored.status_code == replay.status_code == 200

    active = list(
        (
            await db_session.scalars(
                select(StorageObject).where(
                    StorageObject.workspace_id == auth_context.workspace_id,
                    StorageObject.deleted_at.is_(None),
                    StorageObject.byte_size.is_not(None),
                )
            )
        ).all()
    )
    assert len(active) == 1
    assert active[0].object_id == first_record.object_id
    assert active[0].byte_size == len(PDF)


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_restore_rejects_pending_version_without_materialized_payload(
    client,
    db_session,
):
    authorization = await client.post(
        "/api/v1/local-storage/objects",
        json={
            "filename": "pending.pdf",
            "contentType": "application/pdf",
            "byteSize": len(PDF),
        },
    )
    assert authorization.status_code == 201, authorization.text
    object_id = authorization.json()["object"]["objectId"]
    assert (
        await client.delete(f"/api/v1/local-storage/objects/{object_id}")
    ).status_code == 204

    restored = await client.post(f"/api/v1/local-storage/objects/{object_id}/restore")
    assert restored.status_code == 409
    assert restored.json()["detail"]["code"] == "STORAGE-008"
    record = await db_session.scalar(
        select(StorageObject).where(StorageObject.object_id == object_id)
    )
    assert record is not None
    assert record.state is StorageObjectState.pending
    assert record.byte_size is None
    assert record.deleted_at is not None


@pytest.mark.asyncio
async def test_reconcile_rechecks_quota_after_crash_and_later_upload_consumes_stock(
    client,
    db_session,
    auth_context,
):
    await _seed_storage_entitlements(
        db_session,
        auth_context.workspace_id,
        files=1,
        storage_bytes=len(PDF) * 2,
    )
    interrupted = await client.post(
        "/api/v1/local-storage/objects",
        json={
            "filename": "interrompido.pdf",
            "contentType": "application/pdf",
            "byteSize": len(PDF),
            "checksumSha256": hashlib.sha256(PDF).hexdigest(),
        },
    )
    assert interrupted.status_code == 201, interrupted.text
    interrupted_id = interrupted.json()["object"]["objectId"]
    interrupted_record = await db_session.scalar(
        select(StorageObject).where(StorageObject.object_id == interrupted_id)
    )
    assert interrupted_record is not None
    assert interrupted_record.quarantine_key is not None
    S3Service.write_local_object_atomic(interrupted_record.quarantine_key, PDF)

    _, later_upload = await _create_and_upload(client)
    assert later_upload.json()["state"] == "available"

    for _ in range(2):
        reconcile = await client.post("/api/v1/local-storage/reconcile")
        assert reconcile.status_code == 409, reconcile.text
        assert reconcile.json()["detail"]["code"] == "ENT-003"
        assert reconcile.json()["detail"]["entitlement"] == "files"

    await db_session.refresh(interrupted_record)
    assert interrupted_record.state is StorageObjectState.pending
    assert interrupted_record.byte_size is None
    assert S3Service.local_object_exists(interrupted_record.quarantine_key)
    counted = list(
        (
            await db_session.scalars(
                select(StorageObject).where(
                    StorageObject.workspace_id == auth_context.workspace_id,
                    StorageObject.deleted_at.is_(None),
                    StorageObject.byte_size.is_not(None),
                )
            )
        ).all()
    )
    assert len(counted) == 1
