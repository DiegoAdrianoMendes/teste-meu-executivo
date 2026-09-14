"""Durable orchestration for locator-only Meta and TikTok lead callbacks."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.post_commit import queue_realtime_event
from app.models.omnichannel_v6 import (
    LeadHydrationEvent,
    LeadHydrationJob,
    OmnichannelChannelConnection,
    OmnichannelPublicIngressReceipt,
)
from app.services.lead_capture_adapter_service import LeadCaptureInspection


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def append_hydration_event(
    db: AsyncSession,
    job: LeadHydrationJob,
    *,
    event_type: str,
    previous_state: str,
    next_state: str,
    idempotency_key: str,
    payload: dict[str, Any],
    occurred_at: datetime | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> LeadHydrationEvent:
    event = LeadHydrationEvent(
        workspace_id=job.workspace_id,
        hydration_job_id=job.id,
        connection_id=job.connection_id,
        provider=job.provider,
        external_lead_id=job.external_lead_id,
        event_type=event_type,
        previous_state=previous_state,
        next_state=next_state,
        idempotency_key=idempotency_key,
        payload_sha256=canonical_hash(payload),
        lease_owner=job.lease_owner,
        lease_generation=job.lease_generation,
        error_code=error_code,
        error_message=error_message,
        occurred_at=occurred_at or datetime.now(UTC),
        event_metadata=metadata or {},
    )
    db.add(event)
    return event


class LeadHydrationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def enqueue(
        self,
        *,
        workspace_id: UUID,
        connection: OmnichannelChannelConnection,
        ingress_receipt: OmnichannelPublicIngressReceipt,
        external_event_id: str,
        inspection: LeadCaptureInspection,
    ) -> tuple[LeadHydrationJob, bool]:
        if not inspection.requires_hydration:
            raise ValueError("Somente notificações incompletas exigem hidratação.")
        if inspection.provider not in {"meta_ads", "tiktok_ads"}:
            raise ValueError("O provedor não possui hidratação nativa configurável.")
        if inspection.provider != connection.provider:
            raise ValueError("O provedor da inspeção diverge da conexão.")
        locator_hash = canonical_hash(inspection.locator_payload)
        query = select(LeadHydrationJob).where(
            LeadHydrationJob.workspace_id == workspace_id,
            LeadHydrationJob.provider == connection.provider,
            LeadHydrationJob.connection_key == connection.connection_key,
            LeadHydrationJob.external_lead_id == inspection.external_lead_id,
        )
        existing = await self.db.scalar(query)
        if existing is not None:
            if (
                existing.external_form_id
                and inspection.external_form_id
                and existing.external_form_id != inspection.external_form_id
            ):
                raise ValueError("O identificador do lead reapareceu em outro formulário.")
            return existing, True

        job = LeadHydrationJob(
            workspace_id=workspace_id,
            connection_id=connection.id,
            public_ingress_receipt_id=ingress_receipt.id,
            provider=connection.provider,
            connection_key=connection.connection_key,
            external_event_id=external_event_id,
            external_lead_id=inspection.external_lead_id,
            external_form_id=inspection.external_form_id,
            locator_payload=inspection.locator_payload,
            payload_sha256=locator_hash,
            state="queued",
            attempt_count=0,
            max_attempts=7,
            provider_receipt={"externalRequests": 0},
        )
        try:
            async with self.db.begin_nested():
                self.db.add(job)
                await self.db.flush()
                append_hydration_event(
                    self.db,
                    job,
                    event_type="created",
                    previous_state="none",
                    next_state="queued",
                    idempotency_key=f"lead-hydration-created:{job.id}",
                    payload=inspection.locator_payload,
                    metadata={
                        "ingressReceiptId": str(ingress_receipt.id),
                        "externalRequests": 0,
                    },
                )
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(query)
            if existing is None:
                raise
            return existing, True

        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="omnichannel.lead_hydration.updated",
            data={
                "hydrationJobId": str(job.id),
                "externalLeadId": job.external_lead_id,
                "provider": job.provider,
                "state": job.state,
            },
        )
        return job, False

    async def get(self, workspace_id: UUID, job_id: UUID) -> LeadHydrationJob:
        job = await self.db.scalar(
            select(LeadHydrationJob).where(
                LeadHydrationJob.workspace_id == workspace_id,
                LeadHydrationJob.id == job_id,
            )
        )
        if job is None:
            raise LookupError("Trabalho de hidratação não encontrado.")
        return job

    async def list(
        self,
        workspace_id: UUID,
        *,
        provider: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[LeadHydrationJob]:
        query = select(LeadHydrationJob).where(
            LeadHydrationJob.workspace_id == workspace_id
        )
        if provider:
            query = query.where(LeadHydrationJob.provider == provider)
        if state:
            query = query.where(LeadHydrationJob.state == state)
        return list(
            (
                await self.db.scalars(
                    query.order_by(LeadHydrationJob.created_at.desc()).limit(limit)
                )
            ).all()
        )

    async def list_events(
        self,
        workspace_id: UUID,
        job_id: UUID,
        *,
        limit: int = 200,
    ) -> list[LeadHydrationEvent]:
        await self.get(workspace_id, job_id)
        return list(
            (
                await self.db.scalars(
                    select(LeadHydrationEvent)
                    .where(
                        LeadHydrationEvent.workspace_id == workspace_id,
                        LeadHydrationEvent.hydration_job_id == job_id,
                    )
                    .order_by(LeadHydrationEvent.occurred_at.asc())
                    .limit(limit)
                )
            ).all()
        )

    async def transition(
        self,
        workspace_id: UUID,
        job_id: UUID,
        *,
        action: str,
    ) -> LeadHydrationJob:
        job = await self.db.scalar(
            select(LeadHydrationJob)
            .where(
                LeadHydrationJob.workspace_id == workspace_id,
                LeadHydrationJob.id == job_id,
            )
            .with_for_update()
        )
        if job is None:
            raise LookupError("Trabalho de hidratação não encontrado.")
        previous = job.state
        now = datetime.now(UTC)
        if action == "retry":
            if job.state == "hydrated":
                raise ValueError("Um lead já hidratado não pode ser reenfileirado.")
            job.state = "queued"
            job.attempt_count = 0
            job.next_attempt_at = now
            job.completed_at = None
            job.last_error_code = None
            job.last_error_message = None
            event_type = "manual_retry"
        elif action == "cancel":
            if job.state == "hydrated":
                raise ValueError("Um lead já hidratado não pode ser cancelado.")
            job.state = "cancelled"
            job.next_attempt_at = None
            job.completed_at = now
            event_type = "cancelled"
        else:
            raise ValueError("Ação de hidratação inválida.")
        job.lease_owner = None
        job.lease_expires_at = None
        append_hydration_event(
            self.db,
            job,
            event_type=event_type,
            previous_state=previous,
            next_state=job.state,
            idempotency_key=f"lead-hydration-{event_type}:{job.id}:{job.lease_generation + 1}",
            payload={"action": action},
            occurred_at=now,
            metadata={"externalRequests": 0},
        )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="omnichannel.lead_hydration.updated",
            data={"hydrationJobId": str(job.id), "state": job.state},
        )
        await self.db.flush()
        return job


__all__ = [
    "LeadHydrationService",
    "append_hydration_event",
    "canonical_hash",
]
