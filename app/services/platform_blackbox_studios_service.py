"""Composição nativa dos painéis de desempenho, relatórios e biblioteca master."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.configuration import PlatformConfigDocument
from app.models.growth_crm import GrowthProspect
from app.models.matter import Matter
from app.models.support_ticket import SupportTicket
from app.models.task import Task, TaskStatus
from app.models.user import AgentType, User
from app.models.workspace import Workspace
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.platform_blackbox_studios import (
    AdvancedReportRead,
    CreativeLibraryItemInput,
    CreativeLibraryItemRead,
    CreativeLibraryPageRead,
    CreativeLibraryRead,
    PerformanceBoardRead,
    PerformanceRow,
)
from app.services.configuration_service import ConfigurationService
from app.services.platform_metrics_service import PlatformMetricsService

CREATIVE_LIBRARY_NAMESPACE = "blackbox.creative_campaign_library"
_REDACTED_NON_ROOT_PREFIXES = (
    "ia.custo",
    "receita.cac",
    "receita.custo",
    "receita.lucro",
    "receita.margem",
    "receita.ebitda",
)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


class PlatformBlackboxStudiosService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.configurations = ConfigurationService(db)

    async def workspace_team_performance(
        self,
        *,
        period_days: int,
        workspace_id: UUID | None,
        limit: int = 25,
        offset: int = 0,
    ) -> PerformanceBoardRead:
        since = datetime.now(UTC) - timedelta(days=period_days)
        membership_query = (
            select(WorkspaceMembership, User, Workspace)
            .join(User, User.id == WorkspaceMembership.user_id)
            .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
            .where(
                WorkspaceMembership.status == MembershipStatus.active,
                User.agent_type == AgentType.human,
                User.is_active.is_(True),
                User.is_internal_staff.is_(False),
                User.deleted_at.is_(None),
                Workspace.deleted_at.is_(None),
            )
        )
        if workspace_id is not None:
            membership_query = membership_query.where(
                WorkspaceMembership.workspace_id == workspace_id
            )
        memberships = (await self.db.execute(membership_query)).all()

        matter_query = (
            select(
                Matter.workspace_id,
                Matter.responsible_human_id,
                func.count(Matter.id),
                func.avg(Matter.lead_score),
            )
            .where(
                Matter.created_at >= since,
                Matter.deleted_at.is_(None),
                Matter.responsible_human_id.is_not(None),
            )
            .group_by(Matter.workspace_id, Matter.responsible_human_id)
        )
        task_query = (
            select(
                Matter.workspace_id,
                Task.assigned_to,
                func.count(Task.id),
                func.sum(case((Task.status == TaskStatus.done, 1), else_=0)),
            )
            .join(Matter, Matter.id == Task.matter_id)
            .where(
                Task.created_at >= since,
                Task.assigned_to.is_not(None),
                Matter.deleted_at.is_(None),
            )
            .group_by(Matter.workspace_id, Task.assigned_to)
        )
        if workspace_id is not None:
            matter_query = matter_query.where(Matter.workspace_id == workspace_id)
            task_query = task_query.where(Matter.workspace_id == workspace_id)

        matters = {
            (str(row[0]), str(row[1])): (int(row[2] or 0), float(row[3] or 0))
            for row in (await self.db.execute(matter_query)).all()
        }
        tasks = {
            (str(row[0]), str(row[1])): (int(row[2] or 0), int(row[3] or 0))
            for row in (await self.db.execute(task_query)).all()
        }
        rows: list[PerformanceRow] = []
        for membership, user, workspace in memberships:
            key = (str(workspace.id), str(user.id))
            matter_count, average_score = matters.get(key, (0, 0.0))
            task_count, completed_count = tasks.get(key, (0, 0))
            rows.append(
                PerformanceRow(
                    id=f"{workspace.id}:{user.id}",
                    name=user.name,
                    scope_name=workspace.name,
                    role=_enum_value(membership.role),
                    primary_count=matter_count,
                    completed_count=completed_count,
                    pending_count=max(task_count - completed_count, 0),
                    quality_value=round(average_score, 1) if matter_count else None,
                    score=float(completed_count * 10 + matter_count * 2),
                )
            )
        rows.sort(key=lambda item: (-item.score, item.name.casefold()))
        total = len(rows)
        return PerformanceBoardRead(
            audience="workspace_team",
            period_days=period_days,
            generated_at=datetime.now(UTC),
            score_formula="10 pontos por tarefa concluída e 2 por caso atribuído no período.",
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + limit < total,
            rows=rows[offset : offset + limit],
        )

    async def saas_team_performance(
        self,
        *,
        period_days: int,
        limit: int = 25,
        offset: int = 0,
    ) -> PerformanceBoardRead:
        since = datetime.now(UTC) - timedelta(days=period_days)
        users = (
            await self.db.scalars(
                select(User).where(
                    User.agent_type == AgentType.human,
                    User.is_active.is_(True),
                    User.is_internal_staff.is_(True),
                    User.platform_access_active.is_(True),
                    User.deleted_at.is_(None),
                )
            )
        ).all()
        ticket_rows = (
            await self.db.execute(
                select(
                    SupportTicket.assigned_to_user_id,
                    func.sum(
                        case((SupportTicket.created_at >= since, 1), else_=0)
                    ),
                    func.sum(
                        case(
                            (SupportTicket.resolved_at >= since, 1),
                            else_=0,
                        )
                    ),
                    func.avg(
                        case(
                            (
                                SupportTicket.resolved_at >= since,
                                SupportTicket.satisfaction_score,
                            ),
                            else_=None,
                        )
                    ),
                )
                .where(
                    SupportTicket.assigned_to_user_id.is_not(None),
                    or_(
                        SupportTicket.created_at >= since,
                        SupportTicket.resolved_at >= since,
                    ),
                )
                .group_by(SupportTicket.assigned_to_user_id)
            )
        ).all()
        prospect_rows = (
            await self.db.execute(
                select(
                    GrowthProspect.owner_user_id,
                    func.sum(case((GrowthProspect.created_at >= since, 1), else_=0)),
                    func.sum(
                        case((GrowthProspect.won_at >= since, 1), else_=0)
                    ),
                )
                .where(
                    GrowthProspect.owner_user_id.is_not(None),
                    or_(
                        GrowthProspect.created_at >= since,
                        GrowthProspect.won_at >= since,
                    ),
                )
                .group_by(GrowthProspect.owner_user_id)
            )
        ).all()
        tickets = {
            str(row[0]): (int(row[1] or 0), int(row[2] or 0), float(row[3] or 0))
            for row in ticket_rows
        }
        prospects = {
            str(row[0]): (int(row[1] or 0), int(row[2] or 0))
            for row in prospect_rows
        }
        rows = []
        for user in users:
            ticket_count, resolved_count, satisfaction = tickets.get(
                str(user.id), (0, 0, 0.0)
            )
            prospect_count, won_count = prospects.get(str(user.id), (0, 0))
            rows.append(
                PerformanceRow(
                    id=str(user.id),
                    name=user.name,
                    scope_name="Equipe Meu Executivo",
                    role=user.platform_role_package,
                    primary_count=ticket_count + prospect_count,
                    completed_count=resolved_count + won_count,
                    pending_count=max(ticket_count - resolved_count, 0),
                    quality_value=round(satisfaction, 1) if satisfaction else None,
                    score=float(resolved_count * 8 + won_count * 12),
                )
            )
        rows.sort(key=lambda item: (-item.score, item.name.casefold()))
        total = len(rows)
        return PerformanceBoardRead(
            audience="saas_team",
            period_days=period_days,
            generated_at=datetime.now(UTC),
            score_formula="8 pontos por ticket resolvido e 12 por oportunidade convertida.",
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + limit < total,
            rows=rows[offset : offset + limit],
        )

    async def workspace_benchmark(
        self,
        *,
        period_days: int,
        limit: int = 25,
        offset: int = 0,
    ) -> PerformanceBoardRead:
        since = datetime.now(UTC) - timedelta(days=period_days)
        workspaces = (
            await self.db.scalars(
                select(Workspace).where(
                    Workspace.deleted_at.is_(None),
                    Workspace.is_active.is_(True),
                )
            )
        ).all()
        member_rows = (
            await self.db.execute(
                select(
                    WorkspaceMembership.workspace_id,
                    func.count(WorkspaceMembership.id),
                )
                .where(WorkspaceMembership.status == MembershipStatus.active)
                .group_by(WorkspaceMembership.workspace_id)
            )
        ).all()
        matter_rows = (
            await self.db.execute(
                select(
                    Matter.workspace_id,
                    func.count(Matter.id),
                    func.avg(Matter.lead_score),
                )
                .where(Matter.created_at >= since, Matter.deleted_at.is_(None))
                .group_by(Matter.workspace_id)
            )
        ).all()
        task_rows = (
            await self.db.execute(
                select(
                    Matter.workspace_id,
                    func.count(Task.id),
                    func.sum(case((Task.status == TaskStatus.done, 1), else_=0)),
                )
                .join(Matter, Matter.id == Task.matter_id)
                .where(Task.created_at >= since, Matter.deleted_at.is_(None))
                .group_by(Matter.workspace_id)
            )
        ).all()
        members = {str(row[0]): int(row[1] or 0) for row in member_rows}
        matters = {
            str(row[0]): (int(row[1] or 0), float(row[2] or 0))
            for row in matter_rows
        }
        tasks = {
            str(row[0]): (int(row[1] or 0), int(row[2] or 0))
            for row in task_rows
        }
        rows = []
        for workspace in workspaces:
            matter_count, average_score = matters.get(str(workspace.id), (0, 0.0))
            task_count, completed_count = tasks.get(str(workspace.id), (0, 0))
            rows.append(
                PerformanceRow(
                    id=str(workspace.id),
                    name=workspace.name,
                    scope_name="Benchmark entre escritórios",
                    role=f"{members.get(str(workspace.id), 0)} usuários ativos",
                    primary_count=matter_count,
                    completed_count=completed_count,
                    pending_count=max(task_count - completed_count, 0),
                    quality_value=round(average_score, 1) if matter_count else None,
                    score=float(completed_count * 10 + matter_count * 2),
                )
            )
        rows.sort(key=lambda item: (-item.score, item.name.casefold()))
        total = len(rows)
        return PerformanceBoardRead(
            audience="workspace_benchmark",
            period_days=period_days,
            generated_at=datetime.now(UTC),
            score_formula=(
                "10 pontos por tarefa concluída e 2 por caso criado; "
                "acesso exclusivo do titular."
            ),
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + limit < total,
            rows=rows[offset : offset + limit],
        )

    async def creative_library(self) -> CreativeLibraryRead:
        document = await self.db.scalar(
            select(PlatformConfigDocument).where(
                PlatformConfigDocument.namespace == CREATIVE_LIBRARY_NAMESPACE
            )
        )
        if document is None:
            return CreativeLibraryRead(current_version=0, items=[])
        return self._creative_read(document)

    async def creative_library_page(
        self,
        *,
        status_filter: str,
        query: str | None,
        limit: int,
        offset: int,
    ) -> CreativeLibraryPageRead:
        library = await self.creative_library()
        items = library.items
        if status_filter == "active":
            items = [item for item in items if item.status != "archived"]
        elif status_filter != "all":
            items = [item for item in items if item.status == status_filter]
        normalized_query = (query or "").strip().casefold()
        if normalized_query:
            items = [
                item
                for item in items
                if normalized_query
                in " ".join(
                    [
                        item.name,
                        item.objective,
                        item.kind,
                        item.channel,
                        *item.tags,
                    ]
                ).casefold()
            ]
        items.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        total = len(items)
        return CreativeLibraryPageRead(
            current_version=library.current_version,
            etag=library.etag,
            updated_at=library.updated_at,
            items=items[offset : offset + limit],
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + limit < total,
        )

    async def upsert_creative(
        self,
        *,
        data: CreativeLibraryItemInput,
        actor_user_id: UUID,
    ) -> CreativeLibraryRead:
        document = await self.db.scalar(
            select(PlatformConfigDocument).where(
                PlatformConfigDocument.namespace == CREATIVE_LIBRARY_NAMESPACE
            )
        )
        current_items = list((document.payload if document else {}).get("items") or [])
        now = datetime.now(UTC).isoformat()
        item_id = data.id or str(uuid4())
        serialized = {
            "id": item_id,
            "name": data.name.strip(),
            "kind": data.kind,
            "channel": data.channel,
            "objective": data.objective.strip(),
            "status": data.status,
            "tags": sorted({tag.strip()[:80] for tag in data.tags if tag.strip()}),
            "assetUrl": data.asset_url,
            "copyText": data.copy_text,
            "notes": data.notes,
            "createdAt": now,
            "updatedAt": now,
        }
        replaced = False
        for index, current in enumerate(current_items):
            if str(current.get("id")) != item_id:
                continue
            serialized["createdAt"] = current.get("createdAt") or now
            current_items[index] = serialized
            replaced = True
            break
        if not replaced:
            current_items.append(serialized)
        if len(current_items) > 500:
            raise ValueError("A biblioteca master atingiu o limite de 500 itens.")
        target = await self.configurations.write_platform(
            namespace=CREATIVE_LIBRARY_NAMESPACE,
            payload={"items": current_items},
            actor_user_id=actor_user_id,
            expected_etag=data.expected_etag,
            reason=f"{'Atualização' if replaced else 'Inclusão'} de item na biblioteca master",
        )
        await self.db.refresh(target)
        return self._creative_read(target)

    async def archive_creative(
        self,
        *,
        item_id: str,
        actor_user_id: UUID,
        expected_etag: str | None,
    ) -> CreativeLibraryRead:
        document = await self.db.scalar(
            select(PlatformConfigDocument).where(
                PlatformConfigDocument.namespace == CREATIVE_LIBRARY_NAMESPACE
            )
        )
        if document is None:
            raise LookupError("Biblioteca master ainda não foi criada.")
        # Não mutar o JSON rastreado pelo SQLAlchemy antes da escrita versionada.
        items = deepcopy(list(document.payload.get("items") or []))
        now = datetime.now(UTC).isoformat()
        found = False
        for item in items:
            if str(item.get("id")) == item_id:
                item["status"] = "archived"
                item["updatedAt"] = now
                found = True
                break
        if not found:
            raise LookupError("Criativo ou campanha não encontrado.")
        target = await self.configurations.write_platform(
            namespace=CREATIVE_LIBRARY_NAMESPACE,
            payload={"items": items},
            actor_user_id=actor_user_id,
            expected_etag=expected_etag,
            reason=f"Arquivamento do item {item_id} na biblioteca master",
        )
        await self.db.refresh(target)
        return self._creative_read(target)

    async def advanced_report(
        self,
        *,
        period_days: int,
        category: str | None,
        query: str | None,
        only_with_data: bool,
        sort_by: str,
        sort_direction: str,
        limit: int,
        offset: int,
        include_financial: bool,
        include_sensitive: bool,
    ) -> AdvancedReportRead:
        # Preserve saved links and filters created before the KPI taxonomy was
        # consolidated into the 17 canonical categories.
        category = {
            "assinantes": "assinaturas",
            "uso_engajamento": "produto",
            "operacao_ia": "ia",
            "saude": "sucesso_cliente",
        }.get(category or "", category)
        payload = await PlatformMetricsService(self.db).get_all_metrics(
            period_days=period_days
        )
        categories = [
            {"key": str(item["key"]), "label": str(item["label"])}
            for item in payload["categories"]
            if include_financial or str(item["key"]) != "receita"
        ]
        lowered_query = (query or "").strip().casefold()
        rows: list[dict[str, Any]] = []
        for group in payload["categories"]:
            if not include_financial and group["key"] == "receita":
                continue
            if category and group["key"] != category:
                continue
            for metric in group["kpis"]:
                key = str(metric["key"])
                if not include_sensitive and key.startswith(_REDACTED_NON_ROOT_PREFIXES):
                    continue
                label = str(metric["label"])
                if lowered_query and lowered_query not in f"{key} {label}".casefold():
                    continue
                has_data = not bool(metric.get("sem_dado"))
                if only_with_data and not has_data:
                    continue
                series = [
                    {"date": str(point["date"]), "value": float(point["value"])}
                    for point in metric.get("series") or []
                    if isinstance(point, dict)
                    and point.get("date") is not None
                    and isinstance(point.get("value"), int | float)
                ]
                rows.append(
                    {
                        "key": key,
                        "label": label,
                        "category": str(group["key"]),
                        "categoryLabel": str(group["label"]),
                        "value": metric.get("value"),
                        "unit": metric.get("unit"),
                        "deltaPct": metric.get("delta_pct"),
                        "hasData": has_data,
                        "note": metric.get("nota"),
                        "series": series,
                    }
                )
        reverse = sort_direction == "desc"
        if sort_by == "value":
            rows.sort(
                key=lambda item: (
                    isinstance(item["value"], int | float),
                    float(item["value"])
                    if isinstance(item["value"], int | float)
                    else 0,
                ),
                reverse=reverse,
            )
        elif sort_by == "category":
            rows.sort(
                key=lambda item: (item["categoryLabel"].casefold(), item["label"].casefold()),
                reverse=reverse,
            )
        else:
            rows.sort(key=lambda item: item["label"].casefold(), reverse=reverse)
        total_rows = len(rows)
        rows_with_data = sum(1 for item in rows if item["hasData"])
        rows = rows[offset:offset + limit]
        chart_rows = [
            {
                "key": item["key"],
                "label": item["label"],
                "value": float(item["value"]),
                "category": item["categoryLabel"],
            }
            for item in rows
            if isinstance(item["value"], int | float)
        ][:20]
        trend_rows = [
            {
                "metricKey": item["key"],
                "metricLabel": item["label"],
                **point,
            }
            for item in rows
            for point in item["series"]
        ][:1_000]
        return AdvancedReportRead(
            generated_at=datetime.now(UTC),
            period_days=period_days,
            available_categories=categories,
            applied_filters={
                "category": category,
                "query": query,
                "onlyWithData": only_with_data,
                "sortBy": sort_by,
                "sortDirection": sort_direction,
                "limit": limit,
                "offset": offset,
            },
            total_rows=total_rows,
            rows_with_data=rows_with_data,
            limit=limit,
            offset=offset,
            has_more=offset + len(rows) < total_rows,
            rows=rows,
            chart_rows=chart_rows,
            trend_rows=trend_rows,
        )

    @staticmethod
    def _creative_read(document: PlatformConfigDocument) -> CreativeLibraryRead:
        return CreativeLibraryRead(
            current_version=document.current_version,
            etag=document.etag,
            updated_at=document.updated_at,
            items=[
                CreativeLibraryItemRead.model_validate(item)
                for item in document.payload.get("items") or []
            ],
        )
