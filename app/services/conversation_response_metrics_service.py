"""Tenant-scoped response-time state and KPI calculations."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from statistics import fmean, median
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import (
    MessageAuthorType,
    MessageDirection,
    OmnichannelMessage,
    OmnichannelThread,
    ThreadStatus,
)
from app.models.omnichannel_v6 import WorkspaceTeam
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.conversation_response_metrics import (
    ConversationResponseAggregateRead,
    ConversationResponseFilterPreset,
    ConversationResponseFiltersRead,
    ConversationResponseKpiRead,
    ConversationResponseMetricRead,
    ConversationResponseMetricsRead,
    ConversationResponsePeriod,
    ConversationResponsePeriodRead,
    ConversationResponseSlaStatus,
    ConversationResponseSort,
    ConversationResponseSortDirection,
    ConversationResponseState,
)

CLIENT_FACING_AUDIENCES = ("client_private", "public")
MAX_SCOPE_THREADS = 10_000
MESSAGE_QUERY_CHUNK = 400
MAX_CUSTOM_PERIOD = timedelta(days=3650)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _seconds_between(started_at: datetime, ended_at: datetime) -> int:
    return max(0, int((_utc(ended_at) - _utc(started_at)).total_seconds()))


class ConversationResponseMetricsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def calculate(
        self,
        workspace_id: UUID,
        *,
        period: ConversationResponsePeriod = ConversationResponsePeriod.all_time,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        preset: ConversationResponseFilterPreset = ConversationResponseFilterPreset.all,
        attendant_ids: Iterable[UUID] = (),
        team_ids: Iterable[UUID] = (),
        statuses: Iterable[ConversationResponseState] = (),
        sla_statuses: Iterable[ConversationResponseSlaStatus] = (),
        sort_by: ConversationResponseSort = ConversationResponseSort.duration,
        sort_direction: ConversationResponseSortDirection = (
            ConversationResponseSortDirection.desc
        ),
        offset: int = 0,
        limit: int = 100,
    ) -> ConversationResponseMetricsRead:
        generated_at = _utc_now()
        period_start, period_end = self._resolve_period(
            period,
            date_from=date_from,
            date_to=date_to,
            now=generated_at,
        )
        attendant_filter = tuple(dict.fromkeys(attendant_ids))
        team_filter = tuple(dict.fromkeys(team_ids))
        status_filter = tuple(dict.fromkeys(statuses))
        sla_filter = tuple(dict.fromkeys(sla_statuses))

        thread_query = select(OmnichannelThread).where(
            OmnichannelThread.workspace_id == workspace_id
        )
        if attendant_filter:
            thread_query = thread_query.where(
                OmnichannelThread.assigned_human_id.in_(attendant_filter)
            )
        if team_filter:
            thread_query = thread_query.where(
                OmnichannelThread.assigned_team_id.in_(team_filter)
            )
        thread_rows = await self.db.execute(
            thread_query.order_by(OmnichannelThread.id.asc()).limit(MAX_SCOPE_THREADS + 1)
        )
        threads = list(thread_rows.scalars().all())
        if len(threads) > MAX_SCOPE_THREADS:
            raise ValueError(
                "O recorte excede 10.000 conversas; aplique período, equipe ou atendente."
            )

        messages = await self._eligible_messages(
            workspace_id,
            [thread.id for thread in threads],
            as_of=period_end,
        )
        messages_by_thread: dict[UUID, list[OmnichannelMessage]] = defaultdict(list)
        for message in messages:
            messages_by_thread[message.thread_id].append(message)

        team_names = await self._team_names(
            workspace_id,
            {thread.assigned_team_id for thread in threads if thread.assigned_team_id},
        )
        attendant_names = await self._attendant_names(
            workspace_id,
            {thread.assigned_human_id for thread in threads if thread.assigned_human_id},
        )

        metrics: list[ConversationResponseMetricRead] = []
        activity_by_thread: dict[UUID, datetime] = {}
        for thread in threads:
            metric = self._thread_metric(
                thread,
                messages_by_thread.get(thread.id, []),
                as_of=period_end,
                team_names=team_names,
                attendant_names=attendant_names,
            )
            activity_by_thread[thread.id] = (
                metric.last_client_facing_activity_at or _utc(thread.created_at)
            )
            metrics.append(metric)

        metrics = [
            metric
            for metric in metrics
            if self._in_period(
                activity_by_thread[metric.thread_id],
                started_at=period_start,
                ended_at=period_end,
            )
            and self._matches_preset(metric, preset)
            and (not status_filter or metric.state in status_filter)
            and (not sla_filter or metric.sla_status in sla_filter)
        ]
        metrics = self._sort_metrics(metrics, sort_by, sort_direction)

        account_name = await self.db.scalar(
            select(Workspace.name).where(Workspace.id == workspace_id)
        )
        total = len(metrics)
        page = metrics[offset : offset + limit]
        return ConversationResponseMetricsRead(
            generated_at=generated_at,
            period=ConversationResponsePeriodRead(
                preset=period,
                started_at=period_start,
                ended_at=period_end,
                as_of=period_end,
            ),
            filters=ConversationResponseFiltersRead(
                preset=preset,
                attendant_ids=list(attendant_filter),
                team_ids=list(team_filter),
                statuses=list(status_filter),
                sla_statuses=list(sla_filter),
                sort_by=sort_by,
                sort_direction=sort_direction,
            ),
            total=total,
            offset=offset,
            limit=limit,
            has_more=offset + len(page) < total,
            items=page,
            account=ConversationResponseAggregateRead(
                group_id=workspace_id,
                group_name=account_name or "Conta",
                metrics=self._kpis(metrics),
            ),
            by_team=self._grouped_aggregates(metrics, dimension="team"),
            by_attendant=self._grouped_aggregates(metrics, dimension="attendant"),
        )

    @staticmethod
    def _resolve_period(
        period: ConversationResponsePeriod,
        *,
        date_from: datetime | None,
        date_to: datetime | None,
        now: datetime,
    ) -> tuple[datetime | None, datetime]:
        now = _utc(now)
        if period == ConversationResponsePeriod.custom:
            if date_from is None or date_to is None:
                raise ValueError("date_from e date_to são obrigatórios para período customizado.")
            started_at = _utc(date_from)
            ended_at = min(_utc(date_to), now)
            if ended_at < started_at:
                raise ValueError("date_to deve ser igual ou posterior a date_from.")
            if ended_at - started_at > MAX_CUSTOM_PERIOD:
                raise ValueError("O período customizado não pode exceder 3.650 dias.")
            return started_at, ended_at
        if date_from is not None or date_to is not None:
            raise ValueError("date_from e date_to só podem ser usados com period=custom.")
        lookbacks = {
            ConversationResponsePeriod.last_24_hours: timedelta(hours=24),
            ConversationResponsePeriod.last_7_days: timedelta(days=7),
            ConversationResponsePeriod.last_30_days: timedelta(days=30),
            ConversationResponsePeriod.last_90_days: timedelta(days=90),
        }
        lookback = lookbacks.get(period)
        return (now - lookback if lookback else None), now

    async def _eligible_messages(
        self,
        workspace_id: UUID,
        thread_ids: list[UUID],
        *,
        as_of: datetime,
    ) -> list[OmnichannelMessage]:
        output: list[OmnichannelMessage] = []
        eligible = or_(
            and_(
                OmnichannelMessage.direction == MessageDirection.inbound,
                OmnichannelMessage.author_type == MessageAuthorType.client,
            ),
            and_(
                OmnichannelMessage.direction == MessageDirection.outbound,
                OmnichannelMessage.audience.in_(CLIENT_FACING_AUDIENCES),
            ),
        )
        for start in range(0, len(thread_ids), MESSAGE_QUERY_CHUNK):
            chunk = thread_ids[start : start + MESSAGE_QUERY_CHUNK]
            rows = await self.db.execute(
                select(OmnichannelMessage)
                .where(
                    OmnichannelMessage.workspace_id == workspace_id,
                    OmnichannelMessage.thread_id.in_(chunk),
                    OmnichannelMessage.occurred_at <= as_of,
                    eligible,
                )
                .order_by(
                    OmnichannelMessage.thread_id.asc(),
                    OmnichannelMessage.occurred_at.asc(),
                    OmnichannelMessage.id.asc(),
                )
            )
            output.extend(rows.scalars().all())
        return output

    async def _team_names(
        self,
        workspace_id: UUID,
        team_ids: set[UUID],
    ) -> dict[UUID, str]:
        if not team_ids:
            return {}
        rows = await self.db.execute(
            select(WorkspaceTeam.id, WorkspaceTeam.name).where(
                WorkspaceTeam.workspace_id == workspace_id,
                WorkspaceTeam.id.in_(team_ids),
            )
        )
        return dict(rows.all())

    async def _attendant_names(
        self,
        workspace_id: UUID,
        user_ids: set[UUID],
    ) -> dict[UUID, str]:
        if not user_ids:
            return {}
        rows = await self.db.execute(
            select(User.id, User.name)
            .join(
                WorkspaceMembership,
                and_(
                    WorkspaceMembership.user_id == User.id,
                    WorkspaceMembership.workspace_id == workspace_id,
                ),
            )
            .where(
                User.id.in_(user_ids),
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )
        return dict(rows.all())

    def _thread_metric(
        self,
        thread: OmnichannelThread,
        messages: list[OmnichannelMessage],
        *,
        as_of: datetime,
        team_names: dict[UUID, str],
        attendant_names: dict[UUID, str],
    ) -> ConversationResponseMetricRead:
        moments = [_utc(message.occurred_at) for message in messages]
        sides = [
            "client" if message.direction == MessageDirection.inbound else "office"
            for message in messages
        ]
        first_inbound_index = next(
            (index for index, side in enumerate(sides) if side == "client"),
            None,
        )
        first_response_index = None
        if first_inbound_index is not None:
            first_response_index = next(
                (
                    index
                    for index in range(first_inbound_index + 1, len(sides))
                    if sides[index] == "office"
                ),
                None,
            )
        first_client_at = (
            moments[first_inbound_index] if first_inbound_index is not None else None
        )
        first_response_at = (
            moments[first_response_index] if first_response_index is not None else None
        )
        first_response_seconds = (
            _seconds_between(first_client_at, first_response_at)
            if first_client_at is not None and first_response_at is not None
            else None
        )

        if not messages:
            state = ConversationResponseState.sem_mensagem
            state_started_at = None
            duration_seconds = None
        else:
            latest_side = sides[-1]
            turn_start_index = len(sides) - 1
            while turn_start_index > 0 and sides[turn_start_index - 1] == latest_side:
                turn_start_index -= 1
            state_started_at = moments[turn_start_index]
            closed_at = _utc(thread.closed_at) if thread.closed_at else None
            closed_as_of = (
                thread.status == ThreadStatus.closed
                and (closed_at is None or closed_at <= as_of)
            )
            if latest_side == "client":
                state = ConversationResponseState.cliente_aguardando_escritorio
            elif closed_as_of:
                state = ConversationResponseState.respondido
                state_started_at = closed_at or moments[-1]
            else:
                state = ConversationResponseState.escritorio_aguardando_cliente
            duration_seconds = _seconds_between(state_started_at, as_of)

        first_deadline = thread.first_response_due_at or thread.sla_due_at
        first_deadline = _utc(first_deadline) if first_deadline else None
        sla_due_at: datetime | None
        evaluation_at: datetime | None
        if state == ConversationResponseState.cliente_aguardando_escritorio:
            if first_response_at is None:
                sla_due_at = first_deadline
            else:
                sla_due_at = _utc(thread.sla_due_at) if thread.sla_due_at else None
            evaluation_at = as_of
        elif first_response_at is not None:
            sla_due_at = first_deadline
            evaluation_at = first_response_at
        elif first_client_at is not None:
            sla_due_at = first_deadline
            evaluation_at = as_of
        else:
            sla_due_at = None
            evaluation_at = None

        if evaluation_at is None:
            sla_status = ConversationResponseSlaStatus.not_applicable
            overdue_seconds = 0
        elif sla_due_at is None:
            sla_status = ConversationResponseSlaStatus.without_sla
            overdue_seconds = 0
        elif evaluation_at > sla_due_at:
            sla_status = ConversationResponseSlaStatus.overdue
            overdue_seconds = _seconds_between(sla_due_at, evaluation_at)
        else:
            sla_status = ConversationResponseSlaStatus.within_sla
            overdue_seconds = 0

        first_response_within_sla = None
        if first_response_at is not None and first_deadline is not None:
            first_response_within_sla = first_response_at <= first_deadline

        team_id = thread.assigned_team_id if thread.assigned_team_id in team_names else None
        attendant_id = (
            thread.assigned_human_id
            if thread.assigned_human_id in attendant_names
            else None
        )
        return ConversationResponseMetricRead(
            thread_id=thread.id,
            thread_status=_enum_value(thread.status),
            primary_channel=_enum_value(thread.primary_channel),
            state=state,
            state_started_at=state_started_at,
            duration_seconds=duration_seconds,
            last_client_facing_activity_at=moments[-1] if moments else None,
            assigned_team_id=team_id,
            assigned_team_name=team_names.get(team_id),
            assigned_attendant_id=attendant_id,
            assigned_attendant_name=attendant_names.get(attendant_id),
            sla_due_at=sla_due_at,
            sla_status=sla_status,
            is_overdue=sla_status == ConversationResponseSlaStatus.overdue,
            overdue_seconds=overdue_seconds,
            first_client_message_at=first_client_at,
            first_response_at=first_response_at,
            first_response_seconds=first_response_seconds,
            first_response_within_sla=first_response_within_sla,
        )

    @staticmethod
    def _in_period(
        activity_at: datetime,
        *,
        started_at: datetime | None,
        ended_at: datetime,
    ) -> bool:
        activity_at = _utc(activity_at)
        return activity_at <= ended_at and (started_at is None or activity_at >= started_at)

    @staticmethod
    def _matches_preset(
        metric: ConversationResponseMetricRead,
        preset: ConversationResponseFilterPreset,
    ) -> bool:
        if preset == ConversationResponseFilterPreset.pending:
            return metric.state == ConversationResponseState.cliente_aguardando_escritorio
        if preset == ConversationResponseFilterPreset.overdue:
            return metric.sla_status == ConversationResponseSlaStatus.overdue
        if preset == ConversationResponseFilterPreset.waiting_for_client:
            return metric.state == ConversationResponseState.escritorio_aguardando_cliente
        if preset == ConversationResponseFilterPreset.responded:
            return metric.state == ConversationResponseState.respondido
        if preset == ConversationResponseFilterPreset.without_messages:
            return metric.state == ConversationResponseState.sem_mensagem
        return True

    @staticmethod
    def _sort_metrics(
        metrics: list[ConversationResponseMetricRead],
        sort_by: ConversationResponseSort,
        direction: ConversationResponseSortDirection,
    ) -> list[ConversationResponseMetricRead]:
        def value(metric: ConversationResponseMetricRead) -> object | None:
            values = {
                ConversationResponseSort.duration: metric.duration_seconds,
                ConversationResponseSort.started_at: metric.state_started_at,
                ConversationResponseSort.last_activity: (
                    metric.last_client_facing_activity_at
                ),
                ConversationResponseSort.sla_due_at: metric.sla_due_at,
                ConversationResponseSort.first_response: metric.first_response_seconds,
                ConversationResponseSort.attendant: metric.assigned_attendant_name,
                ConversationResponseSort.team: metric.assigned_team_name,
                ConversationResponseSort.status: metric.state.value,
            }
            selected = values[sort_by]
            return selected.casefold() if isinstance(selected, str) else selected

        populated = [metric for metric in metrics if value(metric) is not None]
        empty = [metric for metric in metrics if value(metric) is None]
        populated.sort(
            key=lambda metric: (value(metric), str(metric.thread_id)),
            reverse=direction == ConversationResponseSortDirection.desc,
        )
        empty.sort(key=lambda metric: str(metric.thread_id))
        return populated + empty

    @staticmethod
    def _kpis(metrics: list[ConversationResponseMetricRead]) -> ConversationResponseKpiRead:
        waits = [
            metric.duration_seconds
            for metric in metrics
            if metric.state == ConversationResponseState.cliente_aguardando_escritorio
            and metric.duration_seconds is not None
        ]
        first_responses = [
            metric.first_response_seconds
            for metric in metrics
            if metric.first_response_seconds is not None
        ]
        evaluated = [
            metric
            for metric in metrics
            if metric.sla_status
            in {
                ConversationResponseSlaStatus.within_sla,
                ConversationResponseSlaStatus.overdue,
            }
        ]
        within_sla = sum(
            metric.sla_status == ConversationResponseSlaStatus.within_sla
            for metric in evaluated
        )
        sorted_waits = sorted(waits)
        p90 = (
            sorted_waits[max(0, math.ceil(len(sorted_waits) * 0.9) - 1)]
            if sorted_waits
            else None
        )
        return ConversationResponseKpiRead(
            conversations=len(metrics),
            pending=len(waits),
            average_wait_seconds=round(fmean(waits), 2) if waits else None,
            median_wait_seconds=round(float(median(waits)), 2) if waits else None,
            p90_wait_seconds=round(float(p90), 2) if p90 is not None else None,
            longest_wait_seconds=max(waits) if waits else None,
            average_first_response_seconds=(
                round(fmean(first_responses), 2) if first_responses else None
            ),
            within_sla_rate=(
                round(within_sla / len(evaluated) * 100, 2) if evaluated else None
            ),
            sla_evaluated=len(evaluated),
            within_sla=within_sla,
        )

    def _grouped_aggregates(
        self,
        metrics: list[ConversationResponseMetricRead],
        *,
        dimension: str,
    ) -> list[ConversationResponseAggregateRead]:
        grouped: dict[tuple[UUID | None, str], list[ConversationResponseMetricRead]] = (
            defaultdict(list)
        )
        for metric in metrics:
            if dimension == "team":
                group_id = metric.assigned_team_id
                group_name = metric.assigned_team_name or "Sem equipe"
            else:
                group_id = metric.assigned_attendant_id
                group_name = metric.assigned_attendant_name or "Sem atendente"
            grouped[(group_id, group_name)].append(metric)
        output = [
            ConversationResponseAggregateRead(
                group_id=group_id,
                group_name=group_name,
                metrics=self._kpis(group_metrics),
            )
            for (group_id, group_name), group_metrics in grouped.items()
        ]
        return sorted(
            output,
            key=lambda item: (item.group_id is None, item.group_name.casefold()),
        )
