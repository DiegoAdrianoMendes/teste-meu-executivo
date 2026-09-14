"""Dormente #9 — estado por task individual do Celery Beat.

Cobre o risco operacional real: uma task periódica falhar em silêncio e
não aparecer em superfície nenhuma.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.periodic_task_run import (
    TASK_RUN_ERROR_MESSAGE_MAX_CHARS,
    TASK_RUN_STATUS_ERROR,
    TASK_RUN_STATUS_SUCCESS,
    PeriodicTaskRun,
)
from app.services.periodic_task_health_service import (
    STATUS_NEVER_RUN,
    PeriodicTaskHealthService,
    beat_entries,
    beat_task_names,
)
from app.workers.periodic_task_tracking import (
    record_task_failed,
    record_task_started,
    record_task_succeeded,
)

DUNNING_TASK = "finance_tasks.dunning_tick"


@pytest.mark.asyncio
async def test_beat_schedule_is_read_from_celery_and_covers_the_dunning_tick():
    names = beat_task_names()

    assert DUNNING_TASK in names, "o tick da régua de cobrança precisa ser observável"
    assert len(beat_entries()) >= len(names) >= 20


@pytest.mark.asyncio
async def test_instrumentation_records_success(db_session):
    started = datetime.now(UTC) - timedelta(seconds=3)
    await record_task_started(db_session, DUNNING_TASK, started_at=started)
    await record_task_succeeded(db_session, DUNNING_TASK)

    record = (
        await db_session.execute(
            select(PeriodicTaskRun).where(PeriodicTaskRun.task_name == DUNNING_TASK)
        )
    ).scalar_one()

    assert record.last_status == TASK_RUN_STATUS_SUCCESS
    assert record.success_count == 1
    assert record.error_count == 0
    assert record.last_error_type is None
    assert record.last_duration_ms is not None and record.last_duration_ms >= 3000


@pytest.mark.asyncio
async def test_instrumentation_records_error_with_truncated_message(db_session):
    await record_task_started(db_session, DUNNING_TASK)
    await record_task_failed(
        db_session,
        DUNNING_TASK,
        error=ValueError("boleto " + "x" * 900),
    )

    record = (
        await db_session.execute(
            select(PeriodicTaskRun).where(PeriodicTaskRun.task_name == DUNNING_TASK)
        )
    ).scalar_one()

    assert record.last_status == TASK_RUN_STATUS_ERROR
    assert record.error_count == 1
    assert record.last_error_type == "ValueError"
    assert record.last_error_message is not None
    assert record.last_error_message.startswith("boleto ")
    assert len(record.last_error_message) == TASK_RUN_ERROR_MESSAGE_MAX_CHARS
    assert record.last_error_at is not None


@pytest.mark.asyncio
async def test_overview_marks_never_run_tasks_honestly(db_session):
    overview = await PeriodicTaskHealthService(db_session).overview()

    assert len(overview["tasks"]) == len(beat_entries())
    assert all(task["status"] == STATUS_NEVER_RUN for task in overview["tasks"])
    # Nunca executada não é sucesso, não é zero e não é "atrasada":
    # sem última execução não há âncora para calcular o próximo disparo.
    assert all(task["isLate"] is None for task in overview["tasks"])
    assert all(task["lastFinishedAt"] is None for task in overview["tasks"])
    assert overview["summary"]["neverRun"] == len(overview["tasks"])
    assert overview["summary"]["healthy"] == 0


@pytest.mark.asyncio
async def test_overview_flags_failing_and_late_tasks(db_session):
    stale = datetime.now(UTC) - timedelta(days=2)
    await record_task_started(db_session, DUNNING_TASK, started_at=stale)
    await record_task_failed(
        db_session, DUNNING_TASK, error=RuntimeError("Asaas fora do ar"), finished_at=stale
    )

    overview = await PeriodicTaskHealthService(db_session).overview()
    dunning = next(t for t in overview["tasks"] if t["taskName"] == DUNNING_TASK)

    assert dunning["status"] == TASK_RUN_STATUS_ERROR
    assert dunning["errorType"] == "RuntimeError"
    assert dunning["isLate"] is True
    assert overview["summary"]["failing"] == 1
    assert overview["summary"]["late"] == 1
    # As demais continuam honestamente marcadas como nunca executadas.
    assert overview["summary"]["neverRun"] == len(overview["tasks"]) - 1


@pytest.mark.asyncio
async def test_status_tasks_endpoint_lists_every_beat_task(platform_client, db_session):
    await record_task_started(db_session, DUNNING_TASK)
    await record_task_failed(db_session, DUNNING_TASK, error=RuntimeError("Asaas fora do ar"))
    await db_session.commit()

    response = await platform_client.get("/api/v1/status/tasks")

    assert response.status_code == 200
    payload = response.json()
    returned = {task["taskName"] for task in payload["tasks"]}
    assert returned == beat_task_names()
    assert payload["summary"]["total"] == len(payload["tasks"])

    dunning = next(t for t in payload["tasks"] if t["taskName"] == DUNNING_TASK)
    assert dunning["status"] == TASK_RUN_STATUS_ERROR
    assert dunning["errorType"] == "RuntimeError"
    # Mensagem crua NÃO vai para superfície pública.
    assert "Asaas" not in response.text

    never_run = [t for t in payload["tasks"] if t["taskName"] != DUNNING_TASK]
    assert never_run and all(t["status"] == STATUS_NEVER_RUN for t in never_run)


@pytest.mark.asyncio
async def test_status_tasks_endpoint_is_closed_to_anonymous_visitors(unauthenticated_client):
    """A página /status é pública de propósito, mas ela responde "no ar / fora
    do ar". O inventário por task é outra coisa: enumera as rotinas internas do
    deploy (campanha de WhatsApp, hidratação de lead, régua de cobrança, OAuth
    Meta) com contador de falha por rotina. Isso é mapa de arquitetura e estado
    operacional, e não pode sair para visitante anônimo."""
    response = await unauthenticated_client.get("/api/v1/status/tasks")

    assert response.status_code in (401, 403)
    # E o agregado público continua público — a restrição é cirúrgica.
    public = await unauthenticated_client.get("/api/v1/status")
    assert public.status_code == 200
