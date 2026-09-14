"""Regras canônicas de calendário para operações do workspace."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace

DEFAULT_WORKSPACE_TIMEZONE = "America/Sao_Paulo"


async def resolve_workspace_timezone(
    db: AsyncSession,
    workspace_id: UUID,
) -> ZoneInfo:
    """Resolve o fuso IANA configurado, com fallback previsível para a base BR."""
    workspace = await db.get(Workspace, workspace_id)
    raw_timezone = (workspace.settings or {}).get("timezone") if workspace else None
    try:
        return ZoneInfo(raw_timezone or DEFAULT_WORKSPACE_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_WORKSPACE_TIMEZONE)


def workspace_date_window_utc(
    date_from: date,
    date_to: date,
    timezone: ZoneInfo,
) -> tuple[datetime, datetime]:
    """Converte dias locais em uma janela UTC semiaberta [início, fim)."""
    if date_to < date_from:
        raise ValueError("A data final deve ser igual ou posterior à inicial.")
    start_at = datetime.combine(date_from, time.min, tzinfo=timezone).astimezone(UTC)
    end_at = datetime.combine(
        date_to + timedelta(days=1),
        time.min,
        tzinfo=timezone,
    ).astimezone(UTC)
    return start_at, end_at
