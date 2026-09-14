"""Predicados financeiros compartilhados (sem dependências de serviço).

S53: "vencida" tinha duas definições: `FinanceEntry.status == overdue` (só o
webhook do Asaas grava) e `due_date <= now` fora de pago/cancelado (régua de
cobrança). Um lançamento vencido sem evento do Asaas nunca sugeria cobrança.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, or_

from app.models.finance import FinanceEntry, FinanceEntryStatus

OPEN_RECEIVABLE_STATUSES = (
    FinanceEntryStatus.pending,
    FinanceEntryStatus.scheduled,
    FinanceEntryStatus.overdue,
)


def finance_entry_overdue_predicate(now: datetime):
    """Vencida = marcada `overdue` OU com vencimento no passado e ainda em aberto."""
    return or_(
        FinanceEntry.status == FinanceEntryStatus.overdue,
        and_(
            FinanceEntry.due_date.is_not(None),
            FinanceEntry.due_date <= now,
            FinanceEntry.status.notin_((FinanceEntryStatus.paid, FinanceEntryStatus.cancelled)),
        ),
    )
