"""
Service: MatterService
Lógica de negócio para casos jurídicos (MatterCard).
Cada método representa um caso de uso isolado.
"""
import enum
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.legal_area_catalog import canonical_area_key, infer_product, route_for
from app.models.agent_run import AgentRun
from app.models.card_field_change import CardFieldChange, CardFieldChangeActorType
from app.models.campaign_attribution import CampaignAttribution
from app.models.client import Client
from app.models.conversation_insight import ConversationInsight
from app.models.finance import FinanceEntry, FinanceEntryStatus
from app.models.klm_operations import OmnichannelThread
from app.models.matter import Matter, KanbanColumnId, MicroStatus
from app.models.task import Task
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.matter import MatterCreate, MatterUpdate, MatterMoveRequest
from app.core.logging import logger


class MatterService:
    """Encapsula toda a lógica de negócio relacionada a casos jurídicos."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _routing_text(matter: Matter | MatterCreate) -> str:
        values = (
            getattr(matter, "demand_type", None),
            getattr(getattr(matter, "health_subtheme", None), "value", None),
            getattr(matter, "legal_product_custom", None),
            getattr(matter, "legal_object_name", None),
            getattr(matter, "legal_issue_summary", None),
            getattr(matter, "executive_summary", None),
            getattr(matter, "health_subtheme_custom", None),
            getattr(matter, "health_object_name", None),
            getattr(matter, "health_denial_reason", None),
        )
        return " | ".join(str(value).strip() for value in values if value)

    @classmethod
    def _resolved_legal_routing(cls, matter: Matter | MatterCreate) -> dict[str, Any]:
        practice_area = getattr(matter, "practice_area", None)
        practice_area_key = getattr(practice_area, "value", practice_area)
        area_key = canonical_area_key(
            getattr(matter, "legal_area_key", None) or practice_area_key
        )
        product_key = getattr(matter, "legal_product_key", None)
        health_subtheme = getattr(matter, "health_subtheme", None)
        health_product_key = getattr(health_subtheme, "value", health_subtheme)
        if not product_key and health_product_key:
            health_route = route_for(area_key, str(health_product_key))
            if health_route.get("productKey"):
                product_key = str(health_product_key)
        if not product_key:
            inferred = infer_product(area_key, cls._routing_text(matter))
            product_key = inferred["key"] if inferred else None
        route = route_for(area_key, product_key)
        if product_key and not route.get("productKey"):
            # Custom products remain routable through the area's default specialist.
            route["productKey"] = product_key
            route["productLabel"] = (
                getattr(matter, "legal_product_custom", None)
                or product_key.replace("_", " ").title()
            )
        return route

    async def _sync_legal_routing(
        self,
        matter: Matter,
        *,
        source: str,
    ) -> dict[str, Any]:
        route = self._resolved_legal_routing(matter)
        matter.legal_area_key = route["areaKey"]
        matter.legal_product_key = route.get("productKey")
        context = dict(matter.legal_context or {})
        context["legalRouting"] = {
            **route,
            "source": source,
            "resolvedAt": datetime.now(timezone.utc).isoformat(),
            "manualContextRevision": int(matter.manual_context_revision or 0),
        }
        matter.legal_context = context

        threads = list(
            (
                await self.db.scalars(
                    select(OmnichannelThread).where(
                        OmnichannelThread.workspace_id == matter.workspace_id,
                        OmnichannelThread.matter_id == matter.id,
                    )
                )
            ).all()
        )
        for thread in threads:
            metadata = dict(thread.thread_metadata or {})
            prior_auto_owner = metadata.get("autoOwnerAgentKey")
            manual_override = bool(metadata.get("manualOwnerAgentOverride"))
            next_auto_owner = route["sdrAgentKey"]
            if not manual_override and (
                thread.owner_agent_key is None
                or prior_auto_owner is None
                or thread.owner_agent_key == prior_auto_owner
            ):
                thread.owner_agent_key = next_auto_owner
            metadata["legalRouting"] = context["legalRouting"]
            metadata["autoOwnerAgentKey"] = next_auto_owner
            thread.thread_metadata = metadata
            thread.version = int(thread.version or 0) + 1
            thread.updated_at = datetime.now(timezone.utc)
        return route

    async def _validate_workspace_references(
        self,
        workspace_id: UUID,
        *,
        client_id: Optional[UUID] = None,
        responsible_human_id: Optional[UUID] = None,
        current_agent_id: Optional[UUID] = None,
    ) -> None:
        if client_id is not None:
            client_exists = await self.db.scalar(
                select(Client.id).where(
                    Client.id == client_id,
                    Client.workspace_id == workspace_id,
                    Client.deleted_at.is_(None),
                )
            )
            if not client_exists:
                raise ValueError("Cliente não pertence ao workspace ativo.")

        for field_label, user_id in (
            ("Responsável humano", responsible_human_id),
            ("Agente atual", current_agent_id),
        ):
            if user_id is None:
                continue
            membership_exists = await self.db.scalar(
                select(WorkspaceMembership.id).where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.user_id == user_id,
                    WorkspaceMembership.status == MembershipStatus.active,
                )
            )
            if not membership_exists:
                raise ValueError(f"{field_label} não pertence ao workspace ativo.")

    async def get_all(
        self,
        workspace_id: UUID,
        search: Optional[str] = None,
        include_sensitive_search: bool = True,
        column_id: Optional[str] = None,
        priority: Optional[str] = None,
        urgency: Optional[str] = None,
        micro_status: Optional[str] = None,
        classification: Optional[str] = None,
        lead_source: Optional[str] = None,
        is_blocked: Optional[bool] = None,
        needs_approval: Optional[bool] = None,
        agent_id: Optional[str] = None,
        doc_score_below: Optional[int] = None,
        close_intent: Optional[str] = None,
        document_readiness: Optional[str] = None,
        has_unread_messages: Optional[bool] = None,
        has_financial_pending: Optional[bool] = None,
        updated_within_days: Optional[int] = None,
        min_claim_value: Optional[float] = None,
        max_claim_value: Optional[float] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Matter]:
        """
        Lista casos com filtros opcionais.
        Suporta todos os filtros do AdvancedFilterBar do frontend:
          - search: busca por nome do cliente, tipo de demanda ou nº do processo
          - column_id: fase do Kanban
          - priority: alta | media | baixa
          - urgency: urgente | normal | baixa
          - micro_status: status detalhado dentro da fase
          - classification: Premium | Forte | Bom | Frio
          - is_blocked: filtrar bloqueados
          - needs_approval: filtrar com aprovação pendente
          - agent_id: filtrar por responsável humano ou agente atribuído
          - doc_score_below: filtrar por score de documentação abaixo de X%
        """
        from app.models.client import Client

        needs_insight_join = any([
            bool(search and include_sensitive_search),
            bool(close_intent),
            bool(document_readiness),
            has_financial_pending is not None,
        ])

        query = (
            select(Matter)
            .execution_options(populate_existing=True)
            .options(
                selectinload(Matter.client),
                selectinload(Matter.responsible_human),
                selectinload(Matter.current_agent),
                selectinload(Matter.card_score),
                selectinload(Matter.conversation_insight),
            )
            .where(Matter.deleted_at.is_(None), Matter.workspace_id == workspace_id)
            .order_by(Matter.updated_at.desc())
        )

        if needs_insight_join:
            query = query.outerjoin(Matter.conversation_insight)

        # Filtro de busca textual
        if search and include_sensitive_search:
            query = query.join(Matter.client).where(
                or_(
                    Client.name.ilike(f"%{search}%"),
                    Client.employer.ilike(f"%{search}%"),
                    Matter.demand_type.ilike(f"%{search}%"),
                    Matter.case_number.ilike(f"%{search}%"),
                    Matter.executive_summary.ilike(f"%{search}%"),
                    Matter.lead_source.ilike(f"%{search}%"),
                    ConversationInsight.summary.ilike(f"%{search}%"),
                )
            )
        elif search:
            query = query.where(
                or_(
                    Matter.demand_type.ilike(f"%{search}%"),
                    Matter.case_number.ilike(f"%{search}%"),
                    Matter.lead_source.ilike(f"%{search}%"),
                    Matter.health_object_name.ilike(f"%{search}%"),
                )
            )

        if column_id:
            query = query.where(Matter.column_id == column_id)
        if priority:
            query = query.where(Matter.priority == priority)
        if urgency:
            # Mapear aliases do frontend para valores do enum do banco
            # O banco usa: urgente | normal | baixa
            # O frontend pode enviar: alta | urgente | media | normal | baixa
            urgency_map = {
                "alta": "urgente",
                "urgente": "urgente",
                "media": "normal",
                "normal": "normal",
                "baixa": "baixa",
            }
            urgency_val = urgency_map.get(urgency.lower(), urgency)
            query = query.where(Matter.urgency == urgency_val)
        if micro_status:
            query = query.where(Matter.micro_status == micro_status)
        if classification:
            query = query.where(Matter.classification == classification)
        if lead_source:
            query = query.where(Matter.lead_source == lead_source)
        if is_blocked is not None:
            query = query.where(Matter.is_blocked == is_blocked)
        if needs_approval is not None:
            query = query.where(Matter.needs_approval == needs_approval)
        if doc_score_below is not None:
            query = query.where(Matter.doc_score < doc_score_below)
        if close_intent:
            query = query.where(ConversationInsight.close_intent == close_intent)
        if document_readiness:
            query = query.where(ConversationInsight.document_readiness == document_readiness)
        if has_unread_messages is not None:
            query = query.where(Matter.unread_messages > 0 if has_unread_messages else Matter.unread_messages == 0)
        if updated_within_days is not None:
            threshold = datetime.now(timezone.utc) - timedelta(days=updated_within_days)
            query = query.where(Matter.updated_at >= threshold)
        if min_claim_value is not None:
            query = query.where(Matter.claim_value >= min_claim_value)
        if max_claim_value is not None:
            query = query.where(Matter.claim_value <= max_claim_value)

        # Filtro por agente: busca no responsável humano, agente atual OU na lista de atribuídos
        if agent_id:
            query = query.where(
                or_(
                    Matter.responsible_human_id == agent_id,
                    Matter.current_agent_id == agent_id,
                    # JSON contains — funciona em PostgreSQL (JSONB) e SQLite (JSON)
                    Matter.assigned_agent_ids.contains([agent_id]),
                )
            )

        if has_financial_pending is not None:
            payment_phrase = or_(
                Matter.micro_status == MicroStatus.pagamento_pendente,
                Matter.next_action.ilike("%pagamento%"),
                ConversationInsight.recommended_action.ilike("%pagamento%"),
            )
            open_finance = Matter.finance_entries.any(
                FinanceEntry.status.in_([
                    FinanceEntryStatus.pending,
                    FinanceEntryStatus.scheduled,
                    FinanceEntryStatus.overdue,
                ])
            )
            if has_financial_pending:
                query = query.where(or_(payment_phrase, open_finance))
            else:
                query = query.where(
                    and_(
                        Matter.micro_status != MicroStatus.pagamento_pendente,
                        or_(Matter.next_action.is_(None), ~Matter.next_action.ilike("%pagamento%")),
                        or_(
                            ConversationInsight.recommended_action.is_(None),
                            ~ConversationInsight.recommended_action.ilike("%pagamento%"),
                        ),
                        ~open_finance,
                    )
                )

        query = query.offset(skip).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().unique().all())

    async def get_by_id(self, matter_id: UUID, workspace_id: UUID) -> Optional[Matter]:
        """Busca um caso completo com todos os dados aninhados."""
        query = (
            select(Matter)
            .options(
                selectinload(Matter.client),
                selectinload(Matter.responsible_human),
                selectinload(Matter.current_agent),
                selectinload(Matter.tasks).selectinload(Task.assignee),
                selectinload(Matter.tasks).selectinload(Task.participants),
                selectinload(Matter.tasks).selectinload(Task.review_rounds),
                selectinload(Matter.documents),
                selectinload(Matter.timeline_events).selectinload(
                    Matter.timeline_events.property.mapper.class_.agent
                ),
                selectinload(Matter.conversations),
                selectinload(Matter.approval_items),
                selectinload(Matter.campaign_attribution),
                selectinload(Matter.revenue_projection),
                selectinload(Matter.honorarium_estimate),
                selectinload(Matter.treatment_estimates).selectinload(
                    Matter.treatment_estimates.property.mapper.class_.medication
                ),
                selectinload(Matter.card_score),
                selectinload(Matter.field_changes),
                selectinload(Matter.agent_runs).selectinload(AgentRun.overrides),
                selectinload(Matter.human_overrides),
                selectinload(Matter.knowledge_chunks),
                selectinload(Matter.conversation_insight),
            )
            .where(Matter.id == matter_id, Matter.deleted_at.is_(None), Matter.workspace_id == workspace_id)
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def create(
        self,
        data: MatterCreate,
        workspace_id: UUID,
        created_by: Optional[UUID] = None,
    ) -> Matter:
        """Cria um novo caso e registra o evento na timeline."""
        await self._validate_workspace_references(
            workspace_id,
            client_id=data.client_id,
            responsible_human_id=data.responsible_human_id,
        )
        values = data.model_dump()
        route = self._resolved_legal_routing(data)
        values["legal_area_key"] = route["areaKey"]
        values["legal_product_key"] = route.get("productKey")
        values["legal_context"] = {
            **dict(values.get("legal_context") or {}),
            "legalRouting": {
                **route,
                "source": "matter_create",
                "resolvedAt": datetime.now(timezone.utc).isoformat(),
                "manualContextRevision": 0,
            },
        }
        matter = Matter(**values, workspace_id=workspace_id)
        self.db.add(matter)
        await self.db.flush()

        event = TimelineEvent(
            matter_id=matter.id,
            agent_id=created_by,
            event_type=TimelineEventType.status_change,
            title="Caso criado",
            description=f"Caso '{data.demand_type}' criado na fase '{data.column_id.value}'.",
        )
        self.db.add(event)
        await self._record_field_change(
            matter_id=matter.id,
            field_name="matter_created",
            old_value=None,
            new_value={
                "column_id": self._normalize_audit_value(data.column_id),
                "micro_status": self._normalize_audit_value(data.micro_status),
                "classification": self._normalize_audit_value(data.classification),
                "priority": self._normalize_audit_value(data.priority),
                "urgency": self._normalize_audit_value(data.urgency),
                "lead_source": self._normalize_audit_value(data.lead_source),
                "executive_summary": self._normalize_audit_value(data.executive_summary),
                "legal_area_key": route["areaKey"],
                "legal_product_key": route.get("productKey"),
                "legal_routing": route,
            },
            changed_by=created_by,
            summary="Card criado com dados iniciais do dossiê.",
            change_source="create",
        )
        await self.db.flush()

        logger.info("matter_created", matter_id=str(matter.id))
        # Recarregar com todos os relacionamentos via selectinload para evitar
        # MissingGreenlet ao serializar a resposta (lazy-load proibido em async)
        reloaded = await self.get_by_id(matter.id, workspace_id=workspace_id)
        return reloaded or matter

    async def update(
        self,
        matter_id: UUID,
        data: MatterUpdate,
        updated_by: Optional[UUID] = None,
        workspace_id: Optional[UUID] = None,
        change_source: str = "manual_api",
    ) -> Optional[Matter]:
        """Atualiza campos do caso e registra na timeline se relevante."""
        if workspace_id is None:
            return None
        matter = await self.get_by_id(matter_id, workspace_id)
        if not matter:
            return None

        update_values = data.model_dump(exclude_unset=True)
        await self._validate_workspace_references(
            workspace_id,
            responsible_human_id=update_values.get("responsible_human_id"),
            current_agent_id=update_values.get("current_agent_id"),
        )

        update_data = update_values
        changed_fields: list[str] = []
        for field, value in update_data.items():
            old_value = getattr(matter, field)
            if self._normalize_audit_value(old_value) == self._normalize_audit_value(value):
                continue
            setattr(matter, field, value)
            changed_fields.append(field)
            await self._record_field_change(
                matter_id=matter.id,
                field_name=field,
                old_value=old_value,
                new_value=value,
                changed_by=updated_by,
                summary=f"Campo '{field}' atualizado no dossiê por uma pessoa.",
                change_source=change_source[:120],
            )

        if changed_fields and change_source.startswith("manual"):
            matter.manual_context_revision = int(matter.manual_context_revision or 0) + 1
            matter.manual_context_updated_at = datetime.now(timezone.utc)
        routing_fields = {
            "practice_area",
            "legal_area_key",
            "legal_product_key",
            "legal_product_custom",
            "legal_object_name",
            "legal_issue_summary",
            "executive_summary",
            "demand_type",
            "health_subtheme",
            "health_subtheme_custom",
            "health_object_name",
            "health_denial_reason",
        }
        if routing_fields.intersection(changed_fields):
            await self._sync_legal_routing(matter, source=change_source)
        matter.updated_at = datetime.now(timezone.utc)
        await self.db.flush()

        logger.info("matter_updated", matter_id=str(matter_id), fields=list(update_data.keys()))
        return matter

    async def update_order(self, matter_id: UUID, new_order: int, workspace_id: UUID) -> Optional[Matter]:
        """
        Atualiza a posição (order) de um card dentro da coluna.
        Usado pelo frontend após drag-and-drop vertical.
        """
        matter = await self.db.get(Matter, matter_id)
        if not matter or matter.deleted_at or matter.workspace_id != workspace_id:
            return None
        matter.card_order = new_order
        matter.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        logger.info("matter_reordered", matter_id=str(matter_id), order=new_order)
        return matter

    async def record_manual_client_changes(
        self,
        *,
        workspace_id: UUID,
        client_id: UUID,
        changes: list[tuple[str, Any, Any]],
        updated_by: UUID,
        change_source: str = "manual_case_360",
    ) -> int:
        """Propagate client corrections to every linked matter context and audit trail."""
        if not changes:
            return 0
        matters = list(
            (
                await self.db.scalars(
                    select(Matter).where(
                        Matter.workspace_id == workspace_id,
                        Matter.client_id == client_id,
                        Matter.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        now = datetime.now(timezone.utc)
        for matter in matters:
            for field_name, old_value, new_value in changes:
                await self._record_field_change(
                    matter_id=matter.id,
                    field_name=f"client.{field_name}",
                    old_value=old_value,
                    new_value=new_value,
                    changed_by=updated_by,
                    summary=f"Dado do cliente '{field_name}' corrigido manualmente no Caso 360.",
                    change_source=change_source,
                )
            matter.manual_context_revision = int(matter.manual_context_revision or 0) + 1
            matter.manual_context_updated_at = now
            matter.updated_at = now
        await self.db.flush()
        return len(matters)

    async def update_manual_attribution(
        self,
        *,
        matter: Matter,
        values: dict[str, Any],
        updated_by: UUID,
        change_source: str = "manual_case_360",
    ) -> bool:
        """Apply an audited attribution correction without losing its prior value."""
        if not values:
            return False
        attribution = matter.campaign_attribution
        if attribution is None:
            attribution = CampaignAttribution(matter_id=matter.id)
            matter.campaign_attribution = attribution
            self.db.add(attribution)

        changed = False
        for field_name, value in values.items():
            normalized = (value.strip() or None) if isinstance(value, str) else value
            old_value = getattr(attribution, field_name)
            if self._normalize_audit_value(old_value) == self._normalize_audit_value(normalized):
                continue
            setattr(attribution, field_name, normalized)
            changed = True
            await self._record_field_change(
                matter_id=matter.id,
                field_name=f"campaign_attribution.{field_name}",
                old_value=old_value,
                new_value=normalized,
                changed_by=updated_by,
                summary=f"Atribuição '{field_name}' corrigida manualmente no Caso 360.",
                change_source=change_source,
            )

        if changed:
            now = datetime.now(timezone.utc)
            matter.manual_context_revision = int(matter.manual_context_revision or 0) + 1
            matter.manual_context_updated_at = now
            matter.updated_at = now
            await self.db.flush()
        return changed

    async def move_card(
        self,
        matter_id: UUID,
        move: MatterMoveRequest,
        moved_by: Optional[UUID] = None,
        workspace_id: Optional[UUID] = None,
    ) -> Optional[Matter]:
        """
        Move o card para uma nova coluna do Kanban.
        Registra o evento de movimentação na timeline.
        Chamado pelo frontend no drag-and-drop entre colunas.
        """
        if workspace_id is None:
            return None
        matter = await self.get_by_id(matter_id, workspace_id)
        if not matter:
            return None

        old_column = matter.column_id
        old_micro_status = matter.micro_status
        matter.column_id = move.column_id
        if move.micro_status:
            matter.micro_status = move.micro_status
        matter.updated_at = datetime.now(timezone.utc)

        event = TimelineEvent(
            matter_id=matter_id,
            agent_id=moved_by,
            event_type=TimelineEventType.card_moved,
            title=f"Card movido para '{move.column_id.value}'",
            description=f"Fase anterior: '{old_column.value}'",
            event_data={"from": old_column.value, "to": move.column_id.value},
        )
        self.db.add(event)
        await self._record_field_change(
            matter_id=matter.id,
            field_name="column_id",
            old_value=old_column,
            new_value=move.column_id,
            changed_by=moved_by,
            summary=f"Card movido de '{old_column.value}' para '{move.column_id.value}'.",
            change_source="move",
        )
        if move.micro_status and old_micro_status != move.micro_status:
            await self._record_field_change(
                matter_id=matter.id,
                field_name="micro_status",
                old_value=old_micro_status,
                new_value=move.micro_status,
                changed_by=moved_by,
                summary=f"Microstatus alterado para '{move.micro_status.value}'.",
                change_source="move",
            )
        if old_column != matter.column_id or old_micro_status != matter.micro_status:
            matter.manual_context_revision = int(matter.manual_context_revision or 0) + 1
            matter.manual_context_updated_at = datetime.now(timezone.utc)
        await self.db.flush()

        logger.info(
            "card_moved",
            matter_id=str(matter_id),
            from_col=old_column.value,
            to_col=move.column_id.value,
        )
        return matter

    async def add_note(
        self,
        matter_id: UUID,
        note: str,
        agent_id: Optional[UUID] = None,
        workspace_id: Optional[UUID] = None,
    ) -> Optional[TimelineEvent]:
        """Adiciona uma observação rápida na timeline do caso."""
        matter = await self.db.get(Matter, matter_id)
        if not matter or matter.deleted_at or (workspace_id and matter.workspace_id != workspace_id):
            return None

        event = TimelineEvent(
            matter_id=matter_id,
            agent_id=agent_id,
            event_type=TimelineEventType.comment,
            title="Observação adicionada",
            description=note,
        )
        self.db.add(event)
        matter.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return event

    async def soft_delete(self, matter_id: UUID, workspace_id: UUID) -> bool:
        """Soft delete de um caso."""
        matter = await self.db.get(Matter, matter_id)
        if not matter or matter.deleted_at or matter.workspace_id != workspace_id:
            return False
        matter.soft_delete()
        await self.db.flush()
        return True

    async def _record_field_change(
        self,
        matter_id: UUID,
        field_name: str,
        old_value: Any,
        new_value: Any,
        changed_by: Optional[UUID],
        summary: Optional[str],
        change_source: str,
    ) -> None:
        self.db.add(
            CardFieldChange(
                matter_id=matter_id,
                changed_by_user_id=changed_by,
                actor_type=CardFieldChangeActorType.human if changed_by else CardFieldChangeActorType.system,
                field_name=field_name,
                old_value={"value": self._normalize_audit_value(old_value)} if old_value is not None else None,
                new_value={"value": self._normalize_audit_value(new_value)} if new_value is not None else None,
                summary=summary,
                change_source=change_source,
            )
        )

    def _normalize_audit_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, enum.Enum):
            return value.value
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, list):
            return [self._normalize_audit_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self._normalize_audit_value(item) for key, item in value.items()}
        return value
