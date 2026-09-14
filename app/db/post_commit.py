"""Post-commit dispatch for advisory notifications and durable-work wakeups."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.core.logging_privacy import safe_error_type

_REALTIME_EVENTS_KEY = "klm_post_commit_realtime_events"
_OPERATIONAL_ALERTS_KEY = "klm_post_commit_operational_alerts"
_NATIVE_OUTBOX_WAKEUPS_KEY = "klm_post_commit_native_outbox_wakeups"
_AGENT_RUN_WAKEUPS_KEY = "klm_post_commit_agent_run_wakeups"
_MEETING_NOTIFICATION_WAKEUPS_KEY = "klm_post_commit_meeting_notification_wakeups"
_TASK_REMINDER_WAKEUPS_KEY = "klm_post_commit_task_reminder_wakeups"
_FUNNEL_AUTOMATION_WAKEUPS_KEY = "klm_post_commit_funnel_automation_wakeups"
_ADVISORY_DISPATCH_TIMEOUT_SECONDS = 1.0
_BROKER_WAKEUP_TIMEOUT_SECONDS = 1.0


async def _run_bounded(
    operation: Awaitable[Any],
    *,
    timeout_seconds: float,
) -> Any:
    return await asyncio.wait_for(operation, timeout=timeout_seconds)


def queue_realtime_event(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    event_type: str,
    data: dict[str, Any],
) -> None:
    events = session.info.setdefault(_REALTIME_EVENTS_KEY, [])
    events.append(
        {
            "workspace_id": workspace_id,
            "event_type": event_type,
            "data": data,
        }
    )


def discard_realtime_events(session: AsyncSession) -> None:
    session.info.pop(_REALTIME_EVENTS_KEY, None)
    session.info.pop(_OPERATIONAL_ALERTS_KEY, None)
    session.info.pop(_NATIVE_OUTBOX_WAKEUPS_KEY, None)
    session.info.pop(_AGENT_RUN_WAKEUPS_KEY, None)
    session.info.pop(_MEETING_NOTIFICATION_WAKEUPS_KEY, None)
    session.info.pop(_TASK_REMINDER_WAKEUPS_KEY, None)
    session.info.pop(_FUNNEL_AUTOMATION_WAKEUPS_KEY, None)


def queue_agent_run_wakeup(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    agent_run_id: UUID,
    task_id: str,
) -> None:
    wakeups = session.info.setdefault(_AGENT_RUN_WAKEUPS_KEY, set())
    wakeups.add((workspace_id, agent_run_id, task_id))


def queue_native_outbox_wakeup(
    session: AsyncSession,
    *,
    workspace_id: UUID,
) -> None:
    wakeups = session.info.setdefault(_NATIVE_OUTBOX_WAKEUPS_KEY, set())
    wakeups.add(workspace_id)


def queue_meeting_notification_wakeup(
    session: AsyncSession,
    *,
    workspace_id: UUID,
) -> None:
    """Wake the meeting outbox only after the surrounding commit succeeds."""
    wakeups = session.info.setdefault(_MEETING_NOTIFICATION_WAKEUPS_KEY, set())
    wakeups.add(workspace_id)


def queue_task_reminder_wakeup(
    session: AsyncSession,
    *,
    workspace_id: UUID,
) -> None:
    """Wake the durable task-reminder outbox after its transaction commits."""
    wakeups = session.info.setdefault(_TASK_REMINDER_WAKEUPS_KEY, set())
    wakeups.add(workspace_id)


def queue_funnel_automation_wakeup(
    session: AsyncSession, *, workspace_id: UUID, operation_id: str
) -> None:
    wakeups = session.info.setdefault(_FUNNEL_AUTOMATION_WAKEUPS_KEY, set())
    wakeups.add((workspace_id, operation_id))


def queue_operational_alert(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    alert_type: str,
    entity_id: UUID,
) -> None:
    alerts = session.info.setdefault(_OPERATIONAL_ALERTS_KEY, [])
    alerts.append(
        {
            "workspace_id": workspace_id,
            "alert_type": alert_type,
            "entity_id": entity_id,
        }
    )


async def publish_committed_realtime_events(session: AsyncSession) -> None:
    # Wake durable intent first. Realtime and alerts are advisory and must never
    # delay the workers that can recover committed state independently.
    wakeups = set(session.info.pop(_NATIVE_OUTBOX_WAKEUPS_KEY, set()))
    for workspace_id in wakeups:
        try:
            from app.workers.native_outbox_tasks import process_native_outbox_workspace

            await _run_bounded(
                asyncio.to_thread(
                    process_native_outbox_workspace.delay,
                    str(workspace_id),
                ),
                timeout_seconds=_BROKER_WAKEUP_TIMEOUT_SECONDS,
            )
        except Exception as error:
            # The durable event remains pending and the periodic recovery task will
            # retry it. Broker availability must not invalidate the domain commit.
            logger.warning(
                "post_commit_native_outbox_wakeup_failed",
                workspace_id=str(workspace_id),
                error_type=safe_error_type(error),
            )

    agent_run_wakeups = set(session.info.pop(_AGENT_RUN_WAKEUPS_KEY, set()))
    for workspace_id, agent_run_id, task_id in agent_run_wakeups:
        try:
            from app.workers.agent_tasks import execute_agent_run

            await _run_bounded(
                asyncio.to_thread(
                    execute_agent_run.apply_async,
                    args=[str(agent_run_id), str(workspace_id)],
                    task_id=task_id,
                ),
                timeout_seconds=_BROKER_WAKEUP_TIMEOUT_SECONDS,
            )
        except Exception as error:
            # AgentRun is the durable intent. Beat recovery retries queued runs after
            # the dispatch lease expires, so broker failure is advisory here.
            logger.warning(
                "post_commit_agent_run_wakeup_failed",
                agent_run_id=str(agent_run_id),
                workspace_id=str(workspace_id),
                error_type=safe_error_type(error),
            )

    funnel_wakeups = set(session.info.pop(_FUNNEL_AUTOMATION_WAKEUPS_KEY, set()))
    for workspace_id, operation_id in funnel_wakeups:
        try:
            from app.workers.funnel_automation_tasks import process_funnel_automation

            await _run_bounded(
                asyncio.to_thread(
                    process_funnel_automation.apply_async,
                    args=[str(workspace_id), operation_id],
                    task_id=f"funnel-automation:{operation_id}",
                ),
                timeout_seconds=_BROKER_WAKEUP_TIMEOUT_SECONDS,
            )
        except Exception as error:
            logger.warning(
                "post_commit_funnel_automation_wakeup_failed",
                operation_id=operation_id,
                error_type=safe_error_type(error),
            )

    meeting_wakeups = set(session.info.pop(_MEETING_NOTIFICATION_WAKEUPS_KEY, set()))
    for workspace_id in meeting_wakeups:
        try:
            from app.workers.meeting_notification_tasks import (
                process_meeting_notification_workspace,
            )

            await _run_bounded(
                asyncio.to_thread(
                    process_meeting_notification_workspace.apply_async,
                    args=[str(workspace_id)],
                    ignore_result=True,
                    retry=False,
                ),
                timeout_seconds=_BROKER_WAKEUP_TIMEOUT_SECONDS,
            )
        except Exception as error:
            # The durable row remains pending and Celery beat recovers it. A
            # broker outage after commit can never turn a successful API call
            # into a false 500.
            logger.warning(
                "post_commit_meeting_notification_wakeup_failed",
                workspace_id=str(workspace_id),
                error_type=safe_error_type(error),
            )

    task_reminder_wakeups = set(session.info.pop(_TASK_REMINDER_WAKEUPS_KEY, set()))
    for workspace_id in task_reminder_wakeups:
        try:
            from app.workers.task_reminder_tasks import process_task_reminder_workspace

            await _run_bounded(
                asyncio.to_thread(
                    process_task_reminder_workspace.apply_async,
                    args=[str(workspace_id)],
                    ignore_result=True,
                    retry=False,
                ),
                timeout_seconds=_BROKER_WAKEUP_TIMEOUT_SECONDS,
            )
        except Exception as error:
            # The durable row remains pending and Celery beat recovers it.
            logger.warning(
                "post_commit_task_reminder_wakeup_failed",
                workspace_id=str(workspace_id),
                error_type=safe_error_type(error),
            )

    events = list(session.info.pop(_REALTIME_EVENTS_KEY, []))
    if events:
        from app.websockets.manager import ws_manager

        for event in events:
            try:
                await _run_bounded(
                    ws_manager.broadcast_board_update(
                        event["event_type"],
                        event["data"],
                        workspace_id=event["workspace_id"],
                    ),
                    timeout_seconds=_ADVISORY_DISPATCH_TIMEOUT_SECONDS,
                )
                if str(event["event_type"]).startswith("SUPPORT_"):
                    from app.websockets.platform_manager import platform_ws_manager

                    await _run_bounded(
                        platform_ws_manager.publish_support_signal(
                            workspace_id=event["workspace_id"],
                            event_type=event["event_type"],
                            data=event["data"],
                        ),
                        timeout_seconds=_ADVISORY_DISPATCH_TIMEOUT_SECONDS,
                    )
            except Exception as error:
                # Realtime is advisory. A committed domain transaction must never be
                # reported as failed because a socket or Redis became unavailable.
                logger.warning(
                    "post_commit_realtime_publish_failed",
                    event_type=event["event_type"],
                    error_type=safe_error_type(error),
                )

    alerts = list(session.info.pop(_OPERATIONAL_ALERTS_KEY, []))
    for alert in alerts:
        try:
            from app.db.tenant import set_workspace_context
            from app.services.operational_alert_service import OperationalAlertService

            await set_workspace_context(session, alert["workspace_id"])
            service = OperationalAlertService(session)
            if alert["alert_type"] == "contract_signed":
                await _run_bounded(
                    service.notify_contract_signed(alert["entity_id"]),
                    timeout_seconds=_ADVISORY_DISPATCH_TIMEOUT_SECONDS,
                )
            else:
                raise ValueError("Tipo de alerta pós-commit desconhecido")
        except Exception as error:
            logger.warning(
                "post_commit_operational_alert_failed",
                alert_type=alert["alert_type"],
                error_type=safe_error_type(error),
            )
