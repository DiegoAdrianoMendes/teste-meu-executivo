"""S53: uma definição só de "vencida" para leitores de sugestão e saldo."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.client import Client
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.matter import Matter, PracticeArea
from app.services.finance_predicates import (
    OPEN_RECEIVABLE_STATUSES,
    finance_entry_overdue_predicate,
)
from app.services.whatsapp_picker_service import WhatsAppPickerService


async def _entry(db_session, workspace_id, matter_id, *, status, dias):
    entry = FinanceEntry(
        workspace_id=workspace_id,
        matter_id=matter_id,
        title=f"Parcela {uuid4().hex[:4]}",
        entry_type=FinanceEntryType.receivable,
        status=status,
        amount=100,
        due_date=datetime.now(UTC) + timedelta(days=dias),
        entry_metadata={},
    )
    db_session.add(entry)
    await db_session.flush()
    return entry


@pytest.mark.asyncio
async def test_pending_past_due_counts_as_overdue_and_paid_does_not(db_session, auth_context):
    client = Client(workspace_id=auth_context.workspace_id, name="Devedor")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.civel,
        demand_type="Cobrança",
    )
    db_session.add(matter)
    await db_session.flush()
    vencida_sem_evento = await _entry(
        db_session, auth_context.workspace_id, matter.id, status=FinanceEntryStatus.pending, dias=-1
    )
    ws = auth_context.workspace_id
    await _entry(db_session, ws, matter.id, status=FinanceEntryStatus.paid, dias=-5)
    await _entry(db_session, ws, matter.id, status=FinanceEntryStatus.pending, dias=+3)
    await _entry(db_session, ws, matter.id, status=FinanceEntryStatus.scheduled, dias=+9)
    await db_session.commit()

    now = datetime.now(UTC)
    vencidas = (
        await db_session.scalars(
            select(FinanceEntry.id).where(
                FinanceEntry.matter_id == matter.id, finance_entry_overdue_predicate(now)
            )
        )
    ).all()
    assert vencidas == [vencida_sem_evento.id]

    total = await db_session.scalar(
        select(func.count(FinanceEntry.id)).where(
            FinanceEntry.matter_id == matter.id, FinanceEntry.status.in_(OPEN_RECEIVABLE_STATUSES)
        )
    )
    assert total == 3, "pendente vencida, pendente futura e agendada contam como em aberto"
    saldo = await WhatsAppPickerService(db_session)._open_balance(ws, matter.id)
    assert saldo == "300.00"
