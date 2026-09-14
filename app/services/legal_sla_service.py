"""Service: SLA em camadas do trabalho jurídico (Onda 4, Plano Mestre §8).

Camadas, da mais frouxa para a mais restritiva (cada uma progressivamente
mais cedo que a anterior — CONTRATO da Onda 4): prazo legal < prazo fatal
< prazo interno < SLA do cliente < prazo do playbook < margem de
segurança. "Mais restritivo" = a data MAIS CEDO entre as camadas
preenchidas; a margem de segurança é subtraída por cima dessa data para
chegar ao prazo efetivo de trabalho (`LegalDemand.sla_effective_at`).

Escalonamento: D-7/D-3/D-1/D-0/atrasado, cada nível dispara uma única vez
por demanda (`SlaEscalationLog`, unique por workspace+demanda+nível) via
`OperationalAlertService` — reusa o gate fail-closed
(`external_delivery_allowed()`) e o canal Email/WhatsApp já existentes,
NUNCA reimplementa entrega. Confirmação de leitura é
`SlaEscalationLog.acknowledged_at`.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.legal_controladoria import SlaEscalationLevel, SlaEscalationLog
from app.models.legal_demand import LEGAL_DEMAND_TERMINAL_PHASES, LegalDemand
from app.services.operational_alert_service import OperationalAlertService

# Ordem da mais frouxa para a mais restritiva — a camada vencedora é a
# menor data dentre as preenchidas (não a última da lista).
_SLA_LAYERS: tuple[tuple[str, str], ...] = (
    ("prazo_legal", "prazo_legal_at"),
    ("prazo_fatal", "prazo_fatal_at"),
    ("prazo_interno", "prazo_interno_at"),
    ("sla_cliente", "sla_cliente_at"),
    ("prazo_playbook", "prazo_playbook_at"),
)

# Limiares de dias restantes até o prazo efetivo → nível de escalonamento.
# Avaliados na ordem (mais severo primeiro): atrasado < d0 <= 0 < d1 <= 1
# < d3 <= 3 < d7 <= 7.
_ESCALATION_THRESHOLDS: tuple[tuple[int, SlaEscalationLevel], ...] = (
    (0, SlaEscalationLevel.d0),
    (1, SlaEscalationLevel.d1),
    (3, SlaEscalationLevel.d3),
    (7, SlaEscalationLevel.d7),
)
# Ordem de severidade (menor índice = mais severo) — usada para decidir se
# um novo nível calculado é uma escalada real (nunca "desescala" sozinho).
_LEVEL_SEVERITY: dict[str, int] = {
    SlaEscalationLevel.atrasado.value: 0,
    SlaEscalationLevel.d0.value: 1,
    SlaEscalationLevel.d1.value: 2,
    SlaEscalationLevel.d3.value: 3,
    SlaEscalationLevel.d7.value: 4,
}


class LegalSlaError(RuntimeError):
    pass


class LegalSlaDemandNotFoundError(LegalSlaError):
    pass


class LegalSlaLogNotFoundError(LegalSlaError):
    pass


def compute_effective_deadline(demand: LegalDemand) -> tuple[datetime | None, str | None]:
    """Data mais restritiva dentre as camadas preenchidas, menos a margem
    de segurança. Não infere nada além do que já está gravado na demanda."""
    present = [
        (layer_name, value)
        for layer_name, field_name in _SLA_LAYERS
        if (value := getattr(demand, field_name)) is not None
    ]
    if not present:
        return None, None
    layer_name, layer_dt = min(present, key=lambda item: item[1])
    margin_days = demand.margem_seguranca_dias or 0
    effective = layer_dt - timedelta(days=margin_days)
    return effective, layer_name


def escalation_level_for(effective_at: datetime, *, now: datetime | None = None) -> SlaEscalationLevel | None:
    """Nível de escalonamento para uma data efetiva, ou None se ainda
    fora de qualquer janela (> D-7)."""
    now = now or datetime.now(UTC)
    remaining_days = (effective_at.date() - now.date()).days
    if remaining_days < 0:
        return SlaEscalationLevel.atrasado
    for threshold_days, level in _ESCALATION_THRESHOLDS:
        if remaining_days <= threshold_days:
            return level
    return None


class LegalSlaService:
    def __init__(self, db: AsyncSession, workspace_id: UUID) -> None:
        if workspace_id is None:
            raise ValueError("workspace_id is required")
        self.db = db
        self.workspace_id = workspace_id
        self.alert_service = OperationalAlertService(db)

    async def recompute(self, legal_demand_id: UUID, *, now: datetime | None = None) -> LegalDemand:
        """Recalcula `sla_effective_at`/`sla_effective_layer` e escala se
        o nível calculado for mais severo que o último já disparado."""
        instant = now or datetime.now(UTC)
        demand = await self.get_demand(legal_demand_id)

        effective_at, layer = compute_effective_deadline(demand)
        demand.sla_effective_at = effective_at
        demand.sla_effective_layer = layer

        if effective_at is None or demand.phase in LEGAL_DEMAND_TERMINAL_PHASES:
            await self.db.flush()
            return demand

        level = escalation_level_for(effective_at, now=instant)
        if level is None:
            await self.db.flush()
            return demand

        current_severity = _LEVEL_SEVERITY[level.value]
        last_severity = _LEVEL_SEVERITY.get(demand.sla_escalation_level or "", 99)
        if current_severity < last_severity:
            await self._escalate(demand, level=level, effective_at=effective_at, layer=layer, now=instant)
            demand.sla_escalation_level = level.value

        await self.db.flush()
        return demand

    async def recompute_due(self, *, now: datetime | None = None) -> int:
        """Varre as demandas do workspace com camada de SLA preenchida e
        recalcula todas — idempotente (nível já escalado nunca reescala)."""
        instant = now or datetime.now(UTC)
        result = await self.db.execute(
            select(LegalDemand.id).where(
                LegalDemand.workspace_id == self.workspace_id,
                LegalDemand.phase.notin_(list(LEGAL_DEMAND_TERMINAL_PHASES)),
            )
        )
        demand_ids = [row[0] for row in result.all()]
        touched = 0
        for demand_id in demand_ids:
            await self.recompute(demand_id, now=instant)
            touched += 1
        return touched

    async def acknowledge(
        self, log_id: UUID, *, acknowledged_by_user_id: UUID, at: datetime | None = None
    ) -> SlaEscalationLog:
        instant = at or datetime.now(UTC)
        log = await self.db.scalar(
            select(SlaEscalationLog).where(
                SlaEscalationLog.id == log_id,
                SlaEscalationLog.workspace_id == self.workspace_id,
            )
        )
        if log is None:
            raise LegalSlaLogNotFoundError(f"Escalonamento {log_id} não encontrado.")
        if log.acknowledged_at is None:
            log.acknowledged_by_user_id = acknowledged_by_user_id
            log.acknowledged_at = instant
            await self.db.flush()
        return log

    async def list_escalations(self, legal_demand_id: UUID) -> list[SlaEscalationLog]:
        result = await self.db.execute(
            select(SlaEscalationLog)
            .where(
                SlaEscalationLog.workspace_id == self.workspace_id,
                SlaEscalationLog.legal_demand_id == legal_demand_id,
            )
            .order_by(SlaEscalationLog.created_at.desc())
        )
        return list(result.scalars().all())

    # ── Internos ─────────────────────────────────────────────────────────

    async def get_demand(self, legal_demand_id: UUID) -> LegalDemand:
        demand = await self.db.scalar(
            select(LegalDemand).where(
                LegalDemand.id == legal_demand_id,
                LegalDemand.workspace_id == self.workspace_id,
            )
        )
        if demand is None:
            raise LegalSlaDemandNotFoundError(f"Demanda {legal_demand_id} não encontrada.")
        return demand

    async def _escalate(
        self,
        demand: LegalDemand,
        *,
        level: SlaEscalationLevel,
        effective_at: datetime,
        layer: str | None,
        now: datetime,
    ) -> SlaEscalationLog | None:
        log = SlaEscalationLog(
            workspace_id=self.workspace_id,
            legal_demand_id=demand.id,
            level=level,
            layer=layer,
            due_at=effective_at,
            escalated_to_user_id=demand.responsible_user_id,
            channel_results={},
        )
        self.db.add(log)
        try:
            async with self.db.begin_nested():
                await self.db.flush()
        except IntegrityError:
            # Já escalado neste nível por outra varredura concorrente —
            # idempotente, mesmo padrão de ExceptionCenterService.open_exception.
            return None

        sent = await self.alert_service.notify_sla_escalation(
            demand.id,
            level=level.value,
            layer=layer,
            due_at=effective_at,
        )
        log.channel_results = {"dispatched": bool(sent)}
        await self.db.flush()
        return log
