from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.deps import AuthContext
from app.db.append_only import ImmutableRecordError
from app.models.client import Client
from app.models.matter import Matter
from app.models.omnichannel_v6 import (
    LeadCaptureReceipt,
    LeadHydrationEvent,
    LeadHydrationJob,
    OmnichannelChannelConnection,
    OmnichannelPublicIngressReceipt,
)
from app.services.lead_capture_adapter_service import LeadCaptureInspection
from app.services.lead_hydration_service import LeadHydrationService
from app.services.lead_hydration_transport import (
    LeadHydrationError,
    LeadHydrationReceipt,
    LeadHydrationRequest,
)
from app.workers import lead_hydration_tasks as hydration_tasks


pytestmark = pytest.mark.usefixtures("active_subscription")


class RecordingFetcher:
    def __init__(self, *, failure: LeadHydrationError | None = None) -> None:
        self.failure = failure
        self.requests: list[LeadHydrationRequest] = []

    async def fetch(self, request: LeadHydrationRequest) -> LeadHydrationReceipt:
        self.requests.append(request)
        if self.failure:
            raise self.failure
        return LeadHydrationReceipt(
            provider=request.provider,
            external_lead_id=request.external_lead_id,
            provider_request_id=f"request-{request.external_lead_id}",
            raw_payload={
                "id": request.external_lead_id,
                "form_id": request.external_form_id,
                "campaign_name": "Negativas de saúde",
                "field_data": [
                    {"name": "full_name", "values": ["Marina Costa"]},
                    {"name": "phone_number", "values": ["+5511987654321"]},
                    {"name": "medicamento", "values": ["Nivolumabe"]},
                ],
            },
        )


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def _create_job(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> LeadHydrationJob:
    suffix = uuid4().hex[-8:]
    connection_key = f"meta-leads:hydration-{suffix}"
    response = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json={
            "channel": "webchat",
            "provider": "meta_ads",
            "connectionKey": connection_key,
            "displayName": f"Meta Lead Ads {suffix}",
            "transportMode": "webhook",
        },
    )
    assert response.status_code == 201, response.text
    connection = await db_session.get(
        OmnichannelChannelConnection, UUID(response.json()["id"])
    )
    assert connection is not None
    connection.status = "connected"
    ingress = OmnichannelPublicIngressReceipt(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        provider=connection.provider,
        connection_key=connection.connection_key,
        external_event_id=f"lead:meta-hydration-{suffix}",
        external_lead_id=f"meta-hydration-{suffix}",
        payload_sha256="a" * 64,
        signature_sha256="b" * 64,
        signature_scheme="meta_x_hub_signature_256",
        request_metadata={},
        received_at=datetime.now(UTC),
    )
    db_session.add(ingress)
    await db_session.flush()
    job, deduplicated = await LeadHydrationService(db_session).enqueue(
        workspace_id=auth_context.workspace_id,
        connection=connection,
        ingress_receipt=ingress,
        external_event_id=ingress.external_event_id,
        inspection=LeadCaptureInspection(
            provider="meta_ads",
            external_lead_id=ingress.external_lead_id,
            external_form_id=f"form-{suffix}",
            raw_payload_digest="c" * 64,
            locator_payload={
                "externalLeadId": ingress.external_lead_id,
                "externalFormId": f"form-{suffix}",
            },
            normalized=None,
        ),
    )
    assert deduplicated is False
    await db_session.commit()
    return job


@pytest.mark.asyncio
async def test_hydration_worker_fetches_once_and_materializes_one_canonical_case(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    monkeypatch,
):
    job = await _create_job(client, db_session, auth_context)
    monkeypatch.setattr(hydration_tasks, "external_delivery_allowed", lambda: True)
    fetcher = RecordingFetcher()

    first = await hydration_tasks.process_workspace_hydrations(
        job.workspace_id,
        session_factory=_session_factory(db_session),
        fetcher_factory=lambda _connection: fetcher,
    )
    second = await hydration_tasks.process_workspace_hydrations(
        job.workspace_id,
        session_factory=_session_factory(db_session),
        fetcher_factory=lambda _connection: fetcher,
    )

    await db_session.refresh(job)
    assert first == {"hydrated": 1}
    assert second == {}
    assert len(fetcher.requests) == 1
    assert fetcher.requests[0].request_key == f"lead-hydration:{job.id}"
    assert job.state == "hydrated"
    assert job.attempt_count == 1
    assert job.lead_capture_receipt_id is not None
    assert await db_session.scalar(select(func.count()).select_from(Client)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Matter)) == 1
    assert await db_session.scalar(select(func.count()).select_from(LeadCaptureReceipt)) == 1

    history = await client.get(
        f"/api/v1/klm/omnichannel/lead-hydrations/{job.id}/events"
    )
    assert history.status_code == 200, history.text
    assert [item["eventType"] for item in history.json()] == [
        "created",
        "claimed",
        "fetch_started",
        "hydrated",
    ]
    event = await db_session.scalar(
        select(LeadHydrationEvent).where(LeadHydrationEvent.hydration_job_id == job.id)
    )
    assert event is not None
    event.event_metadata = {"tampered": True}
    with pytest.raises(ImmutableRecordError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_retryable_hydration_reuses_stable_request_key(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    monkeypatch,
):
    job = await _create_job(client, db_session, auth_context)
    monkeypatch.setattr(hydration_tasks, "external_delivery_allowed", lambda: True)
    failing = RecordingFetcher(
        failure=LeadHydrationError(
            "Rate limit temporário.",
            code="provider_rate_limited",
            retryable=True,
        )
    )
    first = await hydration_tasks.process_hydration_job(
        job.workspace_id,
        job.id,
        session_factory=_session_factory(db_session),
        fetcher_factory=lambda _connection: failing,
    )
    await db_session.refresh(job)
    assert first["status"] == "retrying"
    assert job.attempt_count == 1
    job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    recovered = RecordingFetcher()
    second = await hydration_tasks.process_hydration_job(
        job.workspace_id,
        job.id,
        session_factory=_session_factory(db_session),
        fetcher_factory=lambda _connection: recovered,
    )
    await db_session.refresh(job)

    assert second["status"] == "hydrated"
    assert [item.request_key for item in failing.requests] == [f"lead-hydration:{job.id}"]
    assert [item.request_key for item in recovered.requests] == [f"lead-hydration:{job.id}"]
    assert job.state == "hydrated"
    assert job.attempt_count == 2
    assert job.lease_generation == 2


@pytest.mark.asyncio
async def test_hydration_gate_closes_before_provider_fetch(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    monkeypatch,
):
    job = await _create_job(client, db_session, auth_context)
    monkeypatch.setattr(hydration_tasks, "external_delivery_allowed", lambda: False)
    fetcher = RecordingFetcher()

    outcome = await hydration_tasks.process_hydration_job(
        job.workspace_id,
        job.id,
        session_factory=_session_factory(db_session),
        fetcher_factory=lambda _connection: fetcher,
    )

    await db_session.refresh(job)
    assert outcome == {"status": "blocked_external", "externalRequests": 0}
    assert fetcher.requests == []
    assert job.state == "blocked_external"
    assert job.attempt_count == 0


@pytest.mark.asyncio
async def test_expired_hydration_lease_is_safely_reclaimed(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    monkeypatch,
):
    job = await _create_job(client, db_session, auth_context)
    job.state = "processing"
    job.lease_owner = "dead-worker"
    job.lease_generation = 1
    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    job.fetch_attempted_at = datetime.now(UTC) - timedelta(minutes=6)
    job.attempt_count = 1
    await db_session.commit()
    monkeypatch.setattr(hydration_tasks, "external_delivery_allowed", lambda: True)
    fetcher = RecordingFetcher()

    outcome = await hydration_tasks.process_hydration_job(
        job.workspace_id,
        job.id,
        session_factory=_session_factory(db_session),
        fetcher_factory=lambda _connection: fetcher,
    )

    await db_session.refresh(job)
    assert outcome["status"] == "hydrated"
    assert len(fetcher.requests) == 1
    assert job.state == "hydrated"
    assert job.attempt_count == 2
    assert job.lease_generation == 2
