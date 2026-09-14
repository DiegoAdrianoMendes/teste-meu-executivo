"""Quiet hours no fuso do workspace, não UTC fixo (baixo 8 da auditoria Meta
23/07/2026). Ver docs/AUDITORIA_META_CANAIS_2026-07-23.md."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.omnichannel_orchestration_service import (
    DEFAULT_WORKSPACE_TIMEZONE,
    OmnichannelOrchestrationService,
)


def test_quiet_hours_respect_explicit_timezone_across_utc_boundary():
    # 02:00 UTC == 23:00 America/Sao_Paulo (UTC-3) do dia anterior.
    now_utc = datetime(2026, 7, 23, 2, 0, tzinfo=UTC)
    sao_paulo = ZoneInfo("America/Sao_Paulo")

    quiet_hours = {"enabled": True, "startHour": 22, "endHour": 6}

    # Em UTC puro, 02:00 cai dentro da janela 22-6 também (falso positivo por
    # coincidência não prova nada); usamos uma janela que só bloqueia à noite
    # local para diferenciar de fato UTC vs BR.
    blocked_in_br_night = OmnichannelOrchestrationService._rule_quiet_hours_blocked(
        quiet_hours, now_utc, workspace_timezone=sao_paulo
    )
    assert blocked_in_br_night is True

    # Mesma instância de tempo, mas avaliada como se already fosse hora local
    # (sem conversão) usando um horário que só bloqueia de dia em UTC.
    quiet_hours_day = {"enabled": True, "startHour": 1, "endHour": 3}
    blocked_utc_naive = OmnichannelOrchestrationService._rule_quiet_hours_blocked(
        quiet_hours_day, now_utc, workspace_timezone=None
    )
    blocked_br = OmnichannelOrchestrationService._rule_quiet_hours_blocked(
        quiet_hours_day, now_utc, workspace_timezone=sao_paulo
    )
    # 02:00 UTC bate na janela 1-3 (UTC), mas em BR são 23:00 do dia anterior,
    # fora da janela 1-3.
    assert blocked_utc_naive is True
    assert blocked_br is False


def test_quiet_hours_without_timezone_falls_back_to_naive_hour():
    now = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
    quiet_hours = {"enabled": True, "startHour": 9, "endHour": 11}
    assert OmnichannelOrchestrationService._rule_quiet_hours_blocked(quiet_hours, now) is True


@pytest.mark.asyncio
async def test_resolve_workspace_timezone_reads_workspace_settings(db_session):
    workspace = Workspace(
        name="WS BR",
        slug=f"ws-tz-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={"timezone": "America/Manaus"},
    )
    db_session.add(workspace)
    await db_session.flush()

    service = OmnichannelOrchestrationService(db_session)
    resolved = await service._resolve_workspace_timezone(workspace.id)
    assert str(resolved) == "America/Manaus"


@pytest.mark.asyncio
async def test_resolve_workspace_timezone_defaults_to_sao_paulo_when_unset(db_session):
    workspace = Workspace(
        name="WS sem fuso",
        slug=f"ws-tz-default-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()

    service = OmnichannelOrchestrationService(db_session)
    resolved = await service._resolve_workspace_timezone(workspace.id)
    assert str(resolved) == DEFAULT_WORKSPACE_TIMEZONE


@pytest.mark.asyncio
async def test_resolve_workspace_timezone_falls_back_on_invalid_value(db_session):
    workspace = Workspace(
        name="WS fuso invalido",
        slug=f"ws-tz-invalid-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={"timezone": "Not/A_Real_Zone"},
    )
    db_session.add(workspace)
    await db_session.flush()

    service = OmnichannelOrchestrationService(db_session)
    resolved = await service._resolve_workspace_timezone(workspace.id)
    assert str(resolved) == DEFAULT_WORKSPACE_TIMEZONE
