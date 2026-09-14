"""Calcula e persiste cadência humana sem executar entrega externa."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import (
    KlmAuditEvent,
    MessageAuthorType,
    MessageDirection,
    OmnichannelMessage,
)
from app.models.workspace import Workspace
from app.schemas.messaging_cadence import (
    MessagingCadencePlanRead,
    MessagingCadenceSettingsRead,
    MessagingCadenceSettingsUpdate,
)
from app.services.audit_actor_service import resolve_human_actor_label

DEFAULT_MESSAGING_CADENCE = MessagingCadenceSettingsRead().model_dump(
    mode="json",
    by_alias=True,
)


class MessagingCadenceService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_settings(self, workspace_id: UUID) -> MessagingCadenceSettingsRead:
        workspace = await self._workspace(workspace_id)
        raw = deepcopy((workspace.settings or {}).get("messagingCadence") or {})
        merged = deepcopy(DEFAULT_MESSAGING_CADENCE)
        merged.update(raw)
        return MessagingCadenceSettingsRead.model_validate(merged)

    async def update_settings(
        self,
        workspace_id: UUID,
        payload: MessagingCadenceSettingsUpdate,
        *,
        actor_user_id: UUID,
        actor_label: str,
    ) -> MessagingCadenceSettingsRead:
        actor_label = await resolve_human_actor_label(
            self.db,
            actor_user_id,
        )
        workspace = await self._workspace(workspace_id, for_update=True)
        before = await self.get_settings(workspace_id)
        settings_data = deepcopy(workspace.settings or {})
        settings_data["messagingCadence"] = payload.model_dump(mode="json", by_alias=True)
        workspace.settings = settings_data
        after = MessagingCadenceSettingsRead.model_validate(
            settings_data["messagingCadence"]
        )
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type="messaging_cadence_updated",
                entity_type="workspace",
                entity_id=workspace_id,
                summary="Cadência de mensagens automatizadas atualizada.",
                before_data=before.model_dump(mode="json", by_alias=True),
                after_data=after.model_dump(mode="json", by_alias=True),
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return after

    async def plan(
        self,
        workspace_id: UUID,
        *,
        text: str,
        idempotency_key: str,
        now: datetime | None = None,
        count_recent_messages: bool = True,
    ) -> MessagingCadencePlanRead:
        settings = await self.get_settings(workspace_id)
        planned_at = self._as_utc(now or datetime.now(UTC))
        minute_count = 0
        hour_count = 0
        if count_recent_messages:
            minute_count, hour_count = await self._recent_agent_counts(
                workspace_id,
                planned_at,
            )

        pre_delay_ms = self._deterministic_between(
            settings.pre_delay_min_ms,
            settings.pre_delay_max_ms,
            f"{idempotency_key}:pre",
        )
        raw_typing_ms = int((len(text.strip()) / settings.characters_per_second) * 1000)
        typing_base = max(settings.typing_min_ms, min(settings.typing_max_ms, raw_typing_ms))
        typing_duration_ms = self._with_jitter(
            typing_base,
            settings.jitter_percent,
            f"{idempotency_key}:typing",
        )
        if not settings.enabled:
            pre_delay_ms = 0
            typing_duration_ms = 0
        typing_starts_at = planned_at + timedelta(milliseconds=pre_delay_ms)
        not_before = typing_starts_at + timedelta(milliseconds=typing_duration_ms)
        state = "ready_after_typing" if settings.enabled else "cadence_disabled"
        reason = (
            "Aguardando atraso inicial e tempo proporcional de digitação."
            if settings.enabled
            else "Cadência desativada pelo administrador."
        )

        quiet_end = self._quiet_period_end(planned_at, settings)
        if quiet_end is not None and quiet_end > not_before:
            typing_starts_at = quiet_end
            not_before = quiet_end + timedelta(milliseconds=typing_duration_ms)
            state = "deferred_quiet_hours"
            reason = "Adiada até o fim do horário silencioso configurado."

        if (
            minute_count >= settings.max_messages_per_minute
            or hour_count >= settings.max_messages_per_hour
        ):
            rate_not_before = planned_at + timedelta(seconds=settings.cooldown_seconds)
            if rate_not_before > not_before:
                typing_starts_at = rate_not_before
                not_before = rate_not_before + timedelta(milliseconds=typing_duration_ms)
            state = "deferred_rate_limit"
            reason = "Adiada pelo limite de mensagens automatizadas do workspace."

        total_delay_ms = max(0, int((not_before - planned_at).total_seconds() * 1000))
        return MessagingCadencePlanRead(
            enabled=settings.enabled,
            show_typing_indicator=settings.enabled and settings.show_typing_indicator,
            pre_delay_ms=pre_delay_ms,
            typing_duration_ms=typing_duration_ms,
            total_delay_ms=total_delay_ms,
            planned_at=planned_at,
            typing_starts_at=typing_starts_at,
            not_before=not_before,
            state=state,
            reason=reason,
            minute_count=minute_count,
            hour_count=hour_count,
            max_messages_per_minute=settings.max_messages_per_minute,
            max_messages_per_hour=settings.max_messages_per_hour,
            max_consecutive_without_reply=settings.max_consecutive_without_reply,
            official_api_only=settings.official_api_only,
            require_valid_consent=settings.require_valid_consent,
            stop_on_block_or_opt_out=settings.stop_on_block_or_opt_out,
        )

    async def _recent_agent_counts(
        self,
        workspace_id: UUID,
        now: datetime,
    ) -> tuple[int, int]:
        filters = (
            OmnichannelMessage.workspace_id == workspace_id,
            OmnichannelMessage.direction == MessageDirection.outbound,
            OmnichannelMessage.author_type == MessageAuthorType.agent,
        )
        minute_count = await self.db.scalar(
            select(func.count(OmnichannelMessage.id)).where(
                *filters,
                OmnichannelMessage.occurred_at >= now - timedelta(minutes=1),
            )
        )
        hour_count = await self.db.scalar(
            select(func.count(OmnichannelMessage.id)).where(
                *filters,
                OmnichannelMessage.occurred_at >= now - timedelta(hours=1),
            )
        )
        return int(minute_count or 0), int(hour_count or 0)

    async def count_recent_agent_messages_for_connection(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        *,
        now: datetime | None = None,
        window: timedelta = timedelta(hours=1),
    ) -> int:
        """Conta mensagens `author_type=agent` de UMA conexão na janela (default 1h).

        Distinto de `_recent_agent_counts` (que é por workspace inteiro): este
        método alimenta o cap de 200 msgs automatizadas/hora POR CONTA do
        Instagram (Meta Policy Gate, `app/services/meta_policy_gate.py`), que
        precisa isolar o contador por `connection_id`.
        """
        reference = self._as_utc(now or datetime.now(UTC))
        count = await self.db.scalar(
            select(func.count(OmnichannelMessage.id)).where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.connection_id == connection_id,
                OmnichannelMessage.direction == MessageDirection.outbound,
                OmnichannelMessage.author_type == MessageAuthorType.agent,
                OmnichannelMessage.occurred_at >= reference - window,
            )
        )
        return int(count or 0)

    @classmethod
    def _quiet_period_end(
        cls,
        now: datetime,
        settings: MessagingCadenceSettingsRead,
    ) -> datetime | None:
        if not settings.quiet_hours_enabled or settings.quiet_hours_start == settings.quiet_hours_end:
            return None
        zone = ZoneInfo(settings.timezone)
        local_now = now.astimezone(zone)
        hour = local_now.hour
        start = settings.quiet_hours_start
        end = settings.quiet_hours_end
        in_quiet = (start < end and start <= hour < end) or (
            start > end and (hour >= start or hour < end)
        )
        if not in_quiet:
            return None
        end_day = local_now.date()
        if start > end and hour >= start:
            end_day += timedelta(days=1)
        local_end = datetime.combine(end_day, time(hour=end), tzinfo=zone)
        return local_end.astimezone(UTC)

    async def _workspace(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> Workspace:
        query = select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.deleted_at.is_(None),
            Workspace.is_active.is_(True),
        )
        if for_update:
            query = query.with_for_update()
        workspace = await self.db.scalar(query)
        if workspace is None:
            raise LookupError("Workspace não encontrado.")
        return workspace

    @staticmethod
    def _deterministic_between(minimum: int, maximum: int, seed: str) -> int:
        if maximum <= minimum:
            return minimum
        ratio = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
        return round(minimum + (maximum - minimum) * ratio)

    @staticmethod
    def _with_jitter(value: int, percent: int, seed: str) -> int:
        if value <= 0 or percent <= 0:
            return max(0, value)
        ratio = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[8:16], 16) / 0xFFFFFFFF
        factor = 1 + ((ratio * 2) - 1) * (percent / 100)
        return max(0, round(value * factor))

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


__all__ = ["DEFAULT_MESSAGING_CADENCE", "MessagingCadenceService"]
