from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.workspace_time_service import workspace_date_window_utc


def test_workspace_date_window_uses_complete_local_day_in_utc():
    start_at, end_at = workspace_date_window_utc(
        date(2026, 7, 24),
        date(2026, 7, 24),
        ZoneInfo("America/Sao_Paulo"),
    )

    assert start_at == datetime(2026, 7, 24, 3, 0, tzinfo=UTC)
    assert end_at == datetime(2026, 7, 25, 3, 0, tzinfo=UTC)
    assert datetime(2026, 7, 25, 0, 30, tzinfo=UTC) < end_at


def test_workspace_date_window_rejects_inverted_period():
    with pytest.raises(
        ValueError,
        match="A data final deve ser igual ou posterior à inicial",
    ):
        workspace_date_window_utc(
            date(2026, 7, 25),
            date(2026, 7, 24),
            ZoneInfo("America/Sao_Paulo"),
        )
