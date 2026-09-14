"""Saúde por task individual do Celery Beat.

Fecha o dormente #9 (`docs/AUDITORIA_FINAL_UX_DORMENTES_2026-07.md`): o
`GET /status` só expõe a CONTAGEM de workers vivos, então uma única task
periódica que falha em silêncio (o tick da régua de cobrança, por
exemplo) não aparece em lugar nenhum enquanto o worker continuar de pé.

Regras de honestidade obrigatórias:
  * task que NUNCA rodou é `never_run` — nunca `success`, nunca zero;
  * atraso de task que nunca rodou é `None` (indeterminado), porque não
    existe âncora `last_run_at` para calcular o próximo disparo;
  * o atraso é derivado do PRÓPRIO objeto de schedule do beat
    (`schedule.remaining_estimate`), não de uma reimplementação de cron.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.periodic_task_run import (
    TASK_RUN_STATUS_ERROR,
    TASK_RUN_STATUS_RUNNING,
    TASK_RUN_STATUS_SUCCESS,
    PeriodicTaskRun,
)

#: Folga antes de chamar uma task de atrasada. O beat tem jitter próprio e
#: o worker pode estar com a fila cheia; sem folga o painel vira ruído.
LATE_GRACE = timedelta(seconds=120)

STATUS_NEVER_RUN = "never_run"


def beat_entries() -> list[dict]:
    """Lê o beat_schedule REAL do Celery (fonte única, sem lista paralela)."""
    from app.workers.celery_app import celery_app

    entries: list[dict] = []
    schedule = getattr(celery_app.conf, "beat_schedule", None) or {}
    for schedule_name, definition in schedule.items():
        task_name = definition.get("task")
        if not task_name:
            continue
        entries.append(
            {
                "schedule_name": schedule_name,
                "task_name": task_name,
                "schedule": definition.get("schedule"),
            }
        )
    entries.sort(key=lambda entry: entry["schedule_name"])
    return entries


def beat_task_names() -> set[str]:
    return {entry["task_name"] for entry in beat_entries()}


def _describe_schedule(schedule) -> str:
    if schedule is None:
        return "indefinido"
    if isinstance(schedule, (int, float)):
        return f"a cada {int(schedule)}s"
    return str(schedule)


def _expected_next_run(schedule, last_run_at: datetime | None) -> datetime | None:
    """Próximo disparo esperado a partir da última execução observada."""
    if schedule is None or last_run_at is None:
        return None
    if isinstance(schedule, (int, float)):
        return last_run_at + timedelta(seconds=float(schedule))
    remaining = getattr(schedule, "remaining_estimate", None)
    if remaining is None:
        return None
    try:
        delta = remaining(last_run_at)
    except Exception:  # schedule exótico não pode derrubar o painel
        return None
    if not isinstance(delta, timedelta):
        return None
    return last_run_at + delta


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _aware(value: datetime | None) -> datetime | None:
    """SQLite devolve datetime naive; comparar com `now(UTC)` exige tz."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class PeriodicTaskHealthService:
    """Read model por task periódica, sobre `periodic_task_runs`."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _records_by_task(self) -> dict[str, PeriodicTaskRun]:
        result = await self.db.execute(select(PeriodicTaskRun))
        return {record.task_name: record for record in result.scalars().all()}

    async def overview(self, *, now: datetime | None = None) -> dict:
        moment = now or datetime.now(UTC)
        records = await self._records_by_task()

        tasks: list[dict] = []
        for entry in beat_entries():
            record = records.get(entry["task_name"])
            tasks.append(self._serialize(entry, record, moment))

        return {
            "generatedAt": moment.isoformat(),
            "graceSeconds": int(LATE_GRACE.total_seconds()),
            "summary": {
                "total": len(tasks),
                "failing": sum(1 for task in tasks if task["status"] == TASK_RUN_STATUS_ERROR),
                "late": sum(1 for task in tasks if task["isLate"] is True),
                "neverRun": sum(1 for task in tasks if task["status"] == STATUS_NEVER_RUN),
                "healthy": sum(
                    1
                    for task in tasks
                    if task["status"] == TASK_RUN_STATUS_SUCCESS and task["isLate"] is not True
                ),
            },
            "tasks": tasks,
        }

    @staticmethod
    def _serialize(entry: dict, record: PeriodicTaskRun | None, moment: datetime) -> dict:
        base = {
            "scheduleName": entry["schedule_name"],
            "taskName": entry["task_name"],
            "scheduleDescription": _describe_schedule(entry["schedule"]),
        }

        if record is None or record.last_status is None:
            # Honestidade: nunca executada não é sucesso e não é zero.
            return {
                **base,
                "status": STATUS_NEVER_RUN,
                "lastStartedAt": None,
                "lastFinishedAt": None,
                "lastSuccessAt": None,
                "lastErrorAt": None,
                "lastDurationMs": None,
                "errorType": None,
                "successCount": 0,
                "errorCount": 0,
                "isLate": None,
                "expectedNextRunAt": None,
            }

        anchor = _aware(record.last_finished_at) or _aware(record.last_started_at)
        expected_next = _expected_next_run(entry["schedule"], anchor)
        is_late: bool | None = None
        if expected_next is not None:
            is_late = moment > expected_next + LATE_GRACE
        if record.last_status == TASK_RUN_STATUS_RUNNING:
            # Ainda rodando: o "atraso" só faz sentido depois que terminar.
            is_late = None

        return {
            **base,
            "status": record.last_status,
            "lastStartedAt": _isoformat(_aware(record.last_started_at)),
            "lastFinishedAt": _isoformat(_aware(record.last_finished_at)),
            "lastSuccessAt": _isoformat(_aware(record.last_success_at)),
            "lastErrorAt": _isoformat(_aware(record.last_error_at)),
            "lastDurationMs": record.last_duration_ms,
            # A mensagem crua fica só no banco/log: `/status` é público e
            # `str(exc)` costuma carregar payload do tenant.
            "errorType": record.last_error_type,
            "successCount": record.success_count,
            "errorCount": record.error_count,
            "isLate": is_late,
            "expectedNextRunAt": _isoformat(expected_next),
        }
