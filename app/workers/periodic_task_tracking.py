"""Instrumentação CENTRALIZADA das tasks periódicas do Celery Beat.

Não há uma linha de código de telemetria dentro das ~28 tasks: tudo é
feito por signal do Celery (`task_prerun` / `task_postrun` /
`task_failure`), que já recebe o nome da task e o resultado. Só as tasks
presentes no `beat_schedule` são persistidas — task avulsa disparada por
requisição não é problema de saúde do beat e inflaria a tabela.

Ordem real dos signals quando a task levanta exceção:
`task_prerun` -> `task_failure` -> `task_postrun(state="FAILURE")`; por
isso `task_postrun` só marca sucesso quando `state == "SUCCESS"`, e nunca
sobrescreve o erro já registrado.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.models.periodic_task_run import (
    TASK_RUN_ERROR_MESSAGE_MAX_CHARS,
    TASK_RUN_STATUS_ERROR,
    TASK_RUN_STATUS_RUNNING,
    TASK_RUN_STATUS_SUCCESS,
    PeriodicTaskRun,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _duration_ms(started_at: datetime | None, finished_at: datetime) -> int | None:
    started = _aware(started_at)
    if started is None:
        return None
    return max(int((finished_at - started).total_seconds() * 1000), 0)


async def _get_or_create(session, task_name: str) -> PeriodicTaskRun:
    result = await session.execute(
        select(PeriodicTaskRun).where(PeriodicTaskRun.task_name == task_name)
    )
    record = result.scalar_one_or_none()
    if record is None:
        record = PeriodicTaskRun(task_name=task_name, success_count=0, error_count=0)
        session.add(record)
        await session.flush()
    return record


async def record_task_started(
    session, task_name: str, *, started_at: datetime | None = None
) -> PeriodicTaskRun:
    record = await _get_or_create(session, task_name)
    record.last_started_at = started_at or _now()
    record.last_status = TASK_RUN_STATUS_RUNNING
    record.updated_at = _now()
    await session.flush()
    return record


async def record_task_succeeded(
    session, task_name: str, *, finished_at: datetime | None = None
) -> PeriodicTaskRun:
    moment = finished_at or _now()
    record = await _get_or_create(session, task_name)
    record.last_status = TASK_RUN_STATUS_SUCCESS
    record.last_finished_at = moment
    record.last_success_at = moment
    record.last_duration_ms = _duration_ms(record.last_started_at, moment)
    record.last_error_type = None
    record.last_error_message = None
    record.success_count = (record.success_count or 0) + 1
    record.updated_at = moment
    await session.flush()
    return record


async def record_task_failed(
    session,
    task_name: str,
    *,
    error: BaseException | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    finished_at: datetime | None = None,
) -> PeriodicTaskRun:
    moment = finished_at or _now()
    resolved_type = error_type or (type(error).__name__ if error is not None else "UnknownError")
    resolved_message = error_message if error_message is not None else str(error or "")

    record = await _get_or_create(session, task_name)
    record.last_status = TASK_RUN_STATUS_ERROR
    record.last_finished_at = moment
    record.last_error_at = moment
    record.last_duration_ms = _duration_ms(record.last_started_at, moment)
    record.last_error_type = resolved_type[:120]
    record.last_error_message = resolved_message[:TASK_RUN_ERROR_MESSAGE_MAX_CHARS] or None
    record.error_count = (record.error_count or 0) + 1
    record.updated_at = moment
    await session.flush()
    return record


# ── Ponte síncrona (o worker do Celery não roda em event loop) ─────────────


def _is_tracked(task_name: str | None) -> bool:
    if not task_name:
        return False
    try:
        from app.services.periodic_task_health_service import beat_task_names

        return task_name in beat_task_names()
    except Exception:
        return False


def _run_in_worker_session(coro_factory) -> None:
    """Executa a persistência sem jamais derrubar a task instrumentada."""
    from app.core.logging import logger

    async def _runner() -> None:
        from app.db.session import WorkerSessionLocal

        async with WorkerSessionLocal() as session:
            await coro_factory(session)
            await session.commit()

    try:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_runner())
        finally:
            asyncio.set_event_loop(None)
            loop.close()
    except Exception as error:  # telemetria nunca quebra a task de negócio
        logger.warning(
            "periodic_task_tracking.persist_failed", error_type=type(error).__name__
        )


def on_task_prerun(sender=None, task_id=None, task=None, **_kwargs) -> None:
    task_name = getattr(sender, "name", None) or getattr(task, "name", None)
    if not _is_tracked(task_name):
        return
    started_at = _now()
    _run_in_worker_session(
        lambda session: record_task_started(session, task_name, started_at=started_at)
    )


def on_task_postrun(sender=None, task_id=None, task=None, state=None, **_kwargs) -> None:
    task_name = getattr(sender, "name", None) or getattr(task, "name", None)
    if not _is_tracked(task_name) or state != "SUCCESS":
        return
    finished_at = _now()
    _run_in_worker_session(
        lambda session: record_task_succeeded(session, task_name, finished_at=finished_at)
    )


def on_task_failure(sender=None, task_id=None, exception=None, **_kwargs) -> None:
    task_name = getattr(sender, "name", None)
    if not _is_tracked(task_name):
        return
    finished_at = _now()
    _run_in_worker_session(
        lambda session: record_task_failed(
            session, task_name, error=exception, finished_at=finished_at
        )
    )


def register_periodic_task_tracking() -> None:
    """Conecta os signals uma única vez (idempotente via `dispatch_uid`)."""
    from celery.signals import task_failure, task_postrun, task_prerun

    task_prerun.connect(on_task_prerun, weak=False, dispatch_uid="klm_periodic_task_prerun")
    task_postrun.connect(on_task_postrun, weak=False, dispatch_uid="klm_periodic_task_postrun")
    task_failure.connect(on_task_failure, weak=False, dispatch_uid="klm_periodic_task_failure")
