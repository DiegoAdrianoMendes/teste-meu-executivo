"""Relatórios avançados tenant-aware para a operação do escritório.

O catálogo é derivado das entidades canônicas do workspace. Contagens iguais a
zero são dados válidos; taxas sem denominador permanecem sem valor, em vez de
serem substituídas por estimativas.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Callable, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.legal_area_catalog import LEGAL_AREA_CATALOG, canonical_area_key
from app.models.campaign_attribution import CampaignAttribution
from app.models.document import Document, DocumentStatus
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.flow import FlowRun
from app.models.klm_operations import (
    AcquisitionEvent,
    FollowUpStatus,
    FollowUpTask,
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelMessage,
    OmnichannelThread,
    ThreadStatus,
)
from app.models.matter import KanbanColumnId, Matter, Priority, Urgency
from app.models.meeting import Meeting, MeetingStatus
from app.models.revenue_projection import RevenueProjection
from app.models.task import Task, TaskStatus
from app.models.user import User
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership


CATEGORIES: tuple[tuple[str, str], ...] = (
    ("leads", "Leads e qualificação"),
    ("funnel", "Funil e conversão"),
    ("service", "Conversas e atendimento"),
    ("acquisition", "Aquisição e atribuição"),
    ("team", "Equipe e produtividade"),
    ("documents", "Documentos e RAG"),
    ("meetings", "Agenda e reuniões"),
    ("finance", "Receita e cobrança"),
    ("flows", "Flows e follow-ups"),
    ("quality", "Qualidade e risco"),
    ("legal_areas", "Áreas jurídicas"),
    ("legal_products", "Produtos jurídicos"),
)
CATEGORY_LABELS = dict(CATEGORIES)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _number(value: Any) -> float:
    return float(value or 0)


def _average(values: Iterable[float]) -> float | None:
    rows = list(values)
    return round(sum(rows) / len(rows), 2) if rows else None


def _rate(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator * 100, 2) if denominator else None


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    rows = sorted(values)
    if not rows:
        return None
    index = (len(rows) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(rows) - 1)
    weight = index - lower
    return round(rows[lower] * (1 - weight) + rows[upper] * weight, 2)


def _delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / abs(previous) * 100, 2)


def _metric_polarity(key: str) -> str:
    """Informa se crescer tende a ser bom, ruim ou apenas descritivo."""
    negative_markers = (
        "blocked",
        "cancelled",
        "failed",
        "failure",
        "lost",
        "no_show",
        "overdue",
        "pending",
        "p90",
        "response.",
        "retry",
        "suppressed",
        "unread",
    )
    positive_markers = (
        "attribution_coverage",
        "collection_rate",
        "completed",
        "coverage",
        "delivered",
        "qualified",
        "read",
        "score",
        "show_rate",
        "won",
    )
    if any(marker in key for marker in negative_markers):
        return "negative"
    if any(marker in key for marker in positive_markers):
        return "positive"
    return "neutral"


def _moment(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class WorkspaceAnalyticsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def advanced_report(
        self,
        *,
        workspace_id: UUID,
        period_days: int,
        legal_area_key: str | None,
        legal_product_key: str | None,
        channel: str | None,
        responsible_user_id: UUID | None,
        stage: str | None,
        category: str | None,
        query: str | None,
        only_with_data: bool,
        sort_by: str,
        sort_direction: str,
        limit: int,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        current_start = now - timedelta(days=period_days)
        previous_start = current_start - timedelta(days=period_days)
        canonical_area = canonical_area_key(legal_area_key)

        matters = list(
            (
                await self.db.scalars(
                    select(Matter).where(
                        Matter.workspace_id == workspace_id,
                        Matter.deleted_at.is_(None),
                        Matter.created_at >= previous_start,
                        Matter.created_at <= now,
                    )
                )
            ).all()
        )
        matter_ids = [item.id for item in matters]
        attributions = list(
            (
                await self.db.scalars(
                    select(CampaignAttribution).where(
                        CampaignAttribution.matter_id.in_(matter_ids)
                    )
                )
            ).all()
        ) if matter_ids else []
        attribution_by_matter = {item.matter_id: item for item in attributions}

        threads = list(
            (
                await self.db.scalars(
                    select(OmnichannelThread).where(
                        OmnichannelThread.workspace_id == workspace_id
                    )
                )
            ).all()
        )
        thread_channels: dict[UUID, set[str]] = defaultdict(set)
        for thread in threads:
            if thread.matter_id:
                thread_channels[thread.matter_id].add(_enum_value(thread.primary_channel))

        def area_of(matter: Matter) -> str:
            return canonical_area_key(
                matter.legal_area_key or _enum_value(matter.practice_area)
            ) or "nao_classificada"

        def product_of(matter: Matter) -> str:
            if matter.legal_product_key:
                return matter.legal_product_key
            if matter.health_subtheme:
                return _enum_value(matter.health_subtheme)
            return "nao_classificado"

        def channels_of(matter: Matter) -> set[str]:
            values = set(thread_channels.get(matter.id, set()))
            attribution = attribution_by_matter.get(matter.id)
            if attribution:
                values.update(
                    str(value).strip().casefold()
                    for value in (attribution.channel, attribution.source)
                    if value
                )
            if matter.lead_source:
                values.add(matter.lead_source.strip().casefold())
            return values or {"nao_informado"}

        def matches_scope(matter: Matter) -> bool:
            if canonical_area and area_of(matter) != canonical_area:
                return False
            if legal_product_key and product_of(matter) != legal_product_key:
                return False
            if responsible_user_id and matter.responsible_human_id != responsible_user_id:
                return False
            if stage and _enum_value(matter.column_id) != stage:
                return False
            if channel and channel.casefold() not in channels_of(matter):
                return False
            return True

        scoped_matters = [item for item in matters if matches_scope(item)]
        current_matters = [
            item for item in scoped_matters if (_moment(item.created_at) or now) >= current_start
        ]
        previous_matters = [
            item
            for item in scoped_matters
            if previous_start <= (_moment(item.created_at) or now) < current_start
        ]
        current_ids = {item.id for item in current_matters}
        previous_ids = {item.id for item in previous_matters}
        scoped_ids = current_ids | previous_ids
        dimension_scoped = any(
            value is not None
            for value in (
                canonical_area,
                legal_product_key,
                channel,
                responsible_user_id,
                stage,
            )
        )

        messages = list(
            (
                await self.db.scalars(
                    select(OmnichannelMessage).where(
                        OmnichannelMessage.workspace_id == workspace_id,
                        OmnichannelMessage.occurred_at >= previous_start,
                        OmnichannelMessage.occurred_at <= now,
                    )
                )
            ).all()
        )
        if dimension_scoped:
            messages = [item for item in messages if item.matter_id in scoped_ids]
        if channel:
            messages = [
                item for item in messages if _enum_value(item.channel).casefold() == channel.casefold()
            ]
        current_messages, previous_messages = self._split_by_moment(
            messages, current_start, lambda item: item.occurred_at
        )

        meetings = await self._workspace_rows(
            Meeting,
            Meeting.workspace_id == workspace_id,
            Meeting.starts_at >= previous_start,
            Meeting.starts_at <= now,
        )
        if dimension_scoped:
            meetings = [item for item in meetings if item.matter_id in scoped_ids]
        current_meetings, previous_meetings = self._split_by_moment(
            meetings, current_start, lambda item: item.starts_at
        )

        finances = await self._workspace_rows(
            FinanceEntry,
            FinanceEntry.workspace_id == workspace_id,
            FinanceEntry.deleted_at.is_(None),
            FinanceEntry.created_at >= previous_start,
            FinanceEntry.created_at <= now,
        )
        if dimension_scoped:
            finances = [item for item in finances if item.matter_id in scoped_ids]
        current_finances, previous_finances = self._split_by_moment(
            finances, current_start, lambda item: item.created_at
        )

        acquisitions = await self._workspace_rows(
            AcquisitionEvent,
            AcquisitionEvent.workspace_id == workspace_id,
            AcquisitionEvent.happened_at >= previous_start,
            AcquisitionEvent.happened_at <= now,
        )
        if dimension_scoped:
            acquisitions = [item for item in acquisitions if item.matter_id in scoped_ids]
        if channel:
            acquisitions = [
                item for item in acquisitions if item.channel.casefold() == channel.casefold()
            ]
        current_acquisitions, previous_acquisitions = self._split_by_moment(
            acquisitions, current_start, lambda item: item.happened_at
        )

        follow_ups = await self._workspace_rows(
            FollowUpTask,
            FollowUpTask.workspace_id == workspace_id,
            FollowUpTask.created_at >= previous_start,
            FollowUpTask.created_at <= now,
        )
        if dimension_scoped:
            follow_ups = [item for item in follow_ups if item.matter_id in scoped_ids]
        current_follow_ups, previous_follow_ups = self._split_by_moment(
            follow_ups, current_start, lambda item: item.created_at
        )

        flow_runs = await self._workspace_rows(
            FlowRun,
            FlowRun.workspace_id == workspace_id,
            FlowRun.started_at >= previous_start,
            FlowRun.started_at <= now,
        )
        if dimension_scoped:
            flow_runs = [item for item in flow_runs if item.matter_id in scoped_ids]
        current_flow_runs, previous_flow_runs = self._split_by_moment(
            flow_runs, current_start, lambda item: item.started_at
        )

        documents = await self._matter_rows(
            Document,
            workspace_id=workspace_id,
            matter_ids=scoped_ids,
            period_start=previous_start,
            period_end=now,
        )
        tasks = await self._matter_rows(
            Task,
            workspace_id=workspace_id,
            matter_ids=scoped_ids,
            period_start=previous_start,
            period_end=now,
        )
        projections = await self._matter_rows(
            RevenueProjection,
            workspace_id=workspace_id,
            matter_ids=scoped_ids,
            period_start=previous_start,
            period_end=now,
        )
        current_documents, previous_documents = self._split_by_moment(
            documents, current_start, lambda item: item.created_at
        )
        current_tasks, previous_tasks = self._split_by_moment(
            tasks, current_start, lambda item: item.created_at
        )
        current_projections, previous_projections = self._split_by_moment(
            projections, current_start, lambda item: item.created_at
        )

        rows: list[dict[str, Any]] = []

        def add(
            key: str,
            label: str,
            category_key: str,
            value: float | int | None,
            previous_value: float | int | None,
            unit: str,
            description: str,
            source: str,
            *,
            dimensions: dict[str, str] | None = None,
            note: str | None = None,
        ) -> None:
            current_number = round(float(value), 2) if value is not None else None
            previous_number = (
                round(float(previous_value), 2) if previous_value is not None else None
            )
            rows.append(
                {
                    "key": key,
                    "label": label,
                    "category": category_key,
                    "category_label": CATEGORY_LABELS[category_key],
                    "value": current_number,
                    "previous_value": previous_number,
                    "delta_pct": _delta(current_number, previous_number),
                    "polarity": _metric_polarity(key),
                    "unit": unit,
                    "has_data": current_number is not None,
                    "description": description,
                    "source": source,
                    "dimensions": dimensions or {},
                    "note": note,
                }
            )

        self._add_global_metrics(
            add=add,
            current_matters=current_matters,
            previous_matters=previous_matters,
            current_messages=current_messages,
            previous_messages=previous_messages,
            current_meetings=current_meetings,
            previous_meetings=previous_meetings,
            current_finances=current_finances,
            previous_finances=previous_finances,
            current_documents=current_documents,
            previous_documents=previous_documents,
            current_tasks=current_tasks,
            previous_tasks=previous_tasks,
            current_projections=current_projections,
            previous_projections=previous_projections,
            current_acquisitions=current_acquisitions,
            previous_acquisitions=previous_acquisitions,
            current_follow_ups=current_follow_ups,
            previous_follow_ups=previous_follow_ups,
            current_flow_runs=current_flow_runs,
            previous_flow_runs=previous_flow_runs,
            threads=[
                item
                for item in threads
                if not dimension_scoped or item.matter_id in current_ids
            ],
            now=now,
        )
        self._add_dimension_metrics(
            add=add,
            current_matters=current_matters,
            previous_matters=previous_matters,
            area_of=area_of,
            product_of=product_of,
            channels_of=channels_of,
            canonical_area=canonical_area,
        )

        catalog_total = len(rows)
        lowered = (query or "").strip().casefold()
        matched_rows = [
            item
            for item in rows
            if (not category or item["category"] == category)
            and (
                not lowered
                or lowered
                in " ".join(
                    (
                        item["key"],
                        item["label"],
                        item["category_label"],
                        item["description"],
                    )
                ).casefold()
            )
        ]
        coverage_total = len(matched_rows)
        coverage_with_data = sum(1 for item in matched_rows if item["has_data"])
        filtered_rows = [
            item for item in matched_rows if not only_with_data or item["has_data"]
        ]
        reverse = sort_direction == "desc"
        if sort_by == "value":
            filtered_rows.sort(
                key=lambda item: (
                    item["value"] is not None,
                    item["value"] if item["value"] is not None else 0,
                ),
                reverse=reverse,
            )
        elif sort_by == "category":
            filtered_rows.sort(
                key=lambda item: (item["category_label"].casefold(), item["label"].casefold()),
                reverse=reverse,
            )
        else:
            filtered_rows.sort(key=lambda item: item["label"].casefold(), reverse=reverse)
        total_rows = len(filtered_rows)
        rows_with_data = sum(1 for item in filtered_rows if item["has_data"])
        truncated = total_rows > limit
        filtered_rows = filtered_rows[:limit]

        category_rows = []
        for key, label in CATEGORIES:
            category_metrics = [
                item for item in matched_rows if item["category"] == key
            ]
            category_rows.append(
                {
                    "key": key,
                    "label": label,
                    "metric_count": len(category_metrics),
                    "metrics_with_data": sum(
                        1 for item in category_metrics if item["has_data"]
                    ),
                }
            )

        responsible_ids = {
            item.responsible_human_id
            for item in matters
            if item.responsible_human_id is not None
        }
        users = list(
            (
                await self.db.scalars(
                    select(User)
                    .join(
                        WorkspaceMembership,
                        WorkspaceMembership.user_id == User.id,
                    )
                    .where(
                        User.id.in_(responsible_ids),
                        WorkspaceMembership.workspace_id == workspace_id,
                        WorkspaceMembership.status == MembershipStatus.active,
                    )
                )
            ).all()
        ) if responsible_ids else []
        observed_channels = sorted(
            {
                value
                for matter in matters
                for value in channels_of(matter)
                if value != "nao_informado"
            }
            | {_enum_value(item.primary_channel) for item in threads}
        )
        product_options = [
            {
                "value": product["key"],
                "label": product["label"],
                "parent_value": area["areaKey"],
            }
            for area in LEGAL_AREA_CATALOG
            for product in area["products"]
            if not canonical_area or area["areaKey"] == canonical_area
        ]
        return {
            "generated_at": now,
            "period_days": period_days,
            "previous_period_days": period_days,
            "catalog_total": catalog_total,
            "total_rows": total_rows,
            "rows_with_data": rows_with_data,
            "coverage_total": coverage_total,
            "coverage_with_data": coverage_with_data,
            "truncated": truncated,
            "applied_filters": {
                "legalAreaKey": canonical_area,
                "legalProductKey": legal_product_key,
                "channel": channel,
                "responsibleUserId": str(responsible_user_id)
                if responsible_user_id
                else None,
                "stage": stage,
                "category": category,
                "query": query,
                "onlyWithData": only_with_data,
            },
            "categories": category_rows,
            "options": {
                "legalAreas": [
                    {"value": area["areaKey"], "label": area["name"]}
                    for area in LEGAL_AREA_CATALOG
                ],
                "legalProducts": product_options,
                "channels": [
                    {"value": value, "label": value.replace("_", " ").title()}
                    for value in observed_channels
                ],
                "responsibleUsers": [
                    {"value": str(user.id), "label": user.name} for user in users
                ],
                "stages": [
                    {
                        "value": item.value,
                        "label": item.value.replace("_", " ").title(),
                    }
                    for item in KanbanColumnId
                ],
            },
            "rows": filtered_rows,
        }

    async def _workspace_rows(self, model: Any, *filters: Any) -> list[Any]:
        return list((await self.db.scalars(select(model).where(*filters))).all())

    async def _matter_rows(
        self,
        model: Any,
        *,
        workspace_id: UUID,
        matter_ids: set[UUID],
        period_start: datetime,
        period_end: datetime,
    ) -> list[Any]:
        if not matter_ids:
            return []
        return list(
            (
                await self.db.scalars(
                    select(model)
                    .join(Matter, model.matter_id == Matter.id)
                    .where(
                        Matter.workspace_id == workspace_id,
                        Matter.deleted_at.is_(None),
                        model.matter_id.in_(matter_ids),
                        model.created_at >= period_start,
                        model.created_at <= period_end,
                    )
                )
            ).all()
        )

    @staticmethod
    def _split_by_moment(
        rows: Iterable[Any],
        current_start: datetime,
        moment_getter: Callable[[Any], datetime],
    ) -> tuple[list[Any], list[Any]]:
        current: list[Any] = []
        previous: list[Any] = []
        for item in rows:
            value = _moment(moment_getter(item))
            (current if value and value >= current_start else previous).append(item)
        return current, previous

    @staticmethod
    def _response_times(messages: list[OmnichannelMessage]) -> list[float]:
        pending: dict[UUID, datetime] = {}
        values: list[float] = []
        for message in sorted(messages, key=lambda item: item.occurred_at):
            occurred_at = _moment(message.occurred_at)
            if occurred_at is None:
                continue
            if message.direction == MessageDirection.inbound:
                pending.setdefault(message.thread_id, occurred_at)
            elif message.direction == MessageDirection.outbound:
                inbound_at = pending.pop(message.thread_id, None)
                if inbound_at and occurred_at >= inbound_at:
                    values.append((occurred_at - inbound_at).total_seconds())
        return values

    def _add_global_metrics(self, *, add: Callable[..., None], **data: Any) -> None:
        current_matters: list[Matter] = data["current_matters"]
        previous_matters: list[Matter] = data["previous_matters"]

        def count_matters(rows: list[Matter], predicate: Callable[[Matter], bool]) -> int:
            return sum(1 for item in rows if predicate(item))

        current_total = len(current_matters)
        previous_total = len(previous_matters)
        matter_specs = (
            ("total", "Leads recebidos", lambda item: True),
            ("qualified", "Leads qualificados", lambda item: item.lead_score >= 60),
            ("won", "Casos ganhos", lambda item: item.column_id == KanbanColumnId.ganho),
            ("lost", "Casos perdidos", lambda item: item.column_id == KanbanColumnId.perdido),
            ("blocked", "Casos bloqueados", lambda item: item.is_blocked),
            ("high_priority", "Leads de alta prioridade", lambda item: item.priority == Priority.alta),
            ("urgent", "Leads urgentes", lambda item: item.urgency == Urgency.urgente),
            ("approval", "Casos aguardando aprovação", lambda item: item.needs_approval),
            ("with_next_action", "Casos com próxima ação", lambda item: bool(item.next_action)),
            ("with_responsible", "Casos com responsável", lambda item: item.responsible_human_id is not None),
        )
        matter_counts: dict[str, tuple[int, int]] = {}
        for key, label, predicate in matter_specs:
            current = count_matters(current_matters, predicate)
            previous = count_matters(previous_matters, predicate)
            matter_counts[key] = (current, previous)
            add(
                f"leads.{key}",
                label,
                "leads",
                current,
                previous,
                "count",
                f"Contagem de {label.casefold()} entre casos criados no período.",
                "matters",
            )
        for key, label in (
            ("qualified", "Taxa de qualificação"),
            ("won", "Taxa de fechamento"),
            ("lost", "Taxa de perda"),
            ("blocked", "Taxa de bloqueio"),
            ("high_priority", "Taxa de alta prioridade"),
            ("urgent", "Taxa de urgência"),
            ("with_next_action", "Cobertura de próxima ação"),
            ("with_responsible", "Cobertura de responsável"),
        ):
            current, previous = matter_counts[key]
            add(
                f"leads.{key}_rate",
                label,
                "leads",
                _rate(current, current_total),
                _rate(previous, previous_total),
                "percent",
                f"{label} sobre o total de leads do mesmo período.",
                "matters",
                note=None if current_total else "Sem leads no período para formar o denominador.",
            )
        for key, label, getter, unit in (
            ("avg_lead_score", "Score médio do lead", lambda item: item.lead_score, "score"),
            ("avg_stars", "Potencial médio em estrelas", lambda item: item.stars_rating, "score"),
            ("avg_doc_score", "Score documental médio", lambda item: item.doc_score, "score"),
            ("avg_production_score", "Score de produção médio", lambda item: item.production_score, "score"),
            ("claim_value_total", "Valor total das causas", lambda item: _number(item.claim_value), "BRL"),
            ("economic_value_total", "Valor econômico anual", lambda item: _number(item.economic_value_annual), "BRL"),
            ("open_tasks_total", "Tarefas abertas declaradas nos cards", lambda item: item.open_tasks, "count"),
            ("unread_total", "Mensagens não lidas nos cards", lambda item: item.unread_messages, "count"),
        ):
            current_values = [float(getter(item)) for item in current_matters]
            previous_values = [float(getter(item)) for item in previous_matters]
            aggregation = sum if key.endswith("_total") else _average
            add(
                f"leads.{key}",
                label,
                "leads",
                aggregation(current_values) if current_values else (0 if key.endswith("_total") else None),
                aggregation(previous_values) if previous_values else (0 if key.endswith("_total") else None),
                unit,
                f"{label} calculado sobre os casos do período.",
                "matters",
            )

        quality_specs = (
            (
                "lead_score",
                "Qualidade média dos leads",
                _average(float(item.lead_score) for item in current_matters),
                _average(float(item.lead_score) for item in previous_matters),
                "score",
                "matters.lead_score",
            ),
            (
                "document_score",
                "Qualidade documental média",
                _average(float(item.doc_score) for item in current_matters),
                _average(float(item.doc_score) for item in previous_matters),
                "score",
                "matters.doc_score",
            ),
            (
                "production_score",
                "Qualidade média de produção",
                _average(float(item.production_score) for item in current_matters),
                _average(float(item.production_score) for item in previous_matters),
                "score",
                "matters.production_score",
            ),
            (
                "potential",
                "Potencial médio dos casos",
                _average(float(item.stars_rating) for item in current_matters),
                _average(float(item.stars_rating) for item in previous_matters),
                "score",
                "matters.stars_rating",
            ),
            (
                "blocked_rate",
                "Casos com bloqueio operacional",
                _rate(matter_counts["blocked"][0], current_total),
                _rate(matter_counts["blocked"][1], previous_total),
                "percent",
                "matters.is_blocked",
            ),
            (
                "human_review_rate",
                "Casos que exigem revisão humana",
                _rate(matter_counts["approval"][0], current_total),
                _rate(matter_counts["approval"][1], previous_total),
                "percent",
                "matters.needs_approval",
            ),
        )
        for key, label, current, previous, unit, source in quality_specs:
            add(
                f"quality.{key}",
                label,
                "quality",
                current,
                previous,
                unit,
                f"{label} calculada exclusivamente a partir dos casos do período.",
                source,
                note=None if current_total else "Sem casos no período para calcular este indicador.",
            )

        stage_counts_current = defaultdict(int)
        stage_counts_previous = defaultdict(int)
        for item in current_matters:
            stage_counts_current[_enum_value(item.column_id)] += 1
        for item in previous_matters:
            stage_counts_previous[_enum_value(item.column_id)] += 1
        for stage_item in KanbanColumnId:
            label = stage_item.value.replace("_", " ").title()
            add(
                f"funnel.stage.{stage_item.value}.count",
                f"Casos na etapa {label}",
                "funnel",
                stage_counts_current[stage_item.value],
                stage_counts_previous[stage_item.value],
                "count",
                "Quantidade de casos criados no período que se encontram nesta etapa.",
                "matters.column_id",
                dimensions={"stage": stage_item.value},
            )
            add(
                f"funnel.stage.{stage_item.value}.share",
                f"Participação da etapa {label}",
                "funnel",
                _rate(stage_counts_current[stage_item.value], current_total),
                _rate(stage_counts_previous[stage_item.value], previous_total),
                "percent",
                "Participação da etapa no total de casos do período.",
                "matters.column_id",
                dimensions={"stage": stage_item.value},
            )

        self._add_message_metrics(
            add, data["current_messages"], data["previous_messages"], data["threads"], data["now"]
        )
        self._add_status_metrics(
            add,
            category="meetings",
            prefix="meetings",
            label_prefix="Reuniões",
            current=data["current_meetings"],
            previous=data["previous_meetings"],
            status_getter=lambda item: _enum_value(item.status),
            statuses=[item.value for item in MeetingStatus],
            source="meetings",
        )
        completed = sum(
            1
            for item in data["current_meetings"]
            if item.status == MeetingStatus.completed
        )
        no_show = sum(
            1
            for item in data["current_meetings"]
            if item.status == MeetingStatus.no_show
        )
        completed_previous = sum(
            1
            for item in data["previous_meetings"]
            if item.status == MeetingStatus.completed
        )
        no_show_previous = sum(
            1
            for item in data["previous_meetings"]
            if item.status == MeetingStatus.no_show
        )
        add(
            "meetings.show_rate",
            "Taxa de comparecimento",
            "meetings",
            _rate(completed, completed + no_show),
            _rate(
                completed_previous,
                completed_previous + no_show_previous,
            ),
            "percent",
            "Reuniões realizadas sobre as reuniões decididas: realizadas e no-show.",
            "meetings",
        )

        self._add_status_metrics(
            add,
            category="documents",
            prefix="documents",
            label_prefix="Documentos",
            current=data["current_documents"],
            previous=data["previous_documents"],
            status_getter=lambda item: _enum_value(item.status),
            statuses=[item.value for item in DocumentStatus],
            source="documents",
        )
        add(
            "documents.per_case",
            "Documentos por caso",
            "documents",
            round(len(data["current_documents"]) / current_total, 2) if current_total else None,
            round(len(data["previous_documents"]) / previous_total, 2) if previous_total else None,
            "ratio",
            "Documentos criados no período, vinculados aos casos do recorte, por caso recebido.",
            "documents",
        )

        self._add_status_metrics(
            add,
            category="team",
            prefix="tasks",
            label_prefix="Tarefas",
            current=data["current_tasks"],
            previous=data["previous_tasks"],
            status_getter=lambda item: _enum_value(item.status),
            statuses=[item.value for item in TaskStatus],
            source="tasks",
        )
        ai_current = sum(1 for item in data["current_tasks"] if item.is_ai_generated)
        ai_previous = sum(1 for item in data["previous_tasks"] if item.is_ai_generated)
        add(
            "team.ai_task_rate",
            "Tarefas originadas por IA",
            "team",
            _rate(ai_current, len(data["current_tasks"])),
            _rate(ai_previous, len(data["previous_tasks"])),
            "percent",
            "Participação de tarefas geradas por agentes no total de tarefas.",
            "tasks.is_ai_generated",
        )

        current_projection = sum(_number(item.projected_revenue) for item in data["current_projections"])
        previous_projection = sum(_number(item.projected_revenue) for item in data["previous_projections"])
        add(
            "finance.projected_revenue",
            "Receita projetada",
            "finance",
            current_projection,
            previous_projection,
            "BRL",
            "Soma das projeções criadas no período e vinculadas aos casos do recorte.",
            "revenue_projections",
        )
        for status in FinanceEntryStatus:
            current_rows = [item for item in data["current_finances"] if item.status == status]
            previous_rows = [item for item in data["previous_finances"] if item.status == status]
            label = status.value.replace("_", " ").title()
            add(
                f"finance.{status.value}.count",
                f"Lançamentos {label}",
                "finance",
                len(current_rows),
                len(previous_rows),
                "count",
                "Quantidade de lançamentos financeiros no estado informado.",
                "finance_entries",
            )
            add(
                f"finance.{status.value}.amount",
                f"Valor {label}",
                "finance",
                sum(_number(item.amount) for item in current_rows),
                sum(_number(item.amount) for item in previous_rows),
                "BRL",
                "Valor dos lançamentos financeiros no estado informado.",
                "finance_entries",
            )
        paid_current = sum(
            _number(item.amount)
            for item in data["current_finances"]
            if item.status == FinanceEntryStatus.paid
            and item.entry_type == FinanceEntryType.receivable
        )
        receivable_current = sum(
            _number(item.amount)
            for item in data["current_finances"]
            if item.entry_type == FinanceEntryType.receivable
        )
        paid_previous = sum(
            _number(item.amount)
            for item in data["previous_finances"]
            if item.status == FinanceEntryStatus.paid
            and item.entry_type == FinanceEntryType.receivable
        )
        receivable_previous = sum(
            _number(item.amount)
            for item in data["previous_finances"]
            if item.entry_type == FinanceEntryType.receivable
        )
        add(
            "finance.collection_rate",
            "Taxa de recebimento",
            "finance",
            _rate(paid_current, receivable_current),
            _rate(paid_previous, receivable_previous),
            "percent",
            "Recebíveis pagos sobre os recebíveis lançados no período.",
            "finance_entries",
        )

        self._add_status_metrics(
            add,
            category="flows",
            prefix="flows",
            label_prefix="Execuções de flow",
            current=data["current_flow_runs"],
            previous=data["previous_flow_runs"],
            status_getter=lambda item: item.status,
            statuses=["active", "paused", "completed", "failed", "waiting_human"],
            source="flow_runs",
        )
        self._add_status_metrics(
            add,
            category="flows",
            prefix="followups",
            label_prefix="Follow-ups",
            current=data["current_follow_ups"],
            previous=data["previous_follow_ups"],
            status_getter=lambda item: _enum_value(item.status),
            statuses=[item.value for item in FollowUpStatus],
            source="klm_follow_up_tasks",
        )

        event_names_current = {item.event_name for item in data["current_acquisitions"]}
        event_names_previous = {item.event_name for item in data["previous_acquisitions"]}
        add(
            "acquisition.events.total",
            "Eventos de aquisição",
            "acquisition",
            len(data["current_acquisitions"]),
            len(data["previous_acquisitions"]),
            "count",
            "Eventos de aquisição deduplicados recebidos no período.",
            "klm_acquisition_events",
        )
        add(
            "acquisition.event_types",
            "Tipos de evento observados",
            "acquisition",
            len(event_names_current),
            len(event_names_previous),
            "count",
            "Quantidade de nomes de evento distintos no período.",
            "klm_acquisition_events",
        )
        add(
            "acquisition.attribution_coverage",
            "Cobertura de atribuição",
            "acquisition",
            _rate(
                sum(
                    1
                    for item in data["current_acquisitions"]
                    if item.campaign or item.source or item.channel
                ),
                len(data["current_acquisitions"]),
            ),
            _rate(
                sum(
                    1
                    for item in data["previous_acquisitions"]
                    if item.campaign or item.source or item.channel
                ),
                len(data["previous_acquisitions"]),
            ),
            "percent",
            "Eventos que carregam campanha, origem ou canal sobre o total.",
            "klm_acquisition_events",
        )
        for dimension, getter in (
            ("channel", lambda item: item.channel),
            ("source", lambda item: item.source),
            ("campaign", lambda item: item.campaign),
        ):
            current_count = len({getter(item) for item in data["current_acquisitions"] if getter(item)})
            previous_count = len({getter(item) for item in data["previous_acquisitions"] if getter(item)})
            add(
                f"acquisition.distinct_{dimension}s",
                f"{dimension.replace('_', ' ').title()} distintos",
                "acquisition",
                current_count,
                previous_count,
                "count",
                f"Quantidade de {dimension}s distintos observados no período.",
                "klm_acquisition_events",
            )

    def _add_message_metrics(
        self,
        add: Callable[..., None],
        current: list[OmnichannelMessage],
        previous: list[OmnichannelMessage],
        threads: list[OmnichannelThread],
        now: datetime,
    ) -> None:
        specs = (
            ("total", "Mensagens", lambda item: True),
            ("inbound", "Mensagens recebidas", lambda item: item.direction == MessageDirection.inbound),
            ("outbound", "Mensagens enviadas", lambda item: item.direction == MessageDirection.outbound),
            ("internal", "Notas internas", lambda item: item.direction == MessageDirection.internal),
            ("human", "Mensagens humanas", lambda item: item.author_type == MessageAuthorType.human),
            ("agent", "Mensagens de agentes", lambda item: item.author_type == MessageAuthorType.agent),
            ("failed", "Mensagens com falha", lambda item: item.delivery_status == MessageDeliveryStatus.failed),
            ("delivered", "Mensagens entregues", lambda item: item.delivery_status in {MessageDeliveryStatus.delivered, MessageDeliveryStatus.read}),
        )
        for key, label, predicate in specs:
            add(
                f"service.messages.{key}",
                label,
                "service",
                sum(1 for item in current if predicate(item)),
                sum(1 for item in previous if predicate(item)),
                "count",
                f"{label} registradas no período.",
                "klm_omnichannel_messages",
            )
        current_times = self._response_times(current)
        previous_times = self._response_times(previous)
        add(
            "service.response.median",
            "Mediana do tempo de resposta",
            "service",
            round(median(current_times), 2) if current_times else None,
            round(median(previous_times), 2) if previous_times else None,
            "seconds",
            "Mediana entre uma mensagem recebida e a primeira resposta subsequente.",
            "klm_omnichannel_messages",
        )
        add(
            "service.response.p90",
            "P90 do tempo de resposta",
            "service",
            _percentile(current_times, 0.9),
            _percentile(previous_times, 0.9),
            "seconds",
            "Tempo abaixo do qual ficaram 90% das respostas observadas.",
            "klm_omnichannel_messages",
        )
        for status in ThreadStatus:
            add(
                f"service.threads.{status.value}",
                f"Conversas {status.value.replace('_', ' ')}",
                "service",
                sum(1 for item in threads if item.status == status),
                None,
                "count",
                "Snapshot atual das conversas no estado informado.",
                "klm_omnichannel_threads",
                note="Snapshot atual; comparação anterior não é reconstruída.",
            )
        add(
            "service.threads.unread",
            "Mensagens não lidas nas conversas",
            "service",
            sum(item.unread_count for item in threads),
            None,
            "count",
            "Soma atual de mensagens não lidas nas conversas do recorte.",
            "klm_omnichannel_threads",
            note="Snapshot atual; comparação anterior não é reconstruída.",
        )
        add(
            "service.sla.overdue",
            "Conversas com resposta atrasada",
            "service",
            sum(1 for item in threads if item.sla_due_at and (_moment(item.sla_due_at) or now) < now),
            None,
            "count",
            "Conversas cujo prazo de resposta está vencido no momento da consulta.",
            "klm_omnichannel_threads.sla_due_at",
            note="Snapshot atual; comparação anterior não é reconstruída.",
        )

    @staticmethod
    def _add_status_metrics(
        add: Callable[..., None],
        *,
        category: str,
        prefix: str,
        label_prefix: str,
        current: list[Any],
        previous: list[Any],
        status_getter: Callable[[Any], str],
        statuses: list[str],
        source: str,
    ) -> None:
        add(
            f"{prefix}.total",
            f"{label_prefix} no período",
            category,
            len(current),
            len(previous),
            "count",
            f"Total de {label_prefix.casefold()} vinculadas ao recorte.",
            source,
        )
        for status in statuses:
            label = status.replace("_", " ").title()
            current_count = sum(1 for item in current if status_getter(item) == status)
            previous_count = sum(1 for item in previous if status_getter(item) == status)
            add(
                f"{prefix}.{status}.count",
                f"{label_prefix} {label}",
                category,
                current_count,
                previous_count,
                "count",
                f"Quantidade no estado {label.casefold()}.",
                source,
                dimensions={"status": status},
            )
            add(
                f"{prefix}.{status}.rate",
                f"Taxa de {label_prefix.casefold()} {label.casefold()}",
                category,
                _rate(current_count, len(current)),
                _rate(previous_count, len(previous)),
                "percent",
                f"Participação do estado {label.casefold()} no total do período.",
                source,
                dimensions={"status": status},
            )

    def _add_dimension_metrics(
        self,
        *,
        add: Callable[..., None],
        current_matters: list[Matter],
        previous_matters: list[Matter],
        area_of: Callable[[Matter], str],
        product_of: Callable[[Matter], str],
        channels_of: Callable[[Matter], set[str]],
        canonical_area: str | None,
    ) -> None:
        def group(rows: list[Matter], getter: Callable[[Matter], Iterable[str]]) -> dict[str, list[Matter]]:
            values: dict[str, list[Matter]] = defaultdict(list)
            for item in rows:
                for key in getter(item):
                    values[key].append(item)
            return values

        current_areas = group(current_matters, lambda item: (area_of(item),))
        previous_areas = group(previous_matters, lambda item: (area_of(item),))
        current_products = group(current_matters, lambda item: (product_of(item),))
        previous_products = group(previous_matters, lambda item: (product_of(item),))
        current_channels = group(current_matters, channels_of)
        previous_channels = group(previous_matters, channels_of)

        def dimension_rows(
            *,
            dimension: str,
            category: str,
            key: str,
            label: str,
            current: list[Matter],
            previous: list[Matter],
            additional_dimensions: dict[str, str] | None = None,
        ) -> None:
            current_won = sum(1 for item in current if item.column_id == KanbanColumnId.ganho)
            previous_won = sum(1 for item in previous if item.column_id == KanbanColumnId.ganho)
            current_qualified = sum(1 for item in current if item.lead_score >= 60)
            previous_qualified = sum(1 for item in previous if item.lead_score >= 60)
            dimensions = {dimension: key, **(additional_dimensions or {})}
            specs = (
                ("leads", "Leads", len(current), len(previous), "count"),
                ("qualified", "Qualificados", current_qualified, previous_qualified, "count"),
                ("won", "Ganhos", current_won, previous_won, "count"),
                ("conversion", "Conversão", _rate(current_won, len(current)), _rate(previous_won, len(previous)), "percent"),
                ("lead_score", "Score médio", _average(float(item.lead_score) for item in current), _average(float(item.lead_score) for item in previous), "score"),
            )
            for suffix, metric_label, value, previous_value, unit in specs:
                add(
                    f"{category}.{key}.{suffix}",
                    f"{metric_label} — {label}",
                    category,
                    value,
                    previous_value,
                    unit,
                    f"{metric_label} no recorte {label}.",
                    "matters",
                    dimensions=dimensions,
                )

        for area in LEGAL_AREA_CATALOG:
            key = area["areaKey"]
            if canonical_area and key != canonical_area:
                continue
            dimension_rows(
                dimension="legalArea",
                category="legal_areas",
                key=key,
                label=area["name"],
                current=current_areas[key],
                previous=previous_areas[key],
            )
            for product in area["products"]:
                product_key = product["key"]
                dimension_rows(
                    dimension="legalProduct",
                    category="legal_products",
                    key=product_key,
                    label=product["label"],
                    current=current_products[product_key],
                    previous=previous_products[product_key],
                    additional_dimensions={"legalArea": key},
                )
        channel_keys = sorted(set(current_channels) | set(previous_channels))
        for key in channel_keys:
            dimension_rows(
                dimension="channel",
                category="acquisition",
                key=f"channel.{key}",
                label=key.replace("_", " ").title(),
                current=current_channels[key],
                previous=previous_channels[key],
            )
