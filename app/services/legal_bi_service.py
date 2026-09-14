"""Service: BI jurídico — indicadores de OPERAÇÃO, JURÍDICO e IA (Onda 8,
Plano Mestre §15).

Só leitura agregada. NÃO cria tabela nova: todo indicador deriva de
entidades já existentes e já auditadas nas ondas anteriores —
`LegalDemand`/`LEGAL_DEMAND_TERMINAL_PHASES` (Onda 2), a jornada
publicação→pasta e `KlmAuditEvent` (Onda 4, `legal_journey_service.py`),
`LegalArtifactVersion`/`LegalArtifactState` (Onda 6), `LegalException`
(Onda 2) e `AgentRun`/`HumanOverride` (governança de agentes). Nenhum
campo novo, nenhuma coluna editável pelo usuário entra como métrica —
toda contagem parte de um evento real (transição de estado, execução de
agente, decisão humana) ou de uma coluna que já é fato de domínio (fase,
risco, valor), nunca de um rótulo livre que o usuário poderia inflar.

Regra dura (Plano Mestre §15), aplicada em TODOS os três blocos:
  * NUNCA apresentar causalidade onde há só correlação — os nomes dos
    campos descrevem o que foi contado, não por que aconteceu;
  * SEMPRE expor corte temporal (`period_from`/`period_to`), amostra
    (`sample_size`) e origem (`origin`) via `BiHonesty`, devolvida junto
    de cada bloco;
  * quando o dado pedido não existe de forma confiável no domínio (ex.:
    "tempo economizado" e "rollback" de agente — nenhum modelo grava
    isso hoje), o serviço NUNCA fabrica um número: declara o campo em
    `missing_fields` e não devolve `None` disfarçado de zero.

Interpretações de domínio assumidas (documentadas aqui para auditoria,
mesmo padrão de `legal_controladoria_panel_service.py`):
  * "tempo publicação→X" = média/mediana, em horas, entre
    `ProcessPublication.created_at` (captura) e o `KlmAuditEvent`
    (`event_type='legal_journey_advanced'`) cujo `correlation_id`
    termina em `->X` — a jornada é a ÚNICA porta de avanço
    (`legal_journey_service.py`), então esse evento é o carimbo real da
    transição, nunca um campo editável;
  * "retrabalho" = contagem do único ciclo nomeado no grafo da jornada
    (`correcoes_pendentes -> minuta_em_producao`, ver
    `VALID_TRANSITIONS`), não uma inferência sobre volume de edição;
  * "tarefas D-1/D0/atrasadas" fazem o parse de `Task.due_date` (string
    livre, não validada em `YYYY-MM-DD`) só quando o formato é válido;
    o restante entra em `tarefas_sem_data_classificavel` — nunca é
    descartado silenciosamente;
  * "duração" de uma demanda jurídica usa `LegalDemand.updated_at` do
    registro já em fase terminal como proxy da data de saída (o
    `TimestampMixin` toca `updated_at` na própria transição de fase,
    ver `LegalDemandService.transition_phase`); se o registro for
    tocado de novo por outro motivo após o encerramento, a duração para
    de refletir a saída real — por isso é sempre reportada como
    aproximação, nunca como fato exato;
  * "resultado" (ganho/perdido) só existe quando a demanda tem
    `matter_id` vinculado — lido de `Matter.column_id`
    (`KanbanColumnId.ganho`/`perdido`). `LegalDemand` isolada (sem
    Matter) não tem essa taxonomia; a contagem correspondente entra em
    `sem_matter_vinculado`, nunca é forçada em "perdido" por omissão;
  * "passos automatizados"/"aceitação"/"edição"/"bloqueios" de IA
    mapeiam literalmente para `AgentRun.status` e
    `HumanOverride.decision` — "abstention" é rotulado explicitamente
    como proxy de `AgentRunStatus.waiting_human` (o agente parou e pediu
    humano), nunca apresentado como sinônimo exato do termo do produto.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import median
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.client import Client
from app.models.human_override import HumanOverride, HumanOverrideDecision
from app.models.klm_operations import KlmAuditEvent, LegalArtifactVersion
from app.models.legal_demand import (
    LEGAL_DEMAND_TERMINAL_PHASES,
    LegalDemand,
    LegalDemandNature,
)
from app.models.legal_exception import ExceptionCenterStatus, LegalException
from app.models.matter import Matter
from app.models.process_monitoring import (
    MonitoredProcess,
    MonitoredProcessStatus,
    ProcessPublication,
)
from app.models.task import Task, TaskStatus

DEFAULT_WINDOW_DAYS = 30
STALE_PROCESS_DAYS = 15  # mesmo corte de legal_controladoria_panel_service.py

_JOURNEY_MILESTONES: tuple[str, ...] = (
    "triagem_concluida",
    "prazo_confirmado",
    "trabalho_distribuido",
)


def _default_window(now: datetime) -> tuple[datetime, datetime]:
    return now - timedelta(days=DEFAULT_WINDOW_DAYS), now


@dataclass(frozen=True)
class BiHonesty:
    """Metadado de honestidade obrigatório (Plano Mestre §15) — vai junto
    de todo bloco de indicadores devolvido por este serviço."""

    generated_at: datetime
    period_from: datetime
    period_to: datetime
    sample_size: int
    origin: str
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class JourneyTimingStat:
    to_state: str
    sample_size: int
    avg_hours: float | None
    median_hours: float | None


@dataclass(frozen=True)
class CountRow:
    key: str
    count: int


@dataclass(frozen=True)
class AmountRow:
    key: str
    total_value: float
    count: int


@dataclass(frozen=True)
class ClientSlaRow:
    client_id: str
    client_name: str
    no_prazo: int
    atrasado: int
    sem_sla_definido: int


@dataclass(frozen=True)
class NatureDurationRow:
    nature: str
    sample_size: int
    avg_days: float


@dataclass(frozen=True)
class OperationBi:
    honesty: BiHonesty
    tempo_por_marco: tuple[JourneyTimingStat, ...]
    tarefas_d0: int
    tarefas_d1: int
    tarefas_atrasadas: int
    tarefas_sem_data_classificavel: int
    tarefas_sem_responsavel: int
    retrabalho_correcoes_pendentes: int
    minutas_com_multiplas_versoes: int
    processos_sem_movimentacao: int
    backlog_por_responsavel: tuple[CountRow, ...]
    sla_por_cliente: tuple[ClientSlaRow, ...]
    excecoes_por_origem: tuple[CountRow, ...]


@dataclass(frozen=True)
class LegalBi:
    honesty: BiHonesty
    estoque_por_fase: tuple[CountRow, ...]
    entradas_no_periodo: int
    saidas_no_periodo: int
    duracao_media_dias_por_natureza: tuple[NatureDurationRow, ...]
    valores_por_fase: tuple[AmountRow, ...]
    contingencia_por_risco: tuple[AmountRow, ...]
    acordos_no_periodo: int
    demandas_com_estrategia_registrada: int
    resultado_por_coluna_kanban: tuple[CountRow, ...]
    resultado_sem_matter_vinculado: int
    motivo_encerramento_registrado: int


@dataclass(frozen=True)
class AiBi:
    honesty: BiHonesty
    passos_automatizados_por_agente: tuple[CountRow, ...]
    execucoes_totais: int
    execucoes_falhas: int
    execucoes_aguardando_humano: int
    intervencoes_humanas_total: int
    taxa_intervencao_humana: float | None
    decisoes_aceitas: int
    decisoes_corrigidas: int
    decisoes_rejeitadas: int
    custo_usd_por_agente: tuple[AmountRow, ...]
    tokens_input_totais: int
    tokens_output_totais: int


class LegalBiService:
    def __init__(self, db: AsyncSession, workspace_id: UUID) -> None:
        if workspace_id is None:
            raise ValueError("workspace_id is required")
        self.db = db
        self.workspace_id = workspace_id

    # ── OPERAÇÃO ─────────────────────────────────────────────────────────

    async def get_operation_bi(
        self,
        *,
        period_from: datetime | None = None,
        period_to: datetime | None = None,
        now: datetime | None = None,
    ) -> OperationBi:
        instant = now or datetime.now(UTC)
        default_from, default_to = _default_window(instant)
        since = period_from or default_from
        until = period_to or default_to

        tempo_por_marco = tuple(
            [await self._journey_timing(state, since, until) for state in _JOURNEY_MILESTONES]
        )
        (
            tarefas_d0,
            tarefas_d1,
            tarefas_atrasadas,
            tarefas_sem_data,
            tarefas_sem_responsavel,
        ) = await self._task_deadline_counts(instant)
        retrabalho = await self._count_journey_rework(since, until)
        minutas_retrabalhadas = await self._count_multi_version_artifacts(since, until)
        processos_estagnados = await self._count_stagnant_processes(instant)
        backlog = await self._backlog_por_responsavel()
        sla_por_cliente = await self._sla_por_cliente()
        excecoes_por_origem = await self._excecoes_por_origem(since, until)

        sample_size = (
            tarefas_d0
            + tarefas_d1
            + tarefas_atrasadas
            + tarefas_sem_data
            + sum(row.count for row in backlog)
        )

        honesty = BiHonesty(
            generated_at=instant,
            period_from=since,
            period_to=until,
            sample_size=sample_size,
            origin=(
                "process_publications+klm_audit_events (jornada), tasks+matters, "
                "legal_demands, legal_exceptions, monitored_processes"
            ),
            missing_fields=(
                "tarefas_sem_data_classificavel — Task.due_date é texto livre não "
                "validado; datas fora de YYYY-MM-DD não entram nas janelas D0/D1/atrasadas",
            ),
        )
        return OperationBi(
            honesty=honesty,
            tempo_por_marco=tempo_por_marco,
            tarefas_d0=tarefas_d0,
            tarefas_d1=tarefas_d1,
            tarefas_atrasadas=tarefas_atrasadas,
            tarefas_sem_data_classificavel=tarefas_sem_data,
            tarefas_sem_responsavel=tarefas_sem_responsavel,
            retrabalho_correcoes_pendentes=retrabalho,
            minutas_com_multiplas_versoes=minutas_retrabalhadas,
            processos_sem_movimentacao=processos_estagnados,
            backlog_por_responsavel=backlog,
            sla_por_cliente=sla_por_cliente,
            excecoes_por_origem=excecoes_por_origem,
        )

    async def _journey_timing(
        self, to_state: str, since: datetime, until: datetime
    ) -> JourneyTimingStat:
        result = await self.db.execute(
            select(KlmAuditEvent.occurred_at, ProcessPublication.created_at)
            .select_from(KlmAuditEvent)
            .join(ProcessPublication, ProcessPublication.id == KlmAuditEvent.entity_id)
            .where(
                KlmAuditEvent.workspace_id == self.workspace_id,
                KlmAuditEvent.entity_type == "process_publication",
                KlmAuditEvent.event_type == "legal_journey_advanced",
                KlmAuditEvent.correlation_id.like(f"%->{to_state}"),
                KlmAuditEvent.occurred_at >= since,
                KlmAuditEvent.occurred_at <= until,
            )
        )
        deltas_hours: list[float] = []
        for occurred_at, published_created_at in result.all():
            if occurred_at is None or published_created_at is None:
                continue
            delta = (occurred_at - published_created_at).total_seconds() / 3600.0
            if delta >= 0:
                deltas_hours.append(delta)
        if not deltas_hours:
            return JourneyTimingStat(
                to_state=to_state, sample_size=0, avg_hours=None, median_hours=None
            )
        return JourneyTimingStat(
            to_state=to_state,
            sample_size=len(deltas_hours),
            avg_hours=round(sum(deltas_hours) / len(deltas_hours), 2),
            median_hours=round(median(deltas_hours), 2),
        )

    async def _task_deadline_counts(self, instant: datetime) -> tuple[int, int, int, int, int]:
        today = instant.date()
        tomorrow = today + timedelta(days=1)
        result = await self.db.execute(
            select(Task.due_date, Task.assigned_to)
            .select_from(Task)
            .join(Matter, Matter.id == Task.matter_id)
            .where(
                Matter.workspace_id == self.workspace_id,
                Matter.deleted_at.is_(None),
                Task.status != TaskStatus.done,
            )
        )
        d0 = d1 = atrasadas = sem_data = sem_responsavel = 0
        for due_date_raw, assigned_to in result.all():
            if assigned_to is None:
                sem_responsavel += 1
            parsed: date | None = None
            if due_date_raw:
                try:
                    parsed = date.fromisoformat(due_date_raw.strip()[:10])
                except (ValueError, AttributeError):
                    parsed = None
            if parsed is None:
                sem_data += 1
                continue
            if parsed < today:
                atrasadas += 1
            elif parsed == today:
                d0 += 1
            elif parsed == tomorrow:
                d1 += 1
        return d0, d1, atrasadas, sem_data, sem_responsavel

    async def _count_journey_rework(self, since: datetime, until: datetime) -> int:
        result = await self.db.execute(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.workspace_id == self.workspace_id,
                KlmAuditEvent.entity_type == "process_publication",
                KlmAuditEvent.event_type == "legal_journey_advanced",
                KlmAuditEvent.correlation_id.like("correcoes_pendentes->%"),
                KlmAuditEvent.occurred_at >= since,
                KlmAuditEvent.occurred_at <= until,
            )
        )
        return int(result.scalar_one() or 0)

    async def _count_multi_version_artifacts(self, since: datetime, until: datetime) -> int:
        subquery = (
            select(
                LegalArtifactVersion.artifact_key,
                func.count(LegalArtifactVersion.id).label("version_count"),
            )
            .where(
                LegalArtifactVersion.workspace_id == self.workspace_id,
                LegalArtifactVersion.created_at >= since,
                LegalArtifactVersion.created_at <= until,
            )
            .group_by(LegalArtifactVersion.artifact_key)
            .subquery()
        )
        result = await self.db.execute(
            select(func.count()).select_from(subquery).where(subquery.c.version_count > 1)
        )
        return int(result.scalar_one() or 0)

    async def _count_stagnant_processes(self, instant: datetime) -> int:
        cutoff = instant - timedelta(days=STALE_PROCESS_DAYS)
        result = await self.db.execute(
            select(func.count(MonitoredProcess.id)).where(
                MonitoredProcess.workspace_id == self.workspace_id,
                MonitoredProcess.status == MonitoredProcessStatus.ativo,
                or_(
                    MonitoredProcess.last_movement_at.is_(None),
                    MonitoredProcess.last_movement_at < cutoff,
                ),
            )
        )
        return int(result.scalar_one() or 0)

    async def _backlog_por_responsavel(self) -> tuple[CountRow, ...]:
        result = await self.db.execute(
            select(LegalDemand.responsible_user_id, func.count(LegalDemand.id))
            .where(
                LegalDemand.workspace_id == self.workspace_id,
                LegalDemand.phase.notin_(list(LEGAL_DEMAND_TERMINAL_PHASES)),
            )
            .group_by(LegalDemand.responsible_user_id)
        )
        rows = []
        for responsible_id, count in result.all():
            key = str(responsible_id) if responsible_id else "sem_responsavel"
            rows.append(CountRow(key=key, count=int(count)))
        return tuple(sorted(rows, key=lambda row: -row.count))

    async def _sla_por_cliente(self) -> tuple[ClientSlaRow, ...]:
        result = await self.db.execute(
            select(
                LegalDemand.client_id,
                Client.name,
                LegalDemand.sla_effective_at,
            )
            .join(Client, Client.id == LegalDemand.client_id)
            .where(
                LegalDemand.workspace_id == self.workspace_id,
                LegalDemand.phase.notin_(list(LEGAL_DEMAND_TERMINAL_PHASES)),
            )
        )
        now = datetime.now(UTC)
        buckets: dict[str, dict] = {}
        for client_id, client_name, sla_effective_at in result.all():
            key = str(client_id)
            bucket = buckets.setdefault(
                key,
                {"client_name": client_name, "no_prazo": 0, "atrasado": 0, "sem_sla_definido": 0},
            )
            if sla_effective_at is None:
                bucket["sem_sla_definido"] += 1
            elif sla_effective_at >= now:
                bucket["no_prazo"] += 1
            else:
                bucket["atrasado"] += 1
        rows = [
            ClientSlaRow(
                client_id=client_id,
                client_name=payload["client_name"],
                no_prazo=payload["no_prazo"],
                atrasado=payload["atrasado"],
                sem_sla_definido=payload["sem_sla_definido"],
            )
            for client_id, payload in buckets.items()
        ]
        return tuple(sorted(rows, key=lambda row: -row.atrasado))

    async def _excecoes_por_origem(self, since: datetime, until: datetime) -> tuple[CountRow, ...]:
        result = await self.db.execute(
            select(LegalException.origin, func.count(LegalException.id))
            .where(
                LegalException.workspace_id == self.workspace_id,
                LegalException.status == ExceptionCenterStatus.aberta,
                LegalException.created_at >= since,
                LegalException.created_at <= until,
            )
            .group_by(LegalException.origin)
        )
        rows = [CountRow(key=origin, count=int(count)) for origin, count in result.all()]
        return tuple(sorted(rows, key=lambda row: -row.count))

    # ── JURÍDICO ─────────────────────────────────────────────────────────

    async def get_legal_bi(
        self,
        *,
        period_from: datetime | None = None,
        period_to: datetime | None = None,
        now: datetime | None = None,
    ) -> LegalBi:
        instant = now or datetime.now(UTC)
        default_from, default_to = _default_window(instant)
        since = period_from or default_from
        until = period_to or default_to

        estoque = await self._estoque_por_fase()
        entradas = await self._count_entradas(since, until)
        saidas = await self._count_saidas(since, until)
        duracao = await self._duracao_media_por_natureza()
        valores = await self._valores_por_fase()
        contingencia = await self._contingencia_por_risco()
        acordos = await self._count_acordos(since, until)
        com_estrategia = await self._count_com_estrategia(since, until)
        resultado_kanban, sem_matter = await self._resultado_via_matter()
        motivo_registrado = await self._count_motivo_encerramento(since, until)

        sample_size = sum(row.count for row in estoque)
        honesty = BiHonesty(
            generated_at=instant,
            period_from=since,
            period_to=until,
            sample_size=sample_size,
            origin="legal_demands (+ matters.column_id quando vinculado, klm_audit_events)",
            missing_fields=(
                "duracao_media_dias_por_natureza é aproximada por LegalDemand.updated_at "
                "no estado terminal atual, não pelo carimbo exato da transição",
                "resultado_por_coluna_kanban só existe para demandas com matter_id "
                "vinculado — o restante está em resultado_sem_matter_vinculado",
            ),
        )
        return LegalBi(
            honesty=honesty,
            estoque_por_fase=estoque,
            entradas_no_periodo=entradas,
            saidas_no_periodo=saidas,
            duracao_media_dias_por_natureza=duracao,
            valores_por_fase=valores,
            contingencia_por_risco=contingencia,
            acordos_no_periodo=acordos,
            demandas_com_estrategia_registrada=com_estrategia,
            resultado_por_coluna_kanban=resultado_kanban,
            resultado_sem_matter_vinculado=sem_matter,
            motivo_encerramento_registrado=motivo_registrado,
        )

    async def _estoque_por_fase(self) -> tuple[CountRow, ...]:
        result = await self.db.execute(
            select(LegalDemand.phase, func.count(LegalDemand.id))
            .where(LegalDemand.workspace_id == self.workspace_id)
            .group_by(LegalDemand.phase)
        )
        return tuple(CountRow(key=phase.value, count=int(count)) for phase, count in result.all())

    async def _count_entradas(self, since: datetime, until: datetime) -> int:
        result = await self.db.execute(
            select(func.count(LegalDemand.id)).where(
                LegalDemand.workspace_id == self.workspace_id,
                LegalDemand.created_at >= since,
                LegalDemand.created_at <= until,
            )
        )
        return int(result.scalar_one() or 0)

    async def _count_saidas(self, since: datetime, until: datetime) -> int:
        result = await self.db.execute(
            select(KlmAuditEvent.after_data).where(
                KlmAuditEvent.workspace_id == self.workspace_id,
                KlmAuditEvent.entity_type == "legal_demand",
                KlmAuditEvent.event_type == "legal_demand_phase_transitioned",
                KlmAuditEvent.occurred_at >= since,
                KlmAuditEvent.occurred_at <= until,
            )
        )
        terminal_values = {phase.value for phase in LEGAL_DEMAND_TERMINAL_PHASES}
        count = 0
        for (after_data,) in result.all():
            if isinstance(after_data, dict) and after_data.get("toPhase") in terminal_values:
                count += 1
        return count

    async def _duracao_media_por_natureza(self) -> tuple[NatureDurationRow, ...]:
        result = await self.db.execute(
            select(LegalDemand.nature, LegalDemand.created_at, LegalDemand.updated_at).where(
                LegalDemand.workspace_id == self.workspace_id,
                LegalDemand.phase.in_(list(LEGAL_DEMAND_TERMINAL_PHASES)),
            )
        )
        buckets: dict[str, list[float]] = {}
        for nature, created_at, updated_at in result.all():
            if created_at is None or updated_at is None:
                continue
            days = (updated_at - created_at).total_seconds() / 86400.0
            if days < 0:
                continue
            buckets.setdefault(nature.value, []).append(days)
        rows = [
            NatureDurationRow(
                nature=nature,
                sample_size=len(values),
                avg_days=round(sum(values) / len(values), 1),
            )
            for nature, values in buckets.items()
        ]
        return tuple(sorted(rows, key=lambda row: -row.sample_size))

    async def _valores_por_fase(self) -> tuple[AmountRow, ...]:
        result = await self.db.execute(
            select(LegalDemand.phase, func.sum(LegalDemand.value), func.count(LegalDemand.id))
            .where(
                LegalDemand.workspace_id == self.workspace_id,
                LegalDemand.value.is_not(None),
            )
            .group_by(LegalDemand.phase)
        )
        return tuple(
            AmountRow(key=phase.value, total_value=float(total or 0), count=int(count))
            for phase, total, count in result.all()
        )

    async def _contingencia_por_risco(self) -> tuple[AmountRow, ...]:
        result = await self.db.execute(
            select(LegalDemand.risk, func.sum(LegalDemand.value), func.count(LegalDemand.id))
            .where(
                LegalDemand.workspace_id == self.workspace_id,
                LegalDemand.phase.notin_(list(LEGAL_DEMAND_TERMINAL_PHASES)),
                LegalDemand.value.is_not(None),
            )
            .group_by(LegalDemand.risk)
        )
        return tuple(
            AmountRow(key=risk.value, total_value=float(total or 0), count=int(count))
            for risk, total, count in result.all()
        )

    async def _count_acordos(self, since: datetime, until: datetime) -> int:
        result = await self.db.execute(
            select(func.count(LegalDemand.id)).where(
                LegalDemand.workspace_id == self.workspace_id,
                LegalDemand.nature == LegalDemandNature.acordo,
                LegalDemand.created_at >= since,
                LegalDemand.created_at <= until,
            )
        )
        return int(result.scalar_one() or 0)

    async def _count_com_estrategia(self, since: datetime, until: datetime) -> int:
        result = await self.db.execute(
            select(func.count(LegalDemand.id)).where(
                LegalDemand.workspace_id == self.workspace_id,
                LegalDemand.strategy.is_not(None),
                LegalDemand.created_at >= since,
                LegalDemand.created_at <= until,
            )
        )
        return int(result.scalar_one() or 0)

    async def _resultado_via_matter(self) -> tuple[tuple[CountRow, ...], int]:
        result = await self.db.execute(
            select(LegalDemand.matter_id, Matter.column_id)
            .join(Matter, Matter.id == LegalDemand.matter_id)
            .where(
                LegalDemand.workspace_id == self.workspace_id,
                LegalDemand.phase.in_(list(LEGAL_DEMAND_TERMINAL_PHASES)),
            )
        )
        counts: dict[str, int] = {}
        for _matter_id, column_id in result.all():
            key = column_id.value if column_id else "sem_coluna"
            counts[key] = counts.get(key, 0) + 1
        rows = tuple(CountRow(key=key, count=count) for key, count in counts.items())

        sem_matter_result = await self.db.execute(
            select(func.count(LegalDemand.id)).where(
                LegalDemand.workspace_id == self.workspace_id,
                LegalDemand.phase.in_(list(LEGAL_DEMAND_TERMINAL_PHASES)),
                LegalDemand.matter_id.is_(None),
            )
        )
        return rows, int(sem_matter_result.scalar_one() or 0)

    async def _count_motivo_encerramento(self, since: datetime, until: datetime) -> int:
        result = await self.db.execute(
            select(KlmAuditEvent.after_data).where(
                KlmAuditEvent.workspace_id == self.workspace_id,
                KlmAuditEvent.entity_type == "legal_demand",
                KlmAuditEvent.event_type == "legal_demand_phase_transitioned",
                KlmAuditEvent.occurred_at >= since,
                KlmAuditEvent.occurred_at <= until,
            )
        )
        terminal_values = {phase.value for phase in LEGAL_DEMAND_TERMINAL_PHASES}
        count = 0
        for (after_data,) in result.all():
            if (
                isinstance(after_data, dict)
                and after_data.get("toPhase") in terminal_values
                and after_data.get("reason")
            ):
                count += 1
        return count

    # ── IA ───────────────────────────────────────────────────────────────

    async def get_ai_bi(
        self,
        *,
        period_from: datetime | None = None,
        period_to: datetime | None = None,
        now: datetime | None = None,
    ) -> AiBi:
        instant = now or datetime.now(UTC)
        default_from, default_to = _default_window(instant)
        since = period_from or default_from
        until = period_to or default_to

        passos_por_agente = await self._passos_por_agente(since, until)
        totais, falhas, aguardando = await self._execucoes_status_counts(since, until)
        overrides_total, aceitas, corrigidas, rejeitadas = await self._decisoes_humanas(
            since, until
        )
        custo_por_agente = await self._custo_por_agente(since, until)
        tokens_in, tokens_out = await self._tokens_totais(since, until)

        taxa_intervencao = round(overrides_total / totais, 4) if totais > 0 else None

        honesty = BiHonesty(
            generated_at=instant,
            period_from=since,
            period_to=until,
            sample_size=totais,
            origin="agent_runs + human_overrides",
            missing_fields=(
                "tempo_economizado — nenhum modelo grava a linha de base manual "
                "para comparar; não é calculado para não fabricar o número",
                "rollback — não há um estado/evento dedicado de reversão de saída "
                "de agente hoje; não é calculado para não fabricar o número",
                "execucoes_aguardando_humano é rotulado como proxy de abstention "
                "(AgentRunStatus.waiting_human), não o conceito exato do produto",
            ),
        )
        return AiBi(
            honesty=honesty,
            passos_automatizados_por_agente=passos_por_agente,
            execucoes_totais=totais,
            execucoes_falhas=falhas,
            execucoes_aguardando_humano=aguardando,
            intervencoes_humanas_total=overrides_total,
            taxa_intervencao_humana=taxa_intervencao,
            decisoes_aceitas=aceitas,
            decisoes_corrigidas=corrigidas,
            decisoes_rejeitadas=rejeitadas,
            custo_usd_por_agente=custo_por_agente,
            tokens_input_totais=tokens_in,
            tokens_output_totais=tokens_out,
        )

    async def _passos_por_agente(self, since: datetime, until: datetime) -> tuple[CountRow, ...]:
        result = await self.db.execute(
            select(AgentRun.agent_name, func.count(AgentRun.id))
            .where(
                AgentRun.workspace_id == self.workspace_id,
                AgentRun.status == AgentRunStatus.completed,
                AgentRun.created_at >= since,
                AgentRun.created_at <= until,
            )
            .group_by(AgentRun.agent_name)
        )
        rows = [CountRow(key=name, count=int(count)) for name, count in result.all()]
        return tuple(sorted(rows, key=lambda row: -row.count))

    async def _execucoes_status_counts(
        self, since: datetime, until: datetime
    ) -> tuple[int, int, int]:
        result = await self.db.execute(
            select(AgentRun.status, func.count(AgentRun.id))
            .where(
                AgentRun.workspace_id == self.workspace_id,
                AgentRun.created_at >= since,
                AgentRun.created_at <= until,
            )
            .group_by(AgentRun.status)
        )
        counts = {status.value: int(count) for status, count in result.all()}
        totais = sum(counts.values())
        falhas = counts.get(AgentRunStatus.failed.value, 0)
        aguardando = counts.get(AgentRunStatus.waiting_human.value, 0)
        return totais, falhas, aguardando

    async def _decisoes_humanas(
        self, since: datetime, until: datetime
    ) -> tuple[int, int, int, int]:
        result = await self.db.execute(
            select(HumanOverride.decision, func.count(HumanOverride.id))
            .where(
                HumanOverride.workspace_id == self.workspace_id,
                HumanOverride.created_at >= since,
                HumanOverride.created_at <= until,
            )
            .group_by(HumanOverride.decision)
        )
        counts = {decision.value: int(count) for decision, count in result.all()}
        total = sum(counts.values())
        return (
            total,
            counts.get(HumanOverrideDecision.accepted.value, 0),
            counts.get(HumanOverrideDecision.corrected.value, 0),
            counts.get(HumanOverrideDecision.rejected.value, 0),
        )

    async def _custo_por_agente(self, since: datetime, until: datetime) -> tuple[AmountRow, ...]:
        result = await self.db.execute(
            select(AgentRun.agent_name, func.sum(AgentRun.cost_usd), func.count(AgentRun.id))
            .where(
                AgentRun.workspace_id == self.workspace_id,
                AgentRun.created_at >= since,
                AgentRun.created_at <= until,
            )
            .group_by(AgentRun.agent_name)
        )
        rows = [
            AmountRow(key=name, total_value=float(total or 0), count=int(count))
            for name, total, count in result.all()
        ]
        return tuple(sorted(rows, key=lambda row: -row.total_value))

    async def _tokens_totais(self, since: datetime, until: datetime) -> tuple[int, int]:
        result = await self.db.execute(
            select(func.sum(AgentRun.input_tokens), func.sum(AgentRun.output_tokens)).where(
                AgentRun.workspace_id == self.workspace_id,
                AgentRun.created_at >= since,
                AgentRun.created_at <= until,
            )
        )
        row = result.one()
        return int(row[0] or 0), int(row[1] or 0)


__all__ = [
    "LegalBiService",
    "BiHonesty",
    "JourneyTimingStat",
    "CountRow",
    "AmountRow",
    "ClientSlaRow",
    "NatureDurationRow",
    "OperationBi",
    "LegalBi",
    "AiBi",
    "DEFAULT_WINDOW_DAYS",
]
