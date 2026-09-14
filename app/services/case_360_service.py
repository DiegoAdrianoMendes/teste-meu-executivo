"""
Service: Case360Service — Caso 360 canônico (Onda 5, Plano Mestre §5).

`LegalDemand` (Onda 2) já é a demanda canônica que unifica as 5 naturezas
(judicial/administrativo/consultivo/contrato/massificado). Este serviço só
AGREGA — numa única passada, sem N+1 — a visão 360 de UMA demanda: não
recria nenhum domínio já existente, só consulta:

  * `Client` (cliente titular) e `CaseParty` (partes de primeira classe,
    Onda 5) — sem perder o resumo legado em `LegalDemand.parties`;
  * `Matter` (quando a demanda tem operação Kanban vinculada) + o bloco
    'oportunidade' (CRM/funil) SEPARADO do bloco 'demanda contratada'
    (dados jurídicos da própria `LegalDemand`) — a distinção que o Plano
    Mestre §5 exige nunca se perde;
  * `MonitoredProcess`/`ProcessPublication` (módulo processual, Onda 4) —
    processo(s) vinculado(s) e estado da jornada de cada publicação;
  * as camadas de SLA já persistidas em `LegalDemand` (Onda 4) + a última
    `DistributionDecision` + `SlaEscalationLog` ainda não confirmados
    (trabalho distribuído + SLA);
  * `Document` vinculados ao `Matter` da demanda;
  * `FinanceEntry`/`FeeArrangement` filtrados por `matter_id` — SUBLEDGER
    real do caso, nunca o financeiro do workspace inteiro;
  * `Conversation` do `Matter` (conversa canônica vinculada);
  * `CaseStrategyService.get_matrix` (Onda 5 — matriz fato-prova-pedido) —
    REUSADA como o bloco "provas", nenhuma tabela de evidência nova aqui;
  * `LegalException` abertas do `Matter` (Central de Exceções, Onda 2);
  * `TimelineEvent` do `Matter`.

Naturezas sem operação comercial/processual (consultivo, contrato sem
Matter vinculado) não quebram: todo bloco que depende de `Matter` volta
vazio/None honestamente (nunca fabrica dado) quando `demand.matter_id` é
`None` — ver `test_case_360_service.py::test_case_360_natureza_sem_matter_...`.

Cada bloco é UMA query (ou uma query com IN batelado, nunca um loop Python
reconsultando o banco por item) — o número de statements SQL não pode
crescer com o volume de itens agregados (ver
tests/services/test_case_360_service.py::
test_case_360_query_count_is_constant_regardless_of_volume, mesmo racional
de LegalControladoriaPanelService).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case_party import CaseParty, CasePartyPole
from app.models.client import Client
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.fee_arrangement import FeeArrangement
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.legal_controladoria import DistributionDecision, SlaEscalationLog
from app.models.legal_demand import LegalDemand
from app.models.legal_exception import ExceptionCenterStatus, LegalException
from app.models.matter import Matter
from app.models.process_monitoring import MonitoredProcess, ProcessPublication
from app.models.timeline_event import TimelineEvent
from app.models.user import User
from app.services.case_strategy_service import CaseStrategyMatrix, CaseStrategyService

RECENT_PUBLICATIONS_LIMIT = 30
RECENT_DOCUMENTS_LIMIT = 50
RECENT_CONVERSATIONS_LIMIT = 20
RECENT_TIMELINE_LIMIT = 50


class Case360NotFoundError(HTTPException):
    def __init__(self, detail: str = "Demanda jurídica não encontrada.") -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


@dataclass(frozen=True)
class FinanceTotals:
    total_receivable: Decimal
    total_paid: Decimal
    total_pending: Decimal
    total_overdue: Decimal


@dataclass(frozen=True)
class Case360Result:
    """Bundle read-model — a montagem final em schema fica no endpoint
    (mesmo padrão de `ControladoriaPainelRead`/`LegalControladoriaPanelService`:
    o serviço devolve dado bruto agregado, o endpoint mapeia para o schema)."""

    generated_at: datetime
    demand: LegalDemand
    client: Client
    responsible_user: Optional[User]
    matter: Optional[Matter]
    # ── Partes (bloco 'partes') ──
    case_parties: list[CaseParty]
    # ── Processo(s) + jornada ──
    processes: list[MonitoredProcess]
    publications_by_process: dict[UUID, list[ProcessPublication]]
    # ── Trabalho distribuído + SLA ──
    distribution_last_decision: Optional[DistributionDecision]
    open_sla_escalations: list[SlaEscalationLog]
    # ── Documentos ──
    documents: list[Document]
    # ── Financeiro (subledger real do caso) ──
    finance_entries: list[FinanceEntry]
    finance_totals: FinanceTotals
    fee_arrangements: list[FeeArrangement]
    # ── Comunicações ──
    conversations_recent: list[Conversation]
    conversations_total: int
    # ── Provas (reusa a matriz fato-prova-pedido, Onda 5) ──
    strategy_matrix: CaseStrategyMatrix
    # ── Exceções abertas do caso ──
    open_exceptions: list[LegalException]
    # ── Timeline ──
    timeline: list[TimelineEvent]


_ZERO = Decimal("0")


class Case360Service:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _require_demand(self, workspace_id: UUID, demand_id: UUID) -> LegalDemand:
        demand = await self.db.scalar(
            select(LegalDemand).where(
                LegalDemand.id == demand_id,
                LegalDemand.workspace_id == workspace_id,
            )
        )
        if demand is None:
            raise Case360NotFoundError()
        return demand

    async def get_case_360(self, workspace_id: UUID, demand_id: UUID) -> Case360Result:
        instant = datetime.now(UTC)
        demand = await self._require_demand(workspace_id, demand_id)

        client = await self.db.scalar(select(Client).where(Client.id == demand.client_id))
        if client is None:
            # LegalDemand.client_id é RESTRICT + obrigatório — só falha aqui
            # se o dado estiver corrompido; nunca fabrica um cliente vazio.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Cliente titular da demanda não encontrado — dado inconsistente.",
            )

        responsible_user: Optional[User] = None
        if demand.responsible_user_id is not None:
            responsible_user = await self.db.scalar(
                select(User).where(User.id == demand.responsible_user_id)
            )

        matter: Optional[Matter] = None
        if demand.matter_id is not None:
            matter = await self.db.scalar(
                select(Matter).where(
                    Matter.id == demand.matter_id,
                    Matter.workspace_id == workspace_id,
                    Matter.deleted_at.is_(None),
                )
            )

        case_parties = list(
            await self.db.scalars(
                select(CaseParty)
                .where(
                    CaseParty.legal_demand_id == demand.id,
                    CaseParty.workspace_id == workspace_id,
                )
                .order_by(CaseParty.created_at.asc())
            )
        )

        processes: list[MonitoredProcess] = []
        publications_by_process: dict[UUID, list[ProcessPublication]] = {}
        if matter is not None:
            processes = list(
                await self.db.scalars(
                    select(MonitoredProcess)
                    .where(
                        MonitoredProcess.workspace_id == workspace_id,
                        MonitoredProcess.matter_id == matter.id,
                    )
                    .order_by(MonitoredProcess.created_at.asc())
                )
            )
            process_ids = [process.id for process in processes]
            if process_ids:
                publications = list(
                    await self.db.scalars(
                        select(ProcessPublication)
                        .where(
                            ProcessPublication.workspace_id == workspace_id,
                            ProcessPublication.monitored_process_id.in_(process_ids),
                        )
                        .order_by(ProcessPublication.published_at.desc())
                        .limit(RECENT_PUBLICATIONS_LIMIT)
                    )
                )
                publications_by_process = {process_id: [] for process_id in process_ids}
                for publication in publications:
                    if publication.monitored_process_id in publications_by_process:
                        publications_by_process[publication.monitored_process_id].append(
                            publication
                        )

        distribution_last_decision = await self.db.scalar(
            select(DistributionDecision)
            .where(
                DistributionDecision.workspace_id == workspace_id,
                DistributionDecision.legal_demand_id == demand.id,
            )
            .order_by(DistributionDecision.decided_at.desc())
            .limit(1)
        )
        open_sla_escalations = list(
            await self.db.scalars(
                select(SlaEscalationLog)
                .where(
                    SlaEscalationLog.workspace_id == workspace_id,
                    SlaEscalationLog.legal_demand_id == demand.id,
                    SlaEscalationLog.acknowledged_at.is_(None),
                )
                .order_by(SlaEscalationLog.due_at.asc())
            )
        )

        documents: list[Document] = []
        finance_entries: list[FinanceEntry] = []
        finance_totals = FinanceTotals(
            total_receivable=_ZERO, total_paid=_ZERO, total_pending=_ZERO, total_overdue=_ZERO
        )
        fee_arrangements: list[FeeArrangement] = []
        conversations_recent: list[Conversation] = []
        conversations_total = 0
        open_exceptions: list[LegalException] = []
        timeline: list[TimelineEvent] = []

        if matter is not None:
            documents = list(
                await self.db.scalars(
                    select(Document)
                    .where(Document.matter_id == matter.id)
                    .order_by(Document.created_at.desc())
                    .limit(RECENT_DOCUMENTS_LIMIT)
                )
            )

            finance_filter = FinanceEntry.matter_id == matter.id
            if demand.finance_ref is not None:
                finance_filter = or_(finance_filter, FinanceEntry.id == demand.finance_ref)
            finance_entries = list(
                await self.db.scalars(
                    select(FinanceEntry)
                    .where(
                        FinanceEntry.workspace_id == workspace_id,
                        FinanceEntry.deleted_at.is_(None),
                        finance_filter,
                    )
                    .order_by(FinanceEntry.due_date.asc().nullslast())
                )
            )
            finance_totals = await self._finance_totals(workspace_id, matter.id)

            fee_arrangements = list(
                await self.db.scalars(
                    select(FeeArrangement).where(
                        FeeArrangement.workspace_id == workspace_id,
                        FeeArrangement.matter_id == matter.id,
                    )
                )
            )

            conversations_recent = list(
                await self.db.scalars(
                    select(Conversation)
                    .where(Conversation.matter_id == matter.id)
                    .order_by(Conversation.timestamp.desc())
                    .limit(RECENT_CONVERSATIONS_LIMIT)
                )
            )
            conversations_total = int(
                await self.db.scalar(
                    select(func.count(Conversation.id)).where(
                        Conversation.matter_id == matter.id
                    )
                )
                or 0
            )

            open_exceptions = list(
                await self.db.scalars(
                    select(LegalException)
                    .where(
                        LegalException.workspace_id == workspace_id,
                        LegalException.matter_id == matter.id,
                        LegalException.status == ExceptionCenterStatus.aberta,
                    )
                    .order_by(LegalException.created_at.desc())
                )
            )

            timeline = list(
                await self.db.scalars(
                    select(TimelineEvent)
                    .where(TimelineEvent.matter_id == matter.id)
                    .order_by(TimelineEvent.timestamp.desc())
                    .limit(RECENT_TIMELINE_LIMIT)
                )
            )

        strategy_matrix = await CaseStrategyService(self.db).get_matrix(workspace_id, demand.id)

        return Case360Result(
            generated_at=instant,
            demand=demand,
            client=client,
            responsible_user=responsible_user,
            matter=matter,
            case_parties=case_parties,
            processes=processes,
            publications_by_process=publications_by_process,
            distribution_last_decision=distribution_last_decision,
            open_sla_escalations=open_sla_escalations,
            documents=documents,
            finance_entries=finance_entries,
            finance_totals=finance_totals,
            fee_arrangements=fee_arrangements,
            conversations_recent=conversations_recent,
            conversations_total=conversations_total,
            strategy_matrix=strategy_matrix,
            open_exceptions=open_exceptions,
            timeline=timeline,
        )

    async def _finance_totals(self, workspace_id: UUID, matter_id: UUID) -> FinanceTotals:
        result = await self.db.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                FinanceEntry.entry_type == FinanceEntryType.receivable,
                                FinanceEntry.amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (FinanceEntry.status == FinanceEntryStatus.paid, FinanceEntry.amount),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                FinanceEntry.status.in_(
                                    (FinanceEntryStatus.pending, FinanceEntryStatus.scheduled)
                                ),
                                FinanceEntry.amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                FinanceEntry.status == FinanceEntryStatus.overdue,
                                FinanceEntry.amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ).where(
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.matter_id == matter_id,
                FinanceEntry.deleted_at.is_(None),
            )
        )
        receivable, paid, pending, overdue = result.one()
        return FinanceTotals(
            total_receivable=Decimal(str(receivable)),
            total_paid=Decimal(str(paid)),
            total_pending=Decimal(str(pending)),
            total_overdue=Decimal(str(overdue)),
        )

    # ── Partes de primeira classe (bloco 'partes') ───────────────────────

    async def add_party(
        self,
        workspace_id: UUID,
        demand_id: UUID,
        *,
        name: str,
        pole: CasePartyPole,
        role: Optional[str] = None,
        qualification: Optional[dict] = None,
        notes: Optional[str] = None,
    ) -> CaseParty:
        await self._require_demand(workspace_id, demand_id)
        party = CaseParty(
            workspace_id=workspace_id,
            legal_demand_id=demand_id,
            name=name,
            pole=pole,
            role=role,
            qualification=qualification or {},
            notes=notes,
        )
        self.db.add(party)
        await self.db.flush()
        return party

    async def list_parties(self, workspace_id: UUID, demand_id: UUID) -> list[CaseParty]:
        await self._require_demand(workspace_id, demand_id)
        rows = await self.db.scalars(
            select(CaseParty)
            .where(
                CaseParty.legal_demand_id == demand_id,
                CaseParty.workspace_id == workspace_id,
            )
            .order_by(CaseParty.created_at.asc())
        )
        return list(rows.all())
