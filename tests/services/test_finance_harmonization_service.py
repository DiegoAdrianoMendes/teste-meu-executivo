from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.bank_reconciliation import BankTransaction, BankTransactionSource
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_harmonization import (
    FinanceHarmonizationDecision,
    FinanceHarmonizationMatchKind,
    FinanceHarmonizationStatus,
)
from app.services.finance_harmonization_service import (
    BASE_MONTHLY_LIMIT,
    DEFAULT_BONUS,
    FinanceHarmonizationService,
)

CSV_HARMONIZATION_SAMPLE = (
    "date,value,description\n"
    "2026-07-02,250.00,Aluguel escritorio\n"  # bate exato com o FinanceEntry abaixo
    "2026-07-10,999.00,Transacao desconhecida\n"  # só no documento
)


async def _seed_matching_entry(db_session, workspace_id, *, amount=Decimal("250.00"), due_date=None):
    entry = FinanceEntry(
        workspace_id=workspace_id,
        title="Aluguel escritorio",
        entry_type=FinanceEntryType.expense,
        status=FinanceEntryStatus.pending,
        amount=amount,
        due_date=due_date or datetime(2026, 7, 2, tzinfo=UTC),
    )
    db_session.add(entry)
    await db_session.flush()
    return entry


async def _seed_orphan_entry(db_session, workspace_id):
    entry = FinanceEntry(
        workspace_id=workspace_id,
        title="Consultoria avulsa",
        entry_type=FinanceEntryType.receivable,
        status=FinanceEntryStatus.pending,
        amount=Decimal("777.00"),
        due_date=datetime(2026, 7, 15, tzinfo=UTC),
    )
    db_session.add(entry)
    await db_session.flush()
    return entry


@pytest.mark.asyncio
async def test_create_run_classifies_exato_e_so_no_documento(db_session, auth_context):
    await _seed_matching_entry(db_session, auth_context.workspace_id)
    service = FinanceHarmonizationService(db_session)

    run = await service.create_run(
        auth_context.workspace_id,
        filename="extrato-julho.csv",
        content_type="text/csv",
        raw_bytes=CSV_HARMONIZATION_SAMPLE.encode("utf-8"),
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        created_by=auth_context.user_id,
    )

    assert run.status == FinanceHarmonizationStatus.aguardando_validacao
    kinds = sorted(item.match_kind.value for item in run.items)
    assert kinds == ["exato", "so_no_documento"]
    assert run.totals["batem"] == 1
    assert run.totals["soNoDocumento"] == 1


@pytest.mark.asyncio
async def test_create_run_classifies_aproximado_within_3_days(db_session, auth_context):
    # Lançamento no sistema em 2026-07-04, documento aponta 2026-07-02 (2 dias) -> aproximado.
    await _seed_matching_entry(
        db_session, auth_context.workspace_id, due_date=datetime(2026, 7, 4, tzinfo=UTC)
    )
    service = FinanceHarmonizationService(db_session)

    run = await service.create_run(
        auth_context.workspace_id,
        filename="extrato.csv",
        content_type="text/csv",
        raw_bytes="date,value,description\n2026-07-02,250.00,Aluguel escritorio\n".encode("utf-8"),
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        created_by=auth_context.user_id,
    )

    assert [item.match_kind for item in run.items] == [FinanceHarmonizationMatchKind.aproximado]
    assert run.items[0].delta_days == 2


@pytest.mark.asyncio
async def test_create_run_classifies_so_no_sistema_for_orphan_entries(db_session, auth_context):
    await _seed_orphan_entry(db_session, auth_context.workspace_id)
    service = FinanceHarmonizationService(db_session)

    run = await service.create_run(
        auth_context.workspace_id,
        filename="extrato-vazio.csv",
        content_type="text/csv",
        raw_bytes="date,value,description\n".encode("utf-8"),
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        created_by=auth_context.user_id,
    )

    assert [item.match_kind for item in run.items] == [FinanceHarmonizationMatchKind.so_no_sistema]
    assert run.totals["soNoSistema"] == 1


@pytest.mark.asyncio
async def test_create_run_matches_bank_transaction_by_fingerprint(db_session, auth_context):
    txn = BankTransaction(
        workspace_id=auth_context.workspace_id,
        source=BankTransactionSource.csv,
        external_fingerprint="doc-fp-shared",
        date=datetime(2026, 7, 20, tzinfo=UTC),
        value=Decimal("42.00"),
        description="Transacao compartilhada",
        raw={},
    )
    db_session.add(txn)
    await db_session.flush()

    service = FinanceHarmonizationService(db_session)
    run = await service.create_run(
        auth_context.workspace_id,
        filename="extrato.csv",
        content_type="text/csv",
        raw_bytes="id,date,value,description\ndoc-fp-shared,2026-07-20,42.00,Transacao compartilhada\n".encode(
            "utf-8"
        ),
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        created_by=auth_context.user_id,
    )

    assert [item.match_kind for item in run.items] == [FinanceHarmonizationMatchKind.exato]


@pytest.mark.asyncio
async def test_decide_item_never_mutates_finance_entry(db_session, auth_context):
    entry = await _seed_matching_entry(db_session, auth_context.workspace_id)
    service = FinanceHarmonizationService(db_session)

    run = await service.create_run(
        auth_context.workspace_id,
        filename="extrato.csv",
        content_type="text/csv",
        raw_bytes="date,value,description\n2026-07-02,250.00,Aluguel escritorio\n".encode("utf-8"),
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        created_by=auth_context.user_id,
    )
    item = run.items[0]

    decided = await service.decide_item(
        auth_context.workspace_id,
        run.id,
        item.id,
        decision=FinanceHarmonizationDecision.confirmado,
        decided_by=auth_context.user_id,
    )
    assert decided.decision == FinanceHarmonizationDecision.confirmado

    # A decisão humana NUNCA aplica baixa/categorização — F14 é diagnóstica,
    # quem aplica continua sendo o F7.
    await db_session.refresh(entry)
    assert entry.status == FinanceEntryStatus.pending


@pytest.mark.asyncio
async def test_decide_item_is_exactly_once(db_session, auth_context):
    await _seed_matching_entry(db_session, auth_context.workspace_id)
    service = FinanceHarmonizationService(db_session)
    run = await service.create_run(
        auth_context.workspace_id,
        filename="extrato.csv",
        content_type="text/csv",
        raw_bytes="date,value,description\n2026-07-02,250.00,Aluguel escritorio\n".encode("utf-8"),
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        created_by=auth_context.user_id,
    )
    item = run.items[0]

    first = await service.decide_item(
        auth_context.workspace_id,
        run.id,
        item.id,
        decision=FinanceHarmonizationDecision.confirmado,
        decided_by=auth_context.user_id,
    )
    # Redecidir com o mesmo valor é idempotente (no-op).
    second = await service.decide_item(
        auth_context.workspace_id,
        run.id,
        item.id,
        decision=FinanceHarmonizationDecision.confirmado,
        decided_by=auth_context.user_id,
    )
    assert first.decided_at == second.decided_at

    # Mudar a decisão depois de decidido é bloqueado (definitiva).
    with pytest.raises(HTTPException) as excinfo:
        await service.decide_item(
            auth_context.workspace_id,
            run.id,
            item.id,
            decision=FinanceHarmonizationDecision.rejeitado,
            decided_by=auth_context.user_id,
        )
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_validate_run_is_idempotent_and_blocks_after_rejected(db_session, auth_context):
    service = FinanceHarmonizationService(db_session)
    run = await service.create_run(
        auth_context.workspace_id,
        filename="extrato-vazio.csv",
        content_type="text/csv",
        raw_bytes="date,value,description\n".encode("utf-8"),
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        created_by=auth_context.user_id,
    )

    validated = await service.validate_run(auth_context.workspace_id, run.id, decided_by=auth_context.user_id)
    assert validated.status == FinanceHarmonizationStatus.validado

    # Validar de novo é no-op idempotente.
    again = await service.validate_run(auth_context.workspace_id, run.id, decided_by=auth_context.user_id)
    assert again.status == FinanceHarmonizationStatus.validado

    with pytest.raises(HTTPException) as excinfo:
        await service.reject_run(auth_context.workspace_id, run.id, decided_by=auth_context.user_id)
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_quota_allows_base_plus_default_bonus_then_429(db_session, auth_context):
    service = FinanceHarmonizationService(db_session)
    total_allowed = BASE_MONTHLY_LIMIT + DEFAULT_BONUS
    assert total_allowed == 3

    for _ in range(total_allowed):
        await service.create_run(
            auth_context.workspace_id,
            filename="extrato-vazio.csv",
            content_type="text/csv",
            raw_bytes="date,value,description\n".encode("utf-8"),
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            created_by=auth_context.user_id,
        )

    with pytest.raises(HTTPException) as excinfo:
        await service.create_run(
            auth_context.workspace_id,
            filename="extrato-vazio-4.csv",
            content_type="text/csv",
            raw_bytes="date,value,description\n".encode("utf-8"),
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            created_by=auth_context.user_id,
        )
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail["code"] == "FIN-HARM-QUOTA-429"

    status_payload = await service.get_quota_status(auth_context.workspace_id, today=date(2026, 7, 20))
    assert status_payload["used"] == 3
    assert status_payload["limit"] == 3
    assert status_payload["remaining"] == 0
    assert status_payload["renewsAt"] == date(2026, 8, 1)


@pytest.mark.asyncio
async def test_admin_grant_bonus_extends_quota_for_that_competence(db_session, auth_context):
    service = FinanceHarmonizationService(db_session)

    await service.admin_grant_bonus(
        auth_context.workspace_id, competence="2026-07", extra=2, granted_by=auth_context.user_id
    )
    status_payload = await service.get_quota_status(auth_context.workspace_id, today=date(2026, 7, 5))
    assert status_payload["limit"] == BASE_MONTHLY_LIMIT + DEFAULT_BONUS + 2

    for _ in range(status_payload["limit"]):
        await service.create_run(
            auth_context.workspace_id,
            filename="extrato-vazio.csv",
            content_type="text/csv",
            raw_bytes="date,value,description\n".encode("utf-8"),
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            created_by=auth_context.user_id,
        )

    with pytest.raises(HTTPException) as excinfo:
        await service.create_run(
            auth_context.workspace_id,
            filename="extrato-vazio-extra.csv",
            content_type="text/csv",
            raw_bytes="date,value,description\n".encode("utf-8"),
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            created_by=auth_context.user_id,
        )
    assert excinfo.value.status_code == 429


@pytest.mark.asyncio
async def test_pdf_without_parser_creates_honest_run_without_inventing_rows(db_session, auth_context):
    service = FinanceHarmonizationService(db_session)
    run = await service.create_run(
        auth_context.workspace_id,
        filename="extrato.pdf",
        content_type="application/pdf",
        raw_bytes=b"%PDF-1.4 fake",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        created_by=auth_context.user_id,
    )
    assert run.parser_warning is not None
    assert all(item.match_kind == FinanceHarmonizationMatchKind.so_no_sistema for item in run.items) or not run.items
