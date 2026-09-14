"""Service: ProtocolFolderService — pasta de protocolo + manifesto (Onda 7,
Plano Mestre §10).

A plataforma PREPARA e VALIDA a pasta de protocolo; ela NUNCA protocola.
`PROTOCOLO É EXCLUSIVAMENTE HUMANO`: não há, neste módulo nem em
`app/api/v1/endpoints/protocol_folder.py`, nenhuma função/rota que envie
algo a um tribunal. O único caminho para o `ProtocolFolder` sair de
`bloqueado`/`pendente` para `protocolavel` é `build_protocol_folder`
recalcular TODAS as validações do §10 e todas passarem; o único caminho
para registrar que o protocolo de fato aconteceu é
`register_protocol_receipt` — sempre chamado DEPOIS do evento real, nunca
disparado por um agente (ver
tests/services/test_protocol_folder_service.py::
test_no_agent_route_exists_to_execute_protocol e
tests/api/test_protocol_folder.py).

REUSA (não duplica):
  * `Case360Service.get_case_360` (Onda 5) — cliente, matter, processo(s),
    documentos, financeiro, matriz fato-prova-pedido: a MESMA agregação
    usada pela tela Caso 360, nunca uma consulta paralela.
  * `legal_editor_service.recompute_checklist` (Onda 6) — placeholder/
    procuração/custas do artefato já são calculados lá; este serviço não
    reimplementa essa lógica, só lê o resultado.
  * `citation_verification_service.extract_citations` +
    `LegalCitationVerification` (Onda 6) — o item "citações verificadas"
    usa a MESMA extração por regex e a MESMA tabela de veredito já usada
    por `ApprovalService._require_legal_artifact_protocolable`.
  * `LegalReviewFinding` (Onda 6) — achado crítico aberto bloqueia, mesma
    regra da aprovação.
  * `legal_docx_service.export_docx` (Onda 6) — o arquivo da subpasta
    `01_PETICAO` é o MESMO `.docx` já produzido pelo round-trip Word,
    nunca uma serialização paralela.
  * `Document`/`StorageObject` (GED existente) — os arquivos das demais
    subpastas são os documentos já anexados ao caso; nenhum upload novo
    nasce aqui.

Limitação documentada (honesta, não escondida):
  * "assinatura" não tem verificação criptográfica nesta onda (não há
    nenhuma integração de assinatura digital neste código-base) — o
    campo `signature_confirmed` é SEMPRE uma confirmação humana explícita
    passada por quem monta a pasta, nunca inferida.
  * a leitura de bytes reais usa o mesmo `StorageObject` e a mesma verificação
    de recibo SSE-KMS/checksum/MIME do armazenamento privado. Em dev/teste,
    usa o objeto local; em staging/produção, lê somente objetos S3 já marcados
    como limpos pelo antivírus.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.klm_runtime import aws_private_storage_allowed
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.klm_operations import LegalArtifactVersion
from app.models.legal_artifact_verification import (
    CitationVerificationStatus,
    LegalCitationVerification,
    LegalReviewFinding,
    LegalReviewSeverity,
)
from app.models.legal_demand import LegalDemand
from app.models.protocol_folder import (
    PROTOCOL_FOLDER_SUBFOLDERS,
    ProtocolFolder,
    ProtocolFolderState,
)
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.services.case_360_service import Case360NotFoundError, Case360Result, Case360Service
from app.services.citation_verification_service import extract_citations
from app.services.entitlement_service import (
    EntitlementDisabledError,
    EntitlementEnforcementService,
    EntitlementKey,
)
from app.services.legal_docx_service import export_docx
from app.services.legal_editor_service import LegalEditorArtifactNotFoundError, recompute_checklist
from app.services.local_object_storage_service import (
    LocalObjectIntegrityError,
    LocalObjectUnavailableError,
)
from app.services.private_document_storage_service import PrivateDocumentStorageService
from app.services.s3_service import S3Service, StorageUnavailableError

MANIFEST_FOLDER = "00_MANIFESTO"
PETITION_FOLDER = "01_PETICAO"
POWER_OF_ATTORNEY_FOLDER = "02_PROCURACAO"
PERSONAL_DOCS_FOLDER = "03_DOCS_PESSOAIS"
PROOF_FOLDER = "04_PROVAS"
PROCEDURAL_DOCS_FOLDER = "05_DOCS_PROCESSUAIS"
COSTS_FOLDER = "06_CUSTAS"
ORIGINALS_FOLDER = "99_ORIGINAIS"

# Pastas indispensáveis por `LegalArtifactVersion.kind` — quando o ato
# gerado JÁ É a procuração (`power_of_attorney`), o próprio `01_PETICAO`
# cumpre esse papel; `02_PROCURACAO` deixa de ser exigida.
_INDISPENSABLE_FOLDERS_BY_KIND: dict[str, frozenset[str]] = {
    "petition": frozenset({PETITION_FOLDER, POWER_OF_ATTORNEY_FOLDER}),
    "power_of_attorney": frozenset({PETITION_FOLDER}),
    "contract": frozenset({PETITION_FOLDER}),
    "proposal": frozenset({PETITION_FOLDER}),
}

# Mapa honesto do vocabulário fechado de `DocumentType` (app/models/document.py)
# para a subpasta correspondente. `peticao_inicial` só cai aqui quando foi
# ANEXADA como upload (não é o artefato gerado pelo editor, que já vai em
# `01_PETICAO` via `export_docx`). Não existe, hoje, um `DocumentType`
# dedicado a "documento pessoal" (RG/CPF/comprovante de residência do
# cliente) — `03_DOCS_PESSOAIS` fica estruturalmente presente na pasta,
# mas nunca populada por este mapa (limitação documentada, não fabricada).
_DOCUMENT_TYPE_TO_FOLDER: dict[DocumentType, str] = {
    DocumentType.procuracao: POWER_OF_ATTORNEY_FOLDER,
    DocumentType.comprovante: COSTS_FOLDER,
    DocumentType.sentenca: PROCEDURAL_DOCS_FOLDER,
    DocumentType.recurso: PROCEDURAL_DOCS_FOLDER,
    DocumentType.contrato: PROCEDURAL_DOCS_FOLDER,
    DocumentType.peticao_inicial: PROCEDURAL_DOCS_FOLDER,
    DocumentType.outro: ORIGINALS_FOLDER,
}

# As oito constantes de subpasta acima DEVEM ser exatamente
# `PROTOCOL_FOLDER_SUBFOLDERS` (app/models/protocol_folder.py) — a
# asserção falha em import-time (nunca em runtime de request) se alguém
# editar um dos dois lados sem editar o outro.
assert (
    MANIFEST_FOLDER,
    PETITION_FOLDER,
    POWER_OF_ATTORNEY_FOLDER,
    PERSONAL_DOCS_FOLDER,
    PROOF_FOLDER,
    PROCEDURAL_DOCS_FOLDER,
    COSTS_FOLDER,
    ORIGINALS_FOLDER,
) == PROTOCOL_FOLDER_SUBFOLDERS

_ALLOWED_EXTENSIONS = frozenset({"docx", "pdf", "jpg", "jpeg", "png"})

_GLOBAL_PLACEHOLDER_PATTERN = re.compile(
    r"\[\s*[●•]+.*?\]|\[\s*(?:CPF|CNPJ|PREENCHER)\b[^\]]*\]|\bXXX+\b|\[\s*\.\.\.\s*\]|_{4,}",
    re.IGNORECASE,
)


class ProtocolFolderNotFoundError(HTTPException):
    def __init__(self, detail: str = "Pasta de protocolo não encontrada no workspace.") -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class ProtocolFolderArtifactStateError(HTTPException):
    """Erro 422 explícito — nunca deixa a montagem seguir com dado inconsistente."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


class ProtocolFolderSyncNotConfiguredError(HTTPException):
    """Gate fechado honesto (§10 item 3, "opção de sync... gated"): nenhuma
    credencial de Drive/OneDrive/SharePoint está integrada neste
    código-base hoje (grep confirma: sem `msal`/`google-api-python-client`
    conectado a este fluxo). Nunca finge sucesso nem grava um espelho
    fictício — levanta explícito para o chamador decidir (mesmo espírito
    de `NullCitationSourceProvider`)."""

    def __init__(self, provider: str) -> None:
        super().__init__(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                f"Sincronização com '{provider}' não está configurada nesta instância — "
                "baixe o .zip (export.zip) e faça o upload manual até essa integração existir."
            ),
        )


@dataclass(frozen=True)
class ProtocolFolderFile:
    order: int
    folder: str
    filename: str
    source_type: str  # "artifact" | "document"
    source_id: UUID
    sha256: str
    size_bytes: int
    extension: str
    content: bytes = field(repr=False, compare=False)

    def as_manifest_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "folder": self.folder,
            "filename": self.filename,
            "sourceType": self.source_type,
            "sourceId": str(self.source_id),
            "sha256": self.sha256,
            "sizeBytes": self.size_bytes,
            "extension": self.extension,
        }


@dataclass(frozen=True)
class ProtocolFolderBuildResult:
    folder: ProtocolFolder
    files: list[ProtocolFolderFile]


class DocumentContentResolver(Protocol):
    """Fonte injetável de bytes de um `Document` já anexado ao caso —
    default lê o armazenamento local (dev/teste); testes injetam um stub
    para não depender de filesystem real (ver
    tests/services/test_protocol_folder_service.py)."""

    async def read(self, *, workspace_id: UUID, document: Document) -> bytes | None: ...


class DefaultDocumentContentResolver:
    """Lê a versão física mais recente disponível em `StorageObject`.

    A leitura é interna, servidor-a-servidor, sem emitir URL pública. Em S3,
    o objeto precisa estar disponível, limpo e confirmar o recibo privado;
    em modo local, preserva o adapter de desenvolvimento/teste. Qualquer
    divergência devolve `None` e impede a pasta de fingir que contém um
    documento inexistente.

    A sessão é injetada no construtor (nunca um contexto global
    compartilhado) — cada chamada de `build_protocol_folder` cria a sua
    própria instância, seguro sob concorrência de `asyncio`."""

    def __init__(self, db: AsyncSession, *, s3: S3Service | None = None) -> None:
        self._db = db
        self._s3 = s3 or S3Service()

    async def read(self, *, workspace_id: UUID, document: Document) -> bytes | None:
        record = await self._db.scalar(
            select(StorageObject)
            .where(
                StorageObject.workspace_id == workspace_id,
                StorageObject.object_id == document.id,
                StorageObject.state == StorageObjectState.available,
                StorageObject.deleted_at.is_(None),
            )
            .order_by(StorageObject.version.desc())
            .limit(1)
        )
        if record is None or not record.storage_key:
            return None
        if aws_private_storage_allowed():
            if record.av_status is not StorageAVStatus.clean or not record.checksum_sha256:
                return None
            private_storage = PrivateDocumentStorageService(self._db, s3=self._s3)
            try:
                private_storage.verify_private_bucket()
                receipt = await asyncio.to_thread(
                    self._s3.client.get_object,
                    Bucket=settings.S3_BUCKET_NAME,
                    Key=record.storage_key,
                )
                private_storage._verify_receipt(record, receipt, state="clean")
                handle = receipt["Body"]
                try:
                    return await asyncio.to_thread(handle.read)
                finally:
                    handle.close()
            except (
                BotoCoreError,
                ClientError,
                KeyError,
                LocalObjectIntegrityError,
                LocalObjectUnavailableError,
                OSError,
                RuntimeError,
                StorageUnavailableError,
                ValueError,
            ):
                return None
        try:
            handle = await asyncio.to_thread(self._s3.open_local_object, record.storage_key)
        except (OSError, ValueError, StorageUnavailableError):
            return None
        try:
            return await asyncio.to_thread(handle.read)
        finally:
            handle.close()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug or "arquivo"


def _extension_for(filename: str) -> str:
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def _has_global_placeholder_marker(content: object) -> bool:
    """Varredura extra sobre o conteúdo do artefato pela regra global do
    escritório (nunca entregar com placeholder) — cobre marcadores que
    `compute_checklist` (Onda 6) não cobre porque não são `{{var}}`/
    marcador interno do editor (ex.: `[CPF]`, `XXX`, `[...]`, `____`)."""
    from app.services.citation_verification_service import iter_text_blocks

    return any(_GLOBAL_PLACEHOLDER_PATTERN.search(block) for block in iter_text_blocks(content))


async def _require_demand(db: AsyncSession, workspace_id: UUID, demand_id: UUID) -> LegalDemand:
    demand = await db.scalar(
        select(LegalDemand).where(
            LegalDemand.workspace_id == workspace_id,
            LegalDemand.id == demand_id,
        )
    )
    if demand is None:
        raise ProtocolFolderNotFoundError("Demanda jurídica não encontrada.")
    return demand


async def _require_artifact(
    db: AsyncSession, workspace_id: UUID, demand: LegalDemand, artifact_id: UUID
) -> LegalArtifactVersion:
    artifact = await db.scalar(
        select(LegalArtifactVersion).where(
            LegalArtifactVersion.id == artifact_id,
            LegalArtifactVersion.workspace_id == workspace_id,
        )
    )
    if artifact is None:
        raise ProtocolFolderNotFoundError("Documento jurídico (artefato) não encontrado.")
    if artifact.legal_demand_id != demand.id:
        raise ProtocolFolderArtifactStateError(
            "O artefato informado não pertence à demanda jurídica informada."
        )
    if artifact.matter_id is None:
        raise ProtocolFolderArtifactStateError(
            "Artefato sem Caso operacional (Matter) vinculado; a pasta de protocolo "
            "exige um Matter."
        )
    return artifact


def _pending(item: str, detail: str) -> dict[str, str]:
    return {"item": item, "detail": detail}


def _select_documents_for_proof_folder(result: Case360Result) -> set[UUID]:
    return {
        link.document_id
        for link in result.strategy_matrix.proof_links
        if link.document_id is not None and link.status.value == "tem"
    }


async def _build_files(
    *,
    db: AsyncSession,
    workspace_id: UUID,
    artifact: LegalArtifactVersion,
    result: Case360Result,
    resolver: DocumentContentResolver,
    pending_items: list[dict[str, str]],
) -> list[ProtocolFolderFile]:
    files: list[ProtocolFolderFile] = []
    order = 1

    export_result = export_docx(artifact)
    petition_bytes = export_result.content
    petition_sha256 = hashlib.sha256(petition_bytes).hexdigest()
    files.append(
        ProtocolFolderFile(
            order=order,
            folder=PETITION_FOLDER,
            filename=export_result.filename,
            source_type="artifact",
            source_id=artifact.id,
            sha256=petition_sha256,
            size_bytes=len(petition_bytes),
            extension=_extension_for(export_result.filename),
            content=petition_bytes,
        )
    )
    order += 1

    proof_document_ids = _select_documents_for_proof_folder(result)
    documents_by_id = {document.id: document for document in result.documents}

    # 04_PROVAS primeiro (prioridade sobre o mapa por doc_type — um
    # documento vinculado a uma prova nunca é duplicado em outra subpasta).
    ordered_documents: list[tuple[Document, str]] = []
    for document_id in sorted(proof_document_ids, key=lambda i: str(i)):
        document = documents_by_id.get(document_id)
        if document is not None:
            ordered_documents.append((document, PROOF_FOLDER))

    for document in result.documents:
        if document.id in proof_document_ids:
            continue
        if document.status != DocumentStatus.completo:
            pending_items.append(
                _pending(
                    "documento_nao_validado",
                    f"Documento '{document.name}' não está com status completo — "
                    "não incluído na pasta.",
                )
            )
            continue
        folder = _DOCUMENT_TYPE_TO_FOLDER.get(document.doc_type)
        if folder is None:
            continue
        ordered_documents.append((document, folder))

    for document, folder in ordered_documents:
        content = await resolver.read(workspace_id=workspace_id, document=document)
        if content is None:
            pending_items.append(
                _pending(
                    "arquivo_indisponivel",
                    f"Arquivo de '{document.name}' não pôde ser lido do armazenamento.",
                )
            )
            continue
        if not content:
            pending_items.append(
                _pending("arquivo_vazio", f"Arquivo de '{document.name}' está vazio (0 bytes).")
            )
            continue
        sha256 = hashlib.sha256(content).hexdigest()
        filename = f"{_slugify(document.name)}"
        extension = _extension_for(document.name) or _extension_for(document.mime_type or "")
        if extension and not filename.endswith(f".{extension}"):
            filename = f"{filename}.{extension}"
        files.append(
            ProtocolFolderFile(
                order=order,
                folder=folder,
                filename=filename,
                source_type="document",
                source_id=document.id,
                sha256=sha256,
                size_bytes=len(content),
                extension=extension,
                content=content,
            )
        )
        order += 1

    return files


def _detect_hash_drift(
    *, previous_files: list[dict[str, Any]], new_files: list[ProtocolFolderFile]
) -> list[dict[str, str]]:
    """Compara o hash já persistido de uma montagem anterior (qualquer
    montagem, aprovada ou não — a garantia é sobre O ARQUIVO, não sobre o
    estado da pasta) contra o hash recém-calculado do MESMO documento.
    Nunca compara `00_MANIFESTO` (ele muda em toda montagem, por design)."""
    previous_by_key = {
        (row.get("sourceType"), row.get("sourceId")): row.get("sha256")
        for row in previous_files
        if row.get("folder") != MANIFEST_FOLDER
    }
    drifted: list[dict[str, str]] = []
    for new_file in new_files:
        key = (new_file.source_type, str(new_file.source_id))
        previous_sha256 = previous_by_key.get(key)
        if previous_sha256 is not None and previous_sha256 != new_file.sha256:
            drifted.append(
                _pending(
                    "hash_alterado",
                    f"O arquivo '{new_file.filename}' mudou desde a última montagem desta "
                    "pasta de protocolo (hash divergente) — verifique se a versão correta "
                    "está anexada antes de protocolar.",
                )
            )
    return drifted


async def build_protocol_folder(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    legal_demand_id: UUID,
    artifact_id: UUID,
    tribunal: str | None = None,
    signature_confirmed: bool = False,
    resolver: DocumentContentResolver | None = None,
) -> ProtocolFolderBuildResult:
    """(Re)monta a pasta de protocolo de UMA (demanda, versão de artefato) —
    upsert na mesma linha (`uq_protocol_folder_demand_artifact`) para que
    a detecção de hash alterado compare contra a montagem anterior. Nunca
    promove `protocolavel` sozinho fora das validações do §10; nunca
    protocola."""
    demand = await _require_demand(db, workspace_id, legal_demand_id)
    artifact = await _require_artifact(db, workspace_id, demand, artifact_id)

    enforcement = EntitlementEnforcementService(db)
    legal_suite_access = await enforcement.enforce_feature(
        workspace_id=workspace_id,
        entitlement=EntitlementKey.legal_suite,
    )
    existing = await db.scalar(
        select(ProtocolFolder).where(
            ProtocolFolder.workspace_id == workspace_id,
            ProtocolFolder.legal_demand_id == legal_demand_id,
            ProtocolFolder.artifact_version_id == artifact_id,
        )
    )
    if existing is not None and legal_suite_access is not None:
        # Rebuild não cria estoque, mas também não pode manter acesso após
        # revogação/remoção da capacidade específica de pastas de protocolo.
        # ``None`` é somente o bypass explícito do simulador local sem contrato;
        # ambientes contratados/AWS sempre resolvem a capacidade abaixo.
        resolved = await enforcement.resolver.resolve(
            workspace_id,
            EntitlementKey.protocol_folders,
        )
        if not resolved.enabled:
            raise EntitlementDisabledError(
                "Entitlement protocol_folders desabilitado para este workspace."
            )
    else:

        async def _load_current_quantity() -> int:
            return int(
                await db.scalar(
                    select(func.count(ProtocolFolder.id)).where(
                        ProtocolFolder.workspace_id == workspace_id
                    )
                )
                or 0
            )

        await enforcement.enforce_stock(
            workspace_id=workspace_id,
            entitlement=EntitlementKey.protocol_folders,
            load_current_quantity=_load_current_quantity,
        )

    try:
        case_360 = await Case360Service(db).get_case_360(workspace_id, legal_demand_id)
    except Case360NotFoundError as exc:
        raise ProtocolFolderNotFoundError("Demanda jurídica (Caso 360) não encontrada.") from exc

    try:
        checklist = await recompute_checklist(
            db, workspace_id=workspace_id, artifact_id=artifact.id
        )
    except LegalEditorArtifactNotFoundError as exc:
        raise ProtocolFolderNotFoundError(str(exc)) from exc

    pending_items: list[dict[str, str]] = []
    blocking = False

    # ── versão liberada (aprovação, Onda 6) ─────────────────────────────
    artifact_status = artifact.status
    if artifact_status != "approved":
        blocking = True
        pending_items.append(
            _pending(
                "artefato_nao_aprovado",
                f"A versão {artifact.version} do artefato está em status "
                f"'{artifact_status}', não 'approved' — protocolo exige versão aprovada.",
            )
        )

    # ── placeholder (checklist do editor + varredura global) ───────────
    has_global_placeholder = _has_global_placeholder_marker(artifact.content)
    if checklist.has_placeholder or has_global_placeholder:
        blocking = True
        pending_items.append(
            _pending(
                "placeholder",
                "O conteúdo do artefato tem placeholder/variável pendente ou marcador "
                "de preenchimento (regra global do escritório).",
            )
        )

    # ── citações verificadas (mesma regra de ApprovalService) ───────────
    verified_texts = set(
        await db.scalars(
            select(LegalCitationVerification.citation_text).where(
                LegalCitationVerification.workspace_id == workspace_id,
                LegalCitationVerification.artifact_version_id == artifact.id,
                LegalCitationVerification.status == CitationVerificationStatus.verificada.value,
            )
        )
    )
    unverified_citations = [
        candidate.text
        for candidate in extract_citations(artifact.content)
        if candidate.text not in verified_texts
    ]
    if unverified_citations:
        blocking = True
        pending_items.append(
            _pending(
                "citacao_nao_verificada",
                "Citação(ões) sem conferência na fonte: " + ", ".join(unverified_citations[:5]),
            )
        )

    # ── achado crítico de revisão adversarial aberto ────────────────────
    critical_findings = list(
        await db.scalars(
            select(LegalReviewFinding).where(
                LegalReviewFinding.workspace_id == workspace_id,
                LegalReviewFinding.artifact_version_id == artifact.id,
                LegalReviewFinding.severity == LegalReviewSeverity.critica.value,
            )
        )
    )
    if critical_findings:
        blocking = True
        pending_items.append(
            _pending(
                "achado_critico_aberto",
                f"{len(critical_findings)} achado(s) crítico(s) da revisão adversarial "
                "ainda aberto(s).",
            )
        )

    # ── assinatura (confirmação humana explícita, nunca inferida) ──────
    if not signature_confirmed:
        pending_items.append(
            _pending(
                "assinatura",
                "Assinatura ainda não confirmada por um humano responsável.",
            )
        )

    # ── procuração / custas (reusa o checklist do editor) ───────────────
    kind = artifact.kind
    procuracao_dispensada = kind == "power_of_attorney"
    if not procuracao_dispensada and not checklist.has_procuracao:
        pending_items.append(_pending("procuracao", "Procuração não encontrada/validada."))
    if kind == "petition" and checklist.has_custas_pagas is False:
        pending_items.append(
            _pending("custas", "Nenhum lançamento financeiro de custas pago foi encontrado.")
        )

    # ── responsável / tribunal ───────────────────────────────────────────
    if case_360.responsible_user is None:
        pending_items.append(_pending("responsavel", "Nenhum responsável vinculado ao caso."))
    resolved_tribunal = tribunal
    if not resolved_tribunal:
        first_process = case_360.processes[0] if case_360.processes else None
        resolved_tribunal = first_process.tribunal if first_process else None
    if not resolved_tribunal:
        pending_items.append(
            _pending("tribunal", "Tribunal de destino não informado nem localizado no processo.")
        )
        resolved_tribunal = ""

    # ── arquivos (extensão/tamanho/hash calculados na montagem) ─────────
    active_resolver = resolver or DefaultDocumentContentResolver(db)
    files = await _build_files(
        db=db,
        workspace_id=workspace_id,
        artifact=artifact,
        result=case_360,
        resolver=active_resolver,
        pending_items=pending_items,
    )

    for produced in files:
        if produced.extension not in _ALLOWED_EXTENSIONS:
            pending_items.append(
                _pending(
                    "extensao_invalida",
                    f"Arquivo '{produced.filename}' tem extensão não reconhecida "
                    f"('{produced.extension}').",
                )
            )

    present_folders = {produced.folder for produced in files}
    indispensable = _INDISPENSABLE_FOLDERS_BY_KIND.get(kind, frozenset({PETITION_FOLDER}))
    for required_folder in indispensable:
        if required_folder not in present_folders:
            pending_items.append(
                _pending(
                    "documento_indispensavel_ausente",
                    f"Subpasta obrigatória '{required_folder}' está sem arquivo.",
                )
            )

    # ── hash de arquivo alterado desde uma montagem anterior ────────────
    previous_files: list[dict[str, Any]] = list(existing.files) if existing is not None else []
    drift_items = _detect_hash_drift(previous_files=previous_files, new_files=files)
    if drift_items:
        blocking = True
        pending_items.extend(drift_items)

    total_size_bytes = sum(produced.size_bytes for produced in files)
    generated_at = datetime.now(UTC)

    if blocking:
        state = ProtocolFolderState.bloqueado
    elif pending_items:
        state = ProtocolFolderState.pendente
    else:
        state = ProtocolFolderState.protocolavel

    manifest = {
        "processo": {
            "numeroCnj": case_360.processes[0].numero_cnj if case_360.processes else None,
            "presente": bool(case_360.processes),
        },
        "tribunal": resolved_tribunal,
        "tipoAto": kind,
        "responsavel": (
            {"userId": str(case_360.responsible_user.id), "nome": case_360.responsible_user.name}
            if case_360.responsible_user
            else None
        ),
        "versaoLiberada": {
            "artifactVersionId": str(artifact.id),
            "version": artifact.version,
            "status": artifact_status,
        },
        "arquivos": [produced.as_manifest_dict() for produced in files],
        "tamanhoTotalBytes": total_size_bytes,
        "assinaturaConfirmada": signature_confirmed,
        "procuracao": {
            "dispensada": procuracao_dispensada,
            "presente": checklist.has_procuracao,
        },
        "custas": {
            "exigida": kind == "petition",
            "pagaNoFinanceiro": checklist.has_custas_pagas,
        },
        "citacoes": {
            "total": len(extract_citations(artifact.content)),
            "naoVerificadas": unverified_citations,
        },
        "achadosCriticosAbertos": len(critical_findings),
        "placeholders": {
            "presente": bool(checklist.has_placeholder or has_global_placeholder),
        },
        "pendencias": pending_items,
        "geradoEm": generated_at.isoformat(),
        "estado": state.value,
    }

    if existing is not None:
        protocol_folder = existing
        protocol_folder.tribunal = resolved_tribunal
        protocol_folder.act_type = kind
        protocol_folder.state = state.value
        protocol_folder.manifest = manifest
        protocol_folder.files = [produced.as_manifest_dict() for produced in files]
        protocol_folder.signature_confirmed = signature_confirmed
        protocol_folder.built_by_user_id = user_id
        protocol_folder.built_at = generated_at
    else:
        protocol_folder = ProtocolFolder(
            workspace_id=workspace_id,
            legal_demand_id=legal_demand_id,
            matter_id=artifact.matter_id,
            artifact_version_id=artifact_id,
            tribunal=resolved_tribunal,
            act_type=kind,
            state=state.value,
            manifest=manifest,
            files=[produced.as_manifest_dict() for produced in files],
            signature_confirmed=signature_confirmed,
            built_by_user_id=user_id,
            built_at=generated_at,
        )
        db.add(protocol_folder)

    await db.flush()
    return ProtocolFolderBuildResult(folder=protocol_folder, files=files)


async def get_protocol_folder(
    db: AsyncSession, workspace_id: UUID, protocol_folder_id: UUID
) -> ProtocolFolder:
    folder = await db.scalar(
        select(ProtocolFolder).where(
            ProtocolFolder.workspace_id == workspace_id,
            ProtocolFolder.id == protocol_folder_id,
        )
    )
    if folder is None:
        raise ProtocolFolderNotFoundError()
    return folder


def build_zip(*, manifest: dict[str, Any], files: list[ProtocolFolderFile]) -> bytes:
    """Serializa a pasta numerada + o manifesto num único `.zip` — a MESMA
    lista de arquivos já validada por `build_protocol_folder`, nunca uma
    segunda passada de coleta. O manifesto vai como `manifesto.json`
    dentro de `00_MANIFESTO/` (estruturado — ver limitação sobre PDF no
    topo do módulo)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
        archive.writestr(f"{MANIFEST_FOLDER}/manifesto.json", manifest_bytes)
        for produced in sorted(files, key=lambda item: item.order):
            entry_name = f"{produced.folder}/{produced.order:02d}_{produced.filename}"
            archive.writestr(entry_name, produced.content)
    return buffer.getvalue()


_SUPPORTED_SYNC_PROVIDERS = frozenset({"google_drive", "onedrive", "sharepoint"})


async def sync_protocol_folder_to_external_storage(
    *, protocol_folder: ProtocolFolder, provider: str
) -> None:
    """Ponto único de extensão para "opção de sync p/ Drive/OneDrive/
    SharePoint" (§10 item 3) — HOJE sempre gated (ver
    `ProtocolFolderSyncNotConfiguredError`): nenhuma credencial dessas
    integrações está plugada neste código-base. Implementações futuras
    entram aqui, sem redesenho do restante do módulo (mesmo padrão de
    `CitationSourceProvider` em citation_verification_service.py)."""
    if provider not in _SUPPORTED_SYNC_PROVIDERS:
        raise ValueError(f"Provedor de sincronização desconhecido: '{provider}'.")
    raise ProtocolFolderSyncNotConfiguredError(provider)


async def register_protocol_receipt(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    protocol_folder_id: UUID,
    reference: str,
    note: str | None = None,
    protocoled_at: datetime | None = None,
) -> ProtocolFolder:
    """Registra o recibo de um protocolo que JÁ ACONTECEU fora da
    plataforma — SEMPRE posterior, SEMPRE colado por um humano
    (`user_id` obrigatório). Não valida `state == protocolavel` porque
    este registro é sobre um FATO já ocorrido no mundo real, não sobre a
    plataforma autorizando algo; a plataforma nunca impede o humano de
    registrar o que de fato aconteceu."""
    await EntitlementEnforcementService(db).enforce_feature(
        workspace_id=workspace_id,
        entitlement=EntitlementKey.legal_suite,
    )
    if not reference or not reference.strip():
        raise ProtocolFolderArtifactStateError(
            "Referência do recibo (número/protocolo) é obrigatória."
        )
    folder = await get_protocol_folder(db, workspace_id, protocol_folder_id)
    folder.receipt_registered_at = protocoled_at or datetime.now(UTC)
    folder.receipt_registered_by_user_id = user_id
    folder.receipt_reference = reference.strip()
    folder.receipt_note = note
    await db.flush()
    return folder
