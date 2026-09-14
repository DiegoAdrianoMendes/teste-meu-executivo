"""Testes: pasta de protocolo + manifesto (Onda 7, Plano Mestre §10).
Ver app/services/protocol_folder_service.py.
"""

from __future__ import annotations

import hashlib
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.protocol_folder_service as protocol_folder_service_module
from app.core.deps import AuthContext
from app.models.billing import BillingInterval, Plan, PlanTier, Subscription, SubscriptionStatus
from app.models.client import Client
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.legal_artifact_verification import (
    CitationVerificationStatus,
    LegalCitationVerification,
    LegalReviewFinding,
    LegalReviewSeverity,
)
from app.models.legal_demand import LegalDemand, LegalDemandNature, LegalDemandPhase
from app.models.matter import Matter, PracticeArea
from app.models.protocol_folder import ProtocolFolder
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.schemas.klm_operations import LegalArtifactVersionCreate
from app.services.entitlement_service import (
    CurrentStockLimitExceededError,
    EntitlementDisabledError,
    EntitlementNotConfiguredError,
)
from app.services.klm_operations_service import KlmOperationsService
from app.services.protocol_folder_service import (
    DefaultDocumentContentResolver,
    ProtocolFolderArtifactStateError,
    build_protocol_folder,
    build_zip,
    register_protocol_receipt,
)


class _StubResolver:
    """Injeta bytes conhecidos por `document.id` — nunca toca o filesystem
    real (ver `DocumentContentResolver` em protocol_folder_service.py)."""

    def __init__(self, content_by_document_id: dict[UUID, bytes]) -> None:
        self._content_by_document_id = content_by_document_id

    async def read(self, *, workspace_id, document) -> bytes | None:
        return self._content_by_document_id.get(document.id)


class _PrivateS3Stub:
    def __init__(self, receipt: dict) -> None:
        self.client = self
        self.receipt = receipt
        self.requested: list[tuple[str, str]] = []

    def verify_private_bucket(self) -> None:
        return None

    def get_object(self, **kwargs) -> dict:
        self.requested.append((kwargs["Bucket"], kwargs["Key"]))
        return self.receipt

    def open_local_object(self, _key: str):
        raise AssertionError("storage local não deve ser usado com o gate privado aberto")


@pytest.mark.asyncio
async def test_default_resolver_reads_verified_clean_private_s3_object(
    db_session, auth_context, monkeypatch
):
    payload = b"documento juridico limpo"
    checksum = hashlib.sha256(payload).hexdigest()
    client = await _make_client(db_session, auth_context.workspace_id)
    matter = await _make_matter(db_session, auth_context.workspace_id, client.id)
    document = await _make_procuracao_document(db_session, matter.id)
    storage_key = f"private/{auth_context.workspace_id}/{document.id}/v1"
    record = StorageObject(
        workspace_id=auth_context.workspace_id,
        object_id=document.id,
        version=1,
        matter_id=matter.id,
        client_id=client.id,
        original_filename=document.name,
        declared_content_type="application/pdf",
        detected_content_type="application/pdf",
        expected_byte_size=len(payload),
        byte_size=len(payload),
        expected_sha256=checksum,
        checksum_sha256=checksum,
        storage_key=storage_key,
        state=StorageObjectState.available,
        av_status=StorageAVStatus.clean,
    )
    db_session.add(record)
    await db_session.flush()
    receipt = {
        "Body": BytesIO(payload),
        "Metadata": {"sha256": checksum, "storage-state": "clean"},
        "ServerSideEncryption": "aws:kms",
        "ContentLength": len(payload),
        "ContentType": "application/pdf",
    }
    s3 = _PrivateS3Stub(receipt)
    monkeypatch.setattr(
        protocol_folder_service_module,
        "aws_private_storage_allowed",
        lambda: True,
    )
    monkeypatch.setattr(protocol_folder_service_module.settings, "S3_BUCKET_NAME", "private-test")

    resolved = await DefaultDocumentContentResolver(db_session, s3=s3).read(
        workspace_id=auth_context.workspace_id,
        document=document,
    )

    assert resolved == payload
    assert s3.requested == [("private-test", storage_key)]


async def _seed_protocol_folder_plan(
    db: AsyncSession,
    workspace_id,
    *,
    limit_quantity: int,
    legal_suite_enabled: bool | None = True,
) -> PlanEntitlement:
    plan = Plan(
        name=f"Plano pasta de protocolo {uuid4().hex[:8]}",
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
    protocol_folders_entitlement = PlanEntitlement(
        plan_id=plan.id,
        entitlement=EntitlementKey.protocol_folders,
        enabled=True,
        limit_quantity=limit_quantity,
        hard_limit=True,
    )
    entitlements = [protocol_folders_entitlement]
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
    return protocol_folders_entitlement


async def _make_client(db_session, workspace_id) -> Client:
    client = Client(workspace_id=workspace_id, name="Cliente Protocolo")
    db_session.add(client)
    await db_session.flush()
    return client


async def _make_matter(db_session, workspace_id, client_id) -> Matter:
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_id,
        practice_area=PracticeArea.civel,
        demand_type="Caso pasta de protocolo",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


async def _make_demand(
    db_session, workspace_id, client_id, matter_id, *, responsible_user_id=None
) -> LegalDemand:
    demand = LegalDemand(
        workspace_id=workspace_id,
        nature=LegalDemandNature.judicial,
        area="civel",
        client_id=client_id,
        parties=[],
        phase=LegalDemandPhase.triagem,
        sla={},
        documents_required=[],
        matter_id=matter_id,
        responsible_user_id=responsible_user_id,
    )
    db_session.add(demand)
    await db_session.flush()
    return demand


async def _make_artifact(
    db_session, workspace_id, matter, user_id, *, sections, legal_demand_id, kind="petition"
):
    ops = KlmOperationsService(db_session)
    artifact = await ops.create_legal_artifact_version(
        workspace_id,
        user_id,
        LegalArtifactVersionCreate(
            matter_id=matter.id,
            kind=kind,
            title="Petição — pasta de protocolo",
            content={"sections": sections},
            legal_demand_id=legal_demand_id,
        ),
    )
    await db_session.flush()
    return artifact


def _approve(artifact) -> None:
    artifact.state.status = "approved"


async def _make_procuracao_document(db_session, matter_id, *, name="procuracao.pdf") -> Document:
    document = Document(
        matter_id=matter_id,
        name=name,
        doc_type=DocumentType.procuracao,
        status=DocumentStatus.completo,
        mime_type="application/pdf",
    )
    db_session.add(document)
    await db_session.flush()
    return document


async def _make_paid_custas_entry(db_session, workspace_id, matter_id) -> FinanceEntry:
    entry = FinanceEntry(
        workspace_id=workspace_id,
        matter_id=matter_id,
        title="Custas iniciais",
        entry_type=FinanceEntryType.expense,
        status=FinanceEntryStatus.paid,
        amount=250,
    )
    db_session.add(entry)
    await db_session.flush()
    return entry


async def _seed_minimal(db_session, auth_context, *, sections, responsible=True):
    client = await _make_client(db_session, auth_context.workspace_id)
    matter = await _make_matter(db_session, auth_context.workspace_id, client.id)
    demand = await _make_demand(
        db_session,
        auth_context.workspace_id,
        client.id,
        matter.id,
        responsible_user_id=auth_context.user_id if responsible else None,
    )
    artifact = await _make_artifact(
        db_session,
        auth_context.workspace_id,
        matter,
        auth_context.user_id,
        sections=sections,
        legal_demand_id=demand.id,
    )
    return client, matter, demand, artifact


async def test_build_blocks_when_artifact_not_approved(db_session, auth_context):
    _, _, demand, artifact = await _seed_minimal(
        db_session, auth_context, sections=[{"title": "Fatos", "body": "Texto sem pendência."}]
    )

    result = await build_protocol_folder(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        legal_demand_id=demand.id,
        artifact_id=artifact.id,
        tribunal="TJDFT",
        signature_confirmed=True,
        resolver=_StubResolver({}),
    )

    assert result.folder.state == "bloqueado"
    items = {item["item"] for item in result.folder.manifest["pendencias"]}
    assert "artefato_nao_aprovado" in items


async def test_build_blocks_on_placeholder(db_session, auth_context):
    _, _, demand, artifact = await _seed_minimal(
        db_session,
        auth_context,
        sections=[{"title": "Qualificação", "body": "Requerente [CPF] devidamente qualificado."}],
    )
    _approve(artifact)
    await db_session.flush()

    result = await build_protocol_folder(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        legal_demand_id=demand.id,
        artifact_id=artifact.id,
        tribunal="TJDFT",
        signature_confirmed=True,
        resolver=_StubResolver({}),
    )

    assert result.folder.state == "bloqueado"
    items = {item["item"] for item in result.folder.manifest["pendencias"]}
    assert "placeholder" in items


async def test_build_blocks_on_unverified_citation_and_unblocks_once_verified(
    db_session, auth_context
):
    _, _, demand, artifact = await _seed_minimal(
        db_session,
        auth_context,
        sections=[{"title": "Do direito", "body": "Nos termos da Súmula 7 do STJ."}],
    )
    _approve(artifact)
    await db_session.flush()

    blocked = await build_protocol_folder(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        legal_demand_id=demand.id,
        artifact_id=artifact.id,
        tribunal="TJDFT",
        signature_confirmed=True,
        resolver=_StubResolver({}),
    )
    assert blocked.folder.state == "bloqueado"
    items = {item["item"] for item in blocked.folder.manifest["pendencias"]}
    assert "citacao_nao_verificada" in items

    db_session.add(
        LegalCitationVerification(
            workspace_id=auth_context.workspace_id,
            legal_demand_id=demand.id,
            artifact_version_id=artifact.id,
            citation_text="Súmula 7 do STJ",
            citation_kind="sumula",
            status=CitationVerificationStatus.verificada.value,
            source_reference="https://stj.jus.br/sumula/7",
        )
    )
    await db_session.flush()

    rebuilt = await build_protocol_folder(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        legal_demand_id=demand.id,
        artifact_id=artifact.id,
        tribunal="TJDFT",
        signature_confirmed=True,
        resolver=_StubResolver({}),
    )
    items_after = {item["item"] for item in rebuilt.folder.manifest["pendencias"]}
    assert "citacao_nao_verificada" not in items_after
    assert rebuilt.folder.id == blocked.folder.id  # upsert na mesma linha


async def test_build_blocks_on_critical_review_finding(db_session, auth_context):
    _, _, demand, artifact = await _seed_minimal(
        db_session, auth_context, sections=[{"title": "Fatos", "body": "Texto sem pendência."}]
    )
    _approve(artifact)
    db_session.add(
        LegalReviewFinding(
            workspace_id=auth_context.workspace_id,
            legal_demand_id=demand.id,
            artifact_version_id=artifact.id,
            severity=LegalReviewSeverity.critica.value,
            kind="pedido_sem_fato",
            description="Pedido sem fato de sustentação.",
        )
    )
    await db_session.flush()

    result = await build_protocol_folder(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        legal_demand_id=demand.id,
        artifact_id=artifact.id,
        tribunal="TJDFT",
        signature_confirmed=True,
        resolver=_StubResolver({}),
    )

    assert result.folder.state == "bloqueado"
    items = {item["item"] for item in result.folder.manifest["pendencias"]}
    assert "achado_critico_aberto" in items


async def test_build_pendente_when_procuracao_custas_and_signature_missing(
    db_session, auth_context
):
    _, matter, demand, artifact = await _seed_minimal(
        db_session, auth_context, sections=[{"title": "Fatos", "body": "Texto sem pendência."}]
    )
    _approve(artifact)
    await db_session.flush()

    result = await build_protocol_folder(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        legal_demand_id=demand.id,
        artifact_id=artifact.id,
        tribunal="TJDFT",
        signature_confirmed=False,
        resolver=_StubResolver({}),
    )

    assert result.folder.state == "pendente"
    items = {item["item"] for item in result.folder.manifest["pendencias"]}
    assert {"procuracao", "custas", "assinatura"} <= items


async def test_build_protocolavel_when_all_validations_pass(db_session, auth_context):
    _, matter, demand, artifact = await _seed_minimal(
        db_session, auth_context, sections=[{"title": "Fatos", "body": "Texto sem pendência."}]
    )
    _approve(artifact)
    procuracao = await _make_procuracao_document(db_session, matter.id)
    await _make_paid_custas_entry(db_session, auth_context.workspace_id, matter.id)
    await db_session.flush()

    result = await build_protocol_folder(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        legal_demand_id=demand.id,
        artifact_id=artifact.id,
        tribunal="TJDFT",
        signature_confirmed=True,
        resolver=_StubResolver({procuracao.id: b"%PDF-procuracao-bytes"}),
    )

    assert result.folder.state == "protocolavel"
    assert result.folder.manifest["pendencias"] == []
    folders_present = {row["folder"] for row in result.folder.files}
    assert folders_present == {"01_PETICAO", "02_PROCURACAO"}
    orders = [row["order"] for row in result.folder.files]
    assert orders == sorted(orders)


async def test_build_detects_hash_drift_after_file_changes(db_session, auth_context):
    _, matter, demand, artifact = await _seed_minimal(
        db_session, auth_context, sections=[{"title": "Fatos", "body": "Texto sem pendência."}]
    )
    _approve(artifact)
    procuracao = await _make_procuracao_document(db_session, matter.id)
    await _make_paid_custas_entry(db_session, auth_context.workspace_id, matter.id)
    await db_session.flush()

    first = await build_protocol_folder(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        legal_demand_id=demand.id,
        artifact_id=artifact.id,
        tribunal="TJDFT",
        signature_confirmed=True,
        resolver=_StubResolver({procuracao.id: b"versao-original"}),
    )
    assert first.folder.state == "protocolavel"

    second = await build_protocol_folder(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        legal_demand_id=demand.id,
        artifact_id=artifact.id,
        tribunal="TJDFT",
        signature_confirmed=True,
        resolver=_StubResolver({procuracao.id: b"versao-adulterada"}),
    )

    assert second.folder.state == "bloqueado"
    items = {item["item"] for item in second.folder.manifest["pendencias"]}
    assert "hash_alterado" in items


async def test_build_zip_contains_manifest_and_ordered_files(db_session, auth_context):
    _, matter, demand, artifact = await _seed_minimal(
        db_session, auth_context, sections=[{"title": "Fatos", "body": "Texto sem pendência."}]
    )
    _approve(artifact)
    procuracao = await _make_procuracao_document(db_session, matter.id)
    await _make_paid_custas_entry(db_session, auth_context.workspace_id, matter.id)
    await db_session.flush()

    result = await build_protocol_folder(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        legal_demand_id=demand.id,
        artifact_id=artifact.id,
        tribunal="TJDFT",
        signature_confirmed=True,
        resolver=_StubResolver({procuracao.id: b"%PDF-procuracao-bytes"}),
    )

    zip_bytes = build_zip(manifest=result.folder.manifest, files=result.files)
    with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
        names = archive.namelist()
        assert "00_MANIFESTO/manifesto.json" in names
        assert any(name.startswith("01_PETICAO/") for name in names)
        assert any(name.startswith("02_PROCURACAO/") for name in names)


async def test_register_protocol_receipt_requires_reference(db_session, auth_context):
    _, matter, demand, artifact = await _seed_minimal(
        db_session, auth_context, sections=[{"title": "Fatos", "body": "Texto sem pendência."}]
    )
    _approve(artifact)
    await db_session.flush()
    result = await build_protocol_folder(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        legal_demand_id=demand.id,
        artifact_id=artifact.id,
        tribunal="TJDFT",
        signature_confirmed=True,
        resolver=_StubResolver({}),
    )

    with pytest.raises(ProtocolFolderArtifactStateError):
        await register_protocol_receipt(
            db_session,
            workspace_id=auth_context.workspace_id,
            user_id=auth_context.user_id,
            protocol_folder_id=result.folder.id,
            reference="   ",
        )


async def test_register_protocol_receipt_is_posterior_and_human(db_session, auth_context):
    _, matter, demand, artifact = await _seed_minimal(
        db_session, auth_context, sections=[{"title": "Fatos", "body": "Texto sem pendência."}]
    )
    _approve(artifact)
    await db_session.flush()
    result = await build_protocol_folder(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        legal_demand_id=demand.id,
        artifact_id=artifact.id,
        tribunal="TJDFT",
        signature_confirmed=True,
        resolver=_StubResolver({}),
    )
    assert result.folder.is_protocoled is False

    protocoled_at = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    updated = await register_protocol_receipt(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        protocol_folder_id=result.folder.id,
        reference="PJe 0000001-00.2026.8.07.0001",
        note="Protocolado presencialmente pelo advogado.",
        protocoled_at=protocoled_at,
    )

    assert updated.is_protocoled is True
    assert updated.receipt_reference == "PJe 0000001-00.2026.8.07.0001"
    assert updated.receipt_registered_by_user_id == auth_context.user_id
    assert updated.receipt_registered_at == protocoled_at


def test_no_agent_route_exists_to_execute_protocol() -> None:
    """§10: a plataforma NUNCA protocola — só monta/valida a pasta e
    registra (depois, humano) o recibo de um protocolo já ocorrido."""
    import app.services.protocol_folder_service as module

    public_callables = {
        name for name in dir(module) if not name.startswith("_") and callable(getattr(module, name))
    }
    forbidden_markers = ("protocolar", "submit_to_court", "file_petition", "efetivar_protocolo")
    for name in public_callables:
        lowered = name.lower()
        assert not any(marker in lowered for marker in forbidden_markers), name

    from app.api.v1.router import api_router

    protocol_folder_paths = {
        route.path
        for route in api_router.routes
        if hasattr(route, "path") and "protocol-folder" in route.path
    }
    assert protocol_folder_paths == {
        "/api/v1/legal/demands/{demand_id}/protocol-folder/build",
        "/api/v1/legal/demands/{demand_id}/protocol-folder/{protocol_folder_id}",
        "/api/v1/legal/demands/{demand_id}/protocol-folder/{protocol_folder_id}/export.zip",
        "/api/v1/legal/demands/{demand_id}/protocol-folder/{protocol_folder_id}/receipt",
    }


async def test_protocol_folder_write_fails_closed_without_legal_suite(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    _, _, demand, artifact = await _seed_minimal(
        db_session,
        auth_context,
        sections=[{"title": "Fatos", "body": "Texto sem pendência."}],
    )
    await _seed_protocol_folder_plan(
        db_session,
        auth_context.workspace_id,
        limit_quantity=10,
        legal_suite_enabled=None,
    )

    with pytest.raises(EntitlementNotConfiguredError):
        await build_protocol_folder(
            db_session,
            workspace_id=auth_context.workspace_id,
            user_id=auth_context.user_id,
            legal_demand_id=demand.id,
            artifact_id=artifact.id,
            resolver=_StubResolver({}),
        )


async def test_protocol_folder_quota_counts_new_rows_but_allows_rebuild(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    _, matter, demand, first_artifact = await _seed_minimal(
        db_session,
        auth_context,
        sections=[{"title": "Fatos", "body": "Primeira versão."}],
    )
    await _seed_protocol_folder_plan(
        db_session,
        auth_context.workspace_id,
        limit_quantity=1,
    )

    first = await build_protocol_folder(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        legal_demand_id=demand.id,
        artifact_id=first_artifact.id,
        tribunal="TJDFT",
        resolver=_StubResolver({}),
    )
    rebuilt = await build_protocol_folder(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        legal_demand_id=demand.id,
        artifact_id=first_artifact.id,
        tribunal="TJDFT",
        resolver=_StubResolver({}),
    )
    assert rebuilt.folder.id == first.folder.id

    second_artifact = await _make_artifact(
        db_session,
        auth_context.workspace_id,
        matter,
        auth_context.user_id,
        sections=[{"title": "Fatos", "body": "Segunda versão."}],
        legal_demand_id=demand.id,
    )
    with pytest.raises(CurrentStockLimitExceededError) as captured:
        await build_protocol_folder(
            db_session,
            workspace_id=auth_context.workspace_id,
            user_id=auth_context.user_id,
            legal_demand_id=demand.id,
            artifact_id=second_artifact.id,
            tribunal="TJDFT",
            resolver=_StubResolver({}),
        )
    assert captured.value.current_quantity == 1
    assert captured.value.limit_quantity == 1

    folder_count = await db_session.scalar(
        select(func.count(ProtocolFolder.id)).where(
            ProtocolFolder.workspace_id == auth_context.workspace_id
        )
    )
    assert folder_count == 1


@pytest.mark.parametrize(
    ("revocation_mode", "expected_error"),
    [
        ("disabled", EntitlementDisabledError),
        ("removed", EntitlementNotConfiguredError),
    ],
)
async def test_protocol_folder_rebuild_requires_specific_entitlement(
    db_session: AsyncSession,
    auth_context: AuthContext,
    revocation_mode: str,
    expected_error: type[Exception],
) -> None:
    _, _, demand, artifact = await _seed_minimal(
        db_session,
        auth_context,
        sections=[{"title": "Fatos", "body": "Texto sem pendência."}],
    )
    protocol_entitlement = await _seed_protocol_folder_plan(
        db_session,
        auth_context.workspace_id,
        limit_quantity=1,
    )
    await build_protocol_folder(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        legal_demand_id=demand.id,
        artifact_id=artifact.id,
        tribunal="TJDFT",
        resolver=_StubResolver({}),
    )

    if revocation_mode == "disabled":
        protocol_entitlement.enabled = False
    else:
        await db_session.delete(protocol_entitlement)
    await db_session.flush()

    with pytest.raises(expected_error):
        await build_protocol_folder(
            db_session,
            workspace_id=auth_context.workspace_id,
            user_id=auth_context.user_id,
            legal_demand_id=demand.id,
            artifact_id=artifact.id,
            tribunal="TJDFT",
            resolver=_StubResolver({}),
        )


async def test_protocol_folder_endpoint_maps_stock_limit_to_ent_003(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    _, matter, demand, first_artifact = await _seed_minimal(
        db_session,
        auth_context,
        sections=[{"title": "Fatos", "body": "Primeira versão."}],
    )
    await _seed_protocol_folder_plan(
        db_session,
        auth_context.workspace_id,
        limit_quantity=1,
    )
    await db_session.commit()

    first = await client.post(
        f"/api/v1/legal/demands/{demand.id}/protocol-folder/build",
        json={
            "artifactId": str(first_artifact.id),
            "tribunal": "TJDFT",
            "signatureConfirmed": False,
        },
    )
    assert first.status_code == 200, first.text

    second_artifact = await _make_artifact(
        db_session,
        auth_context.workspace_id,
        matter,
        auth_context.user_id,
        sections=[{"title": "Fatos", "body": "Segunda versão."}],
        legal_demand_id=demand.id,
    )
    await db_session.commit()
    second = await client.post(
        f"/api/v1/legal/demands/{demand.id}/protocol-folder/build",
        json={
            "artifactId": str(second_artifact.id),
            "tribunal": "TJDFT",
            "signatureConfirmed": False,
        },
    )
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "ENT-003"
    assert second.json()["detail"]["entitlement"] == "protocol_folders"
