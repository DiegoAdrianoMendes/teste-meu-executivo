"""Service: Painel diário da Controladoria jurídica (Onda 4, Plano Mestre §8).

Só dado agregado para o painel EXISTENTE — sem frontend novo nesta onda
(a UI de controladoria entra na Onda 8). Reusa integralmente o módulo
processual (`MonitoredProcess`/`ProcessPublication`,
app/models/process_monitoring.py) e a Central de Exceções (`LegalException`,
Onda 2) — NENHUM dos dois é recriado aqui, só agregado.

Toda métrica é UMA query agregada (func.count/func.max com GROUP BY,
quando cabível) — nunca um loop Python por item consultando o banco de
novo, para não introduzir N+1 conforme o volume de dados cresce (ver
tests/services/test_legal_controladoria_panel_service.py::
test_painel_query_count_is_constant_regardless_of_volume).

Interpretações de domínio assumidas (documentadas aqui para auditoria,
não infra fato novo):
  * "publicações não tratadas" = ProcessPublication cujo prazo_status
    ainda não chegou a um estado terminal humano (NULL ou
    'prazo_sugerido' — 'confirmado'/'dispensado' já foram tratados).
  * "processos sem movimentação" = MonitoredProcess ativo cujo
    last_movement_at é nulo ou mais antigo que STALE_PROCESS_DAYS.
  * "minutas bloqueadas" = LegalArtifactState com status
    'changes_requested' (mudança solicitada pelo revisor, minuta não
    segue até resolução).
  * "clientes sem atualização" = clientes com pelo menos um Matter cujo
    Matter mais recentemente tocado está mais antigo que
    STALE_CLIENT_DAYS.
  * "audiências próximas" = Meeting agendada (status=scheduled) nos
    próximos AUDIENCIA_PROXIMA_DAYS dias (o módulo não distingue
    audiência de outra reunião — mesma entidade reusada de
    app/models/meeting.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus
from app.models.klm_operations import LegalArtifactState
from app.models.legal_demand import LEGAL_DEMAND_TERMINAL_PHASES, LegalDemand
from app.models.legal_exception import ExceptionCenterStatus, LegalException
from app.models.matter import Matter
from app.models.meeting import Meeting, MeetingStatus
from app.models.process_monitoring import (
    DeadlineStatus,
    MonitoredProcess,
    MonitoredProcessStatus,
    ProcessPublication,
)

STALE_PROCESS_DAYS = 15
STALE_CLIENT_DAYS = 30
AUDIENCIA_PROXIMA_DAYS = 7


@dataclass(frozen=True)
class DeadlineWindowCounts:
    d7: int
    d3: int
    d1: int
    d0: int
    atrasados: int


@dataclass(frozen=True)
class ControladoriaPainel:
    generated_at: datetime
    publicacoes_nao_tratadas: int
    prazos_sugeridos: int
    prazos_fatais: int
    prazos_por_janela: DeadlineWindowCounts
    tarefas_sem_responsavel: int
    processos_sem_movimentacao: int
    audiencias_proximas: int
    documentos_pendentes: int
    minutas_bloqueadas: int
    excecoes_de_agentes: int
    clientes_sem_atualizacao: int


class LegalControladoriaPanelService:
    def __init__(self, db: AsyncSession, workspace_id: UUID) -> None:
        if workspace_id is None:
            raise ValueError("workspace_id is required")
        self.db = db
        self.workspace_id = workspace_id

    async def get_panel(self, *, now: datetime | None = None) -> ControladoriaPainel:
        instant = now or datetime.now(UTC)
        today = instant.date()

        publicacoes_nao_tratadas = await self._count(
            select(func.count(ProcessPublication.id)).where(
                ProcessPublication.workspace_id == self.workspace_id,
                or_(
                    ProcessPublication.prazo_status.is_(None),
                    ProcessPublication.prazo_status == DeadlineStatus.prazo_sugerido,
                ),
            )
        )
        prazos_sugeridos = await self._count(
            select(func.count(ProcessPublication.id)).where(
                ProcessPublication.workspace_id == self.workspace_id,
                ProcessPublication.prazo_status == DeadlineStatus.prazo_sugerido,
            )
        )
        prazos_fatais = await self._count(
            select(func.count(ProcessPublication.id)).where(
                ProcessPublication.workspace_id == self.workspace_id,
                ProcessPublication.prazo_status == DeadlineStatus.confirmado,
            )
        )
        prazos_por_janela = await self._deadline_window_counts(today)

        tarefas_sem_responsavel = await self._count(
            select(func.count(LegalDemand.id)).where(
                LegalDemand.workspace_id == self.workspace_id,
                LegalDemand.responsible_user_id.is_(None),
                LegalDemand.phase.notin_(list(LEGAL_DEMAND_TERMINAL_PHASES)),
            )
        )

        stale_process_cutoff = instant - timedelta(days=STALE_PROCESS_DAYS)
        processos_sem_movimentacao = await self._count(
            select(func.count(MonitoredProcess.id)).where(
                MonitoredProcess.workspace_id == self.workspace_id,
                MonitoredProcess.status == MonitoredProcessStatus.ativo,
                or_(
                    MonitoredProcess.last_movement_at.is_(None),
                    MonitoredProcess.last_movement_at < stale_process_cutoff,
                ),
            )
        )

        audiencias_proximas = await self._count(
            select(func.count(Meeting.id)).where(
                Meeting.workspace_id == self.workspace_id,
                Meeting.status == MeetingStatus.scheduled,
                Meeting.starts_at >= instant,
                Meeting.starts_at <= instant + timedelta(days=AUDIENCIA_PROXIMA_DAYS),
            )
        )

        documentos_pendentes = await self._count(
            select(func.count(Document.id))
            .join(Matter, Matter.id == Document.matter_id)
            .where(
                Matter.workspace_id == self.workspace_id,
                Matter.deleted_at.is_(None),
                Document.status.in_([DocumentStatus.aguardando, DocumentStatus.parcial]),
            )
        )

        minutas_bloqueadas = await self._count(
            select(func.count(LegalArtifactState.id)).where(
                LegalArtifactState.workspace_id == self.workspace_id,
                LegalArtifactState.status == "changes_requested",
            )
        )

        excecoes_de_agentes = await self._count(
            select(func.count(LegalException.id)).where(
                LegalException.workspace_id == self.workspace_id,
                LegalException.status == ExceptionCenterStatus.aberta,
            )
        )

        clientes_sem_atualizacao = await self._stale_client_count(instant)

        return ControladoriaPainel(
            generated_at=instant,
            publicacoes_nao_tratadas=publicacoes_nao_tratadas,
            prazos_sugeridos=prazos_sugeridos,
            prazos_fatais=prazos_fatais,
            prazos_por_janela=prazos_por_janela,
            tarefas_sem_responsavel=tarefas_sem_responsavel,
            processos_sem_movimentacao=processos_sem_movimentacao,
            audiencias_proximas=audiencias_proximas,
            documentos_pendentes=documentos_pendentes,
            minutas_bloqueadas=minutas_bloqueadas,
            excecoes_de_agentes=excecoes_de_agentes,
            clientes_sem_atualizacao=clientes_sem_atualizacao,
        )

    async def _deadline_window_counts(self, today: date) -> DeadlineWindowCounts:
        result = await self.db.execute(
            select(ProcessPublication.data_fatal).where(
                ProcessPublication.workspace_id == self.workspace_id,
                ProcessPublication.prazo_status == DeadlineStatus.confirmado,
                ProcessPublication.data_fatal.is_not(None),
            )
        )
        counts = {"d7": 0, "d3": 0, "d1": 0, "d0": 0, "atrasados": 0}
        for (data_fatal,) in result.all():
            remaining = (data_fatal - today).days
            if remaining < 0:
                counts["atrasados"] += 1
            elif remaining == 0:
                counts["d0"] += 1
            elif remaining <= 1:
                counts["d1"] += 1
            elif remaining <= 3:
                counts["d3"] += 1
            elif remaining <= 7:
                counts["d7"] += 1
        return DeadlineWindowCounts(**counts)

    async def _stale_client_count(self, instant: datetime) -> int:
        cutoff = instant - timedelta(days=STALE_CLIENT_DAYS)
        subquery = (
            select(
                Matter.client_id.label("client_id"),
                func.max(Matter.updated_at).label("last_touch"),
            )
            .where(Matter.workspace_id == self.workspace_id, Matter.deleted_at.is_(None))
            .group_by(Matter.client_id)
            .subquery()
        )
        result = await self.db.execute(
            select(func.count()).select_from(subquery).where(subquery.c.last_touch < cutoff)
        )
        return int(result.scalar_one() or 0)

    async def _count(self, statement) -> int:
        result = await self.db.execute(statement)
        return int(result.scalar_one() or 0)
