"""Durable worker for Meta and TikTok locator-only Lead Ads callbacks."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.post_commit import queue_realtime_event
from app.db.session import WorkerSessionLocal
from app.db.tenant import set_workspace_context
from app.db.worker_discovery import set_worker_discovery_context
from app.models.omnichannel_v6 import LeadHydrationJob, OmnichannelChannelConnection
from app.models.workspace import Workspace
from app.services.lead_capture_adapter_service import LeadCaptureAdapterService
from app.services.lead_hydration_service import append_hydration_event
from app.services.lead_hydration_transport import (
    LeadHydrationError,
    LeadHydrationFetcher,
    LeadHydrationReceipt,
    LeadHydrationRequest,
)
from app.services.omnichannel_v6_service import OmnichannelV6Service
from app.workers.celery_app import celery_app
from app.workers.fanout import FanoutMetrics, bounded_batch_size

HYDRATION_LEASE = timedelta(minutes=5)
WORKSPACE_BATCH_SIZE = 64
TOTAL_BATCH_SIZE = 200


class LeadHydrationFencedError(RuntimeError):
    """A stale worker attempted to settle a newer lease generation."""


class _UnconfiguredFetcher:
    async def fetch(self, request: LeadHydrationRequest) -> LeadHydrationReceipt:
        raise LeadHydrationError(
            "O conector oficial para buscar os dados do lead ainda não foi homologado.",
            code="adapter_not_configured",
            retryable=False,
        )


FetcherFactory = Callable[[OmnichannelChannelConnection], LeadHydrationFetcher]


def _default_fetcher_factory(
    connection: OmnichannelChannelConnection,
) -> LeadHydrationFetcher:
    return _UnconfiguredFetcher()


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _retry_at(job_id: UUID, attempts: int, *, now: datetime) -> datetime:
    exponent = max(0, min(attempts - 1, 8))
    base_seconds = min(30 * (2**exponent), 1800)
    jitter = int(hashlib.sha256(str(job_id).encode()).hexdigest()[:2], 16) % 16
    return now + timedelta(seconds=base_seconds + jitter)


def _due_predicate(now: datetime):
    ready = and_(
        LeadHydrationJob.state.in_(["queued", "retrying"]),
        or_(
            LeadHydrationJob.next_attempt_at.is_(None),
            LeadHydrationJob.next_attempt_at <= now,
        ),
    )
    abandoned = and_(
        LeadHydrationJob.state == "processing",
        or_(
            LeadHydrationJob.lease_expires_at.is_(None),
            LeadHydrationJob.lease_expires_at <= now,
        ),
    )
    return or_(ready, abandoned)


async def _due_workspace_ids(
    *,
    session_factory=WorkerSessionLocal,
    now: datetime | None = None,
    batch_size: int = WORKSPACE_BATCH_SIZE,
) -> list[UUID]:
    reference = _aware(now)
    async with session_factory() as db:
        # Sem isto, a leitura sob FORCE RLS devolve zero linhas e o beat despacha
        # nada: ver app/db/worker_discovery.py (S6).
        await set_worker_discovery_context(db, family="lead_hydration")
        return list(
            (
                await db.scalars(
                    select(LeadHydrationJob.workspace_id)
                    .join(Workspace, Workspace.id == LeadHydrationJob.workspace_id)
                    .where(
                        Workspace.deleted_at.is_(None),
                        Workspace.is_active.is_(True),
                        _due_predicate(reference),
                    )
                    .group_by(LeadHydrationJob.workspace_id)
                    .order_by(func.min(LeadHydrationJob.created_at))
                    .limit(bounded_batch_size(batch_size))
                )
            ).all()
        )


async def _claim(
    db: AsyncSession,
    job: LeadHydrationJob,
    *,
    now: datetime,
) -> tuple[str, int]:
    previous = job.state
    owner = str(uuid4())
    generation = int(job.lease_generation or 0) + 1
    job.state = "processing"
    job.lease_owner = owner
    job.lease_generation = generation
    job.lease_expires_at = now + HYDRATION_LEASE
    job.last_error_code = None
    job.last_error_message = None
    append_hydration_event(
        db,
        job,
        event_type="claimed",
        previous_state=previous,
        next_state="processing",
        idempotency_key=f"lead-hydration-claim:{job.id}:{generation}",
        payload={"leaseGeneration": generation},
        occurred_at=now,
        metadata={"leaseExpiresAt": job.lease_expires_at.isoformat()},
    )
    await db.commit()
    return owner, generation


async def _claimed_job(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    job_id: UUID,
    lease_owner: str,
    generation: int,
    lock: bool = False,
) -> LeadHydrationJob | None:
    query = select(LeadHydrationJob).where(
        LeadHydrationJob.workspace_id == workspace_id,
        LeadHydrationJob.id == job_id,
        LeadHydrationJob.state == "processing",
        LeadHydrationJob.lease_owner == lease_owner,
        LeadHydrationJob.lease_generation == generation,
    )
    if lock:
        query = query.with_for_update()
    return await db.scalar(query)


async def _prepare_fetch(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    job_id: UUID,
    lease_owner: str,
    generation: int,
    fetcher_factory: FetcherFactory,
) -> tuple[LeadHydrationRequest, LeadHydrationFetcher] | None:
    job = await _claimed_job(
        db,
        workspace_id=workspace_id,
        job_id=job_id,
        lease_owner=lease_owner,
        generation=generation,
    )
    if job is None:
        raise LeadHydrationFencedError("Lease substituído antes da busca.")
    if not external_delivery_allowed():
        previous = job.state
        job.state = "blocked_external"
        job.completed_at = datetime.now(UTC)
        job.last_error_code = "external_gate_closed"
        job.last_error_message = "Busca externa bloqueada pelo gate do Meu Executivo."
        append_hydration_event(
            db,
            job,
            event_type="blocked_external",
            previous_state=previous,
            next_state=job.state,
            idempotency_key=f"lead-hydration-blocked:{job.id}:{generation}",
            payload={"externalRequests": 0},
            error_code=job.last_error_code,
            error_message=job.last_error_message,
        )
        job.lease_owner = None
        job.lease_expires_at = None
        await db.commit()
        return None

    connection = await db.scalar(
        select(OmnichannelChannelConnection).where(
            OmnichannelChannelConnection.workspace_id == workspace_id,
            OmnichannelChannelConnection.id == job.connection_id,
        )
    )
    if connection is None:
        raise LeadHydrationError(
            "A conexão do lead não foi encontrada.",
            code="route_unavailable",
            retryable=False,
        )
    if connection.status not in {"connected", "active", "configured_local"}:
        raise LeadHydrationError(
            "A conexão oficial não está ativa.",
            code="connection_inactive",
            retryable=True,
        )
    fetcher = fetcher_factory(connection)
    now = datetime.now(UTC)
    transition = await db.execute(
        update(LeadHydrationJob)
        .where(
            LeadHydrationJob.workspace_id == workspace_id,
            LeadHydrationJob.id == job.id,
            LeadHydrationJob.state == "processing",
            LeadHydrationJob.lease_owner == lease_owner,
            LeadHydrationJob.lease_generation == generation,
            LeadHydrationJob.lease_expires_at > now,
        )
        .values(
            fetch_attempted_at=now,
            attempt_count=LeadHydrationJob.attempt_count + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if transition.rowcount != 1:
        await db.rollback()
        raise LeadHydrationFencedError("Lease expirado antes da busca externa.")
    job.fetch_attempted_at = now
    job.attempt_count += 1
    append_hydration_event(
        db,
        job,
        event_type="fetch_started",
        previous_state="processing",
        next_state="processing",
        idempotency_key=f"lead-hydration-fetch:{job.id}:{generation}",
        payload={"attempt": job.attempt_count},
        occurred_at=now,
        metadata={"externalRequests": 1},
    )
    await db.commit()
    return (
        LeadHydrationRequest(
            workspace_id=workspace_id,
            hydration_job_id=job.id,
            connection_id=connection.id,
            provider=connection.provider,
            connection_key=connection.connection_key,
            external_lead_id=job.external_lead_id,
            external_form_id=job.external_form_id,
            locator_payload=dict(job.locator_payload or {}),
            request_key=f"lead-hydration:{job.id}",
        ),
        fetcher,
    )


async def _settle_success(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    job_id: UUID,
    lease_owner: str,
    generation: int,
    request: LeadHydrationRequest,
    receipt: LeadHydrationReceipt,
) -> None:
    job = await _claimed_job(
        db,
        workspace_id=workspace_id,
        job_id=job_id,
        lease_owner=lease_owner,
        generation=generation,
        lock=True,
    )
    if job is None:
        raise LeadHydrationFencedError("Resultado chegou após a troca do lease.")
    if receipt.provider != request.provider:
        raise LeadHydrationError(
            "A resposta de hidratação veio de outro provedor.",
            code="provider_receipt_mismatch",
            retryable=False,
        )
    if str(receipt.external_lead_id) != request.external_lead_id:
        raise LeadHydrationError(
            "A resposta não corresponde ao identificador solicitado.",
            code="lead_receipt_mismatch",
            retryable=False,
        )
    connection = await db.scalar(
        select(OmnichannelChannelConnection).where(
            OmnichannelChannelConnection.workspace_id == workspace_id,
            OmnichannelChannelConnection.id == request.connection_id,
        )
    )
    if connection is None:
        raise LeadHydrationError(
            "A conexão desapareceu antes da materialização.",
            code="route_unavailable",
            retryable=False,
        )
    try:
        normalized = LeadCaptureAdapterService.normalize(connection, receipt.raw_payload)
    except (TypeError, ValueError, ValidationError) as error:
        raise LeadHydrationError(
            "O provedor ainda não retornou telefone ou e-mail válido para o lead.",
            code="provider_payload_incomplete",
            retryable=True,
        ) from error
    if normalized.external_lead_id != request.external_lead_id:
        raise LeadHydrationError(
            "O payload hidratado possui identificador divergente.",
            code="hydrated_lead_mismatch",
            retryable=False,
        )

    result = await OmnichannelV6Service(db).ingest_lead_capture(
        workspace_id, None, normalized
    )
    now = _aware(receipt.fetched_at)
    job.state = "hydrated"
    job.lead_capture_receipt_id = result["receipt"].id
    job.hydrated_at = now
    job.completed_at = now
    job.next_attempt_at = None
    job.last_error_code = None
    job.last_error_message = None
    job.provider_receipt = {
        "provider": receipt.provider,
        "providerRequestId": receipt.provider_request_id,
        "fetchedAt": now.isoformat(),
        "leadCaptureReceiptId": str(result["receipt"].id),
        "deduplicated": bool(result["deduplicated"]),
        "externalRequests": 1,
    }
    append_hydration_event(
        db,
        job,
        event_type="hydrated",
        previous_state="processing",
        next_state="hydrated",
        idempotency_key=f"lead-hydration-complete:{job.id}:{generation}",
        payload=job.provider_receipt,
        occurred_at=now,
        metadata={
            "matterId": str(result["matter_id"]),
            "threadId": str(result["thread"].id),
        },
    )
    job.lease_owner = None
    job.lease_expires_at = None
    queue_realtime_event(
        db,
        workspace_id=workspace_id,
        event_type="omnichannel.lead_hydration.updated",
        data={
            "hydrationJobId": str(job.id),
            "leadCaptureReceiptId": str(result["receipt"].id),
            "threadId": str(result["thread"].id),
            "state": job.state,
        },
    )
    await db.commit()


async def _settle_failure(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    job_id: UUID,
    lease_owner: str,
    generation: int,
    error: LeadHydrationError,
) -> str:
    job = await _claimed_job(
        db,
        workspace_id=workspace_id,
        job_id=job_id,
        lease_owner=lease_owner,
        generation=generation,
        lock=True,
    )
    if job is None:
        raise LeadHydrationFencedError("Falha chegou após a troca do lease.")
    now = datetime.now(UTC)
    retry = error.retryable and job.attempt_count < job.max_attempts
    next_state = "retrying" if retry else "failed"
    job.state = next_state
    job.next_attempt_at = _retry_at(job.id, job.attempt_count, now=now) if retry else None
    job.completed_at = None if retry else now
    job.last_error_code = error.code
    job.last_error_message = str(error)
    append_hydration_event(
        db,
        job,
        event_type="retry_scheduled" if retry else "failed",
        previous_state="processing",
        next_state=next_state,
        idempotency_key=f"lead-hydration-{next_state}:{job.id}:{generation}",
        payload={
            "attempt": job.attempt_count,
            "nextAttemptAt": job.next_attempt_at,
        },
        occurred_at=now,
        error_code=error.code,
        error_message=str(error),
    )
    job.lease_owner = None
    job.lease_expires_at = None
    queue_realtime_event(
        db,
        workspace_id=workspace_id,
        event_type="omnichannel.lead_hydration.updated",
        data={"hydrationJobId": str(job.id), "state": next_state},
    )
    await db.commit()
    return next_state


async def process_hydration_job(
    workspace_id: UUID,
    job_id: UUID,
    *,
    session_factory=WorkerSessionLocal,
    fetcher_factory: FetcherFactory = _default_fetcher_factory,
    now: datetime | None = None,
) -> dict[str, Any]:
    reference = _aware(now)
    async with session_factory() as db:
        await set_workspace_context(db, workspace_id)
        job = await db.scalar(
            select(LeadHydrationJob)
            .where(
                LeadHydrationJob.workspace_id == workspace_id,
                LeadHydrationJob.id == job_id,
                _due_predicate(reference),
            )
            .with_for_update()
        )
        if job is None:
            return {"status": "skipped", "externalRequests": 0}
        lease_owner, generation = await _claim(db, job, now=reference)

    try:
        async with session_factory() as db:
            await set_workspace_context(db, workspace_id)
            prepared = await _prepare_fetch(
                db,
                workspace_id=workspace_id,
                job_id=job_id,
                lease_owner=lease_owner,
                generation=generation,
                fetcher_factory=fetcher_factory,
            )
        if prepared is None:
            return {"status": "blocked_external", "externalRequests": 0}
        request, fetcher = prepared
        receipt = await fetcher.fetch(request)
        async with session_factory() as db:
            await set_workspace_context(db, workspace_id)
            await _settle_success(
                db,
                workspace_id=workspace_id,
                job_id=job_id,
                lease_owner=lease_owner,
                generation=generation,
                request=request,
                receipt=receipt,
            )
        return {"status": "hydrated", "externalRequests": 1}
    except LeadHydrationFencedError:
        return {"status": "fenced", "externalRequests": 0}
    except LeadHydrationError as error:
        async with session_factory() as db:
            await set_workspace_context(db, workspace_id)
            state = await _settle_failure(
                db,
                workspace_id=workspace_id,
                job_id=job_id,
                lease_owner=lease_owner,
                generation=generation,
                error=error,
            )
        return {"status": state, "externalRequests": 0 if error.code.endswith("gate") else 1}
    except Exception as error:
        logger.error(
            "lead_hydration.unexpected",
            hydration_job_id=str(job_id),
            error_type=safe_error_type(error),
        )
        wrapped = LeadHydrationError(
            "Falha interna temporária na hidratação do lead.",
            code="internal_temporary_error",
            retryable=True,
        )
        async with session_factory() as db:
            await set_workspace_context(db, workspace_id)
            state = await _settle_failure(
                db,
                workspace_id=workspace_id,
                job_id=job_id,
                lease_owner=lease_owner,
                generation=generation,
                error=wrapped,
            )
        return {"status": state, "externalRequests": 1}


async def process_workspace_hydrations(
    workspace_id: UUID,
    *,
    session_factory=WorkerSessionLocal,
    fetcher_factory: FetcherFactory = _default_fetcher_factory,
    batch_size: int = TOTAL_BATCH_SIZE,
    now: datetime | None = None,
) -> dict[str, int]:
    reference = _aware(now)
    async with session_factory() as db:
        await set_workspace_context(db, workspace_id)
        ids = list(
            (
                await db.scalars(
                    select(LeadHydrationJob.id)
                    .where(
                        LeadHydrationJob.workspace_id == workspace_id,
                        _due_predicate(reference),
                    )
                    .order_by(LeadHydrationJob.created_at.asc())
                    .limit(bounded_batch_size(batch_size, maximum=TOTAL_BATCH_SIZE))
                )
            ).all()
        )
    counts: dict[str, int] = {}
    for job_id in ids:
        outcome = await process_hydration_job(
            workspace_id,
            job_id,
            session_factory=session_factory,
            fetcher_factory=fetcher_factory,
            now=reference,
        )
        status = str(outcome["status"])
        counts[status] = counts.get(status, 0) + 1
    return counts


async def process_due_hydrations(
    *,
    session_factory=WorkerSessionLocal,
    batch_size: int = WORKSPACE_BATCH_SIZE,
    publish: Callable[[str], Any] | None = None,
) -> dict[str, int]:
    workspace_ids = await _due_workspace_ids(
        session_factory=session_factory,
        batch_size=batch_size,
    )
    metrics = FanoutMetrics(
        event="lead_hydration_due",
        batch_limit=bounded_batch_size(batch_size),
        discovery_rows=len(workspace_ids),
        discovered_workspaces=len(workspace_ids),
    )
    enqueue = publish or (lambda workspace: process_workspace.delay(workspace))
    for workspace_id in workspace_ids:
        try:
            enqueue(str(workspace_id))
            metrics.enqueued_workspaces += 1
        except Exception as error:
            metrics.publish_failures += 1
            logger.error(
                "lead_hydration.publish_failed",
                workspace_id=str(workspace_id),
                error_type=safe_error_type(error),
            )
    metrics.emit()
    return {
        "workspaces": len(workspace_ids),
        "enqueued": metrics.enqueued_workspaces,
        "publishFailures": metrics.publish_failures,
    }


def _run(coro):
    return asyncio.run(coro)


@celery_app.task(name="lead_hydration_tasks.process_due")
def process_due(batch_size: int = WORKSPACE_BATCH_SIZE) -> dict[str, int]:
    return _run(process_due_hydrations(batch_size=batch_size))


@celery_app.task(name="lead_hydration_tasks.process_workspace")
def process_workspace(workspace_id: str) -> dict[str, int]:
    return _run(process_workspace_hydrations(UUID(workspace_id)))


__all__ = [
    "LeadHydrationFencedError",
    "process_due_hydrations",
    "process_hydration_job",
    "process_workspace_hydrations",
]
