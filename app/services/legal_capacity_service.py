"""Service: Capacidade jurídica — carga vs disponibilidade por
responsável/equipe/unidade (Onda 8, Plano Mestre §15 item 2).

NÃO reimplementa carga nem disponibilidade do zero:
  * "carga" reusa a MESMA leitura de `LegalDistributionService.
    _active_assignment_counts` — `LegalDemand` fora de fase terminal,
    agrupada por `responsible_user_id` — mais a contagem de `Task` em
    aberto (`status != done`) via o mesmo join com `Matter` usado em
    `ReportsService._count_tasks`;
  * "disponibilidade" reusa os MESMOS modelos de
    `app/models/staff_availability.py` (Onda "colaboração nativa") —
    `StaffAvailabilityProfile`/`StaffRecurringAvailability`/
    `StaffAvailabilityException`. Este serviço NÃO chama
    `StaffAvailabilityService.evaluate()` por pessoa: isso emitiria 2+
    queries por responsável (N+1) ao rodar o painel para toda a equipe.
    Em vez disso, ele recomputa a MESMA regra (modo "away" com janela +
    exceção ativa bloqueante) em lote, com um número fixo de queries
    independente da quantidade de responsáveis — ver `_bulk_availability`.
    Quando precisão pontual de UMA pessoa em UM instante for necessária
    (ex.: decidir para quem redirecionar agora), o caminho certo
    continua sendo `StaffAvailabilityService.evaluate()`.

Interpretações de domínio assumidas (documentadas para auditoria, mesmo
padrão de `legal_bi_service.py`):
  * "carga" conta trabalho ATIVO (LegalDemand não terminal + Task não
    concluída) — nunca soma valor monetário nem gravidade, é contagem
    pura de itens em aberto;
  * "disponibilidade" só existe em horas/semana quando o responsável tem
    `StaffRecurringAvailability` configurada
    (`StaffAvailabilityProfile.schedule_configured=True`). Sem agenda
    configurada, `horas_semana_configuradas` é `None` (não é
    fabricado como 0 nem como 40h) — o painel deve mostrar
    "sem agenda configurada", nunca inferir uma jornada padrão;
  * carga (contagem de itens) e disponibilidade (horas/semana) são
    unidades diferentes — este serviço NUNCA divide uma pela outra para
    produzir um "% de utilização": isso apresentaria causalidade
    (carga vs. tempo) onde só há duas contagens correlacionadas de
    origens distintas. O consumidor do endpoint decide como cruzar os
    dois números;
  * agregação "por unidade"/"por equipe" usa `LegalDemand.unit`/`.team`
    (texto livre preenchido na própria demanda, Onda 2) — carga é
    somada diretamente por esses campos; disponibilidade agregada por
    unidade/equipe NÃO é calculada (perfil de disponibilidade é por
    pessoa, não por unidade) e entra em `missing_fields`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.legal_demand import LEGAL_DEMAND_TERMINAL_PHASES, LegalDemand
from app.models.matter import Matter
from app.models.staff_availability import (
    AvailabilityExceptionKind,
    AvailabilityExceptionScope,
    AvailabilityMode,
    StaffAvailabilityException,
    StaffAvailabilityProfile,
    StaffRecurringAvailability,
)
from app.models.task import Task, TaskStatus
from app.models.user import User
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership


def _loaded_utc(value: datetime) -> datetime:
    """SQLite descarta o offset de timezone ao ler de volta; PostgreSQL em
    produção preserva. Mesmo utilitário de
    `app/services/staff_availability_service.py:_loaded_utc` — sem essa
    normalização, comparar um datetime lido do banco com `instant`
    (sempre timezone-aware) explode em `TypeError` sob SQLite."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class CapacityHonesty:
    generated_at: datetime
    sample_size: int
    origin: str
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResponsibleCapacity:
    responsible_user_id: str
    responsible_name: str
    carga_demandas_ativas: int
    carga_tarefas_abertas: int
    disponibilidade_configurada: bool
    horas_semana_configuradas: float | None
    disponivel_agora: bool
    motivo_disponibilidade: str


@dataclass(frozen=True)
class GroupCapacity:
    key: str
    carga_demandas_ativas: int


@dataclass(frozen=True)
class CapacityReport:
    honesty: CapacityHonesty
    por_responsavel: tuple[ResponsibleCapacity, ...]
    por_unidade: tuple[GroupCapacity, ...]
    por_equipe: tuple[GroupCapacity, ...]


class LegalCapacityService:
    def __init__(self, db: AsyncSession, workspace_id: UUID) -> None:
        if workspace_id is None:
            raise ValueError("workspace_id is required")
        self.db = db
        self.workspace_id = workspace_id

    async def get_capacity(self, *, now: datetime | None = None) -> CapacityReport:
        instant = now or datetime.now(UTC)

        members = await self._active_members()
        member_ids = [member_id for member_id, _name in members]

        carga_demandas = await self._active_demand_counts(member_ids)
        carga_tarefas = await self._open_task_counts(member_ids)
        availability = await self._bulk_availability(member_ids, instant)

        por_responsavel = tuple(
            ResponsibleCapacity(
                responsible_user_id=str(member_id),
                responsible_name=name,
                carga_demandas_ativas=carga_demandas.get(str(member_id), 0),
                carga_tarefas_abertas=carga_tarefas.get(str(member_id), 0),
                disponibilidade_configurada=availability[str(member_id)]["configured"],
                horas_semana_configuradas=availability[str(member_id)]["hours_per_week"],
                disponivel_agora=availability[str(member_id)]["available_now"],
                motivo_disponibilidade=availability[str(member_id)]["reason"],
            )
            for member_id, name in members
        )
        por_unidade = await self._group_active_demands_by(LegalDemand.unit)
        por_equipe = await self._group_active_demands_by(LegalDemand.team)

        honesty = CapacityHonesty(
            generated_at=instant,
            sample_size=len(members),
            origin=(
                "workspace_memberships (ativos) + legal_demands (carga) + tasks/matters "
                "(carga) + staff_availability_profiles/recurring/exceptions (disponibilidade)"
            ),
            missing_fields=(
                "horas_semana_configuradas é None quando o responsável não tem agenda "
                "recorrente configurada — nunca é assumido um padrão de 40h",
                "não há % de utilização: carga (contagem de itens) e disponibilidade "
                "(horas/semana) são unidades diferentes, cruzá-las é decisão de quem lê",
                "por_unidade/por_equipe só somam carga — disponibilidade é um atributo "
                "por pessoa, não por unidade/equipe",
            ),
        )
        return CapacityReport(
            honesty=honesty,
            por_responsavel=por_responsavel,
            por_unidade=por_unidade,
            por_equipe=por_equipe,
        )

    async def _active_members(self) -> list[tuple[UUID, str]]:
        result = await self.db.execute(
            select(WorkspaceMembership.user_id, User.name)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == self.workspace_id,
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )
        return [(user_id, name) for user_id, name in result.all()]

    async def _active_demand_counts(self, member_ids: list[UUID]) -> dict[str, int]:
        if not member_ids:
            return {}
        result = await self.db.execute(
            select(LegalDemand.responsible_user_id, func.count(LegalDemand.id))
            .where(
                LegalDemand.workspace_id == self.workspace_id,
                LegalDemand.responsible_user_id.in_(member_ids),
                LegalDemand.phase.notin_(list(LEGAL_DEMAND_TERMINAL_PHASES)),
            )
            .group_by(LegalDemand.responsible_user_id)
        )
        return {str(user_id): int(count) for user_id, count in result.all()}

    async def _open_task_counts(self, member_ids: list[UUID]) -> dict[str, int]:
        if not member_ids:
            return {}
        result = await self.db.execute(
            select(Task.assigned_to, func.count(Task.id))
            .select_from(Task)
            .join(Matter, Matter.id == Task.matter_id)
            .where(
                Matter.workspace_id == self.workspace_id,
                Matter.deleted_at.is_(None),
                Task.assigned_to.in_(member_ids),
                Task.status != TaskStatus.done,
            )
            .group_by(Task.assigned_to)
        )
        return {str(user_id): int(count) for user_id, count in result.all()}

    async def _bulk_availability(
        self, member_ids: list[UUID], instant: datetime
    ) -> dict[str, dict]:
        """Recomputa em lote a mesma regra "disponível agora" de
        `StaffAvailabilityService.evaluate()` (modo away + exceção
        bloqueante ativa), sem consultar por pessoa (evita N+1). Não cobre
        agenda recorrente por dia da semana no cálculo de "agora" — apenas
        a janela away/exceção, que é o que decide bloqueio imediato; as
        horas/semana configuradas vêm de `StaffRecurringAvailability`
        separadamente, somadas por perfil."""
        base: dict[str, dict] = {
            str(member_id): {
                "configured": False,
                "hours_per_week": None,
                "available_now": True,
                "reason": "schedule_not_configured",
            }
            for member_id in member_ids
        }
        if not member_ids:
            return base

        profiles_result = await self.db.execute(
            select(StaffAvailabilityProfile).where(
                StaffAvailabilityProfile.workspace_id == self.workspace_id,
                StaffAvailabilityProfile.staff_user_id.in_(member_ids),
            )
        )
        profiles = {str(p.staff_user_id): p for p in profiles_result.scalars().all()}

        exceptions_result = await self.db.execute(
            select(StaffAvailabilityException).where(
                StaffAvailabilityException.workspace_id == self.workspace_id,
                StaffAvailabilityException.deleted_at.is_(None),
                StaffAvailabilityException.starts_at <= instant,
                StaffAvailabilityException.ends_at > instant,
            )
        )
        exceptions = list(exceptions_result.scalars().all())
        blocking_by_staff: dict[str, StaffAvailabilityException] = {}
        workspace_scope_blocking: StaffAvailabilityException | None = None
        for exc in exceptions:
            if exc.kind == AvailabilityExceptionKind.available_override:
                continue
            if exc.scope == AvailabilityExceptionScope.workspace:
                workspace_scope_blocking = exc
            elif exc.staff_user_id is not None:
                blocking_by_staff[str(exc.staff_user_id)] = exc

        profile_ids = [p.id for p in profiles.values() if p.schedule_configured]
        hours_by_profile: dict[str, float] = {}
        if profile_ids:
            slots_result = await self.db.execute(
                select(
                    StaffRecurringAvailability.profile_id,
                    StaffRecurringAvailability.starts_at,
                    StaffRecurringAvailability.ends_at,
                ).where(
                    StaffRecurringAvailability.workspace_id == self.workspace_id,
                    StaffRecurringAvailability.profile_id.in_(profile_ids),
                )
            )
            for profile_id, starts_at, ends_at in slots_result.all():
                start_seconds = starts_at.hour * 3600 + starts_at.minute * 60 + starts_at.second
                end_seconds = ends_at.hour * 3600 + ends_at.minute * 60 + ends_at.second
                hours = max(0.0, (end_seconds - start_seconds) / 3600.0)
                key = str(profile_id)
                hours_by_profile[key] = hours_by_profile.get(key, 0.0) + hours

        for member_id in member_ids:
            key = str(member_id)
            profile = profiles.get(key)
            configured = profile is not None and profile.schedule_configured
            hours_per_week = (
                hours_by_profile.get(str(profile.id)) if (profile and configured) else None
            )

            away_active = False
            if profile is not None and profile.availability_mode == AvailabilityMode.away:
                starts = (
                    _loaded_utc(profile.availability_starts_at)
                    if profile.availability_starts_at
                    else None
                )
                ends = (
                    _loaded_utc(profile.availability_ends_at)
                    if profile.availability_ends_at
                    else None
                )
                if starts is None and ends is None:
                    away_active = True
                elif starts is not None and ends is not None:
                    away_active = starts <= instant <= ends
                elif starts is not None:
                    away_active = starts <= instant
                elif ends is not None:
                    away_active = instant <= ends

            blocking_exception = blocking_by_staff.get(key) or workspace_scope_blocking
            if away_active:
                available_now = False
                reason = "scheduled_away"
            elif blocking_exception is not None:
                available_now = False
                reason = blocking_exception.kind.value
            else:
                available_now = True
                reason = "recurring_schedule" if configured else "schedule_not_configured"

            base[key] = {
                "configured": configured,
                "hours_per_week": round(hours_per_week, 1) if hours_per_week is not None else None,
                "available_now": available_now,
                "reason": reason,
            }
        return base

    async def _group_active_demands_by(self, column) -> tuple[GroupCapacity, ...]:
        result = await self.db.execute(
            select(column, func.count(LegalDemand.id))
            .where(
                LegalDemand.workspace_id == self.workspace_id,
                LegalDemand.phase.notin_(list(LEGAL_DEMAND_TERMINAL_PHASES)),
            )
            .group_by(column)
        )
        rows = [
            GroupCapacity(key=(value or "nao_informado"), carga_demandas_ativas=int(count))
            for value, count in result.all()
        ]
        return tuple(sorted(rows, key=lambda row: -row.carga_demandas_ativas))


__all__ = [
    "LegalCapacityService",
    "CapacityHonesty",
    "ResponsibleCapacity",
    "GroupCapacity",
    "CapacityReport",
]
