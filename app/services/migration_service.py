"""
Service: Importador Universal de Migração (Onda 3, Plano Mestre §12).

Pipeline completo, cada passo idempotente e retomável:

  receber → staging → validado → prévia → importando → reconciliando →
  concluido (com semáforo vermelho/amarelo/verde) | revertido | erro

`receive_and_stage` faz receber+detectar+staging+validação+normalização+
dedupe/conflito num único passo síncrono (o arquivo bruto não é persistido
em disco — mesmo racional de `finance_harmonization_service.create_run`,
F14: parseado em memória a partir do hash já calculado, nunca reaberto de
um storage próprio; ver docstring de `app.models.migration_job` para o
racional completo de "arquivo nunca alterado"). Os passos seguintes
(`build_preview`, `import_batch`, `reconcile`, `decide_cutover`,
`rollback`, `reprocess_record`) operam só sobre o que já está em staging e
são, cada um, seguros de repetir.

Domínios com importador real nesta onda: clientes → `Client`, casos →
`Matter`, processos → anexa `Matter.case_number`, recebimentos/despesas →
`FinanceEntry`. Os outros 8 domínios (contatos, partes, tarefas, prazos,
documentos, contratos, honorarios, tags) ficam staged com `target_id` nulo
e uma divergência não-crítica `importador_pendente` — nunca fingem ter
sido importados (mesma disciplina honesta do `parser_warning` de F14).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.advisory import acquire_transaction_advisory_lock
from app.models.client import Client
from app.models.finance import FinanceEntry, FinanceEntryType
from app.models.matter import Matter, PracticeArea
from app.models.migration_job import (
    MIGRATION_DOMAINS_WITH_IMPORTER,
    MIGRATION_ROLLBACK_ELIGIBLE_STATUSES,
    MigrationConflict,
    MigrationDivergence,
    MigrationDivergenceSeverity,
    MigrationDomain,
    MigrationJob,
    MigrationJobStatus,
    MigrationRecordStatus,
    MigrationSourceSystem,
    MigrationStagingRecord,
)
from app.services.cnj_numero import InvalidCnjNumberError, normalizar_numero_cnj
from app.services.migration_parsers import (
    ParsedRow,
    UnsupportedMigrationFormatError,
    parse_content,
    sha256_hex,
)

# Ordem de importação: casos dependem de clientes; processos/recebimentos/
# despesas podem depender de casos. Domínios fora desta lista não têm
# importador nesta onda.
_DOMAIN_IMPORT_ORDER: tuple[MigrationDomain, ...] = (
    MigrationDomain.clientes,
    MigrationDomain.casos,
    MigrationDomain.processos,
    MigrationDomain.recebimentos,
    MigrationDomain.despesas,
)

_BALANCE_TOLERANCE = Decimal("0.01")


# ── Erros de domínio ─────────────────────────────────────────────────────


class MigrationJobNotFoundError(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job de migração não encontrado."
        )


class MigrationStagingRecordNotFoundError(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND, detail="Registro de staging não encontrado."
        )


class MigrationDivergenceNotFoundError(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND, detail="Divergência não encontrada."
        )


class MigrationInvalidTransitionError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


# ── Normalização (helpers puros, sem I/O) ───────────────────────────────


def _only_digits(value: Any) -> Optional[str]:
    if value is None:
        return None
    digits = re.sub(r"[^0-9]", "", str(value))
    return digits or None


def _clean_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    cleaned = str(value).strip().replace("R$", "").strip()
    if not cleaned:
        return None
    if "," in cleaned and cleaned.rfind(",") > cleaned.rfind("."):
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_date_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


@dataclass
class _NormalizeOutcome:
    normalized: dict[str, Any]
    dedupe_key: str
    weak_identity: bool
    weak_signature: Optional[str]
    error: Optional[str]
    rejected: bool = False


def _map_row(raw: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    if not mapping:
        return dict(raw)
    mapped = dict(raw)
    for target_field, source_column in mapping.items():
        if source_column in raw:
            mapped[target_field] = raw[source_column]
    return mapped


def _normalize_clientes(mapped: dict[str, Any]) -> _NormalizeOutcome:
    nome = _clean_str(mapped.get("nome"))
    if not nome:
        return _NormalizeOutcome({}, "", False, None, "Campo obrigatório ausente: nome.")
    documento = _only_digits(mapped.get("documento"))
    referencia_externa = _clean_str(mapped.get("referencia_externa"))
    normalized = {
        "nome": nome,
        "documento": documento,
        "email": _clean_str(mapped.get("email")),
        "telefone": _clean_str(mapped.get("telefone")),
        "referencia_externa": referencia_externa,
    }
    if documento:
        return _NormalizeOutcome(normalized, f"doc:{documento}", False, None, None)
    weak_signature = re.sub(r"\s+", " ", nome.strip().lower())
    dedupe_key = f"nome:{_stable_hash(mapped)}"
    return _NormalizeOutcome(normalized, dedupe_key, True, weak_signature, None)


def _map_practice_area(raw_area: Optional[str]) -> PracticeArea:
    if raw_area:
        candidate = raw_area.strip().lower().replace(" ", "_")
        for area in PracticeArea:
            if area.value == candidate:
                return area
    return PracticeArea.civel


def _normalize_casos(mapped: dict[str, Any]) -> _NormalizeOutcome:
    cliente_ref = _clean_str(mapped.get("cliente_ref"))
    if not cliente_ref:
        return _NormalizeOutcome({}, "", False, None, "Campo obrigatório ausente: cliente_ref.")
    numero_cnj = None
    cnj_raw = mapped.get("numero_cnj")
    if cnj_raw:
        try:
            numero_cnj = normalizar_numero_cnj(str(cnj_raw))
        except InvalidCnjNumberError:
            numero_cnj = None
    referencia_externa = _clean_str(mapped.get("referencia_externa"))
    normalized = {
        "cliente_ref": cliente_ref,
        "area": _map_practice_area(_clean_str(mapped.get("area"))).value,
        "demanda": _clean_str(mapped.get("demanda")) or "Caso migrado",
        "numero_cnj": numero_cnj,
        "referencia_externa": referencia_externa,
    }
    if referencia_externa:
        return _NormalizeOutcome(normalized, f"ref:{referencia_externa}", False, None, None)
    if numero_cnj:
        return _NormalizeOutcome(normalized, f"cnj:{numero_cnj}", False, None, None)
    weak_signature = f"{cliente_ref.lower()}|{normalized['demanda'].lower()}"
    return _NormalizeOutcome(normalized, f"caso:{_stable_hash(mapped)}", True, weak_signature, None)


def _normalize_processos(mapped: dict[str, Any]) -> _NormalizeOutcome:
    cnj_raw = mapped.get("numero_cnj")
    if not cnj_raw:
        return _NormalizeOutcome({}, "", False, None, "Campo obrigatório ausente: numero_cnj.")
    try:
        numero_cnj = normalizar_numero_cnj(str(cnj_raw))
    except InvalidCnjNumberError as error:
        normalized = {"numero_cnj_bruto": str(cnj_raw)}
        return _NormalizeOutcome(
            normalized,
            f"cnjbruto:{_stable_hash(str(cnj_raw))}",
            False,
            None,
            f"CNJ inválido: {error}",
            rejected=True,
        )
    normalized = {
        "numero_cnj": numero_cnj,
        "caso_ref": _clean_str(mapped.get("caso_ref")),
        "cliente_ref": _clean_str(mapped.get("cliente_ref")),
    }
    return _NormalizeOutcome(normalized, f"cnj:{numero_cnj}", False, None, None)


def _normalize_financeiro(
    mapped: dict[str, Any], entry_type: FinanceEntryType
) -> _NormalizeOutcome:
    valor = _parse_decimal(mapped.get("valor"))
    if valor is None:
        return _NormalizeOutcome(
            {}, "", False, None, "Campo obrigatório ausente ou inválido: valor."
        )
    referencia_externa = _clean_str(mapped.get("referencia_externa"))
    normalized = {
        "descricao": _clean_str(mapped.get("descricao")) or "Lançamento migrado",
        "valor": str(valor),
        "tipo": entry_type.value,
        "vencimento": _parse_date_iso(mapped.get("vencimento")),
        "referencia_externa": referencia_externa,
        "caso_ref": _clean_str(mapped.get("caso_ref")),
    }
    if referencia_externa:
        return _NormalizeOutcome(normalized, f"ref:{referencia_externa}", False, None, None)
    weak_signature = None
    dedupe_key = f"fin:{_stable_hash(mapped)}"
    return _NormalizeOutcome(normalized, dedupe_key, True, weak_signature, None)


def _normalize_prazos(mapped: dict[str, Any]) -> _NormalizeOutcome:
    referencia_externa = _clean_str(mapped.get("referencia_externa"))
    normalized = {
        "descricao": _clean_str(mapped.get("descricao")) or "Prazo migrado",
        "vencimento": _parse_date_iso(mapped.get("vencimento")),
        "caso_ref": _clean_str(mapped.get("caso_ref")),
        "referencia_externa": referencia_externa,
    }
    if referencia_externa:
        return _NormalizeOutcome(normalized, f"ref:{referencia_externa}", False, None, None)
    return _NormalizeOutcome(normalized, f"prazo:{_stable_hash(mapped)}", True, None, None)


def _normalize_generic(mapped: dict[str, Any]) -> _NormalizeOutcome:
    referencia_externa = _clean_str(mapped.get("referencia_externa"))
    normalized = dict(mapped)
    if referencia_externa:
        return _NormalizeOutcome(normalized, f"ref:{referencia_externa}", False, None, None)
    return _NormalizeOutcome(normalized, f"hash:{_stable_hash(mapped)}", True, None, None)


def _normalize_row(domain: MigrationDomain, mapped: dict[str, Any]) -> _NormalizeOutcome:
    if domain == MigrationDomain.clientes:
        return _normalize_clientes(mapped)
    if domain == MigrationDomain.casos:
        return _normalize_casos(mapped)
    if domain == MigrationDomain.processos:
        return _normalize_processos(mapped)
    if domain == MigrationDomain.recebimentos:
        return _normalize_financeiro(mapped, FinanceEntryType.receivable)
    if domain == MigrationDomain.despesas:
        return _normalize_financeiro(mapped, FinanceEntryType.expense)
    if domain == MigrationDomain.prazos:
        return _normalize_prazos(mapped)
    return _normalize_generic(mapped)


class MigrationService:
    def __init__(self, db: AsyncSession, workspace_id: UUID) -> None:
        if workspace_id is None:
            raise ValueError("workspace_id is required")
        self.db = db
        self.workspace_id = workspace_id

    # ── Leitura escopada por workspace ──────────────────────────────────

    async def get_job(self, job_id: UUID) -> MigrationJob:
        job = await self.db.scalar(
            select(MigrationJob).where(
                MigrationJob.id == job_id, MigrationJob.workspace_id == self.workspace_id
            )
        )
        if job is None:
            raise MigrationJobNotFoundError()
        return job

    async def list_jobs(self, *, skip: int = 0, limit: int = 50) -> list[MigrationJob]:
        result = await self.db.scalars(
            select(MigrationJob)
            .where(MigrationJob.workspace_id == self.workspace_id)
            .order_by(MigrationJob.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.all())

    async def list_staging_records(
        self,
        job_id: UUID,
        *,
        domain: Optional[MigrationDomain] = None,
        conflict: Optional[MigrationConflict] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[MigrationStagingRecord]:
        await self.get_job(job_id)
        query = select(MigrationStagingRecord).where(
            MigrationStagingRecord.workspace_id == self.workspace_id,
            MigrationStagingRecord.job_id == job_id,
        )
        if domain is not None:
            query = query.where(MigrationStagingRecord.domain == domain)
        if conflict is not None:
            query = query.where(MigrationStagingRecord.conflict == conflict)
        result = await self.db.scalars(
            query.order_by(MigrationStagingRecord.created_at).offset(skip).limit(limit)
        )
        return list(result.all())

    async def list_divergences(
        self, job_id: UUID, *, resolved: Optional[bool] = None
    ) -> list[MigrationDivergence]:
        await self.get_job(job_id)
        query = select(MigrationDivergence).where(
            MigrationDivergence.workspace_id == self.workspace_id,
            MigrationDivergence.job_id == job_id,
        )
        if resolved is not None:
            query = query.where(MigrationDivergence.resolvido == resolved)
        result = await self.db.scalars(query.order_by(MigrationDivergence.created_at))
        return list(result.all())

    # ── 1) Receber + detectar + staging + validação + normalização ─────

    async def receive_and_stage(
        self,
        *,
        filename: str,
        content_type: Optional[str],
        content: bytes,
        source_system: MigrationSourceSystem,
        created_by: Optional[UUID] = None,
        default_domain: Optional[str] = None,
        field_mapping: Optional[dict[str, dict[str, str]]] = None,
    ) -> MigrationJob:
        content_hash = sha256_hex(content)

        # Idempotência de recebimento: reenviar os MESMOS bytes no mesmo
        # workspace devolve o job já existente (mesmo desenho de
        # ExceptionCenterService.open_exception — replay não duplica).
        await acquire_transaction_advisory_lock(
            self.db, namespace="migration-receive", value=f"{self.workspace_id}:{content_hash}"
        )
        existing = await self.db.scalar(
            select(MigrationJob).where(
                MigrationJob.workspace_id == self.workspace_id,
                MigrationJob.status != MigrationJobStatus.revertido,
            ).where(
                MigrationJob.source_manifest["sha256"].as_string() == content_hash
            )
        )
        if existing is not None:
            return existing

        job = MigrationJob(
            workspace_id=self.workspace_id,
            source_system=source_system,
            status=MigrationJobStatus.recebido,
            source_manifest={
                "filename": filename,
                "contentType": content_type,
                "byteSize": len(content),
                "sha256": content_hash,
                "uploadedAt": datetime.now(UTC).isoformat(),
            },
            field_mapping=field_mapping or {},
            started_at=datetime.now(UTC),
            created_by=created_by,
        )
        self.db.add(job)
        await self.db.flush()

        try:
            parsed = parse_content(
                filename=filename,
                content_type=content_type,
                content=content,
                default_domain=default_domain,
            )
        except UnsupportedMigrationFormatError as error:
            job.status = MigrationJobStatus.erro
            job.error_message = str(error)
            await self.db.flush()
            return job

        job.source_manifest = {**job.source_manifest, "formatDetected": parsed.format_detected}

        if parsed.warning and not parsed.rows:
            job.status = MigrationJobStatus.erro
            job.error_message = parsed.warning
            await self.db.flush()
            return job

        job.status = MigrationJobStatus.staging
        await self.db.flush()

        row_counts, rejected_rows, weak_signatures = await self._stage_rows(job, parsed.rows)
        job.source_manifest = {
            **job.source_manifest,
            "rowCounts": row_counts,
            "rejectedRows": rejected_rows,
        }
        job.status = MigrationJobStatus.validado
        await self.db.flush()
        await self._detect_conflicts(job, weak_signatures)
        return job

    async def _stage_rows(
        self, job: MigrationJob, rows: list[ParsedRow]
    ) -> tuple[dict[str, int], int, dict[UUID, str]]:
        mapping_config = job.field_mapping or {}
        row_counts: dict[str, int] = {}
        rejected_rows = 0
        seen_keys: set[tuple[str, str]] = set()
        weak_signatures: dict[UUID, str] = {}

        for row in rows:
            domain_raw = (row.domain_hint or "").strip().lower()
            try:
                domain = MigrationDomain(domain_raw)
            except ValueError:
                rejected_rows += 1
                self.db.add(
                    MigrationDivergence(
                        workspace_id=self.workspace_id,
                        job_id=job.id,
                        domain=None,
                        tipo="dominio_desconhecido",
                        severity=MigrationDivergenceSeverity.atencao,
                        detalhe=(
                            f"Linha {row.row_number}: domínio '{domain_raw or '(vazio)'}' não "
                            "reconhecido — linha ignorada."
                        ),
                        context={"row_number": row.row_number, "raw": row.data},
                    )
                )
                continue

            mapped = _map_row(row.data, mapping_config.get(domain.value, {}))
            outcome = _normalize_row(domain, mapped)

            key = (domain.value, outcome.dedupe_key)
            if outcome.dedupe_key and key in seen_keys:
                self.db.add(
                    MigrationDivergence(
                        workspace_id=self.workspace_id,
                        job_id=job.id,
                        domain=domain,
                        tipo="linha_duplicada_no_arquivo",
                        severity=MigrationDivergenceSeverity.atencao,
                        detalhe=f"Linha {row.row_number}: chave já vista neste arquivo — ignorada.",
                        context={"row_number": row.row_number, "raw": row.data},
                    )
                )
                continue
            seen_keys.add(key)

            if outcome.error:
                record_status = (
                    MigrationRecordStatus.rejeitado
                    if outcome.rejected
                    else MigrationRecordStatus.invalido
                )
                severity = MigrationDivergenceSeverity.atencao
                self.db.add(
                    MigrationDivergence(
                        workspace_id=self.workspace_id,
                        job_id=job.id,
                        domain=domain,
                        tipo="cnj_invalido" if outcome.rejected else "validacao_estrutural",
                        severity=severity,
                        detalhe=f"Linha {row.row_number}: {outcome.error}",
                        context={"row_number": row.row_number, "raw": row.data},
                    )
                )
            else:
                record_status = MigrationRecordStatus.pendente

            record_id = uuid4()
            record = MigrationStagingRecord(
                id=record_id,
                workspace_id=self.workspace_id,
                job_id=job.id,
                domain=domain,
                raw=row.data,
                normalized=outcome.normalized,
                dedupe_key=outcome.dedupe_key or f"row:{row.row_number}:{_stable_hash(row.data)}",
                weak_identity=outcome.weak_identity,
                conflict=MigrationConflict.none,
                record_status=record_status,
                validation_error=outcome.error,
            )
            self.db.add(record)
            if outcome.weak_signature:
                weak_signatures[record_id] = outcome.weak_signature
            row_counts[domain.value] = row_counts.get(domain.value, 0) + 1

        await self.db.flush()
        return row_counts, rejected_rows, weak_signatures

    async def _detect_conflicts(
        self, job: MigrationJob, weak_signatures: dict[UUID, str]
    ) -> None:
        records = list(
            (
                await self.db.scalars(
                    select(MigrationStagingRecord).where(
                        MigrationStagingRecord.job_id == job.id,
                        MigrationStagingRecord.record_status == MigrationRecordStatus.pendente,
                    )
                )
            ).all()
        )

        # Ambiguidade (homônimos): agrupa por (domínio, assinatura fraca).
        groups: dict[tuple[str, str], list[MigrationStagingRecord]] = {}
        for record in records:
            signature = weak_signatures.get(record.id)
            if record.weak_identity and signature:
                groups.setdefault((record.domain.value, signature), []).append(record)

        ambiguous_ids: set[UUID] = set()
        for members in groups.values():
            if len(members) > 1:
                for member in members:
                    member.conflict = MigrationConflict.ambiguous
                    ambiguous_ids.add(member.id)
                self.db.add(
                    MigrationDivergence(
                        workspace_id=self.workspace_id,
                        job_id=job.id,
                        domain=members[0].domain,
                        tipo="identidade_ambigua",
                        severity=MigrationDivergenceSeverity.atencao,
                        detalhe=(
                            f"{len(members)} registros com a mesma identidade fraca — "
                            "revisão humana necessária antes de mesclar."
                        ),
                        context={"staging_record_ids": [str(m.id) for m in members]},
                    )
                )

        # Duplicidade contra produção (identidade forte).
        for record in records:
            if record.id in ambiguous_ids or record.weak_identity:
                continue
            existing_target = await self._find_existing_target(record)
            if existing_target is not None:
                record.conflict = MigrationConflict.duplicate

        await self.db.flush()

    async def _find_existing_target(self, record: MigrationStagingRecord) -> Optional[UUID]:
        if record.domain == MigrationDomain.clientes:
            documento = record.normalized.get("documento")
            if not documento:
                return None
            existing = await self.db.scalar(
                select(Client.id).where(
                    Client.workspace_id == self.workspace_id,
                    Client.cpf == documento,
                    Client.deleted_at.is_(None),
                )
            )
            return existing
        if record.domain in (MigrationDomain.casos, MigrationDomain.processos):
            case_number = record.normalized.get("numero_cnj")
            if not case_number:
                return None
            existing = await self.db.scalar(
                select(Matter.id).where(
                    Matter.workspace_id == self.workspace_id,
                    Matter.case_number == case_number,
                    Matter.deleted_at.is_(None),
                )
            )
            return existing
        if record.domain in (MigrationDomain.recebimentos, MigrationDomain.despesas):
            external_ref = record.normalized.get("referencia_externa")
            if not external_ref:
                return None
            existing = await self.db.scalar(
                select(FinanceEntry.id).where(
                    FinanceEntry.workspace_id == self.workspace_id,
                    FinanceEntry.external_ref == external_ref,
                    FinanceEntry.deleted_at.is_(None),
                )
            )
            return existing
        return None

    # ── 2) Prévia (contagens por domínio) ───────────────────────────────

    async def build_preview(self, job_id: UUID) -> MigrationJob:
        job = await self.get_job(job_id)
        if job.status not in (MigrationJobStatus.validado, MigrationJobStatus.previa):
            raise MigrationInvalidTransitionError(
                f"Prévia exige job validado (status atual: {job.status.value})."
            )

        summary: dict[str, dict[str, int]] = {}
        for domain in MigrationDomain:
            counts = await self.db.execute(
                select(
                    sa_func.count().filter(MigrationStagingRecord.record_status.in_(
                        [MigrationRecordStatus.pendente]
                    )),
                    sa_func.count().filter(
                        MigrationStagingRecord.record_status == MigrationRecordStatus.invalido
                    ),
                    sa_func.count().filter(
                        MigrationStagingRecord.conflict == MigrationConflict.duplicate
                    ),
                    sa_func.count().filter(
                        MigrationStagingRecord.conflict == MigrationConflict.ambiguous
                    ),
                    sa_func.count(),
                ).where(
                    MigrationStagingRecord.job_id == job.id,
                    MigrationStagingRecord.domain == domain,
                )
            )
            valid_count, invalid_count, duplicate_count, ambiguous_count, total = counts.one()
            if total == 0:
                continue
            summary[domain.value] = {
                "staged": total,
                "validos": valid_count,
                "invalidos": invalid_count,
                "duplicados": duplicate_count,
                "ambiguos": ambiguous_count,
            }

        job.preview_summary = summary
        job.status = MigrationJobStatus.previa
        await self.db.flush()
        return job

    # ── 3) Importação por lotes idempotente ─────────────────────────────

    async def import_batch(self, job_id: UUID, *, batch_size: int = 50) -> dict[str, Any]:
        job = await self.get_job(job_id)
        if job.status not in (MigrationJobStatus.previa, MigrationJobStatus.importando):
            raise MigrationInvalidTransitionError(
                f"Importação exige prévia calculada (status atual: {job.status.value})."
            )
        job.status = MigrationJobStatus.importando
        await self.db.flush()

        processed = 0
        for domain in _DOMAIN_IMPORT_ORDER:
            if processed >= batch_size:
                break
            remaining_budget = batch_size - processed
            records = list(
                (
                    await self.db.scalars(
                        select(MigrationStagingRecord)
                        .where(
                            MigrationStagingRecord.job_id == job.id,
                            MigrationStagingRecord.domain == domain,
                            MigrationStagingRecord.record_status == MigrationRecordStatus.pendente,
                        )
                        .order_by(MigrationStagingRecord.created_at)
                        .limit(remaining_budget)
                    )
                ).all()
            )
            for record in records:
                await self._import_record(job, record)
                processed += 1

        await self.db.flush()
        remaining = await self.db.scalar(
            select(sa_func.count()).where(
                MigrationStagingRecord.job_id == job.id,
                MigrationStagingRecord.domain.in_(list(MIGRATION_DOMAINS_WITH_IMPORTER)),
                MigrationStagingRecord.record_status == MigrationRecordStatus.pendente,
            )
        )
        if remaining == 0:
            job.status = MigrationJobStatus.reconciliando
        await self.db.flush()
        return {"processed": processed, "remaining": remaining, "status": job.status.value}

    async def _import_record(self, job: MigrationJob, record: MigrationStagingRecord) -> None:
        try:
            if record.domain == MigrationDomain.clientes:
                await self._import_cliente(job, record)
            elif record.domain == MigrationDomain.casos:
                await self._import_caso(job, record)
            elif record.domain == MigrationDomain.processos:
                await self._import_processo(job, record)
            elif record.domain in (MigrationDomain.recebimentos, MigrationDomain.despesas):
                await self._import_financeiro(job, record)
        except Exception as error:  # noqa: BLE001 — um registro ruim não derruba o lote
            record.record_status = MigrationRecordStatus.invalido
            record.validation_error = f"Falha ao importar: {error}"

    async def _import_cliente(self, job: MigrationJob, record: MigrationStagingRecord) -> None:
        if record.conflict == MigrationConflict.duplicate:
            existing_id = await self._find_existing_target(record)
            if existing_id is not None:
                record.target_id = existing_id
                record.target_table = "clients"
                record.record_status = MigrationRecordStatus.mesclado
                record.imported_at = datetime.now(UTC)
                return
        client = Client(
            workspace_id=self.workspace_id,
            name=record.normalized["nome"],
            cpf=record.normalized.get("documento"),
            email=record.normalized.get("email"),
            phone=record.normalized.get("telefone"),
        )
        self.db.add(client)
        await self.db.flush()
        record.target_id = client.id
        record.target_table = "clients"
        record.record_status = MigrationRecordStatus.importado
        record.imported_at = datetime.now(UTC)

    async def _resolve_client_ref(self, job: MigrationJob, cliente_ref: str) -> Optional[UUID]:
        staged = await self.db.scalar(
            select(MigrationStagingRecord).where(
                MigrationStagingRecord.job_id == job.id,
                MigrationStagingRecord.domain == MigrationDomain.clientes,
                MigrationStagingRecord.target_id.is_not(None),
            ).where(
                (MigrationStagingRecord.normalized["referencia_externa"].as_string() == cliente_ref)
                | (MigrationStagingRecord.normalized["documento"].as_string() == cliente_ref)
            )
        )
        if staged is not None:
            return staged.target_id
        digits = _only_digits(cliente_ref)
        if digits:
            existing = await self.db.scalar(
                select(Client.id).where(
                    Client.workspace_id == self.workspace_id,
                    Client.cpf == digits,
                    Client.deleted_at.is_(None),
                )
            )
            if existing is not None:
                return existing
        return None

    async def _resolve_caso_ref(self, job: MigrationJob, caso_ref: Optional[str]) -> Optional[UUID]:
        if not caso_ref:
            return None
        staged = await self.db.scalar(
            select(MigrationStagingRecord).where(
                MigrationStagingRecord.job_id == job.id,
                MigrationStagingRecord.domain == MigrationDomain.casos,
                MigrationStagingRecord.target_id.is_not(None),
            ).where(
                (MigrationStagingRecord.normalized["referencia_externa"].as_string() == caso_ref)
                | (MigrationStagingRecord.normalized["numero_cnj"].as_string() == caso_ref)
            )
        )
        if staged is not None:
            return staged.target_id
        existing = await self.db.scalar(
            select(Matter.id).where(
                Matter.workspace_id == self.workspace_id,
                Matter.case_number == caso_ref,
                Matter.deleted_at.is_(None),
            )
        )
        return existing

    async def _import_caso(self, job: MigrationJob, record: MigrationStagingRecord) -> None:
        client_id = await self._resolve_client_ref(job, record.normalized["cliente_ref"])
        if client_id is None:
            record.record_status = MigrationRecordStatus.rejeitado
            record.validation_error = "cliente_ref não resolvido a nenhum cliente."
            self.db.add(
                MigrationDivergence(
                    workspace_id=self.workspace_id,
                    job_id=job.id,
                    domain=MigrationDomain.casos,
                    tipo="caso_sem_cliente_vinculado",
                    severity=MigrationDivergenceSeverity.atencao,
                    detalhe=(
                        "Caso não vinculado a nenhum cliente (cliente_ref="
                        f"{record.normalized['cliente_ref']})."
                    ),
                    context={"staging_record_id": str(record.id)},
                )
            )
            return

        numero_cnj = record.normalized.get("numero_cnj")
        if numero_cnj and record.conflict == MigrationConflict.duplicate:
            existing_id = await self._find_existing_target(record)
            if existing_id is not None:
                record.target_id = existing_id
                record.target_table = "matters"
                record.record_status = MigrationRecordStatus.mesclado
                record.imported_at = datetime.now(UTC)
                return

        matter = Matter(
            workspace_id=self.workspace_id,
            client_id=client_id,
            practice_area=PracticeArea(record.normalized["area"]),
            demand_type=record.normalized["demanda"],
            case_number=numero_cnj,
        )
        self.db.add(matter)
        await self.db.flush()
        record.target_id = matter.id
        record.target_table = "matters"
        record.record_status = MigrationRecordStatus.importado
        record.imported_at = datetime.now(UTC)

    async def _import_processo(self, job: MigrationJob, record: MigrationStagingRecord) -> None:
        numero_cnj = record.normalized.get("numero_cnj")
        if record.conflict == MigrationConflict.duplicate:
            existing_id = await self._find_existing_target(record)
            if existing_id is not None:
                record.target_id = existing_id
                record.target_table = "matters"
                record.record_status = MigrationRecordStatus.mesclado
                record.imported_at = datetime.now(UTC)
                return

        matter_id = await self._resolve_caso_ref(job, record.normalized.get("caso_ref"))
        if matter_id is None and record.normalized.get("cliente_ref"):
            # sem caso explícito: tenta achar um caso do mesmo cliente já
            # importado neste job (link fraco, só como último recurso).
            client_id = await self._resolve_client_ref(job, record.normalized["cliente_ref"])
            if client_id is not None:
                matter_id = await self.db.scalar(
                    select(Matter.id).where(
                        Matter.workspace_id == self.workspace_id,
                        Matter.client_id == client_id,
                        Matter.case_number.is_(None),
                    ).limit(1)
                )

        if matter_id is None:
            record.record_status = MigrationRecordStatus.rejeitado
            record.validation_error = "Processo sem vínculo a caso/cliente."
            self.db.add(
                MigrationDivergence(
                    workspace_id=self.workspace_id,
                    job_id=job.id,
                    domain=MigrationDomain.processos,
                    tipo="processo_sem_vinculo",
                    severity=MigrationDivergenceSeverity.critica,
                    detalhe=f"Processo {numero_cnj} não vinculado a nenhum caso/cliente.",
                    context={"staging_record_id": str(record.id)},
                )
            )
            return

        matter = await self.db.get(Matter, matter_id)
        if matter is not None and not matter.case_number:
            matter.case_number = numero_cnj
        record.target_id = matter_id
        record.target_table = "matters"
        record.record_status = MigrationRecordStatus.importado
        record.imported_at = datetime.now(UTC)

    async def _import_financeiro(self, job: MigrationJob, record: MigrationStagingRecord) -> None:
        if record.conflict == MigrationConflict.duplicate:
            existing_id = await self._find_existing_target(record)
            if existing_id is not None:
                record.target_id = existing_id
                record.target_table = "finance_entries"
                record.record_status = MigrationRecordStatus.mesclado
                record.imported_at = datetime.now(UTC)
                return

        matter_id = await self._resolve_caso_ref(job, record.normalized.get("caso_ref"))
        due_date_iso = record.normalized.get("vencimento")
        due_date = (
            datetime.fromisoformat(due_date_iso).replace(tzinfo=UTC) if due_date_iso else None
        )
        entry = FinanceEntry(
            workspace_id=self.workspace_id,
            matter_id=matter_id,
            title=record.normalized["descricao"],
            entry_type=FinanceEntryType(record.normalized["tipo"]),
            amount=Decimal(record.normalized["valor"]),
            due_date=due_date,
            external_ref=record.normalized.get("referencia_externa"),
            entry_metadata={
                "migrationJobId": str(job.id),
                "migrationStagingRecordId": str(record.id),
            },
        )
        self.db.add(entry)
        await self.db.flush()
        record.target_id = entry.id
        record.target_table = "finance_entries"
        record.record_status = MigrationRecordStatus.importado
        record.imported_at = datetime.now(UTC)

    # ── 4) Reconciliação domínio a domínio + semáforo ───────────────────

    async def reconcile(self, job_id: UUID) -> MigrationJob:
        job = await self.get_job(job_id)
        if job.status == MigrationJobStatus.concluido:
            return job
        if job.status != MigrationJobStatus.reconciliando:
            raise MigrationInvalidTransitionError(
                f"Reconciliação exige importação concluída (status atual: {job.status.value})."
            )

        summary: dict[str, dict[str, int]] = {}
        for domain in MigrationDomain:
            counts = await self.db.execute(
                select(
                    sa_func.count(),
                    sa_func.count().filter(
                        MigrationStagingRecord.record_status == MigrationRecordStatus.importado
                    ),
                    sa_func.count().filter(
                        MigrationStagingRecord.record_status == MigrationRecordStatus.mesclado
                    ),
                    sa_func.count().filter(
                        MigrationStagingRecord.record_status == MigrationRecordStatus.rejeitado
                    ),
                    sa_func.count().filter(
                        MigrationStagingRecord.record_status == MigrationRecordStatus.pendente
                    ),
                    sa_func.count().filter(
                        MigrationStagingRecord.record_status == MigrationRecordStatus.invalido
                    ),
                ).where(
                    MigrationStagingRecord.job_id == job.id,
                    MigrationStagingRecord.domain == domain,
                )
            )
            origem, importado, mesclado, rejeitado, pendente, invalido = counts.one()
            if origem == 0:
                continue
            summary[domain.value] = {
                "origem": origem,
                "importado": importado,
                "mesclado": mesclado,
                "rejeitado": rejeitado,
                "invalidos": invalido,
                "aguardandoImportador": (
                    pendente if domain not in MIGRATION_DOMAINS_WITH_IMPORTER else 0
                ),
            }
            if pendente and domain not in MIGRATION_DOMAINS_WITH_IMPORTER:
                self.db.add(
                    MigrationDivergence(
                        workspace_id=self.workspace_id,
                        job_id=job.id,
                        domain=domain,
                        tipo="importador_pendente",
                        severity=MigrationDivergenceSeverity.atencao,
                        detalhe=(
                            f"{pendente} registros de '{domain.value}' aguardando importador "
                            "dedicado (onda futura) — dados preservados em staging."
                        ),
                    )
                )

        await self._check_saldo_financeiro(job, summary)
        await self._check_prazos_fatais(job)

        job.reconciliation_summary = summary
        has_critica = await self.db.scalar(
            select(sa_func.count()).where(
                MigrationDivergence.job_id == job.id,
                MigrationDivergence.severity == MigrationDivergenceSeverity.critica,
                MigrationDivergence.resolvido.is_(False),
            )
        )
        has_atencao = await self.db.scalar(
            select(sa_func.count()).where(
                MigrationDivergence.job_id == job.id,
                MigrationDivergence.severity == MigrationDivergenceSeverity.atencao,
                MigrationDivergence.resolvido.is_(False),
            )
        )
        if has_critica:
            job.semaphore = "vermelho"
        elif has_atencao:
            job.semaphore = "amarelo"
        else:
            job.semaphore = "verde"
        job.semaphore_computed_at = datetime.now(UTC)
        job.status = MigrationJobStatus.concluido
        job.completed_at = datetime.now(UTC)
        await self.db.flush()
        return job

    async def _check_saldo_financeiro(
        self, job: MigrationJob, summary: dict[str, dict[str, int]]
    ) -> None:
        for domain in (MigrationDomain.recebimentos, MigrationDomain.despesas):
            if domain.value not in summary:
                continue
            records = list(
                (
                    await self.db.scalars(
                        select(MigrationStagingRecord).where(
                            MigrationStagingRecord.job_id == job.id,
                            MigrationStagingRecord.domain == domain,
                            MigrationStagingRecord.record_status.in_(
                                [MigrationRecordStatus.importado, MigrationRecordStatus.mesclado]
                            ),
                        )
                    )
                ).all()
            )
            origem_total = sum(
                (Decimal(record.normalized["valor"]) for record in records), Decimal("0")
            )
            entry_type = (
                FinanceEntryType.receivable
                if domain == MigrationDomain.recebimentos
                else FinanceEntryType.expense
            )
            importado_total = await self.db.scalar(
                select(sa_func.coalesce(sa_func.sum(FinanceEntry.amount), 0)).where(
                    FinanceEntry.workspace_id == self.workspace_id,
                    FinanceEntry.entry_type == entry_type,
                    FinanceEntry.entry_metadata["migrationJobId"].as_string() == str(job.id),
                )
            )
            importado_total = Decimal(str(importado_total or 0))
            if abs(origem_total - importado_total) > _BALANCE_TOLERANCE:
                self.db.add(
                    MigrationDivergence(
                        workspace_id=self.workspace_id,
                        job_id=job.id,
                        domain=domain,
                        tipo="saldo_financeiro_divergente",
                        severity=MigrationDivergenceSeverity.critica,
                        detalhe=(
                            f"Saldo de '{domain.value}' divergente: origem R$ {origem_total} "
                            f"vs. importado R$ {importado_total}."
                        ),
                        context={
                            "origem": str(origem_total),
                            "importado": str(importado_total),
                        },
                    )
                )

    async def _check_prazos_fatais(self, job: MigrationJob) -> None:
        today = datetime.now(UTC).date().isoformat()
        records = list(
            (
                await self.db.scalars(
                    select(MigrationStagingRecord).where(
                        MigrationStagingRecord.job_id == job.id,
                        MigrationStagingRecord.domain == MigrationDomain.prazos,
                        MigrationStagingRecord.record_status == MigrationRecordStatus.pendente,
                    )
                )
            ).all()
        )
        for record in records:
            vencimento = record.normalized.get("vencimento")
            if vencimento and vencimento < today:
                self.db.add(
                    MigrationDivergence(
                        workspace_id=self.workspace_id,
                        job_id=job.id,
                        domain=MigrationDomain.prazos,
                        tipo="prazo_fatal_perdido",
                        severity=MigrationDivergenceSeverity.critica,
                        detalhe=(
                            f"Prazo '{record.normalized.get('descricao')}' com vencimento "
                            f"{vencimento} já vencido antes da conclusão da migração."
                        ),
                        context={"staging_record_id": str(record.id)},
                    )
                )

    # ── 5) Semáforo + decisão de corte ──────────────────────────────────

    async def get_semaphore(self, job_id: UUID) -> dict[str, Any]:
        job = await self.get_job(job_id)
        return {
            "semaphore": job.semaphore,
            "semaphoreComputedAt": job.semaphore_computed_at,
            "reconciliationSummary": job.reconciliation_summary,
            "cutoverDecision": job.cutover_decision,
        }

    async def decide_cutover(
        self, job_id: UUID, *, decided_by: UUID, decision: str, notes: Optional[str] = None
    ) -> MigrationJob:
        job = await self.get_job(job_id)
        if job.status != MigrationJobStatus.concluido:
            raise MigrationInvalidTransitionError(
                "Decisão de corte exige reconciliação concluída "
                f"(status atual: {job.status.value})."
            )
        # Registrado, não vira garantia de qualidade dos dados (Plano Mestre
        # §12) — só audita a escolha humana.
        job.cutover_decision = {
            "decision": decision,
            "decidedBy": str(decided_by),
            "decidedAt": datetime.now(UTC).isoformat(),
            "notes": notes,
            "semaphoreAtDecision": job.semaphore,
        }
        await self.db.flush()
        return job

    # ── 6) Fila de divergências / reprocessar correção ──────────────────

    async def resolve_divergence(
        self,
        job_id: UUID,
        divergence_id: UUID,
        *,
        resolved_by: UUID,
        resolution_note: Optional[str] = None,
    ) -> MigrationDivergence:
        await self.get_job(job_id)
        divergence = await self.db.scalar(
            select(MigrationDivergence).where(
                MigrationDivergence.id == divergence_id,
                MigrationDivergence.job_id == job_id,
                MigrationDivergence.workspace_id == self.workspace_id,
            )
        )
        if divergence is None:
            raise MigrationDivergenceNotFoundError()
        divergence.resolvido = True
        divergence.resolved_by = resolved_by
        divergence.resolved_at = datetime.now(UTC)
        divergence.resolution_note = resolution_note
        await self.db.flush()
        return divergence

    async def reprocess_record(
        self, job_id: UUID, record_id: UUID, *, normalized_patch: dict[str, Any]
    ) -> MigrationStagingRecord:
        """Corrige manualmente um registro inválido/rejeitado (ex.: CNJ
        digitado errado) e devolve para a fila de importação."""
        await self.get_job(job_id)
        record = await self.db.scalar(
            select(MigrationStagingRecord).where(
                MigrationStagingRecord.id == record_id,
                MigrationStagingRecord.job_id == job_id,
                MigrationStagingRecord.workspace_id == self.workspace_id,
            )
        )
        if record is None:
            raise MigrationStagingRecordNotFoundError()

        merged = {**record.raw, **normalized_patch}
        outcome = _normalize_row(record.domain, merged)
        record.normalized = outcome.normalized
        if outcome.error:
            record.record_status = (
                MigrationRecordStatus.rejeitado
                if outcome.rejected
                else MigrationRecordStatus.invalido
            )
            record.validation_error = outcome.error
        else:
            record.record_status = MigrationRecordStatus.pendente
            record.validation_error = None
            record.conflict = MigrationConflict.none
        await self.db.flush()
        return record

    # ── 7) Rollback do job inteiro ──────────────────────────────────────

    async def rollback(
        self, job_id: UUID, *, rolled_back_by: Optional[UUID] = None
    ) -> MigrationJob:
        job = await self.get_job(job_id)
        if job.status == MigrationJobStatus.revertido:
            return job
        if job.status not in MIGRATION_ROLLBACK_ELIGIBLE_STATUSES:
            raise MigrationInvalidTransitionError(
                f"Rollback não permitido neste estado (status atual: {job.status.value})."
            )

        records = list(
            (
                await self.db.scalars(
                    select(MigrationStagingRecord).where(
                        MigrationStagingRecord.job_id == job.id,
                        MigrationStagingRecord.target_id.is_not(None),
                    )
                )
            ).all()
        )

        # Só apaga o que este job CRIOU (record_status == importado); o que
        # foi mesclado em entidade já existente só perde o vínculo local.
        by_table: dict[str, list[MigrationStagingRecord]] = {}
        for record in records:
            if record.record_status == MigrationRecordStatus.mesclado:
                record.target_id = None
                record.target_table = None
                record.record_status = MigrationRecordStatus.pendente
                continue
            if record.record_status == MigrationRecordStatus.importado and record.target_table:
                by_table.setdefault(record.target_table, []).append(record)

        for table_name, model in (
            ("finance_entries", FinanceEntry),
            ("matters", Matter),
            ("clients", Client),
        ):
            for record in by_table.get(table_name, []):
                entity = await self.db.get(model, record.target_id)
                if entity is not None:
                    await self.db.delete(entity)
                record.target_id = None
                record.target_table = None
                record.record_status = MigrationRecordStatus.pendente

        job.status = MigrationJobStatus.revertido
        job.rolled_back_at = datetime.now(UTC)
        job.rolled_back_by = rolled_back_by
        await self.db.flush()
        return job
