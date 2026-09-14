"""Prova do contrato de interoperabilidade Word round-trip (Onda 6, §9).

Estes são os testes que PROVAM o contrato antes do editor ser construído
por cima — cada teste corresponde a um item explícito do §9:

  1. export→reimport sem mudança = mesma versão (idempotente)
  2. reimport com mudança = nova versão + diff detectado
  3. reimport de artefato aprovado com conteúdo alterado = aprovação
     invalidada + manifesto invalidado
  4. elemento não-preservável é sinalizado (não some silencioso)
  5. version-id ausente/adulterado no reimport = rejeitado
  6. (bônus, explicitamente pedido no enunciado) versão de origem do
     .docx superada por outra versão = IMPEDE merge automático
"""

from __future__ import annotations

import io
import zipfile
from uuid import uuid4

import pytest

from app.models.client import Client
from app.models.matter import Matter, PracticeArea
from app.models.user import AgentType, User, UserRole
from app.schemas.approval import ApprovalResolveRequest
from app.schemas.klm_operations import LegalArtifactVersionCreate
from app.services.approval_service import ApprovalService
from app.services.klm_operations_service import KlmOperationsService
from app.services.legal_docx_service import (
    KLM_PROPERTY_EVIDENCE_SHA256,
    DocxMergeConflictError,
    DocxVersionRejectedError,
    export_docx,
    reimport_docx,
)


async def _make_client(db_session, workspace_id) -> Client:
    client = Client(workspace_id=workspace_id, name="Cliente Round-Trip")
    db_session.add(client)
    await db_session.flush()
    return client


async def _make_matter(db_session, workspace_id, client_id) -> Matter:
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_id,
        practice_area=PracticeArea.civel,
        demand_type="Caso round-trip",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


async def _make_user(db_session) -> User:
    user = User(
        name="Advogado Round-Trip",
        email=f"roundtrip-{uuid4().hex[:10]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="AR",
        color="#223344",
        is_active=True,
        is_internal_staff=False,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _seed(db_session):
    workspace_id = uuid4()
    client = await _make_client(db_session, workspace_id)
    matter = await _make_matter(db_session, workspace_id, client.id)
    user = await _make_user(db_session)
    return workspace_id, matter, user


async def _create_petition(db_session, workspace_id, matter, user, *, sections):
    ops = KlmOperationsService(db_session)
    artifact = await ops.create_legal_artifact_version(
        workspace_id,
        user.id,
        LegalArtifactVersionCreate(
            matter_id=matter.id,
            kind="petition",
            title="Petição inicial — round-trip",
            content={"sections": sections},
        ),
    )
    await db_session.commit()
    return artifact


def _mutate_document_xml(docx_bytes: bytes, transform) -> bytes:
    """Reabre um .docx em memória, aplica `transform` no XML de um part e
    reempacota — simula tanto uma edição legítima no Word quanto uma
    adulteração maliciosa, dependendo do `transform` passado pelo teste."""
    zin = zipfile.ZipFile(io.BytesIO(docx_bytes))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            data = transform(item.filename, data)
            zout.writestr(item.filename, data)
    return buffer.getvalue()


def _inject_extra_zip_entries(docx_bytes: bytes, entries: dict[str, bytes]) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx_bytes))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        for name, data in entries.items():
            zout.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_export_then_reimport_without_changes_is_idempotent(db_session):
    workspace_id, matter, user = await _seed(db_session)
    artifact = await _create_petition(
        db_session,
        workspace_id,
        matter,
        user,
        sections=[{"title": "Dos fatos", "body": "Linha um.\nLinha dois."}],
    )

    export_result = export_docx(artifact)
    assert export_result.filename.endswith("-v1.docx")
    assert export_result.manifest.fully_preserved

    result = await reimport_docx(
        db_session,
        workspace_id=workspace_id,
        user_id=user.id,
        artifact_id=artifact.id,
        file_bytes=export_result.content,
        expected_version=1,
    )
    await db_session.commit()

    assert result.created_new_version is False
    assert result.content_changed is False
    assert result.artifact.id == artifact.id
    assert result.artifact.version == 1

    ops = KlmOperationsService(db_session)
    all_versions = await ops.list_legal_artifacts(workspace_id, matter_id=matter.id)
    assert [v.version for v in all_versions] == [1]


@pytest.mark.asyncio
async def test_reimport_with_changed_body_creates_new_version_and_detects_diff(db_session):
    workspace_id, matter, user = await _seed(db_session)
    artifact = await _create_petition(
        db_session,
        workspace_id,
        matter,
        user,
        sections=[{"title": "Dos fatos", "body": "Texto original."}],
    )
    export_result = export_docx(artifact)

    edited_bytes = _mutate_document_xml(
        export_result.content,
        lambda name, data: (
            data.replace(b"Texto original.", b"Texto editado no Word.")
            if name == "word/document.xml"
            else data
        ),
    )

    result = await reimport_docx(
        db_session,
        workspace_id=workspace_id,
        user_id=user.id,
        artifact_id=artifact.id,
        file_bytes=edited_bytes,
        expected_version=1,
    )
    await db_session.commit()

    assert result.created_new_version is True
    assert result.content_changed is True
    assert result.artifact.version == 2
    assert result.artifact.content["sections"][0]["body"] == "Texto editado no Word."
    assert result.diff["sections"][0]["change"] == "modified"
    assert result.diff["sections"][0]["oldBody"] == "Texto original."
    assert result.diff["sections"][0]["newBody"] == "Texto editado no Word."
    assert result.approval_invalidated is False

    ops = KlmOperationsService(db_session)
    all_versions = await ops.list_legal_artifacts(workspace_id, matter_id=matter.id)
    assert sorted(v.version for v in all_versions) == [1, 2]


@pytest.mark.asyncio
async def test_reimport_of_approved_version_with_changes_invalidates_approval_and_manifest(
    db_session,
):
    workspace_id, matter, user = await _seed(db_session)
    artifact = await _create_petition(
        db_session,
        workspace_id,
        matter,
        user,
        sections=[{"title": "Do pedido", "body": "Pedido original."}],
    )

    ops = KlmOperationsService(db_session)
    _, approval = await ops.submit_legal_artifact_for_approval(workspace_id, user.id, artifact.id)
    await db_session.commit()

    await ApprovalService(db_session, workspace_id).resolve(
        approval.id,
        ApprovalResolveRequest(status="approved", resolution_reason="Conferido."),
    )
    await db_session.commit()

    refreshed = await ops.get_legal_artifact(workspace_id, artifact.id)
    assert refreshed.state.status == "approved"

    export_result = export_docx(refreshed)
    edited_bytes = _mutate_document_xml(
        export_result.content,
        lambda name, data: (
            data.replace(b"Pedido original.", b"Pedido alterado depois de aprovado.")
            if name == "word/document.xml"
            else data
        ),
    )

    result = await reimport_docx(
        db_session,
        workspace_id=workspace_id,
        user_id=user.id,
        artifact_id=refreshed.id,
        file_bytes=edited_bytes,
        expected_version=1,
    )
    await db_session.commit()

    assert result.created_new_version is True
    assert result.approval_invalidated is True
    assert result.manifest.approval_valid is False
    assert result.manifest.invalidated_reason == "approved_version_content_changed_on_docx_reimport"

    stale = await ops.get_legal_artifact(workspace_id, artifact.id)
    assert stale.state.status == "changes_requested"

    new_version = await ops.get_legal_artifact(workspace_id, result.artifact.id)
    assert new_version.version == 2
    assert new_version.state.status == "draft"


@pytest.mark.asyncio
async def test_unpreservable_elements_are_flagged_not_silently_dropped(db_session):
    workspace_id, matter, user = await _seed(db_session)
    artifact = await _create_petition(
        db_session,
        workspace_id,
        matter,
        user,
        sections=[{"title": "Dos fatos", "body": "Texto simples."}],
    )
    export_result = export_docx(artifact)

    # Simula o usuário colando uma tabela no Word: injeta <w:tbl> e uma
    # imagem em word/media/ que nosso parser de texto não recria.
    contaminated = _mutate_document_xml(
        export_result.content,
        lambda name, data: (
            data.replace(
                b"<w:sectPr/>",
                b"<w:tbl><w:tr><w:tc><w:p><w:r><w:t>celula</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
                b"<w:sectPr/>",
            )
            if name == "word/document.xml"
            else data
        ),
    )
    contaminated = _inject_extra_zip_entries(
        contaminated, {"word/media/image1.png": b"\x89PNG-fake-bytes"}
    )

    result = await reimport_docx(
        db_session,
        workspace_id=workspace_id,
        user_id=user.id,
        artifact_id=artifact.id,
        file_bytes=contaminated,
        expected_version=1,
    )
    await db_session.commit()

    kinds = {item.kind for item in result.manifest.not_preserved}
    assert "table" in kinds
    assert "image" in kinds
    assert result.manifest.fully_preserved is False
    # O texto do parágrafo original continua presente — não desapareceu.
    assert result.artifact.content["sections"][0]["body"] == "Texto simples."


@pytest.mark.asyncio
async def test_reimport_rejects_missing_or_tampered_version_id(db_session):
    workspace_id, matter, user = await _seed(db_session)
    artifact = await _create_petition(
        db_session,
        workspace_id,
        matter,
        user,
        sections=[{"title": "Dos fatos", "body": "Texto."}],
    )
    export_result = export_docx(artifact)

    # Caso 1: identificador de versão ausente (custom.xml removido).
    zin = zipfile.ZipFile(io.BytesIO(export_result.content))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename == "docProps/custom.xml":
                continue
            zout.writestr(item, zin.read(item.filename))
    missing_version_bytes = buffer.getvalue()

    with pytest.raises(DocxVersionRejectedError):
        await reimport_docx(
            db_session,
            workspace_id=workspace_id,
            user_id=user.id,
            artifact_id=artifact.id,
            file_bytes=missing_version_bytes,
            expected_version=1,
        )

    # Caso 2: identificador de versão adulterado (hash de evidência trocado).
    tampered_bytes = _mutate_document_xml(
        export_result.content,
        lambda name, data: (
            data.replace(
                KLM_PROPERTY_EVIDENCE_SHA256.encode() + b'">',
                KLM_PROPERTY_EVIDENCE_SHA256.encode() + b'-x">',
            ).replace(
                b"<vt:lpwstr>" + artifact.evidence_sha256.encode() + b"</vt:lpwstr>",
                b"<vt:lpwstr>0000000000000000000000000000000000000000000000000000000000000000</vt:lpwstr>",
            )
            if name == "docProps/custom.xml"
            else data
        ),
    )

    with pytest.raises(DocxVersionRejectedError):
        await reimport_docx(
            db_session,
            workspace_id=workspace_id,
            user_id=user.id,
            artifact_id=artifact.id,
            file_bytes=tampered_bytes,
            expected_version=1,
        )

    # Nenhuma das duas tentativas rejeitadas criou versão nova.
    ops = KlmOperationsService(db_session)
    all_versions = await ops.list_legal_artifacts(workspace_id, matter_id=matter.id)
    assert [v.version for v in all_versions] == [1]


@pytest.mark.asyncio
async def test_reimport_rejects_docx_zip_bomb_before_xml_expansion(
    db_session,
    monkeypatch,
):
    from app.services import legal_docx_service

    workspace_id, matter, user = await _seed(db_session)
    artifact = await _create_petition(
        db_session,
        workspace_id,
        matter,
        user,
        sections=[{"title": "Dos fatos", "body": "Texto."}],
    )
    monkeypatch.setattr(
        legal_docx_service.settings,
        "IMPORT_ARCHIVE_MAX_COMPRESSION_RATIO",
        2.0,
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"0" * 100_000)

    with pytest.raises(DocxVersionRejectedError, match="compressão"):
        await reimport_docx(
            db_session,
            workspace_id=workspace_id,
            user_id=user.id,
            artifact_id=artifact.id,
            file_bytes=buffer.getvalue(),
            expected_version=1,
        )


@pytest.mark.asyncio
async def test_reimport_of_stale_export_is_rejected_never_auto_merged(db_session):
    workspace_id, matter, user = await _seed(db_session)
    artifact = await _create_petition(
        db_session,
        workspace_id,
        matter,
        user,
        sections=[{"title": "Dos fatos", "body": "Versão um."}],
    )
    stale_export = export_docx(artifact)

    # Alguém avança o documento para a versão 2 por outro caminho
    # (ex.: edição no editor web) enquanto o .docx da v1 ainda circula.
    ops = KlmOperationsService(db_session)
    await ops.create_legal_artifact_version(
        workspace_id,
        user.id,
        LegalArtifactVersionCreate(
            matter_id=matter.id,
            kind="petition",
            title="Petição inicial — round-trip",
            content={"sections": [{"title": "Dos fatos", "body": "Versão dois."}]},
            artifact_key=artifact.artifact_key,
            expected_version=1,
        ),
    )
    await db_session.commit()

    edited_stale_bytes = _mutate_document_xml(
        stale_export.content,
        lambda name, data: (
            data.replace("Versão um.".encode(), b"Editando um .docx desatualizado.")
            if name == "word/document.xml"
            else data
        ),
    )

    with pytest.raises(DocxMergeConflictError):
        await reimport_docx(
            db_session,
            workspace_id=workspace_id,
            user_id=user.id,
            artifact_id=artifact.id,
            file_bytes=edited_stale_bytes,
            expected_version=1,
        )

    all_versions = await ops.list_legal_artifacts(workspace_id, matter_id=matter.id)
    assert sorted(v.version for v in all_versions) == [1, 2]
