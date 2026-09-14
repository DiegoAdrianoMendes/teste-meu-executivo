"""
Service: ReconciliationService (F7 — Conciliação Bancária)
Ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F7 / §3.8.

Fontes: importação de arquivo OFX/CSV e extrato Asaas (`get_statement` do
`PaymentGatewayPort`, já existente em `app/payments/`).

Idempotência: `bank_transactions.external_fingerprint` é UNIQUE por
workspace (fitid do OFX, ou hash determinístico de data+valor+descrição
para CSV/Asaas) — reimportar o mesmo arquivo/extrato nunca duplica linhas.
`reconciliation_matches.bank_transaction_id` é UNIQUE (1:1) — confirmar um
match aplica a baixa/categorização uma única vez; chamadas subsequentes são
no-op (idempotência sagrada).

Nada aplica sem confirmação humana, exceto:
- o match determinístico de uma cobrança NOSSA (FinanceCharge conhecida por
  valor+data, sem ambiguidade) — vira `confirmado` automaticamente;
- a transação `PAYMENT_FEE` do extrato Asaas, que sempre vira uma despesa
  (categoria `taxa_gateway`) automaticamente, pois é custo operacional
  inequívoco, não uma cobrança do cliente.

O resto vira sugestão (`sugerido`, method=deterministico ou ia) e só se
aplica quando um humano confirma via `confirm_match`/`confirm_matches_batch`.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging_privacy import safe_error_type
from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.models.bank_reconciliation import (
    BankTransaction,
    BankTransactionSource,
    ReconciliationMatch,
    ReconciliationMethod,
    ReconciliationStatus,
    ReconciliationTargetKind,
)
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_charge import FinanceCharge, FinanceChargeStatus
from app.payments.factory import GatewayCredentialError, get_gateway_for_workspace
from app.schemas.finance import FinanceEntryUpdate
from app.schemas.finance_expense import FinanceExpenseCreate
from app.services.finance_expense_service import FinanceExpenseService
from app.services.finance_service import FinanceService

GATEWAY_FEE_CATEGORY = "taxa_gateway"

# Palavras-chave -> categoria sugerida, usadas como heurística determinística
# e como fallback caso a chamada ao gateway de IA falhe/não esteja
# configurada. `app/ai` continua sendo a fonte "de verdade" quando disponível
# (ver `_suggest_category_via_ai`).
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "custas": ("custas", "taxa judiciaria", "taxa judiciária", "tribunal"),
    "aluguel": ("aluguel", "locacao", "locação", "condominio", "condomínio"),
    "software": ("software", "saas", "assinatura", "licenca", "licença"),
    "salarios": ("folha", "salario", "salário", "pro-labore", "pró-labore"),
    GATEWAY_FEE_CATEGORY: ("taxa asaas", "tarifa", "fee", "gateway"),
}


@dataclass
class IngestResult:
    imported: int = 0
    duplicates: int = 0
    matched_deterministic: int = 0
    suggested: int = 0


@dataclass
class _ParsedRow:
    fingerprint_seed: str
    date: datetime
    value: Decimal
    description: Optional[str]
    raw: dict[str, Any]


class ReconciliationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Fingerprint / idempotência ──

    @staticmethod
    def _hash_fingerprint(txn_date: date, value: Decimal, description: Optional[str]) -> str:
        payload = f"{txn_date.isoformat()}|{value}|{(description or '').strip().lower()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ── Parsing OFX ──

    _OFX_TRN_RE = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.IGNORECASE | re.DOTALL)
    _OFX_FIELD_RE = re.compile(r"<([A-Z0-9]+)>([^<\r\n]*)")

    @classmethod
    def _parse_ofx(cls, raw_text: str) -> list[_ParsedRow]:
        rows: list[_ParsedRow] = []
        for block in cls._OFX_TRN_RE.findall(raw_text):
            fields = {m.group(1).upper(): m.group(2).strip() for m in cls._OFX_FIELD_RE.finditer(block)}
            fitid = fields.get("FITID")
            trnamt = fields.get("TRNAMT")
            dtposted = fields.get("DTPOSTED")
            if not fitid or not trnamt or not dtposted:
                continue
            try:
                value = Decimal(trnamt)
            except InvalidOperation:
                continue
            txn_date = cls._parse_ofx_date(dtposted)
            description = fields.get("MEMO") or fields.get("NAME")
            rows.append(
                _ParsedRow(
                    fingerprint_seed=f"ofx:{fitid}",
                    date=txn_date,
                    value=value,
                    description=description,
                    raw=fields,
                )
            )
        return rows

    @staticmethod
    def _parse_ofx_date(raw: str) -> datetime:
        digits = re.sub(r"[^0-9]", "", raw)[:14].ljust(8, "0")
        year, month, day = int(digits[0:4]), int(digits[4:6]), int(digits[6:8])
        return datetime(year, month, day, tzinfo=timezone.utc)

    # ── Parsing CSV ──

    @staticmethod
    def _parse_csv(raw_text: str) -> list[_ParsedRow]:
        rows: list[_ParsedRow] = []
        reader = csv.DictReader(io.StringIO(raw_text))
        for record in reader:
            normalized = {(k or "").strip().lower(): (v or "").strip() for k, v in record.items()}
            raw_date = normalized.get("date") or normalized.get("data")
            raw_value = normalized.get("value") or normalized.get("valor")
            description = normalized.get("description") or normalized.get("descricao") or normalized.get(
                "descrição"
            )
            if not raw_date or not raw_value:
                continue
            try:
                value = Decimal(raw_value.replace(",", "."))
            except InvalidOperation:
                continue
            txn_date = ReconciliationService._parse_flexible_date(raw_date)
            if txn_date is None:
                continue
            rows.append(
                _ParsedRow(
                    fingerprint_seed="",  # calculado via hash de conteúdo
                    date=txn_date,
                    value=value,
                    description=description,
                    raw=dict(normalized),
                )
            )
        return rows

    @staticmethod
    def _parse_flexible_date(raw: str) -> Optional[datetime]:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                parsed = datetime.strptime(raw, fmt)
                return parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    # ── Ingestão comum ──

    async def _ingest_rows(
        self, workspace_id: UUID, source: BankTransactionSource, rows: list[_ParsedRow]
    ) -> IngestResult:
        result = IngestResult()
        for row in rows:
            fingerprint = row.fingerprint_seed or self._hash_fingerprint(
                row.date.date(), row.value, row.description
            )
            txn = BankTransaction(
                workspace_id=workspace_id,
                source=source,
                external_fingerprint=fingerprint,
                date=row.date,
                value=row.value,
                description=row.description,
                raw=row.raw,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(txn)
                    await self.db.flush()
            except IntegrityError:
                result.duplicates += 1
                continue

            result.imported += 1
            applied = await self._auto_apply_gateway_fee(workspace_id, txn)
            if applied:
                result.matched_deterministic += 1
                continue

            matched = await self._try_deterministic_charge_match(workspace_id, txn)
            if matched:
                result.matched_deterministic += 1
                continue

            await self._create_suggestion(workspace_id, txn)
            result.suggested += 1
        return result

    async def ingest_ofx(self, workspace_id: UUID, file_bytes: bytes) -> IngestResult:
        raw_text = file_bytes.decode("utf-8", errors="ignore")
        rows = self._parse_ofx(raw_text)
        return await self._ingest_rows(workspace_id, BankTransactionSource.ofx, rows)

    async def ingest_csv(self, workspace_id: UUID, file_bytes: bytes) -> IngestResult:
        raw_text = file_bytes.decode("utf-8", errors="ignore")
        rows = self._parse_csv(raw_text)
        return await self._ingest_rows(workspace_id, BankTransactionSource.csv, rows)

    async def pull_asaas_statement(self, workspace_id: UUID, start: date, finish: date) -> IngestResult:
        if not external_delivery_allowed():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "FIN-RECON-001",
                    "message": "Entrega externa desabilitada (gate fechado) — extrato Asaas indisponível.",
                },
            )
        try:
            gateway = await get_gateway_for_workspace(self.db, workspace_id)
        except GatewayCredentialError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "FIN-RECON-002", "message": str(exc)},
            ) from exc

        page = await gateway.get_statement(start, finish)
        rows: list[_ParsedRow] = []
        for entry in page.entries:
            entry_date = entry.date or start
            rows.append(
                _ParsedRow(
                    fingerprint_seed="",
                    date=datetime(entry_date.year, entry_date.month, entry_date.day, tzinfo=timezone.utc),
                    value=Decimal(str(entry.value)),
                    description=entry.description,
                    raw={"transactionType": entry.transaction_type, **entry.raw},
                )
            )
        return await self._ingest_rows(workspace_id, BankTransactionSource.asaas, rows)

    # ── PAYMENT_FEE -> despesa automática ──

    async def _auto_apply_gateway_fee(self, workspace_id: UUID, txn: BankTransaction) -> bool:
        transaction_type = str((txn.raw or {}).get("transactionType") or "").upper()
        if txn.source != BankTransactionSource.asaas or transaction_type != "PAYMENT_FEE":
            return False

        expense_service = FinanceExpenseService(self.db)
        expense = await expense_service.create_expense(
            workspace_id,
            FinanceExpenseCreate(
                title=f"Taxa de gateway ({txn.description or 'Asaas'})",
                description=txn.description,
                amount=float(abs(txn.value)),
                category=GATEWAY_FEE_CATEGORY,
                due_date=txn.date,
                entry_metadata={"bankTransactionId": str(txn.id)},
            ),
        )
        match = ReconciliationMatch(
            workspace_id=workspace_id,
            bank_transaction_id=txn.id,
            target_kind=ReconciliationTargetKind.expense,
            target_id=expense.id,
            method=ReconciliationMethod.deterministico,
            confidence=Decimal("1.0000"),
            suggested_category=GATEWAY_FEE_CATEGORY,
            status=ReconciliationStatus.confirmado,
            confirmed_at=self._utc_now(),
        )
        self.db.add(match)
        await self.db.flush()

        await self._apply_target(
            workspace_id, ReconciliationTargetKind.expense, expense.id, category=GATEWAY_FEE_CATEGORY
        )
        return True

    # ── Match determinístico de cobrança nossa ──

    async def _try_deterministic_charge_match(self, workspace_id: UUID, txn: BankTransaction) -> bool:
        if txn.value <= 0:
            return False

        external_reference = str((txn.raw or {}).get("externalReference") or "").strip()
        query = select(FinanceCharge).where(
            FinanceCharge.workspace_id == workspace_id,
            FinanceCharge.value == txn.value,
            FinanceCharge.status.in_(
                (FinanceChargeStatus.confirmada, FinanceChargeStatus.recebida)
            ),
        )
        if external_reference:
            query = query.where(FinanceCharge.external_reference == external_reference)
        else:
            day_start = datetime(txn.date.year, txn.date.month, txn.date.day, tzinfo=timezone.utc)
            day_end = day_start.replace(hour=23, minute=59, second=59)
            query = query.where(FinanceCharge.due_date >= day_start, FinanceCharge.due_date <= day_end)

        candidates = (await self.db.execute(query)).scalars().all()
        unmatched_candidates = []
        for candidate in candidates:
            already_matched = await self.db.execute(
                select(ReconciliationMatch).where(
                    ReconciliationMatch.workspace_id == workspace_id,
                    ReconciliationMatch.target_kind == ReconciliationTargetKind.charge,
                    ReconciliationMatch.target_id == candidate.id,
                    ReconciliationMatch.status == ReconciliationStatus.confirmado,
                )
            )
            if already_matched.scalar_one_or_none() is None:
                unmatched_candidates.append(candidate)

        if len(unmatched_candidates) != 1:
            return False

        charge = unmatched_candidates[0]
        match = ReconciliationMatch(
            workspace_id=workspace_id,
            bank_transaction_id=txn.id,
            target_kind=ReconciliationTargetKind.charge,
            target_id=charge.id,
            method=ReconciliationMethod.deterministico,
            confidence=Decimal("1.0000"),
            status=ReconciliationStatus.confirmado,
            confirmed_at=self._utc_now(),
        )
        self.db.add(match)
        await self.db.flush()

        await self._apply_target(workspace_id, ReconciliationTargetKind.charge, charge.id)
        return True

    # ── Sugestão (determinística por palavra-chave, ou IA) ──

    async def _create_suggestion(self, workspace_id: UUID, txn: BankTransaction) -> ReconciliationMatch:
        category, tags, method, confidence = await self._suggest_category(txn)
        match = ReconciliationMatch(
            workspace_id=workspace_id,
            bank_transaction_id=txn.id,
            target_kind=None,
            target_id=None,
            method=method,
            confidence=confidence,
            suggested_category=category,
            suggested_tags=tags,
            status=ReconciliationStatus.sugerido,
        )
        self.db.add(match)
        await self.db.flush()
        return match

    async def _suggest_category(
        self, txn: BankTransaction
    ) -> tuple[Optional[str], list[str], ReconciliationMethod, Optional[Decimal]]:
        description = (txn.description or "").lower()
        for category, keywords in _CATEGORY_KEYWORDS.items():
            if any(keyword in description for keyword in keywords):
                return category, [category], ReconciliationMethod.deterministico, Decimal("0.9000")

        ai_result = await self._suggest_category_via_ai(txn)
        if ai_result is not None:
            category, tags, confidence = ai_result
            return category, tags, ReconciliationMethod.ia, confidence

        return None, [], ReconciliationMethod.ia, Decimal("0.1000")

    async def _suggest_category_via_ai(
        self, txn: BankTransaction
    ) -> Optional[tuple[Optional[str], list[str], Decimal]]:
        """Consulta o gateway de IA (TaskClass.SIMPLE) para sugerir categoria.

        Best-effort: qualquer falha (gate fechado, credencial ausente,
        provider indisponível) apenas resulta em `None` — a sugestão cai
        para o valor neutro do heurístico acima. Com `AI_PROVIDER=fake`
        (ambiente de dev/CI) o gateway responde de forma determinística,
        sem tocar rede.
        """
        try:
            from app.ai.base import AIMessage
            from app.ai.factory import get_provider_for_task
            from app.ai.models import TaskClass

            provider = get_provider_for_task(TaskClass.SIMPLE)
            schema = {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["category"],
            }
            result = await provider.complete(
                [
                    AIMessage(
                        role="system",
                        content=(
                            "Classifique a transação bancária em uma categoria financeira curta "
                            "(ex.: custas, aluguel, software, salarios, taxa_gateway, outros). "
                            "Responda apenas o JSON solicitado."
                        ),
                    ),
                    AIMessage(
                        role="user",
                        content=f"Descrição: {txn.description or ''} | Valor: {txn.value}",
                    ),
                ],
                temperature=0.0,
                max_tokens=200,
                response_schema=schema,
                workspace_id=str(txn.workspace_id),
                agent_name="reconciliation",
                contains_sensitive_data=False,
                allow_response_cache=True,
            )
            data = result.structured_data or {}
            category = data.get("category")
            tags = data.get("tags") or ([] if not category else [category])
            if not category:
                return None
            return str(category), [str(tag) for tag in tags], Decimal("0.6000")
        except Exception as exc:  # noqa: BLE001 — best-effort, nunca bloqueia o fluxo
            logger.warning("reconciliation.ai_suggestion_failed", error_type=safe_error_type(exc))
            return None

    # ── Leitura ──

    async def list_pending(self, workspace_id: UUID, *, limit: int = 100) -> list[ReconciliationMatch]:
        # `selectinload` evita lazy-load da transação em contexto async (e é o
        # dado que a UI de conciliação (F7) precisa para exibir a coluna
        # "transação" — descrição, data e valor — sem uma query por linha).
        result = await self.db.execute(
            select(ReconciliationMatch)
            .options(selectinload(ReconciliationMatch.bank_transaction))
            .where(
                ReconciliationMatch.workspace_id == workspace_id,
                ReconciliationMatch.status == ReconciliationStatus.sugerido,
            )
            .order_by(ReconciliationMatch.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _get_match(self, workspace_id: UUID, match_id: UUID) -> ReconciliationMatch:
        result = await self.db.execute(
            select(ReconciliationMatch).where(
                ReconciliationMatch.id == match_id,
                ReconciliationMatch.workspace_id == workspace_id,
            )
        )
        match = result.scalar_one_or_none()
        if match is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "FIN-RECON-003", "message": "Sugestão de conciliação não encontrada."},
            )
        return match

    # ── Confirmação / rejeição ──

    async def confirm_match(
        self,
        workspace_id: UUID,
        match_id: UUID,
        *,
        target_kind: ReconciliationTargetKind,
        target_id: UUID,
        confirmed_by: Optional[UUID],
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> ReconciliationMatch:
        match = await self._get_match(workspace_id, match_id)

        # Idempotência: confirmar 2x nunca reaplica a baixa/categorização.
        if match.status == ReconciliationStatus.confirmado:
            return match
        if match.status == ReconciliationStatus.rejeitado:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "FIN-RECON-004", "message": "Sugestão já foi rejeitada."},
            )

        match.target_kind = target_kind
        match.target_id = target_id
        match.status = ReconciliationStatus.confirmado
        match.confirmed_by = confirmed_by
        match.confirmed_at = self._utc_now()
        if category:
            match.suggested_category = category
        if tags is not None:
            match.suggested_tags = tags
        await self.db.flush()

        await self._apply_target(workspace_id, target_kind, target_id, category=category, tags=tags)
        return match

    async def confirm_matches_batch(
        self,
        workspace_id: UUID,
        *,
        items: list[dict[str, Any]],
        confirmed_by: Optional[UUID],
    ) -> list[ReconciliationMatch]:
        confirmed: list[ReconciliationMatch] = []
        for item in items:
            confirmed.append(
                await self.confirm_match(
                    workspace_id,
                    item["match_id"],
                    target_kind=item["target_kind"],
                    target_id=item["target_id"],
                    confirmed_by=confirmed_by,
                    category=item.get("category"),
                    tags=item.get("tags"),
                )
            )
        return confirmed

    async def reject_match(
        self,
        workspace_id: UUID,
        match_id: UUID,
        *,
        confirmed_by: Optional[UUID],
        reason: Optional[str] = None,
    ) -> ReconciliationMatch:
        match = await self._get_match(workspace_id, match_id)
        if match.status == ReconciliationStatus.confirmado:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "FIN-RECON-005", "message": "Sugestão já foi confirmada."},
            )
        if match.status == ReconciliationStatus.rejeitado:
            return match

        match.status = ReconciliationStatus.rejeitado
        match.confirmed_by = confirmed_by
        match.confirmed_at = self._utc_now()
        if reason:
            snapshot = dict(match.ai_snapshot or {})
            snapshot["rejectionReason"] = reason
            match.ai_snapshot = snapshot
        await self.db.flush()
        return match

    async def reject_matches_batch(
        self,
        workspace_id: UUID,
        *,
        match_ids: list[UUID],
        confirmed_by: Optional[UUID],
        reason: Optional[str] = None,
    ) -> list[ReconciliationMatch]:
        return [
            await self.reject_match(workspace_id, match_id, confirmed_by=confirmed_by, reason=reason)
            for match_id in match_ids
        ]

    # ── Aplicação no alvo (baixa/categorização) ──

    async def _apply_target(
        self,
        workspace_id: UUID,
        target_kind: ReconciliationTargetKind,
        target_id: UUID,
        *,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> None:
        if target_kind == ReconciliationTargetKind.charge:
            charge = await self.db.get(FinanceCharge, target_id)
            if charge is None or charge.workspace_id != workspace_id:
                return
            if charge.status != FinanceChargeStatus.recebida:
                charge.status = FinanceChargeStatus.recebida
                await self.db.flush()
            entry_id = charge.entry_id
        else:
            entry_id = target_id

        entry = await self.db.get(FinanceEntry, entry_id)
        if entry is None or entry.workspace_id != workspace_id:
            return
        if entry.entry_type not in (FinanceEntryType.receivable, FinanceEntryType.expense):
            return
        if entry.status == FinanceEntryStatus.paid:
            return

        update_payload: dict[str, Any] = {"status": FinanceEntryStatus.paid}
        if tags is not None:
            update_payload["tags"] = tags
        await FinanceService(self.db).update_entry(
            workspace_id, entry.id, FinanceEntryUpdate(**update_payload)
        )
        # `category` não existe em `FinanceEntryUpdate` (schema F1/F8 não o
        # expõe para update genérico) — setamos diretamente no ORM, mesma
        # sessão/identity map do `update_entry` acima.
        if category:
            entry.category = category
            await self.db.flush()

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)
