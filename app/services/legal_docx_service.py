"""Service: contrato de interoperabilidade Word round-trip (Onda 6, §9).

SPIKE que PROVA por teste o contrato de export/reimport `.docx` ANTES do
editor web ser construído por cima (dono já apanhou disso no editor do
USEIUS/Lastro — ver docs/ONDA6_WORD_ROUNDTRIP_LICOES.md).

`python-docx` e `openpyxl` NÃO estão instalados neste venv (verificado:
`ModuleNotFoundError`) e NÃO foram adicionados só para este spike — por
instrução explícita, implementamos o máximo do contrato com o que já
existe (stdlib `zipfile` + `xml.etree`). Um `.docx` é um pacote OOXML
(zip de XML); construímos e lemos esse pacote diretamente. Isso é MENOS
robusto que `python-docx` para um `.docx` arbitrário vindo de qualquer
versão do Word — a lista honesta do que preserva/não preserva está em
docs/ONDA6_WORD_ROUNDTRIP_LICOES.md, não aqui.

REUSA (não recria):
  * `LegalArtifactVersion`/`LegalArtifactState` (app/models/klm_operations.py,
    Onda 2/5) — a versão é sempre imutável (register_append_only_model já
    bloqueia UPDATE/DELETE por ORM e por trigger SQLite); este módulo NUNCA
    tenta mutar `content` de uma versão existente, só insere versão nova.
  * `KlmOperationsService.create_legal_artifact_version` — mesma trava de
    concorrência (`expected_version`) e mesmo snapshot de origem já usados
    pela API `/api/v1/klm/legal-artifacts`.
  * `evidence_sha256` (já calculado e travado por `before_insert` em
    `LegalArtifactVersion`) como selo de integridade da versão — não
    inventamos segredo novo para detectar adulteração do identificador de
    versão embutido no `.docx`.
  * Status `"changes_requested"` de `LegalArtifactState` (já interpretado
    em `legal_controladoria_panel_service.py` como "minuta bloqueada,
    revisor precisa agir") para representar "aprovação invalidada por
    reimportação com conteúdo alterado" — não é criado um status novo.

Contrato (ver §9 e docs/ONDA6_WORD_ROUNDTRIP_LICOES.md):
  * `export_docx(artifact)` → bytes de um `.docx` editável carregando o
    identificador da versão (`klmArtifactVersionRowId` +
    `klmArtifactEvidenceSha256`) em custom property OOXML.
  * `reimport_docx(...)` → detecta mudança, gera SEMPRE versão nova (nunca
    sobrescreve), preserva parágrafos/títulos/comentários suportados,
    SINALIZA (nunca esconde) elemento não preservável, invalida aprovação
    + manifesto se o conteúdo de uma versão aprovada mudou, e IMPEDE
    merge automático em conflito (versão de origem do `.docx` não é mais
    a versão mais recente do documento).

SEM frontend nesta onda.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from xml.etree import ElementTree as ET

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.request_body import (
    ArchiveSafetyLimits,
    UnsafeArchiveError,
    inspect_zip_archive,
)
from app.models.klm_operations import LegalArtifactVersion
from app.schemas.klm_operations import LegalArtifactVersionCreate
from app.services.klm_operations_service import KlmOperationsService

# ── Namespaces OOXML ─────────────────────────────────────────────────────
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_CUSTOM_PROPS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
_VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
_CUSTOM_PROPS_FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"

# Nomes das custom properties que carregam o identificador de versão do KLM.
KLM_PROPERTY_VERSION_ROW_ID = "klmArtifactVersionRowId"
KLM_PROPERTY_EVIDENCE_SHA256 = "klmArtifactEvidenceSha256"

_CONTENT_TYPES_XML = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    b'<Default Extension="rels" ContentType='
    b'"application/vnd.openxmlformats-package.relationships+xml"/>'
    b'<Default Extension="xml" ContentType="application/xml"/>'
    b'<Override PartName="/word/document.xml" ContentType='
    b'"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    b'<Override PartName="/docProps/core.xml" ContentType='
    b'"application/vnd.openxmlformats-package.core-properties+xml"/>'
    b'<Override PartName="/docProps/custom.xml" ContentType='
    b'"application/vnd.openxmlformats-officedocument.custom-properties+xml"/>'
    b"</Types>"
)

_ROOT_RELS_XML = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    b'<Relationship Id="rId1" Type='
    b'"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    b'Target="word/document.xml"/>'
    b'<Relationship Id="rId2" Type='
    b'"http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
    b'Target="docProps/core.xml"/>'
    b'<Relationship Id="rId3" Type='
    b'"http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties" '
    b'Target="docProps/custom.xml"/>'
    b"</Relationships>"
)


def _w(tag: str) -> str:
    return f"{{{_W_NS}}}{tag}"


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug or "documento"


# ── Erros do contrato (nunca merge automático, nunca versão adulterada) ──


class DocxArtifactNotFoundError(LookupError):
    """O artifact_id informado não existe neste workspace."""


class DocxVersionRejectedError(ValueError):
    """O `.docx` não tem identificador de versão do KLM válido e íntegro.

    Cobre tanto "ausente" (custom property não existe) quanto "adulterado"
    (existe mas não corresponde a nenhuma versão íntegra do workspace).
    Nunca tentamos adivinhar a versão de origem nesses casos.
    """


class DocxMergeConflictError(ValueError):
    """A versão de origem do `.docx` não é mais a versão mais recente.

    Alguém avançou o documento entre a exportação e esta reimportação.
    Nenhuma tentativa de mesclagem automática é feita — o usuário precisa
    exportar de novo a partir da versão atual.
    """


# ── Estruturas de retorno (o "manifesto" deste round-trip) ──────────────


@dataclass(frozen=True)
class UnpreservedElement:
    """Um elemento do `.docx` reimportado que este contrato NÃO recria no
    conteúdo estruturado — sinalizado, nunca descartado em silêncio."""

    kind: str
    detail: str


@dataclass(frozen=True)
class DocxManifest:
    """Manifesto de fidelidade deste export/reimport específico."""

    supports: list[str]
    not_preserved: list[UnpreservedElement] = field(default_factory=list)
    approval_valid: bool = True
    invalidated_reason: str | None = None

    @property
    def fully_preserved(self) -> bool:
        return not self.not_preserved


@dataclass(frozen=True)
class DocxExportResult:
    artifact_id: UUID
    artifact_key: UUID
    version: int
    filename: str
    content: bytes
    manifest: DocxManifest


@dataclass(frozen=True)
class DocxReimportResult:
    artifact: LegalArtifactVersion
    created_new_version: bool
    content_changed: bool
    manifest: DocxManifest
    approval_invalidated: bool
    diff: dict[str, Any]


# ── export_docx ───────────────────────────────────────────────────────────


def export_docx(artifact: LegalArtifactVersion) -> DocxExportResult:
    """Gera um `.docx` editável a partir de UMA versão imutável já persistida.

    O identificador de versão (linha imutável + evidence_sha256 já travado
    por `before_insert`) vai embutido em custom property OOXML — é esse par
    que `reimport_docx` usa para nunca aceitar um `.docx` sem procedência
    ou adulterado.
    """
    if not artifact.evidence_sha256:
        raise ValueError(
            "Artefato sem evidence_sha256 persistido; certifique-se de que a "
            "versão passou por INSERT (flush) antes de exportar."
        )
    sections = list((artifact.content or {}).get("sections") or [])
    data = _build_docx_package(
        title=artifact.title,
        sections=sections,
        version_row_id=str(artifact.id),
        evidence_sha256=artifact.evidence_sha256,
    )
    manifest = DocxManifest(
        supports=["paragraphs", "headings (Heading1 = título de seção)"],
        not_preserved=[],
        approval_valid=True,
        invalidated_reason=None,
    )
    return DocxExportResult(
        artifact_id=artifact.id,
        artifact_key=artifact.artifact_key,
        version=artifact.version,
        filename=f"{_slugify(artifact.title)}-v{artifact.version}.docx",
        content=data,
        manifest=manifest,
    )


def _build_docx_package(
    *, title: str, sections: list[dict], version_row_id: str, evidence_sha256: str
) -> bytes:
    paragraphs: list[str] = []
    for section in sections:
        section_title = str(section.get("title") or "")
        section_body = str(section.get("body") or "")
        paragraphs.append(
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
            f'<w:r><w:t xml:space="preserve">{_xml_escape(section_title)}</w:t></w:r></w:p>'
        )
        for line in section_body.split("\n"):
            paragraphs.append(
                f'<w:p><w:r><w:t xml:space="preserve">{_xml_escape(line)}</w:t></w:r></w:p>'
            )

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{_W_NS}"><w:body>'
        + "".join(paragraphs)
        + "<w:sectPr/></w:body></w:document>"
    ).encode("utf-8")

    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        "<cp:coreProperties "
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{_xml_escape(title)}</dc:title>"
        "</cp:coreProperties>"
    ).encode()

    custom_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Properties xmlns="{_CUSTOM_PROPS_NS}" xmlns:vt="{_VT_NS}">'
        f'<property fmtid="{_CUSTOM_PROPS_FMTID}" pid="2" name="{KLM_PROPERTY_VERSION_ROW_ID}">'
        f"<vt:lpwstr>{_xml_escape(version_row_id)}</vt:lpwstr></property>"
        f'<property fmtid="{_CUSTOM_PROPS_FMTID}" pid="3" name="{KLM_PROPERTY_EVIDENCE_SHA256}">'
        f"<vt:lpwstr>{_xml_escape(evidence_sha256)}</vt:lpwstr></property>"
        "</Properties>"
    ).encode()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", _ROOT_RELS_XML)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("docProps/core.xml", core_xml)
        zf.writestr("docProps/custom.xml", custom_xml)
    return buffer.getvalue()


# ── Parsing do .docx reimportado ──────────────────────────────────────────


@dataclass
class _ParsedDocx:
    sections: list[dict]
    comments: list[dict]
    custom_properties: dict[str, str]
    not_preserved: list[UnpreservedElement]


def _parse_docx_package(data: bytes) -> _ParsedDocx:
    try:
        inspection = inspect_zip_archive(
            data,
            limits=ArchiveSafetyLimits(
                max_entries=settings.IMPORT_ARCHIVE_MAX_ENTRIES,
                max_uncompressed_bytes=settings.IMPORT_ARCHIVE_MAX_UNCOMPRESSED_BYTES,
                max_compression_ratio=settings.IMPORT_ARCHIVE_MAX_COMPRESSION_RATIO,
                max_entry_uncompressed_bytes=settings.LEGAL_DOCX_UPLOAD_MAX_BYTES,
            ),
        )
    except UnsafeArchiveError as exc:
        raise DocxVersionRejectedError(str(exc)) from exc
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise DocxVersionRejectedError(
            "O arquivo enviado não é um .docx válido (não é um zip OOXML legível)."
        ) from exc

    names = set(inspection.names)
    if "word/document.xml" not in names:
        raise DocxVersionRejectedError(
            "O arquivo enviado não contém word/document.xml; não é um .docx válido."
        )

    document_xml = zf.read("word/document.xml")

    custom_properties: dict[str, str] = {}
    if "docProps/custom.xml" in names:
        custom_properties = _parse_custom_properties(zf.read("docProps/custom.xml"))

    comments: list[dict] = []
    if "word/comments.xml" in names:
        comments = _parse_comments(zf.read("word/comments.xml"))

    not_preserved = _scan_not_preserved(names, document_xml)
    sections = _parse_sections(document_xml)

    return _ParsedDocx(
        sections=sections,
        comments=comments,
        custom_properties=custom_properties,
        not_preserved=not_preserved,
    )


def _scan_not_preserved(names: set[str], document_xml: bytes) -> list[UnpreservedElement]:
    not_preserved: list[UnpreservedElement] = []
    for media_name in sorted(n for n in names if n.startswith("word/media/")):
        not_preserved.append(UnpreservedElement(kind="image", detail=media_name))
    for name in sorted(names):
        if re.match(r"word/(header|footer)\d*\.xml$", name):
            not_preserved.append(UnpreservedElement(kind="header_or_footer", detail=name))
    if "word/footnotes.xml" in names:
        not_preserved.append(UnpreservedElement(kind="footnotes", detail="word/footnotes.xml"))
    if "word/endnotes.xml" in names:
        not_preserved.append(UnpreservedElement(kind="endnotes", detail="word/endnotes.xml"))

    raw_text = document_xml.decode("utf-8", errors="replace")
    if "<w:tbl" in raw_text:
        not_preserved.append(
            UnpreservedElement(kind="table", detail="word/document.xml contém <w:tbl>")
        )
    if "<w:drawing" in raw_text or "<w:pict" in raw_text or "<w:object" in raw_text:
        not_preserved.append(
            UnpreservedElement(
                kind="embedded_object_or_drawing",
                detail="word/document.xml contém <w:drawing>/<w:pict>/<w:object>",
            )
        )
    has_tracked_changes = (
        "<w:ins " in raw_text
        or "<w:ins>" in raw_text
        or "<w:del " in raw_text
        or "<w:del>" in raw_text
    )
    if has_tracked_changes:
        not_preserved.append(
            UnpreservedElement(
                kind="tracked_change",
                detail="word/document.xml contém <w:ins>/<w:del> (controle de alterações do Word)",
            )
        )
    if "mc:AlternateContent" in raw_text:
        not_preserved.append(
            UnpreservedElement(
                kind="alternate_content", detail="word/document.xml contém mc:AlternateContent"
            )
        )
    return not_preserved


def _parse_sections(document_xml: bytes) -> list[dict]:
    root = ET.fromstring(document_xml)
    body = root.find(_w("body"))
    if body is None:
        return []

    sections: list[dict] = []
    current_title: str | None = None
    current_body_lines: list[str] = []

    def _flush() -> None:
        if current_title is not None:
            sections.append({"title": current_title, "body": "\n".join(current_body_lines)})

    for paragraph in body.findall(_w("p")):
        style = None
        ppr = paragraph.find(_w("pPr"))
        if ppr is not None:
            pstyle = ppr.find(_w("pStyle"))
            if pstyle is not None:
                style = pstyle.get(_w("val"))
        text = "".join(t.text or "" for t in paragraph.iter(_w("t")))
        if style == "Heading1":
            _flush()
            current_title = text
            current_body_lines = []
        else:
            if current_title is None:
                # Parágrafo antes de qualquer heading — não descartamos: vira
                # o corpo de uma seção sem título em vez de desaparecer.
                current_title = ""
            current_body_lines.append(text)
    _flush()
    return sections


def _parse_custom_properties(data: bytes) -> dict[str, str]:
    root = ET.fromstring(data)
    result: dict[str, str] = {}
    for prop in root.findall(f"{{{_CUSTOM_PROPS_NS}}}property"):
        name = prop.get("name")
        if not name:
            continue
        children = list(prop)
        result[name] = children[0].text or "" if children else ""
    return result


def _parse_comments(data: bytes) -> list[dict]:
    root = ET.fromstring(data)
    comments: list[dict] = []
    for comment in root.findall(_w("comment")):
        text = "".join(t.text or "" for t in comment.iter(_w("t")))
        comments.append(
            {
                "id": comment.get(_w("id")),
                "author": comment.get(_w("author")) or "",
                "date": comment.get(_w("date")) or "",
                "text": text,
            }
        )
    return comments


def _diff_sections(old_sections: list[dict], new_sections: list[dict]) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    for index in range(max(len(old_sections), len(new_sections))):
        old = old_sections[index] if index < len(old_sections) else None
        new = new_sections[index] if index < len(new_sections) else None
        if old is None:
            changes.append(
                {
                    "index": index,
                    "change": "added",
                    "newTitle": new.get("title") if new else None,
                    "newBody": new.get("body") if new else None,
                }
            )
        elif new is None:
            changes.append(
                {
                    "index": index,
                    "change": "removed",
                    "oldTitle": old.get("title"),
                    "oldBody": old.get("body"),
                }
            )
        elif old.get("title") != new.get("title") or old.get("body") != new.get("body"):
            changes.append(
                {
                    "index": index,
                    "change": "modified",
                    "oldTitle": old.get("title"),
                    "newTitle": new.get("title"),
                    "oldBody": old.get("body"),
                    "newBody": new.get("body"),
                }
            )
    return {"sections": changes}


def _now() -> datetime:
    return datetime.now(UTC)


# ── reimport_docx ─────────────────────────────────────────────────────────


async def reimport_docx(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    artifact_id: UUID,
    file_bytes: bytes,
    expected_version: int,
) -> DocxReimportResult:
    """Reimporta um `.docx` editado, seguindo o contrato §9 à risca:

    * version-id ausente/adulterado → `DocxVersionRejectedError` (rejeita,
      nunca assume a versão de origem).
    * versão de origem não é mais a mais recente → `DocxMergeConflictError`
      (nunca tenta mesclar automaticamente).
    * sem mudança de conteúdo → devolve a MESMA versão (idempotente).
    * com mudança → cria versão NOVA (nunca sobrescreve); se a versão
      superada estava aprovada, invalida a aprovação (`changes_requested`)
      e o manifesto (`manifest.approval_valid = False`).
    * elemento não preservável → sinalizado em `manifest.not_preserved`,
      nunca descartado em silêncio.
    """
    ops = KlmOperationsService(db)
    anchor = await ops.get_legal_artifact(workspace_id, artifact_id)
    if anchor is None:
        raise DocxArtifactNotFoundError("Documento jurídico não encontrado no workspace.")

    parsed = _parse_docx_package(file_bytes)

    row_id_raw = parsed.custom_properties.get(KLM_PROPERTY_VERSION_ROW_ID)
    evidence_raw = parsed.custom_properties.get(KLM_PROPERTY_EVIDENCE_SHA256)
    if not row_id_raw or not evidence_raw:
        raise DocxVersionRejectedError(
            "O .docx não contém o identificador de versão do KLM "
            f"({KLM_PROPERTY_VERSION_ROW_ID}/{KLM_PROPERTY_EVIDENCE_SHA256}); "
            "reimportação recusada — nunca assumimos a versão de origem."
        )
    try:
        source_version_row_id = UUID(row_id_raw)
    except (ValueError, AttributeError) as exc:
        raise DocxVersionRejectedError(
            "O identificador de versão do KLM embutido no .docx é inválido."
        ) from exc

    source_version = await db.scalar(
        select(LegalArtifactVersion).where(
            LegalArtifactVersion.id == source_version_row_id,
            LegalArtifactVersion.workspace_id == workspace_id,
        )
    )
    if source_version is None or source_version.evidence_sha256 != evidence_raw:
        raise DocxVersionRejectedError(
            "O identificador de versão do KLM embutido no .docx não corresponde a "
            "nenhuma versão íntegra deste workspace — o arquivo pode ter sido "
            "adulterado ou não veio de uma exportação do KLM."
        )
    if source_version.artifact_key != anchor.artifact_key:
        raise DocxVersionRejectedError(
            "O .docx enviado pertence a outro documento jurídico deste workspace."
        )

    latest_version_number = await db.scalar(
        select(func.max(LegalArtifactVersion.version)).where(
            LegalArtifactVersion.workspace_id == workspace_id,
            LegalArtifactVersion.artifact_key == anchor.artifact_key,
        )
    )
    if source_version.version != latest_version_number:
        raise DocxMergeConflictError(
            f"Este .docx foi exportado da versão {source_version.version}, mas a versão "
            f"atual do documento é {latest_version_number}. Exporte de novo antes de "
            "reimportar — nenhuma mesclagem automática é feita."
        )
    if expected_version != source_version.version:
        raise DocxMergeConflictError(
            f"expected_version ({expected_version}) não corresponde à versão de origem "
            f"do .docx ({source_version.version})."
        )

    latest = source_version
    latest_state = latest.state

    new_content: dict[str, Any] = {"sections": parsed.sections}
    if parsed.comments:
        new_content["comments"] = parsed.comments

    manifest = DocxManifest(
        supports=["paragraphs", "headings", "comentários (word/comments.xml)"],
        not_preserved=parsed.not_preserved,
        approval_valid=True,
        invalidated_reason=None,
    )

    if new_content == (latest.content or {}):
        return DocxReimportResult(
            artifact=latest,
            created_new_version=False,
            content_changed=False,
            manifest=manifest,
            approval_invalidated=False,
            diff={},
        )

    diff = _diff_sections((latest.content or {}).get("sections") or [], parsed.sections)

    new_artifact = await ops.create_legal_artifact_version(
        workspace_id,
        user_id,
        LegalArtifactVersionCreate(
            matter_id=latest.matter_id,
            kind=latest.kind,
            title=latest.title,
            content=new_content,
            artifact_key=latest.artifact_key,
            expected_version=latest.version,
        ),
    )

    approval_invalidated = False
    previous_status = latest_state.status if latest_state is not None else None
    if latest_state is not None and previous_status == "approved":
        latest_state.status = "changes_requested"
        latest_state.updated_at = _now()
        approval_invalidated = True
        manifest = replace(
            manifest,
            approval_valid=False,
            invalidated_reason="approved_version_content_changed_on_docx_reimport",
        )
        await ops.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="legal_artifact_approval_invalidated_by_docx_reimport",
            entity_type="legal_artifact_version",
            entity_id=latest.id,
            summary=(
                f"Reimportação de .docx alterou o conteúdo da versão aprovada "
                f"{latest.version} de '{latest.title}'; aprovação invalidada, "
                f"nova versão {new_artifact.version} criada para revisão."
            ),
            after_data={
                "previousStatus": "approved",
                "newStatus": "changes_requested",
                "newVersion": new_artifact.version,
            },
        )

    await db.flush()

    return DocxReimportResult(
        artifact=new_artifact,
        created_new_version=True,
        content_changed=True,
        manifest=manifest,
        approval_invalidated=approval_invalidated,
        diff=diff,
    )
