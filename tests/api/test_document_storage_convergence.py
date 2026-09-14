from __future__ import annotations

import hashlib
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
from app.models.document import Document, DocumentStatus
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.matter import Matter
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.models.timeline_event import TimelineEvent
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole
from app.services.s3_service import S3Service

PDF = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"
ACTIVE_PDF = b"%PDF-1.7\n/OpenAction <</S /JavaScript>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def local_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "object-storage")
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    monkeypatch.setattr(settings, "PAPERCLIP_PUBLIC_URL", "http://test")


def _target(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.path}?{parsed.query}"


async def _matter(db_session, workspace_id):
    client = Client(workspace_id=workspace_id, name="Cliente Storage")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        demand_type="Demanda documental",
    )
    db_session.add(matter)
    await db_session.flush()
    return client, matter


async def _seed_active_storage_subscription(db_session, workspace_id) -> None:
    plan = Plan(
        name="Plano storage produção",
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
                limit_quantity=100,
                hard_limit=True,
            ),
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.storage_bytes,
                enabled=True,
                limit_quantity=10 * 1024 * 1024,
                hard_limit=True,
            ),
        ]
    )
    await db_session.commit()


async def _start_document(client, matter_id, payload: bytes):
    response = await client.post(
        f"/api/v1/matters/{matter_id}/documents",
        headers={"Idempotency-Key": f"document-upload-{matter_id}"},
        json={
            "name": "contrato.pdf",
            "docType": "contrato",
            "fileSize": str(len(payload)),
            "mimeType": "application/pdf",
        },
    )
    assert response.status_code == 201, response.text
    return response


@pytest.mark.asyncio
async def test_first_document_authorization_requires_idempotency_key(
    client,
    db_session,
    auth_context,
):
    _, matter = await _matter(db_session, auth_context.workspace_id)

    response = await client.post(
        f"/api/v1/matters/{matter.id}/documents",
        json={
            "name": "contrato.pdf",
            "docType": "contrato",
            "fileSize": str(len(PDF)),
            "mimeType": "application/pdf",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_first_document_authorization_replay_reuses_consumed_object(
    client,
    db_session,
    auth_context,
):
    _, matter = await _matter(db_session, auth_context.workspace_id)
    key = "document-first-authorization-replay"
    request = {
        "name": "contrato.pdf",
        "docType": "contrato",
        "fileSize": str(len(PDF)),
        "mimeType": "application/pdf",
        "checksumSha256": hashlib.sha256(PDF).hexdigest(),
    }
    first = await client.post(
        f"/api/v1/matters/{matter.id}/documents",
        headers={"Idempotency-Key": key},
        json=request,
    )
    assert first.status_code == 201, first.text
    first_body = first.json()
    uploaded = await client.put(
        _target(first_body["uploadUrl"]),
        content=PDF,
        headers={"Content-Type": "application/pdf"},
    )
    assert uploaded.status_code == 204, uploaded.text
    confirmed = await client.post(
        f"/api/v1/documents/{first_body['documentId']}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text

    replay = await client.post(
        f"/api/v1/matters/{matter.id}/documents",
        headers={"Idempotency-Key": key},
        json=request,
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["documentId"] == first_body["documentId"]
    assert replay.json()["uploadRequired"] is False
    assert replay.json()["uploadUrl"] is None

    conflict = await client.post(
        f"/api/v1/matters/{matter.id}/documents",
        headers={"Idempotency-Key": key},
        json={**request, "name": "outro.pdf"},
    )
    assert conflict.status_code == 409

    assert len((await db_session.scalars(select(Document))).all()) == 1
    assert len((await db_session.scalars(select(StorageObject))).all()) == 1
    assert len((await db_session.scalars(select(TimelineEvent))).all()) == 1


@pytest.mark.asyncio
async def test_legacy_document_upload_converges_to_versioned_storage_and_is_single_use(
    client,
    db_session,
    auth_context,
):
    bound_client, matter = await _matter(db_session, auth_context.workspace_id)
    created = await _start_document(client, matter.id, PDF)
    document_id = created.json()["documentId"]
    upload_url = created.json()["uploadUrl"]

    assert "/api/v1/local-storage/upload?ticket=" in upload_url
    uploaded = await client.put(
        _target(upload_url),
        content=PDF,
        headers={"Content-Type": "application/pdf"},
    )
    assert uploaded.status_code == 204

    replay = await client.put(
        _target(upload_url),
        content=PDF,
        headers={"Content-Type": "application/pdf"},
    )
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "STORAGE-008"

    confirmed = await client.post(f"/api/v1/documents/{document_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "completo"

    stored = await db_session.scalar(
        select(StorageObject).where(StorageObject.object_id == document_id)
    )
    assert stored is not None
    assert stored.version == 1
    assert stored.state is StorageObjectState.available
    assert stored.av_status is StorageAVStatus.simulated_unverified
    assert stored.scan_method == "local_heuristic_v1"
    assert stored.checksum_sha256 == hashlib.sha256(PDF).hexdigest()
    assert stored.matter_id == matter.id
    assert stored.client_id == bound_client.id

    authorization = await client.get(f"/api/v1/documents/{document_id}/download-url")
    assert authorization.status_code == 200, authorization.text
    parsed = urlsplit(authorization.json()["downloadUrl"])
    token = parse_qs(parsed.query)["ticket"][0]
    downloaded = await client.get(parsed.path, headers={"X-Storage-Ticket": token})
    assert downloaded.status_code == 200
    assert downloaded.content == PDF
    assert downloaded.headers["x-checksum-sha256"] == hashlib.sha256(PDF).hexdigest()

    replayed_download = await client.get(
        parsed.path,
        headers={"X-Storage-Ticket": token},
    )
    assert replayed_download.status_code == 401
    assert replayed_download.json()["detail"]["code"] == "STORAGE-005"


@pytest.mark.asyncio
async def test_legacy_document_active_pdf_is_quarantined_and_cannot_download(
    client,
    db_session,
    auth_context,
):
    _, matter = await _matter(db_session, auth_context.workspace_id)
    created = await _start_document(client, matter.id, ACTIVE_PDF)
    document_id = created.json()["documentId"]

    uploaded = await client.put(
        _target(created.json()["uploadUrl"]),
        content=ACTIVE_PDF,
        headers={"Content-Type": "application/pdf"},
    )
    assert uploaded.status_code == 204

    confirmed = await client.post(f"/api/v1/documents/{document_id}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "invalido"
    stored = await db_session.scalar(
        select(StorageObject).where(StorageObject.object_id == document_id)
    )
    assert stored is not None
    assert stored.state is StorageObjectState.quarantined
    assert stored.av_status is StorageAVStatus.simulated_blocked
    assert stored.quarantine_reason == "simulated_pdf_active_content"

    download = await client.get(f"/api/v1/documents/{document_id}/download-url")
    assert download.status_code == 409
    assert download.json()["detail"]["code"] == "DOC-001"


@pytest.mark.asyncio
async def test_document_delete_restore_converges_with_storage_and_preserves_actor(
    client,
    db_session,
    auth_context,
):
    _, matter = await _matter(db_session, auth_context.workspace_id)
    created = await _start_document(client, matter.id, PDF)
    document_id = created.json()["documentId"]
    uploaded = await client.put(
        _target(created.json()["uploadUrl"]),
        content=PDF,
        headers={"Content-Type": "application/pdf"},
    )
    assert uploaded.status_code == 204
    assert (
        await client.post(f"/api/v1/documents/{document_id}/confirm")
    ).status_code == 200

    first_delete = await client.delete(f"/api/v1/documents/{document_id}")
    replay_delete = await client.delete(f"/api/v1/documents/{document_id}")
    assert first_delete.status_code == replay_delete.status_code == 204

    document = await db_session.get(Document, document_id)
    storage = await db_session.scalar(
        select(StorageObject).where(StorageObject.object_id == document_id)
    )
    assert document is not None
    assert document.status is DocumentStatus.invalido
    assert storage is not None
    assert storage.deleted_at is not None
    assert storage.deleted_by_user_id == auth_context.user_id
    hidden = await client.get(f"/api/v1/matters/{matter.id}/documents")
    assert hidden.status_code == 200
    assert hidden.json() == []

    restored = await client.post(f"/api/v1/documents/{document_id}/restore")
    replay_restore = await client.post(f"/api/v1/documents/{document_id}/restore")
    assert restored.status_code == replay_restore.status_code == 200
    assert restored.json()["status"] == "completo"

    await db_session.refresh(storage)
    await db_session.refresh(document)
    assert storage.deleted_at is None
    assert storage.deleted_by_user_id == auth_context.user_id
    assert storage.restored_by_user_id == auth_context.user_id
    assert document.status is DocumentStatus.completo
    visible = await client.get(f"/api/v1/matters/{matter.id}/documents")
    assert visible.status_code == 200
    assert [item["id"] for item in visible.json()] == [document_id]

    timeline = list(
        (
            await db_session.scalars(
                select(TimelineEvent).where(TimelineEvent.matter_id == matter.id)
            )
        ).all()
    )
    assert sum(event.title.startswith("Documento excluído:") for event in timeline) == 1
    assert sum(event.title.startswith("Documento restaurado:") for event in timeline) == 1


@pytest.mark.asyncio
async def test_legacy_document_sensitive_links_require_lead_data_edit(
    client,
    db_session,
    auth_context,
):
    _, matter = await _matter(db_session, auth_context.workspace_id)
    created = await _start_document(client, matter.id, PDF)
    document_id = created.json()["documentId"]

    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    assert membership is not None
    membership.role = WorkspaceRole.workspace_admin
    membership.permission_denials = [
        {"module": "lead_data", "action": "edit", "scope": "workspace"}
    ]
    auth_context.role = WorkspaceRole.workspace_admin
    await db_session.flush()

    blocked_create = await client.post(
        f"/api/v1/matters/{matter.id}/documents",
        headers={"Idempotency-Key": "document-upload-permission-blocked"},
        json={
            "name": "bloqueado.pdf",
            "docType": "contrato",
            "fileSize": str(len(PDF)),
            "mimeType": "application/pdf",
        },
    )
    blocked_delete = await client.delete(f"/api/v1/documents/{document_id}")
    assert blocked_create.status_code == 403
    assert blocked_delete.status_code == 403
    assert blocked_create.json()["detail"]["code"] == "AUTH-012"
    assert blocked_delete.json()["detail"]["code"] == "AUTH-012"


@pytest.mark.asyncio
async def test_document_upload_fails_closed_in_production_without_creating_records(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    _, matter = await _matter(db_session, auth_context.workspace_id)
    await _seed_active_storage_subscription(db_session, auth_context.workspace_id)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")

    response = await client.post(
        f"/api/v1/matters/{matter.id}/documents",
        headers={"Idempotency-Key": "document-upload-production-disabled"},
        json={
            "name": "producao.pdf",
            "docType": "contrato",
            "fileSize": str(len(PDF)),
            "mimeType": "application/pdf",
        },
    )
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "DOC-008"
    assert await db_session.scalar(select(Document)) is None
    assert await db_session.scalar(select(StorageObject)) is None
