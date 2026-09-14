"""Service: distribuição configurável de trabalho jurídico (Onda 4, Plano
Mestre §8).

`LegalDemand` é o "trabalho jurídico" distribuído (ver app/models/
legal_demand.py — camada canônica que já unifica as 7 naturezas). Este
service NÃO reimplementa disponibilidade/férias/substituição: delega para
`StaffAvailabilityService.resolve_responsibility`
(app/services/staff_availability_service.py), que já resolve o redirect
aprovado (perfil/exceção de ausência) do candidato escolhido.

Toda (re)distribuição — automática por regra ou manual — grava uma
`DistributionDecision` com `reason` obrigatório: "nunca redistribui sem
registrar" (Plano Mestre §8).
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.legal_controladoria import (
    DistributionDecision,
    DistributionRule,
    DistributionStrategy,
    DistributionTrigger,
)
from app.models.legal_demand import LEGAL_DEMAND_TERMINAL_PHASES, LegalDemand
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.services.staff_availability_service import StaffAvailabilityService


class DistributionError(RuntimeError):
    """Base para erros de domínio da distribuição."""


class DistributionRuleNotFoundError(DistributionError):
    pass


class DistributionRuleNameConflictError(DistributionError):
    pass


class DistributionDemandNotFoundError(DistributionError):
    pass


class DistributionNoRuleMatchedError(DistributionError):
    """Nenhuma regra ativa casou com os critérios da demanda."""


class DistributionNoAvailableCandidateError(DistributionError):
    """Toda a lista de candidatos elegíveis está indisponível/lotada."""


class DistributionReasonRequiredError(DistributionError):
    """Toda (re)distribuição — automática ou manual — exige motivo."""


class LegalDistributionService:
    def __init__(self, db: AsyncSession, workspace_id: UUID) -> None:
        if workspace_id is None:
            raise ValueError("workspace_id is required")
        self.db = db
        self.workspace_id = workspace_id
        self.availability_service = StaffAvailabilityService(db)

    # ── Regras (CRUD) ────────────────────────────────────────────────────

    async def create_rule(
        self,
        *,
        name: str,
        description: str | None = None,
        active: bool = True,
        priority: int = 100,
        match_natures: list[str] | None = None,
        match_areas: list[str] | None = None,
        match_client_ids: list[str] | None = None,
        match_risks: list[str] | None = None,
        match_units: list[str] | None = None,
        match_teams: list[str] | None = None,
        strategy: DistributionStrategy = DistributionStrategy.round_robin,
        candidate_user_ids: list[str] | None = None,
        max_active_per_candidate: int | None = None,
        respect_availability: bool = True,
        created_by_user_id: UUID | None = None,
    ) -> DistributionRule:
        existing = await self.db.scalar(
            select(DistributionRule).where(
                DistributionRule.workspace_id == self.workspace_id,
                DistributionRule.name == name,
            )
        )
        if existing is not None:
            raise DistributionRuleNameConflictError(
                f"Já existe uma regra de distribuição chamada '{name}' neste workspace."
            )
        candidates = [str(item) for item in (candidate_user_ids or [])]
        await self._assert_candidates_are_active_members(candidates)
        rule = DistributionRule(
            workspace_id=self.workspace_id,
            name=name,
            description=description,
            active=active,
            priority=priority,
            match_natures=list(match_natures or []),
            match_areas=list(match_areas or []),
            match_client_ids=[str(item) for item in (match_client_ids or [])],
            match_risks=list(match_risks or []),
            match_units=list(match_units or []),
            match_teams=list(match_teams or []),
            strategy=strategy,
            candidate_user_ids=candidates,
            max_active_per_candidate=max_active_per_candidate,
            respect_availability=respect_availability,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(rule)
        await self.db.flush()
        return rule

    async def update_rule(self, rule_id: UUID, **fields) -> DistributionRule:
        rule = await self.get_rule(rule_id)
        if "candidate_user_ids" in fields and fields["candidate_user_ids"] is not None:
            fields["candidate_user_ids"] = [str(item) for item in fields["candidate_user_ids"]]
            await self._assert_candidates_are_active_members(fields["candidate_user_ids"])
        if "match_client_ids" in fields and fields["match_client_ids"] is not None:
            fields["match_client_ids"] = [str(item) for item in fields["match_client_ids"]]
        for key, value in fields.items():
            if value is not None and hasattr(rule, key):
                setattr(rule, key, value)
        await self.db.flush()
        return rule

    async def get_rule(self, rule_id: UUID) -> DistributionRule:
        rule = await self.db.scalar(
            select(DistributionRule).where(
                DistributionRule.id == rule_id,
                DistributionRule.workspace_id == self.workspace_id,
            )
        )
        if rule is None:
            raise DistributionRuleNotFoundError(f"Regra {rule_id} não encontrada.")
        return rule

    async def list_rules(self, *, active_only: bool = False) -> list[DistributionRule]:
        statement = select(DistributionRule).where(
            DistributionRule.workspace_id == self.workspace_id
        )
        if active_only:
            statement = statement.where(DistributionRule.active.is_(True))
        statement = statement.order_by(
            DistributionRule.priority.asc(), DistributionRule.created_at.asc()
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def delete_rule(self, rule_id: UUID) -> None:
        rule = await self.get_rule(rule_id)
        await self.db.delete(rule)
        await self.db.flush()

    async def _assert_candidates_are_active_members(self, candidate_ids: list[str]) -> None:
        if not candidate_ids:
            return
        uuids = [UUID(str(item)) for item in candidate_ids]
        result = await self.db.execute(
            select(WorkspaceMembership.user_id).where(
                WorkspaceMembership.workspace_id == self.workspace_id,
                WorkspaceMembership.user_id.in_(uuids),
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )
        active_ids = {str(row[0]) for row in result.all()}
        missing = [item for item in candidate_ids if item not in active_ids]
        if missing:
            raise DistributionError(
                f"Candidatos sem membership ativa no workspace: {', '.join(missing)}"
            )

    # ── Atribuição ───────────────────────────────────────────────────────

    async def assign(
        self,
        legal_demand_id: UUID,
        *,
        trigger: DistributionTrigger = DistributionTrigger.regra_automatica,
        forced_user_id: UUID | None = None,
        reason: str | None = None,
        decided_by_user_id: UUID | None = None,
        at: datetime | None = None,
    ) -> DistributionDecision:
        """Atribui (ou reatribui) o responsável de uma demanda.

        `forced_user_id` pula a seleção por regra (redistribuição manual —
        `trigger` deve ser `redistribuicao_manual` e `reason` é obrigatório).
        Sem `forced_user_id`, procura a primeira regra ativa (menor
        `priority`) cujos critérios casam com a demanda e resolve um
        candidato elegível — considerando capacidade e, se
        `rule.respect_availability`, disponibilidade/substituição via
        `StaffAvailabilityService`.
        """
        instant = at or datetime.now(UTC)
        demand = await self._get_demand(legal_demand_id)

        rule: DistributionRule | None = None
        chosen_user_id: UUID
        auto_reason: str

        if forced_user_id is not None:
            if trigger == DistributionTrigger.regra_automatica:
                trigger = DistributionTrigger.redistribuicao_manual
            if not reason:
                raise DistributionReasonRequiredError(
                    "Redistribuição manual exige motivo explícito."
                )
            await self._assert_candidates_are_active_members([str(forced_user_id)])
            chosen_user_id = forced_user_id
            auto_reason = reason
        else:
            rule = await self._match_rule(demand)
            if rule is None:
                raise DistributionNoRuleMatchedError(
                    f"Nenhuma regra ativa casa com a demanda {legal_demand_id}."
                )
            chosen_user_id, layer_note = await self._select_candidate(rule, instant)
            auto_reason = reason or (
                f"Regra '{rule.name}' (estratégia={rule.strategy.value}): {layer_note}"
            )

        decision = DistributionDecision(
            workspace_id=self.workspace_id,
            legal_demand_id=demand.id,
            rule_id=rule.id if rule is not None else None,
            trigger=trigger,
            previous_user_id=demand.responsible_user_id,
            new_user_id=chosen_user_id,
            reason=auto_reason,
            decided_by_user_id=decided_by_user_id,
            decided_at=instant,
        )
        self.db.add(decision)
        demand.responsible_user_id = chosen_user_id
        await self.db.flush()
        return decision

    async def list_history(self, legal_demand_id: UUID) -> list[DistributionDecision]:
        result = await self.db.execute(
            select(DistributionDecision)
            .where(
                DistributionDecision.workspace_id == self.workspace_id,
                DistributionDecision.legal_demand_id == legal_demand_id,
            )
            .order_by(DistributionDecision.decided_at.desc())
        )
        return list(result.scalars().all())

    # ── Internos ─────────────────────────────────────────────────────────

    async def _get_demand(self, legal_demand_id: UUID) -> LegalDemand:
        demand = await self.db.scalar(
            select(LegalDemand).where(
                LegalDemand.id == legal_demand_id,
                LegalDemand.workspace_id == self.workspace_id,
            )
        )
        if demand is None:
            raise DistributionDemandNotFoundError(f"Demanda {legal_demand_id} não encontrada.")
        return demand

    async def _match_rule(self, demand: LegalDemand) -> DistributionRule | None:
        rules = await self.list_rules(active_only=True)
        for rule in rules:
            if self._rule_matches(rule, demand):
                return rule
        return None

    @staticmethod
    def _rule_matches(rule: DistributionRule, demand: LegalDemand) -> bool:
        def _matches(criteria: list, value: str | None) -> bool:
            if not criteria:
                return True
            return value is not None and value in criteria

        return (
            _matches(rule.match_natures, demand.nature.value)
            and _matches(rule.match_areas, demand.area)
            and _matches(rule.match_client_ids, str(demand.client_id))
            and _matches(rule.match_risks, demand.risk.value)
            and _matches(rule.match_units, demand.unit)
            and _matches(rule.match_teams, demand.team)
        )

    async def _active_assignment_counts(self, candidate_ids: list[str]) -> dict[str, int]:
        if not candidate_ids:
            return {}
        uuids = [UUID(str(item)) for item in candidate_ids]
        result = await self.db.execute(
            select(LegalDemand.responsible_user_id, func.count(LegalDemand.id))
            .where(
                LegalDemand.workspace_id == self.workspace_id,
                LegalDemand.responsible_user_id.in_(uuids),
                LegalDemand.phase.notin_(list(LEGAL_DEMAND_TERMINAL_PHASES)),
            )
            .group_by(LegalDemand.responsible_user_id)
        )
        return {str(row[0]): int(row[1]) for row in result.all()}

    async def _last_round_robin_user(self, rule_id: UUID) -> str | None:
        last = await self.db.scalar(
            select(DistributionDecision.new_user_id)
            .where(DistributionDecision.rule_id == rule_id)
            .order_by(DistributionDecision.decided_at.desc())
            .limit(1)
        )
        return str(last) if last else None

    async def _select_candidate(
        self, rule: DistributionRule, instant: datetime
    ) -> tuple[UUID, str]:
        candidates = [str(item) for item in rule.candidate_user_ids]
        if not candidates:
            raise DistributionNoAvailableCandidateError(
                f"Regra '{rule.name}' não tem candidatos configurados."
            )
        active_members = await self._assert_active_members_subset(candidates)
        counts = await self._active_assignment_counts(candidates)
        capacity = rule.max_active_per_candidate

        def _under_capacity(candidate_id: str) -> bool:
            if capacity is None:
                return True
            return counts.get(candidate_id, 0) < capacity

        ordered = self._ordered_by_strategy(rule, candidates, counts)
        if rule.strategy == DistributionStrategy.round_robin:
            last_user = await self._last_round_robin_user(rule.id)
            if last_user in ordered:
                start_index = ordered.index(last_user) + 1
                ordered = ordered[start_index:] + ordered[:start_index]

        tried_unavailable: list[str] = []
        for candidate_id in ordered:
            if candidate_id not in active_members:
                continue
            if not _under_capacity(candidate_id):
                continue
            if rule.respect_availability:
                resolution = await self.availability_service.resolve_responsibility(
                    workspace_id=self.workspace_id,
                    responsible_user_id=UUID(candidate_id),
                    at=instant,
                )
                # `resolve_responsibility` é fail-closed: quando não há
                # redirect ATIVO e aprovado, ela devolve o próprio
                # candidato (`effective_user_id == candidate_id`) mesmo
                # que ele esteja em ausência — NUNCA infere substituto.
                # Só aceitamos o candidato quando ele está realmente
                # disponível ("responsible_available") ou foi redirecionado
                # a um usuário concreto ("redirected_to_available_user").
                # Qualquer outro motivo (ausência sem redirect, redirect
                # para time, ciclo, limite de saltos) é tratado como
                # indisponível — tenta o próximo candidato da regra.
                if resolution.reason == "redirected_to_available_user" and resolution.effective_user_id is not None:
                    effective_id = str(resolution.effective_user_id)
                    return resolution.effective_user_id, (
                        f"candidato preferencial {candidate_id} em ausência, "
                        f"substituído por {effective_id} (redirect aprovado)"
                    )
                if resolution.reason != "responsible_available":
                    tried_unavailable.append(candidate_id)
                    continue
            note = (
                f"candidato {candidate_id} selecionado "
                f"(carga atual={counts.get(candidate_id, 0)})"
            )
            return UUID(candidate_id), note

        detail = f"em ausência sem substituto: {', '.join(tried_unavailable)}" if tried_unavailable else "sem candidatos configurados"
        raise DistributionNoAvailableCandidateError(
            f"Regra '{rule.name}': todos os candidatos estão indisponíveis ou na "
            f"capacidade máxima ({detail})."
        )

    async def _assert_active_members_subset(self, candidate_ids: list[str]) -> set[str]:
        uuids = [UUID(str(item)) for item in candidate_ids]
        result = await self.db.execute(
            select(WorkspaceMembership.user_id).where(
                WorkspaceMembership.workspace_id == self.workspace_id,
                WorkspaceMembership.user_id.in_(uuids),
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )
        return {str(row[0]) for row in result.all()}

    @staticmethod
    def _ordered_by_strategy(
        rule: DistributionRule, candidates: list[str], counts: dict[str, int]
    ) -> list[str]:
        # fixed/seniority/round_robin: a ordem de cadastro É a ordem de
        # prioridade (round_robin ainda roda o cursor por cima, ver
        # chamador). least_load ordena pela carga atual (menor primeiro),
        # com empate resolvido pela ordem cadastrada (sort estável).
        if rule.strategy == DistributionStrategy.least_load:
            return sorted(candidates, key=lambda candidate_id: counts.get(candidate_id, 0))
        return list(candidates)
