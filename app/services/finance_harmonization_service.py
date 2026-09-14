"""
Service: FinanceHarmonizationService (F14 — Harmonização de Documentos
Financeiros × Lançamentos Manuais).

Extensão da conciliação bancária (F7, `app/services/reconciliation_service.py`):
lá o alvo é casar um extrato bancário cru contra cobranças/lançamentos e
aplicar a baixa quando confirmado. Aqui o alvo é diagnóstico — comparar
qualquer documento financeiro (extrato/DRE em PDF/XML/CSV) contra o
financeiro local (`FinanceEntry` + `BankTransaction`) do período e produzir
uma tabela comparativa (bate | não bate | só no documento | só no sistema).

Nada muda `FinanceEntry`/`BankTransaction`: decidir uma linha ou
validar/rejeitar o run só grava a decisão humana. Quem aplica baixa
continua sendo o F7 (`ReconciliationService.confirm_match`) — esta
harmonização nunca chama `_apply_target`, de propósito, para não virar um
segundo caminho de aplicação (F7 continua sendo o único).

Quota mensal: `FinanceHarmonizationQuota` é `UNIQUE(workspace_id,
competence)` — incrementar `used_count` é sempre feito dentro do `INSERT ...
ON CONFLICT`/`SELECT FOR UPDATE` da mesma transação do run, então duas
tentativas concorrentes nunca furam o limite (idempotência sagrada).
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.bank_reconciliation import BankTransaction
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_harmonization import (
    FinanceHarmonizationDecision,
    FinanceHarmonizationItem,
    FinanceHarmonizationMatchedSource,
    FinanceHarmonizationMatchKind,
    FinanceHarmonizationQuota,
    FinanceHarmonizationRun,
    FinanceHarmonizationSourceType,
    FinanceHarmonizationStatus,
)
from app.services.finance_document_parser import ParsedDocumentRow, parse_document

# 1 verificação completa/mês (base) + 2 bônus padrão já inclusos = 3 usos.
# `FinanceHarmonizationQuota.bonus_extra` é aditivo, só via admin/blackbox.
BASE_MONTHLY_LIMIT = 1
DEFAULT_BONUS = 2
APPROXIMATE_MATCH_TOLERANCE_DAYS = 3

_EXTENSION_TO_SOURCE_TYPE = {
    "csv": FinanceHarmonizationSourceType.csv,
    "xml": FinanceHarmonizationSourceType.xml,
    "pdf": FinanceHarmonizationSourceType.pdf,
}


class FinanceHarmonizationQuotaExceededError(Exception):
    def __init__(self, *, renews_at: date, limit: int, used: int) -> None:
        self.renews_at = renews_at
        self.limit = limit
        self.used = used
        super().__init__(
            f"Cota mensal de verificação completa esgotada ({used}/{limit}). "
            f"Renova em {renews_at.isoformat()}."
        )


@dataclass
class _SystemRow:
    source: FinanceHarmonizationMatchedSource
    entity_id: UUID
    row_date: date
    value: Decimal
    description: Optional[str]
    fingerprint: Optional[str] = None


def _competence_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _next_competence_start(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def _detect_source_type(filename: str, content_type: Optional[str]) -> FinanceHarmonizationSourceType:
    lowered = (filename or "").lower()
    for ext, source_type in _EXTENSION_TO_SOURCE_TYPE.items():
        if lowered.endswith(f".{ext}"):
            return source_type
    if content_type:
        if "csv" in content_type:
            return FinanceHarmonizationSourceType.csv
        if "xml" in content_type:
            return FinanceHarmonizationSourceType.xml
        if "pdf" in content_type:
            return FinanceHarmonizationSourceType.pdf
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "FIN-HARM-001",
            "message": "Formato de arquivo não reconhecido — envie PDF, XML ou CSV.",
        },
    )


class FinanceHarmonizationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Quota ──

    async def _get_or_create_quota(
        self, workspace_id: UUID, competence: str
    ) -> FinanceHarmonizationQuota:
        result = await self.db.execute(
            select(FinanceHarmonizationQuota).where(
                FinanceHarmonizationQuota.workspace_id == workspace_id,
                FinanceHarmonizationQuota.competence == competence,
            )
        )
        quota = result.scalar_one_or_none()
        if quota is not None:
            return quota

        quota = FinanceHarmonizationQuota(
            workspace_id=workspace_id, competence=competence, used_count=0, bonus_extra=0
        )
        self.db.add(quota)
        try:
            async with self.db.begin_nested():
                await self.db.flush()
        except IntegrityError:
            # Corrida: outra transação já criou a linha da competência —
            # descarta a instância local e relê a que ganhou (idempotência).
            self.db.expunge(quota)
            result = await self.db.execute(
                select(FinanceHarmonizationQuota).where(
                    FinanceHarmonizationQuota.workspace_id == workspace_id,
                    FinanceHarmonizationQuota.competence == competence,
                )
            )
            quota = result.scalar_one()
        return quota

    async def get_quota_status(self, workspace_id: UUID, *, today: Optional[date] = None) -> dict[str, Any]:
        today = today or datetime.now(timezone.utc).date()
        competence = _competence_of(today)
        quota = await self._get_or_create_quota(workspace_id, competence)
        limit = BASE_MONTHLY_LIMIT + DEFAULT_BONUS + quota.bonus_extra
        return {
            "competence": competence,
            "used": quota.used_count,
            "limit": limit,
            "remaining": max(limit - quota.used_count, 0),
            "renewsAt": _next_competence_start(today),
        }

    async def _consume_quota(self, workspace_id: UUID, competence: str) -> None:
        quota = await self._get_or_create_quota(workspace_id, competence)
        limit = BASE_MONTHLY_LIMIT + DEFAULT_BONUS + quota.bonus_extra
        if quota.used_count >= limit:
            renews_at = _next_competence_start(date.fromisoformat(f"{competence}-01"))
            raise FinanceHarmonizationQuotaExceededError(
                renews_at=renews_at, limit=limit, used=quota.used_count
            )
        quota.used_count += 1
        await self.db.flush()

    async def admin_grant_bonus(
        self, workspace_id: UUID, *, competence: str, extra: int, granted_by: UUID
    ) -> FinanceHarmonizationQuota:
        if extra <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "FIN-HARM-002", "message": "O bônus concedido deve ser positivo."},
            )
        quota = await self._get_or_create_quota(workspace_id, competence)
        quota.bonus_extra += extra
        quota.granted_by = granted_by
        quota.granted_at = self._utc_now()
        await self.db.flush()
        return quota

    # ── Criação do run (upload + matching) ──

    async def create_run(
        self,
        workspace_id: UUID,
        *,
        filename: str,
        content_type: Optional[str],
        raw_bytes: bytes,
        period_start: date,
        period_end: date,
        created_by: Optional[UUID],
    ) -> FinanceHarmonizationRun:
        if period_end < period_start:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "FIN-HARM-003", "message": "Período inválido: fim antes do início."},
            )

        source_type = _detect_source_type(filename, content_type)
        competence = _competence_of(period_start)

        try:
            await self._consume_quota(workspace_id, competence)
        except FinanceHarmonizationQuotaExceededError as error:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "FIN-HARM-QUOTA-429",
                    "message": (
                        f"Limite de verificações completas do mês atingido "
                        f"({error.used}/{error.limit}). Renova em "
                        f"{error.renews_at.isoformat()}."
                    ),
                    "renewsAt": error.renews_at.isoformat(),
                    "limit": error.limit,
                    "used": error.used,
                },
            ) from error

        parsed = parse_document(source_type=source_type.value, filename=filename, raw_bytes=raw_bytes)

        run = FinanceHarmonizationRun(
            workspace_id=workspace_id,
            source_document_name=filename,
            source_type=source_type,
            period_start=period_start,
            period_end=period_end,
            competence=competence,
            status=FinanceHarmonizationStatus.aguardando_validacao,
            parser_warning=parsed.warning,
            created_by=created_by,
        )
        self.db.add(run)
        await self.db.flush()

        items, totals = await self._match_and_build_items(
            workspace_id, run.id, period_start, period_end, parsed.rows
        )
        for item in items:
            self.db.add(item)
        run.totals = totals
        await self.db.flush()
        return await self.get_run(workspace_id, run.id)

    # ── Motor de casamento ──

    async def _load_system_rows(
        self, workspace_id: UUID, period_start: date, period_end: date
    ) -> list[_SystemRow]:
        period_start_dt = datetime(period_start.year, period_start.month, period_start.day, tzinfo=timezone.utc)
        period_end_dt = datetime(
            period_end.year, period_end.month, period_end.day, 23, 59, 59, tzinfo=timezone.utc
        )

        entries_result = await self.db.execute(
            select(FinanceEntry).where(
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.deleted_at.is_(None),
                FinanceEntry.entry_type.in_((FinanceEntryType.receivable, FinanceEntryType.expense)),
                FinanceEntry.status != FinanceEntryStatus.cancelled,
                FinanceEntry.due_date.is_not(None),
                FinanceEntry.due_date >= period_start_dt,
                FinanceEntry.due_date <= period_end_dt,
            )
        )
        rows = [
            _SystemRow(
                source=FinanceHarmonizationMatchedSource.finance_entry,
                entity_id=entry.id,
                row_date=entry.due_date.date(),
                value=Decimal(str(entry.amount)),
                description=entry.title,
            )
            for entry in entries_result.scalars().all()
        ]

        txns_result = await self.db.execute(
            select(BankTransaction).where(
                BankTransaction.workspace_id == workspace_id,
                BankTransaction.date >= period_start_dt,
                BankTransaction.date <= period_end_dt,
            )
        )
        rows.extend(
            _SystemRow(
                source=FinanceHarmonizationMatchedSource.bank_transaction,
                entity_id=txn.id,
                row_date=txn.date.date(),
                value=txn.value,
                description=txn.description,
                fingerprint=txn.external_fingerprint,
            )
            for txn in txns_result.scalars().all()
        )
        return rows

    @staticmethod
    def _description_similarity(a: Optional[str], b: Optional[str]) -> float:
        if not a or not b:
            return 0.0
        return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()

    async def _match_and_build_items(
        self,
        workspace_id: UUID,
        run_id: UUID,
        period_start: date,
        period_end: date,
        doc_rows: list[ParsedDocumentRow],
    ) -> tuple[list[FinanceHarmonizationItem], dict[str, Any]]:
        system_rows = await self._load_system_rows(workspace_id, period_start, period_end)
        unconsumed = list(system_rows)
        items: list[FinanceHarmonizationItem] = []

        for doc_row in doc_rows:
            candidates = [row for row in unconsumed if row.value == doc_row.value]

            # 1) casamento por fingerprint (mesma transação já importada via F7).
            fingerprint_match = next(
                (row for row in candidates if row.fingerprint and row.fingerprint == doc_row.fingerprint),
                None,
            )
            best = fingerprint_match
            if best is None and candidates:
                # 2) valor exato + menor diferença de data (empate quebrado por
                #    descrição mais parecida).
                candidates.sort(
                    key=lambda row: (
                        abs((row.row_date - doc_row.date).days),
                        -self._description_similarity(row.description, doc_row.description),
                    )
                )
                nearest = candidates[0]
                if abs((nearest.row_date - doc_row.date).days) <= APPROXIMATE_MATCH_TOLERANCE_DAYS:
                    best = nearest

            if best is not None:
                unconsumed.remove(best)
                delta_days = (best.row_date - doc_row.date).days
                match_kind = (
                    FinanceHarmonizationMatchKind.exato
                    if delta_days == 0
                    else FinanceHarmonizationMatchKind.aproximado
                )
                items.append(
                    FinanceHarmonizationItem(
                        workspace_id=workspace_id,
                        run_id=run_id,
                        doc_date=doc_row.date,
                        doc_value=doc_row.value,
                        doc_description=doc_row.description,
                        doc_fingerprint=doc_row.fingerprint,
                        doc_raw=doc_row.raw,
                        matched_source=best.source,
                        matched_entry_id=best.entity_id,
                        match_kind=match_kind,
                        delta_value=Decimal("0.00"),
                        delta_days=delta_days,
                    )
                )
            else:
                items.append(
                    FinanceHarmonizationItem(
                        workspace_id=workspace_id,
                        run_id=run_id,
                        doc_date=doc_row.date,
                        doc_value=doc_row.value,
                        doc_description=doc_row.description,
                        doc_fingerprint=doc_row.fingerprint,
                        doc_raw=doc_row.raw,
                        matched_source=None,
                        matched_entry_id=None,
                        match_kind=FinanceHarmonizationMatchKind.so_no_documento,
                        delta_value=None,
                        delta_days=None,
                    )
                )

        for leftover in unconsumed:
            items.append(
                FinanceHarmonizationItem(
                    workspace_id=workspace_id,
                    run_id=run_id,
                    doc_date=None,
                    doc_value=None,
                    doc_description=None,
                    doc_fingerprint=None,
                    matched_source=leftover.source,
                    matched_entry_id=leftover.entity_id,
                    match_kind=FinanceHarmonizationMatchKind.so_no_sistema,
                    delta_value=None,
                    delta_days=None,
                )
            )

        totals = {
            "batem": sum(1 for i in items if i.match_kind == FinanceHarmonizationMatchKind.exato),
            "naoBatem": sum(1 for i in items if i.match_kind == FinanceHarmonizationMatchKind.aproximado),
            "soNoDocumento": sum(
                1 for i in items if i.match_kind == FinanceHarmonizationMatchKind.so_no_documento
            ),
            "soNoSistema": sum(
                1 for i in items if i.match_kind == FinanceHarmonizationMatchKind.so_no_sistema
            ),
        }
        return items, totals

    # ── Leitura ──

    async def list_runs(self, workspace_id: UUID, *, limit: int = 50) -> list[FinanceHarmonizationRun]:
        result = await self.db.execute(
            select(FinanceHarmonizationRun)
            .where(FinanceHarmonizationRun.workspace_id == workspace_id)
            .order_by(FinanceHarmonizationRun.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_run(self, workspace_id: UUID, run_id: UUID) -> FinanceHarmonizationRun:
        result = await self.db.execute(
            select(FinanceHarmonizationRun)
            .options(selectinload(FinanceHarmonizationRun.items))
            .where(
                FinanceHarmonizationRun.id == run_id,
                FinanceHarmonizationRun.workspace_id == workspace_id,
            )
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "FIN-HARM-004", "message": "Verificação de harmonização não encontrada."},
            )
        return run

    async def _get_item(
        self, workspace_id: UUID, run_id: UUID, item_id: UUID
    ) -> FinanceHarmonizationItem:
        result = await self.db.execute(
            select(FinanceHarmonizationItem).where(
                FinanceHarmonizationItem.id == item_id,
                FinanceHarmonizationItem.run_id == run_id,
                FinanceHarmonizationItem.workspace_id == workspace_id,
            )
        )
        item = result.scalar_one_or_none()
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "FIN-HARM-005", "message": "Linha comparativa não encontrada."},
            )
        return item

    # ── Decisão humana (nunca aplica nada em FinanceEntry/BankTransaction) ──

    async def decide_item(
        self,
        workspace_id: UUID,
        run_id: UUID,
        item_id: UUID,
        *,
        decision: FinanceHarmonizationDecision,
        decided_by: Optional[UUID],
    ) -> FinanceHarmonizationItem:
        run = await self.get_run(workspace_id, run_id)
        if run.status in (FinanceHarmonizationStatus.validado, FinanceHarmonizationStatus.rejeitado):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "FIN-HARM-006",
                    "message": "A verificação já foi validada/rejeitada — decisões por linha estão encerradas.",
                },
            )
        item = await self._get_item(workspace_id, run_id, item_id)

        # Exactly-once: redecidir com o mesmo valor é no-op idempotente;
        # mudar de decisão depois de decidido não é permitido por esta rota
        # (evita histórico ambíguo sem um fluxo de "reabrir" explícito).
        if item.decision == decision:
            return item
        if item.decision != FinanceHarmonizationDecision.pendente:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "FIN-HARM-007",
                    "message": "Esta linha já foi decidida — decisão é definitiva (exactly-once).",
                },
            )

        item.decision = decision
        item.decided_by = decided_by
        item.decided_at = self._utc_now()
        await self.db.flush()
        return item

    async def _finalize_run(
        self,
        workspace_id: UUID,
        run_id: UUID,
        *,
        target_status: FinanceHarmonizationStatus,
        decided_by: Optional[UUID],
    ) -> FinanceHarmonizationRun:
        run = await self.get_run(workspace_id, run_id)
        if run.status == target_status:
            return run
        if run.status in (FinanceHarmonizationStatus.validado, FinanceHarmonizationStatus.rejeitado):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "FIN-HARM-008",
                    "message": "Esta verificação já foi encerrada (validada ou rejeitada).",
                },
            )
        run.status = target_status
        run.decided_by = decided_by
        run.decided_at = self._utc_now()
        await self.db.flush()
        return run

    async def validate_run(
        self, workspace_id: UUID, run_id: UUID, *, decided_by: Optional[UUID]
    ) -> FinanceHarmonizationRun:
        return await self._finalize_run(
            workspace_id, run_id, target_status=FinanceHarmonizationStatus.validado, decided_by=decided_by
        )

    async def reject_run(
        self, workspace_id: UUID, run_id: UUID, *, decided_by: Optional[UUID]
    ) -> FinanceHarmonizationRun:
        return await self._finalize_run(
            workspace_id, run_id, target_status=FinanceHarmonizationStatus.rejeitado, decided_by=decided_by
        )

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)
