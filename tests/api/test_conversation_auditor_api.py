from datetime import UTC, datetime, timedelta

import pytest

from app.models.klm_operations import (
    MessageAuthorType,
    MessageDirection,
    OmnichannelChannel,
)
from app.schemas.klm_operations import OmnichannelMessageCreate, OmnichannelThreadCreate
from app.services.klm_operations_service import KlmOperationsService


@pytest.mark.asyncio
async def test_auditor_api_runs_filters_and_locks_actions(
    client,
    db_session,
    auth_context,
    active_subscription,
):
    operations = KlmOperationsService(db_session)
    thread = await operations.create_thread(
        auth_context.workspace_id,
        auth_context.user_id,
        OmnichannelThreadCreate(
            primary_channel=OmnichannelChannel.whatsapp,
            subject="Lead para auditoria via API",
        ),
    )
    await operations.create_message(
        auth_context.workspace_id,
        thread.id,
        None,
        OmnichannelMessageCreate(
            channel=OmnichannelChannel.whatsapp,
            direction=MessageDirection.inbound,
            author_type=MessageAuthorType.client,
            author_label="Lead",
            body="Continuo aguardando.",
            occurred_at=datetime.now(UTC) - timedelta(minutes=20),
            idempotency_key="auditor-api-inbound",
        ),
    )

    run = await client.post(
        "/api/v1/klm/omnichannel/audit/run",
        json={"threadId": str(thread.id), "limit": 1},
    )

    assert run.status_code == 200, run.text
    payload = run.json()
    assert payload["coveragePercent"] == 0
    assert payload["responseCoveragePercent"] == 0
    assert payload["criticalCount"] == 0
    assert payload["findings"][0]["findingType"] == "unanswered_lead"
    assert payload["findings"][0]["internalNoteMessageId"]

    filtered = await client.get(
        "/api/v1/klm/omnichannel/audit/findings",
        params={
            "thread_id": str(thread.id),
            "state": "open",
            "finding_type": "unanswered_lead",
            "query": "aguardando",
        },
    )
    assert filtered.status_code == 200, filtered.text
    assert len(filtered.json()["findings"]) == 1

    finding = payload["findings"][0]
    resolved = await client.post(
        f"/api/v1/klm/omnichannel/audit/findings/{finding['id']}/actions",
        json={
            "action": "resolve",
            "expectedVersion": finding["version"],
            "reason": "Resposta confirmada.",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["state"] == "resolved"

    stale = await client.post(
        f"/api/v1/klm/omnichannel/audit/findings/{finding['id']}/actions",
        json={"action": "dismiss", "expectedVersion": finding["version"]},
    )
    assert stale.status_code == 409
    assert stale.headers["X-KLM-Current-Version"] == str(resolved.json()["version"])
