"""
Service: Growth CRM da plataforma — funil de aquisição B2B dos escritórios-clientes.

Staff-only (chamado só a partir de endpoints atrás de require_platform_root).
Não tem relação com o CRM que o tenant usa para os próprios clientes dele
(app/models/client.py) nem com WorkspaceSubscription/SubscriptionEvent (que
é a assinatura JÁ ativa). Aqui é o pipeline PRÉ-venda.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.auth_security import AuthSecurityEvent
from app.models.growth_crm import (
    LOST_STAGE,
    WON_STAGE,
    GrowthAcquisitionChannel,
    GrowthActivity,
    GrowthActivityType,
    GrowthFunnelStage,
    GrowthProspect,
    GrowthStageEvent,
)
from app.models.user import User
from app.models.workspace import WorkspaceSetupMode, WorkspaceStatus
from app.schemas.workspace import TeamMemberCreate, WorkspaceCreate
from app.services.identity_lifecycle_service import IdentityLifecycleService
from app.services.platform_service import PlatformService
from app.services.workspace_service import WorkspaceService

STAGE_ORDER: list[GrowthFunnelStage] = [
    GrowthFunnelStage.prospect,
    GrowthFunnelStage.contatado,
    GrowthFunnelStage.demo_agendada,
    GrowthFunnelStage.trial,
    GrowthFunnelStage.proposta,
    GrowthFunnelStage.pagante,
]


class GrowthProspectNotFoundError(Exception):
    pass


class GrowthInvalidStageTransitionError(Exception):
    pass


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class PlatformGrowthService:
    """CRUD de prospects, kanban do funil, atividades e métricas de conversão."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_sales_assignees(self) -> list[dict[str, str]]:
        rows = (await self.db.execute(
            select(User.id, User.name, User.platform_role_package)
            .where(
                User.deleted_at.is_(None),
                User.is_active.is_(True),
                User.platform_access_active.is_(True),
                or_(
                    User.is_platform_admin.is_(True),
                    User.platform_role_package.in_(("sales_sdr", "commercial_manager", "platform_admin")),
                ),
            )
            .order_by(User.name.asc())
        )).all()
        return [
            {"id": str(user_id), "name": name, "role_package": role_package or "platform_owner"}
            for user_id, name, role_package in rows
        ]

    async def _validate_sales_assignee(self, user_id: UUID) -> None:
        valid = await self.db.scalar(
            select(User.id).where(
                User.id == user_id,
                User.deleted_at.is_(None),
                User.is_active.is_(True),
                User.platform_access_active.is_(True),
                or_(
                    User.is_platform_admin.is_(True),
                    User.platform_role_package.in_(("sales_sdr", "commercial_manager", "platform_admin")),
                ),
            )
        )
        if valid is None:
            raise ValueError("O responsável precisa ser um funcionário comercial ativo da plataforma.")

    async def convert_to_workspace(
        self,
        prospect_id: UUID,
        *,
        workspace_name: str,
        slug: str | None,
        owner_name: str,
        owner_email: str,
        plan_key: str,
        setup_mode: WorkspaceSetupMode,
        actor: User,
        idempotency_key: str,
    ) -> dict[str, Any]:
        prospect = await self.db.scalar(
            select(GrowthProspect)
            .where(GrowthProspect.id == prospect_id)
            .with_for_update()
        )
        if prospect is None:
            raise GrowthProspectNotFoundError("Prospect não encontrado.")
        if prospect.workspace_id is not None:
            return {
                "prospect_id": str(prospect.id),
                "workspace_id": str(prospect.workspace_id),
                "already_converted": True,
            }
        if plan_key not in PlatformService.PLAN_KEY_TO_TIER:
            raise ValueError("Plano inicial inválido.")
        if len(owner_name.strip()) < 2 or "@" not in owner_email:
            raise ValueError("Nome e e-mail válidos do titular são obrigatórios.")

        workspace = await WorkspaceService(self.db).create_workspace(
            WorkspaceCreate(
                name=workspace_name.strip(),
                slug=slug.strip() if slug else None,
                description=f"Convertido do prospect {prospect.nome_escritorio}.",
                setup_mode=setup_mode,
                settings={
                    "platformLaunchNotes": prospect.notas,
                    "growthProspectId": str(prospect.id),
                    "acquisitionOrigin": self._enum_value(prospect.origem),
                },
            )
        )
        workspace.status = WorkspaceStatus.provisioning
        workspace.is_active = False
        invitation = await IdentityLifecycleService(self.db).create_invitation(
            workspace_id=workspace.id,
            actor_user_id=actor.id,
            payload=TeamMemberCreate(
                name=owner_name.strip(),
                email=owner_email,
                workspace_role="workspace_owner",
                access_package="office_owner",
            ),
            idempotency_key=idempotency_key,
            permission_grants=[],
            permission_denials=[],
            access_package="office_owner",
        )
        await PlatformService(self.db)._ensure_workspace_plan(
            workspace.id,
            plan_key,
            actor_user_id=actor.id,
            idempotency_key=f"{idempotency_key}:commercial-contract",
            reason="Plano inicial definido na conversão do prospect em conta.",
        )

        from_stage = prospect.estagio
        now = datetime.now(timezone.utc)
        prospect.workspace_id = workspace.id
        prospect.estagio = WON_STAGE
        prospect.stage_entered_at = now
        prospect.won_at = now
        prospect.lost_at = None
        prospect.motivo_perda = None
        self.db.add(GrowthStageEvent(
            prospect_id=prospect.id,
            from_estagio=from_stage,
            to_estagio=WON_STAGE,
            actor_user_id=actor.id,
            actor_label=actor.name,
            motivo=f"Convertido na conta {workspace.name}.",
        ))
        self.db.add(AuthSecurityEvent(
            event_type="platform_growth_prospect_converted",
            outcome="success",
            actor_user_id=actor.id,
            subject_user_id=invitation.user_id,
            details={
                "prospect_id": str(prospect.id),
                "workspace_id": str(workspace.id),
                "invitation_id": str(invitation.id),
                "plan_key": plan_key,
                "setup_mode": setup_mode.value,
            },
        ))
        await self.db.flush()
        return {
            "prospect_id": str(prospect.id),
            "workspace_id": str(workspace.id),
            "workspace_name": workspace.name,
            "workspace_status": workspace.status.value,
            "invitation_id": str(invitation.id),
            "invitation_status": invitation.status.value,
            "already_converted": False,
        }

    # ── Serialização ─────────────────────────────────────────────────────

    @staticmethod
    def _enum_value(value: Any) -> Any:
        return value.value if hasattr(value, "value") else value

    @staticmethod
    def _loaded_owner_name(prospect: GrowthProspect) -> Optional[str]:
        """Só acessa o relacionamento `owner` se já foi eager-carregado —
        evita lazy-load implícito fora do contexto async permitido."""
        from sqlalchemy import inspect as sa_inspect

        state = sa_inspect(prospect)
        if "owner" in state.unloaded:
            return None
        owner = prospect.owner
        return getattr(owner, "name", None) if owner else None

    def serialize_prospect(self, prospect: GrowthProspect, *, include_history: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": str(prospect.id),
            "nome_escritorio": prospect.nome_escritorio,
            "contato_nome": prospect.contato_nome,
            "contato_email": prospect.contato_email,
            "contato_telefone": prospect.contato_telefone,
            "cidade": prospect.cidade,
            "uf": prospect.uf,
            "tamanho_estimado": prospect.tamanho_estimado,
            "origem": self._enum_value(prospect.origem),
            "estagio": self._enum_value(prospect.estagio),
            "valor_estimado_cents": prospect.valor_estimado_cents,
            "valor_estimado_brl": round((prospect.valor_estimado_cents or 0) / 100, 2),
            "motivo_perda": prospect.motivo_perda,
            "notas": prospect.notas,
            "owner_user_id": str(prospect.owner_user_id) if prospect.owner_user_id else None,
            "owner_name": self._loaded_owner_name(prospect),
            "workspace_id": str(prospect.workspace_id) if prospect.workspace_id else None,
            "stage_entered_at": prospect.stage_entered_at.isoformat() if prospect.stage_entered_at else None,
            "won_at": prospect.won_at.isoformat() if prospect.won_at else None,
            "lost_at": prospect.lost_at.isoformat() if prospect.lost_at else None,
            "created_at": prospect.created_at.isoformat() if prospect.created_at else None,
            "updated_at": prospect.updated_at.isoformat() if prospect.updated_at else None,
        }
        commercial = dict(prospect.metadata_json or {})
        data.update({
            "score": commercial.get("score"),
            "next_action": commercial.get("next_action"),
            "next_action_at": commercial.get("next_action_at"),
            "campaign": commercial.get("campaign"),
            "utm_source": commercial.get("utm_source"),
            "utm_medium": commercial.get("utm_medium"),
            "utm_campaign": commercial.get("utm_campaign"),
            "proposal_status": commercial.get("proposal_status"),
            "proposal_reference": commercial.get("proposal_reference"),
            "proposal_value_cents": commercial.get("proposal_value_cents"),
        })
        if include_history:
            data["activities"] = [
                self.serialize_activity(activity) for activity in (prospect.activities or [])
            ]
            data["stage_events"] = [
                self.serialize_stage_event(event) for event in (prospect.stage_events or [])
            ]
        return data

    def serialize_activity(self, activity: GrowthActivity) -> dict[str, Any]:
        return {
            "id": str(activity.id),
            "prospect_id": str(activity.prospect_id),
            "tipo": self._enum_value(activity.tipo),
            "descricao": activity.descricao,
            "actor_user_id": str(activity.actor_user_id) if activity.actor_user_id else None,
            "actor_label": activity.actor_label,
            "occurred_at": activity.occurred_at.isoformat() if activity.occurred_at else None,
        }

    def serialize_stage_event(self, event: GrowthStageEvent) -> dict[str, Any]:
        return {
            "id": str(event.id),
            "prospect_id": str(event.prospect_id),
            "from_estagio": self._enum_value(event.from_estagio) if event.from_estagio else None,
            "to_estagio": self._enum_value(event.to_estagio),
            "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
            "actor_label": event.actor_label,
            "motivo": event.motivo,
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        }

    # ── Leitura ──────────────────────────────────────────────────────────

    async def _get_prospect(self, prospect_id: UUID) -> GrowthProspect:
        stmt = (
            select(GrowthProspect)
            .where(GrowthProspect.id == prospect_id)
            .options(
                selectinload(GrowthProspect.activities),
                selectinload(GrowthProspect.stage_events),
                selectinload(GrowthProspect.owner),
                selectinload(GrowthProspect.workspace),
            )
            .execution_options(populate_existing=True)
        )
        prospect = (await self.db.execute(stmt)).scalar_one_or_none()
        if prospect is None:
            raise GrowthProspectNotFoundError(f"Prospect {prospect_id} não encontrado.")
        return prospect

    async def get_prospect(self, prospect_id: UUID) -> GrowthProspect:
        return await self._get_prospect(prospect_id)

    async def list_prospects(
        self,
        *,
        estagio: Optional[str] = None,
        origem: Optional[str] = None,
        owner_user_id: Optional[UUID] = None,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        stmt = select(GrowthProspect).options(selectinload(GrowthProspect.owner))
        count_stmt = select(func.count(GrowthProspect.id))

        conditions = []
        if estagio:
            conditions.append(GrowthProspect.estagio == estagio)
        if origem:
            conditions.append(GrowthProspect.origem == origem)
        if owner_user_id:
            conditions.append(GrowthProspect.owner_user_id == owner_user_id)
        if search:
            like = f"%{search.strip()}%"
            conditions.append(
                or_(
                    GrowthProspect.nome_escritorio.ilike(like),
                    GrowthProspect.contato_nome.ilike(like),
                    GrowthProspect.contato_email.ilike(like),
                    GrowthProspect.cidade.ilike(like),
                )
            )
        for cond in conditions:
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)

        stmt = stmt.order_by(
            GrowthProspect.updated_at.desc(),
            GrowthProspect.id.desc(),
        ).limit(limit).offset(offset)
        items = (await self.db.execute(stmt)).scalars().all()
        total = (await self.db.execute(count_stmt)).scalar_one()
        return {"items": items, "count": total, "limit": limit, "offset": offset}

    async def get_pipeline(self) -> dict[str, list[GrowthProspect]]:
        """Kanban: prospects agrupados por estágio (perdido excluído das colunas ativas)."""
        stmt = (
            select(GrowthProspect)
            .options(selectinload(GrowthProspect.owner))
            .order_by(GrowthProspect.stage_entered_at.desc())
        )
        prospects = (await self.db.execute(stmt)).scalars().all()
        board: dict[str, list[GrowthProspect]] = {stage.value: [] for stage in GrowthFunnelStage}
        for prospect in prospects:
            stage_value = (
                prospect.estagio.value
                if hasattr(prospect.estagio, "value")
                else str(prospect.estagio)
            )
            board.setdefault(stage_value, []).append(prospect)
        return board

    async def get_funnel_metrics(self, *, period_days: int = 90) -> dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(days=period_days)

        all_stmt = select(GrowthProspect)
        prospects = (await self.db.execute(all_stmt)).scalars().all()

        by_stage: dict[str, dict[str, Any]] = {
            stage.value: {"count": 0, "valor_estimado_cents": 0} for stage in GrowthFunnelStage
        }
        by_origem: dict[str, int] = {}
        total = 0
        won = 0
        lost = 0
        pipeline_aberto_cents = 0

        for prospect in prospects:
            total += 1
            stage_value = (
                prospect.estagio.value
                if hasattr(prospect.estagio, "value")
                else str(prospect.estagio)
            )
            origem_value = (
                prospect.origem.value if hasattr(prospect.origem, "value") else str(prospect.origem)
            )
            by_stage.setdefault(stage_value, {"count": 0, "valor_estimado_cents": 0})
            by_stage[stage_value]["count"] += 1
            by_stage[stage_value]["valor_estimado_cents"] += prospect.valor_estimado_cents or 0
            by_origem[origem_value] = by_origem.get(origem_value, 0) + 1

            if prospect.estagio == WON_STAGE:
                won += 1
            elif prospect.estagio == LOST_STAGE:
                lost += 1
            else:
                pipeline_aberto_cents += prospect.valor_estimado_cents or 0

        decided = won + lost
        win_rate_pct = round((won / decided) * 100, 2) if decided else 0.0
        conversion_pct = round((won / total) * 100, 2) if total else 0.0

        avg_days_by_stage = await self._avg_days_per_stage(since=since)

        return {
            "total_prospects": total,
            "won": won,
            "lost": lost,
            "win_rate_pct": win_rate_pct,
            "conversion_prospect_to_pagante_pct": conversion_pct,
            "pipeline_estimado_cents": pipeline_aberto_cents,
            "pipeline_estimado_brl": round(pipeline_aberto_cents / 100, 2),
            "by_stage": by_stage,
            "by_origem": by_origem,
            "avg_days_per_stage": avg_days_by_stage,
            "period_days": period_days,
        }

    async def _avg_days_per_stage(self, *, since: datetime) -> dict[str, float]:
        """Tempo médio (dias) que um prospect passa em cada estágio, a partir dos
        stage_events consecutivos registrados desde `since`."""
        stmt = (
            select(GrowthStageEvent)
            .where(GrowthStageEvent.occurred_at >= since)
            .order_by(GrowthStageEvent.prospect_id, GrowthStageEvent.occurred_at)
        )
        events = (await self.db.execute(stmt)).scalars().all()

        durations: dict[str, list[float]] = {}
        by_prospect: dict[UUID, list[GrowthStageEvent]] = {}
        for event in events:
            by_prospect.setdefault(event.prospect_id, []).append(event)

        for prospect_events in by_prospect.values():
            for i in range(len(prospect_events) - 1):
                current = prospect_events[i]
                nxt = prospect_events[i + 1]
                stage_value = (
                    current.to_estagio.value
                    if hasattr(current.to_estagio, "value")
                    else str(current.to_estagio)
                )
                delta_days = (
                    _as_aware(nxt.occurred_at) - _as_aware(current.occurred_at)
                ).total_seconds() / 86400
                durations.setdefault(stage_value, []).append(max(delta_days, 0.0))

        return {
            stage: round(sum(values) / len(values), 2) for stage, values in durations.items() if values
        }

    # ── Escrita ──────────────────────────────────────────────────────────

    async def create_prospect(
        self,
        *,
        nome_escritorio: str,
        contato_nome: Optional[str] = None,
        contato_email: Optional[str] = None,
        contato_telefone: Optional[str] = None,
        cidade: Optional[str] = None,
        uf: Optional[str] = None,
        tamanho_estimado: Optional[str] = None,
        origem: str = GrowthAcquisitionChannel.outro.value,
        valor_estimado_cents: int = 0,
        owner_user_id: Optional[UUID] = None,
        notas: Optional[str] = None,
        commercial_metadata: Optional[dict[str, Any]] = None,
        actor: Optional[User] = None,
    ) -> GrowthProspect:
        if owner_user_id is not None:
            await self._validate_sales_assignee(owner_user_id)
        prospect = GrowthProspect(
            nome_escritorio=nome_escritorio.strip(),
            contato_nome=contato_nome,
            contato_email=contato_email,
            contato_telefone=contato_telefone,
            cidade=cidade,
            uf=uf.upper() if uf else None,
            tamanho_estimado=tamanho_estimado,
            origem=origem,
            estagio=GrowthFunnelStage.prospect,
            valor_estimado_cents=max(valor_estimado_cents, 0),
            owner_user_id=owner_user_id,
            notas=notas,
            metadata_json={key: value for key, value in (commercial_metadata or {}).items() if value is not None},
        )
        self.db.add(prospect)
        await self.db.flush()

        event = GrowthStageEvent(
            prospect_id=prospect.id,
            from_estagio=None,
            to_estagio=GrowthFunnelStage.prospect,
            actor_user_id=actor.id if actor else None,
            actor_label=actor.name if actor and getattr(actor, "name", None) else "sistema",
            motivo="Prospect criado.",
        )
        self.db.add(event)
        await self.db.flush()
        await self.db.refresh(prospect)
        return prospect

    async def update_prospect(
        self,
        prospect_id: UUID,
        *,
        fields: dict[str, Any],
    ) -> GrowthProspect:
        prospect = await self._get_prospect(prospect_id)
        if fields.get("owner_user_id") is not None:
            await self._validate_sales_assignee(fields["owner_user_id"])
        allowed = {
            "nome_escritorio",
            "contato_nome",
            "contato_email",
            "contato_telefone",
            "cidade",
            "uf",
            "tamanho_estimado",
            "origem",
            "valor_estimado_cents",
            "owner_user_id",
            "notas",
            "workspace_id",
        }
        nullable = {
            "contato_nome",
            "contato_email",
            "contato_telefone",
            "cidade",
            "uf",
            "tamanho_estimado",
            "owner_user_id",
            "notas",
            "workspace_id",
        }
        metadata_fields = {
            "score",
            "next_action",
            "next_action_at",
            "campaign",
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "proposal_status",
            "proposal_reference",
            "proposal_value_cents",
        }
        metadata = dict(prospect.metadata_json or {})
        for key, value in fields.items():
            if key in metadata_fields:
                if isinstance(value, datetime):
                    value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
                    metadata[key] = value.isoformat()
                else:
                    metadata[key] = value
            elif key in allowed and (value is not None or key in nullable):
                setattr(prospect, key, value)
        prospect.metadata_json = metadata
        await self.db.flush()
        await self.db.refresh(prospect)
        return prospect

    async def move_stage(
        self,
        prospect_id: UUID,
        *,
        to_estagio: str,
        motivo: str = "",
        actor: Optional[User] = None,
    ) -> GrowthProspect:
        prospect = await self._get_prospect(prospect_id)
        try:
            target = GrowthFunnelStage(to_estagio)
        except ValueError as error:
            raise GrowthInvalidStageTransitionError(
                f"Estágio inválido: {to_estagio}"
            ) from error

        from_estagio = prospect.estagio
        if from_estagio == target:
            return prospect

        normalized_reason = motivo.strip()
        if target == LOST_STAGE and len(normalized_reason) < 3:
            raise GrowthInvalidStageTransitionError(
                "Informe o motivo da perda antes de encerrar a oportunidade."
            )

        now = datetime.now(timezone.utc)
        prospect.estagio = target
        prospect.stage_entered_at = now
        if target == WON_STAGE:
            prospect.won_at = now
            prospect.lost_at = None
        elif target == LOST_STAGE:
            prospect.lost_at = now
            prospect.won_at = None
            prospect.motivo_perda = normalized_reason
        else:
            prospect.won_at = None
            prospect.lost_at = None
            prospect.motivo_perda = None

        event = GrowthStageEvent(
            prospect_id=prospect.id,
            from_estagio=from_estagio,
            to_estagio=target,
            actor_user_id=actor.id if actor else None,
            actor_label=actor.name if actor and getattr(actor, "name", None) else "sistema",
            motivo=normalized_reason,
        )
        self.db.add(event)
        await self.db.flush()
        await self.db.refresh(prospect)
        return prospect

    async def add_activity(
        self,
        prospect_id: UUID,
        *,
        tipo: str,
        descricao: str = "",
        actor: Optional[User] = None,
    ) -> GrowthActivity:
        prospect = await self._get_prospect(prospect_id)
        try:
            GrowthActivityType(tipo)
        except ValueError as error:
            raise GrowthInvalidStageTransitionError(f"Tipo de atividade inválido: {tipo}") from error

        activity = GrowthActivity(
            prospect_id=prospect.id,
            tipo=tipo,
            descricao=descricao,
            actor_user_id=actor.id if actor else None,
            actor_label=actor.name if actor and getattr(actor, "name", None) else "sistema",
        )
        self.db.add(activity)
        await self.db.flush()
        await self.db.refresh(activity)
        return activity


__all__ = [
    "PlatformGrowthService",
    "GrowthProspectNotFoundError",
    "GrowthInvalidStageTransitionError",
    "STAGE_ORDER",
]
