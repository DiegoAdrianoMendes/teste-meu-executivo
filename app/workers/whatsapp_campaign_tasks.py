"""
Tasks Celery para campanhas oficiais de WhatsApp.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.db.session import WorkerSessionLocal as AsyncSessionLocal
from app.db.tenant import set_workspace_context
from app.db.worker_discovery import set_worker_discovery_context
from app.models.klm_operations import KlmAuditEvent
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignSendJobStatus,
    CampaignStatus,
    WhatsAppCampaign,
    WhatsAppCampaignRecipient,
    WhatsAppCampaignSendJob,
)
from app.models.workspace import Workspace
from app.workers.celery_app import celery_app
from app.workers.fanout import FanoutMetrics, bounded_batch_size

CAMPAIGN_WORKSPACE_BATCH_SIZE = 64

# Sweep de claims órfãos (médio 5 da auditoria Meta): um job fica em
# `processing` desde o momento em que um worker o reivindica (commit do claim
# antes do envio). Se o worker morrer entre o claim e o registro do
# resultado, o job nunca sai de `processing` sozinho. Depois desta janela sem
# atualização, consideramos o claim expirado (crash de worker) e falhamos o
# job — NUNCA reenviamos automaticamente, pois o resultado do envio original
# é desconhecido.
ORPHAN_CLAIM_MAX_AGE = timedelta(minutes=30)
ORPHAN_SWEEP_SOFT_TIME_LIMIT = 240
ORPHAN_SWEEP_TIME_LIMIT = 300
ORPHAN_CLAIM_FAILURE_REASON = "claim expirado (crash de worker)"


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _workspace_id(value: UUID | str) -> UUID:
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("workspace_id must be a valid UUID") from exc


async def _due_campaign_workspace_ids(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = CAMPAIGN_WORKSPACE_BATCH_SIZE,
) -> list[UUID]:
    reference_now = now or datetime.now(UTC)
    limit = bounded_batch_size(batch_size)
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as discovery_db:
        # Sem isto, a leitura sob FORCE RLS devolve zero linhas e o beat despacha
        # nada: ver app/db/worker_discovery.py (S6).
        await set_worker_discovery_context(discovery_db, family="whatsapp_campaign_send")
        return list(
            (
                await discovery_db.scalars(
                    select(WhatsAppCampaignSendJob.workspace_id)
                    .join(
                        WhatsAppCampaign,
                        WhatsAppCampaign.id == WhatsAppCampaignSendJob.campaign_id,
                    )
                    .join(
                        Workspace,
                        Workspace.id == WhatsAppCampaignSendJob.workspace_id,
                    )
                    .where(
                        Workspace.deleted_at.is_(None),
                        Workspace.is_active.is_(True),
                        WhatsAppCampaign.status.in_(
                            [CampaignStatus.scheduled, CampaignStatus.running]
                        ),
                        or_(
                            WhatsAppCampaign.scheduled_at.is_(None),
                            WhatsAppCampaign.scheduled_at <= reference_now,
                            WhatsAppCampaign.status == CampaignStatus.running,
                        ),
                        WhatsAppCampaignSendJob.status.in_(
                            [
                                CampaignSendJobStatus.pending,
                                CampaignSendJobStatus.scheduled,
                            ]
                        ),
                        or_(
                            WhatsAppCampaignSendJob.scheduled_for.is_(None),
                            WhatsAppCampaignSendJob.scheduled_for <= reference_now,
                        ),
                    )
                    .group_by(WhatsAppCampaignSendJob.workspace_id)
                    .order_by(func.min(WhatsAppCampaignSendJob.scheduled_for))
                    .limit(limit)
                )
            ).all()
        )


async def _sweep_orphan_campaign_claims(*, session_factory=None) -> int:
    """Descobre os workspaces com job de campanha preso em `processing` e
    despacha um sweep por workspace. Só lê: a marcação acontece em
    `_sweep_orphan_campaign_claims_for_workspace`, no contexto do workspace.
    Sem o escopo de descoberta, a leitura sob FORCE RLS devolvia zero linhas e
    claim órfão nenhum era encerrado (S6)."""
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        await set_worker_discovery_context(db, family="whatsapp_campaign_claims")
        result = await db.execute(
            select(WhatsAppCampaignSendJob.workspace_id)
            .join(Workspace, Workspace.id == WhatsAppCampaignSendJob.workspace_id)
            .where(
                Workspace.deleted_at.is_(None),
                Workspace.is_active.is_(True),
                WhatsAppCampaignSendJob.status == CampaignSendJobStatus.processing,
                WhatsAppCampaignSendJob.last_error.is_(None),
            )
            .distinct()
        )
        workspace_ids = [row[0] for row in result.all()]
    for workspace_id in workspace_ids:
        sweep_orphan_campaign_claims_workspace.apply_async(
            kwargs={"workspace_id": str(workspace_id)}
        )
    if workspace_ids:
        logger.info("whatsapp_campaigns.orphan_claims_sweep_dispatched", count=len(workspace_ids))
    return len(workspace_ids)


async def _sweep_orphan_campaign_claims_for_workspace(
    workspace_id: UUID | str,
    *,
    session_factory=None,
    now: datetime | None = None,
    max_age: timedelta = ORPHAN_CLAIM_MAX_AGE,
) -> int:
    """Marca como `failed` jobs de campanha presos em `processing` cujo claim
    expirou (worker morreu antes de registrar o resultado). Nunca reenvia:
    apenas encerra o job com uma razão legível e um evento de auditoria, para
    que o operador decida se reprocessa manualmente."""
    resolved_workspace_id = _workspace_id(workspace_id)
    reference_now = now or datetime.now(UTC)
    session_maker = session_factory or AsyncSessionLocal
    swept = 0
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        result = await db.execute(
            select(WhatsAppCampaignSendJob)
            .options(selectinload(WhatsAppCampaignSendJob.recipient))
            .where(
                WhatsAppCampaignSendJob.workspace_id == resolved_workspace_id,
                WhatsAppCampaignSendJob.status == CampaignSendJobStatus.processing,
                WhatsAppCampaignSendJob.last_error.is_(None),
            )
            # Duas varreduras do mesmo workspace não gravam dois eventos de auditoria
            # para o mesmo job: quem não pegou o lock pula (no-op em SQLite).
            .with_for_update(skip_locked=True, of=WhatsAppCampaignSendJob)
        )
        jobs = result.scalars().all()
        for job in jobs:
            recipient = job.recipient
            if recipient is None:
                continue
            if recipient.status == CampaignRecipientStatus.delivery_uncertain:
                continue
            claim = (recipient.send_context or {}).get("claim") or {}
            claimed_at_raw = claim.get("claimedAt")
            if not claimed_at_raw:
                # Sem marcador de claim: não temos como avaliar a idade com
                # segurança, então não tocamos (fail-closed do lado do sweep).
                continue
            try:
                claimed_at = datetime.fromisoformat(claimed_at_raw)
            except (TypeError, ValueError):
                continue
            if claimed_at.tzinfo is None:
                claimed_at = claimed_at.replace(tzinfo=UTC)
            if reference_now - claimed_at < max_age:
                continue

            job.status = CampaignSendJobStatus.failed
            job.last_error = ORPHAN_CLAIM_FAILURE_REASON
            job.processed_at = reference_now
            recipient.status = CampaignRecipientStatus.failed
            recipient.last_error = ORPHAN_CLAIM_FAILURE_REASON
            db.add(
                KlmAuditEvent(
                    workspace_id=job.workspace_id,
                    actor_type="system",
                    actor_label="whatsapp_campaign_orphan_claim_sweep",
                    event_type="whatsapp_campaign_job_claim_expired",
                    entity_type="whatsapp_campaign_send_job",
                    entity_id=job.id,
                    summary=ORPHAN_CLAIM_FAILURE_REASON,
                    before_data={"status": CampaignSendJobStatus.processing.value},
                    after_data={
                        "status": CampaignSendJobStatus.failed.value,
                        "claimedAt": claimed_at_raw,
                        "recipientId": str(recipient.id),
                    },
                )
            )
            swept += 1
        if swept:
            await db.commit()
    if swept:
        logger.info("whatsapp_campaigns.orphan_claims_swept", count=swept)
    return swept


@celery_app.task(name="whatsapp_campaign_tasks.sweep_orphan_campaign_claims")
def sweep_orphan_campaign_claims() -> int:
    """Beat: só descobre e despacha (ver S6-C)."""
    return _run_async(_sweep_orphan_campaign_claims())


@celery_app.task(
    name="whatsapp_campaign_tasks.sweep_orphan_campaign_claims_workspace",
    soft_time_limit=ORPHAN_SWEEP_SOFT_TIME_LIMIT,
    time_limit=ORPHAN_SWEEP_TIME_LIMIT,
)
def sweep_orphan_campaign_claims_workspace(workspace_id: str) -> int:
    """Nome próprio para a telemetria do beat não contar cada workspace como um tick."""
    return _run_async(_sweep_orphan_campaign_claims_for_workspace(workspace_id))


@celery_app.task(name="whatsapp_campaign_tasks.process_due_campaigns")
def process_due_campaigns(workspace_id: str | None = None) -> int:
    if workspace_id is None:
        return _run_async(_async_dispatch_due_campaigns())
    return _run_async(_async_process_due_campaigns(workspace_id))


async def _async_dispatch_due_campaigns(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = CAMPAIGN_WORKSPACE_BATCH_SIZE,
) -> int:
    if not external_delivery_allowed():
        logger.info("whatsapp_campaigns.processing_blocked_by_klm_gate")
        return 0

    limit = bounded_batch_size(batch_size)
    workspace_ids = await _due_campaign_workspace_ids(
        session_factory=session_factory,
        now=now,
        batch_size=limit,
    )
    metrics = FanoutMetrics("whatsapp_campaigns.due", limit)
    metrics.discovery_rows = len(workspace_ids)
    metrics.discovered_workspaces = len(workspace_ids)
    for workspace_id in workspace_ids:
        try:
            process_due_campaigns.apply_async(
                kwargs={"workspace_id": str(workspace_id)}
            )
            metrics.enqueued_workspaces += 1
        except Exception:
            metrics.publish_failures += 1
            raise
    metrics.emit()
    return metrics.enqueued_workspaces


async def _async_process_due_campaigns(
    workspace_id: UUID | str,
    *,
    session_factory=None,
    service_factory=None,
) -> int:
    resolved_workspace_id = _workspace_id(workspace_id)
    if not external_delivery_allowed():
        logger.info(
            "whatsapp_campaigns.processing_blocked_by_klm_gate",
            workspace_id=str(resolved_workspace_id),
        )
        return 0

    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        if service_factory is None:
            from app.services.public_api_service import PublicApiService

            service_factory = PublicApiService
        service = service_factory(db)
        processed = await service.campaign_service.process_due_campaigns(
            workspace_id=resolved_workspace_id
        )
        logger.info(
            "whatsapp_campaigns.processed",
            workspace_id=str(resolved_workspace_id),
            campaigns=processed,
        )
        return processed
