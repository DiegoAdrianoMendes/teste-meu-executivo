from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.approval import ApprovalPriority, ApprovalQueueItem, ApprovalStatus
from app.models.client import Client
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.matter import Matter
from app.models.task import Task, TaskStatus
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import WorkspaceRole
from app.services.local_object_storage_service import (
    LocalObjectStorageService,
    LocalObjectUnavailableError,
    inspect_payload_handle,
)
from app.services.s3_service import S3Service
from tests.identity_helpers import invite_and_accept_team_member


async def _create_matter_bundle(db: AsyncSession, workspace_id, suffix: str = "base"):
    client = Client(
        workspace_id=workspace_id,
        name=f"Cliente {suffix}",
        cpf=f"{uuid4().hex[:11]}",
        employer="Empresa XPTO",
        position="Analista",
    )
    db.add(client)
    await db.flush()

    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        demand_type=f"Demanda {suffix}",
    )
    db.add(matter)
    await db.flush()

    task = Task(
        workspace_id=workspace_id,
        matter_id=matter.id,
        title=f"Tarefa {suffix}",
        status=TaskStatus.pending,
    )
    document = Document(
        matter_id=matter.id,
        name=f"Documento {suffix}",
        doc_type=DocumentType.outro,
        status=DocumentStatus.completo,
    )
    approval = ApprovalQueueItem(
        matter_id=matter.id,
        approval_type="manual_review",
        intent_key=f"security:{suffix}",
        title=f"Aprovação {suffix}",
        priority=ApprovalPriority.media,
        status=ApprovalStatus.pending,
    )
    db.add_all([task, document, approval])
    await db.flush()
    return matter, task, document, approval


@pytest.mark.asyncio
async def test_public_endpoints_require_authentication(unauthenticated_client):
    approval_response = await unauthenticated_client.get("/api/v1/approvals/pending")
    task_response = await unauthenticated_client.get(f"/api/v1/matters/{uuid4()}/tasks")
    document_response = await unauthenticated_client.get(f"/api/v1/matters/{uuid4()}/documents")

    assert approval_response.status_code == 401
    assert task_response.status_code == 401
    assert document_response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_cross_tenant_task_document_and_approval_access_is_blocked(
    client,
    db_session: AsyncSession,
    auth_context,
):
    other_workspace = Workspace(
        name="Workspace Externo",
        slug="workspace-externo",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()

    matter, _, _, approval = await _create_matter_bundle(
        db_session,
        other_workspace.id,
        suffix="externo",
    )
    await db_session.commit()

    tasks_response = await client.get(f"/api/v1/matters/{matter.id}/tasks")
    documents_response = await client.get(f"/api/v1/matters/{matter.id}/documents")
    pending_approvals_response = await client.get("/api/v1/approvals/pending")
    all_approvals_response = await client.get("/api/v1/approvals")
    create_approval_response = await client.post(
        "/api/v1/approvals",
        json={
            "matterId": str(matter.id),
            "approvalType": "cross_tenant_review",
            "title": "Aprovação de outro workspace",
        },
    )
    resolve_approval_response = await client.post(
        f"/api/v1/approvals/{approval.id}/resolve",
        json={"status": "approved"},
    )

    assert tasks_response.status_code == 200
    assert documents_response.status_code == 200
    assert pending_approvals_response.status_code == 200
    assert all_approvals_response.status_code == 200
    assert create_approval_response.status_code == 404
    assert resolve_approval_response.status_code == 404
    assert tasks_response.json() == []
    assert documents_response.json() == []
    assert all(
        item["id"] != str(approval.id) for item in pending_approvals_response.json()
    )
    assert all(item["id"] != str(approval.id) for item in all_approvals_response.json())


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_documents_never_expose_persistent_url_and_download_requires_sensitive_access(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
    tmp_path,
    local_outbox_headers,
):
    client.headers.update(local_outbox_headers)
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "objects")
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    matter, _, document, _ = await _create_matter_bundle(
        db_session,
        auth_context.workspace_id,
        suffix="privado",
    )
    document.name = "private.pdf"
    document.mime_type = "application/pdf"
    document.s3_key = f"matters/{matter.id}/documents/{document.id}/private.pdf"
    document.s3_url = "https://public.example.test/private.pdf"
    S3Service.write_local_object_atomic(
        document.s3_key,
        b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n",
    )
    await db_session.flush()

    listed = await client.get(f"/api/v1/matters/{matter.id}/documents")
    assert listed.status_code == 200
    assert listed.json()[0]["url"] is None

    download = await client.get(f"/api/v1/documents/{document.id}/download-url")
    assert download.status_code == 200
    assert "/api/v1/local-storage/object-download?ticket=" in download.json()[
        "downloadUrl"
    ]

    member, _ = await invite_and_accept_team_member(
        client,
        name="Atendente sem documentos",
        email=f"sem-documentos-{uuid4().hex[:8]}@example.com",
        workspaceRole="operator",
    )
    auth_context.user_id = member["userId"]
    auth_context.membership_id = member["membershipId"]
    auth_context.role = WorkspaceRole.operator

    denied = await client.get(f"/api/v1/documents/{document.id}/download-url")
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "AUTH-012"


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_historical_local_fixture_key_is_adopted_without_weakening_identity_binding(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
    tmp_path,
    local_outbox_headers,
):
    client.headers.update(local_outbox_headers)
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "objects")
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    matter, _, document, _ = await _create_matter_bundle(
        db_session,
        auth_context.workspace_id,
        suffix="fixture-historica",
    )
    document.name = "Negativa do plano"
    document.mime_type = "application/pdf"
    document.s3_key = f"matters/{matter.id}/documents/{document.id}/fixture.pdf"
    S3Service.write_local_object_atomic(
        document.s3_key,
        b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n",
    )
    await db_session.flush()

    download = await client.get(f"/api/v1/documents/{document.id}/download-url")
    assert download.status_code == 200, download.text
    assert "/api/v1/local-storage/object-download?ticket=" in download.json()[
        "downloadUrl"
    ]

    _, _, mismatched_document, _ = await _create_matter_bundle(
        db_session,
        auth_context.workspace_id,
        suffix="fixture-cruzada",
    )
    mismatched_document.mime_type = "application/pdf"
    mismatched_document.s3_key = (
        f"matters/{matter.id}/documents/{mismatched_document.id}/fixture.pdf"
    )
    await db_session.flush()

    rejected = await client.get(
        f"/api/v1/documents/{mismatched_document.id}/download-url"
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "DOC-006"


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_document_upload_fails_closed_when_storage_is_unavailable(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    matter, _, _, _ = await _create_matter_bundle(
        db_session,
        auth_context.workspace_id,
        suffix="upload-indisponivel",
    )

    async def reject_authorization(*_args, **_kwargs):
        raise LocalObjectUnavailableError("storage unavailable")

    monkeypatch.setattr(
        LocalObjectStorageService,
        "create_upload_authorization",
        reject_authorization,
    )
    response = await client.post(
        f"/api/v1/matters/{matter.id}/documents",
        headers={"Idempotency-Key": "document-upload-storage-unavailable"},
        json={
            "name": "laudo.pdf",
            "docType": "outro",
            "mimeType": "application/pdf",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DOC-003"


@pytest.mark.asyncio
async def test_local_public_asset_compatibility_uses_short_lived_tickets_without_aws(
    client,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "objects")
    monkeypatch.setattr(settings, "PAPERCLIP_PUBLIC_URL", "http://test")
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    scan_calls: list[str] = []

    def tracked_incremental_scan(handle, declared_content_type):
        scan_calls.append(declared_content_type)
        return inspect_payload_handle(handle, declared_content_type)

    monkeypatch.setattr(
        "app.api.v1.endpoints.local_storage.inspect_payload_handle",
        tracked_incremental_scan,
    )
    service = S3Service()
    key = "public-site/media/local/imagem.png"
    payload = b"\x89PNG\r\n\x1a\nlocal-safe-image"
    upload_url = urlsplit(
        service.generate_presigned_upload_url(
            key,
            mime_type="image/png",
            expires_in=60,
            bucket_name=settings.S3_PUBLIC_ASSET_BUCKET_NAME,
        )
    )
    upload = await client.put(
        f"{upload_url.path}?{upload_url.query}",
        content=payload,
        headers={"Content-Type": "image/png"},
    )
    assert upload.status_code == 204
    assert scan_calls == ["image/png"]

    download_url = urlsplit(service.get_public_asset_url(key))
    download = await client.get(f"{download_url.path}?{download_url.query}")
    assert download.status_code == 200
    assert download.content == payload
    assert download.headers["cache-control"] == "private, no-store"

    wrong_operation = await client.get(
        f"{upload_url.path.replace('upload', 'download')}?{upload_url.query}"
    )
    assert wrong_operation.status_code == 401
    assert wrong_operation.json()["detail"]["code"] == "STORAGE-005"


@pytest.mark.asyncio
async def test_workspace_setup_requires_invite_token_in_production(
    unauthenticated_client,
    monkeypatch,
):
    original_env = settings.APP_ENV
    original_token = settings.SETUP_INVITE_TOKEN
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "SETUP_INVITE_TOKEN", "setup-secret")
    monkeypatch.setattr(settings, "SETUP_RATE_LIMIT_PER_HOUR", 0)

    payload = {
        "workspaceName": "Workspace Seguro",
        "adminName": "Admin Seguro",
        "adminEmail": "seguro@example.com",
        "adminPassword": "senha12345",
        "mode": "assisted",
    }

    forbidden = await unauthenticated_client.post("/api/v1/workspaces/setup", json=payload)
    allowed = await unauthenticated_client.post(
        "/api/v1/workspaces/setup",
        json={**payload, "adminEmail": "seguro2@example.com", "setupToken": "setup-secret"},
    )

    assert forbidden.status_code == 403
    assert allowed.status_code == 201

    monkeypatch.setattr(settings, "APP_ENV", original_env)
    monkeypatch.setattr(settings, "SETUP_INVITE_TOKEN", original_token)
