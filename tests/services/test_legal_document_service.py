from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import AuthContext
from app.models.billing import BillingInterval, Plan, PlanTier, Subscription, SubscriptionStatus
from app.models.document_asset import (
    DocumentAsset,
    DocumentAssetSource,
    DocumentAssetType,
    DocumentClassificationMethod,
    DocumentOCRStatus,
    DocumentShareLink,
)
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.klm_operations import KlmAuditEvent
from app.models.legal_exception import ExceptionCenterStatus, ExceptionKind, LegalException
from app.models.storage_object import StorageObjectState
from app.services.entitlement_service import (
    CurrentStockLimitExceededError,
    EntitlementNotConfiguredError,
)
from app.services.legal_document_service import (
    LegalDocumentConflictError,
    LegalDocumentNotFoundError,
    LegalDocumentService,
    LegalDocumentValidationError,
)
from app.services.local_object_storage_service import LocalObjectStorageService
from app.services.s3_service import S3Service
from app.services.workspace_entitlement_override_service import (
    WorkspaceEntitlementOverrideService,
)

PDF_CLEAN = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"
EICAR_TEXT = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE - arquivo hostil de teste"


async def _seed_legal_document_plan(
    db: AsyncSession,
    workspace_id,
    *,
    limit_quantity: int,
    legal_suite_enabled: bool | None = True,
) -> None:
    plan = Plan(
        name=f"Plano DMS jurídico {uuid4().hex[:8]}",
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
    db.add(plan)
    await db.flush()
    db.add(
        Subscription(
            workspace_id=workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            amount_cents=0,
            currency="BRL",
        )
    )
    entitlements = [
        PlanEntitlement(
            plan_id=plan.id,
            entitlement=EntitlementKey.files,
            enabled=True,
            limit_quantity=1_000,
            hard_limit=True,
        ),
        PlanEntitlement(
            plan_id=plan.id,
            entitlement=EntitlementKey.storage_bytes,
            enabled=True,
            limit_quantity=1_000_000_000,
            hard_limit=True,
        ),
        PlanEntitlement(
            plan_id=plan.id,
            entitlement=EntitlementKey.legal_documents,
            enabled=True,
            limit_quantity=limit_quantity,
            hard_limit=True,
        )
    ]
    if legal_suite_enabled is not None:
        entitlements.append(
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.legal_suite,
                enabled=legal_suite_enabled,
                limit_quantity=None,
                hard_limit=True,
            )
        )
    db.add_all(entitlements)
    await db.flush()


@pytest.fixture(autouse=True)
def local_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "object-storage")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)


@pytest.mark.asyncio
async def test_ingest_dedupes_identical_content_into_one_asset_and_a_new_reference(
    db_session, auth_context
):
    service = LegalDocumentService(db_session)

    first = await service.ingest_bytes(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="peticao.pdf",
        content_type="application/pdf",
        payload=PDF_CLEAN,
        source=DocumentAssetSource.upload_manual,
    )
    assert first.created_new_asset is True
    assert first.deduplicated is False

    second = await service.ingest_bytes(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="peticao-copia.pdf",
        content_type="application/pdf",
        payload=PDF_CLEAN,
        source=DocumentAssetSource.whatsapp,
    )
    assert second.created_new_asset is False
    assert second.deduplicated is True
    assert second.asset.id == first.asset.id

    assets = (
        await db_session.scalars(
            select(DocumentAsset).where(
                DocumentAsset.workspace_id == auth_context.workspace_id,
                DocumentAsset.content_sha256 == hashlib.sha256(PDF_CLEAN).hexdigest(),
            )
        )
    ).all()
    assert len(assets) == 1

    references = await service.list_references(
        workspace_id=auth_context.workspace_id, document_asset_id=first.asset.id
    )
    assert len(references) == 2
    assert {reference.source for reference in references} == {
        DocumentAssetSource.upload_manual,
        DocumentAssetSource.whatsapp,
    }


@pytest.mark.asyncio
async def test_ingest_is_idempotent_by_source_and_external_ref(db_session, auth_context):
    service = LegalDocumentService(db_session)

    first = await service.ingest_bytes(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="anexo.pdf",
        content_type="application/pdf",
        payload=PDF_CLEAN,
        source=DocumentAssetSource.email,
        external_ref="email-msg-123",
    )
    replay = await service.ingest_bytes(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="anexo.pdf",
        content_type="application/pdf",
        payload=PDF_CLEAN,
        source=DocumentAssetSource.email,
        external_ref="email-msg-123",
    )
    assert replay.reference.id == first.reference.id

    references = await service.list_references(
        workspace_id=auth_context.workspace_id, document_asset_id=first.asset.id
    )
    assert len(references) == 1


@pytest.mark.asyncio
async def test_hostile_content_is_quarantined_and_never_classified_or_ocred(
    db_session, auth_context
):
    service = LegalDocumentService(db_session)

    result = await service.ingest_bytes(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="fatura.txt",
        content_type="text/plain",
        payload=EICAR_TEXT,
        source=DocumentAssetSource.upload_manual,
    )

    assert result.storage_object is not None
    assert result.storage_object.state is StorageObjectState.quarantined
    assert result.asset.document_type is DocumentAssetType.nao_classificado
    assert result.asset.classification_method is DocumentClassificationMethod.indisponivel
    assert result.asset.ocr_status is DocumentOCRStatus.nao_aplicavel

    quarantined = await service.list_quarantined(workspace_id=auth_context.workspace_id)
    assert [item.id for item in quarantined] == [result.asset.id]


@pytest.mark.asyncio
async def test_clean_upload_is_honestly_unclassified_and_ocr_pending(db_session, auth_context):
    service = LegalDocumentService(db_session)

    result = await service.ingest_bytes(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="contrato.pdf",
        content_type="application/pdf",
        payload=PDF_CLEAN,
        source=DocumentAssetSource.upload_manual,
    )

    assert result.asset.document_type is DocumentAssetType.nao_classificado
    assert result.asset.classification_method is DocumentClassificationMethod.indisponivel
    assert result.asset.classification_confidence is None
    assert result.asset.ocr_status is DocumentOCRStatus.ocr_pendente
    assert result.asset.ocr_text is None


@pytest.mark.asyncio
async def test_share_link_expiry_is_enforced(db_session, auth_context):
    service = LegalDocumentService(db_session)
    ingested = await service.ingest_bytes(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="minuta.pdf",
        content_type="application/pdf",
        payload=PDF_CLEAN,
        source=DocumentAssetSource.upload_manual,
    )

    with pytest.raises(LegalDocumentValidationError):
        await service.create_share_link(
            workspace_id=auth_context.workspace_id,
            document_asset_id=ingested.asset.id,
            actor_user_id=auth_context.user_id,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )

    issued = await service.create_share_link(
        workspace_id=auth_context.workspace_id,
        document_asset_id=ingested.asset.id,
        actor_user_id=auth_context.user_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        watermark_enabled=True,
    )

    # Simula a passagem do tempo até depois da expiração.
    await db_session.execute(
        update(DocumentShareLink)
        .where(DocumentShareLink.id == issued.share_link.id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.flush()

    with pytest.raises(LegalDocumentConflictError):
        await service.resolve_share_download(
            workspace_id=auth_context.workspace_id,
            raw_token=issued.raw_token,
        )


@pytest.mark.asyncio
async def test_share_download_records_trail_and_respects_max_downloads(db_session, auth_context):
    service = LegalDocumentService(db_session)
    ingested = await service.ingest_bytes(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="minuta.pdf",
        content_type="application/pdf",
        payload=PDF_CLEAN,
        source=DocumentAssetSource.upload_manual,
    )
    issued = await service.create_share_link(
        workspace_id=auth_context.workspace_id,
        document_asset_id=ingested.asset.id,
        actor_user_id=auth_context.user_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        max_downloads=1,
    )

    result = await service.resolve_share_download(
        workspace_id=auth_context.workspace_id,
        raw_token=issued.raw_token,
        downloaded_by_user_id=auth_context.user_id,
        downloader_ip="203.0.113.5",
    )
    result.handle.close()
    assert result.storage_object.id == ingested.storage_object.id

    downloads = await service.list_share_downloads(
        workspace_id=auth_context.workspace_id, share_link_id=issued.share_link.id
    )
    assert len(downloads) == 1
    assert downloads[0].downloaded_by_user_id == auth_context.user_id
    assert downloads[0].downloader_ip == "203.0.113.5"

    # Limite de downloads (1) já foi consumido — a próxima tentativa falha.
    with pytest.raises(LegalDocumentConflictError):
        await service.resolve_share_download(
            workspace_id=auth_context.workspace_id,
            raw_token=issued.raw_token,
        )


@pytest.mark.asyncio
async def test_revoked_share_link_cannot_be_redeemed(db_session, auth_context):
    service = LegalDocumentService(db_session)
    ingested = await service.ingest_bytes(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="minuta.pdf",
        content_type="application/pdf",
        payload=PDF_CLEAN,
        source=DocumentAssetSource.upload_manual,
    )
    issued = await service.create_share_link(
        workspace_id=auth_context.workspace_id,
        document_asset_id=ingested.asset.id,
        actor_user_id=auth_context.user_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    await service.revoke_share_link(
        workspace_id=auth_context.workspace_id, share_link_id=issued.share_link.id
    )

    with pytest.raises(LegalDocumentConflictError):
        await service.resolve_share_download(
            workspace_id=auth_context.workspace_id,
            raw_token=issued.raw_token,
        )


@pytest.mark.asyncio
async def test_sync_conflict_opens_an_exception_and_never_resolves_silently(
    db_session, auth_context
):
    service = LegalDocumentService(db_session)

    opened = await service.report_sync_conflict(
        workspace_id=auth_context.workspace_id,
        provider="google_drive",
        external_id="file-42",
        local_checksum="a" * 64,
        remote_checksum="b" * 64,
        actor_user_id=auth_context.user_id,
    )
    assert opened.exception_kind is ExceptionKind.conflito_sincronizacao_documental
    assert opened.status is ExceptionCenterStatus.aberta

    # Idempotência: o mesmo provider+external_id não abre uma segunda exceção
    # enquanto a primeira permanecer aberta.
    replay = await service.report_sync_conflict(
        workspace_id=auth_context.workspace_id,
        provider="google_drive",
        external_id="file-42",
        actor_user_id=auth_context.user_id,
    )
    assert replay.id == opened.id

    open_exceptions = (
        await db_session.scalars(
            select(LegalException).where(
                LegalException.workspace_id == auth_context.workspace_id,
                LegalException.exception_kind
                == ExceptionKind.conflito_sincronizacao_documental,
            )
        )
    ).all()
    assert len(open_exceptions) == 1


@pytest.mark.asyncio
async def test_document_asset_is_isolated_by_workspace(db_session, auth_context):
    service = LegalDocumentService(db_session)
    ingested = await service.ingest_bytes(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="confidencial.pdf",
        content_type="application/pdf",
        payload=PDF_CLEAN,
        source=DocumentAssetSource.upload_manual,
    )

    with pytest.raises(LegalDocumentNotFoundError):
        await service.get_asset(workspace_id=uuid4(), document_asset_id=ingested.asset.id)


@pytest.mark.asyncio
async def test_attach_existing_storage_object_reuses_bytes_captured_by_another_pipeline(
    db_session, auth_context
):
    """Reaproveita um StorageObject já resolvido (ex.: mídia inbound do WhatsApp)."""
    storage_service = LocalObjectStorageService(db_session)
    authorization = await storage_service.create_upload_authorization(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="comprovante.pdf",
        content_type="application/pdf",
        byte_size=len(PDF_CLEAN),
        checksum_sha256=hashlib.sha256(PDF_CLEAN).hexdigest(),
        retention_until=None,
        expires_in=300,
    )
    from urllib.parse import parse_qs, urlsplit

    ticket = parse_qs(urlsplit(authorization.ticket.url).query)["ticket"][0]
    stored = await storage_service.finalize_upload(
        ticket=ticket, data=PDF_CLEAN, request_content_type="application/pdf"
    )
    assert stored.state is StorageObjectState.available

    service = LegalDocumentService(db_session)
    result = await service.attach_existing_storage_object(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        storage_object=stored,
        filename="comprovante.pdf",
        source=DocumentAssetSource.whatsapp,
        external_ref="wa-msg-999",
    )
    assert result.created_new_asset is True
    assert result.asset.content_sha256 == stored.checksum_sha256

    replay = await service.attach_existing_storage_object(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        storage_object=stored,
        filename="comprovante.pdf",
        source=DocumentAssetSource.whatsapp,
        external_ref="wa-msg-999",
    )
    assert replay.deduplicated is True
    assert replay.asset.id == result.asset.id


@pytest.mark.asyncio
async def test_legal_document_write_fails_closed_when_legal_suite_is_not_configured(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_legal_document_plan(
        db_session,
        auth_context.workspace_id,
        limit_quantity=10,
        legal_suite_enabled=None,
    )

    with pytest.raises(EntitlementNotConfiguredError):
        await LegalDocumentService(db_session).ingest_bytes(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            filename="sem-suite.pdf",
            content_type="application/pdf",
            payload=PDF_CLEAN,
        )

    asset_count = await db_session.scalar(
        select(func.count(DocumentAsset.id)).where(
            DocumentAsset.workspace_id == auth_context.workspace_id
        )
    )
    assert asset_count == 0


@pytest.mark.asyncio
async def test_legal_document_quota_dedup_and_platform_override_are_explicit(
    db_session: AsyncSession,
    auth_context: AuthContext,
    platform_auth_context: AuthContext,
) -> None:
    await _seed_legal_document_plan(
        db_session,
        auth_context.workspace_id,
        limit_quantity=1,
    )
    service = LegalDocumentService(db_session)
    first = await service.ingest_bytes(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="primeiro.pdf",
        content_type="application/pdf",
        payload=PDF_CLEAN,
    )

    # Um ator platform/root não tem bypass implícito no serviço de domínio.
    with pytest.raises(CurrentStockLimitExceededError):
        await service.ingest_bytes(
            workspace_id=auth_context.workspace_id,
            actor_user_id=platform_auth_context.user_id,
            filename="segundo.pdf",
            content_type="application/pdf",
            payload=PDF_CLEAN + b"\nsegundo",
        )

    # Uma nova referência do mesmo hash não cria estoque adicional.
    duplicate = await service.ingest_bytes(
        workspace_id=auth_context.workspace_id,
        actor_user_id=platform_auth_context.user_id,
        filename="copia.pdf",
        content_type="application/pdf",
        payload=PDF_CLEAN,
        source=DocumentAssetSource.email,
    )
    assert duplicate.deduplicated is True
    assert duplicate.asset.id == first.asset.id

    # A única forma de ultrapassar o plano é um override persistido e auditado.
    await WorkspaceEntitlementOverrideService(db_session).upsert_override(
        workspace_id=auth_context.workspace_id,
        entitlement_key=EntitlementKey.legal_documents.value,
        mode="limit",
        quantity=2,
        reason="Exceção comercial aprovada para o teste de fronteira.",
        actor_user_id=platform_auth_context.user_id,
    )
    audit_event = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.actor_user_id == platform_auth_context.user_id,
            KlmAuditEvent.event_type == "workspace_entitlement_override_upserted",
        )
    )
    assert audit_event is not None
    second = await service.ingest_bytes(
        workspace_id=auth_context.workspace_id,
        actor_user_id=platform_auth_context.user_id,
        filename="segundo.pdf",
        content_type="application/pdf",
        payload=PDF_CLEAN + b"\nsegundo",
    )
    assert second.created_new_asset is True

    asset_count = await db_session.scalar(
        select(func.count(DocumentAsset.id)).where(
            DocumentAsset.workspace_id == auth_context.workspace_id
        )
    )
    assert asset_count == 2


@pytest.mark.asyncio
async def test_upload_endpoint_maps_legal_document_limit_to_ent_003(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_legal_document_plan(
        db_session,
        auth_context.workspace_id,
        limit_quantity=1,
    )
    await db_session.commit()

    first = await client.post(
        "/api/v1/legal/documents/upload",
        files={"file": ("primeiro.pdf", PDF_CLEAN, "application/pdf")},
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        "/api/v1/legal/documents/upload",
        files={
            "file": (
                "segundo.pdf",
                PDF_CLEAN + b"\nsegundo",
                "application/pdf",
            )
        },
    )
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "ENT-003"
    assert second.json()["detail"]["entitlement"] == "legal_documents"


@pytest.mark.asyncio
async def test_upload_endpoint_rejects_document_before_unbounded_read(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    monkeypatch,
) -> None:
    await _seed_legal_document_plan(
        db_session,
        auth_context.workspace_id,
        limit_quantity=1,
    )
    await db_session.commit()
    monkeypatch.setattr(settings, "DOCUMENT_AV_MAX_BYTES", 8)

    response = await client.post(
        "/api/v1/legal/documents/upload",
        files={"file": ("grande.pdf", b"123456789", "application/pdf")},
    )

    assert response.status_code == 413, response.text
    assert response.json()["detail"]["code"] == "LEGAL_DOCUMENT_TOO_LARGE"
    assert response.json()["detail"]["maxBytes"] == 8


@pytest.mark.asyncio
async def test_attach_existing_storage_object_cannot_bypass_zero_document_quota(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    from urllib.parse import parse_qs, urlsplit

    storage_service = LocalObjectStorageService(db_session)
    authorization = await storage_service.create_upload_authorization(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="capturado-antes-do-contrato.pdf",
        content_type="application/pdf",
        byte_size=len(PDF_CLEAN),
        checksum_sha256=hashlib.sha256(PDF_CLEAN).hexdigest(),
        retention_until=None,
        expires_in=300,
    )
    ticket = parse_qs(urlsplit(authorization.ticket.url).query)["ticket"][0]
    stored = await storage_service.finalize_upload(
        ticket=ticket,
        data=PDF_CLEAN,
        request_content_type="application/pdf",
    )
    await _seed_legal_document_plan(
        db_session,
        auth_context.workspace_id,
        limit_quantity=0,
    )

    with pytest.raises(CurrentStockLimitExceededError):
        await LegalDocumentService(db_session).attach_existing_storage_object(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            storage_object=stored,
            filename="capturado-antes-do-contrato.pdf",
            source=DocumentAssetSource.whatsapp,
        )
